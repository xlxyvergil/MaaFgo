"""宝具元数据增量导入与工程目录回归；不联网、不修改真实 CSV。"""
import copy
import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.migrations import enrich_servant_np as tool


def source_row(sid="304800", number="312", *, variants=None):
    variants = variants if variants is not None else [
        {"treasureDeviceId": 304801, "name_jp": "今は知らず、無垢なる湖光", "type": "单体输出", "card": "Arts"},
        {"treasureDeviceId": 304802, "name_jp": "誰も知らぬ、無垢なる鼓動", "type": "群体输出", "card": "Buster",
         "conditions_jp": ["霊基第3段階時"]},
    ]
    return {"servantId": sid, "collectionNo": number, "np_type": "单体输出/群体输出",
            "np_card": "Arts/Buster", "np_variants": json.dumps(variants, ensure_ascii=False)}


class MetadataImportTests(unittest.TestCase):
    def setUp(self):
        self.catalog = {"class_cn": {"lancer": "枪"}, "other": {"keep": True}, "servants": [
            {"id": "304800", "collection_no": 312, "name": "妖精骑士兰斯洛特", "class": "lancer",
             "rarity": 5, "images": ["f_3048000.png"], "bond": {"tags": ["female"]}},
            {"id": "1002100", "name": "历史条目", "images": []},
        ]}

    def test_exact_id_only_preserves_all_existing_data_and_variant_pairs(self):
        before = copy.deepcopy(self.catalog)
        result, report = tool.enrich_catalog(self.catalog, [source_row(), source_row("999", "999")])
        self.assertEqual(self.catalog, before)
        self.assertEqual(report["added"], 1)
        self.assertEqual(report["unmatched_catalog_ids"], ["1002100"])
        self.assertEqual(report["unused_csv_ids"], ["999"])
        self.assertEqual(len(result["servants"]), len(before["servants"]))
        self.assertEqual(result["servants"][1], before["servants"][1])
        for key, value in before["servants"][0].items():
            self.assertEqual(result["servants"][0][key], value)
        variants = result["servants"][0]["np_variants"]
        self.assertIsInstance(variants, list)
        self.assertEqual({(v["type"], v["card"]) for v in variants}, {("单体输出", "Arts"), ("群体输出", "Buster")})
        self.assertEqual(variants[1]["conditions_jp"], ["霊基第3段階時"])
        self.assertEqual(set(result["servants"][0]) - set(before["servants"][0]), set(tool.NP_FIELDS))

    def test_idempotence_and_conflicting_existing_fields(self):
        result, _ = tool.enrich_catalog(self.catalog, [source_row()])
        again, report = tool.enrich_catalog(result, [source_row()])
        self.assertEqual(again, result)
        self.assertEqual((report["added"], report["unchanged"]), (0, 1))
        result["servants"][0]["np_card"] = "Quick"
        with self.assertRaisesRegex(ValueError, "冲突"):
            tool.enrich_catalog(result, [source_row()])

    def test_duplicate_ids_or_collection_conflicts_fail_without_mutation(self):
        for rows in ([source_row(), source_row()], [source_row(number="1")]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                tool.enrich_catalog(self.catalog, rows)
        self.assertNotIn("np_card", self.catalog["servants"][0])

    def test_no_name_or_collection_fallback(self):
        row = source_row(sid="200000")
        row["name_cn"] = "妖精骑士兰斯洛特"
        result, report = tool.enrich_catalog(self.catalog, [row])
        self.assertEqual(result, self.catalog)
        self.assertEqual(report["added"], 0)

    def test_reject_invalid_variants_and_summary_disagreements(self):
        invalid = []
        for text in ("not json", "{}", '["not an object"]'):
            row = source_row()
            row["np_variants"] = text
            invalid.append(row)
        for key, value in (("treasureDeviceId", 100), ("treasureDeviceId", True), ("card", "Extra"), ("card", []), ("type", "治疗"), ("name_jp", "")):
            variants = json.loads(source_row()["np_variants"])
            variants[0][key] = value
            invalid.append(source_row(variants=variants))
        for key, value in (("np_type", "群体输出"), ("np_card", "Quick")):
            row = source_row()
            row[key] = value
            invalid.append(row)
        for row in invalid:
            with self.subTest(row=row), self.assertRaises(ValueError):
                tool.enrich_catalog(self.catalog, [row])

    def test_missing_np_is_explicit_and_color_is_not_guessed(self):
        row = source_row()
        row.update(np_type="待补充", np_card="", np_variants="[]")
        fields = tool.parse_np_fields(row)
        self.assertEqual(fields, {"np_type": "待补充", "np_card": "", "np_variants": []})
        row["np_card"] = "Arts"
        with self.assertRaises(ValueError):
            tool.parse_np_fields(row)

    def test_preview_atomic_write_bom_newlines_and_repeat_noop(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            catalog = directory / "catalog.json"
            csv_path = directory / "source.csv"
            raw = b"\xef\xbb\xbf" + (json.dumps(self.catalog, ensure_ascii=False, indent=2) + "\n").replace("\n", "\r\n").encode("utf8")
            catalog.write_bytes(raw)
            with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(source_row()))
                writer.writeheader()
                writer.writerow(source_row())
            tool.migrate(csv_path, catalog)
            self.assertEqual(catalog.read_bytes(), raw)
            tool.migrate(csv_path, catalog, write=True)
            after = catalog.read_bytes()
            self.assertTrue(after.startswith(b"\xef\xbb\xbf"))
            self.assertNotIn(b"\n", after.replace(b"\r\n", b""))
            self.assertIsInstance(json.loads(after.decode("utf-8-sig"))["servants"][0]["np_variants"], list)
            tool.migrate(csv_path, catalog, write=True)
            self.assertEqual(catalog.read_bytes(), after)
            self.assertEqual(list(directory.glob("*.tmp")), [])

    def test_csv_must_have_all_required_columns(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "incomplete.csv"
            path.write_text("servantId,collectionNo\n304800,312\n", encoding="utf8")
            with self.assertRaisesRegex(ValueError, "缺少字段"):
                tool.read_np_csv(path)


class ProjectMetadataTests(unittest.TestCase):
    def test_project_np_metadata_and_special_variants(self):
        data = json.loads((ROOT / "agent/custom/servant_list.json").read_text(encoding="utf-8-sig"))
        servants = {s["id"]: s for s in data["servants"]}
        annotated = [s for s in servants.values() if all(f in s for f in tool.NP_FIELDS)]
        self.assertEqual(len(annotated), len(servants) - 1)
        self.assertNotIn("np_type", servants["1002100"])
        for item in annotated:
            # Validate the actual shipped catalog without needing the maintenance CSV.
            tool.parse_np_fields({"servantId": item["id"], "np_type": item["np_type"],
                                  "np_card": item["np_card"], "np_variants": json.dumps(item["np_variants"])})
        self.assertEqual(servants["402500"]["np_type"], "群体输出")
        self.assertEqual(servants["1001100"]["np_type"], "辅助宝具")
        self.assertEqual(set(servants["1100900"]["np_card"].split("/")), {"Arts", "Buster", "Quick"})
        self.assertEqual(servants["2300600"]["np_type"], "群体输出/辅助宝具")
        self.assertEqual({(v["type"], v["card"]) for v in servants["304800"]["np_variants"]},
                         {("单体输出", "Arts"), ("群体输出", "Buster")})


if __name__ == "__main__":
    unittest.main()
