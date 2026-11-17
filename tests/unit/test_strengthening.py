"""强化本选关归属、跳过锁定/未知卡片及跨小关调度边界。"""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]
from strengthening_recognition import scan_strengthening
from strengthening_action import StrengtheningRunner
from main_story_action import StoryStopped


class OCRContext:
    def __init__(self, texts):
        self.texts = texts
        self.tasker = NS(stopping=False)

    def clone(self):
        return self

    def run_recognition(self, name, image, pipeline_override=None):
        return NS(all_results=[NS(text=t, score=.99, box=[780, 135 + i * 23, 150, 20])
                               for i, t in enumerate(self.texts)])


def card(value=180):
    image = np.zeros((720, 1280, 3), np.uint8)
    image[120:264, 624:1234] = cv2.cvtColor(np.uint8([[[16, 190, value]]]), cv2.COLOR_HSV2BGR)[0, 0]
    return image


class StrengtheningRecognitionTests(unittest.TestCase):
    def scan(self, texts, value=180):
        return scan_strengthening(OCRContext(texts), card(value))

    def test_accepts_zero_ap_and_spaced_english(self):
        report = self.scan(["强化关卡 织田信长", "S t r e n g t h e n Q u e s t", "任务进行度", "AP0"])
        self.assertEqual(len(report["entries"]), 1)
        self.assertEqual(report["entries"][0]["box"], [950, 194, 1, 1])

    def test_actual_ap_is_preserved_for_zero_discounted_and_regular_costs(self):
        for ap in (0, 5, 11, 20, 23, 40):
            for title, progress in (("强化关卡 测试", "任务进行度"), ("強化クエスト テスト", "クエスト進行度")):
                with self.subTest(ap=ap, title=title):
                    report = self.scan([title, progress, f"AP{ap}"])
                    self.assertEqual(report["entries"][0]["ap_cost"], ap)

    def test_missing_ap_is_uncertain_not_zero(self):
        for ap in ("AP", "AP?", "消費AP"):
            report = self.scan(["強化クエスト テスト", "クエスト進行度", ap])
            self.assertFalse(report["entries"])
            self.assertIsNone(report["rows"][0]["ap_cost"])
            self.assertEqual(report["uncertain_count"], 1)

    def test_jp_locked_conditions_and_temporary_relaxation_are_distinct(self):
        for condition in ("最終再臨後に開放", "絆Lv.4で開放", "未開放"):
            report = self.scan(["強化クエスト テスト", "クエスト進行度", "AP23", condition])
            self.assertFalse(report["entries"])
            self.assertEqual(report["locked_count"], 1)
        report = self.scan(["強化クエスト テスト", "クエスト進行度", "AP23", "開放条件緩和中"])
        self.assertEqual(report["entries"][0]["ap_cost"], 23)

    def test_fullwidth_ap_digits_are_parsed(self):
        report = self.scan(["強化クエスト テスト", "クエスト進行度", "ＡＰ２３"])
        self.assertEqual(report["entries"][0]["ap_cost"], 23)

    def test_unrelated_ocr_digit_does_not_append_to_ap_cost(self):
        report = self.scan(["強化クエスト テスト", "クエスト進行度", "AP23", "1"])
        self.assertEqual(report["entries"][0]["ap_cost"], 23)

    def test_conflicting_costs_are_uncertain_instead_of_guessed(self):
        report = self.scan(["強化クエスト テスト", "クエスト進行度", "AP11", "AP23"])
        self.assertFalse(report["entries"])
        self.assertIsNone(report["rows"][0]["ap_cost"])
        self.assertEqual(report["uncertain_count"], 1)

    def test_rejects_free_and_interlude_even_with_ap_and_progress(self):
        for marker in ("Free Quest", "Interlude"):
            report = self.scan([marker, "任务进行度", "AP10"])
            self.assertFalse(report["entries"])
            self.assertEqual(report["uncertain_count"], 1)

    def test_dark_or_explicitly_locked_cards_are_not_selected(self):
        for texts, brightness in ((["强化关卡 测试", "任务进行度", "AP0"], 90),
                                  (["强化关卡 测试", "任务进行度", "AP0", "灵基再临后开放"], 180)):
            report = self.scan(texts, brightness)
            self.assertFalse(report["entries"])
            self.assertEqual(report["locked_count"], 1)
            self.assertEqual(report["uncertain_count"], 0)

    def test_unknown_and_blank_are_never_treated_as_finished(self):
        report = self.scan([])
        self.assertEqual(report["uncertain_count"], 1)
        report = scan_strengthening(OCRContext([]), np.zeros((720, 1280, 3), np.uint8))
        self.assertEqual(report["uncertain_count"], 1)

    def test_bottom_partial_card_is_not_clicked(self):
        image = np.zeros((720, 1280, 3), np.uint8)
        image[650:720, 624:1234] = card()[120, 800]
        self.assertFalse(scan_strengthening(OCRContext(["强化关卡 测试", "任务进行度", "AP0"]), image)["entries"])


