"""Actual MaaFramework routes synthetic skill menus; no device or real input."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

import maa
import numpy as np
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition
from maa.define import LoggingLevelEnum
from maa.library import Library
from maa.resource import Resource
from maa.tasker import Tasker

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom"), str(Path(__file__).resolve().parent)]
from battle.core.special_skills import SPECIAL_SKILL_GROUPS, SELECT, TD, SpecialSkillChoiceError, SpecialSkillChoices
from battle.runtime.special_skills import ENTRY, UNKNOWN_MENU, SpecialSkillDispatcher
from test_formation_framework import MemoryController


PIPELINE = json.loads((ROOT / "assets/resource/base/pipeline/自动战斗_特殊技能.json").read_text("utf-8"))


class SpecialChoicesFrameworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Library.open(Path(os.environ.get("MAAFW_BINARY_PATH", Path(maa.__file__).parent / "bin")))
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)

    def run_case(self, check):
        state = {"group": None, "unavailable": set(), "clicks": [], "target": False, "follow_target": False}
        errors = []

        class Screen(CustomRecognition):
            def analyze(self, context, argv):
                name = argv.node_name
                group = state["group"]
                hit = bool(group and name in (group.root, *group.options) and name not in state["unavailable"])
                if name == "战斗_技能目标子屏":
                    hit = state["target"]
                if name == UNKNOWN_MENU:
                    hit = state.get("unknown_menu", False)
                return (0, 0, 10, 10) if hit else None

        class Click(CustomAction):
            def run(self, context, argv):
                name = argv.node_name
                group = state["group"]
                if group and name in group.options:
                    state["clicks"].append(name)
                    state["group"] = None
                    state["target"] = state["follow_target"]
                elif name.startswith("战斗_技能目标子屏_选从者"):
                    state["clicks"].append(name)
                    state["target"] = False
                return True

        class FastContext:
            def __init__(self, context):
                self.context = context

            def get_node_data(self, name):
                return self.context.get_node_data(name)

            def run_task(self, name, pipeline_override):
                override = copy.deepcopy(pipeline_override)
                for node in override.values():
                    node.update(timeout=80, rate_limit=1, pre_delay=0, post_delay=0)
                return self.context.run_task(name, pipeline_override=override)

        class Dispatch(CustomAction):
            def run(self, context, argv):
                try:
                    check(FastContext(context), state)
                    return True
                except Exception as exc:
                    errors.append(repr(exc))
                    return False

        nodes = copy.deepcopy(PIPELINE)
        for node in nodes.values():
            node.pop("post_wait_freezes", None)
            node.update(timeout=80, rate_limit=1, pre_delay=0, post_delay=0, on_error=[])
            if node.get("recognition", {}).get("type", "DirectHit") != "DirectHit":
                node["recognition"] = {"type": "Custom", "param": {"custom_recognition": "choice_screen"}}
            node["action"] = {"type": "Custom", "param": {"custom_action": "choice_click"}}
        nodes["root"] = {"action": {"type": "Custom", "param": {"custom_action": "choice_dispatch"}}}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "pipeline"
            path.mkdir()
            (path / "test.json").write_text(json.dumps(nodes, ensure_ascii=False), encoding="utf-8")
            resource = Resource()
            resource.use_cpu()
            self.assertTrue(resource.post_bundle(temp).wait().succeeded)
            resource.register_custom_recognition("choice_screen", Screen())
            resource.register_custom_action("choice_click", Click())
            resource.register_custom_action("choice_dispatch", Dispatch())
            controller = MemoryController(np.zeros((720, 1280, 3), dtype=np.uint8))
            self.assertTrue(controller.post_connection().wait().succeeded)
            tasker = Tasker()
            self.assertTrue(tasker.bind(resource, controller))
            try:
                job = tasker.post_task("root")
                deadline = time.monotonic() + 30
                while not job.status.done and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(job.status.done, "special choice dispatch did not terminate")
                self.assertTrue(job.status.succeeded, str(errors))
                self.assertEqual(controller.inputs, 0)
            finally:
                tasker.post_stop().wait()

    @staticmethod
    def choices(group, values):
        return SpecialSkillChoices(tuple(values)) if group.field == TD else SpecialSkillChoices((), tuple(values))

    def test_every_registered_option_and_following_ally_target(self):
        def check(context, state):
            original = context.get_node_data(ENTRY)
            for group in SPECIAL_SKILL_GROUPS:
                for index, option in enumerate(group.options):
                    state.update(group=group, clicks=[], target=False, follow_target=group.field != TD)
                    dispatcher = SpecialSkillDispatcher(self.choices(group, [index + group.first_value]))
                    target_override = {f"战斗_技能目标子屏_选从者{slot}": {"enabled": slot == 2} for slot in (1, 2, 3)}
                    dispatcher.run(context, target_override, servant_id=group.servant_ids[0], skill_index=group.skills[0])
                    expected = [option] + (["战斗_技能目标子屏_选从者2"] if group.field != TD else [])
                    self.assertEqual(state["clicks"], expected, group.root)
                    self.assertEqual(dispatcher.positions[group.field], 1)
                    self.assertEqual(context.get_node_data(ENTRY), original)
        self.run_case(check)

    def test_separate_queues_repeat_across_servants_without_affecting_ordinary_skills(self):
        def check(context, state):
            dispatcher = SpecialSkillDispatcher(SpecialSkillChoices((2, 3), (1, 0)))
            groups = [SPECIAL_SKILL_GROUPS[0], SPECIAL_SKILL_GROUPS[2], SPECIAL_SKILL_GROUPS[1], SPECIAL_SKILL_GROUPS[0]]
            expected = [groups[0].options[1], groups[1].options[1], groups[2].options[0], groups[3].options[2]]
            for group in groups:
                dispatcher.run(context, {}, servant_id="100100", skill_index=1)
                state["group"] = group
                dispatcher.run(context, {}, servant_id=group.servant_ids[0], skill_index=group.skills[0])
            self.assertEqual(state["clicks"], expected)
            self.assertEqual(dispatcher.positions, {TD: 2, SELECT: 2})
        self.run_case(check)

    def test_unavailable_choice_never_falls_back_or_advances_queue(self):
        def check(context, state):
            group = SPECIAL_SKILL_GROUPS[1]
            state.update(group=group, unavailable={group.options[1]})
            dispatcher = SpecialSkillDispatcher(self.choices(group, [1]))
            with self.assertRaises(SpecialSkillChoiceError):
                dispatcher.run(context, {}, servant_id=group.servant_ids[0], skill_index=1)
            self.assertEqual(state["clicks"], [])
            self.assertEqual(dispatcher.positions[SELECT], 0)
        self.run_case(check)

    def test_exhausted_or_out_of_range_choice_does_not_click_default(self):
        def check(context, state):
            group = SPECIAL_SKILL_GROUPS[2]
            for values, position in (([2], 0), ([0], 1)):
                state.update(group=group, clicks=[])
                dispatcher = SpecialSkillDispatcher(self.choices(group, values))
                dispatcher.positions[SELECT] = position
                with self.assertRaises(SpecialSkillChoiceError):
                    dispatcher.run(context, {}, servant_id=group.servant_ids[0], skill_index=1)
                self.assertEqual(state["clicks"], [])
                self.assertEqual(dispatcher.positions[SELECT], position)
        self.run_case(check)

    def test_missing_menu_does_not_click_a_target_or_space_ishtar_direct_option(self):
        def check(context, state):
            state["target"] = True
            dispatcher = SpecialSkillDispatcher(SpecialSkillChoices((2,)))
            with self.assertRaises(SpecialSkillChoiceError):
                dispatcher.run(context, {}, servant_id="1100900", skill_index=2)
            self.assertEqual(state["clicks"], [])
            self.assertEqual(dispatcher.positions[TD], 0)
        self.run_case(check)

    def test_unregistered_menu_stops_without_spending_a_known_menu_choice(self):
        def check(context, state):
            state["unknown_menu"] = True
            dispatcher = SpecialSkillDispatcher(SpecialSkillChoices((), (1,)))
            with self.assertRaisesRegex(SpecialSkillChoiceError, "未支持"):
                dispatcher.run(context, {}, servant_id="999999", skill_index=1)
            self.assertEqual(state["clicks"], [])
            self.assertEqual(dispatcher.positions[SELECT], 0)
        self.run_case(check)


if __name__ == "__main__":
    unittest.main()
