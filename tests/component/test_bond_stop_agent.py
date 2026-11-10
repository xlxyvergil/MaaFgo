"""真实 Agent 的观察窗口及跨回调完成状态；仅内存截图，不连接设备。"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

import maa
import numpy as np
from maa.agent_client import AgentClient
from maa.controller import CustomController
from maa.define import LoggingLevelEnum
from maa.library import Library
from maa.resource import Resource
from maa.tasker import Tasker

ROOT = Path(__file__).resolve().parents[2]
HELPER = Path(__file__).with_name("helpers") / "bond_stop_agent.py"


class MemoryController(CustomController):
    def __init__(self):
        self.frames = 0
        self.inputs = 0
        super().__init__()

    def connect(self): return True
    def request_uuid(self): return "bond-agent-memory-test"
    def get_features(self): return 0

    def screencap(self):
        self.frames += 1
        image = np.zeros((720, 1280, 3), np.uint8)
        # 新回调取得不同帧，验证完成状态不依赖图片摘要，也不重启计时。
        image[0, 0, 0] = self.frames % 255
        return image

    def reject(self, *args):
        self.inputs += 1
        return False

    start_app = stop_app = click = swipe = touch_down = touch_move = touch_up = reject
    click_key = input_text = key_down = key_up = reject


class BondStopAgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binary = Path(os.environ.get("MAAFW_BINARY_PATH", Path(maa.__file__).parent / "bin")).resolve()
        Library.open(cls.binary)
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)

    def run_case(self, scenario):
        source = json.loads((ROOT / "assets/resource/base/pipeline/原生自动战斗调度.json").read_text("utf-8"))
        nodes = {name: copy.deepcopy(source[name]) for name in (
            "结算-羁绊满停止", "结算-羁绊观察间隔", "结算-羁绊停止出口",
            "结算-羁绊停止异常出口")}
        guard = nodes["结算-羁绊满停止"]
        guard["enabled"] = True
        guard["recognition"]["param"]["custom_recognition"] = "record_bond_guard"
        guard["attach"].update(observation_attempts=80, observation_timeout_ms=100)
        nodes.update({
            "原生自动战斗-羁绊停止配置": {"attach": {"native_bond_stop_mode": "all"}},
            "root": {"action": {"type": "Custom", "param": {
                "custom_action": "victory_handoff"}}, "next": ["dispatch"]},
            "dispatch": {"next": ["结算-羁绊满停止", "tail"]},
            "tail": {"action": {"type": "Custom", "param": {"custom_action": "tail"}}},
        })
        for node in nodes.values():
            node.update(pre_delay=0, post_delay=0, rate_limit=1, timeout=2000)
        nodes["结算-羁绊观察间隔"]["pre_delay"] = 10
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            pipeline = directory / "pipeline"
            pipeline.mkdir()
            (pipeline / "test.json").write_text(json.dumps(nodes, ensure_ascii=False), encoding="utf-8")
            report = directory / "report.json"
            resource = Resource()
            resource.use_cpu()
            self.assertTrue(resource.post_bundle(temp).wait().succeeded)
            controller = MemoryController()
            self.assertTrue(controller.post_connection().wait().succeeded)
            tasker = Tasker()
            self.assertTrue(tasker.bind(resource, controller))
            client = AgentClient()
            client.set_timeout(5000)
            self.assertTrue(client.bind(resource))
            with (directory / "agent.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen(
                    [sys.executable, "-X", "utf8", str(HELPER), client.identifier,
                     scenario, str(report), str(self.binary)],
                    cwd=temp, stdout=log, stderr=subprocess.STDOUT,
                )
                try:
                    self.assertTrue(client.connect(), (directory / "agent.log").read_text("utf-8", errors="replace"))
                    job = tasker.post_task("root")
                    deadline = time.monotonic() + 15
                    while not job.status.done and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertTrue(job.status.done, "Agent guard did not finish")
                    self.assertTrue(job.status.succeeded)
                finally:
                    tasker.post_stop().wait()
                    client.disconnect()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        process.wait(timeout=5)
            output = (directory / "agent.log").read_text("utf-8", errors="replace")
            self.assertEqual(process.returncode, 0, output)
            self.assertTrue(report.exists(), output)
            result = json.loads(report.read_text("utf-8"))
            self.assertEqual(result["seed_reason"], "", output)
            if scenario != "window_max":
                self.assertGreaterEqual(result["reads"]["seed"], 2, output)
                self.assertGreaterEqual(result["seed_elapsed"], .1, output)
                self.assertTrue(result["seed_complete"], output)
            self.assertEqual(controller.inputs, 0, output)
            self.assertNotIn("access violation", output)
            return result

    def test_negative_result_is_not_rechecked_in_a_later_agent_callback(self):
        result = self.run_case("negative")
        self.assertEqual(result["guard_reason"], "")
        self.assertEqual(result["reads"]["guard"], 0)
        self.assertTrue(result["tail"])

    def test_max_appearing_inside_the_observation_window_stops_before_tail(self):
        result = self.run_case("window_max")
        self.assertEqual(result["guard_reason"], "bond_max")
        self.assertEqual(result["reads"]["guard"], 2)
        self.assertFalse(result["tail"])

    def test_changed_frame_with_late_max_does_not_restart_completed_window(self):
        result = self.run_case("late_max")
        self.assertEqual(result["guard_reason"], "")
        self.assertEqual(result["reads"]["guard"], 0)
        self.assertTrue(result["tail"])

    def test_new_battle_clears_completion_and_checks_max_again(self):
        result = self.run_case("next_battle_max")
        self.assertEqual(result["guard_reason"], "bond_max")
        self.assertEqual(result["reads"]["guard"], 2)
        self.assertFalse(result["tail"])


if __name__ == "__main__":
    unittest.main()
