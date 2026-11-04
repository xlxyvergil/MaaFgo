"""Official numeric Chaldea color enums must preserve mixed card order."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]

from battle.core.enums import CardColor, PrimitiveKind
from battle.data.chaldea_converter import convert_chaldea_actions_to_battle_plan


class ChaldeaCardTypeTests(unittest.TestCase):
    def convert(self, attacks):
        return convert_chaldea_actions_to_battle_plan([
            {"type": "attack", "attacks": attacks}
        ]).turns[0]

    def test_official_numeric_and_historical_forms(self):
        for value, color in ((1, CardColor.ARTS), (2, CardColor.BUSTER), (3, CardColor.QUICK),
                             ("1", CardColor.ARTS), ("2", CardColor.BUSTER), ("3", CardColor.QUICK),
                             ("arts", CardColor.ARTS), ("buster", CardColor.BUSTER), ("quick", CardColor.QUICK)):
            with self.subTest(value=value):
                turn = self.convert([{"svt": 0, "cardType": value, "card": 4}])
                self.assertEqual(len(turn.card_sequence), 1)
                self.assertEqual(turn.card_sequence[0].color, color)

    def test_mixed_face_np_face_order_and_servant_slots(self):
        turn = self.convert([{"svt": 2, "cardType": 2, "card": 0},
                             {"svt": 0, "isTD": True, "card": -1},
                             {"svt": 1, "cardType": "1", "card": 2}])
        self.assertEqual([card.kind for card in turn.card_sequence],
                         [PrimitiveKind.SELECT_CARD, PrimitiveKind.SELECT_NP, PrimitiveKind.SELECT_CARD])
        self.assertEqual([card.servant_slot for card in turn.card_sequence], [3, 1, 2])
        self.assertEqual(turn.np_order, (1,))

    def test_special_colors_and_non_enums_do_not_become_normal_cards(self):
        for value in (True, False, 1.0, 2.0, 0, 4, -1, "4", "extra", "weak", "Arts", None, [], {}):
            with self.subTest(value=value):
                turn = self.convert([{"svt": 0, "cardType": value}, {"svt": 1, "isTD": True}])
                self.assertEqual(len(turn.card_sequence), 1)
                self.assertEqual(turn.card_sequence[0].kind, PrimitiveKind.SELECT_NP)


if __name__ == "__main__":
    unittest.main()
