"""Mash forms retain Chaldea fields but share one visual/runtime identity.

These checks use the bundled portraits, real HTTP import/save, and the native
action converter/skill dispatcher. No emulator or network is required.
"""
import copy
import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom"), str(ROOT / "tests")]

from agent.battle_editor.catalog import build_catalog
from agent.battle_editor.server import create_server
from agent.battle_editor.validation import validate_battle
from agent.battle_editor.np_card_metadata import NpMetadataError, resolve_np_card, resolve_face_card
from agent.battle_editor.np_cards import NpCardRenderer, parse_np_card_query, parse_face_card_query
from battle.core.models import BattleAction
from battle.data.chaldea_converter import convert_chaldea_actions_to_battle_plan
from battle.data.servant_identity import canonical_servant_id, servant_template_ids
from battle.perception import card_owner, frontline
from battle.runtime.runtime import AutoBattleRuntime
from chaldea.servant_aliases import build_servant_lookup
from chaldea.support_criteria import build_support_criteria
from formation_test_support import battle_action, formation


def member_for(form, **updates):
    member = {
        "svtId": 800100, "supportType": "none",
        "limitCount": form["limitCount"], "skillIds": copy.deepcopy(form["skillIds"]),
        "tdId": form["tdId"], "skillLvs": [10, 10, 10], "tdLv": 5,
    }
    member.update(updates)
    return member


def plan_for(form, slot=1):
    front = [None, None, None]
    front[slot] = member_for(form)
    actor = {"initialSlot": slot, "svtId": 800100}
    return {
        "team": {"name": "玛修形态合同", "onFieldSvts": front,
                 "backupSvts": [None, None, None],
                 "mysticCode": {"mysticCodeId": 210, "level": 10}},
        "actions": [
            {"type": "skill", "svt": slot, "skill": index,
             "actor": copy.deepcopy(actor), "options": {"playerTarget": slot}}
            for index in (2, 0, 1)
        ] + [{"type": "attack", "attacks": [
            {"isTD": True, "card": -1, "svt": slot, "actor": copy.deepcopy(actor)}]}],
        "futureMashField": {"retained": [None, False, {"future": 1}]},
    }


class MashContractData:
    @classmethod
    def setUpClass(cls):
        cls.catalog = build_catalog(ROOT)
        cls.mash = next(row for row in cls.catalog["servants"] if row["id"] == 800100)
        cls.forms = cls.mash["forms"]
        if not cls.forms:
            raise AssertionError("The editor must expose at least one bundled Mash form")
        raw = json.loads((ROOT / "agent/custom/servant_list.json").read_text("utf-8-sig"))
        cls.lookup = build_servant_lookup(raw["servants"])


class MashRuntimeContractTests(MashContractData, unittest.TestCase):
    def test_all_registered_faces_remain_one_formation_identity(self):
        self.assertEqual(canonical_servant_id(800100), "800100")
        self.assertEqual(servant_template_ids(800100), ("800100",))
        action = formation.AutoFormationFromChaldea()
        registered = set(action._get_servant_info(800100)["images"])
        self.assertGreater(len(registered), 10)
        loaded = action._load_servant_templates(
            800100, [str(ROOT / "assets/resource/base/image/servant_face")])
        self.assertEqual({name for name, _image in loaded}, registered)
        self.assertTrue(all(image.size for _name, image in loaded))

    def test_every_registered_battle_portrait_matches_mash_without_form_filtering(self):
        hud_rois = ((0, 385, 318, 160), (318, 385, 318, 160), (636, 385, 318, 160))
        for family in ("ServantStatus", "CommandCards"):
            root = ROOT / "assets/resource/base/image" / family
            templates = card_owner._load_servant_templates((str(root),), "800100")
            registered = {f"800100/{path.name}" for path in (root / "800100").glob("*.png")}
            self.assertGreater(len(registered), 3)
            self.assertEqual({item.relative_path for item in templates}, registered)
            capacity = 3 if family == "ServantStatus" else 5
            # Exercise all actual art in a few frames; recognizers only receive
            # the canonical candidate, never the editor's selected form fields.
            for start in range(0, len(templates), capacity):
                group = templates[start:start + capacity]
                image = np.zeros((720, 1280, 3), dtype=np.uint8)
                rois = hud_rois if family == "ServantStatus" else tuple(card_owner.CARD_OWNER_ROIS.values())
                for item, (x, y, _width, _height) in zip(group, rois):
                    band = item.bgr
                    if family == "CommandCards":
                        band = cv2.resize(band, (round(band.shape[1] * .925), round(band.shape[0] * .925)),
                                          interpolation=cv2.INTER_CUBIC)
                    height, width = band.shape[:2]
                    image[y + 24:y + 24 + height, x + 8:x + 8 + width] = band
                if family == "ServantStatus":
                    recognizer = frontline.FrontlineServantRecognizer(
                        rois=hud_rois, scales=(1.0,), min_score=.98, min_margin=.05,
                        analysis_scale=1.0, roots=[root])
                    results = recognizer.detect(image, ("800100",))
                else:
                    recognizer = card_owner.CommandCardOwnerRecognizer(
                        roots=[root], min_score=.98, min_margin=.05, scales=(.925,))
                    results = recognizer.detect(image, ("800100", None, None))
                for item, result in zip(group, results):
                    with self.subTest(family=family, portrait=item.relative_path):
                        self.assertEqual(result.servant_id, "800100", result)
                        self.assertIn(result.matched_template, registered)

    def test_form_configuration_does_not_narrow_formation_or_support_images(self):
        action = formation.AutoFormationFromChaldea()
        all_images = set(self.lookup["800100"]["images"])
        for form in self.forms:
            for limit_count in form.get("limitCounts", [form["limitCount"]]):
                with self.subTest(form=form["key"], limit_count=limit_count):
                    data = plan_for(form)
                    member = data["team"]["onFieldSvts"][1]
                    member["limitCount"] = limit_count
                    original = copy.deepcopy(data)
                    expected = action._build_expected(data)
                    self.assertEqual(expected[1]["svt_id"], 800100)
                    self.assertEqual(expected[1]["kind"], "LOCAL")
                    self.assertEqual(battle_action._roster_from_share(data),
                                     (None, "800100", None, None, None, None))
                    self.assertEqual(data, original)
                    member["supportType"] = "friend"
                    criteria = build_support_criteria(data, self.lookup)
                    self.assertEqual(criteria["servant_id"], "800100")
                    self.assertEqual(set(criteria["images"]), all_images)
                    self.assertEqual(action._build_expected(data)[1]["kind"], "SUPPORT")

    def test_different_form_metadata_cannot_bypass_owned_duplicate_check(self):
        for first in self.forms:
            for second in self.forms:
                with self.subTest(first=first["key"], second=second["key"]):
                    data = plan_for(first)
                    data["team"]["backupSvts"][0] = member_for(second)
                    original = copy.deepcopy(data)
                    report = validate_battle(data, self.catalog)
                    duplicate = [issue for issue in report["issues"] if issue["code"] == "duplicate_servant"]
                    self.assertFalse(report["valid"])
                    self.assertEqual(len(duplicate), 1)
                    self.assertEqual(duplicate[0]["slot"], 3)
                    self.assertEqual(data, original)


