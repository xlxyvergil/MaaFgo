"""Exercise production click actions on synthetic menus; never connect to a device."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
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
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom"), str(Path(__file__).parent)]
from battle.core.special_skills import SPECIAL_SKILL_GROUPS, TD, SpecialSkillChoices
from battle.core.enums import Scene
from battle.core.policy import StrategyProfile
from battle.execution.executor import Executor
from battle.runtime.runtime import AutoBattleRuntime
from battle.runtime.special_skills import SpecialSkillDispatcher
from test_formation_framework import MemoryController

PIPELINE = json.loads((ROOT / "assets/resource/base/pipeline/自动战斗_特殊技能.json").read_text("utf-8"))
TARGET = "战斗_技能目标子屏"
NO_OVERLAY = "战斗_特殊技能_无覆盖层"


class AnimationAccelerationFrameworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Library.open(Path(os.environ.get("MAAFW_BINARY_PATH", Path(maa.__file__).parent / "bin")))
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)

    def run_case(self, *, group=None, index=0, follow_target=False, entry=None,
                 keep_delays=False, icon_only=False, swap_return=False):
        option = group.options[index] if group else None
        state = {"phase": "option" if group else "target" if entry == TARGET else "animation",
                 "events": [], "positions": None}
        errors = []

        class Screen(CustomRecognition):
            def analyze(self, context, argv):
                if argv.node_name == TARGET:
                    hit = state["phase"] == "target"
                else:
                    hit = bool(group and state["phase"] == "option"
                               and argv.node_name in (group.root, option))
                return (750, 400, 1, 1) if hit else None

        class Controller(MemoryController):
            def click(self, x, y):
                phase = state["phase"]
                state["events"].append(((x, y), phase, time.monotonic()))
                if phase == "option":
                    state["phase"] = "target" if follow_target else "animation"
                elif phase == "target":
                    if (x, y) != (633, 386):
                        errors.append(f"option acceleration misselected target at {(x, y)}")
                    state["phase"] = "animation"
                return True

        class Dispatch(CustomAction):
            def run(self, context, argv):
                try:
                    values = (index + group.first_value,)
                    choices = SpecialSkillChoices(values) if group.field == TD else SpecialSkillChoices((), values)
                    dispatcher = SpecialSkillDispatcher(choices)
                    override = {f"{TARGET}_选从者{slot}": {"enabled": slot == 2} for slot in (1, 2, 3)}
                    dispatcher.run(context, override, servant_id=group.servant_ids[0], skill_index=group.skills[0])
                    state["positions"] = dispatcher.positions
                    return True
                except Exception as exc:
                    errors.append(repr(exc))
                    return False

        class SwapOpening(CustomAction):
            def run(self, context, argv):
                runtime = AutoBattleRuntime(context, context.tasker.controller,
                                           SimpleNamespace(plan=None), StrategyProfile())
                runtime._wait_until = lambda *args, **kwargs: True
                return runtime._execute_skill_cast(
                    "cast_master_skill", lambda: True, None,
                    (Scene.ORDER_CHANGE, Scene.MAIN_BATTLE), 10)

        nodes = copy.deepcopy(PIPELINE)
        for node in nodes.values():
            node.update(pre_delay=0, timeout=100, rate_limit=1, on_error=[])
            if not keep_delays:
                node["post_delay"] = 0
                node["repeat_delay"] = 1
            if node.get("recognition", {}).get("type", "DirectHit") != "DirectHit":
                node["recognition"] = {"type": "Custom", "param": {"custom_recognition": "animation_screen"}}
        nodes["dispatch"] = {"action": {"type": "Custom", "param": {"custom_action": "animation_dispatch"}},
                             "pre_delay": 0, "post_delay": 0}
        nodes["swap_opening"] = {"action": {"type": "Custom", "param": {"custom_action": "swap_opening"}},
                                 "pre_delay": 0, "post_delay": 0}
        if entry == TARGET:
            for slot in (1, 3):
                nodes[f"{TARGET}_选从者{slot}"]["enabled"] = False
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "pipeline")
            path.mkdir()
            (path / "test.json").write_text(json.dumps(nodes, ensure_ascii=False), encoding="utf-8")
            resource = Resource()
            resource.use_cpu()
            self.assertTrue(resource.post_bundle(temp).wait().succeeded)
            resource.register_custom_recognition("animation_screen", Screen())
            resource.register_custom_action("animation_dispatch", Dispatch())
            resource.register_custom_action("swap_opening", SwapOpening())
            controller = Controller(np.zeros((720, 1280, 3), dtype=np.uint8))
            self.assertTrue(controller.post_connection().wait().succeeded)
            tasker = Tasker()
            self.assertTrue(tasker.bind(resource, controller))
            started = time.monotonic()
            try:
                if icon_only:
                    Executor(None, controller).cast_servant_skill(2, 3)
                else:
                    job = tasker.post_task("dispatch" if group else "swap_opening" if swap_return else entry)
                    deadline = time.monotonic() + 12
                    while not job.status.done and time.monotonic() < deadline:
                        time.sleep(.005)
                    self.assertTrue(job.status.done, "animation route timed out")
                    self.assertTrue(job.status.succeeded, str(errors))
                elapsed = time.monotonic() - started
                self.assertEqual(errors, [])
            finally:
                tasker.post_stop().wait()
        return state, elapsed

    def test_all_options_accelerate_only_after_the_last_selection_and_consume_once(self):
        for group in SPECIAL_SKILL_GROUPS:
            for index, option in enumerate(group.options):
                follows = TARGET in PIPELINE[option].get("next", [])
                for follow_target in ((False, True) if follows else (False,)):
                    with self.subTest(option=option, follow_target=follow_target):
                        state, _ = self.run_case(group=group, index=index, follow_target=follow_target)
                        events = state["events"]
                        self.assertEqual([event[1] for event in events],
                                         ["option", "target", "animation"] if follow_target
                                         else ["option", "animation"])
                        self.assertEqual(events[-1][0], events[-2][0])
                        self.assertEqual(state["positions"][group.field], 1)

    def test_final_ally_target_double_clicks_then_waits_one_second(self):
        state, elapsed = self.run_case(entry=TARGET, keep_delays=True)
        events = state["events"]
        self.assertEqual([(event[0], event[1]) for event in events],
                         [((633, 386), "target"), ((633, 386), "animation")])
        self.assertGreaterEqual(events[1][2] - events[0][2], .09)
        self.assertGreaterEqual(elapsed - (events[-1][2] - events[0][2]), .95)

    def test_intermediate_option_waits_600ms_without_clicking_the_wrong_target(self):
        group = next(group for group in SPECIAL_SKILL_GROUPS if "库库尔坎" in group.root)
        state, _ = self.run_case(group=group, index=1, follow_target=True, keep_delays=True)
        events = state["events"]
        self.assertEqual([event[1] for event in events], ["option", "target", "animation"])
        self.assertGreaterEqual(events[1][2] - events[0][2], .59)
        self.assertEqual(events[1][0], events[2][0])
        self.assertNotEqual(events[0][0], events[1][0])

    def test_no_overlay_taps_top_right_once_then_waits(self):
        state, elapsed = self.run_case(entry=NO_OVERLAY, keep_delays=True)
        self.assertEqual([(event[0], event[1]) for event in state["events"]],
                         [((1240, 20), "animation")])
        self.assertGreaterEqual(elapsed, .95)

    def test_servant_skill_icon_remains_a_single_click(self):
        state, _ = self.run_case(icon_only=True)
        self.assertEqual(len(state["events"]), 1)
        self.assertEqual(state["events"][0][0], (567, 583))

    def test_swap_opening_does_not_send_the_menu_dismissing_acceleration_tap(self):
        state, _ = self.run_case(swap_return=True)
        self.assertEqual(state["events"], [])


if __name__ == "__main__":
    unittest.main()
