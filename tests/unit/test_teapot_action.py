"""茶壶状态守卫：隔离设备，验证两种UI、缺入口保底及不确定时零误点。"""
from collections import deque
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

import numpy as np
from maa.agent.agent_server import AgentServer


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent/custom"))
with patch.object(AgentServer, "custom_action", lambda *args: lambda cls: cls):
    import teapot_action as t


def success(ok=True):
    return NS(status=NS(succeeded=ok))


def context(desired=False):
    return NS(
        tasker=NS(stopping=False, controller=NS()),
        get_node_data=Mock(return_value={"attach": {"use_teapot": desired}}),
        override_pipeline=Mock(return_value=True),
        run_task=Mock(return_value=success()),
    )


class ScriptedRunner(t.TeapotRunner):
    def __init__(self, frames, *, desired=False, failed_action=None, action_callback=None):
        super().__init__(context(desired))
        self.frames = deque(frames)
        self.last = t.TeapotFrame("unknown")
        self.observation_count = 0
        self.actions = []
        self.failed_action = failed_action
        self.action_callback = action_callback

    def observe(self):
        self.observation_count += 1
        if self.frames:
            frame = self.frames.popleft()
            self.last = frame() if callable(frame) else frame
        return self.last

    def _run_task(self, name):
        self.actions.append(name)
        if self.action_callback:
            self.action_callback(name)
        return success(name != self.failed_action)


