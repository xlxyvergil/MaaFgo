"""TeaRunner and task-session integration; no device or game input is used."""
from collections import deque
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import formation_test_support  # Register production imports without an Agent.
from maa.agent.agent_server import AgentServer
from battle.runtime.formation_session import FormationSessions, SESSION_NODE
with patch.object(AgentServer, "custom_action", lambda *args: lambda cls: cls):
    import teapot_action as tea


def result(ok=True):
    return NS(status=NS(succeeded=ok))


class Context:
    def __init__(self, root, token, desired):
        self.root = root
        self.nodes = {SESSION_NODE: {"attach": {"session_id": token}},
                      tea.CONFIG_NODE: {"attach": {"use_teapot": desired}}}
        self.tasker = NS(stopping=False, controller=object())
        self.run_task = Mock(return_value=result())

    def get_task_job(self):
        return NS(job_id=self.root)

    def get_node_data(self, name):
        return self.nodes.get(name, {})

    def override_pipeline(self, update):
        for name, node in update.items():
            self.nodes.setdefault(name, {}).update(node)
        return True


class Runner(tea.TeapotRunner):
    def __init__(self, context, frames=(), *, failed_action=None, stop_action=None):
        super().__init__(context)
        self.frames = deque(frames)
        self.last = tea.TeapotFrame("unknown")
        self.observations = 0
        self.actions = []
        self.failed_action = failed_action
        self.stop_action = stop_action

    def observe(self):
        self.observations += 1
        if self.frames:
            self.last = self.frames.popleft()
        return self.last

    def _run_task(self, name):
        self.actions.append(name)
        if name == self.stop_action:
            self.context.tasker.stopping = True
        return result(name != self.failed_action)


