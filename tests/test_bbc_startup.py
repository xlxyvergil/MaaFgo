"""BBC启动回归：不连接BBC进程/模拟器，Tk测试只使用隔离的透明窗口。"""
import ast
from enum import IntEnum
from pathlib import Path
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]


def load_classes(path, names, namespace):
    # 隔离模块启动副作用（Agent注册、单例、覆盖运行中的BBC日志）。
    tree = ast.parse(path.read_text(encoding="utf-8"))
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name in names]
    for node in classes:
        node.decorator_list = []
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[future, *classes], type_ignores=[]))
    exec(compile(module, str(path), "exec"), namespace)
    return namespace


def server_namespace(page):
    return load_classes(ROOT / "bbcdll/bbc_tcp_server.py", {"BattleControlAPI", "StatusAPI"}, {
        "get_bb_page": lambda: page, "ensure_imports": lambda: None,
        "CT": NS(Scale=1.5, LongEffectOn=False, SettingOn=False),
        "Battle": object(), "_log": Mock(),
    })


def fake_page():
    callbacks = []
    page = NS(
        device=NS(running=False, taskName="None", stop=Mock()),
        servantGroup=[NS(exist=True) for _ in range(3)],
        start=NS(startScript=Mock(), event_generate=Mock()),
        topLabel=NS(cget=lambda _: "前辈！请设置好阵容再出战哦！"),
        winfo_toplevel=lambda: NS(state=lambda: "iconic"),
        after=lambda delay, callback: callbacks.append(callback),
        callbacks=callbacks,
    )
    return page


class ServerStartTests(unittest.TestCase):
    def setUp(self):
        self.page = fake_page()
        self.ns = server_namespace(self.page)
        self.api = self.ns["BattleControlAPI"]

    def test_direct_callback_is_scheduled_and_uses_logical_center(self):
        result = self.api.start_battle()
        self.assertTrue(result["success"])
        self.assertEqual(result["start_status"], "scheduled")
        self.page.start.startScript.assert_not_called()
        self.page.callbacks.pop(0)()
        event = self.page.start.startScript.call_args.args[0]
        self.assertEqual((event.x, event.y), (75, 75))
        self.page.start.event_generate.assert_not_called()
        self.assertEqual(self.page._maa_battle_start["status"], "dispatched")

    def test_duplicate_request_does_not_queue_second_callback(self):
        self.api.start_battle()
        self.api.start_battle()
        self.assertEqual(len(self.page.callbacks), 1)

    def test_missing_servant_is_real_non_retryable_error(self):
        self.page.servantGroup[1].exist = False
        result = self.api.start_battle()
        self.assertFalse(result["success"])
        self.assertIn("第2位", result["error"])
        self.assertFalse(result["retryable"])
        self.assertFalse(self.page.callbacks)

    def test_animation_and_editing_report_different_recovery(self):
        self.ns["CT"].LongEffectOn = True
        self.assertTrue(self.api.start_battle()["retryable"])
        self.ns["CT"].LongEffectOn = False
        self.ns["CT"].SettingOn = True
        self.assertFalse(self.api.start_battle()["retryable"])

    def test_running_other_task_is_not_a_running_battle(self):
        self.page.device.running = True
        self.page.device.taskName = "testConnection"
        self.assertFalse(self.api.start_battle()["success"])
        self.assertFalse(self.ns["StatusAPI"].get_ui_status()["battle_running"])

    def test_existing_battle_is_not_started_again(self):
        self.page.device.running = True
        self.page.device.taskName = "FGObattle"
        self.assertEqual(self.api.start_battle()["start_method"], "already_running")
        self.assertFalse(self.page.callbacks)

    def test_callback_exception_is_exposed_to_client(self):
        self.page.start.startScript.side_effect = RuntimeError("boom")
        self.api.start_battle()
        self.page.callbacks.pop(0)()
        status = self.ns["StatusAPI"].get_ui_status()
        self.assertEqual(status["start_status"], "failed")
        self.assertEqual(status["start_error"], "boom")

    def test_schedule_failure_does_not_leave_a_pending_start(self):
        self.page.after = Mock(side_effect=RuntimeError("Tk unavailable"))
        self.assertFalse(self.api.start_battle()["success"])
        self.assertEqual(self.page._maa_battle_start["status"], "failed")

    def test_loading_another_config_invalidates_queued_callback(self):
        self.api.start_battle()
        self.page._maa_battle_start = {}
        self.page.callbacks.pop(0)()
        self.page.start.startScript.assert_not_called()

    def test_stop_cancels_queued_callback_and_destroys_confirmation(self):
        self.api.start_battle()
        dialog = NS(winfo_exists=lambda: True, destroy=Mock())
        self.page.start.assistReportWin = dialog
        self.api.stop_battle()
        for callback in self.page.callbacks:
            callback()
        self.page.start.startScript.assert_not_called()
        dialog.destroy.assert_called_once()
        self.assertFalse(hasattr(self.page.start, "assistReportWin"))

    def test_status_reports_confirmations_and_unavailable_page(self):
        self.page.start.cache = NS(tips4closesubWin=NS(winfo_exists=lambda: True))
        self.assertEqual(self.ns["StatusAPI"].get_ui_status()["start_dialogs"], ["关闭游戏字幕提示"])
        self.ns["get_bb_page"] = lambda: None
        self.assertFalse(self.ns["StatusAPI"].get_ui_status()["success"])

    def test_actual_tk_callback_runs_while_minimized_or_hidden(self):
        try:
            import tkinter as tk
        except ImportError as error:
            self.skipTest(str(error))
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        try:
            root.attributes("-alpha", 0.0)
            root.geometry("240x160+0+0")
            root.update()
            for window_state in ("normal", "iconic", "withdrawn"):
                with self.subTest(window_state=window_state):
                    if window_state == "normal":
                        root.deiconify()
                    elif window_state == "iconic":
                        root.iconify()
                    else:
                        root.withdraw()
                    root.update()
                    frame = tk.Frame(root)
                    frame.pack()
                    button = tk.Label(frame, text="start")
                    button.place(x=10, y=10)
                    page = fake_page()
                    page.start = button
                    page.after = frame.after
                    page.winfo_toplevel = frame.winfo_toplevel
                    button.startScript = Mock(side_effect=lambda _: setattr(page.device, "running", True))
                    ns = server_namespace(page)
                    result = ns["BattleControlAPI"].start_battle()
                    self.assertTrue(result["success"])
                    self.assertFalse(page.device.running)
                    root.update()
                    self.assertTrue(page.device.running)
                    button.startScript.assert_called_once()
                    frame.destroy()
        finally:
            root.destroy()


