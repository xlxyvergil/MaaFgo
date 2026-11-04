"""BBC must not turn an explicit empty planned turn into free skill/NP use."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'agent'), str(ROOT / 'agent/custom')]

import chaldea
from chaldea.bbc_formatter import chaldea_to_bbc, convert_actions_to_bbc_rounds


class BbcEmptyActionsTests(unittest.TestCase):
    def share(self, **changes):
        return {'team': {'onFieldSvts': [], 'backupSvts': [],
                         'mysticCode': {'mysticCodeId': 210}}, **changes}

    def test_no_actions_missing_null_or_empty_keeps_bbc_free_battle(self):
        for data in [self.share(), self.share(actions=None), self.share(actions=[])]:
            before = copy.deepcopy(data)
            with self.subTest(data=data):
                exported = chaldea_to_bbc(data)
                self.assertFalse(any(key.startswith('round') for key in exported))
                self.assertEqual(data, before)
        self.assertEqual(convert_actions_to_bbc_rounds([]), {})

    def test_explicit_empty_attack_fails_before_becoming_free_battle(self):
        for attack in [{'type': 'attack'}, {'type': 'attack', 'attacks': None},
                       {'type': 'attack', 'attacks': []}]:
            for preceding in [[], [{'type': 'attack', 'attacks': [{'svt': 0, 'isTD': True}]}]]:
                actions = preceding + [attack]
                before = copy.deepcopy(actions)
                with self.subTest(preceding=preceding, attack=attack), \
                     self.assertRaisesRegex(ValueError, '原生战斗'):
                    convert_actions_to_bbc_rounds(actions)
                self.assertEqual(actions, before)

    def test_skills_followed_by_default_normal_cards_remain_supported(self):
        for attack in [{'type': 'attack'}, {'type': 'attack', 'attacks': None},
                       {'type': 'attack', 'attacks': []}]:
            actions = [{'type': 'skill', 'svt': 0, 'skill': 1}, attack]
            with self.subTest(attack=attack):
                exported = convert_actions_to_bbc_rounds(actions)
                self.assertEqual(exported['round1_turns'], 1)
                self.assertEqual(exported['round1_turn0_skill'], [2])
                self.assertEqual(exported['round1_turn0_np'], [])
                self.assertIsNone(exported['round1_turn0_strategy'])

    def test_np_round_still_converts(self):
        exported = convert_actions_to_bbc_rounds([
            {'type': 'attack', 'attacks': [{'svt': 1, 'isTD': True}]}])
        self.assertEqual(exported['round1_turn0_np'], [2])
        self.assertEqual(exported['round1_turn0_skill'], [])

    def test_rejected_empty_turn_never_creates_or_overwrites_bbc_file(self):
        data = self.share(actions=[{'type': 'attack', 'attacks': []}])
        with tempfile.TemporaryDirectory() as directory, \
             patch('chaldea.fetch_share_data', return_value=(data, 1, 2)):
            with self.assertRaisesRegex(ValueError, '原生战斗'):
                chaldea.fetch_and_convert('source', output_dir=directory)
            self.assertEqual(list(Path(directory).iterdir()), [])
            old_path = Path(directory) / 'chaldea_1_2.json'
            old_bytes = b'{"keep": "previous valid BBC configuration"}'
            old_path.write_bytes(old_bytes)
            with self.assertRaisesRegex(ValueError, '原生战斗'):
                chaldea.fetch_and_convert('source', output_dir=directory)
            self.assertEqual(old_path.read_bytes(), old_bytes)
            self.assertEqual(list(Path(directory).iterdir()), [old_path])


if __name__ == '__main__':
    unittest.main()
