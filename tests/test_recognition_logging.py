"""识别日志只落文件、事件不提取截图，以及写入失败和轮转的回归。"""
import contextlib
import io
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]

from maa.agent.agent_server import AgentServer
import mfaalog

with patch.object(AgentServer, "context_sink", lambda: lambda cls: cls):
    import ocr_logger


class EventLogTests(unittest.TestCase):
    def setUp(self):
        self.sink = ocr_logger.OcrLogger()
        self.sink._threshold_cache.clear()
        # 不提供 tasker：任何回退到完整详情/截图的读取都会使测试失败。
        self.ctx = Mock(spec=["get_node_data"])
        self.ctx.get_node_data.return_value = {"recognition": {"param": {"threshold": .8}}}
        local_patch = patch.object(mfaalog, "diagnostic")
        gui_patch = patch.object(mfaalog, "warning")
        self.local = local_patch.start()
        self.gui = gui_patch.start()
        self.addCleanup(local_patch.stop)
        self.addCleanup(gui_patch.stop)

    def event(self, algorithm, raw, hit=True):
        self.sink.on_raw_notification(
            self.ctx, "Node.Recognition." + ("Succeeded" if hit else "Failed"),
            {"name": "测试节点", "reco_id": 1,
             "reco_details": {"algorithm": algorithm, "detail": raw}},
        )

    def test_ocr_hit_preserves_best_text_without_gui_or_detail_query(self):
        self.event("OCR", {"all": [{"text": "其他"}], "best": {"text": " 10 "}})
        self.local.assert_called_once_with("[OCR] 测试节点 命中: '10'")
        self.gui.assert_not_called()
        self.ctx.get_node_data.assert_not_called()

    def test_ocr_miss_keeps_deduplicated_text_preview(self):
        self.event("OCR", {"all": [{"text": t} for t in (" ", "A", " A ", "B", "C", "D")]}, False)
        self.local.assert_called_once_with("[OCR] 测试节点 未命中，识别到: A / B / C …共4条")
        self.gui.assert_not_called()

    def test_empty_ocr_miss_is_silent(self):
        self.event("OCR", {"all": [], "best": None}, False)
        self.local.assert_not_called()

    def test_template_hit_preserves_score_and_caches_threshold(self):
        for _ in range(2):
            self.event("TemplateMatch", {"all": [{"score": .9}], "best": {"score": .95}})
        self.assertEqual(self.local.call_args.args[0], "[模板] 测试节点 命中: 0.9500/0.8")
        self.ctx.get_node_data.assert_called_once_with("测试节点")
        self.gui.assert_not_called()

    def test_template_filter_runs_before_config_query(self):
        self.event("TemplateMatch", {"all": [{"score": .2}], "best": None}, False)
        self.local.assert_not_called()
        self.ctx.get_node_data.assert_not_called()

    def test_template_miss_all_mode_keeps_highest_score(self):
        with patch.object(ocr_logger, "_TEMPLATE_LOG_LEVEL", "all"):
            self.event("TemplateMatch", {"all": [{"score": .2}, {"score": .5}]}, False)
        self.local.assert_called_once_with("[模板] 测试节点 未命中: 最高0.5000/0.8")

    def test_missing_composite_or_unrelated_event_never_queries_detail(self):
        for msg in ("Node.Recognition.Starting", "Node.RecognitionNode.Failed",
                    "Node.Recognition.Succeeded", "Node.Action.Succeeded"):
            self.sink.on_raw_notification(self.ctx, msg, {"name": "没有详情"})
        self.event("And", [{"algorithm": "OCR"}])
        self.event("DirectHit", None)
        self.local.assert_not_called()
        self.gui.assert_not_called()
        self.ctx.get_node_data.assert_not_called()

    def test_pipeline_timeout_remains_visible(self):
        self.sink.on_raw_notification(self.ctx, "Node.PipelineNode.Failed", {"name": "助战"})
        self.gui.assert_called_once_with("[超时] 助战 识别超时（next 列表未命中任何节点）")
        self.sink.on_raw_notification(self.ctx, "Node.PipelineNode.Failed", {"name": "助战", "node_details": {}})
        self.assertEqual(self.gui.call_count, 1)


class DiagnosticFileTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "debug/recognition-123.log"
        self.saved_handler = mfaalog._diagnostic_handler
        mfaalog._diagnostic_handler = None
        self.path_patch = patch.object(mfaalog, "_DIAGNOSTIC_PATH", self.path)
        self.path_patch.start()
        self.addCleanup(self.restore_handler)
        toggle_patch = patch.object(mfaalog, "SHOW_VERBOSE_LOGS_IN_GUI", False)
        toggle_patch.start()
        self.addCleanup(toggle_patch.stop)

    def restore_handler(self):
        handler = mfaalog._diagnostic_handler
        if handler is not None and handler is not False:
            handler.close()
        mfaalog._diagnostic_handler = self.saved_handler
        self.path_patch.stop()

    def test_diagnostic_goes_only_to_file_with_timestamp_and_unicode(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            mfaalog.diagnostic("[SupportAction] 英灵头像 score=0.255")
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")
        self.assertRegex(self.path.read_text(encoding="utf8"),
                         r"\d{4}-\d{2}-\d{2} .*\[INFO\] \[SupportAction\] 英灵头像 score=0.255")

    def test_only_warning_error_and_focus_reach_gui_and_keep_local_levels(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            mfaalog.info("助战查找开始")
            mfaalog.debug("战斗状态细节")
            mfaalog.warning("缺少模板")
            mfaalog.error("截图失败")
            mfaalog.focus("任务1")
        self.assertNotIn("助战查找开始", stdout.getvalue())
        self.assertNotIn("战斗状态细节", stdout.getvalue())
        self.assertNotIn("info:", stdout.getvalue())
        self.assertNotIn("debug:", stdout.getvalue())
        self.assertIn("warn:⚠️ >>> 缺少模板", stdout.getvalue())
        self.assertIn("error:🔴 >>> 截图失败", stdout.getvalue())
        self.assertIn("focus:任务1", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")
        saved = self.path.read_text(encoding="utf8")
        for level, message in (("INFO", "助战查找开始"), ("DEBUG", "战斗状态细节"),
                               ("WARNING", "缺少模板"), ("ERROR", "截图失败")):
            self.assertIn(f"[{level}] {message}", saved)

    def test_multiline_info_does_not_leak_any_line_into_gui(self):
        message = "[ChaldeaPlan] 回合1\ninfo:战斗队伍数据\nerror:这里只是正文"
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            mfaalog.info(message)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")
        self.assertIn(message, self.path.read_text(encoding="utf8"))

    def test_verbose_switch_restores_info_debug_and_can_be_switched_off_again(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            with patch.object(mfaalog, "SHOW_VERBOSE_LOGS_IN_GUI", True):
                mfaalog.info("开启时普通日志")
                mfaalog.debug("开启时调试日志")
            mfaalog.info("关闭时普通日志")
            mfaalog.debug("关闭时调试日志")
        self.assertIn("info:🟣 >>> 开启时普通日志", stdout.getvalue())
        self.assertIn("debug:开启时调试日志", stdout.getvalue())
        self.assertNotIn("关闭时", stdout.getvalue())
        saved = self.path.read_text(encoding="utf8")
        for message in ("开启时普通日志", "开启时调试日志", "关闭时普通日志", "关闭时调试日志"):
            self.assertEqual(saved.count(message), 1)

    def test_rotation_is_bounded_and_exportable_as_log(self):
        with patch.object(mfaalog, "_DIAGNOSTIC_MAX_BYTES", 256):
            for i in range(30):
                mfaalog.diagnostic(f"record={i:02} " + "x" * 100)
        files = list(self.path.parent.iterdir())
        self.assertEqual(len(files), 3)
        self.assertTrue(all(p.suffix == ".log" for p in files))
        self.assertTrue(all(p.stat().st_size <= 256 for p in files))
        self.assertIn("record=29", self.path.read_text(encoding="utf8"))
        self.assertNotIn("record=00", "".join(p.read_text(encoding="utf8") for p in files))

    def test_concurrent_records_are_complete(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda i: mfaalog.diagnostic(f"记录{i:03}"), range(100)))
        lines = self.path.read_text(encoding="utf8").splitlines()
        self.assertEqual(len(lines), 100)
        self.assertEqual({line.split("[INFO] ", 1)[1] for line in lines},
                         {f"记录{i:03}" for i in range(100)})

    def test_file_open_failure_warns_once_without_reintroducing_info_flood(self):
        stdout = io.StringIO()
        with patch.object(mfaalog, "_DiagnosticFileHandler", side_effect=PermissionError("denied")), \
                contextlib.redirect_stdout(stdout):
            mfaalog.diagnostic("记录1")
            mfaalog.diagnostic("记录2")
            mfaalog.info("普通文本")
            mfaalog.debug("调试文本")
            mfaalog.warning("重要警告")
            mfaalog.error("重要错误")
        output = stdout.getvalue()
        self.assertEqual(output.count("本地日志无法写入"), 1)
        self.assertIn(str(self.path), output)
        for hidden in ("记录1", "记录2", "普通文本", "调试文本"):
            self.assertNotIn(hidden, output)
        self.assertIn("warn:⚠️ >>> 重要警告", output)
        self.assertIn("error:🔴 >>> 重要错误", output)

    def test_file_write_failure_does_not_recurse_or_leak_info(self):
        mfaalog.diagnostic("写入前")
        stdout = io.StringIO()
        with patch.object(mfaalog._diagnostic_handler, "shouldRollover", side_effect=OSError("disk full")), \
                contextlib.redirect_stdout(stdout):
            mfaalog.info("写入失败的普通记录")
            mfaalog.error("停止原因")
        self.assertEqual(stdout.getvalue().count("本地日志无法写入"), 1)
        self.assertNotIn("写入失败的普通记录", stdout.getvalue())
        self.assertIn("error:🔴 >>> 停止原因", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
