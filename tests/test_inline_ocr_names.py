"""Batch OCR cache keys must distinguish inline recognitions across nodes."""
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def inline_ocr(value, path=()):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in ("all_of", "any_of") and isinstance(child, list):
                for index, sub in enumerate(child):
                    if not isinstance(sub, dict):
                        continue
                    recognition = sub.get("recognition")
                    kind = recognition.get("type") if isinstance(recognition, dict) else recognition
                    if kind == "OCR":
                        yield path + (key, index), sub
            yield from inline_ocr(child, path + (key,))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from inline_ocr(child, path + (index,))


class InlineOcrNamesTests(unittest.TestCase):
    def test_inline_ocr_cache_keys_are_explicit_and_globally_unique(self):
        aliases = {}
        for file in sorted((ROOT / "assets").rglob("*.json")):
            if "web" in file.relative_to(ROOT / "assets").parts:
                continue
            document = json.loads(file.read_text(encoding="utf-8-sig"))
            for pointer, recognition in inline_ocr(document):
                location = f"{file.relative_to(ROOT)}:{pointer}"
                alias = recognition.get("sub_name")
                self.assertIsInstance(alias, str, location)
                self.assertTrue(alias.strip(), location)
                self.assertNotIn(alias, aliases, f"{location} duplicates {aliases.get(alias)}")
                aliases[alias] = location
        self.assertTrue(aliases, "No inline OCR recognitions were checked")


if __name__ == "__main__":
    unittest.main()
