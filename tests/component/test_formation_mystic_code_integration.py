"""御主礼装在自动/手动编队中的约束；所有设备和视觉入口均隔离。"""
import sys
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from formation_test_support import formation as f


class FormationMysticCodeIntegrationTests(unittest.TestCase):
    def setUp(self):
        for name in ("info", "warning", "error"):
            patcher = patch.object(f.mfaalog, name)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.events = []
        self.current = [dict(kind="LOCAL", svt_id=100100)] + [
            dict(kind="EMPTY", svt_id=None) for _ in range(5)
        ]
        self.expected = [dict(item, slot=index) for index, item in enumerate(self.current)]
        self.context = NS(
            tasker=NS(controller=object(), stopping=False),
            get_node_data=Mock(return_value={"attach": {
                "chaldea_import_source": "fixture", "auto_equip": False,
            }}),
        )
        self.share = {"team": {"mysticCode": {"mysticCodeId": 210}}}
        patcher = patch.object(f, "fetch_share_data", side_effect=lambda *_: (self.share, None, None))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ensure = self.patch("ensure_mystic_code", side_effect=self.check)
        self.real_extract = f.extract_mystic_code_id
        self.extract = self.patch("extract_mystic_code_id", return_value=210)

    def patch(self, name, **kwargs):
        patcher = patch.object(f, name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def check(self, action, mystic_id, *, allow_change):
        self.events.append(("mystic", mystic_id, allow_change))
        return True

    def action(self, *, manual=False):
        action = (f.ValidateFormationFromChaldea if manual else f.AutoFormationFromChaldea)()
        for name in (
            "_begin_verified_formation_update", "_discard_unpublished_formation",
            "_init_paths", "_init_scale", "_prepare_target_templates", "_log_layout",
            "_log_support_identity_if_possible", "_load_equip_database",
        ):
            setattr(action, name, Mock())
        action._build_expected = Mock(return_value=self.expected)
        action._detect_slots_stable = Mock(return_value=self.current)
        action._configure_support_target = Mock(return_value=True)
        action._should_clear_formation = Mock(return_value=False)
        action._confirmed_now = Mock(return_value=True)
        action._relocate_unexpected_support = Mock(return_value=True)
        action._reorder_existing = Mock(return_value=True)
        action._replace_local_servants = Mock(return_value=True)
        action._replace_equips = Mock(side_effect=lambda: self.events.append("equips") or True)
        action._wait_for_formation_page = Mock(return_value="confirm")
        action._manual_identities_match = Mock(return_value=True)
        action._first_mismatch = Mock(return_value=None)
        action._run_pipeline = Mock(side_effect=lambda name: self.events.append(name) or True)
        action._confirm_formation_change_if_present = Mock(
            side_effect=lambda: self.events.append("confirm_popup"))
        action._publish_verified_layout = Mock(side_effect=lambda _: self.events.append("publish"))
        return action

    def run_action(self, action):
        return action.run(self.context, NS(node_name="test")).success

    def test_auto_checks_after_save_even_when_conceptual_equip_automation_is_disabled(self):
        action = self.action()
        self.assertTrue(self.run_action(action))
        action._replace_equips.assert_not_called()
        self.assertEqual(self.events, [
            "自动编队-编队决定", "confirm_popup", ("mystic", 210, True), "publish",
        ])
        self.ensure.assert_called_once_with(action, 210, allow_change=True)

    def test_auto_checks_after_conceptual_equips_and_save(self):
        self.context.get_node_data.return_value["attach"]["auto_equip"] = True
        action = self.action()
        self.assertTrue(self.run_action(action))
        self.assertEqual(self.events, [
            "equips", "自动编队-编队决定", "confirm_popup", ("mystic", 210, True), "publish",
        ])

    def test_auto_mystic_failure_stops_and_does_not_publish_verified_formation(self):
        self.ensure.return_value = False
        self.ensure.side_effect = None
        action = self.action()
        self.assertFalse(self.run_action(action))
        action._publish_verified_layout.assert_not_called()
        action._discard_unpublished_formation.assert_called_once()

    def test_failed_save_does_not_try_changing_mystic_code(self):
        action = self.action()
        action._run_pipeline.return_value = False
        action._run_pipeline.side_effect = None
        self.assertFalse(self.run_action(action))
        self.ensure.assert_not_called()

    def test_missing_mystic_data_skips_all_mystic_detection_in_both_modes(self):
        self.extract.side_effect = self.real_extract
        teams = [
            {}, {"mysticCode": None}, {"mysticCode": {}},
            {"mysticCode": {"level": 10}}, {"mysticCode": {"mysticCodeId": 0}},
        ]
        for team in teams:
            self.share = {"team": team}
            for manual in (False, True):
                with self.subTest(team=team, manual=manual):
                    action = self.action(manual=manual)
                    self.assertTrue(self.run_action(action))
                    self.assertIsNone(action.expected_mystic_code_id)
        self.ensure.assert_not_called()

    def test_reused_action_does_not_keep_previous_mystic_requirement(self):
        self.extract.side_effect = [210, None]
        for manual in (False, True):
            with self.subTest(manual=manual):
                self.extract.side_effect = [210, None]
                self.ensure.reset_mock()
                action = self.action(manual=manual)
                self.assertTrue(self.run_action(action))
                self.assertTrue(self.run_action(action))
                self.ensure.assert_called_once_with(action, 210, allow_change=not manual)
                self.assertIsNone(action.expected_mystic_code_id)

    def test_manual_ready_formation_checks_without_changing_or_saving(self):
        action = self.action(manual=True)
        self.assertTrue(self.run_action(action))
        self.ensure.assert_called_once_with(action, 210, allow_change=False)
        action._run_pipeline.assert_not_called()
        self.assertEqual(self.events, [("mystic", 210, False), "publish"])

    def test_manual_mismatch_stops_before_reordering_or_saving(self):
        self.ensure.return_value = False
        self.ensure.side_effect = None
        for page_state in ("confirm", "edit"):
            with self.subTest(page_state=page_state):
                action = self.action(manual=True)
                action._wait_for_formation_page.return_value = page_state
                action._first_mismatch.return_value = 0
                self.assertFalse(self.run_action(action))
                action._run_pipeline.assert_not_called()
                action._reorder_existing.assert_not_called()
                action._publish_verified_layout.assert_not_called()

    def test_manual_mismatch_still_stops_if_legacy_continue_setting_is_present(self):
        self.context.get_node_data.return_value["attach"]["team_config_error"] = True
        self.ensure.return_value = False
        self.ensure.side_effect = None
        action = self.action(manual=True)
        self.assertFalse(self.run_action(action))
        action._publish_verified_layout.assert_not_called()

    def test_manual_check_precedes_existing_reorder_flow(self):
        action = self.action(manual=True)
        action._wait_for_formation_page.return_value = "edit"
        action._first_mismatch.side_effect = [0, None]
        self.assertTrue(self.run_action(action))
        self.assertEqual(self.events, [
            ("mystic", 210, False), "自动编队-编队决定", "confirm_popup", "publish",
        ])
        action._reorder_existing.assert_called_once()

    def test_invalid_mystic_data_stops_before_any_formation_changes(self):
        self.extract.side_effect = ValueError("invalid_mystic_code: 无效御主礼装")
        for manual in (False, True):
            with self.subTest(manual=manual):
                action = self.action(manual=manual)
                self.assertFalse(self.run_action(action))
                action._run_pipeline.assert_not_called()
                action._reorder_existing.assert_not_called()
                action._publish_verified_layout.assert_not_called()


if __name__ == "__main__":
    unittest.main()
