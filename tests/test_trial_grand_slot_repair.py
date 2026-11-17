"""Trial-only empty Grand slot repair reuses identity and member-reorder guards."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "agent"), str(ROOT / "agent/custom")]
from formation_test_support import formation as f


def member(sid, slot, *, grand=False, support=False):
    return {"kind": "SUPPORT" if support else "LOCAL", "svt_id": sid,
            "slot": slot, "grand_svt": grand}


def expected():
    return [member(304800, 0, grand=True), member(1000200, 1, support=True),
            member(4000100, 2), member(504500, 3),
            {"kind": "EMPTY", "svt_id": None, "slot": 4},
            {"kind": "EMPTY", "svt_id": None, "slot": 5}]


class TrialGrandRepairTests(unittest.TestCase):
    def action(self):
        action = f.AutoFormationFromChaldea.__new__(f.AutoFormationFromChaldea)
        action.trial_grand_slot_repair_enabled = True
        action.expected = expected()
        action.context = NS(tasker=NS(stopping=False), run_recognition=Mock())
        action._fail = Mock(return_value=False)
        action._shot = Mock(return_value=object())
        action._trial_empty_grand_state = Mock(return_value=True)
        action._get_servant_info = Mock(return_value={"id": 304800, "name": "妖兰", "class": "lancer"})
        action._run_pipeline = Mock(return_value=True)
        action._in_servant_select = Mock(return_value=True)
        action._filter_servant_list = Mock(return_value=True)
        action._find_and_select_servant = Mock(return_value=True)
        action._last_servant_selection_error = None
        action._reorder_existing = Mock(return_value=True)
        action._detect_slots_stable = Mock(return_value=expected())
        action._wait_for = Mock(side_effect=lambda predicate, timeout: bool(predicate()) and bool(predicate()))
        return action

    def test_other_tasks_and_sources_without_own_grand_do_not_observe_or_click(self):
        for enabled, roster in ((False, expected()), (True, [member(1000200, 1, grand=True, support=True)])):
            action = self.action()
            action.trial_grand_slot_repair_enabled, action.expected = enabled, roster
            self.assertTrue(action._repair_trial_grand_slot())
            action._trial_empty_grand_state.assert_not_called()
            action._run_pipeline.assert_not_called()

    def test_no_empty_frame_does_not_guess_or_change_existing_grand_mode(self):
        action = self.action()
        action._trial_empty_grand_state.return_value = False
        self.assertTrue(action._repair_trial_grand_slot())
        action._run_pipeline.assert_not_called()
        action._reorder_existing.assert_not_called()

    def test_uncertain_or_multiple_expected_own_grands_stop_before_input(self):
        for uncertain in (True, False):
            action = self.action()
            if uncertain:
                action._trial_empty_grand_state.return_value = None
            else:
                action.expected[2]["grand_svt"] = True
            self.assertFalse(action._repair_trial_grand_slot())
            action._run_pipeline.assert_not_called()
            self.assertIn("冠位", action._fail.call_args.args[0])

    def test_selecting_through_grand_slot_and_normal_reordering_are_both_required(self):
        action = self.action()
        in_grand = expected()
        in_grand[4], in_grand[0] = in_grand[0], {"kind": "EMPTY", "svt_id": None}
        action._detect_slots_stable.side_effect = [in_grand, expected(), expected()]
        action._trial_empty_grand_state.side_effect = [True, False, False]
        self.assertTrue(action._repair_trial_grand_slot())
        action._run_pipeline.assert_called_once_with("自动编队-试炼打开空冠位格")
        servant = action._get_servant_info.return_value
        action._filter_servant_list.assert_called_once_with(servant)
        action._find_and_select_servant.assert_called_once_with(servant)
        action._reorder_existing.assert_called_once_with()
        self.assertEqual(action._trial_empty_grand_state.call_count, 3)

    def test_selected_wrong_identity_or_duplicate_cannot_advance_to_reorder(self):
        for duplicates in (False, True):
            action = self.action()
            layout = expected()
            if duplicates:
                layout[4] = member(304800, 4)
            else:
                layout[0] = member(305300, 0)
            action._detect_slots_stable.return_value = layout
            self.assertFalse(action._repair_trial_grand_slot())
            action._reorder_existing.assert_not_called()

    def test_unconfirmed_list_page_cannot_select_or_reorder(self):
        action = self.action()
        action._in_servant_select.return_value = False
        self.assertFalse(action._repair_trial_grand_slot())
        action._find_and_select_servant.assert_not_called()
        action._reorder_existing.assert_not_called()

    def test_duplicate_warehouse_error_is_retained_and_never_replaced_by_random_card(self):
        action = self.action()
        action._find_and_select_servant.return_value = False
        action._last_servant_selection_error = "servant_duplicate_candidates"
        self.assertFalse(action._repair_trial_grand_slot())
        action._fail.assert_not_called()
        action._reorder_existing.assert_not_called()

    def test_empty_frame_must_disappear_after_selecting_and_reordering(self):
        action = self.action()
        # The frame remains True even after identity and reorder succeeded.
        self.assertFalse(action._repair_trial_grand_slot())
        self.assertIn("空冠位格消失状态", action._fail.call_args.args[0])


class TrialGrandScopeTests(unittest.TestCase):
    def test_action_requires_profile_and_explicit_boolean_enable(self):
        for attach, enabled in (({}, False), ({"trial_grand_slot_repair": True}, False),
                ({"native_battle_trial_profile": "hellish_202611"}, False),
                ({"native_battle_trial_profile": "other", "trial_grand_slot_repair": True}, False),
                ({"native_battle_trial_profile": "hellish_202611", "trial_grand_slot_repair": "true"}, False),
                ({"native_battle_trial_profile": "hellish_202611", "trial_grand_slot_repair": True}, True)):
            with self.subTest(attach=attach):
                action = f.AutoFormationFromChaldea()
                context = NS(get_node_data=lambda name: {"attach": attach}, tasker=NS(controller=object()))
                with patch.object(action, "_begin_verified_formation_update"), \
                     patch.object(action, "_discard_unpublished_formation"):
                    action.run(context, NS(node_name="执行自动编队"))  # no source, no device work
                self.assertEqual(action.trial_grand_slot_repair_enabled, enabled)

    def test_only_trial_task_enables_the_repair_and_template_is_in_base(self):
        pipeline = json.loads((ROOT / "assets/resource/base/pipeline/自动编队.json").read_text(encoding="utf-8"))
        node = pipeline["自动编队-试炼空冠位格"]["recognition"]["param"]
        self.assertEqual(node["threshold"], .90)
        self.assertTrue((ROOT / "assets/resource/base/image" / node["template"]).is_file())
        enabled_tasks = []
        for path in (ROOT / "assets/tasks").glob("*.json"):
            document = json.loads(path.read_text(encoding="utf-8"))
            for task in document.get("task", []):
                attach = task.get("pipeline_override", {}).get("执行自动编队", {}).get("attach", {})
                if attach.get("trial_grand_slot_repair") is True:
                    enabled_tasks.append(task["name"])
        self.assertEqual(enabled_tasks, ["地狱的试炼"])

    def test_empty_frame_read_distinguishes_absent_from_uncertain_and_ambiguous(self):
        action = f.AutoFormationFromChaldea.__new__(f.AutoFormationFromChaldea)
        frame = object()
        action._shot = Mock(return_value=frame)
        action.context = NS(run_recognition=Mock())
        values = [(None, None), (NS(hit=False), False),
                  (NS(hit=True, filtered_results=[NS(score=.98)]), True),
                  (NS(hit=True, filtered_results=[NS(score=.89)]), None),
                  (NS(hit=True, filtered_results=[NS(score=.99), NS(score=.99)]), None)]
        for detail, expected_value in values:
            with self.subTest(detail=detail):
                action.context.run_recognition.return_value = detail
                self.assertIs(action._trial_empty_grand_state(), expected_value)
        action.context.run_recognition.assert_called_with("自动编队-试炼空冠位格", frame)


if __name__ == "__main__":
    unittest.main()
