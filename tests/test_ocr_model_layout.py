"""Catch case-sensitive OCR resource paths even on a Windows checkout."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class OcrModelLayoutTests(unittest.TestCase):
    def test_base_ocr_directory_and_model_filenames_have_exact_case(self):
        model_root = ROOT / "assets/resource/base/model"
        # Path.exists() alone would accept model/OCR as model/ocr on Windows.
        directories = {entry.name: entry for entry in model_root.iterdir() if entry.is_dir()}
        actual_ocr_names = sorted(name for name in directories if name.casefold() == "ocr")
        self.assertEqual(actual_ocr_names, ["ocr"],
                         "MaaFramework loads model/ocr; the on-disk directory must use that exact case")
        self.assertNotIn("OCR", directories)

        files = {entry.name: entry for entry in directories["ocr"].iterdir() if entry.is_file()}
        for filename in ("det.onnx", "rec.onnx", "keys.txt"):
            with self.subTest(filename=filename):
                variants = sorted(name for name in files if name.casefold() == filename)
                self.assertEqual(variants, [filename], "OCR model filenames must match their exact lowercase spelling")
                self.assertGreater(files[filename].stat().st_size, 0, "OCR model files must not be empty")


if __name__ == "__main__":
    unittest.main()
