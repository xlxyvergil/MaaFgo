"""Chaldea footer routing accepts labeled, stable IDs and never other numbers."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np

from agent.battle_editor.chaldea_image import (
    MAX_TEAM_ID, _anchor, _footer_lines, _interpret_lines, _reader, _team,
    recognize_chaldea_image,
)
from agent.battle_editor.storage import EditorError


def line(text, y, score=.99):
    return {'text': text, 'score': score, 'box': [20, y, 400, 40]}


class ChaldeaImageTests(unittest.TestCase):
    def test_official_footer_and_labeled_team(self):
        result = _interpret_lines([line('Team 107574', 50),
            line('Created by Chaldea App v2.6.0', 100),
            line('https://chaldea.center/laplace', 150)])
        self.assertEqual(result[0], 107574)
        self.assertEqual(len(result[1]), 2)

    def test_creator_or_url_is_enough_to_identify_missing_id(self):
        for anchor in ['Created by Chaldea App v2.6.0', 'https://chaldea.center/laplace']:
            with self.subTest(anchor=anchor), self.assertRaises(EditorError) as caught:
                _interpret_lines([line(anchor, 150)])
            self.assertEqual(caught.exception.code, 'chaldea_team_missing')

    def test_other_battle_numbers_are_not_a_team_id(self):
        for text in ['94000178', 'HP 107574', 'DMG 107574', 'Wave 3', 'Team 107574?']:
            with self.subTest(text=text), self.assertRaises(EditorError) as caught:
                _interpret_lines([line(text, 50), line('https://chaldea.center/laplace', 150)])
            self.assertEqual(caught.exception.code, 'chaldea_team_missing')

    def test_number_without_official_anchor_is_not_chaldea(self):
        self.assertIsNone(_interpret_lines([line('Team 107574', 50),
            line('https://example.com/laplace', 150)]))

    def test_lookalike_domains_and_unrelated_text_are_not_anchors(self):
        for text in ['https://evilchaldea.center/laplace', 'https://chaldea.center.evil/laplace',
                     'chaldea.center/laplace/evil', 'Created by Another Chaldea App',
                     'Chaldea App', 'https://chaldea.center']:
            self.assertFalse(_anchor(text), text)
        self.assertTrue(_anchor('https:// chaldea.center / laplace'))

    def test_disagreeing_ids_fail(self):
        with self.assertRaises(EditorError) as caught:
            _interpret_lines([line('Team 107574', 20), line('Team 107575', 70),
                              line('https://chaldea.center/laplace', 150)])
        self.assertEqual(caught.exception.code, 'chaldea_team_ambiguous')

    def test_repeated_identical_id_is_unambiguous(self):
        self.assertEqual(_interpret_lines([line('Team 107574', 20), line('Team 107574', 70),
                              line('https://chaldea.center/laplace', 150)])[0], 107574)

    def test_low_confidence_anchor_is_not_classified(self):
        self.assertIsNone(_interpret_lines([line('Team 107574', 50),
            line('https://chaldea.center/laplace', 150, .5)]))

    def test_low_confidence_id_is_not_accepted(self):
        with self.assertRaises(EditorError) as caught:
            _interpret_lines([line('Team 107574', 50, .5), line('https://chaldea.center/laplace', 150)])
        self.assertEqual(caught.exception.code, 'chaldea_team_missing')

    def test_only_above_footer_anchor_is_considered(self):
        with self.assertRaises(EditorError) as caught:
            _interpret_lines([line('Team 107574', 200), line('https://chaldea.center/laplace', 150)])
        self.assertEqual(caught.exception.code, 'chaldea_team_missing')

    def test_id_is_not_limited_to_six_digits_but_is_a_safe_positive_integer(self):
        self.assertEqual(_team(f'Team ID: {MAX_TEAM_ID}'), MAX_TEAM_ID)
        self.assertEqual(_team('Team #12345678'), 12345678)
        for value in ['Team 0', 'Team -1', 'Team 01', 'Team 1O7574', 'Team 107 574',
                      f'Team {MAX_TEAM_ID+1}', 'Team 1.5', 'Team 1e5']:
            self.assertIsNone(_team(value), value)

    def test_blank_or_colored_game_footer_skips_model_loading(self):
        with patch('agent.battle_editor.chaldea_image._reader') as reader:
            self.assertIsNone(recognize_chaldea_image(np.full((720, 1280, 3), 255, np.uint8), Path('.')))
            self.assertIsNone(recognize_chaldea_image(np.full((720, 1280, 3), (200, 110, 30), np.uint8), Path('.')))
        reader.assert_not_called()

    def test_footer_geometry_never_scans_whole_long_image(self):
        import cv2
        image = np.full((20000, 600, 3), 255, np.uint8)
        cv2.putText(image, 'Team 107574', (150, 19850), cv2.FONT_HERSHEY_SIMPLEX, .7, (0, 0, 0), 2)
        cv2.putText(image, 'https://chaldea.center/laplace', (90, 19950), cv2.FONT_HERSHEY_SIMPLEX, .6, (0, 0, 0), 1)
        boxes = _footer_lines(image)
        self.assertLessEqual(len(boxes), 12)
        self.assertGreaterEqual(len(boxes), 2)
        self.assertTrue(all(box[1] >= 19520 for _, box in boxes))

    def test_black_border_does_not_join_every_text_row_into_one_band(self):
        import cv2
        image = np.full((1000, 600, 3), 255, np.uint8)
        cv2.putText(image, 'Team 107574', (150, 850), cv2.FONT_HERSHEY_SIMPLEX, .7, (0, 0, 0), 2)
        cv2.putText(image, 'chaldea.center/laplace', (90, 950), cv2.FONT_HERSHEY_SIMPLEX, .6, (0, 0, 0), 1)
        bordered = cv2.copyMakeBorder(image, 20, 60, 20, 20, cv2.BORDER_CONSTANT, value=(0, 0, 0))
        boxes = _footer_lines(bordered)
        self.assertGreaterEqual(len(boxes), 2)
        self.assertTrue(all(20 <= box[0] < 620 for _, box in boxes))

    def test_missing_models_have_an_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(EditorError) as caught:
            _reader(directory)
        self.assertEqual(caught.exception.code, 'chaldea_ocr_unavailable')
        self.assertIn('Team ID', caught.exception.message)

    def test_base_models_load_from_canonical_and_legacy_directories(self):
        for relative in ('resource/base/model/ocr', 'assets/resource/base/model/ocr',
                         'resource/base/model/OCR', 'assets/resource/base/model/OCR'):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary) / relative
                directory.mkdir(parents=True)
                for name in ('rec.onnx', 'keys.txt'):
                    (directory / name).write_bytes(b'fixture')
                with patch('agent.battle_editor.chaldea_image._Reader') as reader:
                    self.assertIs(_reader(temporary), reader.return_value)
                    reader.assert_called_once_with(directory / 'rec.onnx', directory / 'keys.txt')

    def run_mocked_image(self, readings):
        crops = [(np.ones((40, 400, 3), np.uint8), [20, y, 400, 40]) for y in (150, 100, 50)]
        reader = Mock()
        reader.read.side_effect = readings
        with patch('agent.battle_editor.chaldea_image._footer_lines', return_value=crops), \
             patch('agent.battle_editor.chaldea_image._reader', return_value=reader):
            return recognize_chaldea_image(np.ones((500, 500, 3), np.uint8), Path('.')), reader

    def test_public_result_uses_canonical_url_not_bare_numeric_quest_id(self):
        result, reader = self.run_mocked_image([
            ('https://chaldea.center/laplace', .99), ('Created by Chaldea App v2.6.0', .99),
            ('Team 12345678', .99), ('Team 12345678', .99)])
        self.assertEqual(result['kind'], 'chaldea')
        self.assertEqual(result['teamId'], 12345678)
        self.assertEqual(result['source'], 'https://link.chaldea.center/laplace/share?id=12345678')
        self.assertEqual(result['warnings'], [])
        self.assertEqual(reader.read.call_count, 4)

    def test_unrelated_document_reads_only_three_bottom_lines(self):
        result, reader = self.run_mocked_image([('normal document', .99)] * 3)
        self.assertIsNone(result)
        self.assertEqual(reader.read.call_count, 3)

    def test_second_contrast_read_must_agree(self):
        with self.assertRaises(EditorError) as caught:
            self.run_mocked_image([('https://chaldea.center/laplace', .99),
                ('Created by Chaldea App v2.6.0', .99), ('Team 107574', .99), ('Team 107575', .99)])
        self.assertEqual(caught.exception.code, 'chaldea_team_ambiguous')

    def test_second_contrast_read_must_be_confident(self):
        with self.assertRaises(EditorError) as caught:
            self.run_mocked_image([('https://chaldea.center/laplace', .99),
                ('Created by Chaldea App v2.6.0', .99), ('Team 107574', .99), ('Team 107574', .5)])
        self.assertEqual(caught.exception.code, 'chaldea_team_uncertain')


if __name__ == '__main__':
    unittest.main()
