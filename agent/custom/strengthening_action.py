"""强化列表独立调度，战斗复用主线单场，不走地图/通用返回主界面链。"""
import html
import time

from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction

import mfaalog
from battle.runtime.bond_stop_state import bond_stops
from bond_stop_action import BondStopReached
from interlude_action import recognition_detail, same_list
from main_story_action import StoryRunner, StoryStopped


class StrengtheningRunner(StoryRunner):
    def __init__(self, context, *, clock=time.monotonic, sleep=time.sleep):
        isolated = context.clone()
        isolated.override_pipeline({"主线-配置": {"attach": {
            "quest_limit": 999, "battle_mode": "native", "transition_timeout": 120,
            "use_spirit_stone": False, "retry_without_spirit_stone": False,
        }}})
        super().__init__(isolated, clock=clock, sleep=sleep)
        self.config = (context.get_node_data("强化-配置") or {}).get("attach", {})
        self.list_label = "強化クエスト" if self.config.get("server") == "jp" else "强化任务"
        self.list_path = ("カルデアゲート → 強化クエスト" if self.config.get("server") == "jp"
                          else "迦勒底之门 → 强化任务")
        self.limit = self.config.get("quest_limit", 999)
        self.timeout = self.config.get("transition_timeout", 120)
        if type(self.limit) is not int or not 1 <= self.limit <= 999:
            raise ValueError("挑战次数无法读取，无法开始强化本任务。请将“挑战次数上限”设为1至999后重试。")
        if type(self.timeout) is not int or not 10 <= self.timeout <= 600:
            raise ValueError("强化本界面等待时间无法读取。请恢复任务默认设置后重试。")
        self.auto_handle_restrictions = self.config.get("auto_handle_restrictions", True) is True
        self._first_scan = True
        self._network_retries = 0

    def panel(self, image):
        return bool(self.reco("强化-列表界面", image))

    def on_map(self, image):
        return False

    def interrupt(self, image, *, entering=False, forming=False):
        if entering:
            retry = self.reco("进本-断网重连", image)
            if retry:
                if self._network_retries >= 3:
                    raise StoryStopped(f"本关加载时已重试连接3次，仍未能继续。请检查游戏网络，点击游戏中的重试按钮；恢复后回到“{self.list_label}”列表再启动任务。")
                self._network_retries += 1
                mfaalog.warning(f"[挑战强化本] 加载连接中断，点击重试（本关第{self._network_retries}/3次）")
                self.action("进本-断网重连", retry)
                return True
        for name in ("剧情奖励-展示继续", "剧情奖励-礼装详情关闭",
                     "剧情奖励-从者加入详情关闭", "结算-奖励详情返回"):
            result = self.reco(name, image)
            if result:
                self.action(name, result)
                self.pause(1)
                return True
        return super().interrupt(image, entering=entering, forming=forming)

    def _ensure_list(self, image):
        if not self.panel(image):
            raise StoryStopped(f"查找强化本时已离开强化任务列表，无法继续选关。请打开“{self.list_path}”列表后重新启动“挑战强化本”。")

    def _uncertain(self, image):
        result = self.reco("强化-条目扫描", image)
        report = recognition_detail(result)
        return (not result or not {"row_count", "uncertain_count"} <= report.keys()
                or not report["row_count"] or bool(report["uncertain_count"]))

    def locate(self):
        image = self.frame()
        self._ensure_list(image)
        # 首次启动回顶；每场后从游戏返回位置继续，避免反复滚过已完成条目。
        if self._first_scan:
            for _ in range(400):
                previous = image
                self.action("强化-列表复位")
                image = self.frame()
                self._ensure_list(image)
                if same_list(previous, image):
                    self.pause(.6)
                    stable = self.frame()
                    self._ensure_list(stable)
                    if same_list(image, stable):
                        image = stable
                        break
            else:
                raise StoryStopped("强化任务列表复位超过上限，无法确认列表顶部。请手动拉到顶部后重试。")
            self._first_scan = False
        uncertain = False
        for _ in range(400):
            result = self.reco("强化-可挑战条目", image)
            if result:
                return result
            uncertain |= self._uncertain(image)
            previous = image
            self.action("强化-列表向下滑动")
            image = self.frame()
            self._ensure_list(image)
            if same_list(previous, image):
                self.pause(.6)
                stable = self.frame()
                self._ensure_list(stable)
                if same_list(image, stable):
                    result = self.reco("强化-可挑战条目", stable)
                    if result:
                        return result
                    if uncertain or self._uncertain(stable):
                        raise StoryStopped("列表中有无法确认是否可挑战的强化关卡，不能确定已全部完成。请检查当前条目及开启条件后重试。")
                    return None
            image = self.frame()
            self._ensure_list(image)
        raise StoryStopped("强化任务列表查找超过上限，尚未扫描完。请将列表拉到顶部后重试。")

    def enter(self, result):
        detail = recognition_detail(result)
        mfaalog.info(f"[挑战强化本] 选择：{detail.get('title', '已开启强化关卡')}，AP消耗={detail.get('ap_cost')}")
        self.action("强化-可挑战条目", result)
        deadline = self.clock() + self.timeout
        while self.clock() < deadline:
            self.pause(.6)
            image = self.frame()
            self.failure(image)
            if self.interrupt(image, entering=True):
                continue
            if self.battle_ready(image):
                return
        raise StoryStopped(f"点击强化关卡后未确认助战、编队或战斗界面，无法继续。请检查当前画面，手动返回“{self.list_label}”列表后重试。")

    def settle(self):
        try:
            return super().settle()
        except StoryStopped as exc:
            if str(exc) == "战后结算超时，未确认返回地图或副本列表":
                raise StoryStopped(f"战后结算超时，未确认返回“{self.list_label}”列表，无法继续选关。请检查当前奖励或提示窗口，手动处理并回到该列表后重试。") from exc
            raise

    def starting_at_selected_quest(self, image):
        if self.panel(image):
            return False
        if any(self.reco(name, image) for name in ("主线-助战界面", "进本-队伍确认")):
            return True
        raise StoryStopped(
            f"未识别到强化列表、助战选择或编队确认界面，无法开始任务。请打开“{self.list_path}”列表，"
            "或手动选择一个强化关卡，停在助战选择或编队确认页后启动“挑战强化本”。"
        )

    def run(self):
        selected_quest = self.starting_at_selected_quest(self.frame())
        while self.completed < self.limit:
            self.reset_restriction_state()
            self._network_retries = 0
            self._dialogue_choices.clear()
            if selected_quest:
                # 仅接管启动时已选的小关；其后必须回到强化列表按正常扫描流程选关。
                selected_quest = False
                mfaalog.info("[挑战强化本] 从助战或编队确认页继续当前小关，结算返回强化列表后自动选关")
            else:
                result = self.locate()
                if result is None:
                    return f"已扫描强化任务列表，没有可挑战的已开启强化关卡；完成 {self.completed} 个小关。"
                self.enter(result)
            bond_stops.battle_started(self.ctx.get_task_job().job_id, self.battles + 1,
                another_battle_planned=False if self.completed + 1 == self.limit else None)
            self.fight()
            self.settle()
            self.battles += 1
            self.completed += 1
            mfaalog.info(f"[挑战强化本] 已完成 {self.completed}/{self.limit} 个小关，已确认返回强化任务列表")
        return f"达到挑战次数上限：完成 {self.completed} 个强化小关。"


@AgentServer.custom_action("challenge_strengthening")
class StrengtheningAction(CustomAction):
    def run(self, context, argv):
        runner = None
        bond_stops.begin(context.get_task_job().job_id)
        try:
            runner = StrengtheningRunner(context)
            message, ok = runner.run(), True
        except BondStopReached as exc:
            message, ok = exc.result.display(), exc.result.reason == "bond_max"
        except Exception as exc:
            message, ok = f"强化本任务中断：{exc}", False
            if runner:
                message += f"（已完成 {runner.completed} 个小关）"
        (mfaalog.info if ok else mfaalog.error)(f"[挑战强化本] {message}")
        if not context.tasker.stopping:
            context.run_task("强化-结果提示", pipeline_override={
                "强化-结果提示": {"focus": {"Node.Action.Starting": html.escape(message)}}})
        return CustomAction.RunResult(success=ok)
