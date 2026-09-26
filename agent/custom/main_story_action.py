"""独立主线调度：每关重判地图/列表，战斗复用单场入口。"""
import html
import time

from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction

import mfaalog
from battle.runtime.formation_session import session_override, sessions


# Context 克隆隔离节点配置，但框架命中计数属于任务状态，跨关仍需重置。
_BATTLE_RESET = (
    "执行进本", "进本流程-超时重试", "跳过剧情-点击跳过",
    "自动编队-删除活动筛选点击", "自动编队-礼装删除活动筛选点击",
    "自动编队-礼装筛上滑找满破", "自动编队-礼装筛找满破",
    "羁绊补齐-从者删除活动筛选点击", "羁绊补齐-礼装删除活动筛选点击",
    "羁绊补齐-礼装筛上滑找满破", "羁绊补齐-礼装筛找满破",
)


class StoryStopped(RuntimeError):
    pass


def next_names(node):
    """Maa get_node_data 返回规范化 edge 对象，源 JSON 也允许字符串。"""
    return [edge if isinstance(edge, str) else edge["name"] for edge in node.get("next", [])]


def map_swipes(context):
    """读取已有蛇形路径，仅复用滑动动作，每次 repeat 都留出识别机会。"""
    name, seen = "巡-初始化左滑", set()
    while name != "巡-未找到":
        if name in seen:
            raise ValueError("地图轮巡路径出现循环")
        seen.add(name)
        node = context.get_node_data(name) or {}
        action = node.get("action", {})
        if not isinstance(action, dict) or action.get("type") != "Swipe":
            raise ValueError(f"地图轮巡滑动节点无效：{name}")
        for _ in range(int(node.get("repeat", 1))):
            yield name
        candidates = [s for s in next_names(node)
                      if s.startswith("巡-") and "找到目标" not in s]
        if len(candidates) != 1:
            raise ValueError(f"地图轮巡出口不唯一：{name}")
        name = candidates[0]


