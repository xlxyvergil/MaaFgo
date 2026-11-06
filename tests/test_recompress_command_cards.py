"""Hash-source staging and concurrency guards in a temporary project."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import recompress_command_card_assets as tool


class RecompressionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        rgba = np.zeros((256, 256, 4), np.uint8)
        rgba[:, :, 0] = np.arange(256, dtype=np.uint8) * 7
        rgba[:, :, 1] = np.arange(256, dtype=np.uint8)[:, None] * 12
        rgba[:, :, 2] = 120
        rgba[:, :, 3] = np.resize(np.array([0, 1, 127, 128, 255], np.uint8), (256, 256))
        out = io.BytesIO(); Image.fromarray(rgba).save(out, 'PNG'); raw = out.getvalue()
        self.source = self.root / 'original.png'; self.source.write_bytes(raw)
        sha = hashlib.sha256(raw).hexdigest()
        self.index = self.root / 'sources.json'
        self.index.write_text(json.dumps({'sources': {sha: str(self.source)}}), encoding='utf8')
        self.run = self.root / 'run'
        for pack in ('base', 'cn'):
            root = self.root / f'assets/resource/{pack}/image/CommandCards'
            comp = root / '_composition'
            (comp / 'shared').mkdir(parents=True)
            (comp / 'shared/icon.png').write_bytes(raw)
            entry = {'file': 'shared/icon.png', 'source_sha256': sha, 'sha256': sha, 'size': [256, 256]}
            (comp / 'manifest').write_text(json.dumps({'schema_version': 2, 'region': 'JP' if pack == 'base' else 'CN', 'shared': {'icon': entry}, 'np_names': {}}), encoding='utf8')
            if pack == 'base':
                (root / '100').mkdir(); (root / '100/card_servant_1.png').write_bytes(raw)
                portrait = {**entry, 'file': '100/card_servant_1.png'}
                (root / 'manifest').write_text(json.dumps({'schema_version': 1, 'servants': {'100': [portrait]}}), encoding='utf8')
        self.target = self.root / 'assets/resource/base/image/CommandCards/100/card_servant_1.png'
        self.before = self.target.read_bytes()

    def test_stage_does_not_deploy_and_explicit_deployment_is_verified(self):
        tool.stage(self.root, self.index, self.run, workers=1)
        self.assertEqual(self.target.read_bytes(), self.before)
        report = json.loads((self.run / 'report.json').read_text('utf8'))
        self.assertEqual(report['status'], 'staged')
        self.assertEqual(report['summary']['images'], 3)
        tool.deploy(self.root, self.run)
        report = json.loads((self.run / 'report.json').read_text('utf8'))
        self.assertEqual(report['status'], 'deployed_and_verified')
        with Image.open(self.target) as image:
            self.assertEqual(image.mode, 'P')
        self.assertEqual((self.run / 'before/base/image/CommandCards/100/card_servant_1.png').read_bytes(), self.before)

    def test_changed_original_stops_before_any_deployment(self):
        self.source.write_bytes(b'bad original')
        with self.assertRaisesRegex(ValueError, 'Original hash mismatch'):
            tool.stage(self.root, self.index, self.run, workers=1)
        self.assertEqual(self.target.read_bytes(), self.before)

    def test_concurrent_user_edit_is_preserved(self):
        tool.stage(self.root, self.index, self.run, workers=1)
        self.target.write_bytes(b'user edit')
        with self.assertRaisesRegex(ValueError, 'changed after staging'):
            tool.deploy(self.root, self.run)
        self.assertEqual(self.target.read_bytes(), b'user edit')

    def test_formal_asset_cannot_be_used_as_original_source(self):
        index = json.loads(self.index.read_text('utf8'))
        index['sources'] = {key: str(self.target) for key in index['sources']}
        self.index.write_text(json.dumps(index), encoding='utf8')
        with self.assertRaisesRegex(ValueError, 'outside deployed resource roots'):
            tool.stage(self.root, self.index, self.run, workers=1)
        self.assertEqual(self.target.read_bytes(), self.before)


if __name__ == '__main__':
    unittest.main()
