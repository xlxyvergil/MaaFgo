"""御主礼装业务规则回归；隔离视觉和设备，不操作真实游戏。"""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent/custom"))
import mystic_code as m


class MysticCodeDataTests(unittest.TestCase):
    def test_missing_null_and_zero_mean_unspecified(self):
        for data in (
            {}, {"team": {}}, {"team": {"mysticCode": None}},
            {"team": {"mysticCode": {}}},
            {"team": {"mysticCode": {"level": 10}}},
            {"team": {"mysticCode": {"mysticCodeId": None}}},
            {"team": {"mysticCode": {"mysticCodeId": 0}}},
            {"mysticCode": None}, {"mysticCode": {}},
            {"mysticCode": {"id": None}}, {"mysticCode": {"id": 0}},
        ):
            with self.subTest(data=data):
                self.assertIsNone(m.extract_mystic_code_id(data))

    def test_current_and_legacy_ids_are_game_ids(self):
        for code_id in (1, 20, 100, 210, 440, 470):
            with self.subTest(code_id=code_id):
                self.assertEqual(m.extract_mystic_code_id({
                    "team": {"mysticCode": {"mysticCodeId": code_id}},
                }), code_id)
                self.assertEqual(m.extract_mystic_code_id({
                    "mysticCode": {"id": code_id},
                }), code_id)

    def test_explicit_current_field_has_priority_over_legacy(self):
        for current_id, expected in ((210, 210), (0, None)):
            with self.subTest(current_id=current_id):
                self.assertEqual(m.extract_mystic_code_id({
                    "team": {"mysticCode": {"mysticCodeId": current_id}},
                    "mysticCode": {"id": 20},
                }), expected)
        self.assertEqual(m.extract_mystic_code_id({
            "team": {"mysticCode": {"mysticCodeId": 210}},
            "mysticCode": "unused legacy field",
        }), 210)

    def test_legacy_fallback_when_current_id_is_missing_or_null(self):
        for current in (None, {}, {"mysticCodeId": None}):
            with self.subTest(current=current):
                self.assertEqual(m.extract_mystic_code_id({
                    "team": {"mysticCode": current}, "mysticCode": {"id": 20},
                }), 20)

    def test_malformed_containers_and_ids_are_rejected(self):
        for data in (None, [], 1, "team"):
            with self.subTest(data=data):
                with self.assertRaisesRegex(ValueError, "编队数据"):
                    m.extract_mystic_code_id(data)
        for container in ([], "210", 210, False):
            for key in ("current", "legacy"):
                data = ({"team": {"mysticCode": container}} if key == "current"
                        else {"mysticCode": container})
                with self.subTest(data=data):
                    with self.assertRaisesRegex(ValueError, "御主礼装数据"):
                        m.extract_mystic_code_id(data)
        for value in (-1, "210", True, False, 0.0, 210.0, [], {}):
            for data in ({"team": {"mysticCode": {"mysticCodeId": value}}},
                         {"mysticCode": {"id": value}}):
                with self.subTest(data=data):
                    with self.assertRaisesRegex(ValueError, "无效御主礼装"):
                        m.extract_mystic_code_id(data)

    def test_invalid_current_id_cannot_fall_back_to_another_legacy_code(self):
        with self.assertRaisesRegex(ValueError, "无效御主礼装"):
            m.extract_mystic_code_id({
                "team": {"mysticCode": {"mysticCodeId": "210"}},
                "mysticCode": {"id": 20},
            })


