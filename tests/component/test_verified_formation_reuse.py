"""已验证编队交接：不连接设备、不执行战斗或真实点击。"""
import sys
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
from formation_test_support import formation as f, battle_action as a
from test_formation_session import FakeContext
from battle.core.models import FormationSlot, VERIFIED_FORMATION_SOURCE
from battle.runtime.formation_session import FormationSessions, sessions, session_override
from bond_completion_action import CompleteBondFormation


def layout():
    return [dict(kind="LOCAL", svt_id=100100, score=.99),
            dict(kind="LOCAL", svt_id=200100, score=.98),
            dict(kind="SUPPORT", svt_id=100100, score=.99, identity_score=.97),
            *[dict(kind="EMPTY", svt_id=None) for _ in range(3)]]


def slots():
    return tuple(FormationSlot(i, "identified" if i <= 3 else "empty",
                               str(100100 if i in (1, 3) else 200100) if i <= 3 else None,
                               is_support=i == 3) for i in range(1, 7))


class VerifiedSessionTests(unittest.TestCase):
    def test_partial_capture_preserves_known_evidence_and_only_fills_unknown(self):
        store = FormationSessions()
        token = store.begin(1)
        known = (*slots()[:5], FormationSlot(6, "unknown", is_support=False))
        rev = store.begin_update(1, token)
        store.publish_verified(1, token, rev, known)
        capture = store.begin_capture(1, token, reuse_verified=True)
        self.assertEqual(store.capture_seed(1, token, capture), known)
        sampled = tuple(FormationSlot(i, "identified", "300100", is_support=False,
                                     consistent_frames=1) for i in range(1, 7))
        store.append_frame(1, token, capture, sampled)
        result = store.publish(1, token, capture, sampled, "visual-test")
        self.assertEqual(result.slots[:5], known[:5])
        self.assertEqual(result.slots[5].servant_id, "300100")
        self.assertEqual(result.source, VERIFIED_FORMATION_SOURCE)
        self.assertEqual(result.slots[0].consistent_frames, 0)

    def test_update_failure_stale_callback_and_restart_cannot_reuse_previous_result(self):
        store = FormationSessions()
        token = store.begin(1)
        first = store.begin_update(1, token)
        store.publish_verified(1, token, first, slots())
        revision = store.begin_update(1, token)
        self.assertIsNone(store.verified_snapshot(1, token))
        with self.assertRaises(ValueError):
            store.publish_verified(1, token, first, slots())
        store.abandon_update(1, token, revision)
        self.assertIsNone(store.read(1, token)[0])
        with self.assertRaises(ValueError):
            store.publish_verified(1, token, revision, slots())
        store.begin(1)
        with self.assertRaises(ValueError):
            store.publish_verified(1, token, revision, slots())

    def test_new_support_and_new_battle_do_not_reuse_verified_snapshot(self):
        for fallback in (False, True):
            store = FormationSessions()
            token = store.begin(1, repeat=True)
            store.publish_verified(1, token, store.begin_update(1, token), slots())
            store.set_support_fallback(1, token, fallback)
            self.assertIsNone(store.verified_snapshot(1, token))
            self.assertIsNone(store.read(1, token)[0])
        store = FormationSessions()
        token = store.begin(1, repeat=True)
        store.publish_verified(1, token, store.begin_update(1, token), slots())
        store.battle_started(1, token, 2)
        self.assertIsNone(store.verified_snapshot(1, token))
        revision = store.begin_update(1, token)
        store.battle_started(1, token, 3)
        with self.assertRaises(ValueError):
            store.publish_verified(1, token, revision, slots())

    def test_cross_task_and_fallback_publication_are_rejected(self):
        store = FormationSessions()
        token = store.begin(1)
        other = store.begin(2)
        revision = store.begin_update(1, token)
        with self.assertRaises(ValueError):
            store.publish_verified(2, token, revision, slots())
        store.set_support_fallback(1, token, True)
        revision = store.begin_update(1, token)
        with self.assertRaises(ValueError):
            store.publish_verified(1, token, revision, slots())
        self.assertIsNone(store.verified_snapshot(2, other))


