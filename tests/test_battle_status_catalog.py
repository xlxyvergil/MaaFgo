"""Catalog HUD recognition keeps identity evidence separate from shortlist scores."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]

from battle.perception.status_catalog import StatusCatalogRecognizer


IDS = ("9000001", "9000002", "9000003", "9000004")


def portrait(seed):
    # Smooth, distinct colour regions survive the screenshot's two resize steps.
    random = np.random.default_rng(seed)
    pixels = random.integers(20, 236, (24, 24, 3), dtype=np.uint8)
    pixels = cv2.resize(pixels, (256, 256), interpolation=cv2.INTER_CUBIC)
    return np.dstack((pixels, np.full((256, 256), 255, np.uint8)))


def save_portrait(directory, sid, pixels, name="status_servant_1.png"):
    target = directory / sid / name
    target.parent.mkdir(parents=True, exist_ok=True)
    success, encoded = cv2.imencode(".png", pixels)
    assert success
    target.write_bytes(encoded.tobytes())
    return target


def battle_frame(portraits, order=IDS[:3]):
    frame = np.full((720, 1280, 3), (35, 115, 45), dtype=np.uint8)
    for slot, sid in enumerate(order):
        crop = portraits[sid][55:130, 16:240]
        pixels = cv2.resize(crop[:, :, :3], (252, 84), interpolation=cv2.INTER_CUBIC)
        mask = cv2.resize(crop[:, :, 3], (252, 84), interpolation=cv2.INTER_NEAREST) >= 128
        x, y = 28 + slot * 318 + 8, 428
        target = frame[y:y + 84, x:x + 252]
        target[mask] = pixels[mask]
    return frame


class StatusCatalogTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="maafgo-hud-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.portraits = {sid: portrait(index + 11) for index, sid in enumerate(IDS)}
        for sid, pixels in self.portraits.items():
            save_portrait(self.directory, sid, pixels)

    def recognizer(self, **kwargs):
        return StatusCatalogRecognizer(roots=[self.directory], **kwargs)

    def assert_identities(self, observed, expected):
        self.assertEqual([slot.slot for slot in observed.slots], [1, 2, 3])
        self.assertEqual([slot.servant_id for slot in observed.slots], list(expected))
        self.assertTrue(all(slot.best_score >= .90 for slot in observed.slots))

    def test_reused_index_recognizes_new_frame_instead_of_reusing_slot_identity(self):
        recognizer = self.recognizer()
        self.assertEqual(recognizer.index_stats["identity_count"], 4)
        first = recognizer.detect(battle_frame(self.portraits))
        self.assert_identities(first, IDS[:3])
        changed = (IDS[2], IDS[0], IDS[1])
        second = recognizer.detect(battle_frame(self.portraits, changed))
        self.assert_identities(second, changed)
        self.assertTrue(all(slot.margin_scope == "shortlist" for slot in first.slots))
        self.assertTrue(all(slot.matched_template for slot in first.slots))

    def test_scaled_screenshot_and_bgra_input_keep_position_identity(self):
        recognizer = self.recognizer()
        frame = battle_frame(self.portraits)
        for image in (cv2.resize(frame, (1920, 1080), interpolation=cv2.INTER_CUBIC),
                      cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)):
            with self.subTest(shape=image.shape):
                self.assert_identities(recognizer.detect(image), IDS[:3])

    def test_full_audit_reports_catalog_margin_instead_of_shortlist_margin(self):
        result = self.recognizer().detect(battle_frame(self.portraits), full_audit=True)
        self.assert_identities(result, IDS[:3])
        for slot in result.slots:
            self.assertEqual(slot.margin_scope, "catalog")
            self.assertEqual(slot.candidate_count, 4)
            self.assertNotEqual(slot.runner_up_id, slot.servant_id)
            self.assertAlmostEqual(slot.margin, slot.best_score - slot.runner_up_score)

    def test_transparent_resource_edges_do_not_require_the_same_battlefield_background(self):
        for sid, pixels in self.portraits.items():
            pixels[:, :56, 3] = 0
            pixels[:, 224:, 3] = 0
            save_portrait(self.directory, sid, pixels)
        frame = battle_frame(self.portraits)
        self.assert_identities(self.recognizer().detect(frame), IDS[:3])

    def test_different_id_with_identical_portrait_is_rejected_by_identity_margin(self):
        save_portrait(self.directory, IDS[3], self.portraits[IDS[0]])
        result = self.recognizer().detect(battle_frame(self.portraits), full_audit=True)
        ambiguous = result.slots[0]
        self.assertIsNone(ambiguous.servant_id)
        self.assertGreater(ambiguous.best_score, .90)
        self.assertLess(ambiguous.margin, .05)
        self.assertTrue(ambiguous.reason)
        self.assertEqual({ambiguous.best_candidate_id, ambiguous.runner_up_id}, {IDS[0], IDS[3]})

    def test_crowded_coarse_boundary_does_not_certify_a_truncated_shortlist(self):
        for number in range(25):
            save_portrait(self.directory, str(9100000 + number), self.portraits[IDS[0]])
        result = self.recognizer().detect(battle_frame(self.portraits))
        self.assertIsNone(result.slots[0].servant_id)
        self.assertEqual(result.slots[0].reason, "coarse_unreliable")
        self.assertEqual(result.slots[0].margin_scope, "shortlist")

    def test_multiple_ascensions_and_explicit_aliases_do_not_become_runner_up_ids(self):
        canonical, alias = "505600", "505700"
        pixels = self.portraits.pop(IDS[0])
        for path in (self.directory / IDS[0]).iterdir():
            path.unlink()
        (self.directory / IDS[0]).rmdir()
        self.portraits[canonical] = pixels
        save_portrait(self.directory, canonical, pixels)
        save_portrait(self.directory, canonical, pixels, "status_servant_2.png")
        save_portrait(self.directory, alias, pixels)
        recognizer = self.recognizer()
        self.assertEqual(recognizer.index_stats["identity_count"], 4)
        result = recognizer.detect(battle_frame(self.portraits, (canonical, IDS[1], IDS[2])),
                                   full_audit=True)
        self.assert_identities(result, (canonical, IDS[1], IDS[2]))
        self.assertNotIn(result.slots[0].runner_up_id, (canonical, alias))
        self.assertGreater(result.slots[0].margin, .05)

    def test_blank_frame_keeps_unknown_and_does_not_reuse_previous_matches(self):
        recognizer = self.recognizer()
        self.assert_identities(recognizer.detect(battle_frame(self.portraits)), IDS[:3])
        for audit in (False, True):
            with self.subTest(full_audit=audit):
                result = recognizer.detect(np.zeros((720, 1280, 3), np.uint8),
                                           full_audit=audit)
                self.assertTrue(all(slot.servant_id is None for slot in result.slots))
                self.assertTrue(all(np.isfinite(slot.best_score) for slot in result.slots))
                self.assertTrue(all(slot.reason for slot in result.slots))

    def test_unusable_resource_roots_and_corrupt_templates_are_rejected(self):
        for root in (self.directory / "missing", self.directory / "empty"):
            if root.name == "empty":
                root.mkdir()
            with self.subTest(root=root), self.assertRaises(ValueError):
                StatusCatalogRecognizer(roots=[root])
        target = save_portrait(self.directory, "9000005", portrait(5))
        target.write_bytes(b"broken PNG")
        with self.assertRaises(ValueError):
            self.recognizer()

    def test_image_shape_dtype_and_threshold_errors_are_explicit(self):
        recognizer = self.recognizer()
        invalid = (None, np.zeros((720, 1000, 3), np.uint8),
                   np.zeros((720, 1280), np.uint8), np.zeros((720, 1280, 3), np.float32))
        for image in invalid:
            with self.subTest(shape=getattr(image, "shape", None)), self.assertRaises(ValueError):
                recognizer.detect(image)
        for kwargs in ({"min_score": float("nan")}, {"min_score": 1.1},
                       {"min_margin": -.1}, {"min_margin": float("inf")}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.recognizer(**kwargs)

    def test_cli_writes_machine_readable_results_for_multiple_images(self):
        frame = battle_frame(self.portraits)
        paths = [self.directory / "battle frame.png", self.directory / "next.png"]
        for path in paths:
            path.write_bytes(cv2.imencode(".png", frame)[1].tobytes())
        output = self.directory / "report.json"
        completed = subprocess.run(
            [sys.executable, "-X", "utf8", str(ROOT / "tools/recognize_battle_frontline.py"),
             *(str(path) for path in paths), "--status-root", str(self.directory),
             "--output", str(output)],
            cwd=ROOT, encoding="utf-8", capture_output=True, timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(output.read_text("utf-8"))
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["index"]["identity_count"], 4)
        self.assertEqual(len(report["results"]), 2)
        for result in report["results"]:
            self.assertEqual([slot["servant_id"] for slot in result["slots"]], list(IDS[:3]))

    def test_cli_missing_image_and_resource_root_fail_without_traceback(self):
        frame_path = self.directory / "frame.png"
        frame_path.write_bytes(cv2.imencode(".png", battle_frame(self.portraits))[1].tobytes())
        for image, resource in ((self.directory / "missing.png", self.directory),
                                (frame_path, self.directory / "missing-resources")):
            with self.subTest(image=image, resource=resource):
                completed = subprocess.run(
                    [sys.executable, "-X", "utf8", str(ROOT / "tools/recognize_battle_frontline.py"),
                     str(image), "--status-root", str(resource)],
                    cwd=ROOT, encoding="utf-8", capture_output=True, timeout=30,
                )
                self.assertEqual(completed.returncode, 2)
                self.assertIn("战斗头像识别未能完成", completed.stderr)
                self.assertNotIn("Traceback", completed.stderr)

    def test_cli_refuses_to_overwrite_an_input_image_with_json(self):
        frame_path = self.directory / "original.png"
        original = cv2.imencode(".png", battle_frame(self.portraits))[1].tobytes()
        frame_path.write_bytes(original)
        completed = subprocess.run(
            [sys.executable, "-X", "utf8", str(ROOT / "tools/recognize_battle_frontline.py"),
             str(frame_path), "--status-root", str(self.directory), "--output", str(frame_path)],
            cwd=ROOT, encoding="utf-8", capture_output=True, timeout=30,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("输出文件与输入截图相同", completed.stderr)
        self.assertEqual(frame_path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
