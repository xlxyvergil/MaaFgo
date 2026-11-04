"""Synthetic composition tests are behavior tests, not screenshot-accuracy claims."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent.battle.card_composer import CommandCardComposer, _premultiplied


def write_image(path, image):
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode('.png', image)
    assert ok
    encoded.tofile(path)


def solid(size, color):
    image = np.empty((size[1], size[0], 4), dtype=np.uint8)
    image[:] = color
    return image


class ComposerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='card-composer-')
        self.addCleanup(temp.cleanup)
        resource = Path(temp.name) / '中文资源'
        self.root = resource / 'base/image/CommandCards'
        self.cn_root = resource / 'cn/image/CommandCards'
        for color, bgr in [('buster', (30, 50, 180)), ('arts', (180, 50, 30)), ('quick', (30, 180, 50))]:
            shared = self.root / '_composition/shared'
            for part, size in [('bg', (133, 170)), ('icon', (185, 133)), ('txt', (150, 73))]:
                write_image(shared / f'card_{part}_{color}.png', solid(size, (*bgr, 170)))
                write_image(self.cn_root / '_composition/shared' / f'card_{part}_{color}.png', solid(size, (*reversed(bgr), 170)))
            write_image(shared / f'card_bg_np{color}.png', solid((140, 177), (*bgr, 210)))
            write_image(self.cn_root / '_composition/shared' / f'card_bg_np{color}.png', solid((140, 177), (*reversed(bgr), 210)))
        portrait = solid((256, 256), (60, 110, 170, 255))
        portrait[:20, :, 3] = 0
        write_image(self.root / '100/card_servant_1.png', portrait)
        write_image(self.root / '100/card_servant_11.png', solid((256, 256), (160, 80, 60, 255)))
        names = solid((256, 256), (60, 220, 250, 255))
        names[128:] = (0, 0, 0, 0)
        write_image(self.root / '_composition/np/100/card_servant_np.png', names)
        cn = names.copy()
        cn[:128, :, :3] = (250, 220, 60)
        write_image(self.cn_root / '_composition/np/100/card_servant_np.png', cn)
        self.composer = CommandCardComposer([self.root])

    def test_face_returns_bgra_and_binary_mask(self):
        card = self.composer.compose(100, 'arts')
        self.assertEqual(card.bgra.dtype, np.uint8)
        self.assertEqual(card.bgra.shape[2], 4)
        self.assertEqual(card.bgr.shape[:2], card.mask.shape)
        self.assertTrue(set(np.unique(card.mask)) <= {0, 255})
        np.testing.assert_array_equal(card.mask > 0, card.bgra[:, :, 3] >= 128)
        self.assertTrue(card.bgr.flags.c_contiguous)

    def test_single_root_path_is_accepted(self):
        card = CommandCardComposer(self.root).compose(100, 'arts')
        self.assertGreater(card.mask.size, 0)

    def test_face_language_is_explicit_and_default_jp_unchanged(self):
        default = self.composer.compose(100, 'arts')
        jp = self.composer.compose(100, 'arts', region='JP')
        cn = self.composer.compose(100, 'arts', region='CN')
        np.testing.assert_array_equal(default.bgra, jp.bgra)
        self.assertFalse(np.array_equal(jp.bgra, cn.bgra))
        with patch.object(self.composer, '_read', side_effect=AssertionError('cache miss')):
            np.testing.assert_array_equal(cn.bgra, self.composer.compose(100, 'arts', region='CN').bgra)

    def test_cn_never_falls_back_to_jp_text(self):
        (self.cn_root / '_composition/shared/card_txt_arts.png').unlink()
        with self.assertRaisesRegex(FileNotFoundError, 'region=CN'):
            self.composer.compose(100, 'arts', region='CN')
        (self.cn_root / '_composition/np/100/card_servant_np.png').unlink()
        with self.assertRaisesRegex(FileNotFoundError, 'region=CN'):
            self.composer.compose(100, 'arts', card_type='np', region='CN')

    def test_regional_override_and_common_fallback(self):
        relative = '_composition/shared/card_icon_arts.png'
        cn = self.composer._read(relative, expected=(185,133), region='CN')
        jp = self.composer._read(relative, expected=(185,133), region='JP')
        self.assertFalse(np.array_equal(cn,jp))
        (self.cn_root/relative).unlink()
        np.testing.assert_array_equal(self.composer._read(relative,expected=(185,133),region='CN'),jp)
        self.assertGreater(self.composer.compose(100,'arts',region='CN').mask.size,0)

    def test_explicit_custom_region_roots(self):
        composer = CommandCardComposer(self.root, region_roots={'CN': self.cn_root})
        np.testing.assert_array_equal(composer.compose(100,'arts',region='CN').bgra,self.composer.compose(100,'arts',region='CN').bgra)
        with self.assertRaisesRegex(ValueError, 'Unknown resource region'):
            CommandCardComposer(self.root, region_roots={'XX': self.cn_root})

    def test_tightly_cropped_cn_icon_is_not_stretched(self):
        write_image(self.cn_root / '_composition/shared/card_icon_quick.png', solid((155,116),(0,200,0,255)))
        with self.assertRaisesRegex(ValueError, 'unexpected size'):
            self.composer.compose(100, 'quick', region='CN')

    def test_cn_text_uses_saved_widget_size_without_affecting_jp(self):
        with patch('agent.battle.card_composer._premultiplied', wraps=_premultiplied) as resize:
            self.composer.compose(100, 'arts', region='CN')
            self.assertEqual(resize.call_args.args[1:], (158, 82))
            self.composer.compose(100, 'arts', region='JP')
            self.assertEqual(resize.call_args.args[1:], (188, 91))

    def test_np_language_is_explicit_and_isolated(self):
        with self.assertRaisesRegex(ValueError, 'explicit region'):
            self.composer.compose(100, 'quick', card_type='np')
        jp = self.composer.compose(100, 'quick', card_type='np', region='JP')
        cn = self.composer.compose(100, 'quick', card_type='np', region='CN')
        self.assertFalse(np.array_equal(jp.bgra, cn.bgra))
        with self.assertRaises(FileNotFoundError):
            self.composer.compose(100, 'quick', card_type='np', region='NA')

    def test_empty_np_row_never_reuses_another_name(self):
        with self.assertRaisesRegex(ValueError, 'Empty NP name row'):
            self.composer.compose(100, 'quick', card_type='np', region='JP', np_name_row=1)

    def test_explicit_second_np_row(self):
        names = solid((256, 256), (50, 90, 140, 255))
        names[128:, :, :3] = (240, 60, 40)
        write_image(self.root / '_composition/np/100/card_servant_np.png', names)
        a = self.composer.compose(100, 'arts', card_type='np', region='JP')
        b = self.composer.compose(100, 'arts', card_type='np', region='JP', np_name_row=1)
        self.assertFalse(np.array_equal(a.bgra, b.bgra))

    def test_missing_variant_does_not_choose_first_portrait(self):
        with self.assertRaises(FileNotFoundError):
            self.composer.compose(100, 'buster', portrait_variant=2)
        a = self.composer.compose(100, 'buster')
        b = self.composer.compose(100, 'buster', portrait_variant=11)
        self.assertFalse(np.array_equal(a.bgra, b.bgra))

    def test_invalid_arguments(self):
        for field, value in [('servant_id', '../100'), ('servant_id', True), ('portrait_variant', 0), ('color', 'red'), ('card_type', 'extra'), ('np_name_row', True), ('np_name_row', -1), ('np_name_row', 1), ('viewport', (1280, 800)), ('viewport', (1280.0, 720)), ('region', 'TW')]:
            args = {'servant_id': 100, 'color': 'arts', field: value}
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.composer.compose(**args)

    def test_two_resolutions(self):
        a = self.composer.compose(100, 'quick')
        b = self.composer.compose(100, 'quick', viewport=(2560, 1440))
        self.assertLessEqual(abs(b.bgra.shape[0] - 2 * a.bgra.shape[0]), 2)
        self.assertLessEqual(abs(b.bgra.shape[1] - 2 * a.bgra.shape[1]), 2)

    def test_cached_results_are_independent(self):
        a = self.composer.compose(100, 'arts')
        expected = a.bgra.copy()
        a.bgra[:] = 0
        a.mask[:] = 0
        with patch.object(self.composer, '_read', side_effect=AssertionError('cache miss')):
            b = self.composer.compose(100, 'arts')
        np.testing.assert_array_equal(b.bgra, expected)
        self.assertGreater(cv2.countNonZero(b.mask), 0)
        self.composer.clear_cache()
        self.assertEqual(self.composer.cache_info(), {'entries': 0, 'bytes': 0})

    def test_bounded_cache(self):
        composer = CommandCardComposer([self.root], cache_size=1)
        composer.compose(100, 'arts')
        composer.compose(100, 'buster')
        self.assertEqual(composer.cache_info()['entries'], 1)
        composer = CommandCardComposer([self.root], cache_bytes=1)
        composer.compose(100, 'arts')
        self.assertEqual(composer.cache_info()['entries'], 0)

    def test_corrupt_asset_reports_error(self):
        (self.root / '100/card_servant_1.png').write_bytes(b'bad png')
        with self.assertRaisesRegex(ValueError, 'BGRA'):
            self.composer.compose(100, 'arts')

    def test_wrong_shape_reports_error(self):
        write_image(self.root / '100/card_servant_1.png', solid((128, 128), (0, 0, 0, 255)))
        with self.assertRaisesRegex(ValueError, 'unexpected size'):
            self.composer.compose(100, 'arts')

    def test_premultiplied_resize_avoids_black_halos(self):
        image = np.array([[[0, 0, 255, 255], [0, 0, 0, 0]]], dtype=np.uint8)
        scaled = _premultiplied(image, 3, 1)
        self.assertAlmostEqual(float(scaled[0, 1, 2] / scaled[0, 1, 3]), 1.0)

    def test_no_additional_runtime_imports(self):
        path = ROOT / 'agent/battle/card_composer.py'
        tree = ast.parse(path.read_text('utf8'))
        imports = {a.name.split('.')[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for a in node.names}
        imports |= {(node.module or '').split('.')[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        self.assertFalse(imports & {'PIL', 'UnityPy', 'Crypto', 'urllib', 'maa'})

    def test_cli_rgba_export_and_overwrite_protection(self):
        output = self.root.parent / 'result.png'
        cmd = [sys.executable, '-X', 'utf8', str(ROOT / 'tools/compose_command_card.py'), '--servant-id', '100', '--color', 'quick', '--asset-root', str(self.root), '--output', str(output), '--rgba']
        completed = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        before = output.read_bytes()
        completed = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(output.read_bytes(), before)

    def test_cli_rejects_resource_destination(self):
        cmd = [sys.executable, '-X', 'utf8', str(ROOT / 'tools/compose_command_card.py'), '--servant-id', '100', '--color', 'quick', '--asset-root', str(self.root), '--output', str(self.root / 'new.png'), '--rgba']
        completed = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse((self.root / 'new.png').exists())

    def test_cli_cn_export_uses_same_runtime_module(self):
        output = self.root.parent/'cn.png'
        cmd = [sys.executable, '-X', 'utf8', str(ROOT/'tools/compose_command_card.py'), '--servant-id','100', '--color','arts', '--region','CN', '--asset-root',str(self.root), '--region-asset-root',f'CN={self.cn_root}', '--output',str(output), '--rgba']
        completed = subprocess.run(cmd,capture_output=True,text=True)
        self.assertEqual(completed.returncode,0,completed.stderr)
        actual = cv2.imdecode(np.fromfile(output,np.uint8),cv2.IMREAD_UNCHANGED)
        np.testing.assert_array_equal(actual,self.composer.compose(100,'arts',region='CN').bgra)

    def test_cli_rejects_regional_resource_destination(self):
        output = self.cn_root/'new.png'
        cmd = [sys.executable,'-X','utf8',str(ROOT/'tools/compose_command_card.py'),'--servant-id','100','--color','arts','--region','CN','--asset-root',str(self.root),'--output',str(output),'--rgba']
        completed = subprocess.run(cmd,capture_output=True,text=True)
        self.assertNotEqual(completed.returncode,0)
        self.assertFalse(output.exists())


class CompositionResourceTests(unittest.TestCase):
    def test_all_additional_images_are_verified_palette_pngs(self):
        for region,pack in [('JP','base'),('CN','cn')]:
            root = ROOT / f'assets/resource/{pack}/image/CommandCards/_composition'
            manifest = json.loads((root / 'manifest').read_text('utf8'))
            entries = list(manifest['shared'].values()) + list(manifest['np_names'].values())
            entries += [entry for names in manifest.get('np_extra_names', {}).values() for entry in names.values()]
            self.assertEqual(manifest['schema_version'],2)
            self.assertEqual(manifest['region'],region)
            self.assertEqual(len(manifest['shared']),12)
            self.assertGreaterEqual(len(entries),14)
            self.assertFalse((root/'localized').exists())
            self.assertFalse((root/'np/JP').exists())
            self.assertFalse((root/'np/CN').exists())
            self.assertEqual({str(p.relative_to(root)).replace('\\','/') for p in root.rglob('*.png')},{e['file'] for e in entries})
            for entry in entries:
                path = root / entry['file']
                data = path.read_bytes()
                self.assertEqual(data[:8], b'\x89PNG\r\n\x1a\n')
                self.assertEqual(data[25], 3, 'PNG IHDR colour type must be indexed/palette')
                self.assertEqual(hashlib.sha256(data).hexdigest(), entry['sha256'])
                image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
                self.assertIsNotNone(image)
                self.assertEqual(image.shape[2], 4)
                self.assertEqual([image.shape[1], image.shape[0]], entry['size'])

    def test_standalone_composer_with_packaged_samples(self):
        composer = CommandCardComposer()
        for color in ('arts', 'buster', 'quick'):
            self.assertGreater(composer.compose(100100, color).mask.size, 0)
            self.assertGreater(composer.compose(100100, color, region='CN').mask.size, 0)
        for region in ('JP', 'CN'):
            for sid, color in [(202200, 'quick'), (303800, 'arts')]:
                self.assertGreater(composer.compose(sid, color, card_type='np', region=region).mask.size, 0)

    def test_new_name_images_are_not_in_legacy_portrait_directories(self):
        root = ROOT / 'assets/resource/base/image/CommandCards'
        for folder in root.iterdir():
            if folder.is_dir() and folder.name.isdecimal():
                self.assertFalse(any('np' in p.name for p in folder.glob('*.png')))


if __name__ == '__main__':
    unittest.main()
