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


_FRONTLINE_SHORTAGE = "幕间-先发成员不足"
_FILL_THIRD_FRONTLINE = (
    "请回到“队伍确认”页，点第三个先发位的“选择”，补上一名符合关卡限制的从者后，"
    "重新启动“自动幕间副本”。"
)


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
        self._skip_next_reset = False
        self._reentered_at = None

    def _handle_restricted_formation(self, image, *, formation_confirmed=False):
        shortage = self.reco(_FRONTLINE_SHORTAGE, image)
        if not shortage:
            return super()._handle_restricted_formation(image, formation_confirmed=formation_confirmed)
        if not self.auto_handle_restrictions:
            raise StoryStopped(f"游戏提示“先发成员不足”，当前已关闭“自动处理编队限制情况”。{_FILL_THIRD_FRONTLINE}")
        if self._restriction_auto_done:
            raise StoryStopped(f"游戏自动编队后仍提示“先发成员不足”。{_FILL_THIRD_FRONTLINE}")

        self._mark_restricted("先发成员不足")
        self._restriction_direct_start = False
        self._restriction_action(_FRONTLINE_SHORTAGE, shortage)
        self._recover_frontline_formation()
        return True

    def _recover_frontline_formation(self):
        deadline = self.clock() + min(self.timeout, 30)
        auto_opened = False
        while self.clock() < deadline:
            image = self.frame()
            if self.reco(_FRONTLINE_SHORTAGE, image):
                if self._restriction_auto_done:
                    raise StoryStopped(f"游戏自动编队后仍提示“先发成员不足”。{_FILL_THIRD_FRONTLINE}")
            elif auto_opened and self.reco("主线限制-编成条件未满足", image):
                raise StoryStopped(f"游戏未能按关卡条件完成自动编队。{_FILL_THIRD_FRONTLINE}")
            elif auto_opened and super()._handle_restricted_formation(image):
                # 自动编成弹窗后方的“开始任务”仍可命中，先处理确认和编队决定。
                continue
            elif self.reco("进本-队伍确认页可开始", image):
                if self._restriction_auto_done:
                    if self.reco("幕间-第三个先发位空缺", image):
                        raise StoryStopped(f"游戏自动编队后，第三个先发位仍为空。{_FILL_THIRD_FRONTLINE}")
                    return
                if not auto_opened and (button := self.reco("主线限制-打开自动编队", image)):
                    self._restriction_action("主线限制-打开自动编队", button)
                    auto_opened = True
                    continue
            elif not auto_opened and super()._handle_restricted_formation(image):
                continue
            self.pause()
        raise StoryStopped(f"未能确认游戏自动编队的结果，幕间无法继续。{_FILL_THIRD_FRONTLINE}")

    def _returned_to_list(self):
        self._map_opened_at = None
        self._skip_next_reset = False

    def _wait_navigation(self, node, message):
        deadline = self.clock() + self.timeout
        while self.clock() < deadline:
            image = self.frame()
            if self.interrupt(image):
                continue
            if self.reco(node, image):
                return image
            self.pause(.6)
        raise StoryStopped(message)

    def _find_navigation_entry(self, panel, search, scroll, message):
        # 固定查找/翻页循环在Pipeline中执行，命中计数随每轮导航重置。
        self._wait_navigation(panel, message)
        self.ctx.clear_hit_count(scroll)
        try:
            self.task("幕间-导航列表归位")
            self._wait_navigation(panel, message)
            self.task(search)
        except StoryStopped as exc:
            self.check_stop()
            raise StoryStopped(message) from exc

    def reenter_from_home(self):
        if self._reentered_at == self.completed:
            raise StoryStopped("重新进入幕间列表后没有完成新的小关，又遇到无可挑战幕间的地图，已停止重复返回。请检查该幕间的开启条件，并手动打开“幕间物语”列表后重试。")
        self._reentered_at = self.completed
        mfaalog.info("[自动幕间副本] 地图已扫描完且没有“前往幕间物语”，回主界面重新进入幕间列表")
        original = self.ctx
        self.ctx = original.clone()
        try:
            # 命中计数属于同一任务；每轮重置，并只在本次克隆中限制共享返回链。
            home_nodes = ("回主界面", "主界面判断-main-ui", "主界面判断-返回",
                          "回主界面的指引", "无弹窗", "有弹窗返回",
                          "判断是否要初始化", "初始化界面", "初始化完成")
            override = {}
            for name in home_nodes:
                self.ctx.clear_hit_count(name)
                data = self.ctx.get_node_data(name) or {}
                override[name] = {"on_error": [], "timeout": min(self.timeout * 1000, 10000),
                                  "max_hit": min(data.get("max_hit", 6), 6)}
            home_error = "未能返回主界面，无法重新进入幕间列表。请手动回到主界面，打开“迦勒底之门”中的“幕间物语”后重新启动任务。"
            try:
                self.task("回主界面", override)
            except StoryStopped as exc:
                self.check_stop()
                raise StoryStopped(home_error) from exc
            self._wait_navigation("幕间-主界面确认", home_error)
            gate_error = "未找到主界面的“迦勒底之门”入口，无法继续导航。请手动打开“迦勒底之门”中的“幕间物语”后重新启动任务。"
            self._find_navigation_entry("幕间-主界面确认", "幕间-查找迦门入口", "幕间-主界面查找下滑", gate_error)
            self._wait_navigation("幕间-迦勒底之门界面", "点击“迦勒底之门”后未确认进入该页面。请检查游戏画面，手动打开“幕间物语”列表后重新启动任务。")
            tab_error = "在“迦勒底之门”中未找到“幕间物语”页签，无法继续导航。请手动打开该页签后重新启动“自动幕间副本”。"
            self._find_navigation_entry("幕间-迦勒底之门界面", "幕间-查找幕间页签", "幕间-迦门查找下滑", tab_error)
            self._wait_navigation("幕间-列表界面", "点击“幕间物语”后未确认进入幕间列表。请检查游戏画面，手动打开“幕间物语”列表后重新启动任务。")
            self._returned_to_list()
            mfaalog.info(f"[自动幕间副本] 已重新进入幕间列表，继续查找；累计完成 {self.completed}/{self.limit} 个小关")
        finally:
            self.ctx = original

    def total_list(self, image):
        return bool(self.reco("幕间-列表界面", image))

    def on_map(self, image):
        if self.reco("幕间-返回列表", image):
            return True
        # 同地图切换其他幕间后，专属返回入口可能消失。关闭仅作识别，不点击。
        return bool(self.reco("幕间-关闭按钮地图", image)) and not self.total_list(image)

    def panel(self, image):
        return self.total_list(image) or self.on_map(image)

    def _check_formation_handoff(self, image):
        # 只用当前截图确认页面。操作后若出现剧情或奖励，下一轮恢复完整检查。
        for page in (
            "进本-战斗主界面已出现", "进本-队伍确认",
            "助战选择-确认按钮锚点", "助战选择-界面标题锚点",
        ):
            if self.reco(page, image):
                return super().interrupt(image, forming=True, confirmed_page=page), page
        return super()._check_formation_handoff(image)

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
        """首次复位、完成小关后直接扫描；确认触底且无未知条目才报告清空。"""
        up = "幕间-地图列表复位" if map_mode else "幕间-列表复位"
        down = "幕间-地图列表向下滑动" if map_mode else "幕间-列表向下滑动"
        node = "幕间-地图关卡" if map_mode else "幕间-可挑战条目"
        # 总列表还含大量未开启条目；不能用当前可见的少数幕间推定列表长度。
        bound = 12 if map_mode else 400
        self._ensure_list(image, map_mode)
        # 战斗/纯剧情完成后，游戏返回列表顶部；只跳过紧接着的一次复位。
        # 任务首次启动、新进入地图，以及扫描空地图后切回总列表仍须复位。
        skip_reset = self._skip_next_reset
        self._skip_next_reset = False
        if not skip_reset:
            # 一次动作后等待新截图，两次稳定才认定边界。
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
        image = self.frame()
        if self.total_list(image):
            self._returned_to_list()
            return
        result = self.reco("幕间-返回列表", image)
        if not result:
            self._ensure_list(image, True)
            # 扫描结束与返回前可能有加载/条目更新；出现新目标则交回locate继续扫描。
            if self.reco("幕间-地图关卡", image):
                return
            if self._scan_uncertain(image, True):
                raise StoryStopped("返回前的地图列表出现了无法确认的幕间，无法确定是否已扫描完成。请检查当前条目，手动打开“幕间物语”列表后重新启动任务。")
            self.reenter_from_home()
            return
        self.action("幕间-返回列表", result)
        deadline = self.clock() + self.timeout
        while self.clock() < deadline:
            self.pause(.6)
            image = self.frame()
            if self.interrupt(image):
                continue
            if self.total_list(image):
                self._returned_to_list()
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
                if (self._map_opened_at == self.completed
                        and self.reco("幕间-返回列表", image)):
                    raise StoryStopped("已到达故事舞台，但没有找到可挑战的幕间关卡。请检查地图中的幕间及开启条件后重试。")
                self.return_to_list()
                deadline = self.clock() + self.timeout
                continue
            self.pause(.6)
        raise StoryStopped("未识别到幕间列表或故事舞台，无法继续。请回到“幕间物语”列表后启动任务。")

    def _story_visible(self, image):
        # 关闭跳剧情也需要观察剧情出现；只做识别，动作仍尊重原选项。
        probe = self.ctx.clone()
        for name in ("跳过剧情-强制选项", "跳过剧情-灰色继续", "跳过剧情-点击跳过"):
            result = probe.run_recognition(name, image, {name: {"enabled": True}})
            if result and result.hit:
                return True
        return False

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

    def starting_at_selected_quest(self, image):
        if self.panel(image):
            return False
        if any(self.reco(name, image) for name in ("主线-助战界面", "进本-队伍确认")):
            return True
        raise StoryStopped(
            "未识别到幕间列表、故事舞台、助战选择或编队确认界面，无法开始任务。"
            "请打开“幕间物语”列表，或手动选择一个幕间小关，停在助战选择或编队确认页后启动“自动幕间副本”。"
        )

    def run(self):
        selected_quest = self.starting_at_selected_quest(self.frame())
        while self.completed < self.limit:
            self.reset_restriction_state()
            self._dialogue_choices.clear()
            if selected_quest:
                # 只接管启动时已选的小关；结算后仍须经幕间列表或故事舞台选关。
                selected_quest = False
                kind = "battle"
                mfaalog.info("[自动幕间副本] 从助战或编队确认页继续当前小关，结算返回幕间列表或故事舞台后自动选关")
            else:
                target = self.locate()
                if target is None:
                    return f"已扫描幕间列表，没有可挑战的已开启幕间；完成 {self.completed} 个小关，其中战斗 {self.battles} 场。"
                kind = self.enter(target)
            if kind == "map":
                continue
            if kind == "battle":
                # 未到小关上限时允许下场复核，结算后由选关流程确认是否还有可打关卡。
                bond_stops.battle_started(self.ctx.get_task_job().job_id, self.battles + 1,
                    another_battle_planned=self.completed + 1 < self.limit)
                self.fight()
                self.settle()
                self.battles += 1
            self.completed += 1
            self._skip_next_reset = True
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
