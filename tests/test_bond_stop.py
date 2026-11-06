"""羁绊停止状态、同帧证据及外层停止传播；不连接设备。"""
import copy
from dataclasses import dataclass
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]

import bond_stop_action as action
import auto_battle_repeat_action as repeat
import auto_battle_action as battle
import main_story_action as story
import free_quest_action as free
from battle.core.models import FormationSlot
from battle.runtime.bond_stop_state import BondStopStates, bond_stops
from battle.runtime.formation_session import FormationSessions


@dataclass
class Freeze:
    time: int = 1000
    timeout: int = 8000


def frame(marker=1):
    return np.full((8, 12, 3), marker, dtype=np.uint8)


def slots(support=3):
    return tuple(FormationSlot(i, "unknown", is_support=i == support) for i in range(1, 7))


class Context:
    def __init__(self, mode="all", target=6, root=8001, frames=None):
        self.root = root
        self.nodes = {
            action.CONFIG_NODE: {"attach": {"native_bond_stop_mode": mode, "native_bond_stop_slot": target}},
            action.GUARD_NODE: {"attach": {
                "check_attempts": 3, "check_wait_node": "wait",
                "positive_wait_node": "fast", "positive_attempts": 2, "positive_timeout_ms": 1000,
                "page_wait_node": "page_wait", "page_wait_attempts": 2, "page_wait_timeout_ms": 1000,
            }},
        }
        self.frames = list(frames or [frame(2), frame(3)])
        self.current = None
        self.tasker = NS(stopping=False, controller=NS(post_screencap=self.capture))
        self.run_task = Mock(return_value=NS(status=NS(succeeded=True)))
        self.run_recognition = Mock(return_value=NS(hit=False))
        self.wait_freezes = Mock(return_value=True)
        self.clear_hit_count = Mock()
        self.freeze = Freeze()

    def get_task_job(self):
        return NS(job_id=self.root)

    def capture(self):
        self.current = self.frames.pop(0) if self.frames else self.current
        return NS(wait=lambda: NS(get=lambda: self.current))

    def get_node_data(self, name):
        return copy.deepcopy(self.nodes.get(name, {}))

    def get_node_object(self, name):
        return NS(post_wait_freezes=self.freeze)

    def override_pipeline(self, override):
        self.nodes.update(override)
        return True

    def clone(self):
        return self


class StateTests(unittest.TestCase):
    def test_root_restart_rejects_stale_callback_and_isolates_other_roots(self):
        state = BondStopStates()
        old = state.begin(1, "a")
        other = state.begin(2, "b")
        state.latch(2, other, reason="bond_max", message="other")
        current = state.begin(1, "a")
        self.assertIsNone(state.latch(1, old, reason="bond_max", message="stale"))
        state.latch(1, current, reason="bond_max", message="current")
        state.stop_owner("a")
        self.assertIsNone(state.get(1))
        self.assertEqual(state.get(2).message, "other")
        state.finish(2)
        self.assertIsNone(state.get(2))

    def test_formation_mirror_survives_identity_finish_but_not_new_formation(self):
        root = 8801
        bond_stops.begin(root)
        store = FormationSessions()
        token = store.begin(root)
        revision = store.begin_update(root, token)
        store.publish_verified(root, token, revision, slots(support=1))
        self.assertTrue(bond_stops.formation(root)[0]["is_support"])
        self.assertNotIn("servant_id", bond_stops.formation(root)[0])
        store.finish(root, token)
        self.assertEqual(len(bond_stops.formation(root)), 6)
        token = store.begin(root)
        self.assertEqual(bond_stops.formation(root), ())
        store.skip_capture(root, token)
        bond_stops.finish(root)


