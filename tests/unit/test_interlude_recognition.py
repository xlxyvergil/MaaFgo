"""幕间卡片亮度、逐行文字归属与空扫描语义；不连接设备。"""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]
from interlude_recognition import row_candidates, scan_entries


def card_image(*, value=180, green=False):
    image = np.zeros((720, 1280, 3), np.uint8)
    hue = 65 if green else 16
    color = cv2.cvtColor(np.uint8([[[hue, 190, value]]]), cv2.COLOR_HSV2BGR)[0, 0]
    image[120:264, 624:1234] = color
    return image


class Context:
    def __init__(self, texts=(), *, clear=False):
        self.texts, self.clear = texts, clear
        self.tasker = NS(stopping=False)
        self.calls = []

    def clone(self):
        return self

    def run_recognition(self, name, image, pipeline_override=None):
        roi = pipeline_override[name]["recognition"]["param"]["roi"]
        self.calls.append((name, roi))
        if name == "Free-完成角标":
            return NS(hit=self.clear)
        items = [NS(text=text, box=NS(x=780, y=135 + index * 23, w=130, h=19), score=.99)
                 for index, text in enumerate(self.texts)]
        return NS(all_results=items, hit=bool(items))


class InterludeRecognitionTests(unittest.TestCase):
    def test_dark_cards_remain_rows_but_are_never_available(self):
        image = card_image(value=100)
        report = scan_entries(Context(["任务名称", "Interlude", "AP18"]), image)
        self.assertEqual(len(row_candidates(image)), 1)
        self.assertEqual(report["locked_count"], 1)
        self.assertEqual(report["uncertain_count"], 0)
        self.assertEqual(report["entries"], [])

    def test_dark_brown_without_task_evidence_is_unknown_instead_of_locked(self):
        for map_mode in (False, True):
            with self.subTest(map_mode=map_mode):
                report = scan_entries(Context(), card_image(value=100), map_mode=map_mode)
                self.assertEqual(report["entries"], [])
                self.assertEqual(report["locked_count"], 0)
                self.assertGreater(report["uncertain_count"], 0)
                self.assertFalse(report["empty_confirmed"])
                if not map_mode:
                    self.assertEqual(report["row_count"], 1)
                    self.assertEqual(report["rows"][0]["status"], "uncertain")

    def test_bright_header_separates_from_merged_brown_map_background(self):
        image = np.zeros((720, 1280, 3), np.uint8)
        image[95:128, 624:1234] = (20, 45, 80)
        image[128:272, 624:1234] = (40, 110, 195)
        candidates = row_candidates(image)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["row_box"], [624, 128, 610, 144])
        self.assertEqual(candidates[0]["brightness"], 195)

    def test_bright_stage_and_direct_have_different_kinds(self):
        for texts, kind in ((["任务名称", "前往故事的舞台"], "stage"),
                            (["任务名称", "Interlude", "AP18"], "direct")):
            with self.subTest(kind=kind):
                report = scan_entries(Context(texts), card_image())
                self.assertEqual(report["entries"][0]["kind"], kind)
                self.assertEqual(report["entries"][0]["title"], "任务名称")
                self.assertEqual(report["entries"][0]["box"], [950, 194, 1, 1])

    def test_title_prefers_confident_real_text_over_earlier_map_fragments(self):
        class TitleContext(Context):
            def run_recognition(self, name, image, pipeline_override=None):
                result = super().run_recognition(name, image, pipeline_override)
                result.all_results = [
                    NS(text="上闪机行", score=.641, box=NS(x=780, y=124, w=80, h=15)),
                    NS(text="lnteriu", score=.99999, box=NS(x=780, y=127, w=140, h=15)),
                    NS(text="任务进行度", score=.99999, box=NS(x=925, y=130, w=90, h=18)),
                    NS(text="神弓的真价", score=.999733, box=NS(x=777, y=145, w=96, h=22)),
                    NS(text="AP18", score=.9999, box=NS(x=780, y=210, w=70, h=20)),
                ]
                return result
        report = scan_entries(TitleContext(), card_image())
        self.assertEqual(report["entries"][0]["title"], "神弓的真价")
        self.assertEqual(report["entries"][0]["title_box"], [777, 145, 96, 22])

    def test_ap_zero_and_ocr_letter_o_are_available(self):
        for ap in ("AP0", "APO"):
            with self.subTest(ap=ap):
                report = scan_entries(Context(["任务名称", "任务进行度", ap]), card_image())
                self.assertEqual(report["entries"][0]["kind"], "direct")

    def test_spaced_and_ambiguous_english_marker_is_tolerated(self):
        for marker in ("I n t e r l u d e", "lnterlude", "lnterlu e"):
            with self.subTest(marker=marker):
                report = scan_entries(Context(["任务名称", marker, "AP18"]),
                                      card_image(), map_mode=True)
                self.assertEqual(report["entries"][0]["kind"], "map")

    def test_temporary_relaxation_text_does_not_lock_a_bright_card(self):
        report = scan_entries(Context(["任务名称", "任务进行度", "AP0", "开放条件缓和中"]),
                              card_image())
        self.assertEqual(len(report["entries"]), 1)
        self.assertEqual(report["locked_count"], 0)

    def test_explicit_unlock_requirement_rejects_even_a_bright_card(self):
        report = scan_entries(Context(["通关终局特异点后开放", "前往故事的舞台"]), card_image())
        self.assertEqual(report["entries"], [])
        self.assertEqual(report["locked_count"], 1)

    def test_map_requires_interlude_marker_in_addition_to_ap(self):
        report = scan_entries(Context(["任务名称", "任务进行度", "AP18"]),
                              card_image(), map_mode=True)
        self.assertEqual(report["entries"], [])
        self.assertEqual(report["uncertain_count"], 1)
        self.assertFalse(report["empty_confirmed"])

    def test_zero_rows_and_failed_ocr_are_unknown(self):
        for image in (np.zeros((720, 1280, 3), np.uint8), card_image()):
            for map_mode in (False, True):
                with self.subTest(brown=bool(image.any()), map_mode=map_mode):
                    report = scan_entries(Context(), image, map_mode=map_mode)
                    self.assertEqual(report["entries"], [])
                    self.assertGreater(report["uncertain_count"], 0)
                    self.assertFalse(report["empty_confirmed"])

    def test_green_free_card_is_positive_empty_map_evidence(self):
        report = scan_entries(Context(["自由", "关卡", "AP15"]),
                              card_image(green=True), map_mode=True)
        self.assertEqual(report["entries"], [])
        self.assertEqual(report["row_count"], 0)
        self.assertEqual(report["free_count"], 1)
        self.assertEqual(report["uncertain_count"], 0)
        self.assertTrue(report["empty_confirmed"])

    def test_clear_check_is_scoped_to_current_row_lower_edge(self):
        context = Context(["任务名称", "Interlude", "AP18"], clear=True)
        report = scan_entries(context, card_image(), map_mode=True)
        self.assertEqual(report["entries"], [])
        self.assertEqual(report["completed_count"], 1)
        self.assertTrue(report["empty_confirmed"])
        self.assertIn(("Free-完成角标", [624, 220, 200, 75]), context.calls)

    def test_reference_color_location_scales_with_controller_frame(self):
        image = cv2.resize(card_image(), (1920, 1080), interpolation=cv2.INTER_NEAREST)
        candidates = row_candidates(image)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["row_box"], [936, 180, 915, 216])


if __name__ == "__main__":
    unittest.main()
