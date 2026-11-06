"""会话/入口测试不连接设备、不执行战斗。"""
import copy
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from formation_test_support import ROOT, formation as f, battle_action as a, repeat_action as r
from battle.core.models import FormationSlot
from battle.runtime.formation_session import FormationSessions, SESSION_NODE, session_override, sessions


SLOTS = tuple(FormationSlot(i, "unknown", is_support=False) for i in range(1, 7))


def merge_dict(target, update):
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            merge_dict(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


class FakeContext:
    def __init__(self, root=111):
        self.root = root
        self.nodes = {}
        self.tasker = NS(stopping=False, controller=object())
        self.run_task = Mock()
        self.clear_hit_count = Mock()

    def get_task_job(self):
        return NS(job_id=self.root)

    def get_node_data(self, name):
        return self.nodes.get(name, {})

    def override_pipeline(self, update):
        merge_dict(self.nodes, update)
        return True


def publish(store, root, token):
    rev = store.begin_capture(root, token)
    store.append_frame(root, token, rev, SLOTS)
    return store.publish(root, token, rev, SLOTS, "test")


class SessionTests(unittest.TestCase):
    def test_repeat_reuses_initial_snapshot_without_mutating_positions(self):
        store = FormationSessions()
        token = store.begin(1, repeat=True)
        initial = publish(store, 1, token)
        store.battle_started(1, token, 2)
        current, battle = store.read(1, token)
        self.assertIs(current, initial)
        self.assertEqual(battle, 2)
        self.assertEqual(current.captured_battle, 1)
        newer = publish(store, 1, token)
        self.assertEqual(newer.captured_battle, 2)
        self.assertGreater(newer.revision, initial.revision)

    def test_cross_task_restart_and_stale_callbacks_are_rejected(self):
        store = FormationSessions()
        token = store.begin(1)
        rev = store.begin_capture(1, token)
        other = store.begin(2)
        with self.assertRaises(ValueError):
            store.read(2, token)
        store.invalidate(1, token, "failed")
        with self.assertRaises(ValueError):
            store.append_frame(1, token, rev, SLOTS)
        with self.assertRaises(ValueError):
            store.publish(1, token, rev, SLOTS, "test")
        newer = store.begin(1)
        self.assertNotEqual(token, newer)
        self.assertFalse(store.valid(1, token))
        self.assertTrue(store.valid(2, other))
        self.assertFalse(FormationSessions().valid(1, newer))

    def test_reentering_page_and_capture_error_clear_old_result(self):
        store = FormationSessions()
        token = store.begin(1)
        publish(store, 1, token)
        rev = store.begin_capture(1, token)
        with self.assertRaisesRegex(ValueError, "pending"):
            store.read(1, token)
        store.append_frame(1, token, rev, (), "screenshot_unavailable")
        with self.assertRaisesRegex(ValueError, "screenshot_unavailable"):
            store.frames(1, token, rev)
        store.finish(1)
        self.assertFalse(store.valid(1, token))

    def test_skipped_capture_discards_old_snapshot_and_allows_empty_read(self):
        store = FormationSessions()
        token = store.begin(1)
        publish(store, 1, token)
        store.begin_capture(1, token)
        store.skip_capture(1, token)
        snapshot, battle = store.read(1, token)
        self.assertIsNone(snapshot)
        self.assertEqual(battle, 1)

    def test_stop_clears_only_matching_owner(self):
        store = FormationSessions()
        for root in (1, 2):
            store.task_started(root, f"device-{root}")
        first, second = store.begin(1), store.begin(2)
        store.stop_owner("device-1")
        self.assertFalse(store.valid(1, first))
        self.assertTrue(store.valid(2, second))


class EntryTests(unittest.TestCase):
    def setUp(self):
        sessions.stop_owner("")
        self.addCleanup(sessions.stop_owner, "")
        for module in (f, a, r):
            for name in ("info", "error", "debug"):
                patcher = patch.object(module.mfaalog, name)
                patcher.start()
                self.addCleanup(patcher.stop)

    def start(self, ctx, repeat=False):
        token = sessions.begin(ctx.root, repeat=repeat)
        ctx.override_pipeline(session_override(token))
        return token

    def test_capture_hands_snapshot_to_runtime(self):
        ctx = FakeContext()
        token = self.start(ctx)
        def sample(entry, pipeline_override):
            self.assertEqual(entry, "编队身份-采样")
            params = [pipeline_override[n]["recognition"]["param"]["custom_recognition_param"]
                      for n in f.IDENTITY_FRAME_NODES]
            self.assertEqual(
                [p["full_audit"] for p in params],
                [len(f.IDENTITY_FRAME_NODES) > 1 and i == len(f.IDENTITY_FRAME_NODES) - 1
                 for i in range(len(f.IDENTITY_FRAME_NODES))],
            )
            for p in params:
                sessions.append_frame(ctx.root, token, p["revision"], SLOTS)
            return NS(status=NS(succeeded=True))
        ctx.run_task.side_effect = sample
        result = f.CaptureInitialFormation().run(ctx, NS(node_name="编队身份-采集"))
        self.assertTrue(result.success)
        snapshot, _ = sessions.read(ctx.root, token)
        self.assertIsNotNone(snapshot)
        with patch.object(a, "AutoBattleRuntime") as runtime:
            runtime.return_value.run.return_value = NS(ok=True, reason="victory", turns=1)
            result = a.AutoBattleAction().run(ctx, NS(custom_action_param="{}"))
            self.assertTrue(result.success)
            self.assertIs(runtime.call_args.kwargs["initial_formation"], snapshot)

    def test_failed_capture_discards_snapshot_and_starts_runtime_with_generic_strategy(self):
        ctx = FakeContext()
        token = self.start(ctx)
        publish(sessions, ctx.root, token)
        ctx.run_task.return_value = NS(status=NS(succeeded=False))
        self.assertTrue(f.CaptureInitialFormation().run(ctx, NS(node_name="编队身份-采集")).success)
        with patch.object(a, "AutoBattleRuntime") as runtime:
            runtime.return_value.run.return_value = NS(ok=True, reason="victory", turns=1)
            self.assertTrue(a.AutoBattleAction().run(ctx, NS(custom_action_param="{}")).success)
            self.assertIsNone(runtime.call_args.kwargs["initial_formation"])

    def test_direct_battle_has_no_formation(self):
        ctx = FakeContext()
        with patch.object(a, "AutoBattleRuntime") as runtime:
            runtime.return_value.run.return_value = NS(ok=True, reason="victory", turns=1)
            a.AutoBattleAction().run(ctx, NS(custom_action_param="{}"))
            self.assertIsNone(runtime.call_args.kwargs["initial_formation"])

    def test_repeat_one_keeps_outer_session_and_always_cleans_up(self):
        for fail in (False, True):
            ctx = FakeContext()
            seen = []
            def run(entry, pipeline_override):
                token = ctx.nodes[SESSION_NODE]["attach"]["session_id"]
                seen.append(token)
                self.assertTrue(a.BeginNativeFormationSession().run(ctx, NS()).success)
                self.assertEqual(token, ctx.nodes[SESSION_NODE]["attach"]["session_id"])
                if fail:
                    raise RuntimeError("interrupted")
                return NS(status=NS(succeeded=True, failed=False))
            ctx.run_task.side_effect = run
            result = r.AutoBattleRepeatAction().run(ctx, NS(custom_action_param='{"battle_count":1}'))
            self.assertEqual(result.success, not fail)
            self.assertFalse(sessions.valid(ctx.root, seen[0]))

    def test_repeat_passes_initial_snapshot_to_later_battles(self):
        ctx = FakeContext()
        snapshots = []
        def run(entry, pipeline_override):
            token = ctx.nodes[SESSION_NODE]["attach"]["session_id"]
            if not snapshots:
                publish(sessions, ctx.root, token)
            snapshots.append(sessions.read(ctx.root, token))
            return NS(status=NS(succeeded=True, failed=False))
        ctx.run_task.side_effect = run
        self.assertTrue(r.AutoBattleRepeatAction().run(ctx, NS(custom_action_param='{"battle_count":3}')).success)
        self.assertEqual([x[1] for x in snapshots], [1, 2, 3])
        self.assertTrue(all(x[0] is snapshots[0][0] for x in snapshots))

    def test_trial_repeat_uses_full_entry_and_own_count_attach(self):
        ctx = FakeContext()
        ctx.nodes["地狱的试炼-入口"] = {"attach": {"battle_count": "3"}}
        ctx.nodes["原生自动战斗_多次入口"] = {"attach": {"battle_count": "9"}}
        ctx.run_task.return_value = NS(status=NS(succeeded=True, failed=False))
        result = r.AutoBattleRepeatAction().run(ctx, NS(node_name="地狱的试炼-入口", custom_action_param={
            "battle_entry_node": "地狱的试炼-单场入口", "reset_hit_nodes": ["试炼有界节点"],
        }))
        self.assertTrue(result.success)
        self.assertEqual([call.args[0] for call in ctx.run_task.call_args_list],
                         ["地狱的试炼-单场入口"] * 3)
        self.assertTrue(all(call.kwargs["pipeline_override"] == {} for call in ctx.run_task.call_args_list))
        self.assertEqual(ctx.clear_hit_count.call_count, 3)

    def test_trial_repeat_stops_on_failed_round_without_starting_next(self):
        ctx = FakeContext()
        ctx.run_task.side_effect = [
            NS(status=NS(succeeded=True, failed=False)),
            NS(status=NS(succeeded=False, failed=True), entry="地狱的试炼-单场入口"),
        ]
        result = r.AutoBattleRepeatAction().run(ctx, NS(custom_action_param={
            "battle_count": 3, "battle_entry_node": "地狱的试炼-单场入口",
        }))
        self.assertFalse(result.success)
        self.assertEqual(ctx.run_task.call_count, 2)
        self.assertNotIn("连续出击", str(ctx.run_task.call_args_list))

    def test_trial_repeat_one_still_calls_full_navigation_entry(self):
        ctx = FakeContext()
        ctx.run_task.return_value = NS(status=NS(succeeded=True, failed=False))
        result = r.AutoBattleRepeatAction().run(ctx, NS(custom_action_param={
            "battle_count": 1, "battle_entry_node": "地狱的试炼-单场入口",
        }))
        self.assertTrue(result.success)
        self.assertEqual(ctx.run_task.call_args.args[0], "地狱的试炼-单场入口")

    def test_task_end_and_stop_callback_invalidate_session(self):
        ctx = FakeContext()
        sink = a.FormationTaskLifecycle()
        detail = NS(task_id=ctx.root, uuid="device", entry="native")
        sink.on_tasker_task(None, a.NotificationType.Starting, detail)
        token = self.start(ctx)
        sink.on_tasker_task(None, a.NotificationType.Succeeded, detail)
        self.assertFalse(sessions.valid(ctx.root, token))
        token = self.start(ctx)
        detail.entry = "MaaTaskerPostStop"
        sink.on_tasker_task(None, a.NotificationType.Starting, detail)
        self.assertFalse(sessions.valid(ctx.root, token))

    def test_bbc_and_chaldea_routes_keep_completion_gate(self):
        nodes = {}
        for path in (ROOT / "assets/resource/base/pipeline").glob("*.json"):
            nodes.update(json.loads(path.read_text(encoding="utf-8")))
        self.assertEqual(nodes["进本-点击队伍确认"]["next"], ["进本-实际点击队伍确认"])
        self.assertEqual(nodes["进本-实际点击队伍确认"]["action"]["type"], "Click")
        options = json.loads((ROOT / "assets/options/Chaldea导入手动输入.json").read_text(encoding="utf-8"))
        for case in options["option"]["Chaldea编队方式"]["cases"]:
            effective = copy.deepcopy(nodes)
            merge_dict(effective, case["pipeline_override"])
            merge_dict(effective, session_override("native-test"))
            self.assertEqual(effective["进本-编队处理"]["next"], case["pipeline_override"]["进本-编队处理"]["next"])
            endpoint = "手动编队检查完成" if case["name"] == "手动" else "自动编队完成"
            self.assertEqual(effective[endpoint]["next"], ["进本-点击队伍确认"])
            self.assertEqual(effective["进本-点击队伍确认"]["next"], ["原生战斗-校验茶壶"])
            self.assertEqual(effective["原生战斗-校验茶壶"]["next"], ["编队身份-采集"])
        self.assertEqual(nodes["执行自动编队"]["next"], ["执行羁绊补齐自动编队"])
        for name in ("编队身份-采集", "编队身份-采样", *f.IDENTITY_FRAME_NODES):
            self.assertEqual(nodes[name]["on_error"], [])


if __name__ == "__main__":
    unittest.main()