class MashPreviewContractTests(unittest.TestCase):
    def test_all_registered_mash_costume_aliases_render_identical_np_and_faces(self):
        data = json.loads((ROOT / "agent/battle_editor/data/np_cards.json").read_text("utf-8"))
        costumes = [portrait for portrait in data["servants"]["800100"]["portraits"]
                    if portrait["group"] == "costume"]
        self.assertGreater(len(costumes), 3)
        renderer = NpCardRenderer(ROOT)
        for region in ("JP", "CN"):
            for portrait in costumes:
                short, chara = portrait["variant"], int(portrait["key"])
                with self.subTest(region=region, short=short, chara=chara):
                    np_bindings, np_images = [], []
                    for limit in (short, chara):
                        binding = resolve_np_card(ROOT, svt_id=800100, td_id=800107,
                                                  region=region, limit_count=limit)
                        self.assertEqual(binding.portrait_variant, short)
                        self.assertEqual(binding.portrait_source, "limit-count")
                        np_bindings.append(binding)
                        request = parse_np_card_query(
                            f"svtId=800100&tdId=800107&region={region}&limitCount={limit}")
                        np_images.append(renderer.render(request).png)
                        self.assertEqual(request.limit_count, limit)
                    self.assertEqual(*np_bindings)
                    self.assertEqual(*np_images)
                    self.assertTrue(np_images[0].startswith(b"\x89PNG"))
                    for color in ("arts", "buster", "quick"):
                        bindings, images = [], []
                        for limit in (short, chara):
                            bindings.append(resolve_face_card(ROOT, svt_id=800100, region=region,
                                                              color=color, limit_count=limit))
                            request = parse_face_card_query(
                                f"svtId=800100&color={color}&region={region}&limitCount={limit}")
                            images.append(renderer.render(request).png)
                        self.assertEqual(*bindings)
                        self.assertEqual(bindings[0].portrait_variant, short)
                        self.assertEqual(*images)

    def test_short_mash_count_selects_the_same_registered_costume_np_name_override(self):
        # The live Mash sidecar currently has no costume name override. Reuse
        # valid registered name assets to exercise that independent lookup path.
        data = json.loads((ROOT / "agent/battle_editor/data/np_cards.json").read_text("utf-8"))
        for region in ("JP", "CN"):
            regional = data["servants"]["800100"]["regions"][region]
            for chara in (800140, 800190, 800210):
                regional["costumeOverridesByChara"][str(chara)] = copy.deepcopy(regional["nps"]["800104"])
        with tempfile.TemporaryDirectory(prefix="Mash preview metadata ") as directory:
            root = Path(directory)
            metadata = root / "agent/battle_editor/data/np_cards.json"
            metadata.parent.mkdir(parents=True)
            metadata.write_text(json.dumps(data), "utf-8")
            for region in ("JP", "CN"):
                for short, chara in ((12, 800140), (17, 800190), (20, 800210)):
                    with self.subTest(region=region, short=short):
                        a = resolve_np_card(root, svt_id=800100, td_id=800107,
                                            region=region, limit_count=short)
                        b = resolve_np_card(root, svt_id=800100, td_id=800107,
                                            region=region, limit_count=chara)
                        self.assertEqual(a, b)
                        self.assertEqual(a.np_name_file, "card_servant_np1.png")

    def test_unknown_mash_costume_numbers_and_other_servants_are_not_relaxed(self):
        for region in ("JP", "CN"):
            # 16 is a gap among Mash's registered costume portraits; 21 is the
            # next number. Neither may silently fall back to an existing form.
            for sid, td, limit in ((800100, 800107, 16), (800100, 800107, 21),
                                   (800100, 800107, 9999), (100100, 100101, 11)):
                for kind in ("np", "face"):
                    with self.subTest(region=region, servant=sid, limit=limit, kind=kind):
                        with self.assertRaises(NpMetadataError) as caught:
                            if kind == "np":
                                resolve_np_card(ROOT, svt_id=sid, td_id=td,
                                                region=region, limit_count=limit)
                            else:
                                resolve_face_card(ROOT, svt_id=sid, color="arts",
                                                  region=region, limit_count=limit)
                        self.assertEqual(caught.exception.code, "np_portrait_unmapped")
            ordinary = resolve_face_card(ROOT, svt_id=100100, color="arts",
                                         region=region, limit_count=100130)
            self.assertEqual(ordinary.portrait_variant, 11)


