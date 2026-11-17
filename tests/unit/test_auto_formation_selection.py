"""从者重复候选及选中后返回状态的离线流程回归，不控制真实设备。"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from formation_test_support import formation as f


UNIQUE = (0.99, (551, 285), "f_5007003.png", 0.30)
DUPLICATE = (0.994, (551, 285), "f_5007003.png", 0.008)
LOW = (0.60, (551, 285), "f_5007003.png", 0.02)
SERVANT = {"id": 500700, "name": "威廉·莎士比亚"}


class ServantSelectionTests(unittest.TestCase):
    def make_action(self):
        action = f.AutoFormationFromChaldea.__new__(f.AutoFormationFromChaldea)
        action.context = SimpleNamespace(tasker=SimpleNamespace(stopping=False))
        action.controller = SimpleNamespace(post_click=Mock(
            return_value=SimpleNamespace(wait=lambda: None)))
        action.face_dirs = []
        action.sx = action.sy = 1.0
        action._load_servant_templates = Mock(return_value=[("face", object())])
        action._shot = Mock(return_value=object())
        action._run_pipeline = Mock(return_value=True)
        action._wait_for_formation_page = Mock(return_value="edit")
        action._wait_for = Mock(return_value=True)
        action._fail = Mock(return_value=False)
        return action

    def search(self, action, matches):
        with patch.object(f, "_match_servant_list_cards", side_effect=matches), \
                patch.object(f.time, "sleep"):
            return action._find_and_select_servant(SERVANT)

    def test_stable_duplicate_candidates_stop_before_click_or_scroll(self):
        action = self.make_action()
        self.assertFalse(self.search(action, [DUPLICATE, DUPLICATE]))
        action.controller.post_click.assert_not_called()
        self.assertEqual([call.args[0] for call in action._run_pipeline.call_args_list],
                         ["自动编队-从者列表复位顶部"])
        message = action._fail.call_args.args[0]
        for text in ("servant_duplicate_candidates", "威廉·莎士比亚(500700)",
                     "0.994", "0.986", "同名", "保管室", "重试"):
            self.assertIn(text, message)
        self.assertEqual(action._last_servant_selection_error, message)

    def test_one_ambiguous_frame_does_not_report_duplicates(self):
        action = self.make_action()
        with patch.object(f, "_match_servant_list_cards",
                          side_effect=[DUPLICATE, LOW, LOW, LOW]), \
                patch.object(f.time, "sleep"):
            match = action._find_servant_in_still_list([], SERVANT["name"], 0)
        self.assertEqual(match, LOW)
        self.assertFalse(action._servant_list_has_duplicate_candidates(match))
        action._fail.assert_not_called()

    def test_moving_duplicate_candidates_are_not_confirmed(self):
        action = self.make_action()
        moved = (DUPLICATE[0], (551, 330), DUPLICATE[2], DUPLICATE[3])
        with patch.object(f, "_match_servant_list_cards",
                          side_effect=[DUPLICATE, moved] * f.SERVANT_LIST_STABILITY_MAX_CHECKS), \
                patch.object(f.time, "sleep"):
            self.assertIsNone(action._find_servant_in_still_list([], SERVANT["name"], 0))
        action._fail.assert_not_called()

    def test_unique_and_duplicate_frames_do_not_count_as_same_stable_state(self):
        action = self.make_action()
        # 即使分差刚好达到可选边界，第二候选已过阈值也必须单独确认重卡状态。
        boundary_duplicate = (1.0, UNIQUE[1], UNIQUE[2], 0.12)
        with patch.object(f, "_match_servant_list_cards",
                          side_effect=[UNIQUE, boundary_duplicate, UNIQUE, UNIQUE]), \
                patch.object(f.time, "sleep"):
            self.assertEqual(action._find_servant_in_still_list([], SERVANT["name"], 0), UNIQUE)
        action._fail.assert_not_called()

    def test_small_margin_alone_does_not_imply_duplicates(self):
        for match in (None, LOW, (0.89, UNIQUE[1], UNIQUE[2], 0.11), UNIQUE):
            with self.subTest(match=match):
                self.assertFalse(f.AutoFormationFromChaldea._servant_list_has_duplicate_candidates(match))

    def test_normal_edit_return_clicks_target_only_once(self):
        action = self.make_action()
        self.assertTrue(self.search(action, [UNIQUE, UNIQUE]))
        action.controller.post_click.assert_called_once_with(551, 285)
        self.assertEqual(action._run_pipeline.call_count, 1)
        action._fail.assert_not_called()

    def test_confirm_return_reopens_config_once_without_swiping(self):
        action = self.make_action()
        action._wait_for_formation_page.return_value = "confirm"
        self.assertTrue(self.search(action, [UNIQUE, UNIQUE]))
        action.controller.post_click.assert_called_once_with(551, 285)
        self.assertEqual([call.args[0] for call in action._run_pipeline.call_args_list],
                         ["自动编队-从者列表复位顶部", "自动编队-打开配置"])
        action._wait_for.assert_called_once_with(action._in_formation_edit,
                                                f.SELECT_PAGE_ENTER_TIMEOUT_SECONDS)
        action._fail.assert_not_called()

    def test_reopen_failure_stops_and_preserves_return_error(self):
        for pipeline_ok, page_ok in ((False, True), (True, False)):
            with self.subTest(pipeline_ok=pipeline_ok, page_ok=page_ok):
                action = self.make_action()
                action._wait_for_formation_page.return_value = "confirm"
                action._run_pipeline.side_effect = [True, pipeline_ok]
                action._wait_for.return_value = page_ok
                self.assertFalse(self.search(action, [UNIQUE, UNIQUE]))
                self.assertEqual(action.controller.post_click.call_count, 1)
                self.assertEqual(action._run_pipeline.call_count, 2)
                self.assertIn("servant_select_return_failed", action._last_servant_selection_error)

    def test_unknown_return_never_resumes_list_swipes(self):
        action = self.make_action()
        action._wait_for_formation_page.return_value = None
        self.assertFalse(self.search(action, [UNIQUE, UNIQUE]))
        self.assertEqual(action.controller.post_click.call_count, 1)
        self.assertEqual(action._run_pipeline.call_count, 1)
        self.assertIn("servant_select_return_failed", action._last_servant_selection_error)

    def test_stop_during_return_never_opens_config(self):
        action = self.make_action()

        def stopped():
            action.context.tasker.stopping = True
            return "confirm"

        action._wait_for_formation_page.side_effect = stopped
        self.assertFalse(self.search(action, [UNIQUE, UNIQUE]))
        self.assertEqual(action._run_pipeline.call_count, 1)
        action._fail.assert_not_called()

    def test_stop_during_stability_check_does_not_click_target(self):
        action = self.make_action()

        def stopped(*args):
            action.context.tasker.stopping = True
            return UNIQUE

        action._find_servant_in_still_list = stopped
        self.assertFalse(action._find_and_select_servant(SERVANT))
        action.controller.post_click.assert_not_called()
        action._fail.assert_not_called()

    def prepare_replace(self, action):
        action.expected = [{"kind": "LOCAL", "svt_id": 500700}]
        action._detect_slots_stable = Mock(return_value=[{"kind": "OTHER", "svt_id": None}])
        action._get_servant_info = Mock(return_value=SERVANT)
        action._enter_servant_select = Mock(return_value=True)
        action._filter_servant_list = Mock(return_value=True)

    def test_replace_caller_does_not_overwrite_duplicate_error_as_not_found(self):
        action = self.make_action()
        self.prepare_replace(action)
        with patch.object(f, "_match_servant_list_cards", side_effect=[DUPLICATE, DUPLICATE]), \
                patch.object(f.time, "sleep"):
            self.assertFalse(action._replace_local_servants())
        action._fail.assert_called_once()
        self.assertIn("servant_duplicate_candidates", action._fail.call_args.args[0])

    def test_recovered_page_still_requires_target_slot_identity_verification(self):
        action = self.make_action()
        self.prepare_replace(action)
        action._wait_for_formation_page.return_value = "confirm"
        action._wait_for_servant_replace_verify = Mock(return_value=(False, [
            {"kind": "LOCAL", "svt_id": 100100, "score": 0.99}]))
        with patch.object(f, "_match_servant_list_cards",
                          side_effect=[UNIQUE, UNIQUE] * f.MAX_SERVANT_SELECT_ATTEMPTS), \
                patch.object(f.time, "sleep"):
            self.assertFalse(action._replace_local_servants())
        self.assertEqual(action._wait_for_servant_replace_verify.call_count,
                         f.MAX_SERVANT_SELECT_ATTEMPTS)
        self.assertIn("servant_replace_verify_failed", action._fail.call_args.args[0])


class FormationReturnStateTests(unittest.TestCase):
    def test_each_open_config_call_gets_fresh_retry_budget(self):
        action = f.AutoFormationFromChaldea.__new__(f.AutoFormationFromChaldea)
        action.context = SimpleNamespace(
            clear_hit_count=Mock(), tasker=SimpleNamespace(stopping=False),
            run_task=Mock(return_value=SimpleNamespace(
                status=SimpleNamespace(succeeded=True, failed=False))),
        )
        for _ in range(2):
            self.assertTrue(action._run_pipeline("自动编队-打开配置"))
        self.assertEqual(action.context.clear_hit_count.call_count, 2)
        action.context.clear_hit_count.assert_called_with("自动编队-打开配置")

    def test_unrelated_pipeline_does_not_reset_config_budget(self):
        action = f.AutoFormationFromChaldea.__new__(f.AutoFormationFromChaldea)
        action.context = SimpleNamespace(
            clear_hit_count=Mock(), tasker=SimpleNamespace(stopping=False),
            run_task=Mock(return_value=SimpleNamespace(
                status=SimpleNamespace(succeeded=True, failed=False))),
        )
        self.assertTrue(action._run_pipeline("自动编队-从者列表复位顶部"))
        action.context.clear_hit_count.assert_not_called()

    def test_confirm_marker_wins_over_similar_edit_button(self):
        action = f.AutoFormationFromChaldea.__new__(f.AutoFormationFromChaldea)
        action._shot = lambda: object()
        action.config_marker = object()
        action.edit_marker = object()
        action._match_template = Mock(return_value=(0.99, (222, 678)))
        self.assertEqual(action._formation_page_state(), "confirm")
        action._match_template.assert_called_once()

    def test_low_edit_match_is_not_accepted_as_edit(self):
        action = f.AutoFormationFromChaldea.__new__(f.AutoFormationFromChaldea)
        action._shot = lambda: object()
        action.config_marker = action.edit_marker = None
        action._match_template = Mock(side_effect=[(0.5, (0, 0)), (0.662, (0, 0))])
        self.assertIsNone(action._formation_page_state())

    def test_return_wait_requires_three_frames_of_same_page_state(self):
        action = f.AutoFormationFromChaldea.__new__(f.AutoFormationFromChaldea)
        action._formation_page_state = Mock(side_effect=["confirm", "edit", None,
                                                       "confirm", "confirm", "confirm"])
        observed = []

        def wait(predicate, timeout):
            consecutive = 0
            for _ in range(6):
                hit = predicate()
                observed.append(hit)
                consecutive = consecutive + 1 if hit else 0
                if consecutive >= 2:
                    return True
            return False

        action._wait_for = wait
        self.assertEqual(action._wait_for_formation_page(), "confirm")
        self.assertEqual(observed, [False, False, False, False, True, True])

    def test_manual_checker_inherits_same_return_detection(self):
        self.assertIs(f.ValidateFormationFromChaldea._formation_page_state,
                      f.AutoFormationFromChaldea._formation_page_state)
        self.assertIs(f.ValidateFormationFromChaldea._wait_for_formation_page,
                      f.AutoFormationFromChaldea._wait_for_formation_page)


if __name__ == "__main__":
    unittest.main()
