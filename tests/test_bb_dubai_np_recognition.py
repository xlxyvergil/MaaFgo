"""Real JP BB NP cards keep their identity with the type-change HUD visible."""
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'agent'), str(ROOT / 'agent/custom')]
from battle.core.enums import CardColor
from battle.perception import perception
from battle.perception.composed_card import ComposedCardRecognizer

FIXTURES = ROOT / 'tests/fixtures/battle_np'
CASES = (
    ('jp_bb_dubai_ccc_20261114.png', 'card_servant_np.png'),
    ('jp_bb_dubai_ggg_badge_flash_20261114.png', 'card_servant_np_2300610.png'),
    ('jp_bb_dubai_ggg_badge_20261114.png', 'card_servant_np_2300610.png'),
)
EMIYA_CASES = (
    ('jp_emiya_buster_20261114.png', CardColor.BUSTER),
    ('jp_emiya_arts_badge_flash_20261114.png', CardColor.ARTS),
)
ROSTER = ('2300600', '200100', '504500')


def fixture(name, slot=1):
    # Store only the exact half-scale NP1 working ROI, avoiding a whole game
    # screenshot. Nearest expansion followed by the recognizer's AREA reduction
    # recovers these same pixels, with no JPEG or palette approximation.
    strip = cv2.imdecode(np.fromfile(FIXTURES / name, dtype=np.uint8), cv2.IMREAD_COLOR)
    frame = np.zeros((360, 640, 3), np.uint8)
    center = 410 + 230 * (slot - 1)
    left, right = round((center - 145) / 2), round((center + 145) / 2)
    frame[35:185, left:right] = strip
    return cv2.resize(frame, (1280, 720), interpolation=cv2.INTER_NEAREST)


class BbDubaiNpRecognitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recognizer = ComposedCardRecognizer(ROOT, region='JP')

    def scores(self, image, servant_id='2300600', slot=1):
        center = 410 + 230 * (slot - 1)
        left, right = round((center - 145) / 2), round((center + 145) / 2)
        crop = self.recognizer._image(image)[35:185, left:right]
        scores = []
        for template in self.recognizer._prepare(servant_id, 'np').templates:
            matching = cv2.matchTemplate(crop, template.pixels,
                                        cv2.TM_CCOEFF_NORMED, mask=template.mask)
            score = float(np.nan_to_num(matching, nan=-1, posinf=-1, neginf=-1).max())
            scores.append((score, template))
        return scores

    def test_real_fixture_provenance_and_small_working_regions(self):
        entries = json.loads((FIXTURES / 'bb_dubai_np_sources.json').read_text('utf-8'))
        self.assertEqual({entry['file'] for entry in entries}, {name for name, _ in CASES})
        for entry in entries:
            path = FIXTURES / entry['file']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), entry['sha256'])
            strip = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
            self.assertEqual(strip.shape, (150, 146, 3))

    def test_real_attack_and_defense_np_identify_without_lowering_gates(self):
        self.assertEqual(self.recognizer.min_np_score, .80)
        self.assertEqual(self.recognizer.min_margin, .07)
        for name, expected_title in CASES:
            with self.subTest(frame=name):
                result = self.recognizer.detect_np(fixture(name), ROSTER)[0]
                self.assertTrue(result.visual_present, result)
                self.assertTrue(result.authoritative, result)
                self.assertEqual((result.owner_slot, result.servant_id, result.color),
                                 (1, '2300600', CardColor.ARTS))
                self.assertGreaterEqual(result.score, .80)
                self.assertIn('/' + expected_title + ':row0', result.template)

    def test_correct_title_remains_separated_from_other_title_and_wrong_colors(self):
        for name, expected_title in CASES:
            with self.subTest(frame=name):
                scores = self.scores(fixture(name))
                correct = max(score for score, t in scores
                              if t.color is CardColor.ARTS and '/' + expected_title + ':row0' in t.name)
                wrong_title = max(score for score, t in scores
                                  if '/' + expected_title + ':row0' not in t.name)
                wrong_color = max(score for score, t in scores if t.color is not CardColor.ARTS)
                # G.G.G. and C.C.C. share the same portrait/Arts rays. Retain
                # enough of their distinct names to separate even a clear phase.
                self.assertGreaterEqual(correct - wrong_title, .07)
                self.assertLess(wrong_color, .80)

    def test_other_bb_forms_and_unrelated_frontline_identities_stay_negative(self):
        for name, _ in CASES:
            image = fixture(name)
            for sid in ('2300100', '2300200', '200100', '504500'):
                with self.subTest(frame=name, candidate=sid):
                    result = self.recognizer.detect_np(image, (sid, None, None))[0]
                    self.assertFalse(result.visual_present, result)
                    self.assertLess(result.score, .80)

    def test_empty_and_plain_battle_background_do_not_become_np_cards(self):
        # No expected name/portrait texture means even bright or dark fields
        # must remain negative; the lower-HUD exception cannot invent a card.
        for level in (0, 30, 230, 255):
            with self.subTest(level=level):
                image = np.full((720, 1280, 3), level, np.uint8)
                results = self.recognizer.detect_np(image, ROSTER)
                self.assertFalse(any(item.visual_present for item in results))

    def test_hud_exception_preserves_unverified_identity_and_color_features(self):
        bb = self.recognizer._prepare('2300600', 'np')
        emiya = self.recognizer._prepare('200100', 'np')
        other = self.recognizer._prepare('504500', 'np')
        # Odd-height artwork can round its half-scale boundary by one row.
        self.assertTrue(all(np.count_nonzero(t.mask[102:]) == 0
                            for t in bb.templates if t.color is CardColor.ARTS))
        self.assertTrue(all(np.count_nonzero(t.mask[102:]) == 0
                            for t in emiya.templates if t.color is not CardColor.QUICK))
        self.assertTrue(any(np.count_nonzero(t.mask[102:115]) > 0
                            for t in bb.templates if t.color is not CardColor.ARTS))
        self.assertTrue(any(np.count_nonzero(t.mask[102:115]) > 0
                            for t in emiya.templates if t.color is CardColor.QUICK))
        self.assertTrue(any(np.count_nonzero(t.mask[102:115]) > 0 for t in other.templates))

    def test_emiya_red_baseline_and_real_arts_type_change_both_identify(self):
        for name, color in EMIYA_CASES:
            with self.subTest(frame=name):
                results = self.recognizer.detect_np(fixture(name, slot=2), ROSTER)
                result = results[1]
                self.assertTrue(result.visual_present, result)
                self.assertTrue(result.authoritative, result)
                self.assertEqual((result.owner_slot, result.servant_id, result.color),
                                 (2, '200100', color))
                self.assertGreaterEqual(result.score, .80)
                self.assertEqual([r.ui_slot for r in results if r.visual_present], [2])

    def test_emiya_red_and_blue_keep_color_separation_with_badge_mask(self):
        for name, expected in EMIYA_CASES:
            with self.subTest(frame=name):
                scores = self.scores(fixture(name, slot=2), '200100', slot=2)
                correct = max(score for score, t in scores if t.color is expected)
                wrong = max(score for score, t in scores if t.color is not expected)
                self.assertGreaterEqual(correct - wrong, .07)
                # Emiya's choice is Arts/Buster. Do not discard the Quick
                # candidate's distinguishing rays when masking their type HUD.
                self.assertLess(max(score for score, t in scores if t.color is CardColor.QUICK), .80)

    def test_emiya_real_cards_do_not_match_other_servants(self):
        for name, _ in EMIYA_CASES:
            image = fixture(name, slot=2)
            for sid in ('2300600', '504500', '200200'):
                with self.subTest(frame=name, candidate=sid):
                    result = self.recognizer.detect_np(image, (None, sid, None))[1]
                    self.assertFalse(result.visual_present, result)
                    self.assertLess(result.score, .80)

    def test_emiya_real_fixture_provenance_and_small_working_regions(self):
        entries = json.loads((FIXTURES / 'emiya_np_sources.json').read_text('utf-8'))
        self.assertEqual({entry['file'] for entry in entries}, {name for name, _ in EMIYA_CASES})
        for entry in entries:
            path = FIXTURES / entry['file']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), entry['sha256'])
            strip = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
            self.assertEqual(strip.shape, (150, 144, 3))

    def test_real_defense_card_flows_into_battle_state_and_still_obeys_np_seal(self):
        image = fixture('jp_bb_dubai_ggg_badge_flash_20261114.png')
        for sealed in (False, True):
            with self.subTest(sealed=sealed):
                def recognize(node, _):
                    hit = node == '战斗_选卡场景' or (sealed and node == '战斗_宝具卡1_封印')
                    return NS(hit=hit, best_result=NS(score=.99, text='') if hit else None)
                context = NS(run_recognition=Mock(side_effect=recognize))
                state = perception.build(context, image, frontline_servant_ids=ROSTER,
                                         composed_recognizer=self.recognizer)
                self.assertEqual([card.servant_slot for card in state.np_cards], [1])
                card = state.np_cards[0]
                self.assertEqual(card.owner_servant_id, '2300600')
                self.assertIn('card_servant_np_2300610.png', card.owner_template)
                self.assertEqual(card.available, not sealed)
                self.assertEqual(card.blocked_reason, 'sealed_or_cannot_act' if sealed else '')


if __name__ == '__main__':
    unittest.main()
