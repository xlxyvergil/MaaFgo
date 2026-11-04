"""BBC export must reject native-only menu choices before writing a config."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]
import chaldea
from chaldea.bbc_formatter import chaldea_to_bbc, convert_actions_to_bbc_rounds


class BbcSpecialChoicesTests(unittest.TestCase):
    def share(self, delegate):
        return {"team": {"onFieldSvts": [], "backupSvts": [],
                         "mysticCode": {"mysticCodeId": 210}},
                "actions": [{"type": "attack", "attacks": [{"svt": 0, "isTD": True}]}],
                "delegate": delegate}

    def test_nonempty_special_choice_fields_reject_both_conversion_entries(self):
        for delegate in ({"tdTypeChanges": [2]}, {"tdTypeChanges": ["quick"]},
                         {"skillActSelectSelections": [0]},
                         {"tdTypeChanges": [1], "skillActSelectSelections": [1]}):
            data = self.share(delegate)
            original = copy.deepcopy(data)
            with self.subTest(delegate=delegate):
                with self.assertRaisesRegex(ValueError, "原生自动战斗"):
                    chaldea_to_bbc(data)
                with self.assertRaisesRegex(ValueError, "原生自动战斗"):
                    convert_actions_to_bbc_rounds(data["actions"], delegate)
                self.assertEqual(data, original)

    def test_absent_null_or_empty_fields_keep_legacy_bbc_conversion(self):
        baseline = chaldea_to_bbc(self.share({}))
        for delegate in (None, {}, {"tdTypeChanges": None, "skillActSelectSelections": None},
                         {"tdTypeChanges": [], "skillActSelectSelections": []}):
            with self.subTest(delegate=delegate):
                self.assertEqual(chaldea_to_bbc(self.share(delegate)), baseline)

    def test_rejection_does_not_create_or_overwrite_bbc_config(self):
        for field, values in (("tdTypeChanges", [2]), ("skillActSelectSelections", [0])):
            data = self.share({field: values})
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory, \
                    patch("chaldea.fetch_share_data", return_value=(data, 1, 2)):
                with self.assertRaisesRegex(ValueError, "原生自动战斗"):
                    chaldea.fetch_and_convert("source", output_dir=directory)
                self.assertEqual(list(Path(directory).iterdir()), [])
                existing = Path(directory) / "chaldea_1_2.json"
                original = b'{"previous": "valid config"}'
                existing.write_bytes(original)
                with self.assertRaisesRegex(ValueError, "原生自动战斗"):
                    chaldea.fetch_and_convert("source", output_dir=directory)
                self.assertEqual(existing.read_bytes(), original)
                self.assertEqual(list(Path(directory).iterdir()), [existing])


if __name__ == "__main__":
    unittest.main()