class GuardTests(unittest.TestCase):
    def setUp(self):
        bond_stops.finish(8001)
        self.page = patch.object(action, "read_bond_page", return_value=True)
        self.page.start()

    def tearDown(self):
        self.page.stop()
        bond_stops.finish(8001)

    def test_disabled_does_not_capture_or_inspect_a_page(self):
        ctx = Context(mode="off")
        self.assertIsNone(action.inspect_bond_stop(ctx, frame()))
        action.read_bond_page.assert_not_called()
        ctx.run_task.assert_not_called()

    def test_new_callback_gets_its_own_controller_once(self):
        first = Context(frames=[frame(2), frame(3)])
        previous_controller = Mock(post_screencap=Mock(side_effect=first.capture))
        with patch.object(action, "read_any_bond_max", return_value=False):
            self.assertIsNone(action.inspect_bond_stop(first, frame(1), controller=previous_controller))
            previous_controller.post_screencap.side_effect = OSError("expired callback controller")

            second = Context(frames=[frame(5), frame(6)])
            current_controller = Mock(post_screencap=Mock(side_effect=second.capture))
            reads = []

            class CurrentTasker:
                stopping = False

                @property
                def controller(self):
                    reads.append(True)
                    if len(reads) > 1:
                        raise AssertionError("controller acquired twice in one callback")
                    return current_controller

            second.tasker = CurrentTasker()
            self.assertIsNone(action.inspect_bond_stop(second, frame(4)))
        self.assertEqual(len(reads), 1)
        self.assertGreater(current_controller.post_screencap.call_count, 0)

    def test_explicit_controller_is_reused_without_reacquiring(self):
        ctx = Context()
        controller = Mock(post_screencap=Mock(side_effect=ctx.capture))

        class CurrentTasker:
            stopping = False

            @property
            def controller(self):
                raise AssertionError("must reuse the controller supplied by this callback")

        ctx.tasker = CurrentTasker()
        with patch.object(action, "read_any_bond_max", return_value=False):
            self.assertIsNone(action.inspect_bond_stop(ctx, frame(), controller=controller))
        self.assertGreater(controller.post_screencap.call_count, 0)

    def multiselect(self, targets, *, support=4, frames=None):
        ctx = Context(mode="slots", frames=frames)
        ctx.nodes[action.CONFIG_NODE]["attach"].update({
            f"native_bond_stop_slot_{i}": i in targets for i in range(1, 7)})
        bond_stops.set_formation(ctx.root, slots(support=support))
        return ctx

    def test_multiple_selected_maxima_report_actual_formation_slots(self):
        ctx = self.multiselect((1, 4, 6))  # Support4 is ignored; slot6 -> column5.
        with patch.object(action, "read_bond_max", return_value=(1, 3, 5)):
            result = action.inspect_bond_stop(ctx, frame())
        self.assertEqual(result.formation_slots, (1, 6))
        self.assertEqual(result.result_columns, (1, 5))

    def test_unselected_max_does_not_stop_selected_members(self):
        ctx = self.multiselect((1, 6))
        with patch.object(action, "read_bond_max", return_value=(3,)):
            self.assertIsNone(action.inspect_bond_stop(ctx, frame()))

    def test_default_all_uses_whole_row_without_mapping_and_does_not_defer(self):
        ctx = self.multiselect(range(1, 7))
        bond_stops.battle_started(ctx.root, 1, another_battle_planned=True)
        self.assertEqual(action._settings(ctx), ("all", 0))
        with patch.object(action, "read_any_bond_max", return_value=True):
            self.assertEqual(action.inspect_bond_stop(ctx, frame()).reason, "bond_max")
        self.assertIsNone(bond_stops.deferred(ctx.root))

    def test_empty_or_only_support_selection_has_actionable_error(self):
        for targets, expected in (((), "至少勾选一个"), ((4,), "助战或空位")):
            with self.subTest(targets=targets):
                bond_stops.begin(8001)
                ctx = self.multiselect(targets)
                result = action.inspect_bond_stop(ctx, frame())
                self.assertEqual(result.reason, "bond_check_unconfirmed")
                self.assertIn(expected, result.message)
                self.assertIn("检查号位", result.message)

    def test_partial_selection_upgrade_defers_then_checks_next_battle(self):
        ctx = self.multiselect((1, 6))
        bond_stops.battle_started(ctx.root, 1, keep_formation=True, another_battle_planned=True)
        action.read_bond_page.return_value = False
        with patch.object(action, "read_bond_upgrade", return_value=True), patch.object(action, "_save_evidence"):
            self.assertIsNone(action.inspect_bond_stop(ctx, frame(9)))
        self.assertIsNotNone(bond_stops.deferred(ctx.root))
        bond_stops.battle_started(ctx.root, 2, keep_formation=True)
        action.read_bond_page.return_value = True
        with patch.object(action, "read_bond_max", return_value=(5,)):
            result = action.inspect_bond_stop(ctx, frame())
        self.assertEqual(result.battle, 2)
        self.assertEqual(result.formation_slots, (6,))

    def test_all_mode_needs_no_formation_and_retains_actual_hit_frame(self):
        ctx = Context()
        with patch.object(action, "read_any_bond_max", return_value=True):
            stopped = action.inspect_bond_stop(ctx, frame())
        self.assertEqual(stopped.reason, "bond_max")
        self.assertEqual(stopped.formation_slots, ())
        np.testing.assert_array_equal(stopped.image, frame())
        ctx.wait_freezes.assert_not_called()
        ctx.run_task.assert_not_called()

    def test_initial_max_then_upgrade_popup_uses_initial_frame_without_waiting(self):
        ctx = Context(frames=[frame(9)])
        action.read_bond_page.side_effect = [True, False]
        with patch.object(action, "read_any_bond_max", return_value=True):
            result = action.inspect_bond_stop(ctx, frame(4))
        self.assertEqual(result.reason, "bond_max")
        np.testing.assert_array_equal(result.image, frame(4))
        self.assertEqual(len(ctx.frames), 1)
        ctx.wait_freezes.assert_not_called()
        ctx.run_task.assert_not_called()

    def test_other_settlement_page_does_not_wait_or_capture(self):
        ctx = Context()
        action.read_bond_page.return_value = False
        self.assertIsNone(action.inspect_bond_stop(ctx, frame()))
        ctx.run_task.assert_not_called()
        ctx.wait_freezes.assert_not_called()

    def test_max_appearing_after_initial_negative_is_still_detected(self):
        ctx = Context(frames=[frame(2), frame(3), frame(4)])
        with patch.object(action, "read_any_bond_max", side_effect=[False, True, True]):
            self.assertEqual(action.inspect_bond_stop(ctx, frame()).reason, "bond_max")
        ctx.wait_freezes.assert_not_called()

    def test_negative_then_popup_stops_unconfirmed_and_keeps_both_frames(self):
        ctx = Context(frames=[frame(9)])
        with patch.object(action, "read_any_bond_max", side_effect=[False, ValueError("bond_result_page_not_visible")]):
            result = action.inspect_bond_stop(ctx, frame(4))
        self.assertEqual(result.reason, "bond_check_unconfirmed")
        np.testing.assert_array_equal(result.image, frame(4))
        np.testing.assert_array_equal(result.latest_image, frame(9))
        with tempfile.TemporaryDirectory() as tmp, patch.object(action, "Path", side_effect=lambda value: Path(tmp) / value):
            action.save_bond_stop(ctx)
            self.assertTrue(Path(result.screenshot_path).is_file())
            self.assertTrue(Path(result.latest_screenshot_path).is_file())
            self.assertEqual(len(list(Path(tmp).rglob("*.png"))), 2)

    def test_selected_upgrade_defers_only_with_remaining_battle_and_saves_once(self):
        ctx = Context(mode="slot", target=6)
        bond_stops.battle_started(ctx.root, 1, another_battle_planned=True)
        bond_stops.set_formation(ctx.root, slots(support=4))
        action.read_bond_page.return_value = False
        with patch.object(action, "read_bond_upgrade", return_value=True), \
             tempfile.TemporaryDirectory() as tmp, \
             patch.object(action, "Path", side_effect=lambda value: Path(tmp) / value):
            self.assertIsNone(action.inspect_bond_stop(ctx, frame(9)))
            self.assertIsNone(action.inspect_bond_stop(ctx, frame(9)))
            deferred = bond_stops.deferred(ctx.root)
            self.assertEqual(deferred.reason, "bond_upgrade_deferred")
            self.assertTrue(Path(deferred.screenshot_path).is_file())
            self.assertIn("bond_deferred_", deferred.screenshot_path)
            self.assertEqual(len(list(Path(tmp).rglob("*.png"))), 1)
        self.assertIsNone(bond_stops.get(ctx.root))
        ctx.run_task.assert_not_called()
        ctx.wait_freezes.assert_not_called()

    def test_upgrade_does_not_defer_last_battle_or_all_mode(self):
        for mode, remaining in (("slot", False), ("all", True)):
            with self.subTest(mode=mode):
                ctx = Context(mode=mode)
                bond_stops.begin(ctx.root)
                bond_stops.battle_started(ctx.root, 1, another_battle_planned=remaining)
                bond_stops.set_formation(ctx.root, slots(support=4))
                action.read_bond_page.return_value = False
                with patch.object(action, "read_bond_upgrade", return_value=True):
                    stopped = action.inspect_bond_stop(ctx, frame(9))
                self.assertEqual(stopped.reason, "bond_check_unconfirmed")
                self.assertIsNone(bond_stops.deferred(ctx.root))
                self.assertNotIn("羁绊MAX，已停止", stopped.message)

    def test_initial_bond_page_then_upgrade_defers_with_both_frames(self):
        ctx = Context(mode="slot", frames=[frame(9)])
        bond_stops.battle_started(ctx.root, 1, another_battle_planned=True)
        bond_stops.set_formation(ctx.root, slots(support=4))
        with patch.object(action, "read_bond_max", side_effect=[(), ValueError("bond_result_page_not_visible")]), \
             patch.object(action, "read_bond_upgrade", return_value=True), \
             patch.object(action, "_save_evidence"):
            self.assertIsNone(action.inspect_bond_stop(ctx, frame(4)))
        deferred = bond_stops.deferred(ctx.root)
        np.testing.assert_array_equal(deferred.image, frame(4))
        np.testing.assert_array_equal(deferred.latest_image, frame(9))
        self.assertIsNone(bond_stops.get(ctx.root))

    def test_upgrade_preventing_freeze_can_defer_but_other_errors_cannot(self):
        for upgrade in (True, False):
            with self.subTest(upgrade=upgrade):
                ctx = Context(mode="slot", frames=[frame(2), frame(3), frame(9)])
                ctx.wait_freezes.return_value = False
                bond_stops.begin(ctx.root)
                bond_stops.battle_started(ctx.root, 1, another_battle_planned=True)
                bond_stops.set_formation(ctx.root, slots(support=4))
                with patch.object(action, "read_bond_max", return_value=()), \
                     patch.object(action, "read_bond_upgrade", return_value=upgrade), \
                     patch.object(action, "_save_evidence"):
                    result = action.inspect_bond_stop(ctx, frame(4))
                self.assertEqual(bool(bond_stops.deferred(ctx.root)), upgrade)
                if upgrade:
                    self.assertIsNone(result)
                else:
                    self.assertEqual(result.reason, "bond_check_unconfirmed")

    def test_upgrade_never_bypasses_missing_mapping_even_with_remaining_battles(self):
        ctx = Context(mode="slot")
        bond_stops.battle_started(ctx.root, 1, another_battle_planned=True)
        action.read_bond_page.return_value = False
        with patch.object(action, "read_bond_upgrade", return_value=True):
            result = action.inspect_bond_stop(ctx, frame(9))
        self.assertEqual(result.reason, "bond_check_unconfirmed")
        self.assertIsNone(bond_stops.deferred(ctx.root))

    def test_selected_max_still_stops_before_upgrade_despite_remaining_battles(self):
        ctx = Context(mode="slot", target=6)
        bond_stops.battle_started(ctx.root, 1, another_battle_planned=True)
        bond_stops.set_formation(ctx.root, slots(support=4))
        with patch.object(action, "read_bond_max", return_value=(5,)), \
             patch.object(action, "read_bond_upgrade", return_value=True) as upgrade:
            result = action.inspect_bond_stop(ctx, frame(4))
        self.assertEqual(result.reason, "bond_max")
        self.assertEqual(result.formation_slots, (6,))
        self.assertIsNone(bond_stops.deferred(ctx.root))
        upgrade.assert_not_called()

    def test_unrecognized_obstruction_does_not_use_remaining_budget(self):
        ctx = Context(mode="slot", frames=[frame(9)])
        bond_stops.battle_started(ctx.root, 1, another_battle_planned=True)
        bond_stops.set_formation(ctx.root, slots(support=4))
        with patch.object(action, "read_bond_max", side_effect=[(), ValueError("bond_result_page_not_visible")]), \
             patch.object(action, "read_bond_upgrade", return_value=False):
            result = action.inspect_bond_stop(ctx, frame(4))
        self.assertEqual(result.reason, "bond_check_unconfirmed")
        self.assertIsNone(bond_stops.deferred(ctx.root))

    def test_next_battle_after_deferred_upgrade_stops_on_selected_max(self):
        ctx = Context(mode="slot", target=6)
        bond_stops.battle_started(ctx.root, 1, another_battle_planned=True)
        bond_stops.set_formation(ctx.root, slots(support=4))
        action.read_bond_page.return_value = False
        with patch.object(action, "read_bond_upgrade", return_value=True), patch.object(action, "_save_evidence"):
            self.assertIsNone(action.inspect_bond_stop(ctx, frame(9)))
        self.assertIsNotNone(bond_stops.deferred(ctx.root))
        bond_stops.battle_started(ctx.root, 2, another_battle_planned=False)
        bond_stops.set_formation(ctx.root, slots(support=4))
        action.read_bond_page.return_value = True
        with patch.object(action, "read_bond_max", return_value=(5,)):
            result = action.inspect_bond_stop(ctx, frame(4))
        self.assertEqual(result.reason, "bond_max")
        self.assertEqual(result.battle, 2)
        self.assertEqual(result.formation_slots, (6,))
        self.assertIsNone(bond_stops.deferred(ctx.root))

    def test_freeze_failure_never_releases_a_negative_result(self):
        ctx = Context()
        ctx.wait_freezes.return_value = False
        with patch.object(action, "read_any_bond_max", return_value=False) as read:
            result = action.inspect_bond_stop(ctx, frame())
        self.assertEqual(result.reason, "bond_check_unconfirmed")
        self.assertGreaterEqual(read.call_count, 1)

    def test_slot_uses_non_support_order_and_ignores_other_max(self):
        ctx = Context(mode="slot", target=6)
        bond_stops.set_formation(ctx.root, slots(support=1))
        with patch.object(action, "read_bond_max", return_value=(4,)):
            self.assertIsNone(action.inspect_bond_stop(ctx, frame()))
        ctx.frames = [frame(4), frame(5)]
        with patch.object(action, "read_bond_max", return_value=(5,)):
            result = action.inspect_bond_stop(ctx, frame(9))
        self.assertEqual(result.formation_slots, (6,))
        self.assertEqual(result.result_columns, (5,))

    def test_unmapped_target_or_short_layout_stops_instead_of_guessing(self):
        for evidence in ((), slots(support=6), tuple(
                FormationSlot(i, "empty", is_support=False) if i == 5 else value
                for i, value in enumerate(slots(), 1))):
            with self.subTest(evidence=evidence):
                bond_stops.begin(8001)
                bond_stops.set_formation(8001, evidence)
                with patch.object(action, "read_bond_max") as read:
                    result = action.inspect_bond_stop(Context(mode="slot"), frame())
                self.assertEqual(result.reason, "bond_check_unconfirmed")
                read.assert_not_called()

    def test_same_negative_image_cached_changed_image_rechecked(self):
        ctx = Context()
        first = frame()
        with patch.object(action, "read_any_bond_max", return_value=False) as read:
            self.assertIsNone(action.inspect_bond_stop(ctx, first))
            self.assertIsNone(action.inspect_bond_stop(ctx, first))
            self.assertEqual(read.call_count, 5)
            ctx.frames = [frame(6), frame(7)]
            self.assertIsNone(action.inspect_bond_stop(ctx, frame(8)))
            self.assertEqual(read.call_count, 10)

    def test_screenshot_keeps_hit_frame_and_failure_keeps_stop(self):
        ctx = Context()
        with patch.object(action, "read_any_bond_max", return_value=True):
            result = action.inspect_bond_stop(ctx, frame())
        with tempfile.TemporaryDirectory() as tmp, patch.object(action, "Path", side_effect=lambda value: Path(tmp) / value):
            action.save_bond_stop(ctx)
            restored = cv2.imdecode(np.fromfile(result.screenshot_path, np.uint8), cv2.IMREAD_COLOR)
            np.testing.assert_array_equal(restored, result.image)
            self.assertIn("battle1_slotunconfirmed", result.screenshot_path)
        bond_stops.begin(ctx.root)
        result = bond_stops.latch(ctx.root, bond_stops.ensure(ctx.root), reason="bond_max", message="MAX", image=frame())
        with patch.object(action.cv2, "imencode", side_effect=OSError("disk full")):
            action.save_bond_stop(ctx)
        self.assertEqual(bond_stops.get(ctx.root).reason, "bond_max")
        self.assertIn("disk full", result.screenshot_error)