class TeapotTests(unittest.TestCase):
    def setUp(self):
        for name in ("info", "warning", "error"):
            patcher = patch.object(t.mfaalog, name)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_missing_setting_defaults_to_false_and_only_booleans_are_accepted(self):
        ctx = context()
        for node in ({}, {"attach": {}}, {"attach": {"use_teapot": False}}):
            ctx.get_node_data.return_value = node
            self.assertIs(t._settings(ctx), False)
        ctx.get_node_data.return_value = {"attach": {"use_teapot": True}}
        self.assertIs(t._settings(ctx), True)
        for invalid in (0, 1, None, "false", "true", [], {}):
            with self.subTest(value=invalid):
                ctx.get_node_data.return_value = {"attach": {"use_teapot": invalid}}
                with self.assertRaises(ValueError):
                    t._settings(ctx)

    def recognition_runner(self, hits, *, desired=False, attach=None):
        ctx = context(desired)
        ctx.get_node_data.return_value = {"attach": {"use_teapot": desired, **(attach or {})}}
        runner = t.TeapotRunner(ctx)
        runner._capture = Mock(return_value=object())
        runner._hit = Mock(side_effect=lambda name, _: name in hits)
        return runner

    def test_empty_strip_needs_no_task_or_battle_source_flags(self):
        hits = {"茶壶-识别编队页", "茶壶-识别交替过渡", "茶壶-识别无自动道具栏"}
        for attach in ({}, {"native_battle_trial_profile": "other"},
                       {"trial_no_auto_items": False},
                       {"native_battle_trial_profile": "hellish_202611", "trial_no_auto_items": True}):
            runner = self.recognition_runner(hits, attach=attach)
            with self.subTest(attach=attach):
                self.assertEqual(runner.observe(), t.TeapotFrame("absent"))

    def test_only_info_is_still_transition_without_full_empty_proof(self):
        hits = {"茶壶-识别编队页", "茶壶-识别交替过渡"}
        runner = self.recognition_runner(hits)
        self.assertFalse(runner.run())
        self.assertEqual(runner._capture.call_count, 8)

    def test_empty_strip_never_overrides_unknown_popup_bar_storm_or_conflict(self):
        common = {"茶壶-识别编队页", "茶壶-识别无自动道具栏", "茶壶-识别交替过渡"}
        cases = [(common - {"茶壶-识别编队页"}, t.TeapotFrame("unknown")),
                 (common | {"茶壶-识别弹窗", "茶壶-弹窗已开启"}, t.TeapotFrame("popup", True)),
                 (common | {"茶壶-直接已开启"}, t.TeapotFrame("bar", True)),
                 (common | {"茶壶-直接已关闭"}, t.TeapotFrame("bar", False)),
                 (common | {"茶壶-识别风暴罐"}, t.TeapotFrame("storm")),
                 (common | {"茶壶-直接已开启", "茶壶-直接已关闭"}, t.TeapotFrame("entry"))]
        for hits, expected in cases:
            runner = self.recognition_runner(hits)
            with self.subTest(expected=expected):
                self.assertEqual(runner.observe(), expected)
                self.assertNotIn("茶壶-识别无自动道具栏", [call.args[0] for call in runner._hit.call_args_list])

    def test_empty_full_strip_requires_all_eight_samples_and_makes_no_input(self):
        hits = {"茶壶-识别编队页", "茶壶-识别无自动道具栏", "茶壶-识别交替过渡"}
        for desired in (False, True):
            runner = self.recognition_runner(hits, desired=desired)
            with self.subTest(desired=desired):
                self.assertTrue(runner.run())
                self.assertEqual(runner._capture.call_count, 8)
                self.assertEqual(sum(call.args[0] == "茶壶-识别无自动道具栏"
                                     for call in runner._hit.call_args_list), 8)
                runner.context.run_task.assert_called_once_with(t.NOTICE_NODE)
                self.assertIn("本关无法使用茶壶", runner.context.override_pipeline.call_args.args[0]
                              [t.NOTICE_NODE]["focus"]["Node.Recognition.Starting"])
                runner.context.get_node_data.assert_called_with(t.CONFIG_NODE)

    def test_late_real_transition_interrupts_absence_evidence(self):
        hits = {"茶壶-识别编队页", "茶壶-识别交替过渡"}
        runner = self.recognition_runner(hits)
        runner._hit.side_effect = lambda name, _: (
            name in hits or name == "茶壶-识别无自动道具栏" and runner._capture.call_count < 8)
        self.assertFalse(runner.run())
        self.assertEqual(runner._capture.call_count, 8)
        self.assertIn("校验失败", runner.context.override_pipeline.call_args.args[0]
                      [t.NOTICE_NODE]["focus"]["Node.Recognition.Starting"])

    def test_eighth_non_absent_frame_cannot_confirm_empty_bar(self):
        for frame in (t.TeapotFrame("unknown"), t.TeapotFrame("transition"),
                      t.TeapotFrame("storm"), t.TeapotFrame("entry"),
                      t.TeapotFrame("bar", False), t.TeapotFrame("popup", False)):
            with self.subTest(frame=frame):
                runner = ScriptedRunner([t.TeapotFrame("absent")] * 7 + [frame])
                self.assertFalse(runner.run())
                self.assertEqual(runner.observation_count, 8)
                self.assertEqual(runner.actions, [])

    def test_two_real_bar_readings_after_empty_frames_take_precedence(self):
        runner = ScriptedRunner([t.TeapotFrame("absent")] * 6
                                + [t.TeapotFrame("bar", False)] * 2)
        self.assertEqual(runner._initial(), t.TeapotFrame("bar", False))
        self.assertEqual(runner.observation_count, 8)

    def test_capture_uses_pipeline_wait_and_never_waits_for_screenshot_job_status(self):
        ctx = context()
        job = NS(wait=Mock(side_effect=AssertionError("do not wait for screenshot job")))
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        ctx.tasker.controller = NS(post_screencap=Mock(return_value=job), cached_image=image)
        runner = t.TeapotRunner(ctx)
        actual = runner._capture()
        self.assertTrue(np.array_equal(actual, image))
        self.assertIsNot(actual, image)
        job.wait.assert_not_called()
        ctx.run_task.assert_called_once_with(t.WAIT_NODE)

    def test_capture_failure_is_not_interpreted_as_no_entry(self):
        ctx = context()
        ctx.tasker.controller = NS(post_screencap=Mock(), cached_image=None)
        self.assertFalse(t.TeapotRunner(ctx).run())
        self.assertEqual(ctx.run_task.call_args_list[0].args, (t.WAIT_NODE,))
        self.assertIn("茶壶校验失败", ctx.override_pipeline.call_args.args[0][t.NOTICE_NODE]["focus"]["Node.Recognition.Starting"])

    def test_popup_has_priority_over_underlying_formation(self):
        runner = t.TeapotRunner(context())
        runner._capture = Mock(return_value=object())
        hits = {"茶壶-识别弹窗", "茶壶-弹窗已开启", "茶壶-识别编队页"}
        runner._hit = Mock(side_effect=lambda name, _: name in hits)
        self.assertEqual(runner.observe(), t.TeapotFrame("popup", True))
        self.assertEqual([call.args[0] for call in runner._hit.call_args_list],
                         ["茶壶-识别弹窗", "茶壶-弹窗已开启", "茶壶-弹窗已关闭"])

    def test_unknown_or_occluded_page_cannot_be_absent(self):
        runner = t.TeapotRunner(context())
        runner._capture = Mock(return_value=object())
        runner._hit = Mock(return_value=False)
        self.assertEqual(runner.observe(), t.TeapotFrame("unknown"))
        self.assertEqual(len(runner._hit.call_args_list), 2)

    def test_info_structure_or_storm_frame_does_not_determine_teapot_state(self):
        runner = t.TeapotRunner(context())
        runner._capture = Mock(return_value=object())
        hits = {"茶壶-识别编队页", "茶壶-识别复合入口", "茶壶-识别任意入口"}
        runner._hit = Mock(side_effect=lambda name, _: name in hits)
        self.assertEqual(runner.observe(), t.TeapotFrame("entry"))
        self.assertNotIn("茶壶-识别复合入口", [call.args[0] for call in runner._hit.call_args_list])

    def test_only_teapot_icon_state_becomes_a_readable_outer_bar(self):
        runner = t.TeapotRunner(context())
        runner._capture = Mock(return_value=object())
        hits = {"茶壶-识别编队页", "茶壶-直接已关闭", "茶壶-识别任意入口"}
        runner._hit = Mock(side_effect=lambda name, _: name in hits)
        self.assertEqual(runner.observe(), t.TeapotFrame("bar", False))

    def test_conflicting_direct_states_are_unconfirmed_instead_of_disabled(self):
        runner = t.TeapotRunner(context())
        runner._capture = Mock(return_value=object())
        hits = {"茶壶-识别编队页", "茶壶-直接已开启", "茶壶-直接已关闭",
                "茶壶-识别风暴罐", "茶壶-识别交替过渡"}
        runner._hit = Mock(side_effect=lambda name, _: name in hits)
        self.assertEqual(runner.observe(), t.TeapotFrame("entry"))
        self.assertNotIn("茶壶-识别风暴罐", [call.args[0] for call in runner._hit.call_args_list])
        self.assertNotIn("茶壶-识别交替过渡", [call.args[0] for call in runner._hit.call_args_list])

    def test_explicit_storm_without_teapot_state_is_a_neutral_phase(self):
        runner = t.TeapotRunner(context())
        runner._capture = Mock(return_value=object())
        hits = {"茶壶-识别编队页", "茶壶-识别风暴罐", "茶壶-识别任意入口"}
        runner._hit = Mock(side_effect=lambda name, _: name in hits)
        self.assertEqual(runner.observe(), t.TeapotFrame("storm"))

    def test_explicit_transition_is_neutral_only_after_no_teapot_or_storm_state(self):
        runner = t.TeapotRunner(context())
        runner._capture = Mock(return_value=object())
        hits = {"茶壶-识别编队页", "茶壶-识别交替过渡", "茶壶-识别任意入口"}
        runner._hit = Mock(side_effect=lambda name, _: name in hits)
        self.assertEqual(runner.observe(), t.TeapotFrame("transition"))
        hits.add("茶壶-识别风暴罐")
        self.assertEqual(runner.observe(), t.TeapotFrame("storm"))
        hits.add("茶壶-直接已关闭")
        self.assertEqual(runner.observe(), t.TeapotFrame("bar", False))

    def test_real_eight_sample_cycle_preserves_two_teapot_readings(self):
        cycle = [t.TeapotFrame("transition"), t.TeapotFrame("bar", False),
                 t.TeapotFrame("transition"), t.TeapotFrame("storm"),
                 t.TeapotFrame("transition"), t.TeapotFrame("bar", False),
                 t.TeapotFrame("transition"), t.TeapotFrame("storm")]
        runner = ScriptedRunner(cycle)
        self.assertTrue(runner.run())
        self.assertEqual(runner.observation_count, 6)
        self.assertEqual(runner.actions, [])
        runner = ScriptedRunner([t.TeapotFrame("popup", False)] * 2 + cycle)
        self.assertTrue(runner.run())
        self.assertEqual(runner.observation_count, 8)
        self.assertEqual(runner.actions, ["茶壶-关闭弹窗"])

    def test_transition_does_not_preserve_bar_across_unknown_or_conflicting_entry(self):
        for kind in ("unknown", "entry", "absent"):
            with self.subTest(kind=kind):
                frames = [t.TeapotFrame("bar", False), t.TeapotFrame("transition"),
                          t.TeapotFrame(kind), t.TeapotFrame("transition")] * 2
                runner = ScriptedRunner(frames)
                self.assertFalse(runner.run())
                self.assertEqual(runner.actions, [])
                runner = ScriptedRunner([t.TeapotFrame("popup", False)] * 2 + frames)
                self.assertFalse(runner.run())
                self.assertEqual(runner.actions, ["茶壶-关闭弹窗"])

    def test_transition_does_not_preserve_popup_readings(self):
        frames = [t.TeapotFrame("popup", False), t.TeapotFrame("transition")] * 4
        runner = ScriptedRunner(frames)
        self.assertFalse(runner.run())
        self.assertEqual(runner.actions, [])
        runner = ScriptedRunner([t.TeapotFrame("bar", False)] * 2 + frames, desired=True)
        self.assertFalse(runner.run())
        self.assertEqual(runner.actions, ["茶壶-直接设为开启"])

    def test_transition_only_or_one_teapot_reading_never_proves_state_or_absence(self):
        for frames in ([t.TeapotFrame("transition")] * 8,
                       [t.TeapotFrame("bar", False)] + [t.TeapotFrame("transition")] * 7):
            with self.subTest(frames=frames):
                runner = ScriptedRunner(frames)
                self.assertFalse(runner.run())
                self.assertEqual(runner.observation_count, t.OBSERVATION_ROUNDS)
                self.assertEqual(runner.actions, [])
                message = runner.context.override_pipeline.call_args.args[0][t.NOTICE_NODE]["focus"]["Node.Recognition.Starting"]
                self.assertIn("检测到茶壶入口", message)

    def test_opposite_teapot_state_resets_transition_readings(self):
        frames = [t.TeapotFrame("bar", False), t.TeapotFrame("transition"),
                  t.TeapotFrame("bar", True), t.TeapotFrame("transition")] * 2
        runner = ScriptedRunner(frames)
        self.assertFalse(runner.run())
        self.assertEqual(runner.actions, [])
        runner = ScriptedRunner([t.TeapotFrame("popup", False)] * 2 + frames)
        self.assertFalse(runner.run())
        self.assertEqual(runner.actions, ["茶壶-关闭弹窗"])

    def test_absence_requires_confirmed_formation_and_no_known_entry(self):
        runner = t.TeapotRunner(context())
        runner._capture = Mock(return_value=object())
        runner._hit = Mock(side_effect=lambda name, _: name == "茶壶-识别编队页")
        self.assertEqual(runner.observe(), t.TeapotFrame("absent"))
        runner._hit = Mock(side_effect=lambda name, _: name in {"茶壶-识别编队页", "茶壶-识别任意入口"})
        self.assertEqual(runner.observe(), t.TeapotFrame("entry"))

    def test_matching_outer_bar_never_probes_either_ui_type(self):
        for desired in (False, True):
            with self.subTest(desired=desired):
                runner = ScriptedRunner([t.TeapotFrame("bar", desired)] * 2, desired=desired)
                self.assertTrue(runner.run())
                self.assertEqual(runner.actions, [])

    def test_matching_bar_readings_can_span_explicit_storm_phase(self):
        for desired in (False, True):
            with self.subTest(desired=desired):
                frames = [t.TeapotFrame("bar", desired), t.TeapotFrame("storm"),
                          t.TeapotFrame("bar", desired)]
                runner = ScriptedRunner(frames, desired=desired)
                self.assertTrue(runner.run())
                self.assertEqual(runner.actions, [])
                self.assertEqual(runner.observation_count, 3)

    def test_generic_entry_unknown_and_absence_reset_initial_bar_reading(self):
        for kind in ("entry", "unknown", "absent"):
            with self.subTest(kind=kind):
                frames = [t.TeapotFrame("bar", False), t.TeapotFrame(kind)] * 4
                runner = ScriptedRunner(frames)
                self.assertFalse(runner.run())
                self.assertEqual(runner.actions, [])
                self.assertEqual(runner.observation_count, t.OBSERVATION_ROUNDS)

    def test_opposite_teapot_states_cannot_accumulate_across_storm(self):
        frames = [t.TeapotFrame("bar", False), t.TeapotFrame("storm"),
                  t.TeapotFrame("bar", True), t.TeapotFrame("storm")] * 2
        runner = ScriptedRunner(frames)
        self.assertFalse(runner.run())
        self.assertEqual(runner.actions, [])

    def test_popup_readings_cannot_accumulate_across_storm(self):
        frames = [t.TeapotFrame("popup", False), t.TeapotFrame("storm")] * 4
        runner = ScriptedRunner(frames)
        self.assertFalse(runner.run())
        self.assertEqual(runner.actions, [])

    def test_only_storm_frames_never_prove_teapot_state_or_absence(self):
        runner = ScriptedRunner([t.TeapotFrame("storm")] * t.OBSERVATION_ROUNDS)
        self.assertFalse(runner.run())
        self.assertEqual(runner.actions, [])
        message = runner.context.override_pipeline.call_args.args[0][t.NOTICE_NODE]["focus"]["Node.Recognition.Starting"]
        self.assertIn("检测到茶壶入口", message)

    def test_storm_does_not_turn_one_teapot_reading_into_two(self):
        runner = ScriptedRunner([t.TeapotFrame("bar", False)] + [t.TeapotFrame("storm")] * 7)
        self.assertFalse(runner.run())
        self.assertEqual(runner.observation_count, t.OBSERVATION_ROUNDS)
        self.assertEqual(runner.actions, [])

    def test_direct_switch_is_clicked_once_and_tolerates_old_cached_frames(self):
        for desired in (False, True):
            with self.subTest(desired=desired):
                old, new = t.TeapotFrame("bar", not desired), t.TeapotFrame("bar", desired)
                runner = ScriptedRunner([old] * 4 + [new] * 2, desired=desired)
                self.assertTrue(runner.run())
                self.assertEqual(runner.actions, ["茶壶-直接设为开启" if desired else "茶壶-直接设为关闭"])

    def test_direct_switch_never_reclicks_if_verification_fails(self):
        runner = ScriptedRunner([t.TeapotFrame("bar", False)] * 2, desired=True)
        self.assertFalse(runner.run())
        self.assertEqual(runner.actions, ["茶壶-直接设为开启"])
        self.assertEqual(runner.observation_count, 2 + t.OBSERVATION_ROUNDS)

    def test_direct_verification_accepts_target_bar_readings_across_storm(self):
        frames = ([t.TeapotFrame("bar", False)] * 3 + [t.TeapotFrame("storm"),
                  t.TeapotFrame("bar", True), t.TeapotFrame("storm"), t.TeapotFrame("bar", True)])
        runner = ScriptedRunner(frames, desired=True)
        self.assertTrue(runner.run())
        self.assertEqual(runner.actions, ["茶壶-直接设为开启"])

    def test_generic_or_opposite_frames_reset_switch_verification(self):
        for kind, enabled in (("entry", None), ("unknown", None), ("absent", None), ("bar", False)):
            with self.subTest(kind=kind):
                frames = ([t.TeapotFrame("bar", False)] * 2 +
                          [t.TeapotFrame("bar", True), t.TeapotFrame(kind, enabled)] * 4)
                runner = ScriptedRunner(frames, desired=True)
                self.assertFalse(runner.run())
                self.assertEqual(runner.actions, ["茶壶-直接设为开启"])

    def test_popup_response_cannot_accumulate_across_storm(self):
        frames = ([t.TeapotFrame("bar", False)] * 2 +
                  [t.TeapotFrame("popup", False), t.TeapotFrame("storm")] * 4)
        runner = ScriptedRunner(frames, desired=True)
        self.assertFalse(runner.run())
        self.assertEqual(runner.actions, ["茶壶-直接设为开启"])

    def test_outer_bar_click_response_popup_changes_only_teapot_then_returns(self):
        frames = ([t.TeapotFrame("bar", False)] * 3 + [t.TeapotFrame("entry")] +
                  [t.TeapotFrame("popup", False)] * 2 +
                  [t.TeapotFrame("popup", False)] + [t.TeapotFrame("popup", True)] * 2 +
                  [t.TeapotFrame("popup", True)] + [t.TeapotFrame("entry")] +
                  [t.TeapotFrame("bar", True)] * 2)
        runner = ScriptedRunner(frames, desired=True)
        self.assertTrue(runner.run())
        self.assertEqual(runner.actions, ["茶壶-直接设为开启", "茶壶-弹窗设为开启", "茶壶-关闭弹窗"])
        self.assertFalse(any("风暴" in action for action in runner.actions))

    def test_matching_bar_stops_without_sampling_a_hypothetical_popup(self):
        frames = [t.TeapotFrame("bar", False)] * 2 + [t.TeapotFrame("popup", False)] * 2
        runner = ScriptedRunner(frames)
        self.assertTrue(runner.run())
        self.assertEqual(runner.actions, [])
        self.assertEqual(runner.observation_count, 2)

    def test_opened_popup_already_at_target_does_not_get_toggled_again(self):
        frames = ([t.TeapotFrame("bar", False)] * 2 + [t.TeapotFrame("popup", True)] * 2 +
                  [t.TeapotFrame("bar", True)] * 2)
        runner = ScriptedRunner(frames, desired=True)
        self.assertTrue(runner.run())
        self.assertEqual(runner.actions, ["茶壶-直接设为开启", "茶壶-关闭弹窗"])

    def test_popup_response_with_unreadable_state_does_not_toggle_or_close(self):
        frames = [t.TeapotFrame("bar", False)] * 2 + [t.TeapotFrame("popup")]
        runner = ScriptedRunner(frames, desired=True)
        self.assertFalse(runner.run())
        self.assertEqual(runner.actions, ["茶壶-直接设为开启"])

    def test_initial_popup_is_supported_without_clicking_entrance_again(self):
        frames = ([t.TeapotFrame("popup", True)] * 2 + [t.TeapotFrame("popup", False)] * 2 +
                  [t.TeapotFrame("bar", False)] * 2)
        runner = ScriptedRunner(frames)
        self.assertTrue(runner.run())
        self.assertEqual(runner.actions, ["茶壶-弹窗设为关闭", "茶壶-关闭弹窗"])

    def test_correct_initial_popup_is_only_closed(self):
        runner = ScriptedRunner([t.TeapotFrame("popup", False)] * 2 +
                                [t.TeapotFrame("bar", False)] * 2)
        self.assertTrue(runner.run())
        self.assertEqual(runner.actions, ["茶壶-关闭弹窗"])

    def test_popup_return_verification_accepts_explicit_storm_between_target_readings(self):
        frames = ([t.TeapotFrame("popup", False)] * 2 + [t.TeapotFrame("bar", False),
                  t.TeapotFrame("storm"), t.TeapotFrame("bar", False)])
        runner = ScriptedRunner(frames)
        self.assertTrue(runner.run())
        self.assertEqual(runner.actions, ["茶壶-关闭弹窗"])

    def test_popup_read_or_toggle_failure_preserves_popup(self):
        for frames, desired, expected in (
            ([t.TeapotFrame("popup")], False, []),
            ([t.TeapotFrame("popup", False)] * 2, True, ["茶壶-弹窗设为开启"]),
        ):
            with self.subTest(frames=frames):
                runner = ScriptedRunner(frames, desired=desired)
                self.assertFalse(runner.run())
                self.assertEqual(runner.actions, expected)
                self.assertNotIn("茶壶-关闭弹窗", runner.actions)

    def test_unexpected_page_after_close_fails_without_recovery(self):
        frames = [t.TeapotFrame("popup", False)] * 2 + [t.TeapotFrame("unknown")]
        runner = ScriptedRunner(frames)
        self.assertFalse(runner.run())
        self.assertEqual(runner.actions, ["茶壶-关闭弹窗"])

    def test_returned_bar_must_match_the_verified_popup_setting(self):
        for desired in (False, True):
            with self.subTest(desired=desired):
                frames = [t.TeapotFrame("popup", desired)] * 2 + [t.TeapotFrame("bar", not desired)]
                runner = ScriptedRunner(frames, desired=desired)
                self.assertFalse(runner.run())
                self.assertEqual(runner.actions, ["茶壶-关闭弹窗"])

    def test_opening_action_failure_does_not_attempt_any_further_ui_input(self):
        runner = ScriptedRunner([t.TeapotFrame("bar", False)] * 2, desired=True,
                                failed_action="茶壶-直接设为开启")
        self.assertFalse(runner.run())
        self.assertEqual(runner.actions, ["茶壶-直接设为开启"])

    def test_eight_clear_absent_samples_notify_and_continue_for_both_settings(self):
        for desired in (False, True):
            with self.subTest(desired=desired):
                runner = ScriptedRunner([t.TeapotFrame("absent")] * 8, desired=desired)
                self.assertTrue(runner.run())
                self.assertEqual(runner.observation_count, 8)
                self.assertEqual(runner.actions, [])
                runner.context.run_task.assert_called_once_with(t.NOTICE_NODE)
                message = runner.context.override_pipeline.call_args.args[0][t.NOTICE_NODE]["focus"]["Node.Recognition.Starting"]
                self.assertIn("本关无法使用茶壶", message)

    def test_late_flashing_entry_cannot_be_misclassified_as_absent(self):
        runner = ScriptedRunner([t.TeapotFrame("absent")] * 7 + [t.TeapotFrame("entry")])
        self.assertFalse(runner.run())
        self.assertEqual(runner.actions, [])
        self.assertEqual(runner.observation_count, 8)

    def test_waits_through_storm_or_transition_frames_for_late_teapot_state(self):
        runner = ScriptedRunner([t.TeapotFrame("entry")] * 6 + [t.TeapotFrame("bar", False)] * 2)
        self.assertTrue(runner.run())
        self.assertEqual(runner.actions, [])
        self.assertEqual(runner.observation_count, 8)
        runner.context.override_pipeline.assert_not_called()

    def test_unknown_interruption_resets_absent_evidence(self):
        runner = ScriptedRunner([t.TeapotFrame("absent")] * 3 + [t.TeapotFrame("unknown")] +
                                [t.TeapotFrame("absent")] * 4)
        self.assertFalse(runner.run())
        self.assertEqual(runner.actions, [])

    def test_visible_entry_with_unreadable_state_never_continues_as_absent(self):
        runner = ScriptedRunner([t.TeapotFrame("entry")] * 8)
        self.assertFalse(runner.run())
        self.assertEqual(runner.actions, [])

    def test_stopping_before_start_suppresses_capture_actions_and_prompts(self):
        runner = ScriptedRunner([])
        runner.context.tasker.stopping = True
        self.assertFalse(runner.run())
        self.assertEqual(runner.observation_count, 0)
        self.assertEqual(runner.actions, [])
        runner.context.override_pipeline.assert_not_called()

    def test_stop_after_observation_prevents_toggle(self):
        runner = ScriptedRunner([t.TeapotFrame("bar", False)], desired=True)
        def stopped_frame():
            runner.context.tasker.stopping = True
            return t.TeapotFrame("bar", False)
        runner.frames.append(stopped_frame)
        self.assertFalse(runner.run())
        self.assertEqual(runner.actions, [])
        runner.context.override_pipeline.assert_not_called()

    def test_stop_during_opening_prevents_further_capture_or_close(self):
        runner = ScriptedRunner([t.TeapotFrame("bar", False)] * 2, desired=True)
        runner.action_callback = lambda _: setattr(runner.context.tasker, "stopping", True)
        self.assertFalse(runner.run())
        self.assertEqual(runner.actions, ["茶壶-直接设为开启"])
        self.assertEqual(runner.observation_count, 2)
        runner.context.override_pipeline.assert_not_called()

    def test_focus_failure_keeps_absent_notification_and_continue_result(self):
        runner = ScriptedRunner([t.TeapotFrame("absent")] * 8)
        runner.context.override_pipeline.side_effect = RuntimeError("focus unavailable")
        self.assertTrue(runner.run())
        self.assertEqual(runner.actions, [])
        t.mfaalog.info.assert_any_call("[茶壶] 本关无法使用茶壶，跳过茶壶设置后继续战斗。")

    def test_error_prompt_is_escaped_and_invalid_config_never_operates(self):
        runner = ScriptedRunner([], desired="true")
        self.assertFalse(runner.run())
        self.assertEqual(runner.actions, [])
        self.assertEqual(runner.observation_count, 0)
        runner = ScriptedRunner([])
        runner.observe = Mock(side_effect=t.TeapotUnconfirmed('<unknown & "state">'))
        self.assertFalse(runner.run())
        message = runner.context.override_pipeline.call_args.args[0][t.NOTICE_NODE]["focus"]["Node.Recognition.Starting"]
        self.assertIn("&lt;unknown &amp; &quot;state&quot;&gt;", message)

    def test_custom_action_propagates_runner_result(self):
        for ok in (False, True):
            with self.subTest(ok=ok), patch.object(t, "TeapotRunner") as runner:
                runner.return_value.run.return_value = ok
                result = t.EnsureNativeTeapot().run(context(), NS())
                self.assertIs(result.success, ok)


if __name__ == "__main__":
    unittest.main()
