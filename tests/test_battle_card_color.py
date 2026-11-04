"""日服英文卡色 OCR 与国服中文卡色应走同一感知结果。"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "agent/custom"))

from battle.core.enums import CardColor
from battle.core.decider import _best_face_order
from battle.core.models import CommandCard, Confidence
from battle.core.policy import CardPolicy
from battle.perception.perception import _detect_card


class BattleCardColorTests(unittest.TestCase):
    def detect(self, text, *, score=0.97, hit=True):
        context = NS(run_recognition=Mock(return_value=NS(
            hit=hit, best_result=NS(text=text, score=score))))
        image = object()
        card = _detect_card(context, image, 3)
        context.run_recognition.assert_called_once_with("战斗_卡3", image)
        self.assertEqual(card.ui_slot, 3)
        return card

    def test_japanese_english_labels_preserve_the_recognition_confidence(self):
        for text, expected in (("Buster", CardColor.BUSTER),
                               ("Arts", CardColor.ARTS),
                               ("Quick", CardColor.QUICK)):
            with self.subTest(text=text):
                card = self.detect(text)
                self.assertEqual(card.color, expected)
                self.assertEqual(card.confidence.value, 0.97)

    def test_english_case_and_surrounding_whitespace_are_ignored(self):
        for text, expected in (("  bUsTeR\n", CardColor.BUSTER),
                               ("\tARTS  ", CardColor.ARTS),
                               ("\u3000qUiCk\u3000", CardColor.QUICK)):
            with self.subTest(text=text):
                card = self.detect(text, score=0.88)
                self.assertEqual(card.color, expected)
                self.assertEqual(card.confidence.value, 0.88)

    def test_chinese_labels_remain_supported(self):
        for text, expected in (("力击", CardColor.BUSTER),
                               (" 技击 ", CardColor.ARTS),
                               ("迅击", CardColor.QUICK)):
            with self.subTest(text=text):
                card = self.detect(text)
                self.assertEqual(card.color, expected)
                self.assertEqual(card.confidence.value, 0.97)

    def test_unknown_text_and_embedded_color_words_keep_zero_confidence(self):
        # MArts was observed on the live JP screen; accepting it needs separate
        # OCR evidence instead of silently treating every substring as a color.
        for text in ("MArts", "Arts Chain", "Quickness", "Buster!", "技击卡", "", "   "):
            with self.subTest(text=text):
                card = self.detect(text)
                self.assertEqual(card.color, CardColor.UNKNOWN)
                self.assertEqual(card.confidence.value, 0.0)

    def test_failed_recognition_keeps_zero_confidence_even_with_known_text(self):
        card = self.detect("Arts", hit=False)
        self.assertEqual(card.color, CardColor.UNKNOWN)
        self.assertEqual(card.confidence.value, 0.0)

    def test_missing_recognition_detail_keeps_zero_confidence(self):
        for result in (None, NS(hit=True, best_result=None)):
            with self.subTest(result=result):
                context = NS(run_recognition=Mock(return_value=result))
                card = _detect_card(context, object(), 1)
                self.assertEqual(card.color, CardColor.UNKNOWN)
                self.assertEqual(card.confidence.value, 0.0)

    def test_unknown_cards_do_not_beat_known_colors_as_a_fake_chain(self):
        cards = tuple(CommandCard(i, color, None, Confidence(0 if color is CardColor.UNKNOWN else 1))
                      for i, color in enumerate((CardColor.UNKNOWN, CardColor.UNKNOWN,
                                                 CardColor.UNKNOWN, CardColor.QUICK,
                                                 CardColor.ARTS), 1))
        selected = _best_face_order(cards, 3, CardPolicy())
        self.assertIn(4, selected)
        self.assertIn(5, selected)
        self.assertEqual(len(set(selected)), 3)

    def test_unknown_cards_remain_available_for_safe_three_card_filling(self):
        cards = tuple(CommandCard(i, CardColor.UNKNOWN, None, Confidence(0)) for i in range(1, 6))
        self.assertEqual(_best_face_order(cards, 3, CardPolicy()), [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
