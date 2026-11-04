"""助战从者显示优化：只改显示与顺序，不改变已保存选择及运行参数。"""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import generate_support_servant_options as generator


class SupportServantOptionTests(unittest.TestCase):
    def setUp(self):
        self.catalog = json.loads((ROOT / "agent/custom/servant_list.json").read_text("utf-8"))
        self.servants = {str(s["id"]): s for s in self.catalog["servants"]}

    def test_generated_assets_are_current_and_class_labels_match_enhancement(self):
        for path, expected in generator.build_documents().items():
            with self.subTest(path=path.name):
                self.assertEqual(json.loads(path.read_text("utf-8")), expected)
        selection = json.loads((ROOT / "assets/options/support/英灵选择.json").read_text("utf-8"))["option"]["英灵选择"]
        self.assertEqual(selection["default"], "剑士")
        self.assertEqual(selection["cases"][0]["name"], "不要求英灵")
        for case in selection["cases"][1:]:
            key = generator.CLASS_KEYS[case["name"]]
            self.assertEqual(case["label"], self.catalog["class_cn"][key])
            self.assertEqual(len(case["label"].split("(")[0]), 1)
            self.assertEqual(case["pipeline_override"]["助战action"]["attach"]["class_name"], case["name"])
            self.assertEqual(case["option"], [case["name"]])

    def test_all_servants_show_collection_number_and_sort_descending_without_changing_keys(self):
        for path in (ROOT / "assets/options/support/英灵选择").glob("*.json"):
            selection = json.loads(path.read_text("utf-8"))["option"][path.stem]
            numbers = []
            self.assertIn(selection["default"], {case["name"] for case in selection["cases"]})
            for case in selection["cases"]:
                number = self.servants[case["name"]].get("collection_no")
                prefix = f"{number}. " if number is not None else f"ID {case['name']}. "
                self.assertTrue(case["label"].startswith(prefix), (path.name, case))
                numbers.append(number if number is not None else -1)
                self.assertEqual(case["pipeline_override"]["助战action"]["attach"]["servant"], case["name"])
                self.assertIn(f"助战信息-{case['name']}", case["option"])
                self.assertTrue((ROOT / "assets" / case["icon"]).is_file())
            self.assertEqual(numbers, sorted(numbers, reverse=True), path.name)

    def fixture(self):
        return {"option": {"剑士": {"label": "英灵", "default": "100",
            "cases": [
                {"name": "100", "label": "现有译名", "option": ["等级100"],
                 "icon": "portrait100.png", "pipeline_override": {"action": {"attach": {"servant": "100"}}}},
                {"name": "900", "label": "另一从者", "description": "保持说明"},
            ]}, "等级100": {"inputs": [{"default": "90"}]}}}

    def test_collection_order_is_not_internal_id_order_and_all_other_fields_survive(self):
        original = self.fixture()
        before = copy.deepcopy(original)
        catalog = {"100": {"collection_no": 482, "name": "目录译名"}, "900": {"collection_no": 1}}
        updated = generator.build_servant_options(original, "剑士", catalog)
        self.assertEqual(original, before)
        self.assertEqual(updated["option"]["剑士"]["cases"][0]["label"], "482. 现有译名")
        restored = copy.deepcopy(updated)
        for case, old in zip(restored["option"]["剑士"]["cases"], before["option"]["剑士"]["cases"]):
            case["label"] = old["label"]
        self.assertEqual(restored, before)
        self.assertEqual(updated, generator.build_servant_options(updated, "剑士", catalog))

    def test_corrected_collection_number_replaces_old_prefix_without_changing_name(self):
        original = self.fixture()
        original["option"]["剑士"]["cases"][0]["label"] = "400. 现有译名"
        result = generator.build_servant_options(original, "剑士", {"100": {"collection_no": 482}, "900": {"collection_no": 1}})
        self.assertEqual(result["option"]["剑士"]["cases"][0]["label"], "482. 现有译名")

    def test_missing_catalog_or_number_fails_instead_of_guessing_or_dropping_candidate(self):
        for catalog in ({}, {"100": {"collection_no": 0}}, {"100": {"collection_no": "482"}}):
            with self.subTest(catalog=catalog), self.assertRaises(ValueError):
                generator.build_servant_options(self.fixture(), "剑士", catalog)

    def test_legacy_npc_without_collection_number_retains_explicit_internal_id_at_end(self):
        document = self.fixture()
        result = generator.build_servant_options(document, "剑士", {"100": {"collection_no": 2}, "900": {}})
        self.assertEqual([c["label"] for c in result["option"]["剑士"]["cases"]], ["2. 现有译名", "ID 900. 另一从者"])
        self.assertEqual(result, generator.build_servant_options(result, "剑士", {"100": {"collection_no": 2}, "900": {}}))


if __name__ == "__main__":
    unittest.main()
