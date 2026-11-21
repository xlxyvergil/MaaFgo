"""白纸化地球的道具获取途径及战前回退。"""

from dataclasses import dataclass
from html import escape
import re
import unicodedata

from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition

import mfaalog


CONFIG = "OrdealCall-替代方式"
SUPPORT = "OrdealCall-已到助战"
STORM_POD_EMPTY = "OrdealCall-风暴罐不足"
BATTLE_SCREEN = "进本-战斗主界面已出现"
NATIVE_ENGINE = "原生自动战斗_多次入口"
BBC_ENGINE = "执行bbc战斗"
MAP_RECOVERY = {
    "Global_Null": {
        "next": [STORM_POD_EMPTY, "全局-战前吃体力"],
        "timeout": 1600,
        "on_error": [],
    },
}


@dataclass
class MapBattleAttempt:
    started: bool = False
    bbc_idle: bool = True


_map_attempts: dict[int, MapBattleAttempt] = {}


def map_battle_attempt(context):
    return _map_attempts.get(context.get_task_job().job_id)


def _attach(context):
    return (context.get_node_data(CONFIG) or {}).get("attach", {})


def _normalized(text):
    # OCR can confuse voicing marks and the visually similar カ/力. The map
    # independently verifies the selected quest's title template before opening it.
    normalized = unicodedata.normalize("NFKD", text)
    return re.sub(r"[^\w]", "", normalized).replace("カ", "力")


def _succeeded(task):
    return task is not None and task.status.succeeded


def _completed(task, node_name):
    return task is not None and any(node.name == node_name and node.completed for node in task.nodes)


def _hit(context, node, image, override=None):
    result = context.run_recognition(node, image, override)
    return result is not None and result.hit


def _notify(context, message, *, error=True):
    (mfaalog.error if error else mfaalog.info)(message)
    context.run_task("OrdealCall-用户提示", pipeline_override={
        "OrdealCall-用户提示": {"focus": {"Node.Action.Starting": escape(message)}},
    })


def _catalog_overrides(config):
    quest = config["quest"]
    return {
        "OrdealCall-图鉴-道具图标": {"template": f"ordealcall/catalog/{config['item']}.png"},
        "OrdealCall-图鉴-点关卡": {"template": f"ordealcall/{quest}.png"},
    }


