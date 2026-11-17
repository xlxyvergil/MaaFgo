"""Installed optional templates participate in real support image matching."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import zipfile

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]
from maa.agent.agent_server import AgentServer
with patch.object(AgentServer, "custom_action", lambda *args: lambda cls: cls):
    import support_action as support
from support_ce import install_extension
from battle_editor.catalog import build_catalog, resolve_media


class InstalledSupportCERuntimeTests(unittest.TestCase):
    origin = (36, 80)
    ids = (9900001, 9900002)

    def test_any_tries_normal_template_when_star_dominates_sparse_artwork(self):
        eid = self.ids[0]
        normal = np.full((44, 158, 3), 245, np.uint8)
        normal[5:9, 12:16] = 160
        maximum = normal.copy()
        maximum[24:41, 133:150] = (5, 220, 230)
        self.cards[(eid, "非满破")], self.cards[(eid, "满破")] = normal, maximum
        self.install([(eid, "留白画面", ("满破", "非满破"))])
        frame = self.frame([(support.CE_ANCHOR_NORMAL, eid, "非满破")])
        self.assertTrue(self.matches(frame, f"support-ce:{eid}", "任意"))

    def test_new_extension_identity_resolves_names_offline_for_chaldea(self):
        from chaldea.support_criteria import build_support_criteria
        data = {"team": {"onFieldSvts": [{"supportType": "friend", "equip1": {
            "id": self.ids[0], "limitBreak": True}}, None, None], "backupSvts": [None] * 3}}
        with patch("chaldea.game_data.get_equip_name", side_effect=AssertionError("must not fetch online")):
            criteria = build_support_criteria(data, {}, resolve_name=support._support_ce_name)
        self.assertEqual(criteria["ce_specs"][0]["id"], self.ids[0])
        self.assertEqual(criteria["ce_specs"][0]["name"], "同名礼装.png")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ce_dir = self.root / "assets/resource/base/image/lizhuang"
        self.ce_dir.mkdir(parents=True)
        self.cards = {}
        for index, eid in enumerate(self.ids):
            normal = np.random.default_rng(1800 + index).integers(20, 220, (44, 158, 3), dtype=np.uint8)
            maximum = normal.copy()
            maximum[25:40, 133:148] = (10, 245, 250)
            self.cards[(eid, "非满破")] = normal
            self.cards[(eid, "满破")] = maximum
        self.install([(eid, "同名礼装", ("满破", "非满破")) for eid in self.ids])
        for name, value in (("_ROOT_DIR", str(self.root)), ("time.sleep", None),
                            ("mfaalog.info", None), ("mfaalog.error", None), ("mfaalog.warning", None)):
            target = "support_action." + name
            item = patch(target, value) if name == "_ROOT_DIR" else patch(target)
            item.start()
            self.addCleanup(item.stop)

    def install(self, entries):
        files, rows = {}, []
        for eid, name, states in entries:
            templates = {}
            for state in states:
                relative = f"{state}/f_{eid}0.png"
                output = io.BytesIO()
                Image.fromarray(self.cards[(eid, state)]).save(output, "PNG")
                templates[state] = relative
                files[relative] = output.getvalue()
            rows.append({"id": eid, "name": name, "templates": templates})
        manifest = {"schemaVersion": 1, "type": "maafgo-support-ce", "name": "运行时回归包",
                    "version": "1", "equips": rows,
                    "sha256": {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}}
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
            for name, raw in files.items():
                archive.writestr(name, raw)
        install_extension(self.root, output.getvalue())

    def frame(self, cells):
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        for anchor, eid, state in cells:
            x, y = self.origin[0] + anchor[0] + 3, self.origin[1] + anchor[1] + 3
            image[y:y + 44, x:x + 158] = self.cards[(eid, state)]
        return image

    def matches(self, frame, name, state, action=None, anchor=None):
        return (action or support.SupportAction())._match_ce(
            frame, str(self.ce_dir), *self.origin, name,
            anchor or support.CE_ANCHOR_NORMAL, state)

    def spec(self, eid, status="满破", name="相同翻译名.png"):
        return {"id": eid, "name": name, "status": status}

    def search(self, frame, groups, *, grand=False, attach=None):
        settings = {"chaldea_enabled": True, "chaldea_import_source": "test-share"}
        if attach is not None:
            settings = attach
        context = NS(tasker=NS(stopping=False, controller=object()),
                     get_node_data=Mock(return_value={"attach": settings}))
        criteria = {"support_type": "grand" if grand else "normal", "class_name": "any",
                    "servant_id": "", "ce_bond": "any", "active": [0, 0, 0],
                    "passive": [0] * 5, "np_level": 0, "level": 0,
                    "ce_groups": groups, "ce_specs": []}
        action = support.SupportAction()
        for name, value in {"_get_servant_map": {}, "_get_detector": NS(detect=Mock(return_value=[(*self.origin, 1218, 271, .99)])),
                            "_init_scale": None, "_shot_base": frame, "_ref_shift": (0, 0),
                            "_tap": None, "_record_selected_support": None}.items():
            setattr(action, name, Mock(return_value=value))
        action._swipe = Mock(side_effect=lambda *args: setattr(context.tasker, "stopping", True))
        action._match_ce = Mock(wraps=action._match_ce)
        with patch.object(support, "fetch_share_data", return_value=({}, None, None)), \
                patch.object(support, "build_support_criteria", return_value=criteria):
            result = action.run(context, NS(node_name="助战action", custom_action_param=json.dumps(
                {"support_type": "grand" if grand else "normal"} if attach is not None else {"source_mode": "chaldea"})))
        return result, action

    def test_builtin_same_name_and_state_precedes_external_template(self):
        eid = self.ids[0]
        self.install([(eid, "内置礼装", ("满破", "非满破"))])
        # Give the same-named builtin the other card's actual art.
        for state in ("满破", "非满破"):
            path = self.ce_dir / state / "内置礼装.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.fromarray(self.cards[(self.ids[1], state)]).save(path)
        action = support.SupportAction()
        action._ce_ids = {"内置礼装.png": eid}
        frame = self.frame([(support.CE_ANCHOR_NORMAL, self.ids[1], "满破")])
        self.assertTrue(self.matches(frame, "内置礼装.png", "满破", action))
        external_frame = self.frame([(support.CE_ANCHOR_NORMAL, eid, "满破")])
        self.assertFalse(self.matches(external_frame, "内置礼装.png", "满破", action))

    def test_external_both_explicit_states_any_and_opposite_rejection(self):
        for actual in ("满破", "非满破"):
            frame = self.frame([(support.CE_ANCHOR_NORMAL, self.ids[0], actual)])
            name = f"support-ce:{self.ids[0]}"
            with self.subTest(actual=actual):
                self.assertTrue(self.matches(frame, name, actual))
                self.assertTrue(self.matches(frame, name, "任意"))
                self.assertFalse(self.matches(frame, name, support.CE_SUB_SWAP[actual]))
                self.assertFalse(self.matches(frame, f"support-ce:{self.ids[1]}", "任意"))

    def test_chaldea_stable_id_survives_a_different_translated_name(self):
        frame = self.frame([(support.CE_ANCHOR_NORMAL, self.ids[0], "满破")])
        groups = [{"slot": 1, "candidates": [self.spec(self.ids[0], name="另一种旧译名.png")]}]
        result, action = self.search(frame, groups)
        self.assertTrue(result.success)
        action._tap.assert_called_once()

    def test_normal_same_name_different_id_candidates_do_not_overwrite_primary(self):
        frame = self.frame([(support.CE_ANCHOR_NORMAL, self.ids[1], "满破")])
        groups = [{"slot": 1, "candidates": [self.spec(eid) for eid in self.ids]}]
        result, action = self.search(frame, groups)
        self.assertTrue(result.success)
        self.assertEqual([call.args[4] for call in action._match_ce.call_args_list],
                         [f"support-ce:{eid}" for eid in self.ids])
        self.assertEqual(action._ce_ids, {f"support-ce:{eid}": eid for eid in self.ids})

    def test_grand_same_name_different_id_cells_and_states_remain_independent(self):
        groups = [{"slot": 1, "candidates": [self.spec(self.ids[0], "满破")]},
                  {"slot": 2, "candidates": [self.spec(self.ids[1], "非满破")]}]
        frame = self.frame([(support.CE_ANCHOR_GRAND[0], self.ids[0], "满破"),
                            (support.CE_ANCHOR_GRAND[1], self.ids[1], "非满破")])
        result, action = self.search(frame, groups, grand=True)
        self.assertTrue(result.success)
        action._tap.assert_called_once()
        wrong_second = self.frame([(support.CE_ANCHOR_GRAND[0], self.ids[0], "满破"),
                                   (support.CE_ANCHOR_GRAND[1], self.ids[0], "非满破")])
        result, action = self.search(wrong_second, groups, grand=True)
        self.assertFalse(result.success)
        action._tap.assert_not_called()

    def test_single_available_state_with_constant_white_corner_is_unconfirmed(self):
        eid = self.ids[0]
        self.cards[(eid, "非满破")][-30:, -30:] = 255
        self.install([(eid, "白背景", ("非满破",))])
        frame = self.frame([(support.CE_ANCHOR_NORMAL, eid, "非满破")])
        self.assertTrue(self.matches(frame, f"support-ce:{eid}", "任意"))
        self.assertFalse(self.matches(frame, f"support-ce:{eid}", "非满破"))

    def test_switching_extension_single_multi_and_builtin_ignores_stale_other_sources(self):
        key = "普通助战-礼装-满破"
        attach = {key: f"support-ce:{self.ids[0]}", f"{key}-多选": True,
                  f"{key}-扩展包": True, f"{key}-候选:旧内置.png": True,
                  f"{key}-扩展候选:support-ce:{self.ids[1]}": True}
        self.assertEqual(support._custom_ce_candidates(attach, key), [f"support-ce:{self.ids[1]}"])
        attach[f"{key}-多选"] = False
        self.assertEqual(support._custom_ce_candidates(attach, key), [f"support-ce:{self.ids[0]}"])
        attach[f"{key}-多选"] = True
        attach[f"{key}-扩展包"] = False
        self.assertEqual(support._custom_ce_candidates(attach, key), ["旧内置.png"])
        attach[f"{key}-扩展包"] = True
        attach[f"{key}-扩展候选:support-ce:{self.ids[1]}"] = False
        self.assertEqual(support._custom_ce_candidates(attach, key), ["0_空.png"])

    def test_catalog_new_ids_offer_support_image_without_claiming_team_recognition(self):
        catalog = build_catalog(self.root)
        rows = {row["id"]: row for row in catalog["equips"]}
        for eid in self.ids:
            row = rows[eid]
            self.assertEqual(row["name"], "同名礼装")
            self.assertEqual(row["supportRecognition"], {"limitBreak": True, "nonLimitBreak": True})
            self.assertFalse(row["recognizable"])
            self.assertIsNone(row["image"])
            self.assertTrue(row["supportImage"].startswith("/media/supportce/"))
            media = row["supportImage"].split("/")[-1]
            self.assertIsNotNone(resolve_media(self.root, "supportce", media))


if __name__ == "__main__":
    unittest.main()
