"""Legacy command downloads use the common guard; status behavior is untouched."""
import hashlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import update_command_card_assets as tool


class LegacyDownloadTests(unittest.TestCase):
    def test_command_download_is_guarded_and_preserves_source_for_cache_reuse(self):
        image = Image.new('RGBA', (256, 256), (100, 160, 220, 1))
        image.paste((220, 160, 100, 255), (30, 30, 180, 180))
        image.paste((0, 0, 0, 0), (0, 0, 20, 256))
        stream = io.BytesIO(); image.save(stream, 'PNG'); raw = stream.getvalue()
        asset = {'filename': 'card_servant_1.png', 'group': 'ascension', 'key': '1', 'url': 'https://static.atlasacademy.io/JP/Servants/Commands/100/card_servant_1.png'}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(tool, '_request', return_value=raw) as request:
                entry = tool._download_one('100', asset, root / 'assets', False, 'commands', backup=root / 'backup')
                cached = tool._download_one('100', asset, root / 'assets', False, 'commands', previous=entry, backup=root / 'backup')
                self.assertEqual(request.call_count, 1)
            self.assertEqual(entry, cached)
            self.assertEqual(entry['source_sha256'], hashlib.sha256(raw).hexdigest())
            self.assertEqual((root / 'backup/originals/100/card_servant_1.png').read_bytes(), raw)
            with Image.open(root / 'assets/100/card_servant_1.png') as saved:
                self.assertEqual(saved.mode, 'P')
                before, after = np.asarray(image)[:, :, 3], np.asarray(saved.convert('RGBA'))[:, :, 3]
                np.testing.assert_array_equal(before == 0, after == 0)
                np.testing.assert_array_equal(before >= 128, after >= 128)

    def test_status_download_keeps_original_bytes(self):
        stream = io.BytesIO(); Image.new('RGBA', (256, 256)).save(stream, 'PNG'); raw = stream.getvalue()
        asset = {'filename': 'status_servant_1.png', 'group': 'ascension', 'key': '1', 'url': 'https://static.atlasacademy.io/JP/Servants/Status/100/status_servant_1.png'}
        with tempfile.TemporaryDirectory() as temporary, patch.object(tool, '_request', return_value=raw):
            root = Path(temporary)
            result = tool._download_one('100', asset, root, False, 'status')
            self.assertEqual((root / result['file']).read_bytes(), raw)
            self.assertNotIn('quantization_profile', result)


if __name__ == '__main__':
    unittest.main()