class AgentStartTests(unittest.TestCase):
    def setUp(self):
        class CustomAction:
            RunResult = NS
        self.log = NS(info=Mock(), warning=Mock(), error=Mock())
        self.manager = NS(
            send_command=Mock(side_effect=self.command), get_messages_by_title=Mock(return_value=[]),
            set_popup_callback=Mock(), clear_message_queue=Mock(), disconnect_tcp=Mock(),
        )
        self.status = {"success": True, "battle_running": False, "device_running": False,
                       "top_label": "前辈！请设置好阵容再出战哦！", "start_status": "dispatched"}
        self.errors = {}
        self.ns = load_classes(ROOT / "agent/custom/bbc_action.py", {"ExecuteBbcTask", "BattleType"}, {
            "CustomAction": CustomAction, "IntEnum": IntEnum, "threading": threading,
            "mfaalog": self.log, "time": NS(sleep=Mock()), "get_manager": lambda: self.manager,
            "map_battle_attempt": Mock(return_value=None),
        })
        self.action = self.ns["ExecuteBbcTask"]()
        self.context = NS(tasker=NS(stopping=False), override_pipeline=Mock(), get_node_data=lambda _: {
            "attach": {"bbc_team_config": "test.json", "run_count": 1, "apple_type": "silver"}})
        self.state = {"finished": False, "popup_event": threading.Event()}

    def command(self, cmd, args, timeout):
        if cmd in self.errors:
            return self.errors[cmd]
        if cmd == "get_ui_status":
            return dict(self.status)
        if cmd == "start_battle":
            return {"success": True, "start_method": "direct_start_script", "window_state": "iconic"}
        return {"success": True}

    def setup_battle(self):
        return self.action._setup_and_start_battle(
            self.context, "test.json", 1, "silver", self.ns["BattleType"].CONTINUOUS,
            False, False, self.state, self.manager)

    def test_idle_label_is_timeout_not_missing_formation(self):
        result = self.setup_battle()
        self.assertFalse(result["success"])
        self.assertTrue(result["need_restart"])
        self.assertIn("超时", result["error"])
        self.assertNotIn("阵容未设置", result["error"])

    def test_accepted_request_requires_actual_running_state(self):
        self.status.update(battle_running=True, device_running=True, task_name="FGObattle")
        self.assertIs(self.setup_battle(), self.state)
        self.assertTrue(self.state["battle_confirmed"])
        self.assertTrue(any("实际运行" in str(call) for call in self.log.info.call_args_list))

    def test_config_log_is_not_mistaken_for_a_json_file_by_mxu(self):
        self.status.update(battle_running=True, device_running=True, task_name='FGObattle')
        self.setup_battle()
        messages = [c.args[0] for c in self.log.info.call_args_list if '加载' in c.args[0]]
        self.assertEqual(len(messages), 2)
        self.assertTrue(all('test.json' in text and not text.endswith('.json') for text in messages))

    def test_parameter_failure_is_not_ignored(self):
        self.errors["set_run_times"] = {"success": False, "error": "bad value"}
        result = self.setup_battle()
        self.assertIn("bad value", result["error"])
        self.assertFalse(any(c.args[0] == "start_battle" for c in self.manager.send_command.call_args_list))

    def test_ready_and_closed_notifications_are_not_battle_popups(self):
        messages = [{"event": "server_started"}, {"event": "disclaimer_closed", "popup_title": "免责声明！"},
                    {"event": "popup_closed", "popup_title": "脚本停止！"}, {}]
        self.assertFalse(self.action._handle_popups(messages, False, False, self.state, self.manager))
        self.assertFalse(self.state["finished"])

    def test_same_popup_from_callback_and_queue_is_answered_once(self):
        msg = {"event": "popup_show", "popup_title": "助战排序不符合", "popup_message": "test", "popup_id": "123"}
        self.action._handle_popups([msg], True, False, self.state, self.manager)
        self.action._handle_popups([msg], True, False, self.state, self.manager)
        self.assertEqual(sum(c.args[0] == "popup_response" for c in self.manager.send_command.call_args_list), 1)

    def test_callback_error_is_printed_and_preserved_without_retries(self):
        self.status["start_error"] = "callback failed"
        result = self.setup_battle()
        self.assertIn("callback failed", result["error"])
        self.assertFalse(result.get("need_restart", False))
        self.assertEqual(sum(c.args[0] == "start_battle" for c in self.manager.send_command.call_args_list), 1)

    def test_stop_during_start_wait_cancels_bbc_and_returns_stop_state(self):
        original_command = self.command
        def stop_after_accept(cmd, args, timeout):
            result = original_command(cmd, args, timeout)
            if cmd == "start_battle":
                self.context.tasker.stopping = True
            return result
        self.manager.send_command.side_effect = stop_after_accept
        result = self.setup_battle()
        self.assertTrue(result["finished"])
        self.assertEqual(result["popup_message"], "任务已停止")
        self.assertTrue(any(c.args[0] == "stop_battle" for c in self.manager.send_command.call_args_list))

    def test_confirmation_is_reported_without_duplicate_start_or_restart(self):
        self.status["start_dialogs"] = ["助战设置及刷本类型确认"]
        result = self.setup_battle()
        self.assertFalse(result["success"])
        self.assertFalse(result.get("need_restart", False))
        self.assertEqual(sum(c.args[0] == "start_battle" for c in self.manager.send_command.call_args_list), 1)
        self.assertEqual(self.log.warning.call_count, 1)

    def test_failure_result_skips_wait_and_cleans_up_pending_start(self):
        self.action._ensure_bbc_connected = Mock(return_value=True)
        self.action._verify_emulator_connection = Mock(return_value=True)
        self.action._wait_for_battle_end = Mock()
        failure = {"success": False, "error": "startup failed", "need_restart": True}
        def fail(*args):
            args[-2]["start_requested"] = True
            return failure
        self.action._setup_and_start_battle = Mock(side_effect=fail)
        self.assertEqual(self.action._execute_single_battle(self.context), failure)
        self.action._wait_for_battle_end.assert_not_called()
        self.assertTrue(any(c.args[0] == "stop_battle" for c in self.manager.send_command.call_args_list))
        self.manager.set_popup_callback.assert_called_with(None)
        self.manager.disconnect_tcp.assert_called_once()

    def test_restart_reason_is_propagated_to_run(self):
        self.action._execute_single_battle = Mock(side_effect=[
            {"success": False, "error": "startup timeout", "need_restart": True}, {"success": True}])
        self.action._restart_bbc = Mock(return_value=True)
        self.assertTrue(self.action.run(self.context, None).success)
        self.action._restart_bbc.assert_called_once()
        self.assertTrue(any("startup timeout" in str(c) for c in self.log.warning.call_args_list))

    def test_mixed_map_failure_is_reported_without_restarting_bbc(self):
        self.ns["map_battle_attempt"].return_value = NS(started=False, bbc_idle=True)
        self.action._execute_single_battle = Mock(return_value={
            "success": False, "error": "startup timeout", "need_restart": True,
        })
        self.action._restart_bbc = Mock()
        self.assertFalse(self.action.run(self.context, None).success)
        self.action._execute_single_battle.assert_called_once()
        self.action._restart_bbc.assert_not_called()

    def test_game_exception_keeps_restart_reason_in_failure_result(self):
        self.action._ensure_bbc_connected = Mock(return_value=True)
        self.action._verify_emulator_connection = Mock(return_value=True)
        self.action._setup_and_start_battle = Mock(return_value={})
        def game_error(context, state):
            state['need_restart'] = True
            return '游戏异常', '页面0任务结束！\n高速接口获取截图失败'
        self.action._wait_for_battle_end = Mock(side_effect=game_error)
        result = self.action._execute_single_battle(self.context)
        self.assertFalse(result['success'])
        self.assertTrue(result['need_restart'])
        self.assertIn('高速接口获取截图失败', result['error'])
        self.assertNotIn('\n', result['error'])


if __name__ == "__main__":
    unittest.main()
