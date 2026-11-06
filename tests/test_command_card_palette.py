"""Guard alpha classes while quantizing, without game assets or network."""
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from command_card_palette import alpha_class, palette_png, quantize_image


class GuardedPaletteTests(unittest.TestCase):
    def assert_classes(self, before, result):
        after = np.asarray(result.convert('RGBA'))
        np.testing.assert_array_equal(before[:, :, 3] == 0, after[:, :, 3] == 0)
        np.testing.assert_array_equal(before[:, :, 3] >= 128, after[:, :, 3] >= 128)
        self.assertEqual(result.mode, 'P')
        self.assertLessEqual(len(result.getcolors()), 256)

    def test_gradient_preserves_zero_and_threshold_in_saved_png(self):
        rng = np.random.default_rng(1301)
        before = rng.integers(0, 256, (64, 256, 4), dtype=np.uint8)
        before[:, :, 3] = np.arange(256, dtype=np.uint8)
        image = Image.fromarray(before)
        raw = io.BytesIO(); image.save(raw, 'PNG')
        data, _, changed = palette_png(raw.getvalue(), image.size)
        self.assertEqual(changed, 0)
        with Image.open(io.BytesIO(data)) as saved:
            self.assert_classes(before, saved)

    def test_missing_palette_classes_are_reserved_and_repaired(self):
        before = np.array([[[12, 34, 56, 0], [12, 34, 56, 1], [12, 34, 56, 127], [12, 34, 56, 128], [12, 34, 56, 255]]], np.uint8)
        pal = np.tile(np.array([12, 34, 56, 255], np.uint8), (256, 1))
        with patch('imagequant.quantize_raw_rgba_bytes', return_value=(b'\0'*5, pal.ravel().tolist())):
            result, metrics = quantize_image(Image.fromarray(before))
        self.assert_classes(before, result)
        self.assertEqual(metrics['reserved_alpha_classes'], 2)
        self.assertGreater(metrics['alpha_class_corrected_pixels'], 0)

    def test_fully_opaque_and_fully_transparent_images(self):
        for alpha in (0, 255):
            before = np.full((10, 20, 4), [30, 80, 160, alpha], dtype=np.uint8)
            result, _ = quantize_image(Image.fromarray(before))
            self.assert_classes(before, result)

    def test_input_is_not_modified(self):
        image = Image.new('RGBA', (16, 16), (12, 34, 56, 127))
        before = image.tobytes()
        quantize_image(image)
        self.assertEqual(image.tobytes(), before)

    def test_alpha_classes_are_not_full_alpha_losslessness(self):
        np.testing.assert_array_equal(alpha_class(np.array([0, 1, 127, 128, 254, 255])), [0, 1, 1, 2, 2, 2])


if __name__ == '__main__':
    unittest.main()
