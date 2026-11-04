"""不要求英灵时仅按礼装查找的离线回归，不连接设备。"""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_support_fallback import Context
import support_action as support

ROOT = Path(__file__).resolve().parents[1]


class CeOnlySupportTests(unittest.TestCase):
    def setUp(self):
        options = json.loads((ROOT / "assets/options/support/英灵选择.json").read_text("utf-8"))["option"]
        self.selection = options["英灵选择"]
        self.any_case = next(case for case in self.selection["cases"] if case["name"] == "不要求英灵")
        self.context = Context(enabled=False)
        self.addCleanup(support.sessions.stop_owner, "")
        self.context.attach.update(self.any_case["pipeline_override"]["助战action"]["attach"])
        # 模拟从已有英灵配置切换过来，旧的高等级条件不能继续参与匹配。
        self.context.attach.update({
            "servant": "stale-servant", "np_level": 5, "level": 120,
            "普通助战-礼装-满破": "目标礼装.png",
            **{f"skill_active_{i}": 10 for i in range(1, 4)},
            **{f"skill_passive_{i}": 10 for i in range(1, 6)},
        })
        self.action = support.SupportAction()
        for name, value in {
            "_get_servant_map": {"test": {"images": ["test.png"]}},
            "_get_detector": NS(detect=Mock(return_value=[(0, 0, 100, 100, .99)])),
            "_init_scale": None, "_tap": None, "_swipe": None,
            "_shot_base": object(), "_ref_shift": (0, 0),
            "_match_servant": False, "_match_ce": True,
            "_check_active_view": False, "_match_passive": False,
            "_match_bond": True, "_is_connecting": False,
        }.items():
            setattr(self.action, name, Mock(return_value=value))
        for name in ("time.sleep", "mfaalog.info", "mfaalog.error"):
            p = patch("support_action." + name)
            p.start()
            self.addCleanup(p.stop)

    def run_search(self, support_type="normal"):
        return self.action.run(self.context, NS(
            node_name="助战action", custom_action_param=json.dumps({"support_type": support_type})))

    def assert_servant_checks_skipped(self):
        for name in ("_get_servant_map", "_match_servant", "_check_active_view", "_match_passive"):
            getattr(self.action, name).assert_not_called()

    def test_option_is_first_and_keeps_existing_default(self):
        self.assertIs(self.selection["cases"][0], self.any_case)
        self.assertTrue(self.any_case["label"].startswith("0-"))
        self.assertFalse(self.any_case.get("option"))
        self.assertEqual(self.selection["default"], "剑士")

    def test_normal_skips_stale_servant_requirements_and_selects_matching_ce(self):
        detector = self.action._get_detector.return_value
        detector.detect.return_value.append((0, 200, 100, 300, .99))
        self.action._match_ce.side_effect = [False, True]
        self.assertTrue(self.run_search().success)
        self.assert_servant_checks_skipped()
        self.assertEqual(self.action._match_ce.call_count, 2)
        self.assertEqual(self.action._match_ce.call_args.args[-3:],
                         ("目标礼装.png", support.CE_ANCHOR_NORMAL, "满破"))
        controller = self.context.tasker.controller
        self.assertEqual(self.action._tap.call_args_list,
                         [call(controller, *support.CLASS_TABS["ALL"])] * 3 + [call(controller, 50, 250)])
        self.action._swipe.assert_not_called()

    def test_no_servant_or_level_fields_are_required(self):
        for key in list(self.context.attach):
            if key in ("servant", "level", "np_level") or key.startswith("skill_"):
                del self.context.attach[key]
        self.assertTrue(self.run_search().success)
        self.assert_servant_checks_skipped()

    def test_grand_retains_both_ce_slots_and_bond_requirement(self):
        self.context.attach.update({
            "冠位助战-1号礼装-满破": "礼装1.png",
            "冠位助战-2号礼装-满破": "礼装2.png", "ce_bond": "50np",
        })
        detector = self.action._get_detector.return_value
        detector.detect.return_value.extend([(0, 200, 100, 300, .99), (0, 400, 100, 500, .99)])
        # 第一条第二格礼装不符，第二条羁绊状态不符，第三条才可点击。
        self.action._match_ce.side_effect = [True, False, True, True, True, True]
        self.action._match_bond.side_effect = [False, True]
        self.assertTrue(self.run_search("grand").success)
        self.assert_servant_checks_skipped()
        self.assertEqual([c.args[-3:] for c in self.action._match_ce.call_args_list], [
            (name, anchor, "满破")
            for _ in range(3)
            for name, anchor in zip(("礼装1.png", "礼装2.png"), support.CE_ANCHOR_GRAND)
        ])
        self.assertEqual(self.action._match_bond.call_count, 2)
        self.assertEqual(self.action._tap.call_args, call(self.context.tasker.controller, 50, 450))

    def test_ce_mismatch_continues_to_refresh_and_uses_existing_fallback(self):
        self.context.attach["fallback_after_refresh"] = True
        self.action._match_ce.return_value = False
        with patch("support_action.MAX_SWIPE_BEFORE_REFRESH", 2):
            self.assertTrue(self.run_search().success)
        self.assert_servant_checks_skipped()
        self.assertEqual(self.context.refreshes, 10)
        self.assertEqual(self.action._match_ce.call_count, 33)
        self.assertEqual(self.context.entries[-1], "助战兜底-选择首个助战")

    def test_switching_back_to_class_restores_servant_matching(self):
        sword = next(case for case in self.selection["cases"] if case["name"] == "剑士")
        self.context.attach.update(sword["pipeline_override"]["助战action"]["attach"])
        self.context.attach["servant"] = "test"
        self.context.attach.update({f"skill_passive_{i}": 0 for i in range(1, 6)})
        self.action._match_servant.return_value = True
        self.action._check_active_view.return_value = True
        self.assertTrue(self.run_search().success)
        self.action._match_servant.assert_called_once()
        self.action._check_active_view.assert_called_once()
        self.assertEqual(self.action._check_active_view.call_args.args[-2:], (5, 120))
        self.assertEqual(self.action._tap.call_args_list[0],
                         call(self.context.tasker.controller, *support.CLASS_TABS["剑士"]))

    def test_missing_servant_still_fails_for_specific_class(self):
        self.context.attach.update(class_name="剑士", servant="")
        self.assertFalse(self.run_search().success)
        self.action._get_detector.assert_not_called()


if __name__ == "__main__":
    unittest.main()
