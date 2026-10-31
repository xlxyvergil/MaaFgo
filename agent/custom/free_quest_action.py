"""Free Quest 独立调度；只复用通用助战、单场战斗和结算，不处理主线限制。"""
import html
import time

import cv2
import numpy as np

from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction

import mfaalog
from battle.runtime.formation_session import session_override, sessions
from main_story_action import StoryRunner, StoryStopped, _BATTLE_RESET, map_swipes, next_names


class FreeQuestRunner:
    check_stop = StoryRunner.check_stop
    pause = StoryRunner.pause
    frame = StoryRunner.frame
    reco = StoryRunner.reco
    action = StoryRunner.action
    task = StoryRunner.task
    on_map = StoryRunner.on_map
    failure = StoryRunner.failure
    is_defeat = StoryRunner.is_defeat
    reach_formation = StoryRunner.reach_formation
    settle = StoryRunner.settle

    def __init__(self, context, *, clock=time.monotonic, sleep=time.sleep):
        self.ctx, self.tasker = context, context.tasker
        self.controller = self.tasker.controller
        self.clock, self.sleep = clock, sleep
        config = (context.get_node_data("Free-配置") or {}).get("attach", {})
        self.limit = int(config.get("battle_limit", 999))
        self.timeout = int(config.get("transition_timeout", 120))
        self.mode = config.get("battle_mode", "native")
        self.horizontal_repeat = int(config.get("map_horizontal_repeat", 2))
        if not 1 <= self.limit <= 999 or not 10 <= self.timeout <= 600 or self.mode not in ("native", "bbc"):
            raise ValueError("Free 本参数无效")
        if not 1 <= self.horizontal_repeat <= 4:
            raise ValueError("Free 本地图横向步数无效")
        self.battles = self.completed = 0
        self.pending_clear = False
        self.current_title = None

    def panel(self, image):
        return bool(self.reco("主线-列表后退", image) and (
            self.reco("Free-列表标记", image) or self.reco("主线-副本列表信息", image)))

    def interrupt(self, image, *, entering=False, forming=False):
        if entering and self.reco("全局-战前吃体力", image):
            if next_names(self.ctx.get_node_data("全局-战前吃体力") or {}) == ["全局-体力不足"]:
                raise StoryStopped("AP 不足，当前设置不补充体力")
            self.task("全局-战前吃体力")
            return True
        for name in ("主线-右上公告关闭", "进本-普通告知弹窗关闭",
                     "进本-攻略提示关闭", "主线-编队弹窗关闭保底"):
            result = self.reco(name, image)
            if result:
                self.action(name, result)
                self.pause()
                return True
        return False

    def rows(self, image):
        """逐行找到 Free 标记，把 CLEAR 角标限制在该行下沿，避免串到另一关。"""
        local = self.ctx.clone()
        top = 80
        while top < 590:
            result = local.run_recognition("Free-列表标记", image, {
                "Free-列表标记": {"recognition": {"param": {"roi": [620, top, 610, 590-top]}}}
            })
            if not result or not result.hit:
                break
            box = result.box
            clear = local.run_recognition("Free-完成角标", image, {
                "Free-完成角标": {"recognition": {"param": {
                    "roi": [max(620, box.x-30), box.y+75, 180, 65]
                }}}
            })
            yield result, bool(clear and clear.hit)
            top = box.y + 140

    def find_in_panel(self, image):
        for step in range(9):
            rows = list(self.rows(image))
            for result, cleared in rows:
                if self.pending_clear:
                    if self.current_title is None and len(rows) != 1:
                        raise StoryStopped("从助战/编队启动后出现多个 Free Quest，无法确认刚完成的目标")
                    if not self.same_quest(image, result):
                        continue
                    if cleared:
                        self.completed += 1
                        self.pending_clear = False
                        self.current_title = None
                        mfaalog.info(f"[自动清free本] 已确认 CLEAR，共完成 {self.completed} 个 Free 本")
                    else:
                        return result
            # 先复核刚完成的关卡，再选择其他未完成行。
            if not self.pending_clear:
                for result, cleared in rows:
                    if not cleared:
                        return result
                if rows:
                    # 普通章节的详情只有当前地图点的 Free Quest。
                    # 已确认可见行均 CLEAR 后直接退图，不用手势在背景地图上滑动。
                    return None
            if step == 8:
                break
            self.action("主线-列表向上查找" if step < 3 else "主线-列表向下查找")
            self.pause(.6)
            image = self.frame()
            if not self.panel(image):
                raise StoryStopped("查找 Free Quest 时离开了副本详情")
        if self.pending_clear:
            raise StoryStopped("战后未找到原 Free Quest 或 CLEAR 标记，保留现场")
        return None

    def title_patch(self, image, result):
        box = result.box
        dx, dy, width, height = (self.ctx.get_node_data("Free-列表标记") or {}).get(
            "attach", {}).get("title_roi_offset", [125, -35, 290, 40])
        x, y = box.x + dx, box.y + dy
        return image[y:y+height, x:x+width].copy()

    def same_quest(self, image, result):
        if self.current_title is None:
            return True
        patch = self.title_patch(image, result)
        if patch.shape != self.current_title.shape or not patch.size:
            return False
        current = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
        previous = cv2.cvtColor(self.current_title, cv2.COLOR_BGR2GRAY)
        if previous.std() < 2:
            return np.array_equal(current, previous)
        # 2026-11-01 日服首战：列表前次标记出现后标题位移数像素；
        # 允许小幅平移，仍要求文字相关分数 >= .90，不能靠整块背景的平均色差。
        search = cv2.copyMakeBorder(current, 6, 6, 6, 6, cv2.BORDER_REPLICATE)
        return float(cv2.minMaxLoc(cv2.matchTemplate(search, previous, cv2.TM_CCOEFF_NORMED))[1]) >= .90

    def back_to_map(self):
        result = self.reco("主线-列表后退", self.frame())
        if not result:
            raise StoryStopped("无法确认副本详情的后退按钮")
        self.action("主线-列表后退", result)
        deadline = self.clock() + 10
        while self.clock() < deadline:
            self.pause(.5)
            image = self.frame()
            if not self.panel(image) and self.on_map(image):
                return
        raise StoryStopped("关闭副本详情后未确认返回地图")

    def open_panel(self, result, node="Free-地图数字"):
        self.action(node, result)
        deadline = self.clock() + 10
        while self.clock() < deadline:
            self.pause(.5)
            if self.panel(self.frame()):
                return
        raise StoryStopped("点击数字地图点后未确认副本详情")

    def locate(self):
        swipes = iter(self.patrol())
        deadline = self.clock() + self.timeout
        opened = 0
        while self.clock() < deadline:
            image = self.frame()
            if self.interrupt(image, forming=True):
                continue
            if any(self.reco(n, image) for n in ("主线-助战界面", "进本-队伍确认", "进本-战斗主界面已出现")):
                return None
            if self.panel(image):
                result = self.find_in_panel(image)
                if result:
                    return result
                self.back_to_map()
                # 数字可能属于非 Free 关卡或过渡动画；有限退出，不反复点同一个标记。
                opened += 1
                if opened >= 3:
                    raise StoryStopped("数字节点反复没有未完成 Free Quest，请检查地图")
                deadline = self.clock() + self.timeout
                continue
            if self.on_map(image):
                # 隠者の逃げ道 首通会解锁新点并直接回地图。必须先按前回复核
                # 原关卡 CLEAR，不能带着上一关 pending 状态点击新数字点。
                node = "Free-地图前回" if self.pending_clear else "Free-地图数字"
                result = self.reco(node, image)
                if result:
                    self.open_panel(result, node)
                    continue
                name = next(swipes, None)
                if name is None:
                    if self.pending_clear:
                        raise StoryStopped("战后返回地图，但遍历未找到前回标记，无法复核原 Free Quest")
                    return False
                self.swipe_map(name)
                self.pause(1)
                deadline = self.clock() + self.timeout
                continue
            self.pause()
        raise StoryStopped("无法确认地图、Free 本详情、助战或编队画面")

    def patrol(self):
        """复用通用蛇形的动作，按 Free 配置扩展每行横向覆盖；逐步重拍。"""
        for name in map_swipes(self.ctx):
            repeat = self.horizontal_repeat if name.startswith(("巡-左", "巡-右")) else 1
            for _ in range(repeat):
                yield name

    def swipe_map(self, name):
        # 单步执行时不能把通用复位节点的 repeat 覆盖带到下一轮查找。
        original = self.ctx
        self.ctx = original.clone()
        try:
            self.action(name, override={name: {"repeat": 1}})
        finally:
            self.ctx = original

    def enter(self, result):
        # 重拍后复核该行仍未 CLEAR，避免慢识别后点击旧坐标。
        image = self.frame()
        choices = [row for row, cleared in self.rows(image)
                   if not cleared and (not self.pending_clear or self.same_quest(image, row))]
        if not choices:
            raise StoryStopped("点击前未确认未完成 Free Quest")
        self.current_title = self.title_patch(image, choices[0])
        self.action("Free-列表标记", choices[0])
        deadline = self.clock() + self.timeout
        while self.clock() < deadline:
            self.pause()
            image = self.frame()
            self.failure(image)
            if self.interrupt(image, entering=True):
                continue
            if any(self.reco(n, image) for n in ("主线-助战界面", "进本-队伍确认", "进本-战斗主界面已出现")):
                return
        raise StoryStopped("Free 本进本超时，保留现场")

    def fight(self):
        original = self.ctx
        self.ctx = original.clone()
        root = self.ctx.get_task_job().job_id
        token = sessions.begin(root)
        try:
            if self.mode == "bbc":
                self.task("bbc战斗", {"执行BBC任务": {"attach": {"run_count": 1, "battle_type": 0}}})
                return
            if not self.ctx.override_pipeline(session_override(token)):
                raise StoryStopped("无法初始化单场编队会话")
            self.reach_formation()
            for name in _BATTLE_RESET:
                self.ctx.clear_hit_count(name)
            self.task("进本流程", {"进本流程": {"next": ["进本-队伍确认", "进本-战斗主界面已出现"], "on_error": []}})
            self.task("原生自动战斗入口")
            image = self.frame()
            if self.reco("进本-战斗主界面已出现", image) or self.reco("战斗_选卡场景", image):
                raise StoryStopped("单场战斗提前返回，仍停留在战斗界面；保留现场，未计入完成场次")
        finally:
            sessions.finish(root, token)
            self.ctx = original

    def run(self):
        while self.battles < self.limit:
            target = self.locate()
            if target is False:
                return f"地图遍历完成，未找到未完成数字标记；本次确认 CLEAR {self.completed} 个 Free 本，完成战斗 {self.battles} 场"
            if target is not None:
                self.enter(target)
            self.fight()
            self.settle()
            self.battles += 1
            self.pending_clear = True
            mfaalog.info(f"[自动清free本] 已完成战斗 {self.battles}/{self.limit} 场，返回详情复查 CLEAR")
        # 到上限也复核当前行；上限按实际战斗次数，不按三次猜完成。
        image = self.frame()
        if self.panel(image):
            rows = list(self.rows(image))
            if (self.current_title is not None or len(rows) == 1) and any(
                    cleared and self.same_quest(image, row) for row, cleared in rows):
                self.completed += 1
        return f"达到战斗上限：确认 CLEAR {self.completed} 个 Free 本，完成战斗 {self.battles} 场"


@AgentServer.custom_action("auto_free_quest")
class FreeQuestAction(CustomAction):
    def run(self, context, argv):
        runner = None
        try:
            runner = FreeQuestRunner(context.clone())
            message, ok = runner.run(), True
        except Exception as exc:
            message = f"自动清free本中断：{exc}"
            if runner:
                message += f"（确认 CLEAR {runner.completed} 个，战斗 {runner.battles} 场）"
            mfaalog.error(message)
            ok = False
        if not context.tasker.stopping:
            context.run_task("Free-结果提示", pipeline_override={
                "Free-结果提示": {"focus": {"Node.Action.Starting": html.escape(message)}}})
        return CustomAction.RunResult(success=ok)
