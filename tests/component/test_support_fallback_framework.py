"""真实 MaaFramework 路由验证，使用内存画面，不连接设备或点击游戏。"""
import copy
import json
import sys
import tempfile
import unittest
from itertools import product
from pathlib import Path
from unittest.mock import Mock, patch

import maa
import numpy as np
from maa.custom_action import CustomAction
from maa.library import Library
from maa.resource import Resource
from maa.tasker import Tasker
from maa.define import LoggingLevelEnum

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
from test_support_fallback import Context
from test_formation_framework import MemoryController
from formation_test_support import ROOT, battle_action as battle
from support_action import SupportAction
from battle.runtime.formation_session import session_override, sessions


class FallbackFrameworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Library.open(Path(maa.__file__).parent / "bin")
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)

    def test_fallback_crosses_nested_contexts_and_bypasses_both_formation_modes(self):
        self.addCleanup(sessions.stop_owner, "")
        source = json.loads((ROOT / "assets/resource/base/pipeline/进本流程.json").read_text("utf-8"))
        options = json.loads((ROOT / "assets/options/Chaldea导入手动输入.json").read_text("utf-8"))
        support_options = json.loads((ROOT / "assets/options/原生自动战斗助战方式.json").read_text("utf-8"))["option"]
        switch = support_options["多次查找失败随机选择助战"]
        enabled = next(case["pipeline_override"] for case in switch["cases"] if case["name"] == "Yes")
        servant_map = SupportAction._get_servant_map()
        share = json.loads((ROOT / "tests/fixtures/chaldea/雨暗阿_21086_94061640.json").read_text("utf-8"))
        for support_name, active in product(("助战action", "Chaldea助战action"), (False, True)):
            for case in options["option"]["Chaldea编队方式"]["cases"]:
                with self.subTest(support=support_name, fallback=active, mode=case["name"]):
                    sessions.stop_owner("")
                    seen = []
                    nodes = {name: copy.deepcopy(source[name]) for name in (
                        "进本-队伍确认", "进本-编队处理", "进本-助战兜底跳过编队",
                        "助战兜底-选择首个助战", "进本-选择助战",
                    )}
                    formation = case["pipeline_override"]["进本-编队处理"]["next"][0]
                    nodes["进本-编队处理"].update(case["pipeline_override"]["进本-编队处理"])
                    for name in ("进本-队伍确认", "进本-选择助战"):
                        nodes[name]["recognition"] = {"type": "DirectHit"}
                    nodes["进本-选择助战"]["next"] = ["不应进入首个助战原有后续"]
                    # 固定的 ALL 页签点击由记录动作替代，内存控制器拒绝实际输入。
                    nodes["助战兜底-选择首个助战"]["action"] = {
                        "type": "Custom", "param": {"custom_action": "record"}}
                    nodes["助战兜底-选择首个助战"]["repeat_delay"] = 0
                    for name in (formation, "执行羁绊补齐自动编队", "进本-实际点击队伍确认",
                                 "不应进入首个助战原有后续", "进本-点击队伍确认", "编队身份-采集",
                                 "原生战斗-校验茶壶", "原生战斗-兜底校验茶壶"):
                        nodes[name] = {"action": {"type": "Custom", "param": {"custom_action": "record"}}}
                    if case["name"] == "自动":
                        nodes[formation]["next"] = ["执行羁绊补齐自动编队"]
                    nodes["进本-选择助战"]["action"] = {
                        "type": "Custom", "param": {"custom_action": "record"}}
                    nodes["助战刷新"] = {}
                    attach = dict(Context().attach)
                    attach.update(enabled[support_name]["attach"])
                    if support_name == "Chaldea助战action":
                        attach.update(chaldea_enabled=True, chaldea_import_source="test-share")
                    nodes[support_name] = {
                        "attach": attach,
                        "action": {"type": "Custom", "param": {"custom_action": "support_action", "custom_action_param": {}}},
                        "next": ["进本-队伍确认"],
                    }
                    if support_name == "Chaldea助战action":
                        nodes[support_name]["action"]["param"]["custom_action_param"] = {"source_mode": "chaldea"}
                    nodes["资源包配置"] = {"attach": {"resource_package": "base"}}
                    nodes["编队身份-会话"] = {"attach": {"session_id": ""}}
                    nodes["root"] = {"action": {"type": "Custom", "param": {"custom_action": "init"}},
                                     "next": [support_name]}
                    for node in nodes.values():
                        node.update(pre_delay=0, post_delay=0, rate_limit=1, timeout=100)

                    class Init(CustomAction):
                        def run(self, context, argv):
                            root = context.get_task_job().job_id
                            return context.override_pipeline(session_override(sessions.begin(root)))

                    class Record(CustomAction):
                        def run(self, context, argv):
                            seen.append(argv.node_name)
                            return True

                    action = SupportAction()
                    for name, value in {
                        "_get_servant_map": servant_map if support_name == "Chaldea助战action" else {"test": {"images": ["test.png"]}},
                        "_get_detector": Mock(detect=Mock(return_value=[(0, 0, 100, 100, .99)])),
                        "_init_scale": None, "_tap": None, "_swipe": None,
                        "_shot_base": object(), "_ref_shift": (0, 0),
                        "_match_servant": not active, "_match_ce": True,
                        "_check_active_view": True, "_is_connecting": False,
                        "_match_passive": True, "_match_bond": True,
                    }.items():
                        setattr(action, name, Mock(return_value=value))
                    with tempfile.TemporaryDirectory() as temp:
                        folder = Path(temp, "pipeline")
                        folder.mkdir()
                        (folder / "test.json").write_text(json.dumps(nodes, ensure_ascii=False), "utf-8")
                        resource = Resource()
                        resource.use_cpu()
                        self.assertTrue(resource.post_bundle(temp).wait().succeeded)
                        resource.register_custom_action("init", Init())
                        resource.register_custom_action("record", Record())
                        resource.register_custom_action("support_action", action)
                        resource.register_custom_recognition("support_fallback_active", battle.SupportFallbackActive())
                        controller = MemoryController(np.zeros((720, 1280, 3), dtype=np.uint8))
                        self.assertTrue(controller.post_connection().wait().succeeded)
                        tasker = Tasker()
                        self.assertTrue(tasker.bind(resource, controller))
                        with patch("support_action.time.sleep"), patch("support_action.MAX_SWIPE_BEFORE_REFRESH", 1), \
                             patch("support_action.fetch_share_data", return_value=(share, None, None)) as fetch, \
                             patch("support_action.mfaalog.info"), patch("support_action.mfaalog.error") as errors:
                            self.assertTrue(tasker.post_task("root").wait().succeeded, str(errors.call_args_list))
                            if support_name == "Chaldea助战action":
                                fetch.assert_called_once_with("test-share")
                            else:
                                fetch.assert_not_called()
                        self.assertEqual(controller.inputs, 0)
                    expected = (["助战兜底-选择首个助战"] * 3 + ["进本-选择助战", "原生战斗-兜底校验茶壶", "进本-实际点击队伍确认"]
                                if active else [formation] + (["执行羁绊补齐自动编队"] if case["name"] == "自动" else []))
                    self.assertEqual(seen, expected)
        sessions.stop_owner("")


if __name__ == "__main__":
    unittest.main()