class StoryRunner:
    def __init__(self, context, *, clock=time.monotonic, sleep=time.sleep):
        self.ctx = context
        self.tasker = context.tasker
        self.controller = self.tasker.controller
        self.clock, self.sleep = clock, sleep
        self.config = (context.get_node_data("主线-配置") or {}).get("attach", {})
        self.limit = int(self.config.get("quest_limit", 10))
        self.mode = self.config.get("battle_mode", "native")
        self.timeout = int(self.config.get("transition_timeout", 120))
        if not 1 <= self.limit <= 999 or self.mode not in ("native", "bbc") or not 10 <= self.timeout <= 600:
            raise ValueError("主线参数无效")
        self.completed = self.battles = 0

    def check_stop(self):
        if self.tasker.stopping:
            raise StoryStopped("用户已停止")

    def pause(self, seconds=0.5):
        until = self.clock() + seconds
        while self.clock() < until:
            self.check_stop()
            self.sleep(min(0.1, max(0, until - self.clock())))

    def frame(self):
        self.check_stop()
        image = self.controller.post_screencap().wait().get()
        if image is None:
            raise StoryStopped("截图失败")
        return image

    def reco(self, name, image):
        data = self.ctx.get_node_data(name)
        if data and not data.get("enabled", True):
            return None
        result = self.ctx.run_recognition(name, image)
        return result if result and result.hit else None

    def action(self, name, result=None, override=None):
        self.check_stop()
        box = result.box if result else [0, 0, 0, 0]
        detail = self.ctx.run_action(name, box, pipeline_override=override or {})
        if detail is None or not detail.success:
            raise StoryStopped(f"动作失败：{name}")

    def task(self, name, override=None):
        self.check_stop()
        detail = self.ctx.run_task(name, pipeline_override=override or {})
        if detail is None or not detail.status.succeeded:
            raise StoryStopped(f"流程失败：{name}")
        self.check_stop()

    def panel(self, image):
        if self.reco("主线-副本列表信息", image):
            return True
        if not self.reco("主线-列表后退", image):
            return False
        if self.reco("主线-列表NEXT", image):
            return True
        # 国服主线列表没有信息 i。NEXT 不可见时仍须识别列表，才能滚动查找。
        return bool(self.reco("主线-地图菜单", image)
                    and self.reco("主线-地图任务图标", image)
                    and not self.reco("主线-地图控件", image))

    def on_map(self, image):
        return bool(self.reco("主线-地图菜单", image) and self.reco("主线-地图控件", image))

    def battle_ready(self, image):
        return any(self.reco(n, image) for n in
                   ("主线-助战界面", "进本-队伍确认", "进本-战斗主界面已出现"))

    def failure(self, image):
        if any(self.reco(n, image) for n in ("战斗失败_不回主界面", "失败2_不回主界面")):
            raise StoryStopped("战斗失败，需要人工处理")

    def interrupt(self, image, *, entering=False):
        """已识别事件才操作。剧情复用现有跳过链，未知画面不盲点。"""
        if entering and self.reco("全局-战前吃体力", image):
            entry = self.ctx.get_node_data("全局-战前吃体力") or {}
            if next_names(entry) == ["全局-体力不足"]:
                raise StoryStopped("AP 不足，当前设置不补充体力")
            self.task("全局-战前吃体力")
            return True
        if self.reco("跳过剧情-点击跳过", image):
            self.ctx.clear_hit_count("跳过剧情-点击跳过")
            self.task("跳过剧情-点击跳过")
            return True
        names = ["进本-关闭告知弹窗"]
        if entering:
            names = ["进本-编队提示-点击开始", "进本-点击任务开始"] + names
        for name in names:
            result = self.reco(name, image)
            if result:
                self.action(name, result)
                self.pause()
                return True
        return False

    def locate(self):
        """返回列表中的 NEXT；严格先列表，再当前目标，最后单步轮巡。"""
        swipes = iter(map_swipes(self.ctx))
        empty_panels = 0
        deadline = self.clock() + self.timeout
        while self.clock() < deadline:
            image = self.frame()
            if self.panel(image):
                result = self.find_in_panel(image)
                if result:
                    return result
                empty_panels += 1
                if empty_panels >= 2:
                    raise StoryStopped("副本列表中没有可推进的 NEXT 关卡")
                back = self.reco("主线-列表后退", self.frame())
                if not back:
                    raise StoryStopped("列表无 NEXT，且无法确认返回地图按钮")
                self.action("主线-列表后退", back)
                self.pause(1)
                continue
            if self.on_map(image):
                result = self.reco("主线-地图NEXT", image)
                if result:
                    self.open_panel(result)
                    continue
                name = next(swipes, None)
                if name is None:
                    raise StoryStopped("地图遍历完成，未找到可推进的 NEXT 节点")
                # run_action 不运行 next；repeat 强制单步，避免漏掉滑动中出现的目标。
                self.action(name, override={name: {"repeat": 1}})
                self.pause(1)
                deadline = self.clock() + self.timeout
                continue
            if not self.interrupt(image):
                self.pause()
        raise StoryStopped("无法确认主线地图或副本列表，请检查当前画面")

    def find_in_panel(self, image):
        # 先检查当前位置，再滚到顶部，随后向下有限查找。
        for step in range(9):
            result = self.reco("主线-列表NEXT", image)
            if result:
                return result
            if step == 8:
                return None
            self.action("主线-列表向上查找" if step < 3 else "主线-列表向下查找")
            self.pause(0.6)
            image = self.frame()
            if not self.panel(image):
                raise StoryStopped("查找主线关卡时离开了副本列表")

    def open_panel(self, result):
        for _ in range(3):
            self.action("主线-地图NEXT", result)
            until = self.clock() + 5
            while self.clock() < until:
                self.pause()
                image = self.frame()
                if self.panel(image):
                    return
            # 每次重试刷新位置，不重复点击过期坐标。
            image = self.frame()
            if not self.on_map(image):
                break
            result = self.reco("主线-地图NEXT", image)
            if not result:
                break
        raise StoryStopped("点击主线节点后未打开副本列表")

    def enter(self, result):
        self.action("主线-列表NEXT", result)
        deadline = self.clock() + self.timeout
        departed, story_seen = False, False
        stable_return = 0
        clicks, last_click = 1, self.clock()
        while self.clock() < deadline:
            self.pause()
            image = self.frame()
            self.failure(image)
            if self.battle_ready(image):
                return "battle"
            # AP/剧情/开始提示的优先级高于下方仍可见的列表。
            is_story = bool(self.reco("跳过剧情-点击跳过", image))
            if self.interrupt(image, entering=True):
                story_seen |= is_story
                departed |= is_story
                stable_return = 0
                continue
            panel = self.panel(image)
            on_map = not panel and self.on_map(image)
            if (panel or on_map) and departed and story_seen:
                stable_return += 1
                if stable_return >= 2:
                    return "story"
            elif panel and not departed and self.clock() - last_click > 5:
                if clicks >= 3:
                    raise StoryStopped("主线关卡点击无效，可能存在锁定条件")
                result = self.reco("主线-列表NEXT", image)
                if result:
                    self.action("主线-列表NEXT", result)
                    clicks += 1
                    last_click = self.clock()
            elif not panel and not on_map:
                departed = True
                stable_return = 0
        raise StoryStopped("进本超时：未到达助战/战斗，或纯剧情返回尚未确认")

    def fight(self):
        # 不复用上一场的编队快照。仅当前单场持有会话，finally 负责清理。
        if self.mode == "bbc":
            self.task("bbc战斗", {"执行BBC任务": {"attach": {"run_count": 1, "battle_type": 0}}})
            return
        root = self.ctx.get_task_job().job_id
        token = sessions.begin(root)
        try:
            if not self.ctx.override_pipeline(session_override(token)):
                raise StoryStopped("无法初始化本场编队会话")
            for name in _BATTLE_RESET:
                self.ctx.clear_hit_count(name)
            self.task("进本流程")
            if not self.reco("进本-战斗主界面已出现", self.frame()):
                raise StoryStopped("进本结束但未确认战斗画面")
            self.task("原生自动战斗入口")
        finally:
            sessions.finish(root, token)

    def settle(self):
        deadline = self.clock() + self.timeout
        stable_return = 0
        while self.clock() < deadline:
            image = self.frame()
            self.failure(image)
            # 弹窗先处理，避免误认弹窗后透出的地图。
            handled = False
            for name in ("作战成功_结束战斗", "好友申请界面_不回主界面",
                         "战斗结束关闭_不回主界面"):
                result = self.reco(name, image)
                if result:
                    self.action(name, result)
                    handled = True
                    break
            if handled or self.interrupt(image):
                stable_return = 0
                self.pause()
                continue
            # 部分主线阶段战后直接出现整屏“任务完成／获得报酬”，没有“下一步”。
            # 必须先命中专用提示再点击，避免在未知画面盲点。
            reward = self.reco("主线-任务完成报酬", image)
            if reward:
                self.action("主线-任务完成报酬", reward)
                stable_return = 0
                self.pause(1)
                continue
            if self.panel(image) or self.on_map(image):
                stable_return += 1
                if stable_return >= 2:
                    return
            else:
                stable_return = 0
                # 只在已确认的结算页推进，未知页面不盲点。
                result = self.reco("主线-结算界面", image)
                if result:
                    self.action("主线-结算界面", result)
            self.pause(0.8)
        raise StoryStopped("战后结算超时，未确认返回地图或副本列表")

    def run(self):
        while self.completed < self.limit:
            target = self.locate()
            kind = self.enter(target)
            if kind == "battle":
                self.fight()
                self.settle()
                self.battles += 1
            self.completed += 1
            mfaalog.info(f"[自动推主线] 已推进 {self.completed}/{self.limit} 关，战斗 {self.battles} 场")
        return f"达到推进上限：已推进 {self.completed} 关，完成战斗 {self.battles} 场"


@AgentServer.custom_action("auto_main_story")
class MainStoryAction(CustomAction):
    def run(self, context, argv):
        runner = None
        try:
            runner = StoryRunner(context.clone())
            message = runner.run()
            ok = True
        except Exception as exc:
            message = f"主线中断：{exc}"
            if runner:
                message += f"（已推进 {runner.completed} 关，完成战斗 {runner.battles} 场）"
            mfaalog.error(f"[自动推主线] {message}")
            ok = False
        if not context.tasker.stopping:
            context.run_task("主线-结果提示", pipeline_override={
                "主线-结果提示": {"focus": {"Node.Action.Starting": html.escape(message)}}})
        return CustomAction.RunResult(success=ok)
