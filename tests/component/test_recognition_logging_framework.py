"""真实框架事件的本地回归；只识别内存图片，不连接设备或点击。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import maa

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]
from maa.agent.agent_server import AgentServer
from maa.controller import CustomController
from maa.define import LoggingLevelEnum
from maa.library import Library
from maa.resource import Resource
from maa.tasker import Tasker
import mfaalog

with patch.object(AgentServer, "context_sink", lambda: lambda cls: cls):
    import ocr_logger


class ImageController(CustomController):
    def connect(self):
        return True

    def request_uuid(self):
        return "recognition-logging-memory-only"

    def get_features(self):
        return 0

    def screencap(self):
        return self.image.copy()


class FrameworkLoggingTests(unittest.TestCase):
    def test_real_template_event_logs_score_without_full_detail_query(self):
        binary = Path(os.environ.get("MAAFW_BINARY_PATH", Path(maa.__file__).parent / "bin"))
        if not binary.exists():
            self.skipTest("set MAAFW_BINARY_PATH for native framework integration")
        Library.open(binary)
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)
        image = np.random.default_rng(42).integers(0, 255, (720, 1280, 3), dtype=np.uint8)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "pipeline").mkdir()
            (root / "image").mkdir()
            cv2.imwrite(str(root / "image/marker.png"), image[200:248, 300:348])
            (root / "pipeline/log.json").write_text(json.dumps({
                "日志模板测试": {
                    "recognition": {"type": "TemplateMatch", "param": {
                        "template": "marker.png", "threshold": .8}},
                    "post_delay": 0,
                },
            }, ensure_ascii=False), encoding="utf8")
            resource = Resource()
            resource.use_cpu()
            self.assertTrue(resource.post_bundle(root).wait().succeeded)
            controller = ImageController()
            controller.image = image
            self.assertTrue(controller.post_connection().wait().succeeded)
            tasker = Tasker()
            self.assertTrue(tasker.bind(resource, controller))
            sink = ocr_logger.OcrLogger()
            sink._threshold_cache.clear()
            tasker.add_context_sink(sink)
            with patch.object(Tasker, "get_recognition_detail", side_effect=AssertionError("must not copy screenshot")) as query, \
                    patch.object(mfaalog, "diagnostic") as local, patch.object(mfaalog, "info") as gui:
                self.assertTrue(tasker.post_task("日志模板测试").wait().succeeded)
            query.assert_not_called()
            gui.assert_not_called()
            local.assert_called_once_with("[模板] 日志模板测试 命中: 1.0000/0.8")


if __name__ == "__main__":
    unittest.main()
