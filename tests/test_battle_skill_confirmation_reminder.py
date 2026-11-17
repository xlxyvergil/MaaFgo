"""原生入口的客户端提醒按整个任务去重，不连接设备。"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from formation_test_support import battle_action as action
from battle.runtime.formation_session import sessions
from battle.runtime.skill_confirmation_reminder import skill_confirmation_reminders as reminders


class SkillConfirmationReminderTests(unittest.TestCase):
    def setUp(self):
        reminders.stop_owner("")
        sessions.stop_owner("")
        self.addCleanup(reminders.stop_owner, "")
        self.addCleanup(sessions.stop_owner, "")
        self.sink = action.FormationTaskLifecycle()
        self.gui = self.enterContext(patch.object(action.mfaalog, "_print_to_gui"))
        self.enterContext(patch.object(action.mfaalog, "diagnostic"))
        self.enterContext(patch.object(action.mfaalog, "SHOW_VERBOSE_LOGS_IN_GUI", False))
        self.enterContext(patch.object(action, "inspect_victory_settlement"))
        runtime = self.enterContext(patch.object(action, "AutoBattleRuntime"))
        runtime.return_value.run.return_value = NS(ok=True, reason="victory", turns=1)

    def event(self, root, owner="device-a", *, status=None, entry="native"):
        self.sink.on_tasker_task(None, status or action.NotificationType.Starting,
                                 NS(task_id=root, uuid=owner, entry=entry))

    def run_battle(self, root, *, stopping=False):
        # Each battle receives a different Context wrapper with the same root.
        context = NS(get_task_job=lambda: NS(job_id=root), get_node_data=lambda _: {},
                     tasker=NS(stopping=stopping, controller=object()))
        result = action.AutoBattleAction().run(context, NS(custom_action_param="{}"))
        self.assertTrue(result.success)

    def test_visible_warning_once_across_battles_and_formation_restarts(self):
        self.event(11)
        for _ in range(3):
            sessions.begin(11)
            self.run_battle(11)
            sessions.finish(11)
        self.gui.assert_called_once()
        prefix, text = self.gui.call_args.args
        self.assertEqual(prefix, "warn:")
        self.assertIn("技能使用确认", text)
        self.assertIn("停止任务", text)
        self.assertIn("关闭后重新运行", text)
        self.assertIn("如已关闭弹窗可以无视", text)

    def test_new_task_and_reused_id_after_completion_remind_again(self):
        self.event(11)
        self.run_battle(11)
        self.event(12)
        self.run_battle(12)
        self.event(11, status=action.NotificationType.Succeeded)
        self.event(11)
        self.run_battle(11)
        self.assertEqual(self.gui.call_count, 3)

    def test_failed_task_is_cleared_and_starting_resets_reused_id(self):
        for root in (11, 12):
            self.event(root)
            self.run_battle(root)
        self.event(11, status=action.NotificationType.Failed)
        self.run_battle(11)
        self.event(12)
        self.run_battle(12)
        self.assertEqual(self.gui.call_count, 4)

    def test_stopping_one_client_keeps_other_client_deduplicated(self):
        self.event(11, "device-a")
        self.event(12, "device-b")
        self.run_battle(11)
        self.run_battle(12)
        self.event(0, "device-a", entry="MaaTaskerPostStop")
        self.run_battle(12)
        self.event(11, "device-a")
        self.run_battle(11)
        self.assertEqual(self.gui.call_count, 3)

    def test_cancelled_entry_does_not_consume_the_reminder(self):
        self.event(11)
        self.run_battle(11, stopping=True)
        self.gui.assert_not_called()
        self.run_battle(11)
        self.gui.assert_called_once()


if __name__ == "__main__":
    unittest.main()