@AgentServer.custom_recognition("ordeal_call_source")
class OrdealCallSource(CustomRecognition):
    def analyze(self, context, argv):
        titles = [_normalized(title) for title in _attach(context).get("titles", [])]
        if not titles or not _hit(context, "OrdealCall-图鉴-道具详情", argv.image):
            return None
        globes = context.run_recognition("OrdealCall-图鉴-白纸化地球图标", argv.image)
        if globes is None or not globes.hit:
            return None
        ocr_node = "OrdealCall-图鉴-读取关卡标题"
        for globe in globes.filtered_results:
            x, y, width, height = globe.box
            # The title can wrap to a second line. Read just the text next to this globe.
            result = context.run_recognition(ocr_node, argv.image, {
                ocr_node: {"roi": [470, y - 21, 487, 61]},
            })
            if result is None or not result.hit:
                continue
            lines = sorted(result.filtered_results, key=lambda line: (line.box[1] // 12, line.box[0]))
            label = "".join(line.text for line in lines)
            if any(title in _normalized(label) for title in titles):
                return self.AnalyzeResult((x, y, width, height), {"title": label})
            mfaalog.debug(f"[OrdealCall] 白纸化地球获取途径识别为 {label!r}")
        return None


@AgentServer.custom_action("ordeal_call_mark_battle")
class MarkOrdealCallBattle(CustomAction):
    def run(self, context, argv):
        attempt = map_battle_attempt(context)
        if attempt is not None:
            attempt.started = True
        return True


@AgentServer.custom_action("ordeal_call_entry")
class OrdealCallEntry(CustomAction):
    def run(self, context, argv):
        config = _attach(context)
        if config["mode"] == "mixed":
            return self._mixed(context, config)
        return self._catalog_and_battle(context, config)

    def _mixed(self, context, config):
        quest = config["quest"]
        detail = context.run_task("OrdealCall", pipeline_override=MAP_RECOVERY)
        if _completed(detail, STORM_POD_EMPTY):
            return self._storm_empty(context, quest)
        if context.tasker.stopping:
            return False
        if _completed(detail, SUPPORT):
            root_id = context.get_task_job().job_id
            attempt = MapBattleAttempt()
            _map_attempts[root_id] = attempt
            try:
                if self._battle(context, config, watch_start=True):
                    return True
            finally:
                _map_attempts.pop(root_id, None)
            if context.tasker.stopping:
                return False
            if attempt.started:
                _notify(context, f"「{quest}」开战后中断。请查看上方报错和当前游戏画面，处理后再重试。")
                return False
            if not attempt.bbc_idle:
                _notify(context, "未能确认 BBC 已停止，无法切换进图方式。请先在 BBC 中停止任务，再重试。")
                return False
            if not self._at_prebattle(context):
                _notify(context, f"「{quest}」报错后无法确认游戏仍处于战前界面。请查看当前游戏画面，返回主界面后重试。")
                return False

        if "item" not in config:
            return self._no_source(context, quest, mixed=True)
        _notify(context, f"「{quest}」从地图进本失败，正在回主界面并通过道具获取途径重试。", error=False)
        detail = context.run_task("回主界面", pipeline_override={
            "回主界面": {"on_error": [], "timeout": 4000},
            "主界面判断-返回": {"max_hit": 8, "on_error": []},
            "有弹窗返回": {"max_hit": 4, "on_error": []},
            "Global_Null": {"next": [], "on_error": []},
        })
        if not _completed(detail, "无弹窗"):
            if not context.tasker.stopping:
                _notify(context, f"「{quest}」地图进本失败后未能回到主界面，无法打开道具列表。请手动返回主界面后重试。")
            return False
        return self._catalog_and_battle(context, config)

    def _catalog_and_battle(self, context, config):
        quest = config["quest"]
        if "item" not in config:
            return self._no_source(context, quest, mixed=False)
        item_name = config["item_name"]
        stages = [
            ("OrdealCall-图鉴-打开道具列表", {},
             f"未能打开所持道具列表，无法查找「{quest}」的掉落道具。请回到主界面重试，或将「进图方式」改为「从地图进本」。"),
            ("OrdealCall-图鉴-找道具", _catalog_overrides(config),
             f"所持道具列表里未找到「{item_name}」，无法从图鉴进入「{quest}」。请确认持有该道具，或将「进图方式」改为「从地图进本」。"),
            ("OrdealCall-图鉴-找获取途径", {},
             f"「{item_name}」的获取途径中未找到可进入的「{quest}」。请确认关卡已解锁，或将「进图方式」改为「从地图进本」。"),
        ]
        for entry, override, error in stages:
            if context.tasker.stopping:
                return False
            detail = context.run_task(entry, pipeline_override=override)
            if not _succeeded(detail):
                if not context.tasker.stopping:
                    _notify(context, error)
                return False

        entry = ("OrdealCall-图鉴-星光之砂进关卡" if config.get("source_opens_map")
                 else "OrdealCall-图鉴-进关卡")
        detail = context.run_task(entry, pipeline_override={
            **MAP_RECOVERY, **_catalog_overrides(config),
        })
        if context.tasker.stopping:
            return False
        if _completed(detail, STORM_POD_EMPTY):
            return self._storm_empty(context, quest)
        if not _completed(detail, SUPPORT):
            _notify(context, f"从道具获取途径跳转后，未能进入「{quest}」的助战选择。请检查当前游戏画面，或将「进图方式」改为「从地图进本」。")
            return False
        if self._battle(context, config):
            return True
        if not context.tasker.stopping:
            _notify(context, f"「{quest}」已通过图鉴到达助战选择，随后战斗任务报错。请查看上方报错，处理后再重试。")
        return False

    def _battle(self, context, config, *, watch_start=False):
        if context.tasker.stopping:
            return False
        if config["engine"] == "bbc":
            context.clear_hit_count(BBC_ENGINE)
            override = {node: {"on_error": []} for node in (BBC_ENGINE, "bbc战斗", "执行BBC任务")}
            override["执行BBC任务"]["attach"] = {"ordeal_route": True}
            detail = context.run_task(BBC_ENGINE, pipeline_override=override)
            if not _succeeded(detail) or context.tasker.stopping:
                return False
            attempt = map_battle_attempt(context)
            if attempt is not None and (not attempt.bbc_idle or (not attempt.started and self._at_prebattle(context))):
                return False
            return _succeeded(context.run_task("执行战斗结束"))
        context.clear_hit_count(NATIVE_ENGINE)
        override = {
            BATTLE_SCREEN: {"action": {"type": "Custom", "param": {"custom_action": "ordeal_call_mark_battle"}}},
        } if watch_start else {}
        return _succeeded(context.run_task(NATIVE_ENGINE, pipeline_override=override))

    def _at_prebattle(self, context):
        image = context.tasker.controller.post_screencap().wait().get()
        if image is None or _hit(context, BATTLE_SCREEN, image):
            return False
        return _hit(context, "OrdealCall-战前界面", image)

    def _no_source(self, context, quest, *, mixed):
        reason = "地图进本失败，且" if mixed else ""
        _notify(context, f"「{quest}」{reason}没有游戏内道具获取途径，无法通过图鉴进本。请将「进图方式」改为「从地图进本」。")
        return False

    def _storm_empty(self, context, quest):
        _notify(context, f"「{quest}」需要风暴罐，当前数量不足，无法挑战。请获取风暴罐后再重试。")
        return False
