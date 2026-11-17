"""MIX searches every support slot without weakening servant filters; no device."""
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
FLAG = "search_all_support_slots"


class SupportClassTabSelectorTests(unittest.TestCase):
    def test_default_keeps_specific_class_all_and_other_tabs(self):
        for attach in ({}, {FLAG: False}):
            for name, filter_value, expected in (
                ("剑士", "def", (159, 130)), ("OTHER", "def", (629, 130)),
                ("未知职介", "def", (629, 130)), ("剑士", "all", (91, 130)),
                ("", "def", None),
            ):
                with self.subTest(attach=attach, name=name, filter_value=filter_value):
                    self.assertEqual(support._support_class_tab(attach, name, filter_value), expected)

    def test_only_explicit_true_enables_mix_and_overrides_all_filter(self):
        for filter_value in ("def", "all"):
            self.assertEqual(support._support_class_tab({FLAG: True}, "OTHER", filter_value), (698, 130))
        for value in (None, "true", "false", 1, 0, [], {}):
            with self.subTest(value=value):
                self.assertEqual(support._support_class_tab({FLAG: value}, "剑士", "def"), (159, 130))

    def test_any_preserves_current_tab_even_when_mix_is_enabled(self):
        for attach in ({}, {FLAG: False}, {FLAG: True}):
            for filter_value in ("def", "all"):
                self.assertIsNone(support._support_class_tab(attach, "any", filter_value))


