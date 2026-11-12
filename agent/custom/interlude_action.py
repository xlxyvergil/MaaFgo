"""幕间列表调度；导航独立，单场战斗与限制编队复用主线实现。"""
import html
import json
import time

import cv2
import numpy as np
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction

import mfaalog
from battle.runtime.bond_stop_state import bond_stops
from bond_stop_action import BondStopReached
from main_story_action import StoryRunner, StoryStopped


def recognition_detail(result):
    best = getattr(result, "best_result", None)
    detail = getattr(best, "detail", getattr(result, "detail", {}))
    if isinstance(detail, str):
        detail = json.loads(detail)
    return detail if isinstance(detail, dict) else {}


def same_list(first, second):
    """比较卡片头像右缘，避开下沿轮播文字、PICKUP/NEW和进度箭头。"""
    if not isinstance(first, np.ndarray) or not isinstance(second, np.ndarray):
        return False
    if first.shape != second.shape:
        return False
    def patch(image):
        h, w = image.shape[:2]
        # 1280×720国服实拍：下沿增益说明从x775起，PICKUP最右约x740。
        # 保留整段绝对纵坐标，滚动时头像和卡片边界仍会移动；不逐行对齐。
        crop = image[round(h * .14):round(h * .81),
                     round(w * 745 / 1280):round(w * 765 / 1280)]
        return cv2.resize(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), (32, 220))
    return float(np.mean(cv2.absdiff(patch(first), patch(second)))) < 1.5