class TeapotRepeatCacheTests(unittest.TestCase):
    def setUp(self):
        self.store = FormationSessions()
        cache = patch.object(tea, "sessions", self.store)
        cache.start()
        self.addCleanup(cache.stop)
        for name in ("info", "warning", "error"):
            log = patch.object(tea.mfaalog, name)
            log.start()
            self.addCleanup(log.stop)

    def begin(self, root=101, desired=False, repeat=True):
        token = self.store.begin(root, repeat=repeat)
        return Context(root, token, desired), token

    def bar(self, desired):
        return [tea.TeapotFrame("bar", desired)] * tea.STABLE_FRAMES

    def assert_cached_without_observation(self, context):
        runner = Runner(context)
        runner.observe = Mock(side_effect=AssertionError("cache hit must not read UI"))
        self.assertTrue(runner.run())
        runner.observe.assert_not_called()
        self.assertEqual(runner.actions, [])
        context.run_task.assert_not_called()

    def test_verified_on_and_off_settings_skip_all_later_ui_checks(self):
        for desired in (False, True):
            with self.subTest(desired=desired):
                context, token = self.begin(desired=desired)
                first = Runner(context, self.bar(desired))
                self.assertTrue(first.run())
                self.assertEqual(first.observations, tea.STABLE_FRAMES)
                self.assertTrue(self.store.teapot_confirmed(context.root, token, desired))
                self.store.battle_started(context.root, token, 2)
                second = Context(context.root, token, desired)
                self.assert_cached_without_observation(second)
                self.store.battle_started(context.root, token, 3)
                self.assert_cached_without_observation(Context(context.root, token, desired))

    def test_unavailable_first_quest_rechecks_next_party_page_then_reuses_success(self):
        for desired in (False, True):
            with self.subTest(desired=desired):
                context, token = self.begin(desired=desired)
                first = Runner(context, [tea.TeapotFrame("absent")] * tea.ABSENT_STABLE_FRAMES)
                self.assertTrue(first.run())
                self.assertEqual(first.actions, [])
                self.assertFalse(self.store.teapot_confirmed(context.root, token, desired))
                self.store.battle_started(context.root, token, 2)
                second = Runner(Context(context.root, token, desired), self.bar(desired))
                self.assertTrue(second.run())
                self.assertEqual(second.observations, tea.STABLE_FRAMES)
                self.store.battle_started(context.root, token, 3)
                self.assert_cached_without_observation(Context(context.root, token, desired))

    def test_popup_is_cached_only_after_switch_close_and_outer_bar_verification(self):
        context, token = self.begin(desired=True)
        frames = ([tea.TeapotFrame("popup", False)] * tea.STABLE_FRAMES
                  + [tea.TeapotFrame("popup", True)] * tea.STABLE_FRAMES
                  + self.bar(True))
        first = Runner(context, frames)
        self.assertTrue(first.run())
        self.assertEqual(first.actions, ["茶壶-弹窗设为开启", "茶壶-关闭弹窗"])
        self.assertTrue(self.store.teapot_confirmed(context.root, token, True))
        self.store.battle_started(context.root, token, 2)
        self.assert_cached_without_observation(Context(context.root, token, True))

    def test_failed_unknown_popup_toggle_and_close_never_seed_cache(self):
        cases = [
            (False, [tea.TeapotFrame("unknown")] * tea.OBSERVATION_ROUNDS, None),
            (False, [tea.TeapotFrame("popup")] * tea.OBSERVATION_ROUNDS, None),
            (True, self.bar(False), "茶壶-直接设为开启"),
            (False, [tea.TeapotFrame("popup", False)] * tea.STABLE_FRAMES, "茶壶-关闭弹窗"),
            (True, self.bar(False) + [tea.TeapotFrame("unknown")] * tea.OBSERVATION_ROUNDS, None),
        ]
        for desired, frames, failed in cases:
            with self.subTest(desired=desired, failed=failed, kind=frames[-1].kind):
                context, token = self.begin(desired=desired)
                first = Runner(context, frames, failed_action=failed)
                self.assertFalse(first.run())
                self.assertFalse(self.store.teapot_confirmed(context.root, token, desired))
                self.store.battle_started(context.root, token, 2)
                next_run = Runner(Context(context.root, token, desired), self.bar(desired))
                self.assertTrue(next_run.run())
                self.assertEqual(next_run.observations, tea.STABLE_FRAMES)

    def test_stop_before_or_during_setting_change_does_not_seed_cache(self):
        for when in ("before", "toggle"):
            with self.subTest(when=when):
                context, token = self.begin(desired=True)
                context.tasker.stopping = when == "before"
                first = Runner(context, self.bar(False),
                               stop_action="茶壶-直接设为开启" if when == "toggle" else None)
                self.assertFalse(first.run())
                self.assertFalse(self.store.teapot_confirmed(context.root, token, True))
                retry = Runner(Context(context.root, token, True), self.bar(True))
                self.assertTrue(retry.run())
                self.assertEqual(retry.observations, tea.STABLE_FRAMES)

    def test_changed_target_failed_attempt_does_not_revive_old_confirmation(self):
        context, token = self.begin(desired=False)
        self.assertTrue(Runner(context, self.bar(False)).run())
        self.store.battle_started(context.root, token, 2)
        changed = Runner(Context(context.root, token, True), self.bar(False),
                         failed_action="茶壶-直接设为开启")
        self.assertFalse(changed.run())
        self.store.battle_started(context.root, token, 3)
        reverted = Runner(Context(context.root, token, False), self.bar(False))
        self.assertTrue(reverted.run())
        self.assertEqual(reverted.observations, tea.STABLE_FRAMES)

    def test_finish_restart_and_other_root_always_need_their_own_confirmation(self):
        context, token = self.begin(desired=False)
        self.assertTrue(Runner(context, self.bar(False)).run())
        other, _ = self.begin(root=202, desired=False)
        other_run = Runner(other, self.bar(False))
        self.assertTrue(other_run.run())
        self.assertEqual(other_run.observations, tea.STABLE_FRAMES)
        self.assert_cached_without_observation(Context(context.root, token, False))
        self.store.finish(context.root, token)
        renewed, new_token = self.begin(root=context.root, desired=False)
        self.assertNotEqual(token, new_token)
        stale = Runner(Context(context.root, token, False), self.bar(False))
        self.assertTrue(stale.run())
        self.assertEqual(stale.observations, tea.STABLE_FRAMES)
        self.assertFalse(self.store.teapot_confirmed(renewed.root, new_token, False))
        current = Runner(renewed, self.bar(False))
        self.assertTrue(current.run())
        self.assertEqual(current.observations, tea.STABLE_FRAMES)

    def test_single_battle_session_never_skips_a_later_check(self):
        context, token = self.begin(repeat=False)
        for _ in range(2):
            runner = Runner(Context(context.root, token, False), self.bar(False))
            self.assertTrue(runner.run())
            self.assertEqual(runner.observations, tea.STABLE_FRAMES)


if __name__ == "__main__":
    unittest.main()