class SupportClassTabActionTests(unittest.TestCase):
    def setUp(self):
        self.context = Context(enabled=False)
        self.addCleanup(support.sessions.stop_owner, "")
        self.action = support.SupportAction()
        self.servant_map = {"1000200": {"id": 1000200, "class": "alterego", "images": ["test.png"]},
                            "test": {"images": ["test.png"]}}
        for name, value in {
            "_get_servant_map": self.servant_map,
            "_get_detector": NS(detect=Mock(return_value=[(0, 0, 100, 100, .99)])),
            "_init_scale": None, "_tap": None, "_swipe": None,
            "_shot_base": object(), "_ref_shift": (0, 0), "_match_servant": True,
            "_match_ce": True, "_check_active_view": True, "_match_passive": True,
            "_match_bond": True, "_is_connecting": False, "_record_selected_support": None,
        }.items():
            setattr(self.action, name, Mock(return_value=value))
        for name in ("time.sleep", "mfaalog.info", "mfaalog.error"):
            mock = patch("support_action." + name)
            mock.start()
            self.addCleanup(mock.stop)

    def run_search(self, *, chaldea=False):
        return self.action.run(self.context, NS(
            node_name="Chaldea助战action" if chaldea else "助战action",
            custom_action_param='{"source_mode":"chaldea"}' if chaldea else "{}"))

    def share(self, *, any_servant=False):
        friend = {"supportType": "friend", "grandSvt": True,
                  "skillLvs": [10] * 3, "appendLvs": [0, 10, 0, 0, 10],
                  "lv": 100, "tdLv": 5,
                  "equip1": {"id": 9400480}, "equip3": {"id": 9400340},
                  "classBoardData": {"grandBondEquipSkillChange": True}}
        if not any_servant:
            friend["svtId"] = 1000200
        return {"team": {"onFieldSvts": [friend], "backupSvts": []}, "actions": []}

    def test_custom_explicit_flag_selects_mix_but_keeps_identity_checks(self):
        self.context.attach.update({FLAG: True, "class_all": "all"})
        self.assertTrue(self.run_search().success)
        controller = self.context.tasker.controller
        self.assertEqual(self.action._tap.call_args_list,
                         [call(controller, 698, 130)] * 3 + [call(controller, 50, 50)])
        self.action._match_servant.assert_called_once()
        self.action._check_active_view.assert_called_once()

    def test_chaldea_attach_selects_mix_and_preserves_all_friend_requirements(self):
        self.context.attach.update({FLAG: True, "chaldea_enabled": True, "chaldea_import_source": "fixture"})
        with patch.object(support, "fetch_share_data", return_value=(self.share(), None, None)), \
                patch.object(support.os.path, "isfile", return_value=True):
            self.assertTrue(self.run_search(chaldea=True).success)
        self.assertEqual(self.action._tap.call_args_list[:3],
                         [call(self.context.tasker.controller, 698, 130)] * 3)
        self.assertEqual(self.action._match_servant.call_count, 2)
        self.assertEqual(self.action._check_active_view.call_args.args[-4], [10] * 3)
        self.assertEqual(self.action._check_active_view.call_args.args[-2:], (5, 100))
        self.assertEqual(self.action._match_passive.call_args.args[-1], [0, 10, 0, 0, 10])
        self.assertEqual(self.action._match_bond.call_args.args[-1], "50np")
        self.assertEqual(self.action._match_ce.call_count, 2)
        self.assertEqual([item.args[-2] for item in self.action._match_ce.call_args_list], support.CE_ANCHOR_GRAND)

    def test_chaldea_without_flag_keeps_extra_class_tab(self):
        self.context.attach.update(chaldea_enabled=True, chaldea_import_source="fixture")
        with patch.object(support, "fetch_share_data", return_value=(self.share(), None, None)), \
                patch.object(support.os.path, "isfile", return_value=True):
            self.assertTrue(self.run_search(chaldea=True).success)
        self.assertEqual(self.action._tap.call_args_list[:3],
                         [call(self.context.tasker.controller, 629, 130)] * 3)

    def test_chaldea_any_friend_keeps_current_tab_with_flag(self):
        self.context.attach.update({FLAG: True, "chaldea_enabled": True, "chaldea_import_source": "fixture"})
        with patch.object(support, "fetch_share_data", return_value=(self.share(any_servant=True), None, None)), \
                patch.object(support.os.path, "isfile", return_value=True):
            self.assertTrue(self.run_search(chaldea=True).success)
        self.assertEqual(self.action._tap.call_args_list, [call(self.context.tasker.controller, 50, 50)])
        self.action._match_servant.assert_not_called()
        self.action._check_active_view.assert_not_called()
        self.action._match_passive.assert_not_called()
        self.assertEqual(self.action._match_ce.call_count, 2)
        self.assertEqual(self.action._match_bond.call_args.args[-1], "50np")

    def test_custom_any_keeps_current_tab_with_flag(self):
        self.context.attach.update({FLAG: True, "class_name": "any", "class_all": "all"})
        self.assertTrue(self.run_search().success)
        self.assertEqual(self.action._tap.call_args_list, [call(self.context.tasker.controller, 50, 50)])
        self.action._match_servant.assert_not_called()

    def test_mix_does_not_accept_wrong_servant_before_matching_entry(self):
        self.context.attach[FLAG] = True
        self.action._get_detector.return_value.detect.return_value.append((0, 200, 100, 300, .99))
        self.action._match_servant.side_effect = [False, True]
        self.assertTrue(self.run_search().success)
        self.assertEqual(self.action._tap.call_args_list[-1], call(self.context.tasker.controller, 50, 250))
        self.action._check_active_view.assert_called_once()

    def test_search_refreshes_in_mix_without_returning_to_class_or_all(self):
        self.context.attach.update({FLAG: True, "max_swipes_before_refresh": 1})
        self.action._match_servant.side_effect = lambda *args: self.context.refreshes == 1
        self.assertTrue(self.run_search().success)
        self.assertEqual(self.context.refreshes, 1)
        self.assertEqual(self.action._tap.call_args_list,
                         [call(self.context.tasker.controller, 698, 130)] * 3
                         + [call(self.context.tasker.controller, 50, 50)])


class SupportMixResourceContractTests(unittest.TestCase):
    def test_only_trial_chaldea_node_enables_cross_slot_search(self):
        found = []

        def visit(value, location):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key == FLAG:
                        found.append((location + (key,), child))
                    visit(child, location + (key,))
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    visit(child, location + (index,))

        for path in (ROOT / "assets").rglob("*.json"):
            if "web" in path.relative_to(ROOT / "assets").parts:
                continue
            visit(json.loads(path.read_text("utf-8-sig")), (path.relative_to(ROOT).as_posix(),))
        self.assertEqual(found, [(("assets/tasks/地狱的试炼.json", "task", 0, "pipeline_override",
                                  "Chaldea助战action", "attach", FLAG), True)])


if __name__ == "__main__":
    unittest.main()
