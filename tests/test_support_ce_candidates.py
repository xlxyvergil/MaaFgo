"""各助战槽位候选礼装的 OR、跨槽位 AND 及旧配置兼容回归。"""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "tools")]
from test_support_fallback import Context
import support_action as support
import generate_support_ce_options as generator
import prepare_android as android


class CandidateOptionTests(unittest.TestCase):
    def test_generated_options_match_all_formal_templates_and_keep_legacy_controls(self):
        for slot in generator.SLOTS:
            with self.subTest(slot=slot):
                path = ROOT / "assets/options/support" / f"{slot}.json"
                document = json.loads(path.read_text("utf8"))
                self.assertEqual(document, generator.build_options(slot, ROOT / "assets"))
                for status in generator.STATUSES:
                    key = f"{slot}-{status}"
                    options = document["option"]
                    self.assertEqual(options[key]["type"], "scan_select")
                    mode = options[f"{key}-选择方式"]
                    self.assertEqual(mode["default_case"], "单选")
                    self.assertEqual(mode["cases"][0]["option"], [key])
                    self.assertEqual(mode["cases"][1]["option"], [f"{key}-候选"])
                    candidates = options[f"{key}-候选"]
                    self.assertEqual((candidates["type"], candidates["default_case"]), ("checkbox", []))
                    files = {p.name for p in (ROOT / "assets/resource/base/image/lizhuang" / status).glob("*.png")}
                    self.assertEqual({case["name"] for case in candidates["cases"]}, files - set(support.EMPTY_CE))
                    for case in candidates["cases"]:
                        self.assertTrue((ROOT / "assets" / case["icon"]).is_file())

    def test_android_retains_multiselect_and_independent_attach_keys(self):
        source = android.load_interface(ROOT / "assets")
        converted = android.adapt_interface(source, ROOT / "assets")
        for slot in generator.SLOTS:
            for status in generator.STATUSES:
                key = f"{slot}-{status}"
                option = converted["option"][f"{key}-候选"]
                self.assertEqual(option, source["option"][f"{key}-候选"])
                attach = {}
                for case in option["cases"][:2]:
                    attach.update(case["pipeline_override"]["助战action"]["attach"])
                attach.update(source["option"][f"{key}-选择方式"]["cases"][1]["pipeline_override"]["助战action"]["attach"])
                self.assertEqual(support._custom_ce_candidates(attach, key), [case["name"] for case in option["cases"][:2]])

    def test_legacy_single_and_direct_list_inputs(self):
        for value, expected in (("目标.png", ["目标.png"]),
                                (["A.png", "B.png", " A.png "], ["A.png", "B.png"]),
                                ("", ["0_空.png"]), ([], ["0_空.png"])):
            self.assertEqual(support._custom_ce_candidates({"key": value}, "key"), expected)
        with self.assertRaises(ValueError):
            support._custom_ce_candidates({"key": [None]}, "key")


class CandidateMatchingTests(unittest.TestCase):
    def setUp(self):
        self.context = Context(enabled=False)
        self.context.attach.update(class_name="any", ce_bond="any")
        self.addCleanup(support.sessions.stop_owner, "")
        self.action = support.SupportAction()
        for name, value in {
            "_get_detector": NS(detect=Mock(return_value=[(0, 0, 100, 100, .99)])),
            "_init_scale": None, "_tap": None, "_shot_base": object(), "_ref_shift": (0, 0),
            "_match_ce": False, "_match_bond": True,
        }.items():
            setattr(self.action, name, Mock(return_value=value))
        # 未命中即在第一次滑动时停止，保证反例不会无限刷新。
        self.action._swipe = Mock(side_effect=lambda *args: setattr(self.context.tasker, "stopping", True))
        for name in ("time.sleep", "mfaalog.info", "mfaalog.error"):
            p = patch("support_action." + name)
            p.start()
            self.addCleanup(p.stop)

    def select_candidates(self, slot, status, names):
        key = f"{slot}-{status}"
        self.context.attach.update({f"{slot}-状态": status, f"{key}-多选": True,
                                   **{f"{key}-候选:{name}": True for name in names}})

    def run_search(self, grand=False):
        return self.action.run(self.context, NS(node_name="助战action", custom_action_param=json.dumps({
            "support_type": "grand" if grand else "normal"})))

    def test_normal_second_candidate_matches_and_stops_before_third(self):
        self.select_candidates("普通助战-礼装", "满破", ["A.png", "B.png", "C.png"])
        self.action._match_ce.side_effect = [False, True]
        self.assertTrue(self.run_search().success)
        self.assertEqual([call.args[-3] for call in self.action._match_ce.call_args_list], ["A.png", "B.png"])
        self.assertEqual(self.action._tap.call_args.args[-2:], (50, 50))

    def test_all_candidates_miss_does_not_select_entry(self):
        self.select_candidates("普通助战-礼装", "满破", ["A.png", "B.png"])
        self.assertFalse(self.run_search().success)
        self.assertEqual(self.action._match_ce.call_count, 2)
        self.assertEqual(self.action._tap.call_count, 3)  # 仅 ALL 页签点击。

    def test_grand_each_slot_needs_a_match_and_keeps_its_own_status(self):
        self.select_candidates("冠位助战-1号礼装", "满破", ["A.png", "B.png"])
        self.select_candidates("冠位助战-2号礼装", "非满破", ["C.png", "D.png"])
        self.context.attach["礼装状态"] = "非满破"  # 模拟第二格覆盖旧的共用状态。
        self.action._match_ce.side_effect = [False, True, False, True]
        self.assertTrue(self.run_search(grand=True).success)
        self.assertEqual([call.args[-3:] for call in self.action._match_ce.call_args_list], [
            (name, support.CE_ANCHOR_GRAND[slot], status)
            for slot, status, names in ((0, "满破", ["A.png", "B.png"]), (1, "非满破", ["C.png", "D.png"]))
            for name in names])
        self.action._match_bond.assert_called_once()

    def test_grand_first_slot_matching_does_not_replace_second_slot_requirement(self):
        self.select_candidates("冠位助战-1号礼装", "满破", ["A.png"])
        self.select_candidates("冠位助战-2号礼装", "满破", ["B.png", "C.png"])
        self.action._match_ce.side_effect = [True, False, False]
        self.assertFalse(self.run_search(grand=True).success)
        self.action._match_bond.assert_not_called()
        self.assertEqual(self.action._tap.call_count, 3)

    def test_unchecked_multiselect_is_unrestricted_without_using_stale_single(self):
        self.select_candidates("普通助战-礼装", "满破", [])
        self.context.attach["普通助战-礼装-满破"] = "旧条件.png"
        self.action._match_ce.return_value = True
        self.assertTrue(self.run_search().success)
        self.assertEqual(self.action._match_ce.call_args.args[-3], "0_空.png")

    def test_returning_to_single_ignores_old_candidates(self):
        self.select_candidates("普通助战-礼装", "满破", ["旧候选.png"])
        self.context.attach.update({"普通助战-礼装-满破-多选": False, "普通助战-礼装-满破": "旧单选.png"})
        self.action._match_ce.return_value = True
        self.assertTrue(self.run_search().success)
        self.assertEqual(self.action._match_ce.call_args.args[-3], "旧单选.png")


if __name__ == "__main__":
    unittest.main()
