"""Native composed recognition must survive desktop/Android package layouts."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import test_mxu_packaging as mxu_tests


ROOT = Path(__file__).resolve().parents[1]
COMPOSED_MODULE = 'agent/battle/perception/composed_card.py'
SPECIAL_CHOICE_MODULES = ('agent/battle_editor/special_choices.py', 'agent/battle/core/special_skills.py')
ADVANCED_STRATEGY_MODULE = 'agent/battle/core/advanced_strategy.py'
EQUIP_FILTER_FILES = ('agent/battle_editor/equip_filters.py', 'agent/battle_editor/data/equip_filters.json',
                      'agent/battle_editor/data/equip_filters_sources.json', 'agent/custom/equip_categories.json')
RUNTIME_MANIFESTS = (
    'assets/resource/base/image/CommandCards/manifest',
    'assets/resource/base/image/CommandCards/_composition/manifest',
    'assets/resource/cn/image/CommandCards/_composition/manifest',
)


class RuntimeBundleGuardTests(unittest.TestCase):
    def setUp(self):
        self.fixture = mxu_tests.MxuPackagingTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.write(COMPOSED_MODULE, 'runtime-module-fixture')
        self.fixture.document(RUNTIME_MANIFESTS[0], {
            'servants': {'100100': [{'file': '100100/card_servant_1.png'}]},
        })
        for relative, region in zip(RUNTIME_MANIFESTS[1:], ('JP', 'CN')):
            self.fixture.document(relative, {
                'region': region, 'shared': {'frame': {'file': 'shared/frame.png'}},
                'np_names': {'100100': {'file': 'np/100100/card_servant_np.png', 'name_rows': [0]}},
                'np_extra_names': {},
            })

    def test_mxu_guard_rejects_missing_runtime_module_and_manifests(self):
        mxu_tests.mxu.validate_editor_bundle(self.fixture.source)
        for relative in (COMPOSED_MODULE, *SPECIAL_CHOICE_MODULES, ADVANCED_STRATEGY_MODULE,
                         *EQUIP_FILTER_FILES, *RUNTIME_MANIFESTS):
            with self.subTest(relative=relative):
                path = self.fixture.source / relative
                original = path.read_bytes()
                try:
                    path.unlink()
                    with self.assertRaises(RuntimeError):
                        mxu_tests.mxu.validate_editor_bundle(self.fixture.source)
                finally:
                    path.write_bytes(original)

    def test_mxu_guard_rejects_invalid_runtime_manifest_json(self):
        for relative in RUNTIME_MANIFESTS:
            path = self.fixture.source / relative
            original = path.read_bytes()
            try:
                for invalid in (b'[]', b'not json'):
                    with self.subTest(relative=relative, invalid=invalid):
                        path.write_bytes(invalid)
                        with self.assertRaises(RuntimeError):
                            mxu_tests.mxu.validate_editor_bundle(self.fixture.source)
            finally:
                path.write_bytes(original)

    def test_mxu_guard_rejects_empty_or_wrong_region_runtime_manifests(self):
        for relative in RUNTIME_MANIFESTS:
            path = self.fixture.source / relative
            original = path.read_bytes()
            valid = json.loads(original)
            invalid_values = [{}]
            if relative == RUNTIME_MANIFESTS[0]:
                invalid_values.append({'servants': []})
            else:
                invalid_values += [{**valid, 'region': 'NA'}, {**valid, 'shared': {}},
                                   {**valid, 'np_names': {}}, {**valid, 'np_extra_names': []}]
            try:
                for invalid in invalid_values:
                    with self.subTest(relative=relative, invalid=invalid):
                        path.write_text(json.dumps(invalid), encoding='utf8')
                        with self.assertRaises(RuntimeError):
                            mxu_tests.mxu.validate_editor_bundle(self.fixture.source)
            finally:
                path.write_bytes(original)


class PackagedRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='battle-runtime-package-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.project = Path(cls.temp.name) / 'packaged'
        spec = importlib.util.spec_from_file_location('android_runtime_bundle_tests', ROOT / 'tools/prepare_android.py')
        android = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(android)
        # Exercise the real Android exclusion rules without creating an install
        # directory or copying the large editor image bundle.
        android.copy_agent(ROOT / 'agent', cls.project / 'agent')
        cls.identities = ('504500', '505600', '505700', '1002000', '1002100')
        base = ROOT / 'assets/resource/base/image/CommandCards'
        target_base = cls.project / 'resource/base/image/CommandCards'
        target_base.mkdir(parents=True)
        shutil.copy2(base / 'manifest', target_base / 'manifest')
        for sid in cls.identities:
            shutil.copytree(base / sid, target_base / sid)
        for pack in ('base', 'cn'):
            source = ROOT / f'assets/resource/{pack}/image/CommandCards/_composition'
            target = cls.project / f'resource/{pack}/image/CommandCards/_composition'
            target.mkdir(parents=True)
            shutil.copy2(source / 'manifest', target / 'manifest')
            shutil.copytree(source / 'shared', target / 'shared')
            for sid in cls.identities:
                if (source / 'np' / sid).is_dir():
                    shutil.copytree(source / 'np' / sid, target / 'np' / sid)

    def isolated(self, code):
        bootstrap = '''
import os, sys
from pathlib import Path
root = Path(sys.argv[1]).resolve()
os.environ['MAAFGO_PLATFORM'] = 'android'
sys.path[:0] = [str(root / 'agent'), str(root / 'agent/custom')]
assert not (root / 'assets').exists()
assert not (root / 'agent/battle_editor').exists()
assert not (root / 'agent/battle_editor/data/np_cards.json').exists()
'''
        result = subprocess.run([sys.executable, '-I', '-B', '-c', bootstrap + code, str(self.project)],
                                cwd=self.project, capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_android_copy_imports_native_modules_from_resource_layout_without_editor(self):
        self.isolated('''
from battle.runtime.runtime import AutoBattleRuntime
from battle.perception import perception, composed_card
from battle.core import special_skills
import json
from battle.data.servant_identity import canonical_servant_id
from platform_waits import is_android
assert is_android()
assert Path(composed_card.__file__).resolve().is_relative_to(root / 'agent')
assert Path(special_skills.__file__).resolve().is_relative_to(root / 'agent')
assert len(special_skills.SPECIAL_SKILL_GROUPS) == 14
categories = json.loads((root / 'agent/custom/equip_categories.json').read_text(encoding='utf-8'))
assert categories['equips']['9300070']['filter_tag'] == '羁绊礼装'
equips = json.loads((root / 'agent/custom/equip_list.json').read_text(encoding='utf-8'))['equips']
assert next(row for row in equips if row['id'] == '9300070')['filter_tag'] == '羁绊礼装'
recognizer = composed_card.ComposedCardRecognizer.default()
assert recognizer.root == root
assert recognizer.composer.roots == (root / 'resource/base/image/CommandCards',)
assert recognizer._metadata == {}
assert recognizer._prepare('504500', 'np').complete
assert recognizer._prepare('1002000', 'np').complete
assert canonical_servant_id('505700') == '505600'
assert canonical_servant_id('1002100') == '1002000'
assert not any(name.startswith('battle_editor') for name in sys.modules)
''')

    def test_packaged_alias_and_transformed_portrait_match_without_editor_sidecar(self):
        self.isolated('''
import numpy as np
from battle.core.enums import CardColor
from battle.perception.composed_card import ComposedCardRecognizer
from battle.data.servant_identity import servant_template_ids
recognizer = ComposedCardRecognizer(region='JP')
assert set(servant_template_ids('505600')) == {'505600', '505700'}
assert set(servant_template_ids('1002100')) == {'1002000', '1002100'}
assert {sid for sid, _ in recognizer._forms('505600', 'JP')} == {'505600', '505700'}
assert recognizer._prepare('505600', 'np').complete
image = np.full((720, 1280, 3), 30, np.uint8)
card = recognizer.composer.compose('505700', 'buster', portrait_variant=11,
                                   card_type='np', region='JP')
left, top = round(410 - card.anchor[0]), round(217 - card.anchor[1])
h, w = card.bgra.shape[:2]
pixels = image[top:top + h, left:left + w]
alpha = card.bgra[:, :, 3:4].astype(np.float32) / 255
pixels[:] = np.rint(card.bgr * alpha + pixels * (1 - alpha)).astype(np.uint8)
match = recognizer.detect_np(image, ('505600', '504500', '1002000'))[0]
assert match.visual_present and match.authoritative, match
assert match.servant_id == '505600' and match.owner_slot == 1, match
assert match.color is CardColor.BUSTER, match
assert '/505700/v11/' in match.template, match
''')


if __name__ == '__main__':
    unittest.main()
