"""Incremental formation fills preserve unrelated assets and exact identities."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image


SPEC = importlib.util.spec_from_file_location(
    "missing_formation_portrait_tests", Path(__file__).resolve().parents[1] / "tools/build_battle_editor_missing_portraits.py")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class MissingFormationPortraitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / 'project'
        self.source = Path(self.temp.name) / 'source'
        self.cache = Path(self.temp.name) / 'cache'
        self.output = self.project / 'agent/battle_editor/data/ui'
        self.source.mkdir()
        self.media = self.output / 'media'
        self.media.mkdir(parents=True)
        self.manifest_path = self.output / 'manifest.json'
        self.old_name = 'formation_200100_0123456789abcdef.webp'
        Image.new('RGB', (358, 507), 'blue').save(self.media / self.old_name, 'WEBP', quality=90)
        self.original = {
            'schema': 1, 'generator': 'original', 'sources': {'old': {'sha256': 'retained'}},
            'servants': {
                '100100': {'activeSkills': [{'id': 123}], 'appendSkills': [], 'future': {'keep': [None, True]}},
                '200100': {'formationPortrait': self.old_name, 'formPortraits': {'future-form': self.old_name}},
                '1700100': {'activeSkills': []},
                '1002100': {'activeSkills': []},
            },
            'files': {self.old_name: {'width': 358, 'height': 507, 'sha256': hashlib.sha256((self.media / self.old_name).read_bytes()).hexdigest()}},
            'missing': {'formationPortraits': [100100, 1700100, 1002100], 'portraits': [100100], 'skillIcons': []},
            'stats': {'servants': 4, 'formationPortraits': 1, 'formPortraitMappings': 1, 'formPortraitImages': 0},
        }
        self.rows = [
            {'id': 100100, 'type': 'normal', 'extraAssets': {'charaGraph': {'ascension': {'3': 'https://static.atlasacademy.io/JP/CharaGraph/100100/100100b@2.png'}}}},
            {'id': 1700100, 'type': 'enemyCollectionDetail', 'extraAssets': {'charaGraph': {'ascension': {'0': 'https://static.atlasacademy.io/JP/CharaGraph/1700100/1700100a.png'}}}},
        ]
        write_json(self.manifest_path, self.original)
        write_json(self.cache / 'JP_nice_servant.json', self.rows)
        write_json(self.project / 'agent/custom/servant_list.json', {'servants': [
            {'id': '100100', 'collection_no': 1, 'name': '甲', 'rarity': 5, 'class': 'saber'},
            {'id': '200100', 'collection_no': 2, 'name': '乙', 'rarity': 5, 'class': 'archer'},
            {'id': '1700100', 'collection_no': 152, 'name': '所罗门', 'rarity': 5, 'class': 'loreGrandCaster'},
        ]})
        self.card = self.source / '从者立绘/CharaGraph/100100/100100b@2.png'
        self.card.parent.mkdir(parents=True)
        Image.new('RGB', (512, 724), 'red').save(self.card)

    def build(self, ids=None, kind='atlas-cache'):
        return BUILDER.build_missing_portraits(self.project, self.source, self.cache, self.output,
                                              servant_ids=ids, source_kind=kind)

    def test_incremental_card_fill_preserves_forms_skills_sources_and_old_bytes_and_is_idempotent(self):
        old_path = self.media / self.old_name
        before = (old_path.read_bytes(), old_path.stat().st_mtime_ns)
        updated = self.build([100100])
        name = updated['servants']['100100']['formationPortrait']
        entry = updated['files'][name]
        self.assertEqual(entry['source']['kind'], 'atlas-cache')
        self.assertEqual(entry['source']['path'], '从者立绘/CharaGraph/100100/100100b@2.png')
        self.assertEqual(entry['source']['crop'], [0, 0, 512, 724])
        self.assertEqual(entry['source']['ascension'], 3)
        self.assertEqual(entry['source']['atlasURL'], self.rows[0]['extraAssets']['charaGraph']['ascension']['3'])
        self.assertEqual(updated['servants']['100100']['activeSkills'], self.original['servants']['100100']['activeSkills'])
        for sid in ('200100', '1700100', '1002100'):
            self.assertEqual(updated['servants'][sid], self.original['servants'][sid])
        self.assertEqual(updated['files'][self.old_name], self.original['files'][self.old_name])
        self.assertEqual(updated['sources']['old'], self.original['sources']['old'])
        self.assertEqual(updated['missing']['formationPortraits'], [1700100, 1002100])
        self.assertEqual(updated['missing']['portraits'], [100100])
        self.assertEqual((old_path.read_bytes(), old_path.stat().st_mtime_ns), before)
        with Image.open(self.media / name) as image:
            self.assertEqual((image.size, image.mode, image.format), ((358, 507), 'RGB', 'WEBP'))
            r, g, b = image.getpixel((100, 100));self.assertGreater(r, 200);self.assertLess(g + b, 30)
        snapshots = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in [self.manifest_path, *self.media.iterdir()]}
        self.assertEqual(self.build([100100]), updated)
        self.assertEqual(snapshots, {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in snapshots})

    def test_unframed_zero_ascension_requires_explicit_npc_metadata_and_crops_above_name_rows(self):
        path = self.source / '从者立绘/CharaGraph/1700100/1700100a.png'
        path.parent.mkdir(parents=True)
        image = Image.new('RGB', (1024, 1024), 'black')
        image.paste('green', (0, 0, 512, 724));image.save(path)
        updated = self.build([1700100], 'fgo-dump')
        entry = updated['files'][updated['servants']['1700100']['formationPortrait']]
        self.assertEqual(entry['source']['ascension'], 0)
        self.assertEqual(entry['source']['crop'], [0, 0, 512, 724])
        self.assertEqual(entry['source']['kind'], 'fgo-dump')
        self.assertEqual(updated['servants']['100100'], self.original['servants']['100100'])
        with Image.open(self.media / updated['servants']['1700100']['formationPortrait']) as saved:
            self.assertGreater(saved.getpixel((100, 506))[1], 90)
        self.rows[1]['type'] = 'normal'
        write_json(self.cache / 'JP_nice_servant.json', self.rows)
        write_json(self.manifest_path, self.original)
        with self.assertRaisesRegex(ValueError, 'Missing exact declared'):
            self.build([1700100], 'fgo-dump')

    def test_extracted_cache_rejects_an_unframed_texture_and_never_guesses_another_identity(self):
        before = self.manifest_path.read_bytes()
        Image.new('RGB', (1024, 1024), 'red').save(self.card)
        with self.assertRaisesRegex(ValueError, 'layout'):
            self.build([100100])
        self.assertEqual(self.manifest_path.read_bytes(), before)
        Image.new('RGB', (512, 724), 'red').save(self.card)
        self.rows[0]['extraAssets']['charaGraph']['ascension']['3'] = 'https://static.atlasacademy.io/JP/CharaGraph/100101/100101b@2.png'
        write_json(self.cache / 'JP_nice_servant.json', self.rows)
        with self.assertRaisesRegex(ValueError, 'Missing exact declared'):
            self.build([100100])
        self.assertEqual(self.manifest_path.read_bytes(), before)
        self.assertEqual([path.name for path in self.media.iterdir()], [self.old_name])

    def test_default_scope_skips_uneditable_registered_rows_and_explicit_scope_rejects_them(self):
        updated = self.build()
        self.assertIn('formationPortrait', updated['servants']['100100'])
        self.assertNotIn('formationPortrait', updated['servants']['1002100'])
        with self.assertRaisesRegex(ValueError, 'not registered editable'):
            self.build([1002100])
        self.assertEqual(updated['servants']['200100'], self.original['servants']['200100'])


class BundledFormationPortraitTests(unittest.TestCase):
    def test_every_editable_servant_has_a_registered_complete_card(self):
        root = Path(__file__).resolve().parents[1]
        from agent.battle_editor.catalog import build_catalog
        catalog = build_catalog(root)
        missing = [(row['id'], row['name']) for row in catalog['servants']
                   if not row.get('portrait') or (row.get('portraitWidth'), row.get('portraitHeight')) != (358, 507)]
        self.assertEqual(missing, [], '可编辑从者缺少完整卡图，请按准确的 CharaGraph 来源增量补齐 UI 资源。')


if __name__ == '__main__':
    unittest.main()
