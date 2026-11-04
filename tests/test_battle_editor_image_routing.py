"""Image routing restores known protocols without falling back to guessed teams."""
import io
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from agent.battle_editor import screenshot_import
from agent.battle_editor.storage import EditorError


class ImageRoutingTests(unittest.TestCase):
    def setUp(self):
        self.raw = b"encoded image"
        self.root = Path("example-project")
        self.decoded = np.zeros((360, 640, 3), dtype=np.uint8)
        self.decode = self.enterContext(patch.object(
            screenshot_import, "decode_image", return_value=self.decoded))
        self.share = self.enterContext(patch(
            "agent.battle_editor.share_image.decode_share_image", return_value=None))
        self.chaldea = self.enterContext(patch(
            "agent.battle_editor.chaldea_image.recognize_chaldea_image", return_value=None))
        self.factory = self.enterContext(patch.object(screenshot_import, "ScreenshotIndex"))

    def test_maafgo_packet_does_not_start_chaldea_ocr_or_game_templates(self):
        data = {"team": {"name": "完整回导"}, "actions": [], "future": {"保留": True}}
        self.share.return_value = {"kind": "share", "schema": 1, "data": data, "warnings": []}
        result, index = screenshot_import.recognize_image(self.raw, self.root)

        self.assertEqual(result["kind"], "share")
        self.assertIs(result["data"], data)
        self.assertIsNone(index)
        self.assertIsInstance(result["elapsedMs"], int)
        self.decode.assert_called_once_with(self.raw)
        self.assertIs(self.share.call_args.args[0], self.decoded)
        self.chaldea.assert_not_called()
        self.factory.assert_not_called()

    def test_chaldea_footer_identifies_id_without_loading_game_templates(self):
        self.chaldea.return_value = {
            "kind": "chaldea", "teamId": 107574,
            "source": "https://link.chaldea.center/laplace/share?id=107574"}
        result, index = screenshot_import.recognize_image(self.raw, self.root)

        self.assertEqual(result["teamId"], 107574)
        self.assertEqual(result["kind"], "chaldea")
        self.assertIsNone(index)
        self.decode.assert_called_once_with(self.raw)
        self.assertIs(self.chaldea.call_args.args[0], self.decoded)
        self.assertEqual(self.chaldea.call_args.args[1], self.root)
        self.factory.assert_not_called()

    def test_game_image_initializes_index_once_and_reuses_decoded_pixels(self):
        expected = {"kind": "screenshot", "slots": [], "warnings": []}
        self.factory.return_value.recognize.return_value = expected
        result, index = screenshot_import.recognize_image(self.raw, self.root)

        self.assertEqual(result["kind"], "screenshot")
        self.factory.assert_called_once_with(self.root)
        self.assertIs(index, self.factory.return_value)
        self.assertEqual(index.recognize.call_args.args, (self.raw,))
        self.assertIs(index.recognize.call_args.kwargs["decoded"], self.decoded)
        self.decode.assert_called_once_with(self.raw)
        self.share.assert_called_once()
        self.chaldea.assert_called_once()

    def test_existing_game_index_is_reused_for_later_images(self):
        cached = Mock()
        cached.recognize.return_value = {"kind": "screenshot", "slots": []}
        _, index = screenshot_import.recognize_image(self.raw, self.root, cached)

        self.assertIs(index, cached)
        self.factory.assert_not_called()
        self.assertIs(cached.recognize.call_args.kwargs["decoded"], self.decoded)

    def test_damaged_maafgo_packet_never_falls_through_to_other_recognizers(self):
        for code in ("share_incomplete", "share_version", "share_integrity"):
            with self.subTest(code=code):
                error = EditorError(code, "分享标记无法完整恢复", 422)
                self.share.side_effect = error
                with self.assertRaises(EditorError) as raised:
                    screenshot_import.recognize_image(self.raw, self.root)
                self.assertIs(raised.exception, error)
                self.chaldea.assert_not_called()
                self.factory.assert_not_called()

    def test_identified_chaldea_without_unique_id_does_not_guess_game_team(self):
        for code in ("chaldea_missing_team_id", "chaldea_ambiguous_team_id"):
            with self.subTest(code=code):
                error = EditorError(code, "Chaldea 页脚中的 Team ID 无法确认", 422)
                self.chaldea.side_effect = error
                with self.assertRaises(EditorError) as raised:
                    screenshot_import.recognize_image(self.raw, self.root)
                self.assertIs(raised.exception, error)
                self.factory.assert_not_called()

    def test_invalid_input_is_rejected_before_any_recognizer(self):
        self.decode.side_effect = EditorError("image_dimensions", "图片尺寸超限", 413)
        with self.assertRaises(EditorError) as raised:
            screenshot_import.recognize_image(self.raw, self.root)
        self.assertEqual(raised.exception.code, "image_dimensions")
        self.share.assert_not_called()
        self.chaldea.assert_not_called()
        self.factory.assert_not_called()


def png(size):
    with Image.new("RGB", size, "white") as image, io.BytesIO() as stream:
        image.save(stream, "PNG")
        return stream.getvalue()


class LongShareImageInputTests(unittest.TestCase):
    def test_original_chaldea_page_dimensions_are_accepted(self):
        # The user's 1220 x 13289 example is under the existing 20M pixel cap.
        decoded = screenshot_import.decode_image(png((1220, 13289)))
        self.assertEqual(decoded.shape, (13289, 1220, 3))

    def test_footer_only_crop_can_reach_the_missing_team_id_diagnostic(self):
        decoded = screenshot_import.decode_image(png((465, 77)))
        self.assertEqual(decoded.shape, (77, 465, 3))

    def test_long_side_and_total_pixel_limits_remain_enforced(self):
        for size in ((64, 30001), (5000, 4001)):
            with self.subTest(size=size):
                # A header is enough to exercise the allocation boundary.
                image = Mock(format="PNG", n_frames=1, size=size)
                image.__enter__ = Mock(return_value=image)
                image.__exit__ = Mock(return_value=False)
                with patch("PIL.Image.open", return_value=image):
                    with self.assertRaises(EditorError) as raised:
                        screenshot_import.decode_image(b"image header")
                self.assertEqual(raised.exception.code, "image_dimensions")
                self.assertEqual(raised.exception.status, 413)
                image.convert.assert_not_called()

    def test_small_or_extremely_thin_images_remain_rejected(self):
        for size in ((100, 100), (465, 63)):
            with self.subTest(size=size):
                with self.assertRaises(EditorError) as raised:
                    screenshot_import.decode_image(png(size))
                self.assertEqual(raised.exception.code, "image_resolution")


if __name__ == "__main__":
    unittest.main()
