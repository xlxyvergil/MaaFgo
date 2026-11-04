"""Composed card contracts use real registered assets, without a device/editor."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'agent'), str(ROOT / 'agent/custom')]
from battle.card_composer import CommandCardComposer
from battle.core.enums import CardColor
from battle.perception.composed_card import ComposedCardRecognizer

ROSTER = ('504500', '105900', '505400')
ASSET_SERVANTS = (*ROSTER, '800100', '202200')


def canvas():
    return np.full((720, 1280, 3), 30, np.uint8)


def place(image, card, slot, kind='face', *, overlays=False):
    center = 128 + 256 * (slot - 1) if kind == 'face' else 410 + 230 * (slot - 1)
    anchor_y = 515 if kind == 'face' else 217
    left, top = round(center - card.anchor[0]), round(anchor_y - card.anchor[1])
    h, w = card.bgra.shape[:2]
    pixels = image[top:top + h, left:left + w]
    alpha = card.bgra[:, :, 3:4].astype(np.float32) / 255
    pixels[:] = np.rint(card.bgr * alpha + pixels * (1 - alpha)).astype(np.uint8)
    if overlays:
        # Buff/critical/support markers only affect the deliberately excluded
        # top band; unrelated HUD below the card is excluded independently.
        pixels[:40] = (20, 220, 230)
        pixels[234:] = (220, 20, 100)
        if kind == 'np':
            pixels[82:138] = (0, 0, 0)  # sealed card's crossing status banner


class ComposedCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='composed-card-contract-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.project = Path(cls.temp.name)
        base = ROOT / 'assets/resource/base/image/CommandCards'
        cls.base = cls.project / 'assets/resource/base/image/CommandCards'
        cls.base.mkdir(parents=True)
        shutil.copy2(base / 'manifest', cls.base / 'manifest')
        for sid in ASSET_SERVANTS:
            shutil.copytree(base / sid, cls.base / sid)
        for region, origin in [('base', base), ('cn', ROOT / 'assets/resource/cn/image/CommandCards')]:
            target = cls.project / f'assets/resource/{region}/image/CommandCards/_composition'
            target.mkdir(parents=True)
            shutil.copy2(origin / '_composition/manifest', target / 'manifest')
            shutil.copytree(origin / '_composition/shared', target / 'shared')
            for sid in ASSET_SERVANTS:
                shutil.copytree(origin / f'_composition/np/{sid}', target / f'np/{sid}')
        cls.composer = CommandCardComposer(cls.base, cache_size=128)
        cls.recognizer = ComposedCardRecognizer(cls.project)

    def compose(self, sid='105900', color='arts', *, kind='face', variant=3, region='JP', file=None, row=0):
        return self.composer.compose(sid, color, card_type=kind, portrait_variant=variant,
                                     region=region, np_name_file=file, np_name_row=row)

    def test_manifest_only_project_imports_without_editor_package(self):
        self.assertFalse((self.project / 'agent/battle_editor').exists())
        code = ("import sys;sys.path.insert(0,sys.argv[1]);sys.modules['battle_editor']=None;"
                "from battle.perception.composed_card import ComposedCardRecognizer;"
                "r=ComposedCardRecognizer(sys.argv[2]);"
                "assert r._prepare('504500','np').complete;print('ok')")
        run = subprocess.run([sys.executable, '-B', '-c', code, str(ROOT / 'agent'), str(self.project)],
                             capture_output=True, text=True, check=True)
        self.assertEqual(run.stdout.strip(), 'ok')

    def test_all_face_colours_and_current_identities_are_matched(self):
        image = canvas()
        for slot, sid, color in ((1, '105900', 'quick'), (2, '505400', 'arts'),
                                  (3, '504500', 'buster'), (4, '105900', 'arts'),
                                  (5, '505400', 'quick')):
            place(image, self.compose(sid, color, variant=2 if sid == '504500' else 3), slot)
        results = self.recognizer.detect(image, ROSTER)
        self.assertEqual([(r.owner_slot, r.color) for r in results],
                         [(2, CardColor.QUICK), (3, CardColor.ARTS), (1, CardColor.BUSTER),
                          (2, CardColor.ARTS), (3, CardColor.QUICK)])
        self.assertTrue(all(r.visual_present and r.authoritative for r in results))
        self.assertTrue(all(r.confidence.value == r.best_score == r.score for r in results))

    def test_dynamic_overlays_do_not_change_identity_or_colour(self):
        image = canvas()
        place(image, self.compose(), 1, overlays=True)
        result = self.recognizer.detect(image, ROSTER)[0]
        self.assertTrue(result.visual_present)
        self.assertEqual((result.owner_slot, result.color), (2, CardColor.ARTS))

    def test_np_manifest_names_are_crossed_with_each_registered_portrait_and_colour(self):
        image = canvas()
        # The story-unlocked name is also usable with portrait2; a preview-only
        # ascension/name binding must not exclude this real in-game combination.
        place(image, self.compose('504500', 'arts', kind='np', variant=2, file='504510.png'), 1, 'np', overlays=True)
        place(image, self.compose(kind='np'), 2, 'np')
        results = self.recognizer.detect_np(image, ROSTER)
        self.assertEqual([r.visual_present for r in results], [True, True, False])
        self.assertIn('504510.png', results[0].template)
        self.assertEqual([r.owner_slot for r in results], [1, 2, None])
        self.assertTrue(all(r.authoritative for r in results))
        # Colour changes also apply to NP cards without requiring deck guesses.
        image = canvas()
        place(image, self.compose(kind='np', color='buster'), 2, 'np')
        self.assertEqual(self.recognizer.detect_np(image, ROSTER)[1].color, CardColor.BUSTER)

    def test_target_scenery_is_a_complete_negative_and_neighbour_does_not_leak(self):
        image = canvas()
        cv2.putText(image, 'TARGET', (265, 205), cv2.FONT_HERSHEY_SIMPLEX, .8, (230, 230, 230), 2)
        place(image, self.compose(kind='np'), 2, 'np')
        results = self.recognizer.detect_np(image, ROSTER)
        self.assertFalse(results[0].visual_present)
        self.assertTrue(results[0].authoritative)
        self.assertTrue(results[1].visual_present)
        self.assertFalse(results[2].visual_present)

    def test_duplicate_face_identity_does_not_guess_slot_but_np_uses_physical_slot(self):
        image = canvas()
        place(image, self.compose(), 1)
        result = self.recognizer.detect(image, ('105900', '105900', '505400'))[0]
        self.assertTrue(result.visual_present)
        self.assertIsNone(result.owner_slot)
        self.assertEqual(result.reason, 'duplicate_frontline_identity')
        image = canvas()
        place(image, self.compose(kind='np'), 2, 'np')
        result = self.recognizer.detect_np(image, ('105900', '105900', '505400'))[1]
        self.assertTrue(result.visual_present)
        self.assertEqual(result.owner_slot, 2)

    def test_jp_and_cn_are_explicitly_registered_separate_candidates(self):
        for region in ('JP', 'CN'):
            with self.subTest(region=region):
                image = canvas()
                place(image, self.compose(kind='np', region=region), 2, 'np')
                result = self.recognizer.detect_np(image, ROSTER)[1]
                self.assertTrue(result.visual_present)
                self.assertTrue(result.template.startswith(region + '/'))

    def test_unknown_or_missing_assets_never_claim_authoritative_absence(self):
        image = canvas()
        self.assertTrue(all(not r.authoritative for r in self.recognizer.detect_np(image, (None, None, None))))
        result = self.recognizer.detect_np(image, ('999999999', '105900', '505400'))[0]
        self.assertFalse(result.authoritative)
        self.assertEqual(result.reason, 'candidate_assets_incomplete')
        with tempfile.TemporaryDirectory() as directory:
            incomplete = ComposedCardRecognizer(directory)
            self.assertTrue(all(not r.authoritative for r in incomplete.detect_np(image, ROSTER)))

    def test_native_registered_alias_keeps_canonical_owner_without_editor_sidecar(self):
        recognizer = ComposedCardRecognizer(self.project, region='JP')
        with patch('battle.perception.composed_card.servant_template_ids', return_value=('504500', '105900', '123456')):
            # A catalog-declared alternate resource directory is visual evidence
            # for the input identity, not permission to rewrite the roster.
            image = canvas()
            place(image, self.compose('105900'), 1)
            result = recognizer.detect(image, ('504500', None, None))[0]
            self.assertTrue(result.visual_present)
            self.assertEqual((result.owner_slot, result.servant_id), (1, '504500'))
            self.assertIn('/105900/', result.template)
            self.assertTrue(recognizer._prepare('504500', 'np').complete)

    def test_malformed_manifest_is_unknown_instead_of_crashing_runtime(self):
        recognizer = ComposedCardRecognizer(self.project, region='JP')
        recognizer._np_manifests['JP'] = {'np_names': [], 'np_extra_names': 'invalid'}
        result = recognizer.detect_np(canvas(), ROSTER)
        self.assertTrue(all(not r.authoritative and not r.visual_present for r in result))

    def test_scaled_screenshot_preserves_same_card_and_np_decisions(self):
        image = canvas()
        place(image, self.compose(), 3)
        place(image, self.compose(kind='np'), 2, 'np')
        for size in ((640, 360), (1920, 1080)):
            with self.subTest(size=size):
                scaled = cv2.resize(image, size, interpolation=cv2.INTER_LINEAR)
                face, np_cards = self.recognizer.detect_all(scaled, ROSTER)
                self.assertEqual((face[2].owner_slot, face[2].color), (2, CardColor.ARTS))
                self.assertEqual([r.visual_present for r in np_cards], [False, True, False])

    def test_prepared_warm_cache_has_no_composition_io_and_bounds_hold(self):
        recognizer = ComposedCardRecognizer(self.project, region='JP')
        recognizer.detect_all(canvas(), ROSTER)
        with patch.object(recognizer.composer, 'compose', side_effect=AssertionError('cold composition')):
            recognizer.detect_all(canvas(), ROSTER)
        bounded = ComposedCardRecognizer(self.project, region='JP', cache_size=1, cache_bytes=1024 * 1024)
        for sid in ROSTER:
            bounded._prepare(sid, 'face')
        info = bounded.cache_info()
        self.assertLessEqual(info['entries'], 1)
        self.assertLessEqual(info['bytes'], 1024 * 1024)

    def test_np_multiple_registered_rows_and_missing_asset_handling(self):
        manifest = self.project / 'assets/resource/base/image/CommandCards/_composition/manifest'
        data = json.loads(manifest.read_text('utf-8'))
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            target = project / 'assets/resource/base/image/CommandCards'
            shutil.copytree(self.base, target)
            data['np_names']['105900']['name_rows'] = [0, 1]
            # A registered empty row is not a licence to substitute another name;
            # it makes absence non-authoritative while valid row0 stays usable.
            (target / '_composition/manifest').write_text(json.dumps(data), 'utf-8')
            recognizer = ComposedCardRecognizer(project, region='JP')
            prepared = recognizer._prepare('105900', 'np')
            self.assertFalse(prepared.complete)
            self.assertTrue(prepared.templates)

    def test_registered_mash_second_row_and_real_quick_np_with_auto_region(self):
        # Exercise genuinely registered second-row/Quick resources rather than
        # inventing a second row in an otherwise single-row NP texture.
        for sid, color, row, expected_color in (
                ('800100', 'arts', 1, CardColor.ARTS),
                ('202200', 'quick', 0, CardColor.QUICK)):
            with self.subTest(servant=sid, row=row):
                image = canvas()
                place(image, self.compose(sid, color, kind='np', variant=1, row=row), 1, 'np')
                result = self.recognizer.detect_np(image, (sid, '105900', '505400'))[0]
                self.assertTrue(result.visual_present, result)
                self.assertTrue(result.authoritative, result)
                self.assertEqual((result.owner_slot, result.servant_id, result.color),
                                 (1, sid, expected_color))
                self.assertIn(f':row{row}', result.template)

    def live_frame(self, prefix):
        # Raw local emulator evidence stays out of the repository. CI without
        # these optional replay inputs still runs all portable asset contracts.
        directory = ROOT / '.workbuddy/runtime/battle-live-20261028/frames'
        paths = sorted(directory.glob(prefix + '*COMMAND_SELECTION*.png'))
        if not paths:
            self.skipTest('local JP battle replay frames are unavailable')
        image = cv2.imdecode(np.fromfile(paths[0], np.uint8), cv2.IMREAD_COLOR)
        self.assertIsNotNone(image)
        return image

    def test_live_roster_switch_rebinds_slots_and_does_not_reuse_removed_identity(self):
        image = self.live_frame('002')
        recognizer = ComposedCardRecognizer(self.project)
        first_faces, first_np = recognizer.detect_all(image, ROSTER)
        self.assertEqual(first_faces[0].owner_slot, 2)
        self.assertTrue(first_np[1].visual_present)
        swapped_faces, swapped_np = recognizer.detect_all(image, ('504500', '505400', '105900'))
        self.assertEqual([r.owner_slot for r in swapped_faces], [3, 2, 2, 3, 3])
        self.assertEqual([r.servant_id for r in swapped_faces], [r.servant_id for r in first_faces])
        # Pixels still show Yamato's slot2 NP while the supplied roster now says
        # Yui occupies slot2. Old cached evidence must not assert an NP for Yui.
        self.assertFalse(any(r.visual_present for r in swapped_np))
        removed_faces, removed_np = recognizer.detect_all(image, ('504500', '202200', '505400'))
        self.assertFalse(any(r.servant_id == '105900' for r in removed_faces))
        self.assertFalse(removed_np[1].visual_present)
        self.assertTrue(removed_np[1].authoritative)

    def test_live_jpeg_and_scale_preserve_quick_colour_sealed_np_and_background_negatives(self):
        expected = {
            '002': ([(2, CardColor.QUICK), (3, CardColor.ARTS), (3, CardColor.BUSTER),
                     (2, CardColor.BUSTER), (2, CardColor.ARTS)], [2]),
            '006': ([(2, CardColor.BUSTER), (3, CardColor.QUICK), (3, CardColor.ARTS),
                     (3, CardColor.ARTS), (1, CardColor.ARTS)], [1]),
            '014': ([(3, CardColor.QUICK), (3, CardColor.BUSTER), (1, CardColor.ARTS),
                     (1, CardColor.ARTS), (2, CardColor.QUICK)], [1]),
        }
        for prefix, (expected_faces, expected_np) in expected.items():
            original = self.live_frame(prefix)
            for size, quality in (((640, 360), 80), ((1920, 1080), 80), ((640, 360), 50)):
                with self.subTest(frame=prefix, size=size, jpeg_quality=quality):
                    scaled = cv2.resize(original, size, interpolation=(cv2.INTER_AREA if size[0] < 1280
                                                                      else cv2.INTER_LINEAR))
                    ok, encoded = cv2.imencode('.jpg', scaled, [cv2.IMWRITE_JPEG_QUALITY, quality])
                    self.assertTrue(ok)
                    jpeg = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
                    faces, nps = self.recognizer.detect_all(jpeg, ROSTER)
                    self.assertEqual([(r.owner_slot, r.color) for r in faces], expected_faces)
                    # NP existence includes the sealed card in 006; availability
                    # remains the separate restriction recognizer's decision.
                    self.assertEqual([r.ui_slot for r in nps if r.visual_present], expected_np)
                    self.assertTrue(all(r.authoritative for r in nps))


if __name__ == '__main__':
    unittest.main()
