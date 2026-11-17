"""连续出击沿用实际初始编队；不连接设备、不执行战斗。"""
from dataclasses import replace
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import os
import tempfile
import time

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
from formation_test_support import ROOT, battle_action as battle
from formation_test_support import repeat_action
from test_formation_session import FakeContext
from test_support_fallback import Context
from support_action import SupportAction
from battle.core.models import Confidence, FormationSlot, CONTINUOUS_FORMATION_SOURCE
from battle.runtime.formation_session import FormationSessions, sessions, session_override, CONTINUE_NODE
from battle.runtime.bond_stop_state import bond_stops


def initial_slots(support=3):
    return tuple(FormationSlot(i, "identified", "100100" if i in (1, support) else "200100",
                               is_support=i == support, identity_confidence=Confidence(.98, "fixture"),
                               consistent_frames=1) for i in range(1, 7))


def publish(store, root, token, slots=None):
    revision = store.begin_capture(root, token)
    slots = initial_slots() if slots is None else slots
    store.append_frame(root, token, revision, slots)
    return store.publish(root, token, revision, slots, "test-calibration")


class ContinuationSessionTests(unittest.TestCase):
    def setUp(self):
        self.store = FormationSessions()
        self.token = self.store.begin(1, repeat=True)

    def test_only_unique_support_slot_changes_including_reserve_and_duplicate_id(self):
        for support in (1, 3, 4, 6):
            with self.subTest(support=support):
                token = self.store.begin(1, repeat=True)
                original = publish(self.store, 1, token, initial_slots(support))
                self.assertTrue(self.store.prepare_continuation(1, token))
                self.assertIsNone(self.store.read(1, token)[0])
                self.store.battle_started(1, token, 2)
                result = self.store.support_selected(1, token, "405900",
                    confidence=Confidence(.94, "support_selection"), matched_template="f_selected.png")
                self.assertEqual(result.captured_battle, 2)
                self.assertEqual(result.source, CONTINUOUS_FORMATION_SOURCE)
                self.assertGreater(result.revision, original.revision)
                for before, after in zip(original.slots, result.slots):
                    if before.is_support:
                        self.assertEqual(after.servant_id, "405900")
                        self.assertEqual(after.identity_confidence.value, .94)
                        self.assertEqual(after.consistent_frames, 0)  # 不伪造编队页采样帧。
                        self.assertIsNone(after.class_id)
                    else:
                        self.assertIs(after, before)
                self.assertEqual(original.slots[support - 1].servant_id, "100100")

    def test_third_battle_replaces_support_again_and_preserves_unknown_and_empty(self):
        slots = list(initial_slots())
        slots[3] = FormationSlot(4, "unknown", is_support=False)
        slots[4] = FormationSlot(5, "empty", is_support=False)
        original = publish(self.store, 1, self.token, tuple(slots))
        for number, sid in ((2, "405900"), (3, "300100")):
            self.assertTrue(self.store.prepare_continuation(1, self.token))
            self.store.battle_started(1, self.token, number)
            result = self.store.support_selected(1, self.token, sid)
            self.assertEqual(result.slots[2].servant_id, sid)
            self.assertEqual(result.slots[3:], original.slots[3:])

    def test_no_marker_no_reuse_even_with_matching_support(self):
        publish(self.store, 1, self.token)
        self.store.battle_started(1, self.token, 2)
        self.assertIsNone(self.store.support_selected(1, self.token, "100100"))
        self.assertIsNone(self.store.read(1, self.token)[0])

    def test_zero_multiple_or_uncertain_support_markers_cannot_reuse(self):
        for flags in ((False,) * 6, (True, False, True, False, False, False),
                      (None, False, True, False, False, False)):
            with self.subTest(flags=flags):
                token = self.store.begin(1, repeat=True)
                publish(self.store, 1, token, tuple(replace(s, is_support=flag)
                    for s, flag in zip(initial_slots(), flags)))
                self.assertFalse(self.store.prepare_continuation(1, token))
                self.store.battle_started(1, token, 2)
                self.assertIsNone(self.store.support_selected(1, token, "100100"))

    def test_unknown_support_preserves_own_members_including_reserve_slot(self):
        from battle.perception.bond_result import result_formation_order

        for support in (1, 4, 6):
            with self.subTest(support=support):
                token = self.store.begin(1, repeat=True)
                original = publish(self.store, 1, token, initial_slots(support=support))
                for number in (2, 3):
                    self.assertTrue(self.store.prepare_continuation(1, token))
                    self.store.battle_started(1, token, number)
                    self.assertTrue(self.store.awaiting_continuous_support(1, token))
                    result = self.store.support_selected(1, token, None,
                        confidence=Confidence(.99, "stale"), matched_template="old.png")
                    self.assertEqual(result.source, CONTINUOUS_FORMATION_SOURCE)
                    self.assertEqual(result.captured_battle, number)
                    unknown = result.slots[support - 1]
                    self.assertEqual((unknown.status, unknown.servant_id, unknown.is_support),
                                     ("unknown", None, True))
                    self.assertEqual(unknown.identity_confidence.value, 0)
                    self.assertEqual(unknown.matched_template, "")
                    self.assertFalse(self.store.awaiting_continuous_support(1, token))
                    for before, after in zip(original.slots, result.slots):
                        if not before.is_support:
                            self.assertIs(after, before)
                    self.assertEqual(result_formation_order(result.slots),
                                     tuple(i for i in range(1, 7) if i != support))
                self.assertTrue(self.store.prepare_continuation(1, token))
                self.store.battle_started(1, token, 4)
                result = self.store.support_selected(1, token, "405900")
                self.assertEqual(result.slots[support - 1].servant_id, "405900")

    def test_single_battle_fallback_and_no_selection_do_not_reuse(self):
        for mode in ("single", "fallback", "no_selection"):
            with self.subTest(mode=mode):
                token = self.store.begin(1, repeat=mode != "single")
                publish(self.store, 1, token)
                retained = self.store.prepare_continuation(1, token)
                if mode == "single":
                    self.assertFalse(retained)
                    continue
                self.store.battle_started(1, token, 2)
                if mode == "fallback":
                    self.store.set_support_fallback(1, token, True)
                elif mode != "no_selection":
                    self.store.support_selected(1, token, None)
                self.assertIsNone(self.store.read(1, token)[0])

    def test_reenter_capture_or_edit_cannot_restore_old_continuation(self):
        for operation in ("capture", "edit", "skip", "invalidate"):
            with self.subTest(operation=operation):
                token = self.store.begin(1, repeat=True)
                publish(self.store, 1, token)
                self.store.prepare_continuation(1, token)
                self.store.battle_started(1, token, 2)
                if operation == "capture":
                    self.store.begin_capture(1, token)
                elif operation == "edit":
                    self.store.begin_update(1, token)
                elif operation == "skip":
                    self.store.skip_capture(1, token)
                else:
                    self.store.invalidate(1, token, "formation_changed")
                self.assertIsNone(self.store.support_selected(1, token, "100100"))
                if operation != "skip":
                    with self.assertRaises(ValueError):
                        self.store.read(1, token)

    def test_stale_selection_before_next_battle_and_after_stop_is_rejected(self):
        publish(self.store, 1, self.token)
        self.store.prepare_continuation(1, self.token)
        self.assertIsNone(self.store.support_selected(1, self.token, "100100"))
        self.store.finish(1, self.token)
        self.store.begin(1, repeat=True)
        with self.assertRaises(ValueError):
            self.store.support_selected(1, self.token, "100100")