class VerifiedActionTests(unittest.TestCase):
    def setUp(self):
        sessions.stop_owner("")
        self.addCleanup(sessions.stop_owner, "")
        self.ctx = FakeContext()
        self.token = sessions.begin(self.ctx.root)
        self.ctx.override_pipeline(session_override(self.token))
        for name in ("info", "warning", "error"):
            p = patch.object(f.mfaalog, name)
            p.start()
            self.addCleanup(p.stop)

    def action(self, cls=f.AutoFormationFromChaldea):
        action = cls()
        action.context = self.ctx
        return action

    def test_real_auto_action_publishes_after_success_then_capture_does_not_sample(self):
        action = self.action()
        current = layout()
        self.ctx.nodes["auto"] = {"attach": {"chaldea_import_source": "test", "auto_equip": False}}
        expected = [dict(state, slot=i) for i, state in enumerate(current)]
        for name in ("_init_paths", "_init_scale", "_prepare_target_templates", "_log_layout",
                     "_log_support_identity_if_possible", "_confirm_formation_change_if_present"):
            setattr(action, name, Mock())
        action._build_expected = Mock(return_value=expected)
        action._detect_slots_stable = Mock(return_value=current)
        action._should_clear_formation = Mock(return_value=False)
        action._confirmed_now = Mock(return_value=True)
        action._relocate_unexpected_support = Mock(return_value=True)
        action._reorder_existing = Mock(return_value=True)
        action._replace_local_servants = Mock(return_value=True)
        action._run_pipeline = Mock(return_value=True)
        with patch.object(f, "fetch_share_data", return_value=({}, None, None)):
            self.assertTrue(action.run(self.ctx, NS(node_name="auto")).success)
        snapshot = sessions.verified_snapshot(self.ctx.root, self.token)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.slots[0].servant_id, snapshot.slots[2].servant_id)
        self.assertFalse(snapshot.slots[0].is_support)
        self.assertTrue(snapshot.slots[2].is_support)
        self.assertTrue(f.CaptureInitialFormation().run(self.ctx, NS(node_name="编队身份-采集")).success)
        self.ctx.run_task.assert_not_called()
        with patch.object(a, "AutoBattleRuntime") as runtime:
            runtime.return_value.run.return_value = NS(ok=True, reason="test", turns=0)
            self.assertTrue(a.AutoBattleAction().run(self.ctx, NS(custom_action_param="{}")).success)
            self.assertIs(runtime.call_args.kwargs["initial_formation"], snapshot)
        # 保存失败不可遗留上一次的成功结果。
        action._run_pipeline.return_value = False
        with patch.object(f, "fetch_share_data", return_value=({}, None, None)):
            self.assertFalse(action.run(self.ctx, NS(node_name="auto")).success)
        self.assertIsNone(sessions.verified_snapshot(self.ctx.root, self.token))

    def test_unprovided_member_and_any_support_remain_unknown_not_expected_empty(self):
        action = self.action()
        actual = layout()
        actual[2]["svt_id"] = None
        actual[5] = dict(kind="OTHER", svt_id=None)
        action._begin_verified_formation_update()
        action._publish_verified_layout(actual)
        result = sessions.verified_snapshot(self.ctx.root, self.token)
        self.assertEqual(result.slots[2].status, "unknown")
        self.assertTrue(result.slots[2].is_support)
        self.assertEqual(result.slots[5].status, "unknown")
        self.assertEqual(result.slots[4].status, "empty")
        def sample(entry, pipeline_override):
            param = pipeline_override[f.IDENTITY_FRAME_NODES[0]]["recognition"]["param"]["custom_recognition_param"]
            seed = sessions.capture_seed(self.ctx.root, self.token, param["revision"])
            self.assertEqual(seed, result.slots)
            sessions.append_frame(self.ctx.root, self.token, param["revision"], slots())
            return NS(status=NS(succeeded=True))
        self.ctx.run_task.side_effect = sample
        self.assertTrue(f.CaptureInitialFormation().run(self.ctx, NS(node_name="编队身份-采集")).success)
        self.ctx.run_task.assert_called_once()

    def test_stopped_action_does_not_publish_or_skip_sampling(self):
        action = self.action()
        action._begin_verified_formation_update()
        self.ctx.tasker.stopping = True
        action._publish_verified_layout(layout())
        action._discard_unpublished_formation()
        self.assertIsNone(sessions.read(self.ctx.root, self.token)[0])
        self.assertFalse(f.CaptureInitialFormation().run(self.ctx, NS(node_name="编队身份-采集")).success)

    def test_independent_formation_does_not_create_native_session(self):
        action = self.action()
        action.context = FakeContext(root=999)
        action._begin_verified_formation_update()
        action._publish_verified_layout(layout())
        self.assertIsNone(action._formation_update)

    def bond(self):
        action = self.action(CompleteBondFormation)
        action._begin_verified_formation_update()
        action._bond_initial_layout = layout()
        action.expected = [dict(state, slot=i) for i, state in enumerate(layout())]
        action.unspecified_servants_by_slot = {4: {"id": 300100}}
        action.added_servants = {4: {"id": 400100}}
        action.support_marker = object()
        action._match_template = Mock(return_value=(.99, None))
        action._is_empty_slot = Mock(return_value=True)
        action._shot = Mock(return_value=object())
        action._run_pipeline = Mock(return_value=True)
        action._confirm_formation_change_if_present = Mock()
        action._focus_user = Mock()
        return action

    def test_bond_success_updates_replaced_members_and_memory_hit_publishes(self):
        action = self.bond()
        action._record_bond_verified_layout(object(), protected_verified=True)
        action._publish_verified_layout(action._verified_formation_layout)
        result = sessions.verified_snapshot(self.ctx.root, self.token)
        self.assertEqual(result.slots[4].servant_id, "400100")
        action = self.bond()
        self.assertTrue(action._finish_auto_memory_hit([]).success)
        self.assertEqual(sessions.verified_snapshot(self.ctx.root, self.token).slots[4].servant_id, "400100")

    def test_bond_disabled_keeps_result_but_cancelled_update_forces_regular_capture(self):
        action = self.action()
        action._begin_verified_formation_update()
        action._publish_verified_layout(layout())
        previous = sessions.verified_snapshot(self.ctx.root, self.token)
        self.assertTrue(self.action(CompleteBondFormation).run(self.ctx, NS(node_name="bond")).success)
        self.assertIs(sessions.verified_snapshot(self.ctx.root, self.token), previous)
        action = self.bond()
        action.debug_preserve_failure = False
        action.servant_detail_open = False
        action._confirmed_now = lambda predicate: predicate == action._in_formation_edit
        action._wait_for = Mock(return_value=True)
        self.assertTrue(action._abort_safe("test_cancel").success)
        action._discard_unpublished_formation()
        self.assertIsNone(sessions.read(self.ctx.root, self.token)[0])

    def test_bond_enabled_early_failure_and_memory_save_failure_discard_old_result(self):
        action = self.action()
        action._begin_verified_formation_update()
        action._publish_verified_layout(layout())
        self.ctx.nodes["bond"] = {"attach": {"bond_completion_enabled": True}}
        self.assertFalse(self.action(CompleteBondFormation).run(self.ctx, NS(node_name="bond")).success)
        self.assertIsNone(sessions.read(self.ctx.root, self.token)[0])
        action = self.bond()
        action._run_pipeline.return_value = False
        action._abort_safe = Mock(return_value=NS(success=False))
        self.assertFalse(action._finish_auto_memory_hit([]).success)
        action._discard_unpublished_formation()
        self.assertIsNone(sessions.read(self.ctx.root, self.token)[0])

    def test_verified_layout_canonicalizes_alias_without_losing_support_position(self):
        action = self.action()
        action._get_servant_info = Mock(return_value={"id": "505600", "class": "caster"})
        actual = layout()
        actual[0]["svt_id"] = 505700
        actual[2]["svt_id"] = 505600
        action._begin_verified_formation_update()
        action._publish_verified_layout(actual)
        result = sessions.verified_snapshot(self.ctx.root, self.token)
        self.assertEqual(result.slots[0].servant_id, "505600")
        self.assertTrue(result.slots[2].is_support)

    def test_bond_missing_empty_or_support_marker_does_not_invent_identity(self):
        action = self.bond()
        action._match_template.return_value = None
        action._is_empty_slot.return_value = False
        action._record_bond_verified_layout(object(), protected_verified=True)
        action._publish_verified_layout(action._verified_formation_layout)
        result = sessions.verified_snapshot(self.ctx.root, self.token)
        self.assertEqual(result.slots[2].status, "unknown")
        self.assertEqual(result.slots[3].status, "unknown")


if __name__ == "__main__":
    unittest.main()