class StrengtheningDispatchTests(unittest.TestCase):
    def runner(self, targets, limit=2):
        runner = StrengtheningRunner.__new__(StrengtheningRunner)
        runner.completed = runner.battles = 0
        runner.limit = limit
        runner._first_scan = True
        runner.list_path = "迦勒底之门 → 强化任务"
        runner.panel = Mock(return_value=True)
        runner.reco = Mock(return_value=None)
        runner._dialogue_choices = {}
        runner.ctx = NS(get_task_job=lambda: NS(job_id=123456))
        for method in ("reset_restriction_state", "_ensure_list", "frame", "enter", "fight", "settle"):
            setattr(runner, method, Mock())
        runner.locate = Mock(side_effect=targets)
        return runner

    def scan_runner(self, *, uncertain):
        runner = StrengtheningRunner.__new__(StrengtheningRunner)
        runner._first_scan = False
        runner.frame = Mock(return_value=card())
        runner._ensure_list = Mock()
        runner.pause = Mock()
        runner.action = Mock()
        report = {"row_count": 1, "uncertain_count": int(uncertain), "locked_count": 1}
        runner.reco = Mock(side_effect=lambda name, image:
                           NS(hit=True, best_result=NS(detail=report)) if name == "强化-条目扫描" else None)
        return runner

    def test_locked_list_at_bottom_finishes_after_scrolling_and_recheck(self):
        runner = self.scan_runner(uncertain=False)
        with patch("strengthening_action.same_list", return_value=True):
            self.assertIsNone(runner.locate())
        runner.action.assert_called_once_with("强化-列表向下滑动")

    def test_unknown_list_at_bottom_stops_instead_of_claiming_completion(self):
        runner = self.scan_runner(uncertain=True)
        with patch("strengthening_action.same_list", return_value=True), \
             self.assertRaisesRegex(StoryStopped, "不能确定已全部完成"):
            runner.locate()

    def test_list_search_stops_when_page_changes(self):
        runner = self.scan_runner(uncertain=False)
        runner._ensure_list.side_effect = [None, StoryStopped("已离开强化任务列表")]
        with self.assertRaisesRegex(StoryStopped, "已离开"):
            runner.locate()
        runner.action.assert_called_once_with("强化-列表向下滑动")

    def test_returns_to_list_between_two_battles_and_stops_at_limit(self):
        first, second = object(), object()
        runner = self.runner([first, second])
        steps = []
        runner.enter.side_effect = lambda target: steps.append(("enter", target))
        runner.settle.side_effect = lambda: steps.append(("returned", runner.completed))
        with patch("strengthening_action.bond_stops.battle_started") as budget:
            self.assertIn("上限", runner.run())
            self.assertEqual(budget.call_args.kwargs["another_battle_planned"], False)
        self.assertEqual(steps, [("enter", first), ("returned", 0), ("enter", second), ("returned", 1)])
        self.assertEqual(runner.completed, 2)
        self.assertEqual(runner.locate.call_count, 2)

    def test_no_available_entries_finishes_without_battle(self):
        runner = self.runner([None])
        self.assertIn("没有可挑战", runner.run())
        runner.fight.assert_not_called()

    def test_settlement_failure_does_not_count_or_start_another_battle(self):
        runner = self.runner([object(), object()])
        runner.settle.side_effect = StoryStopped("未确认返回强化列表")
        with patch("strengthening_action.bond_stops.battle_started"), self.assertRaises(StoryStopped):
            runner.run()
        self.assertEqual(runner.completed, 0)
        self.assertEqual(runner.locate.call_count, 1)

    def test_support_and_formation_start_finish_current_quest_before_scanning(self):
        for node in ("主线-助战界面", "进本-队伍确认"):
            with self.subTest(entry=node):
                target = object()
                runner = self.runner([target])
                runner.panel.return_value = False
                runner.reco.side_effect = lambda name, _image: object() if name == node else None
                steps = []
                runner.fight.side_effect = lambda: steps.append("fight")
                runner.settle.side_effect = lambda: steps.append("settle")
                def locate():
                    self.assertEqual(runner.completed, 1)
                    self.assertTrue(runner._first_scan)
                    steps.append("scan")
                    return target
                runner.locate.side_effect = locate
                runner.enter.side_effect = lambda _target: steps.append("enter")
                with patch("strengthening_action.bond_stops.battle_started"):
                    runner.run()
                self.assertEqual(steps, ["fight", "settle", "scan", "enter", "fight", "settle"])
                self.assertEqual(runner.completed, 2)
                runner.enter.assert_called_once_with(target)

    def test_selected_quest_counts_towards_limit_without_selecting_another(self):
        runner = self.runner([], limit=1)
        runner.panel.return_value = False
        runner.reco.side_effect = lambda name, _image: object() if name == "进本-队伍确认" else None
        with patch("strengthening_action.bond_stops.battle_started") as budget:
            self.assertIn("完成 1", runner.run())
        runner.locate.assert_not_called()
        runner.enter.assert_not_called()
        runner.fight.assert_called_once()
        runner.settle.assert_called_once()
        self.assertEqual(budget.call_args.kwargs["another_battle_planned"], False)

    def test_selected_quest_settlement_failure_never_starts_list_scan(self):
        runner = self.runner([])
        runner.panel.return_value = False
        runner.reco.side_effect = lambda name, _image: object() if name == "主线-助战界面" else None
        runner.settle.side_effect = StoryStopped("未确认返回强化列表")
        with patch("strengthening_action.bond_stops.battle_started"), self.assertRaises(StoryStopped):
            runner.run()
        runner.locate.assert_not_called()
        self.assertEqual(runner.completed, 0)

    def test_unknown_start_page_stops_before_any_battle_or_scan(self):
        runner = self.runner([])
        runner.panel.return_value = False
        with self.assertRaisesRegex(StoryStopped, "助战选择或编队确认"):
            runner.run()
        runner.fight.assert_not_called()
        runner.locate.assert_not_called()