class MashImportExecutionContractTests(MashContractData, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.temp = tempfile.TemporaryDirectory(prefix="Mash Chaldea 合同 ")
        cls.root = Path(cls.temp.name)
        web = cls.root / "assets/web/battle-editor"
        web.mkdir(parents=True)
        (web / "index.html").write_text("<!doctype html><title>合同测试</title>", "utf-8")
        cls.server = create_server(cls.root, 0)
        cls.server._catalog = cls.catalog
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        cls.thread.join(5)
        cls.server.server_close()
        cls.temp.cleanup()

    def request(self, method, path, payload):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        try:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            connection.request(method, path, body, {"Content-Type": "application/json",
                                                  "X-Editor-Token": self.server.token})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    @patch("mfaalog.info")
    def test_saved_and_imported_forms_dispatch_slots_and_skill_indexes_losslessly(self, _info):
        for form in self.forms:
            for limit_count in form.get("limitCounts", [form["limitCount"]]):
                with self.subTest(form=form["key"], limit_count=limit_count):
                    data = plan_for(form)
                    data["team"]["onFieldSvts"][1]["limitCount"] = limit_count
                    original = copy.deepcopy(data)
                    name = f"Mash_{form['key']}_{limit_count}.json"
                    status, record = self.request("POST", "/api/battles", {"name": name, "data": data})
                    self.assertEqual(status, 201, record)
                    self.assertTrue(record["validation"]["valid"], record)
                    status, imported = self.request("POST", "/api/import", {"source": name})
                    self.assertEqual(status, 200, imported)
                    self.assertEqual(imported["data"], original)
                    roster = battle_action._roster_from_share(imported["data"])
                    self.assertEqual(roster[1], "800100")
                    plan = convert_chaldea_actions_to_battle_plan(
                        imported["data"]["actions"], mystic_code_id=210, initial_servant_ids=roster)
                    turn = plan.turns[0]
                    self.assertEqual([(skill.servant_slot, skill.skill_index) for skill in turn.servant_skills],
                                     [(2, 3), (2, 1), (2, 2)])
                    self.assertEqual(turn.np_order, (2,))
                    calls = []

                    def cast(slot, index):
                        calls.append((slot, index))
                        return True

                    runtime = AutoBattleRuntime.__new__(AutoBattleRuntime)
                    runtime.decider = SimpleNamespace(plan=plan)
                    runtime.roster_servant_ids = roster
                    runtime.executor = SimpleNamespace(cast_servant_skill=cast)
                    # Device acknowledgement is outside this offline contract;
                    # the real dispatcher and actor-identity checks still run.
                    runtime._execute_skill_cast = lambda _kind, issue, *_args, **_kwargs: issue()
                    action = BattleAction(None, (), servant_skills=turn.servant_skills,
                                          skill_sequence=turn.skill_sequence)
                    self.assertTrue(runtime._execute_skills(action))
                    self.assertEqual(calls, [(2, 3), (2, 1), (2, 2)])
                    self.assertEqual(imported["data"], original)

    def test_form_difference_does_not_allow_duplicate_owned_mash_formal_save(self):
        data = plan_for(self.forms[0])
        data["team"]["backupSvts"][0] = member_for(self.forms[-1])
        original = copy.deepcopy(data)
        status, response = self.request("POST", "/api/battles", {"name": "Mash_duplicate.json", "data": data})
        self.assertEqual(status, 422, response)
        self.assertFalse((self.server.storage.battle_dir / "Mash_duplicate.json").exists())
        self.assertEqual(data, original)


if __name__ == "__main__":
    unittest.main()