class ContinuationActionTests(unittest.TestCase):
    def setUp(self):
        sessions.stop_owner("")
        self.addCleanup(sessions.stop_owner, "")
        for target in ("support_action.time.sleep", "support_action.mfaalog.info", "support_action.mfaalog.error"):
            p = patch(target); p.start(); self.addCleanup(p.stop)

    def test_ce_only_reserve_support_keeps_frontline_skills_across_battles(self):
        from battle.core.enums import Scene
        from battle.core.models import BattleState
        from battle.perception import perception
        from battle.perception.frontline import FrontlineIdentity

        ctx = Context(enabled=False)
        ctx.attach.update(class_name="any", servant="stale", np_level=5, level=120,
                          **{f"skill_active_{i}": 10 for i in range(1, 4)},
                          **{f"skill_passive_{i}": 10 for i in range(1, 6)})
        ids = ("100100", "200100", "300100", "400100", "500100", "600100")
        original = publish(sessions, ctx.root, ctx.token, tuple(
            replace(slot, servant_id=sid) for slot, sid in zip(initial_slots(4), ids)))
        action = SupportAction()
        for name, value in {
            "_get_detector": NS(detect=lambda image: [(0, 0, 100, 100, .99)]),
            "_init_scale": None, "_tap": None, "_shot_base": object(),
            "_ref_shift": (0, 0), "_match_ce": True,
            "_match_servant": False, "_check_active_view": False, "_match_passive": False,
        }.items():
            setattr(action, name, Mock(return_value=value))

        def visible(candidates, lineup):
            return tuple(FrontlineIdentity(slot, sid if sid in candidates else None,
                                           Confidence(.98, "fixture"))
                         for slot, sid in enumerate(lineup, 1))

        for number, support_id in ((2, None), (3, "405900"), (4, "403200")):
            with self.subTest(battle=number, support_id=support_id):
                self.assertTrue(sessions.prepare_continuation(ctx.root, ctx.token))
                sessions.battle_started(ctx.root, ctx.token, number)
                def identify(*args):
                    if support_id is not None:
                        action._matched_face_evidence = (.95, f"{support_id}.png")
                    return support_id
                action._identify_unrestricted_support = Mock(side_effect=identify)
                self.assertTrue(action.run(ctx, NS(node_name="助战action", custom_action_param="{}")).success)
                action._identify_unrestricted_support.assert_called_once()
                updated, _ = sessions.read(ctx.root, ctx.token)
                self.assertEqual(updated.slots[3].servant_id, support_id)
                for index in (0, 1, 2, 4, 5):
                    self.assertIs(updated.slots[index], original.slots[index])
                with patch.object(battle, "AutoBattleRuntime") as runtime:
                    runtime.return_value.run.return_value = NS(ok=True, reason="fixture", turns=1)
                    self.assertTrue(battle.AutoBattleAction().run(ctx, NS(custom_action_param="{}")).success)
                    candidates = runtime.call_args.kwargs["roster_servant_ids"]
                    decider = runtime.call_args.args[2]
                self.assertEqual(candidates, ids[:3] + (support_id,) + ids[4:])
                for lineup, expected_slots in ((ids[:3], (1, 2, 3)),
                                               ((support_id, ids[1], ids[2]),
                                                (1, 2, 3) if support_id else (2, 3))):
                    recognizer = NS(detect=lambda image, supplied: visible(supplied, lineup))
                    with patch.object(perception, "_reco", return_value=NS(hit=False)):
                        servants = perception._detect_servants(ctx, object(), candidates, recognizer=recognizer)
                    state = BattleState(Scene.MAIN_BATTLE, Confidence(1.0), (), (), (), servants=servants)
                    skills = decider.decide(state).servant_skills
                    self.assertEqual([(skill.servant_slot, skill.skill_index) for skill in skills],
                                     [(slot, index) for slot in expected_slots for index in (1, 2, 3)])
        action._match_servant.assert_not_called()
        action._check_active_view.assert_not_called()
        action._match_passive.assert_not_called()

    def test_actual_support_action_both_success_branches_update_runtime_roster(self):
        for passive, failed_click in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(passive=passive, failed_click=failed_click):
                ctx = Context(enabled=False)
                ctx.attach["servant"] = "405900"
                ctx.attach["skill_passive_1"] = 10 if passive else 0
                original = publish(sessions, ctx.root, ctx.token)
                sessions.prepare_continuation(ctx.root, ctx.token)
                sessions.battle_started(ctx.root, ctx.token, 2)
                action = SupportAction()
                for name, value in {
                    "_get_servant_map": {"405900": {"images": ["selected.png"]}},
                    "_get_detector": NS(detect=lambda image: [(0, 0, 100, 100, .99)]),
                    "_init_scale": None, "_tap": None, "_shot_base": object(),
                    "_ref_shift": (0, 0), "_match_ce": True, "_check_active_view": True,
                    "_match_passive": True,
                }.items():
                    setattr(action, name, Mock(return_value=value))
                def matched(*args):
                    action._matched_face_evidence = (.95, "selected.png")
                    return True
                action._match_servant = Mock(side_effect=matched)
                if failed_click:
                    action._tap.side_effect = lambda controller, x, y: False if (x, y) == (50, 50) else None
                result = action.run(ctx, NS(node_name="助战action", custom_action_param="{}"))
                self.assertEqual(result.success, not failed_click)
                if failed_click:
                    self.assertIsNone(sessions.read(ctx.root, ctx.token)[0])
                    continue
                updated, number = sessions.read(ctx.root, ctx.token)
                self.assertEqual(number, 2)
                self.assertEqual(updated.slots[2].servant_id, "405900")
                self.assertIs(updated.slots[0], original.slots[0])
                self.assertEqual(updated.slots[2].identity_confidence.value, .95)
                with patch.object(battle, "AutoBattleRuntime") as runtime:
                    runtime.return_value.run.return_value = NS(ok=True, reason="victory", turns=1)
                    self.assertTrue(battle.AutoBattleAction().run(ctx, NS(custom_action_param="{}")).success)
                    self.assertIs(runtime.call_args.kwargs["initial_formation"], updated)
                self.assertTrue(bond_stops.formation(ctx.root)[2]["is_support"])
                self.assertEqual(bond_stops.formation(ctx.root)[2]["reason"], "continuous_support_selected")

    def test_marker_is_only_installed_for_native_session_after_continue_click(self):
        nodes = json.loads((ROOT / "assets/resource/base/pipeline/战斗完成.json").read_text("utf-8"))
        self.assertNotIn(f"[JumpBack]{CONTINUE_NODE}", nodes["连续出击_继续"]["next"])
        ctx = FakeContext()
        ctx.nodes[CONTINUE_NODE] = {"action": {"type": "DoNothing"}}
        ctx.nodes["连续出击_继续"] = nodes["连续出击_继续"]
        self.assertNotIn("连续出击_继续", session_override("test"))
        self.assertEqual(session_override("test", ctx)["连续出击_继续"]["next"][0], f"[JumpBack]{CONTINUE_NODE}")
        token = sessions.begin(ctx.root, repeat=True)
        ctx.override_pipeline(session_override(token, ctx))
        ctx.override_pipeline(session_override(token, ctx))
        self.assertEqual(ctx.nodes["连续出击_继续"]["next"].count(f"[JumpBack]{CONTINUE_NODE}"), 1)
        publish(sessions, ctx.root, token)
        self.assertTrue(battle.RememberContinuousFormation().run(ctx, NS()).success)
        sessions.battle_started(ctx.root, token, 2)
        self.assertIsNotNone(sessions.support_selected(ctx.root, token, "405900"))

    def test_marker_does_not_clear_nonrepeat_snapshot(self):
        ctx = FakeContext()
        token = sessions.begin(ctx.root)
        ctx.override_pipeline(session_override(token))
        original = publish(sessions, ctx.root, token)
        self.assertTrue(battle.RememberContinuousFormation().run(ctx, NS()).success)
        self.assertIs(sessions.read(ctx.root, token)[0], original)

    def test_game_forced_formation_reset_discards_continued_roster(self):
        ctx = FakeContext()
        token = sessions.begin(ctx.root, repeat=True)
        ctx.override_pipeline(session_override(token))
        publish(sessions, ctx.root, token)
        sessions.prepare_continuation(ctx.root, token)
        sessions.battle_started(ctx.root, token, 2)
        sessions.support_selected(ctx.root, token, "405900")
        with patch.object(battle, "AutoBattleRuntime") as runtime:
            runtime.return_value.run.return_value = NS(ok=True, reason="victory", turns=1)
            self.assertTrue(battle.AutoBattleAction().run(ctx, NS(
                custom_action_param='{"native_battle_formation_reset":true}')).success)
            self.assertIsNone(runtime.call_args.kwargs["initial_formation"])
        self.assertIsNone(sessions.read(ctx.root, token)[0])


