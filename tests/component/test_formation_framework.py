"""真实 MaaFramework + 内存控制器集成测试；不是实机识别验收。

使用 MAAFW_BINARY_PATH 指向框架库；本机默认寻找 install-mxu/maafw。
仅控制合成图像；所有输入方法都拒绝执行。
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from formation_test_support import ROOT, formation as f, battle_action as a
from battle.runtime.formation_session import context_session, sessions
from battle.core.models import FormationSlot, VERIFIED_FORMATION_SOURCE
from maa.controller import CustomController
from maa.custom_action import CustomAction
from maa.define import LoggingLevelEnum
from maa.library import Library
from maa.resource import Resource
from maa.tasker import Tasker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
import test_formation_identity as fixtures


class MemoryController(CustomController):
    def __init__(self, image, fail_capture=False):
        self.image = image
        self.fail_capture = fail_capture
        self.frames = 0
        self.inputs = 0
        super().__init__()

    def connect(self):
        return True

    def request_uuid(self):
        return "formation-memory-test"

    def get_features(self):
        return 0

    def screencap(self):
        self.frames += 1
        if self.fail_capture:
            return np.empty((0, 0, 3), dtype=np.uint8)
        image = self.image.copy()
        image[0, 0] = (self.frames % 255, 0, 0)
        return image

    def _reject(self, *args):
        self.inputs += 1
        return False

    start_app = stop_app = click = swipe = touch_down = touch_move = touch_up = _reject
    click_key = input_text = key_down = key_up = _reject


class FrameworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        binary = Path(os.environ.get("MAAFW_BINARY_PATH", ROOT / "install-mxu/maafw"))
        if not binary.exists():
            raise unittest.SkipTest("set MAAFW_BINARY_PATH for the native framework test")
        Library.open(binary.resolve())
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)
        print(f"Framework integration version: {Library.version()}")

    def setUp(self):
        sessions.stop_owner("")
        self.addCleanup(sessions.stop_owner, "")
        self.fixture = fixtures.IdentityTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def run_pipeline(self, page_ok, fail_capture=False, verified_slots=None):
        source = json.loads((ROOT / "assets/resource/base/pipeline/自动编队.json").read_text(encoding="utf-8"))
        nodes = {n: v for n, v in source.items() if n.startswith("编队身份-")}
        nodes.update({
            "root": {"action": {"type": "Custom", "param": {
                "custom_action": "begin_native_formation_session"}},
                "next": ["进本-点击队伍确认"], "on_error": []},
            "进本-点击队伍确认": {"next": ["进本-实际点击队伍确认"]},
            "进本-实际点击队伍确认": {"action": {"type": "Custom", "param": {
                "custom_action": "observe_snapshot"}}, "on_error": []},
        })
        nodes["编队身份-采集"]["attach"]["calibration"] = {
            "verified": True, "calibration_id": "synthetic-test-only",
            "identity_threshold": .95, "identity_margin": .25,
        }
        if verified_slots is not None:
            nodes["root"]["next"] = ["seed_verified"]
            nodes["seed_verified"] = {"action": {"type": "Custom", "param": {
                "custom_action": "seed_verified"}}, "next": ["进本-点击队伍确认"], "on_error": []}
        for node in nodes.values():
            node.update(rate_limit=1, post_delay=0, timeout=200)
        with tempfile.TemporaryDirectory() as temp:
            pipeline = Path(temp, "pipeline")
            pipeline.mkdir()
            (pipeline / "test.json").write_text(json.dumps(nodes, ensure_ascii=False), encoding="utf-8")
            resource = Resource()
            resource.use_cpu()
            self.assertTrue(resource.post_bundle(temp).wait().succeeded)
            observed, received_images, later_screenshots = [], [], []

            class Observe(CustomAction):
                def run(self, context, argv):
                    root, token = context_session(context)
                    observed.append(sessions.read(root, token)[0])
                    # 生产采集失败会清空快照后继续通用策略，后续进本/战斗
                    # 仍需新的截图。不能用始终成功的替身替代这个观测边界。
                    controller = context.tasker.controller
                    captured = controller.post_screencap().wait().succeeded
                    image = controller.cached_image if captured else None
                    visible = captured and image is not None and image.size > 0
                    later_screenshots.append(bool(visible))
                    return bool(visible)

            class SeedVerified(CustomAction):
                def run(self, context, argv):
                    root, token = context_session(context)
                    revision = sessions.begin_update(root, token)
                    sessions.publish_verified(root, token, revision, verified_slots)
                    return True

            resource.register_custom_action("begin_native_formation_session", a.BeginNativeFormationSession())
            resource.register_custom_action("capture_initial_formation", f.CaptureInitialFormation())
            resource.register_custom_action("observe_snapshot", Observe())
            resource.register_custom_action("seed_verified", SeedVerified())
            resource.register_custom_recognition("formation_identity_frame", f.FormationIdentityFrame())
            image = self.fixture.image if page_ok else np.zeros_like(self.fixture.image)
            controller = MemoryController(image, fail_capture)
            self.assertTrue(controller.post_connection().wait().succeeded)
            tasker = Tasker()
            self.assertTrue(tasker.bind(resource, controller))
            tasker.add_sink(a.FormationTaskLifecycle())
            reader_class = f.FormationIdentityReader
            read_frame = reader_class.read_frame

            def observe_frame(reader, image, **kwargs):
                received_images.append(int(image[0, 0, 0]))
                return read_frame(reader, image, **kwargs)

            def reader_factory(roots, calibration):
                return reader_class([self.fixture.root], calibration, self.fixture.catalog)

            with patch.object(reader_class, "acquire", side_effect=reader_factory), \
                 patch.object(reader_class, "read_frame", observe_frame):
                job = tasker.post_task("root")
                deadline = time.monotonic() + 15
                while not job.status.done and time.monotonic() < deadline:
                    time.sleep(.01)
                if not job.status.done:
                    tasker.post_stop()
                    self.fail("framework integration timed out")
                self.assertEqual(job.status.succeeded, not fail_capture)
            self.assertEqual(controller.inputs, 0)
            if fail_capture:
                self.assertEqual(received_images, [])
                # 旧 SDK 会在截图失败时直接阻止后续动作；新 SDK 允许
                # DirectHit 动作继续，因此替身必须自行验证下一帧并失败。
                self.assertLessEqual(len(observed), 1)
                self.assertEqual(later_screenshots, [False] * len(observed))
            else:
                complete = verified_slots is not None and all(s.status != "unknown" for s in verified_slots)
                self.assertEqual(len(received_images), 0 if complete else 1)
                self.assertEqual(len(observed), 1)
                self.assertEqual(later_screenshots, [True])
            if page_ok and not fail_capture:
                snapshot = observed[0]
                self.assertEqual(snapshot.task_id, job.job_id)
                self.assertTrue(all(slot.servant_id == "1000" for slot in snapshot.slots))
                if verified_slots is not None:
                    self.assertEqual(snapshot.source, VERIFIED_FORMATION_SOURCE)
                self.assertFalse(sessions.valid(snapshot.task_id, snapshot.session_id))
            elif observed:
                self.assertIsNone(observed[0])
            # Context 覆盖不得污染共享 Resource/BBC 的默认路径。
            gate = resource.get_node_data("进本-点击队伍确认")["next"]
            self.assertEqual(gate[0]["name"] if isinstance(gate[0], dict) else gate[0],
                             "进本-实际点击队伍确认")

    def test_fresh_frames_and_root_identity_through_nested_run_task(self):
        self.run_pipeline(True)

    def test_failed_page_reaches_start_boundary_without_snapshot(self):
        self.run_pipeline(False)

    def test_screenshot_failure_still_stops_when_later_battle_cannot_observe(self):
        self.run_pipeline(True, fail_capture=True)

    def test_verified_complete_result_skips_identity_callback_through_real_context(self):
        known = tuple(FormationSlot(i, "identified", "1000", is_support=False) for i in range(1, 7))
        self.run_pipeline(True, verified_slots=known)

    def test_verified_partial_result_fills_unknown_through_real_context(self):
        known = tuple(FormationSlot(i, "identified", "1000", is_support=False)
                      if i != 6 else FormationSlot(i, "unknown", is_support=False)
                      for i in range(1, 7))
        self.run_pipeline(True, verified_slots=known)


if __name__ == "__main__":
    unittest.main()
