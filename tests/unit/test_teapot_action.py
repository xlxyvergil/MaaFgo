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

    def test_missing_setting_defaults_to_false_and_accepts_three_states(self):
        ctx = context()
        for node in ({}, {"attach": {}}, {"attach": {"use_teapot": False}}):
            ctx.get_node_data.return_value = node
            self.assertIs(t._settings(ctx), False)
        ctx.get_node_data.return_value = {"attach": {"use_teapot": True}}
        self.assertIs(t._settings(ctx), True)
        ctx.get_node_data.return_value = {"attach": {"use_teapot": "skip"}}
        self.assertEqual(t._settings(ctx), "skip")
        for invalid in (0, 1, None, "false", "true", [], {}):
            with self.subTest(value=invalid):
                ctx.get_node_data.return_value = {"attach": {"use_teapot": invalid}}
                with self.assertRaises(ValueError):
                    t._settings(ctx)

    def test_skip_returns_before_any_teapot_capture_recognition_wait_or_action(self):
        ctx = context("skip")
        ctx.tasker.controller.post_screencap = Mock(side_effect=AssertionError("不检测时不能截图"))
        ctx.run_recognition = Mock(side_effect=AssertionError("不检测时不能识别"))
        runner = t.TeapotRunner(ctx)
        runner._repeat_session = Mock(side_effect=AssertionError("不检测时不能复用确认状态"))
        self.assertTrue(runner.run())
        runner._repeat_session.assert_not_called()
        ctx.tasker.controller.post_screencap.assert_not_called()
        ctx.run_recognition.assert_not_called()
        ctx.run_task.assert_not_called()
        ctx.override_pipeline.assert_not_called()

    def test_skip_still_respects_stop_request(self):
        ctx = context("skip")
        ctx.tasker.stopping = True
        self.assertFalse(t.TeapotRunner(ctx).run())
        ctx.get_node_data.assert_not_called()
        ctx.run_task.assert_not_called()

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

    def test_empty_full_strip_requires_three_samples_and_makes_no_input(self):
        hits = {"茶壶-识别编队页", "茶壶-识别无自动道具栏", "茶壶-识别交替过渡"}
        for desired in (False, True):
            runner = self.recognition_runner(hits, desired=desired)
            with self.subTest(desired=desired):
                self.assertTrue(runner.run())
                self.assertEqual(runner._capture.call_count, t.ABSENT_STABLE_FRAMES)
                self.assertEqual(sum(call.args[0] == "茶壶-识别无自动道具栏"
                                     for call in runner._hit.call_args_list), t.ABSENT_STABLE_FRAMES)
                runner.context.run_task.assert_called_once_with(t.NOTICE_NODE)
                self.assertIn("本关无法使用茶壶", runner.context.override_pipeline.call_args.args[0]
                              [t.NOTICE_NODE]["focus"]["Node.Recognition.Starting"])
                runner.context.get_node_data.assert_called_with(t.CONFIG_NODE)

    def test_transition_before_third_sample_interrupts_absence_evidence(self):
        hits = {"茶壶-识别编队页", "茶壶-识别交替过渡"}
        runner = self.recognition_runner(hits)
        runner._hit.side_effect = lambda name, _: (
            name in hits or name == "茶壶-识别无自动道具栏" and runner._capture.call_count < 3)
        self.assertFalse(runner.run())
        self.assertEqual(runner._capture.call_count, 8)
        self.assertIn("校验失败", runner.context.override_pipeline.call_args.args[0]
                      [t.NOTICE_NODE]["focus"]["Node.Recognition.Starting"])

    def test_two_absent_samples_then_uncertain_frames_cannot_confirm_empty_bar(self):
        for kind in ("unknown", "transition", "storm", "entry", "popup"):
            with self.subTest(kind=kind):
                runner = ScriptedRunner([t.TeapotFrame("absent")] * 2 + [t.TeapotFrame(kind)]
                                        + [t.TeapotFrame("unknown")] * 5)
                self.assertFalse(runner.run())
                self.assertEqual(runner.observation_count, 8)
                self.assertEqual(runner.actions, [])

    def test_one_clear_state_after_two_empty_frames_takes_precedence(self):
        for kind in ("bar", "popup"):
            for desired in (False, True):
                with self.subTest(kind=kind, desired=desired):
                    frame = t.TeapotFrame(kind, desired)
                    runner = ScriptedRunner([t.TeapotFrame("absent")] * 2 + [frame])
                    self.assertEqual(runner._initial(), frame)
                    self.assertEqual(runner.observation_count, 3)

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

    def test_reported_log_sequences_pass_at_first_clear_off_reading(self):
        # 2026-11-15 exported logs: 11:30, 12:44 and 18:32 respectively.
        # The old two-hit requirement rejected all three observed sequences.
        cases = [
            (["absent", "storm", "off", "absent", "storm", "off", "storm", "storm"], 3),
            (["off", "absent", "storm", "absent", "off", "absent", "storm", "off"], 1),
            (["storm", "off", "absent", "storm", "entry", "off", "storm", "storm"], 2),
        ]
        for kinds, first_hit in cases:
            with self.subTest(kinds=kinds):
                frames = [t.TeapotFrame("bar", False) if kind == "off" else t.TeapotFrame(kind)
                          for kind in kinds]
                runner = ScriptedRunner(frames)
                self.assertTrue(runner.run())
                self.assertEqual(runner.observation_count, first_hit)
                self.assertEqual(runner.actions, [])
                runner.context.run_task.assert_not_called()

    def test_uncertain_frames_alone_never_prove_state_or_absence(self):
        for kind in ("transition", "storm", "entry", "unknown", "popup"):
            with self.subTest(kind=kind):
                runner = ScriptedRunner([t.TeapotFrame(kind)] * t.OBSERVATION_ROUNDS)
                self.assertFalse(runner.run())
                self.assertEqual(runner.observation_count, t.OBSERVATION_ROUNDS)
                self.assertEqual(runner.actions, [])

    def test_absence_requires_confirmed_formation_and_no_known_entry(self):
        runner = t.TeapotRunner(context())
        runner._capture = Mock(return_value=object())
        runner._hit = Mock(side_effect=lambda name, _: name == "茶壶-识别编队页")
        self.assertEqual(runner.observe(), t.TeapotFrame("absent"))
        runner._hit = Mock(side_effect=lambda name, _: name in {"茶壶-识别编队页", "茶壶-识别任意入口"})
        self.assertEqual(runner.observe(), t.TeapotFrame("entry"))

    def test_one_matching_outer_bar_passes_without_any_further_observation(self):
        for desired in (False, True):
            with self.subTest(desired=desired):
                runner = ScriptedRunner([t.TeapotFrame("bar", desired)], desired=desired)
                runner.observe = Mock(side_effect=[t.TeapotFrame("bar", desired),
                                                   AssertionError("must not request a second hit")])
                self.assertTrue(runner.run())
                runner.observe.assert_called_once()
                self.assertEqual(runner.actions, [])
                runner.context.run_task.assert_not_called()

    def test_one_clear_state_on_last_observation_is_sufficient(self):
        for desired in (False, True):
            with self.subTest(desired=desired):
                frames = [t.TeapotFrame(kind) for kind in
                          ("unknown", "entry", "storm", "transition", "absent", "entry", "storm")]
                runner = ScriptedRunner(frames + [t.TeapotFrame("bar", desired)], desired=desired)
                self.assertTrue(runner.run())
                self.assertEqual(runner.observation_count, t.OBSERVATION_ROUNDS)
                self.assertEqual(runner.actions, [])

    def test_only_storm_frames_never_prove_teapot_state_or_absence(self):
        runner = ScriptedRunner([t.TeapotFrame("storm")] * t.OBSERVATION_ROUNDS)
        self.assertFalse(runner.run())
        self.assertEqual(runner.actions, [])
        message = runner.context.override_pipeline.call_args.args[0][t.NOTICE_NODE]["focus"]["Node.Recognition.Starting"]
        self.assertIn("道具栏", message)

    def test_direct_switch_is_clicked_once_and_tolerates_old_cached_frames(self):
        for desired in (False, True):
            with self.subTest(desired=desired):
                old, new = t.TeapotFrame("bar", not desired), t.TeapotFrame("bar", desired)
                runner = ScriptedRunner([old] * 4 + [new] * 2, desired=desired)
                self.assertTrue(runner.run())
                self.assertEqual(runner.actions, ["茶壶-直接设为开启" if desired else "茶壶-直接设为关闭"])

    def test_direct_switch_never_reclicks_if_verification_fails(self):
        runner = ScriptedRunner([t.TeapotFrame("bar", False)], desired=True)
        self.assertFalse(runner.run())
        self.assertEqual(runner.actions, ["茶壶-直接设为开启"])
        self.assertEqual(runner.observation_count, 1 + t.OBSERVATION_ROUNDS)

    def test_direct_verification_accepts_target_bar_readings_across_storm(self):
        frames = ([t.TeapotFrame("bar", False)] * 3 + [t.TeapotFrame("storm"),
                  t.TeapotFrame("bar", True), t.TeapotFrame("storm"), t.TeapotFrame("bar", True)])
        runner = ScriptedRunner(frames, desired=True)
        self.assertTrue(runner.run())
        self.assertEqual(runner.actions, ["茶壶-直接设为开启"])

    def test_switch_requires_a_new_target_state_after_click(self):
        for desired in (False, True):
            for kind, enabled in (("entry", None), ("unknown", None), ("storm", None),
                                  ("transition", None), ("absent", None), ("popup", None),
                                  ("bar", not desired)):
                with self.subTest(desired=desired, kind=kind):
                    frames = [t.TeapotFrame("bar", not desired)] + [t.TeapotFrame(kind, enabled)] * 8
                    runner = ScriptedRunner(frames, desired=desired)
                    self.assertFalse(runner.run())
                    self.assertEqual(runner.observation_count, 1 + t.OBSERVATION_ROUNDS)
                    self.assertEqual(runner.actions,
                                     ["茶壶-直接设为开启" if desired else "茶壶-直接设为关闭"])

    def test_direct_switch_accepts_one_new_target_state_after_waiting(self):
        for desired in (False, True):
            with self.subTest(desired=desired):
                frames = ([t.TeapotFrame("bar", not desired)]
                          + [t.TeapotFrame(kind) for kind in
                             ("entry", "storm", "transition", "absent", "unknown", "entry", "storm")]
                          + [t.TeapotFrame("bar", desired)])
                runner = ScriptedRunner(frames, desired=desired)
                self.assertTrue(runner.run())
                self.assertEqual(runner.observation_count, 1 + t.OBSERVATION_ROUNDS)
                self.assertEqual(runner.actions, ["茶壶-直接设为开启" if desired else "茶壶-直接设为关闭"])

    def test_popup_close_waits_for_one_new_matching_outer_state(self):
        for desired in (False, True):
            with self.subTest(desired=desired):
                frames = ([t.TeapotFrame("popup", desired)]
                          + [t.TeapotFrame(kind) for kind in ("unknown", "entry", "storm", "transition", "absent")]
                          + [t.TeapotFrame("bar", desired)])
                runner = ScriptedRunner(frames, desired=desired)
                self.assertTrue(runner.run())
                self.assertEqual(runner.observation_count, 7)
                self.assertEqual(runner.actions, ["茶壶-关闭弹窗"])

    def test_popup_toggle_must_verify_target_before_closing(self):
        for desired in (False, True):
            for kind, enabled in (("popup", not desired), ("popup", None), ("bar", desired),
                                  ("entry", None), ("storm", None), ("absent", None)):
                with self.subTest(desired=desired, kind=kind, enabled=enabled):
                    frames = [t.TeapotFrame("popup", not desired)] + [t.TeapotFrame(kind, enabled)] * 8
                    runner = ScriptedRunner(frames, desired=desired)
                    self.assertFalse(runner.run())
                    self.assertEqual(runner.observation_count, 1 + t.OBSERVATION_ROUNDS)
                    self.assertEqual(runner.actions,
                                     ["茶壶-弹窗设为开启" if desired else "茶壶-弹窗设为关闭"])

    def test_outer_bar_click_response_popup_changes_only_teapot_then_returns(self):
        for desired in (False, True):
            with self.subTest(desired=desired):
                frames = [t.TeapotFrame("bar", not desired), t.TeapotFrame("entry"),
                          t.TeapotFrame("popup", not desired), t.TeapotFrame("popup", not desired),
                          t.TeapotFrame("popup", desired), t.TeapotFrame("popup", desired),
                          t.TeapotFrame("entry"), t.TeapotFrame("bar", desired)]
                runner = ScriptedRunner(frames, desired=desired)
                self.assertTrue(runner.run())
                self.assertEqual(runner.observation_count, 8)
                self.assertEqual(runner.actions,
                                 ["茶壶-直接设为开启" if desired else "茶壶-直接设为关闭",
                                  "茶壶-弹窗设为开启" if desired else "茶壶-弹窗设为关闭", "茶壶-关闭弹窗"])
                self.assertFalse(any("风暴" in action for action in runner.actions))

    def test_matching_bar_stops_without_sampling_a_hypothetical_popup(self):
        frames = [t.TeapotFrame("bar", False)] + [t.TeapotFrame("popup", False)]
        runner = ScriptedRunner(frames)
        self.assertTrue(runner.run())
        self.assertEqual(runner.actions, [])
        self.assertEqual(runner.observation_count, 1)

    def test_opened_popup_already_at_target_does_not_get_toggled_again(self):
        frames = ([t.TeapotFrame("bar", False)] + [t.TeapotFrame("popup", True)] +
                  [t.TeapotFrame("bar", True)])
        runner = ScriptedRunner(frames, desired=True)
        self.assertTrue(runner.run())
        self.assertEqual(runner.actions, ["茶壶-直接设为开启", "茶壶-关闭弹窗"])

    def test_popup_response_with_unreadable_state_does_not_toggle_or_close(self):
        frames = [t.TeapotFrame("bar", False)] + [t.TeapotFrame("popup")]
        runner = ScriptedRunner(frames, desired=True)
        self.assertFalse(runner.run())
        self.assertEqual(runner.actions, ["茶壶-直接设为开启"])

    def test_initial_popup_is_supported_without_clicking_entrance_again(self):
        frames = ([t.TeapotFrame("popup", True)] + [t.TeapotFrame("popup", False)] +
                  [t.TeapotFrame("bar", False)])
        runner = ScriptedRunner(frames)
        self.assertTrue(runner.run())
        self.assertEqual(runner.actions, ["茶壶-弹窗设为关闭", "茶壶-关闭弹窗"])

    def test_correct_initial_popup_is_only_closed(self):
        runner = ScriptedRunner([t.TeapotFrame("popup", False)] +
                                [t.TeapotFrame("bar", False)])
        self.assertTrue(runner.run())
        self.assertEqual(runner.observation_count, 2)
        self.assertEqual(runner.actions, ["茶壶-关闭弹窗"])

    def test_popup_return_verification_waits_through_storm_for_one_target_reading(self):
        frames = [t.TeapotFrame("popup", False), t.TeapotFrame("storm"),
                  t.TeapotFrame("transition"), t.TeapotFrame("bar", False)]
        runner = ScriptedRunner(frames)
        self.assertTrue(runner.run())
        self.assertEqual(runner.observation_count, 4)
        self.assertEqual(runner.actions, ["茶壶-关闭弹窗"])

    def test_popup_read_or_toggle_failure_preserves_popup(self):
        for frames, desired, expected in (
            ([t.TeapotFrame("popup")], False, []),
            ([t.TeapotFrame("popup", False)], True, ["茶壶-弹窗设为开启"]),
        ):
            with self.subTest(frames=frames):
                runner = ScriptedRunner(frames, desired=desired)
                self.assertFalse(runner.run())
                self.assertEqual(runner.actions, expected)
                self.assertNotIn("茶壶-关闭弹窗", runner.actions)

    def test_unexpected_page_after_close_fails_without_recovery(self):
        frames = [t.TeapotFrame("popup", False)] + [t.TeapotFrame("unknown")]
        runner = ScriptedRunner(frames)
        self.assertFalse(runner.run())
        self.assertEqual(runner.actions, ["茶壶-关闭弹窗"])

    def test_returned_bar_must_match_the_verified_popup_setting(self):
        for desired in (False, True):
            for frame in (t.TeapotFrame("bar", not desired), t.TeapotFrame("absent"),
                          t.TeapotFrame("popup", desired)):
                with self.subTest(desired=desired, frame=frame):
                    frames = [t.TeapotFrame("popup", desired)] + [frame] * t.OBSERVATION_ROUNDS
                    runner = ScriptedRunner(frames, desired=desired)
                    self.assertFalse(runner.run())
                    self.assertEqual(runner.observation_count, 1 + t.OBSERVATION_ROUNDS)
                    self.assertEqual(runner.actions, ["茶壶-关闭弹窗"])

    def test_opening_action_failure_does_not_attempt_any_further_ui_input(self):
        runner = ScriptedRunner([t.TeapotFrame("bar", False)], desired=True,
                                failed_action="茶壶-直接设为开启")
        self.assertFalse(runner.run())
        self.assertEqual(runner.actions, ["茶壶-直接设为开启"])

    def test_three_clear_absent_samples_notify_and_continue_for_both_settings(self):
        for desired in (False, True):
            with self.subTest(desired=desired):
                runner = ScriptedRunner([t.TeapotFrame("absent")] * 8, desired=desired)
                self.assertTrue(runner.run())
                self.assertEqual(runner.observation_count, 3)
                self.assertEqual(runner.actions, [])
                runner.context.run_task.assert_called_once_with(t.NOTICE_NODE)
                message = runner.context.override_pipeline.call_args.args[0][t.NOTICE_NODE]["focus"]["Node.Recognition.Starting"]
                self.assertIn("本关无法使用茶壶", message)

    def test_late_flashing_entry_cannot_be_misclassified_as_absent(self):
        runner = ScriptedRunner([t.TeapotFrame("absent")] * 2 + [t.TeapotFrame("entry")]
                                + [t.TeapotFrame("unknown")] * 5)
        self.assertFalse(runner.run())
        self.assertEqual(runner.actions, [])
        self.assertEqual(runner.observation_count, 8)

    def test_waits_through_storm_or_transition_frames_for_late_teapot_state(self):
        runner = ScriptedRunner([t.TeapotFrame("entry")] * 6 + [t.TeapotFrame("bar", False)])
        self.assertTrue(runner.run())
        self.assertEqual(runner.actions, [])
        self.assertEqual(runner.observation_count, 7)
        runner.context.override_pipeline.assert_not_called()

    def test_unknown_interruption_resets_absent_evidence(self):
        runner = ScriptedRunner(([t.TeapotFrame("absent")] * 2 + [t.TeapotFrame("unknown")]) * 2
                                + [t.TeapotFrame("absent")] * 2)
        self.assertFalse(runner.run())
        self.assertEqual(runner.actions, [])

    def test_empty_confirmation_recovers_from_a_transient_low_score(self):
        runner = ScriptedRunner([t.TeapotFrame("absent")] * 2 + [t.TeapotFrame("transition")]
                                + [t.TeapotFrame("absent")] * 3)
        self.assertTrue(runner.run())
        self.assertEqual(runner.observation_count, 6)
        self.assertEqual(runner.actions, [])

    def test_video_failure_sequence_now_confirms_without_waiting_for_all_eight(self):
        runner = ScriptedRunner([t.TeapotFrame("absent")] * 4 + [t.TeapotFrame("transition")]
                                + [t.TeapotFrame("absent")] * 3)
        self.assertTrue(runner.run())
        self.assertEqual(runner.observation_count, 3)
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
        runner.frames = deque([stopped_frame])
        self.assertFalse(runner.run())
        self.assertEqual(runner.actions, [])
        runner.context.override_pipeline.assert_not_called()

    def test_stop_during_opening_prevents_further_capture_or_close(self):
        runner = ScriptedRunner([t.TeapotFrame("bar", False)], desired=True)
        runner.action_callback = lambda _: setattr(runner.context.tasker, "stopping", True)
        self.assertFalse(runner.run())
        self.assertEqual(runner.actions, ["茶壶-直接设为开启"])
        self.assertEqual(runner.observation_count, 1)
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