class MysticCodeSelectionTests(unittest.TestCase):
    def setUp(self):
        self.item = {"id": 210, "name": "决战用迦勒底制服", "maleImageId": 211,
                     "femaleImageId": 212}
        self.match = m.IconMatch(0.99, (880, 650), "fixture.png")
        self.formation = NS(
            context=NS(tasker=NS(stopping=False), override_pipeline=Mock(),
                       run_task=Mock(), clear_hit_count=Mock()),
            controller=NS(post_click=Mock(return_value=NS(wait=Mock()))),
            _run_pipeline=Mock(return_value=True), _fail=Mock(return_value=False),
        )
        self.catalog = self.patcher("_catalog", return_value={210: self.item})
        self.icons = self.patcher("_icons", return_value=[("fixture.png", object())])
        self.stable = self.patcher("_stable_match")
        self.patcher("time.sleep")
        for name in ("info", "warning", "error"):
            self.patcher("mfaalog." + name)

    def patcher(self, name, **kwargs):
        patcher = patch("mystic_code." + name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def ensure(self, *, code_id=210, auto=True):
        return m.ensure_mystic_code(self.formation, code_id, allow_change=auto)

    def pipeline_names(self):
        return [call.args[0] for call in self.formation._run_pipeline.call_args_list]

    def assert_failure_prompt(self, expected_text):
        self.formation._fail.assert_called_once()
        message = self.formation._fail.call_args.args[0]
        self.assertIn(expected_text, message)
        self.formation.context.override_pipeline.assert_called_once()
        self.formation.context.run_task.assert_called_once_with("御主礼装-用户提示")

    def test_unspecified_code_does_not_load_resources_or_operate(self):
        self.assertTrue(self.ensure(code_id=None))
        self.catalog.assert_not_called()
        self.icons.assert_not_called()
        self.stable.assert_not_called()
        self.formation._run_pipeline.assert_not_called()
        self.formation.controller.post_click.assert_not_called()

    def test_already_matching_code_does_not_open_equipment(self):
        for auto in (False, True):
            with self.subTest(auto=auto):
                self.stable.return_value = self.match
                self.assertTrue(self.ensure(auto=auto))
        self.formation._run_pipeline.assert_not_called()
        self.formation.controller.post_click.assert_not_called()
        self.formation._fail.assert_not_called()

    def test_manual_mismatch_prompts_without_clicking_or_changing(self):
        self.stable.return_value = None
        self.assertFalse(self.ensure(auto=False))
        self.formation._run_pipeline.assert_not_called()
        self.formation.controller.post_click.assert_not_called()
        self.assertEqual([call.args[2] for call in self.stable.call_args_list], ["team"])
        self.assert_failure_prompt("请手动更换")
        self.assertIn("决战用迦勒底制服", self.formation._fail.call_args.args[0])
        self.assertIn("210", self.formation._fail.call_args.args[0])

    def test_selected_code_already_correct_returns_and_rechecks_team(self):
        self.stable.side_effect = [None, self.match, self.match]
        self.assertTrue(self.ensure())
        self.assertEqual(self.pipeline_names(), [
            "御主礼装-打开", "御主礼装-确认选择界面", "御主礼装-返回编队",
        ])
        self.formation.controller.post_click.assert_not_called()
        self.assertEqual([call.args[2] for call in self.stable.call_args_list],
                         ["team", "selected", "team"])

    def test_automatic_selection_verifies_both_large_and_team_icons(self):
        self.stable.side_effect = [None, None, self.match, self.match, self.match]
        self.assertTrue(self.ensure())
        self.formation.controller.post_click.assert_called_once_with(880, 650)
        self.assertEqual([call.args[2] for call in self.stable.call_args_list],
                         ["team", "selected", "list", "selected", "team"])
        self.assertEqual(self.pipeline_names()[-1], "御主礼装-返回编队")
        self.formation._fail.assert_not_called()

    def test_wrong_large_icon_after_selection_stops_on_equipment_screen(self):
        self.stable.side_effect = [None, None, self.match, None]
        self.assertFalse(self.ensure())
        self.formation.controller.post_click.assert_called_once()
        self.assertNotIn("御主礼装-返回编队", self.pipeline_names())
        self.assertNotIn("御主礼装-列表左滑查找", self.pipeline_names())
        self.assert_failure_prompt("选择后复核失败")

    def test_wrong_team_icon_after_return_fails(self):
        self.stable.side_effect = [None, self.match, None]
        self.assertFalse(self.ensure())
        self.assertEqual(self.pipeline_names()[-1], "御主礼装-返回编队")
        self.assert_failure_prompt("最终复核失败")

    def test_unowned_code_search_has_page_limit(self):
        self.stable.return_value = None
        with patch.object(m, "MAX_LIST_PAGES", 4):
            self.assertFalse(self.ensure())
        self.assertEqual([call.args[2] for call in self.stable.call_args_list].count("list"), 4)
        self.assertEqual(self.pipeline_names().count("御主礼装-列表左滑查找"), 3)
        self.assertNotIn("御主礼装-返回编队", self.pipeline_names())
        self.formation.controller.post_click.assert_not_called()
        self.assert_failure_prompt("确认账号已持有")

    def test_opening_failure_does_not_attempt_selection(self):
        self.stable.return_value = None
        self.formation._run_pipeline.return_value = False
        self.assertFalse(self.ensure())
        self.assertEqual(self.pipeline_names(), ["御主礼装-打开"])
        self.formation.controller.post_click.assert_not_called()
        self.assert_failure_prompt("无法打开")

    def test_unknown_code_and_missing_icon_resources_stop_before_navigation(self):
        self.assertFalse(self.ensure(code_id=9999))
        self.icons.assert_not_called()
        self.stable.assert_not_called()
        self.formation._run_pipeline.assert_not_called()
        self.assert_failure_prompt("资源缺失")

    def test_corrupt_or_absent_icon_reports_resource_failure(self):
        self.icons.side_effect = ValueError("resource_missing: fixture")
        self.assertFalse(self.ensure())
        self.stable.assert_not_called()
        self.formation._run_pipeline.assert_not_called()
        self.formation.controller.post_click.assert_not_called()
        self.assert_failure_prompt("resource_missing")

    def test_stopped_task_does_not_open_equipment_or_emit_ui_prompt(self):
        self.formation.context.tasker.stopping = True
        self.stable.return_value = None
        self.assertFalse(self.ensure())
        self.formation._run_pipeline.assert_not_called()
        self.formation.controller.post_click.assert_not_called()
        self.formation.context.run_task.assert_not_called()

    def test_stop_while_searching_prevents_next_swipe(self):
        def match_scene(_formation, _icons, scene, _name):
            if scene == "list":
                self.formation.context.tasker.stopping = True
            return None
        self.stable.side_effect = match_scene
        self.assertFalse(self.ensure())
        self.assertNotIn("御主礼装-列表左滑查找", self.pipeline_names())
        self.formation.controller.post_click.assert_not_called()
        self.formation.context.run_task.assert_not_called()

    def test_stop_after_candidate_is_found_prevents_click(self):
        def match_scene(_formation, _icons, scene, _name):
            if scene == "list":
                self.formation.context.tasker.stopping = True
                return self.match
            return None
        self.stable.side_effect = match_scene
        self.assertFalse(self.ensure())
        self.formation.controller.post_click.assert_not_called()
        self.assertNotIn("御主礼装-返回编队", self.pipeline_names())

    def test_stop_after_large_icon_matches_prevents_return_navigation(self):
        def match_scene(_formation, _icons, scene, _name):
            if scene == "selected":
                self.formation.context.tasker.stopping = True
                return self.match
            return None
        self.stable.side_effect = match_scene
        self.assertFalse(self.ensure())
        self.formation.controller.post_click.assert_not_called()
        self.assertNotIn("御主礼装-返回编队", self.pipeline_names())

    def test_prompt_escapes_html_and_logging_survives_prompt_failure(self):
        self.stable.return_value = None
        self.item["name"] = '<name & "value">'
        self.formation.context.run_task.side_effect = RuntimeError("prompt unavailable")
        self.assertFalse(self.ensure(auto=False))
        override = self.formation.context.override_pipeline.call_args.args[0]
        hint = override["御主礼装-用户提示"]["focus"]["Node.Recognition.Starting"]
        self.assertIn("&lt;name &amp; &quot;value&quot;&gt;", hint)
        self.formation._fail.assert_called_once()


if __name__ == "__main__":
    unittest.main()
