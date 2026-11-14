"""Shared empty-item-bar regression using real MaaFramework and TeaRunner.

Every page here is a synthetic composite of repository UI crops/templates.
These are not full live screenshots and prove neither quest entry nor victory.
The memory controller rejects every input; no physical device is connected.
"""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import maa
import numpy as np
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.define import LoggingLevelEnum
from maa.library import Library
from maa.resource import Resource
from maa.tasker import Tasker

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tests/component"), str(ROOT / "tests/unit"),
               str(ROOT / "agent/custom")]
from test_formation_framework import MemoryController
import test_teapot_vision as vision
with patch.object(AgentServer, "custom_action", lambda *args: lambda cls: cls):
    import teapot_action as tea


EMPTY_NODE = "茶壶-识别无自动道具栏"


class CapturedSequenceRunner(tea.TeapotRunner):
    """Only choose the memory screenshots; all capture/recognition stays real."""

    def __init__(self, context, controller, frames):
        super().__init__(context)
        self.memory_controller = controller
        self.frames = frames
        self.observations = 0
        self.observed_kinds = []
        self.tasks = []

    def _capture(self):
        index = self.observations
        self.observations += 1
        if index >= len(self.frames) or self.frames[index] is None:
            # A missing confirmation frame is a real failed screencap, not a
            # manufactured "absent" or a repeat of the previous valid frame.
            self.memory_controller.fail_capture = True
        else:
            self.memory_controller.fail_capture = False
            image = self.frames[index].copy()
            image[1, 0] = (self.observations, 0, 0)
            self.memory_controller.image = image
        captured = super()._capture()
        if not self.memory_controller.fail_capture:
            assert captured[1, 0, 0] == self.observations, "capture must be the current requested frame"
        return captured

    def observe(self):
        frame = super().observe()
        self.observed_kinds.append(frame.kind)
        return frame

    def _run_task(self, name):
        self.tasks.append(name)
        return super()._run_task(name)


class SharedEmptyItemBarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Library.open(Path(maa.__file__).parent / "bin")
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)
        cls.fixture = json.loads((vision.FIXTURE_ROOT / "fixtures.json").read_text("utf-8"))

    def part(self, image, name):
        part = self.fixture["parts"][name]
        x, y, width, height = part["roi"]
        crop = vision.read_image(vision.FIXTURE_ROOT / part["image"])
        self.assertEqual(crop.shape[:2], (height, width))
        image[y:y+height, x:x+width] = crop

    def page(self, *, header=True, info=True, empty=True, item=None):
        image = np.zeros((720, 1280, 3), np.uint8)
        if header:
            self.part(image, "header_jp")
            for relative, x, y in (("battle/开始任务.png", 1097, 653),
                                   ("battle/配置变更.png", 190, 642)):
                crop = vision.read_image(vision.IMAGE_ROOT / relative)
                height, width = crop.shape[:2]
                image[y:y+height, x:x+width] = crop
        if info:
            self.part(image, "bar_transition")
        if empty:
            # Independent real frame crop; not the production template itself.
            self.part(image, "trial_empty_auto_items")
        if item:
            self.part(image, item)
        return image

    def run_in_context(self, callback):
        resource = Resource()
        resource.use_cpu()
        self.assertTrue(resource.post_bundle(ROOT / "assets/resource/base").wait().succeeded)
        observed = {}
        controller = MemoryController(self.page())
        self.assertTrue(controller.post_connection().wait().succeeded)
        test = self

        class Probe(CustomAction):
            def run(self, context, argv):
                try:
                    # Only accelerate waiting; recognition and state-machine
                    # nodes retain their actual production definitions.
                    test.assertTrue(context.override_pipeline({
                        tea.WAIT_NODE: {"pre_delay": 0, "post_delay": 1, "timeout": 250},
                        tea.NOTICE_NODE: {"pre_delay": 0, "post_delay": 0, "timeout": 250},
                    }))
                    callback(context, controller)
                except BaseException as exc:
                    observed["error"] = exc
                return True

        self.assertTrue(resource.register_custom_action("shared_empty_items_probe", Probe()))
        self.assertTrue(resource.override_pipeline({"shared_empty_items_probe": {
            "action": {"type": "Custom", "param": {"custom_action": "shared_empty_items_probe"}},
            "pre_delay": 0, "post_delay": 0, "next": [], "on_error": [],
        }}))
        tasker = Tasker()
        self.assertTrue(tasker.bind(resource, controller))
        try:
            with patch.object(tea.mfaalog, "info"), patch.object(tea.mfaalog, "warning"), \
                 patch.object(tea.mfaalog, "error"):
                self.assertTrue(tasker.post_task("shared_empty_items_probe").wait().succeeded)
            if "error" in observed:
                raise observed["error"]
            self.assertEqual(controller.inputs, 0)
        finally:
            tasker.post_stop().wait()

    def test_generic_context_both_settings_require_three_frames_and_no_input(self):
        def check(context, controller):
            for desired in (False, True):
                self.assertTrue(context.override_pipeline({tea.CONFIG_NODE: {
                    "attach": {"use_teapot": desired}}}))
                # This context does not load a task profile, imported source,
                # Trial flag, or battle strategy to permit empty-bar handling.
                config = context.get_node_data(tea.CONFIG_NODE)["attach"]
                self.assertEqual(config, {"use_teapot": desired})
                image = self.page()
                self.assertTrue(context.run_recognition(EMPTY_NODE, image).hit)
                runner = CapturedSequenceRunner(context, controller, [image] * tea.ABSENT_STABLE_FRAMES)
                self.assertTrue(runner.run())
                self.assertEqual(runner.observations, tea.ABSENT_STABLE_FRAMES)
                self.assertEqual(runner.observed_kinds, ["absent"] * tea.ABSENT_STABLE_FRAMES)
                self.assertEqual(runner.tasks, [tea.WAIT_NODE] * tea.ABSENT_STABLE_FRAMES)
                message = context.get_node_data(tea.NOTICE_NODE)["focus"]["Node.Recognition.Starting"]
                self.assertIn("本关无法使用茶壶", message)
        self.run_in_context(check)

    def test_header_and_both_info_anchors_are_required_for_positive_empty_proof(self):
        def check(context, controller):
            frames = [self.page(header=False), self.page(info=False),
                      self.page(empty=False), np.zeros((720, 1280, 3), np.uint8)]
            expected_kinds = ["unknown", "absent", "transition", "unknown"]
            for image, expected_kind in zip(frames, expected_kinds):
                self.assertFalse(context.run_recognition(EMPTY_NODE, image).hit)
                # With no info at all, the older no-entry absence path remains
                # valid. It must not be confused with the new positive node.
                runner = CapturedSequenceRunner(context, controller, [image])
                self.assertEqual(runner.observe().kind, expected_kind)
            runner = CapturedSequenceRunner(context, controller, [self.page(empty=False)] * 8)
            self.assertFalse(runner.run())
            self.assertEqual(runner.observed_kinds, ["transition"] * 8)
        self.run_in_context(check)

    def test_real_item_crops_and_storm_icon_without_text_are_not_empty(self):
        def check(context, controller):
            cases = [(self.page(item="bar_tea_on"), tea.TeapotFrame("bar", True)),
                     (self.page(item="bar_tea_off"), tea.TeapotFrame("bar", False)),
                     (self.page(item="bar_storm_on"), tea.TeapotFrame("storm"))]
            storm_only = self.page()
            icon = vision.read_image(vision.IMAGE_ROOT / "battle/teapot/bar_storm_icon.png")
            height, width = icon.shape[:2]
            storm_only[655:655+height, 770:770+width] = icon
            cases.append((storm_only, tea.TeapotFrame("storm")))
            for image, expected in cases:
                self.assertFalse(context.run_recognition(EMPTY_NODE, image).hit)
                runner = CapturedSequenceRunner(context, controller, [image])
                self.assertEqual(runner.observe(), expected)
        self.run_in_context(check)

    def test_two_frames_are_insufficient_if_followed_by_unknown_storm_or_transition(self):
        def check(context, controller):
            empty = self.page()
            for third in (self.page(header=False), self.page(empty=False), self.page(item="bar_storm_on")):
                runner = CapturedSequenceRunner(context, controller, [empty] * 2 + [third] * 6)
                self.assertFalse(runner.run())
                self.assertEqual(runner.observations, 8)
                self.assertEqual(runner.observed_kinds[:2], ["absent"] * 2)
                self.assertNotEqual(runner.observed_kinds[-1], "absent")
                self.assertEqual(runner.tasks, [tea.WAIT_NODE] * 8)
        self.run_in_context(check)

    def test_missing_third_capture_never_reuses_old_absent_as_confirmation(self):
        def check(context, controller):
            runner = CapturedSequenceRunner(context, controller, [self.page()] * 2 + [None])
            self.assertFalse(runner.run())
            self.assertEqual(runner.observations, 3)
            self.assertEqual(runner.observed_kinds, ["absent"] * 2)
        self.run_in_context(check)

    def test_transient_frame_restarts_three_frame_confirmation_within_budget(self):
        def check(context, controller):
            empty = self.page()
            runner = CapturedSequenceRunner(context, controller,
                [empty] * 2 + [self.page(empty=False)] + [empty] * 3)
            self.assertTrue(runner.run())
            self.assertEqual(runner.observed_kinds, ["absent", "absent", "transition"] + ["absent"] * 3)
            self.assertEqual(controller.inputs, 0)
        self.run_in_context(check)

    def test_live_carousel_sequence_preserves_real_bar_across_empty_fade(self):
        def check(context, controller):
            for desired, crop in ((False, "bar_tea_off"), (True, "bar_tea_on")):
                self.assertTrue(context.override_pipeline({tea.CONFIG_NODE:{"attach":{"use_teapot":desired}}}))
                bar = self.page(item=crop)
                empty = self.page()
                storm = self.page(item="bar_storm_on")
                transition = self.page(empty=False)
                runner = CapturedSequenceRunner(context, controller,
                    [storm, transition, bar, empty, storm, transition, bar, transition])
                self.assertTrue(runner.run())
                self.assertEqual(runner.observations, 7)
                self.assertEqual(runner.observed_kinds,
                    ["storm", "transition", "bar", "absent", "storm", "transition", "bar"])
                self.assertEqual(runner.tasks, [tea.WAIT_NODE] * 7)
        self.run_in_context(check)

    def test_single_real_bar_then_empty_strip_never_verifies_or_skips_as_absent(self):
        def check(context, controller):
            for desired, crop in ((False, "bar_tea_off"), (True, "bar_tea_on")):
                self.assertTrue(context.override_pipeline({tea.CONFIG_NODE:{"attach":{"use_teapot":desired}}}))
                runner = CapturedSequenceRunner(context, controller,
                    [self.page(item=crop)] + [self.page()] * 7)
                self.assertFalse(runner.run())
                self.assertEqual(runner.observations, tea.OBSERVATION_ROUNDS)
                self.assertEqual(runner.observed_kinds, ["bar"] + ["absent"] * 7)
                self.assertEqual(runner.tasks, [tea.WAIT_NODE] * tea.OBSERVATION_ROUNDS)
                message = context.get_node_data(tea.NOTICE_NODE)["focus"]["Node.Recognition.Starting"]
                self.assertNotIn("本关无法使用茶壶", message)
        self.run_in_context(check)


if __name__ == "__main__":
    unittest.main()
