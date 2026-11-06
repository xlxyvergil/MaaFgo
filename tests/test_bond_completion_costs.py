"""COST archive generation respects special equipment and existing manual values."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class BondCompletionCostGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bond costs 中文 ")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.servants = self.directory / "servants.json"
        self.equips = self.directory / "equips.json"
        self.output = self.directory / "costs.json"
        self.servants.write_text(json.dumps({"servants": []}), encoding="utf-8")
        self.equips.write_text(json.dumps({"equips": [
            {"id": "9400970", "rarity": 4},
            {"id": "9400280", "rarity": 4},
        ]}), encoding="utf-8")

    def generate(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools/update_bond_completion_costs.py"),
             "--servants", str(self.servants), "--equips", str(self.equips),
             "--output", str(self.output)],
            capture_output=True, encoding="utf-8",
            env=os.environ | {"PYTHONIOENCODING": "utf-8"},
            timeout=15, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(self.output.read_text(encoding="utf-8"))

    def test_new_index_uses_actual_portrait_cost_without_changing_ordinary_four_star_cost(self):
        result = self.generate()
        self.assertEqual(result["equips"], {"9400280": 9, "9400970": 5})

    def test_existing_manual_costs_remain_authoritative_including_special_equipment(self):
        self.output.write_text(json.dumps({
            "schema_version": 1, "servants": {"800100": 2},
            "equips": {"9400970": 7, "9400280": 8, "9999990": 6},
        }), encoding="utf-8")
        result = self.generate()
        self.assertEqual(result["servants"], {"800100": 2})
        self.assertEqual(result["equips"], {"9400280": 8, "9400970": 7, "9999990": 6})


if __name__ == "__main__":
    unittest.main()