class InterludeRunner(StoryRunner):
    def __init__(self, context, *, clock=time.monotonic, sleep=time.sleep):
        # 只复用主线执行器的单场行为，不继承另一任务的运行时选项。
        isolated = context.clone()
        isolated.override_pipeline({"主线-配置": {"attach": {
            "quest_limit": 999, "battle_mode": "native", "transition_timeout": 120,
            "use_spirit_stone": False, "retry_without_spirit_stone": False,
        }}})
        super().__init__(isolated, clock=clock, sleep=sleep)
        self.config = (context.get_node_data("幕间-配置") or {}).get("attach", {})
        self.limit = self.config.get("quest_limit", 999)
        self.timeout = self.config.get("transition_timeout", 120)
        if type(self.limit) is not int or not 1 <= self.limit <= 999:
            raise ValueError("挑战次数无法读取，无法开始幕间任务。请将“挑战次数上限”设为1至999后重试。")
        if type(self.timeout) is not int or not 10 <= self.timeout <= 600:
            raise ValueError("幕间界面等待时间无法读取。请恢复任务默认设置后重试。")
        self.auto_handle_restrictions = self.config.get("auto_handle_restrictions", True) is True
        self._map_opened_at = None

    def total_list(self, image):
        return bool(self.reco("幕间-列表界面", image))

    def on_map(self, image):
        return bool(self.reco("幕间-返回列表", image))

    def panel(self, image):
        return self.total_list(image) or self.on_map(image)

    def interrupt(self, image, *, entering=False, forming=False):
        # 奖励可能透出原列表，必须先处理，再允许地图/列表作为结算出口。
        for name in ("剧情奖励-展示继续", "剧情奖励-礼装详情关闭",
                     "剧情奖励-从者加入详情关闭", "结算-奖励详情返回"):
            result = self.reco(name, image)
            if result:
                self.action(name, result)
                self.pause(1)
                return True
        return super().interrupt(image, entering=entering, forming=forming)

    def _ensure_list(self, image, map_mode):
        if not (self.on_map(image) if map_mode else self.total_list(image)):
            raise StoryStopped("查找幕间时列表界面发生变化，无法继续选关。请回到“幕间物语”列表后重试。")

    def _scan_uncertain(self, image, map_mode):
        local = self.ctx.clone()
        scan = local.run_recognition("幕间-条目扫描", image, {
            "幕间-条目扫描": {"recognition": {"param": {
                "custom_recognition_param": {"map": map_mode}
            }}}
        })
        detail = recognition_detail(scan)
        if not scan or not scan.hit or not {"row_count", "uncertain_count"} <= detail.keys():
            return True
        return bool(detail["uncertain_count"]) or (
            not detail["row_count"] and not detail.get("empty_confirmed", False))

    def scan_entries(self, image, map_mode=False):
        """先复位，逐页查找；只有确认触底且没有未知条目才能报告清空。"""
        up = "幕间-地图列表复位" if map_mode else "幕间-列表复位"
        down = "幕间-地图列表向下滑动" if map_mode else "幕间-列表向下滑动"
        node = "幕间-地图关卡" if map_mode else "幕间-可挑战条目"
        # 总列表还含大量未开启条目；不能用当前可见的少数幕间推定列表长度。
        bound = 12 if map_mode else 400
        self._ensure_list(image, map_mode)
        # 一次动作后等待新截图，两次稳定才认定边界，避免短暂加载被当作触底。
        for _ in range(bound):
            previous = image
            self.action(up)
            self.pause(.6)
            image = self.frame()
            self._ensure_list(image, map_mode)
            if same_list(previous, image):
                self.pause(.5)
                stable = self.frame()
                self._ensure_list(stable, map_mode)
                if same_list(image, stable):
                    image = stable
                    break
        else:
            raise StoryStopped("幕间列表复位超过上限，无法确认已回到顶部。请手动将列表拉到顶部后重试。")
        uncertain = False
        for _ in range(bound):
            result = self.reco(node, image)
            if result:
                return node, result
            uncertain |= self._scan_uncertain(image, map_mode)
            previous = image
            self.action(down)
            self.pause(.6)
            image = self.frame()
            self._ensure_list(image, map_mode)
            if same_list(previous, image):
                self.pause(.6)
                stable = self.frame()
                self._ensure_list(stable, map_mode)
                if same_list(image, stable):
                    # 最后稳定帧再复核一次，不能用滑动前的候选/空结果。
                    result = self.reco(node, stable)
                    if result:
                        return node, result
                    uncertain |= self._scan_uncertain(stable, map_mode)
                    if uncertain:
                        raise StoryStopped("列表中有无法确认是否开启的幕间，无法确定已全部完成。请检查当前条目及开启条件后重试。")
                    return None
        raise StoryStopped("幕间列表查找超过上限，尚未确认全部条目。请将列表拉到顶部后重试。")

    def return_to_list(self):
        result = self.reco("幕间-返回列表", self.frame())
        if not result:
            raise StoryStopped("未找到“前往幕间物语”，无法返回幕间列表。请手动返回列表后重试。")
        self.action("幕间-返回列表", result)
        deadline = self.clock() + self.timeout
        while self.clock() < deadline:
            self.pause(.6)
            image = self.frame()
            if self.interrupt(image):
                continue
            if self.total_list(image):
                self._map_opened_at = None
                return
        raise StoryStopped("点击“前往幕间物语”后未返回幕间列表。请检查游戏画面并手动返回后重试。")

    def locate(self):
        deadline = self.clock() + self.timeout
        while self.clock() < deadline:
            image = self.frame()
            if self.interrupt(image, forming=True):
                continue
            if self.total_list(image):
                return self.scan_entries(image)
            if self.on_map(image):
                target = self.scan_entries(image, map_mode=True)
                if target:
                    return target
                if self._map_opened_at == self.completed:
                    raise StoryStopped("已到达故事舞台，但没有找到可挑战的幕间关卡。请检查地图中的幕间及开启条件后重试。")
                self.return_to_list()
                deadline = self.clock() + self.timeout
                continue
            self.pause(.6)
        raise StoryStopped("未识别到幕间列表或故事舞台，无法继续。请回到“幕间物语”列表后启动任务。")

    def _story_visible(self, image):
        # 关闭跳剧情也需要观察剧情出现；只做识别，动作仍尊重原选项。
        probe = self.ctx.clone()
        result = probe.run_recognition("跳过剧情-点击跳过", image, {
            "跳过剧情-点击跳过": {"enabled": True}
        })
        return bool(result and result.hit)

    def enter(self, target):
        node, result = target
        detail = recognition_detail(result)
        label = "前往故事舞台" if detail.get("kind") == "stage" else "挑战幕间小关"
        mfaalog.info(f"[自动幕间副本] {label}：{detail.get('title') or '已识别的幕间'}")
        self.action(node, result)
        deadline = self.clock() + self.timeout
        story_seen, return_since = False, None
        while self.clock() < deadline:
            self.pause(.6)
            image = self.frame()
            self.failure(image)
            story_seen |= self._story_visible(image)
            if self.interrupt(image, entering=True):
                return_since = None
                continue
            if self.battle_ready(image):
                return "battle"
            total, stage = self.total_list(image), self.on_map(image)
            if node == "幕间-可挑战条目" and stage and not story_seen:
                self._map_opened_at = self.completed
                return "map"
            if story_seen and (total or stage):
                if return_since is None:
                    return_since = self.clock()
                if self.clock() - return_since >= 3 and self.clock() >= self.skip_settle_until:
                    return "story"
            else:
                return_since = None
        raise StoryStopped("进入幕间后未确认战斗或剧情完成，无法继续。请检查当前画面；若关闭了“跳过剧情”，请手动完成剧情后重试。")

    def run(self):
        if not self.panel(self.frame()):
            raise StoryStopped("当前不在幕间列表或故事舞台。请打开“幕间物语”列表后启动“自动幕间副本”。")
        while self.completed < self.limit:
            self.reset_restriction_state()
            self._dialogue_choices.clear()
            target = self.locate()
            if target is None:
                return f"已扫描幕间列表，没有可挑战的已开启幕间；完成 {self.completed} 个小关，其中战斗 {self.battles} 场。"
            kind = self.enter(target)
            if kind == "map":
                continue
            if kind == "battle":
                bond_stops.battle_started(self.ctx.get_task_job().job_id, self.battles + 1,
                    another_battle_planned=False if self.completed + 1 == self.limit else None)
                self.fight()
                self.settle()
                self.battles += 1
            self.completed += 1
            mfaalog.info(f"[自动幕间副本] 已完成 {self.completed}/{self.limit} 个小关，战斗 {self.battles} 场")
        return f"达到挑战次数上限：完成 {self.completed} 个小关，其中战斗 {self.battles} 场。"


@AgentServer.custom_action("auto_interlude")
class InterludeAction(CustomAction):
    def run(self, context, argv):
        runner = None
        bond_stops.begin(context.get_task_job().job_id)
        try:
            runner = InterludeRunner(context)
            message, ok = runner.run(), True
        except BondStopReached as exc:
            message, ok = exc.result.display(), exc.result.reason == "bond_max"
        except Exception as exc:
            message, ok = f"幕间任务中断：{exc}", False
            if runner:
                message += f"（已完成 {runner.completed} 个小关，战斗 {runner.battles} 场）"
        (mfaalog.info if ok else mfaalog.error)(f"[自动幕间副本] {message}")
        if not context.tasker.stopping:
            context.run_task("幕间-结果提示", pipeline_override={
                "幕间-结果提示": {"focus": {"Node.Action.Starting": html.escape(message)}}})
        return CustomAction.RunResult(success=ok)