class OuterLoopTests(unittest.TestCase):
    def tearDown(self):
        bond_stops.finish(8001)

    def test_after_battle_handoff_only_checks_confirmed_victory(self):
        for ok, reason, mode, expected in (
            (True, "victory", "all", True), (True, "defeat", "all", False),
            (False, "timeout", "all", False), (True, "victory", "off", False),
        ):
            with self.subTest(ok=ok, reason=reason, mode=mode):
                ctx = Context(mode=mode)
                with patch.object(action, "inspect_bond_stop", return_value=None) as inspect, \
                     patch.object(action, "read_bond_page", return_value=True):
                    action.inspect_victory_settlement(ctx, NS(ok=ok, reason=reason), ctx.tasker.controller)
                self.assertEqual(inspect.called, expected)
                self.assertEqual(len(ctx.frames), 1 if expected else 2)

    def test_disabled_handoff_does_not_require_a_task_context_or_capture(self):
        for context in (NS(get_node_data=lambda name: {}, tasker=NS(stopping=False)),
                        NS(tasker=NS(stopping=False))):
            with self.subTest(context=context):
                controller = Mock()
                self.assertIsNone(action.inspect_victory_settlement(
                    context, NS(ok=True, reason="victory"), controller))
                controller.post_screencap.assert_not_called()

    def test_front_result_then_bond_max_is_captured_before_pipeline_handoff(self):
        ctx = Context(frames=[frame(1), frame(7)])
        with patch.object(action, "read_bond_page", side_effect=lambda c, image: int(image[0, 0, 0]) == 7), \
             patch.object(action, "read_any_bond_max", return_value=True), \
             patch.object(action, "save_bond_stop"):
            result = action.inspect_victory_settlement(ctx, NS(ok=True, reason="victory"), ctx.tasker.controller)
        self.assertEqual(result.reason, "bond_max")
        np.testing.assert_array_equal(result.image, frame(7))
        self.assertEqual(ctx.run_task.call_args.args[0], "page_wait")
        ctx.wait_freezes.assert_not_called()

    def test_front_result_without_bond_page_stops_unconfirmed_after_bound(self):
        ctx = Context(frames=[frame(1), frame(9)])
        with patch.object(action, "read_bond_page", return_value=False), \
             patch.object(action, "save_bond_stop"):
            result = action.inspect_victory_settlement(ctx, NS(ok=True, reason="victory"), ctx.tasker.controller)
        self.assertEqual(result.reason, "bond_check_unconfirmed")
        self.assertEqual(ctx.run_task.call_count, 2)
        np.testing.assert_array_equal(result.image, frame(1))
        np.testing.assert_array_equal(result.latest_image, frame(9))
        ctx.wait_freezes.assert_not_called()

    def test_loot_is_advanced_once_then_bond_max_still_stops(self):
        ctx = Context(frames=[frame(1), frame(7)])
        ctx.run_recognition.side_effect = lambda name, image: NS(hit=name == "战斗_战利品弹窗")
        with patch.object(action, "read_bond_page", side_effect=lambda c, image: int(image[0, 0, 0]) == 7), \
             patch.object(action, "read_any_bond_max", return_value=True), \
             patch.object(action, "save_bond_stop"):
            result = action.inspect_victory_settlement(ctx, NS(ok=True, reason="victory"), ctx.tasker.controller)
        self.assertEqual(result.reason, "bond_max")
        self.assertEqual([call.args[0] for call in ctx.run_task.call_args_list],
                         ["结算-战利品弹窗下一步", "page_wait"])

    def test_stuck_loot_does_not_click_repeatedly_or_skip_bond_check(self):
        ctx = Context(frames=[frame(1), frame(9)])
        ctx.run_recognition.side_effect = lambda name, image: NS(hit=name == "战斗_战利品弹窗")
        with patch.object(action, "read_bond_page", return_value=False), \
             patch.object(action, "save_bond_stop"):
            result = action.inspect_victory_settlement(ctx, NS(ok=True, reason="victory"), ctx.tasker.controller)
        self.assertEqual(result.reason, "bond_check_unconfirmed")
        self.assertEqual([call.args[0] for call in ctx.run_task.call_args_list],
                         ["结算-战利品弹窗下一步", "page_wait", "page_wait"])

    def test_loot_appearing_during_wait_is_advanced_before_bond_max(self):
        ctx = Context(frames=[frame(1), frame(2), frame(7)])
        ctx.run_recognition.side_effect = lambda name, image: NS(
            hit=name == "战斗_战利品弹窗" and int(image[0, 0, 0]) == 2)
        with patch.object(action, "read_bond_page", side_effect=lambda c, image: int(image[0, 0, 0]) == 7), \
             patch.object(action, "read_any_bond_max", return_value=True), \
             patch.object(action, "save_bond_stop"):
            result = action.inspect_victory_settlement(ctx, NS(ok=True, reason="victory"), ctx.tasker.controller)
        self.assertEqual(result.reason, "bond_max")
        self.assertEqual([call.args[0] for call in ctx.run_task.call_args_list],
                         ["page_wait", "结算-战利品弹窗下一步", "page_wait"])

    def test_loot_click_failure_stops_before_waiting_or_retrying(self):
        ctx = Context(frames=[frame(1)])
        ctx.run_recognition.side_effect = lambda name, image: NS(hit=name == "战斗_战利品弹窗")
        ctx.run_task.return_value = NS(status=NS(succeeded=False))
        with patch.object(action, "read_bond_page", return_value=False), \
             patch.object(action, "save_bond_stop"):
            result = action.inspect_victory_settlement(ctx, NS(ok=True, reason="victory"), ctx.tasker.controller)
        self.assertEqual(result.reason, "bond_check_unconfirmed")
        ctx.run_task.assert_called_once_with("结算-战利品弹窗下一步")

    def test_victory_handoff_recognizes_upgrade_before_bond_page_and_defers(self):
        ctx = Context(mode="slot", frames=[frame(1), frame(9)])
        bond_stops.battle_started(ctx.root, 1, another_battle_planned=True)
        bond_stops.set_formation(ctx.root, slots(support=4))
        with patch.object(action, "read_bond_page", return_value=False), \
             patch.object(action, "read_bond_upgrade", side_effect=lambda c, image: int(image[0, 0, 0]) == 9), \
             patch.object(action, "_save_evidence"):
            result = action.inspect_victory_settlement(ctx, NS(ok=True, reason="victory"), ctx.tasker.controller)
        self.assertIsNone(result)
        self.assertIsNone(bond_stops.get(ctx.root))
        np.testing.assert_array_equal(bond_stops.deferred(ctx.root).image, frame(9))
        self.assertEqual(ctx.run_task.call_count, 1)
        ctx.wait_freezes.assert_not_called()

    def test_battle_entry_handoff_is_called_only_for_victory(self):
        for ok, reason in ((True, "victory"), (True, "defeat"), (False, "timeout")):
            with self.subTest(ok=ok, reason=reason):
                ctx = Context(mode="off")
                with patch.object(battle, "AutoBattleRuntime") as runtime, \
                     patch.object(battle, "inspect_victory_settlement") as handoff:
                    runtime.return_value.run.return_value = NS(ok=ok, reason=reason, turns=1)
                    result = battle.AutoBattleAction().run(ctx, NS(custom_action_param="{}", node_name="原生自动战斗入口"))
                self.assertEqual(result.success, ok)
                self.assertEqual(handoff.called, ok and reason == "victory")

    def test_latched_stop_is_consumed_even_when_current_page_is_popup(self):
        ctx = Context()
        token = bond_stops.ensure(ctx.root)
        existing = bond_stops.latch(ctx.root, token, reason="bond_max", message="MAX", image=frame())
        with patch.object(action, "read_bond_page", return_value=False) as page:
            self.assertIs(action.inspect_bond_stop(ctx, frame(9)), existing)
        page.assert_not_called()

    def test_repeat_succeeded_child_stop_never_starts_next_battle(self):
        ctx = Context()
        def child(*args, **kwargs):
            bond_stops.latch(ctx.root, bond_stops.ensure(ctx.root), reason="bond_max", message="第1场羁绊MAX")
            return NS(status=NS(succeeded=True, failed=False), entry=args[0])
        ctx.run_task.side_effect = child
        with patch.object(repeat, "save_bond_stop", side_effect=lambda c: bond_stops.get(c.root)):
            result = repeat.AutoBattleRepeatAction().run(ctx, NS(custom_action_param='{"battle_count":3}'))
        self.assertTrue(result.success)
        self.assertEqual(ctx.run_task.call_count, 1)
        self.assertIn("条件达成停止", str(ctx.nodes["bbc弹窗信息输出"]))
        self.assertNotIn("共 3 场", str(ctx.nodes["bbc弹窗信息输出"]))

    def test_story_and_free_settle_check_before_any_progress_input(self):
        for runner_type in (story.StoryRunner, free.FreeQuestRunner):
            with self.subTest(runner=runner_type):
                runner = object.__new__(runner_type)
                ctx = Context()
                runner.ctx, runner.tasker, runner.controller = ctx, ctx.tasker, ctx.tasker.controller
                runner.mode, runner.timeout = "native", 120
                runner.clock = lambda: 0
                runner.frame = Mock(return_value=frame())
                runner.failure, runner.action = Mock(), Mock()
                stopped = NS(reason="bond_max", display=lambda: "MAX")
                with patch.object(story, "check_settlement_bond_stop", side_effect=action.BondStopReached(stopped)):
                    with self.assertRaises(action.BondStopReached):
                        runner.settle()
                runner.failure.assert_not_called()
                runner.action.assert_not_called()

    def test_outer_runners_report_condition_stop_without_generic_failure(self):
        for module, action_type, runner_name, result_node in (
            (story, story.MainStoryAction, "StoryRunner", "主线-结果提示"),
            (free, free.FreeQuestAction, "FreeQuestRunner", "Free-结果提示"),
        ):
            with self.subTest(module=module.__name__):
                ctx = Context()
                reached = NS(reason="bond_max", display=lambda: "第2场羁绊MAX；截图已保存")
                with patch.object(module, runner_name) as factory:
                    factory.return_value.run.side_effect = action.BondStopReached(reached)
                    result = action_type().run(ctx, NS())
                self.assertTrue(result.success)
                args, kwargs = ctx.run_task.call_args
                self.assertEqual(args[0], result_node)
                self.assertIn("条件达成停止", str(kwargs))
                self.assertNotIn("中断", str(kwargs))


if __name__ == "__main__":
    unittest.main()