class ContinuationFrameworkTests(unittest.TestCase):
    def test_production_marker_runs_after_continue_in_cloned_context(self):
        import maa
        import numpy as np
        from maa.library import Library
        from maa.resource import Resource
        from maa.tasker import Tasker
        from maa.define import LoggingLevelEnum
        from maa.custom_action import CustomAction
        from test_formation_framework import MemoryController
        from battle.runtime.formation_session import context_session

        Library.open(Path(os.environ.get("MAAFW_BINARY_PATH", Path(maa.__file__).parent / "bin")))
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)
        sessions.stop_owner("")
        self.addCleanup(sessions.stop_owner, "")
        production = json.loads((ROOT / "assets/resource/base/pipeline/原生自动战斗.json").read_text("utf-8"))
        nodes = {CONTINUE_NODE: production[CONTINUE_NODE],
                 "连续出击_继续": {"action": {"type": "DoNothing"}, "next": ["全局空节点"]},
                 "全局-战前吃体力": {"enabled": False}, "全局空节点": {},
                 "编队身份-采集": {}, "进本-实际点击队伍确认": {},
                 "driver": {"action": {"type": "Custom", "param": {"custom_action": "driver"}}}}
        observed = []

        class Driver(CustomAction):
            def run(self, context, argv):
                root = context.get_task_job().job_id
                token = sessions.begin(root, repeat=True)
                try:
                    if not context.override_pipeline(session_override(token, context)):
                        return False
                    original = publish(sessions, root, token)
                    for number, sid in ((2, "405900"), (3, "300100")):
                        context.clear_hit_count(CONTINUE_NODE)
                        detail = context.run_task("连续出击_继续")
                        if not detail or not detail.status.succeeded:
                            return False
                        sessions.battle_started(root, token, number)
                        snapshot = sessions.support_selected(root, token, sid)
                        observed.append((snapshot, original, context_session(context)))
                        if snapshot is None:
                            return False
                    return True
                finally:
                    sessions.finish(root, token)

        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "pipeline"
            folder.mkdir()
            (folder / "test.json").write_text(json.dumps(nodes, ensure_ascii=False), encoding="utf-8")
            resource = Resource()
            resource.use_cpu()
            self.assertTrue(resource.post_bundle(temp).wait().succeeded)
            resource.register_custom_action("driver", Driver())
            resource.register_custom_action("remember_continuous_formation", battle.RememberContinuousFormation())
            controller = MemoryController(np.zeros((720, 1280, 3), dtype=np.uint8))
            self.assertTrue(controller.post_connection().wait().succeeded)
            tasker = Tasker()
            self.assertTrue(tasker.bind(resource, controller))
            try:
                job = tasker.post_task("driver")
                deadline = time.monotonic() + 5
                while not job.status.done and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(job.status.done, "continuous marker must have a bounded JumpBack")
                self.assertTrue(job.status.succeeded)
                self.assertEqual(len(observed), 2)
                self.assertIn(CONTINUE_NODE, repeat_action._DEFAULT_RESET_HIT_NODES)
                for (updated, original, (root, token)), sid in zip(observed, ("405900", "300100")):
                    self.assertEqual((updated.task_id, updated.session_id), (root, token))
                    self.assertIs(updated.slots[0], original.slots[0])
                    self.assertEqual(updated.slots[2].servant_id, sid)
                    self.assertFalse(sessions.valid(root, token))
                self.assertEqual(controller.inputs, 0)
            finally:
                tasker.post_stop().wait()


if __name__ == "__main__":
    unittest.main()
