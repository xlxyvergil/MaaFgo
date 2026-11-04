"""BBC uploads convert offline into previews without saving or device startup."""
import copy
import http.client
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from agent.battle_editor.bbc_import import import_bbc_document
from agent.battle_editor.server import create_server
from agent.battle_editor.storage import EditorError
from agent.battle_editor.validation import validate_battle


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/bbc/爱尔奎特_光狐_光狐.json"


class BbcUploadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="bbc-upload 中文 ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        static = self.root / "assets/web/battle-editor"
        static.mkdir(parents=True)
        (static / "index.html").write_text("offline editor", encoding="utf-8")
        for relative in ("agent/chaldea/bbc_servant_ids.json", "agent/utils/Chaldea/equip_names_CN.json"):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        self.config = json.loads(FIXTURE.read_text("utf-8"))
        self.server = create_server(self.root, 0)
        self.server._catalog = {"servants": [], "equips": [], "mysticCodes": []}
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.thread.join(5)
        self.server.server_close()

    def request(self, payload, *, token=True, origin=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Editor-Token"] = self.server.token
        if origin:
            headers["Origin"] = origin
        connection.request("POST", "/api/import-bbc", json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers)
        response = connection.getresponse()
        status, body = response.status, json.loads(response.read())
        connection.close()
        return status, body

    def test_real_config_converts_without_bbc_installation_or_writing(self):
        original = copy.deepcopy(self.config)
        before = {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        status, result = self.request({"config": self.config, "name": FIXTURE.name})
        self.assertEqual(status, 200, result)
        self.assertEqual(result["sourceName"], FIXTURE.name)
        self.assertTrue(result["validation"]["valid"], result["validation"])
        self.assertEqual(result["data"]["_source"], "bbc")
        self.assertEqual(result["data"]["team"]["onFieldSvts"][2]["supportType"], "friend")
        self.assertEqual(sum(action["type"] == "attack" for action in result["data"]["actions"]), 3)
        self.assertEqual(result["data"]["_bbcSource"]["file"], FIXTURE.name)
        self.assertEqual(len(result["data"]["_bbcSource"]["sha256"]), 64)
        self.assertEqual(result["warnings"], result["data"]["_conversionWarnings"])
        self.assertEqual(self.config, original)
        self.assertEqual(before, {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()})

    def test_formation_only_upload_has_no_implicit_turn_or_file_write(self):
        config = {key: value for key, value in self.config.items() if not key.startswith("round")}
        original = copy.deepcopy(config)
        before = {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        status, result = self.request({"config": config, "name": "仅编队 BBC.json"})
        self.assertEqual(status, 200, result)
        self.assertTrue(result["validation"]["valid"], result["validation"])
        self.assertEqual(result["data"]["actions"], [])
        self.assertEqual(result["data"]["_bbcWaveTurns"], [])
        self.assertIn("default_battle", [issue["code"] for issue in result["validation"]["issues"]])
        self.assertEqual(result["data"]["team"]["onFieldSvts"][2]["supportType"], "friend")
        self.assertEqual(config, original)
        self.assertEqual(before, {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()})

    def test_ce_only_upload_preserves_all_or_candidates_without_servant_requirements(self):
        names = json.loads((self.root / "agent/utils/Chaldea/equip_names_CN.json").read_text("utf-8"))
        ids = [9400340, 9400480, 9403280]
        config = {key: value for key, value in self.config.items() if not key.startswith("round")}
        config.update({"servant_2_name": None, "assistMode": "仅礼装",
                       "assistEquip": [names[str(eid)] for eid in ids], "fullEquip": 1})
        original = copy.deepcopy(config)
        before = {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        status, result = self.request({"config": config, "name": "仅礼装候选.json"})
        self.assertEqual(status, 200, result)
        self.assertTrue(result["validation"]["valid"], result["validation"])
        self.assertEqual(result["data"]["actions"], [])
        self.assertEqual(result["data"]["team"]["onFieldSvts"][2], {
            "supportType": "friend", "equip1": {"id": ids[0], "limitBreak": True,
                "candidates": [{"id": eid, "limitBreak": True} for eid in ids[1:]]}})
        self.assertEqual(config, original)
        self.assertEqual(before, {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()})

    def test_grand_ce_only_upload_keeps_effect_and_or_choices_in_their_real_cells(self):
        names = json.loads((self.root / "agent/utils/Chaldea/equip_names_CN.json").read_text("utf-8"))
        ids = [9400340, 9400480, 9403280]
        before = {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        for source, expected in (("NP50", True), ("初始", False)):
            with self.subTest(effect=source):
                config = {key: value for key, value in self.config.items() if not key.startswith("round")}
                config.update({"servant_2_name": None, "assistMode": "冠位助战仅礼装", "jbType": source,
                               "assistEquip": [[names[str(ids[0])], names[str(ids[1])]], None,
                                               [names[str(ids[2])], names[str(ids[0])]]], "fullEquip": 1})
                original = copy.deepcopy(config)
                status, result = self.request({"config": config, "name": "冠位礼装候选.json"})
                self.assertEqual(status, 200, result)
                self.assertTrue(result["validation"]["valid"], result["validation"])
                self.assertEqual(result["data"]["actions"], [])
                self.assertEqual(result["data"]["team"]["onFieldSvts"][2], {
                    "supportType": "friend", "grandSvt": True,
                    "classBoardData": {"grandBondEquipSkillChange": expected},
                    "equip1": {"id": ids[0], "limitBreak": True,
                               "candidates": [{"id": ids[1], "limitBreak": True}]},
                    "equip3": {"id": ids[2], "limitBreak": True,
                               "candidates": [{"id": ids[0], "limitBreak": True}]}})
                self.assertEqual(config, original)
        self.assertEqual(before, {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()})

    def test_grand_effect_upload_does_not_fabricate_a_named_bond_ce(self):
        before = {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        for source, expected in (("NP50", True), ("初始", False)):
            with self.subTest(effect=source):
                config = {key: value for key, value in self.config.items() if not key.startswith("round")}
                config.update({"assistMode": "冠位助战", "jbType": source, "assistEquip": [None, None, None]})
                original = copy.deepcopy(config)
                status, result = self.request({"config": config, "name": "冠位效果.json"})
                self.assertEqual(status, 200, result)
                self.assertTrue(result["validation"]["valid"], result["validation"])
                member = result["data"]["team"]["onFieldSvts"][2]
                self.assertIs(member["grandSvt"], True)
                self.assertEqual(member["classBoardData"], {"grandBondEquipSkillChange": expected})
                self.assertFalse(any(f"equip{slot}" in member for slot in (1, 2, 3)))
                self.assertEqual(config, original)
        self.assertEqual(before, {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()})

    def test_natural_retirement_upload_preserves_initial_instances_in_following_actions(self):
        catalog = json.loads((self.root / "agent/chaldea/bbc_servant_ids.json").read_text("utf-8"))["servants"]
        ids = [100100, 504500, 604200, 2300900]
        config = {f"servant_{slot}_name": next(name for name, entry in catalog.items()
                  if str(entry.get("SN")) == str(sid)) for slot, sid in enumerate(ids)}
        config.update({"assistIdx": 2, "assistMode": "仅从者", "round1_turns": 2,
                       "round1_turn0_np": [1], "round1_turn0_replace": {"1": 4, "4": None},
                       "round1_turn1_skill": [1], "round1_turn1_np": [1]})
        original = copy.deepcopy(config)
        before = {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        status, result = self.request({"config": config, "name": "自然退场 BBC.json"})
        self.assertEqual(status, 200, result)
        self.assertTrue(result["validation"]["valid"], result["validation"])
        actions = result["data"]["actions"]
        self.assertEqual([action["type"] for action in actions], ["attack", "retire", "skill", "attack"])
        self.assertEqual(actions[0]["attacks"][0]["actor"], {"initialSlot": 0, "svtId": ids[0]})
        self.assertEqual(actions[1], {"type": "retire", "svt": 0,
                                     "actor": {"initialSlot": 0, "svtId": ids[0]}})
        self.assertEqual(actions[2], {"type": "skill", "svt": 0, "skill": 0,
                                     "actor": {"initialSlot": 3, "svtId": ids[3]}})
        self.assertEqual(actions[3]["attacks"][0]["actor"], {"initialSlot": 3, "svtId": ids[3]})
        team = result["data"]["team"]
        self.assertEqual([member["svtId"] for member in team["onFieldSvts"]], ids[:3])
        self.assertEqual(team["backupSvts"][0]["svtId"], ids[3])
        self.assertEqual(config, original)
        self.assertEqual(before, {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()})

    def test_special_choice_upload_preserves_zero_values_and_independent_ordered_queues(self):
        catalog = json.loads((self.root / "agent/chaldea/bbc_servant_ids.json").read_text("utf-8"))["servants"]
        ids = [1100900, 505600, 405900]
        config = {f"servant_{slot}_name": next(name for name, entry in catalog.items()
                  if str(entry.get("SN")) == str(sid)) for slot, sid in enumerate(ids)}
        config.update({"assistIdx": 2, "assistMode": "仅从者", "round1_turns": 1,
                       "round1_turn0_skill": [2, [-2, 3], ["Prelati", 4, "不替换"],
                                              9, ["Lilith", 9, "付与即死"]], "round1_turn0_np": [1]})
        original = copy.deepcopy(config)
        before = {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        status, result = self.request({"config": config, "name": "特殊技能选项 BBC.json"})
        self.assertEqual(status, 200, result)
        self.assertTrue(result["validation"]["valid"], result["validation"])
        self.assertEqual(result["data"]["delegate"], {"replaceMemberIndexes": [],
                         "tdTypeChanges": [1, 2], "skillActSelectSelections": [0, 0, 1]})
        skills = [action for action in result["data"]["actions"] if action["type"] == "skill"]
        self.assertEqual([(action["svt"], action["skill"]) for action in skills],
                         [(0, 1), (0, 1), (1, 0), (2, 2), (2, 2)])
        self.assertEqual(result["warnings"], result["data"]["_conversionWarnings"])
        self.assertEqual(config, original)
        self.assertEqual(before, {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()})

    def test_invalid_documents_and_names_are_actionable_and_preserve_files(self):
        for value in (None, [], "BBC", True, {}):
            with self.subTest(config=value):
                status, result = self.request({"config": value, "name": "BBC.json"})
                self.assertEqual(status, 422, result)
                self.assertTrue(result["error"]["message"])
        for filename in ("../secret.json", "C:/private.json", "x\\y.json"):
            with self.subTest(filename=filename):
                status, result = self.request({"config": self.config, "name": filename})
                self.assertEqual(status, 400, result)
        self.assertEqual(list(self.server.storage.battle_dir.iterdir()), [])

    def test_unsupported_extended_instruction_is_rejected_before_import(self):
        self.config["round1_turn0_skill"] = [["BBDubai", 3, 0]]
        status, result = self.request({"config": self.config, "name": "extended.json"})
        self.assertEqual(status, 422, result)
        self.assertEqual(result["error"]["code"], "bbc_conversion_failed")
        self.assertEqual(list(self.server.storage.battle_dir.iterdir()), [])

    def test_upload_requires_same_origin_and_session_token(self):
        payload = {"config": self.config, "name": "BBC.json"}
        self.assertEqual(self.request(payload, token=False)[0], 403)
        self.assertEqual(self.request(payload, origin="https://untrusted.invalid")[0], 403)

    def test_conversion_failure_returns_422_without_internal_traceback(self):
        with patch("agent.battle_editor.bbc_import._converter", side_effect=ValueError("无法转换这条动作")):
            with self.assertRaises(EditorError) as raised:
                import_bbc_document(self.root, self.config)
        self.assertEqual((raised.exception.status, raised.exception.code), (422, "bbc_conversion_failed"))

    def test_conversion_warnings_remain_visible_in_saved_plan_checks(self):
        result = import_bbc_document(self.root, self.config, FIXTURE.name)
        result["data"]["_conversionWarnings"] = ["BBC 按波次推进，请核对固定回合", "候选选卡需手动核对"]
        path = self.server.storage.save_battle("bbc_saved", result["data"], create=True)
        restored = self.server.storage.read_battle(path["name"])["data"]
        report = validate_battle(restored)
        warnings = [issue for issue in report["issues"] if issue["code"] == "bbc_conversion_warning"]
        self.assertTrue(report["valid"], report)
        self.assertEqual([issue["message"] for issue in warnings], result["data"]["_conversionWarnings"])
        self.assertTrue(all(issue["severity"] == "warning" for issue in warnings))
        self.assertFalse(any(issue["code"] == "preserved_field" and issue["path"].startswith("_") for issue in report["issues"]))

    def test_grand_bond_effect_is_a_real_filter_without_fabricating_a_bond_ce(self):
        for state in (True, False):
            data = {"team": {"onFieldSvts": [{"supportType": "friend", "grandSvt": True,
                                             "classBoardData": {"grandBondEquipSkillChange": state}}, None, None],
                             "backupSvts": [None, None, None]}, "actions": []}
            report = validate_battle(data)
            self.assertTrue(report["valid"], report)
            self.assertNotIn("unrestricted_support", [issue["code"] for issue in report["issues"]])
            self.assertNotIn("equip2", data["team"]["onFieldSvts"][0])

    def test_new_process_imports_no_online_chaldea_or_device_modules(self):
        script = r'''
import builtins, json, pathlib, socket, sys
sys.path.insert(0, sys.argv[1])
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'chaldea', 'maa', 'requests', 'mfaalog', 'custom'}:
        raise AssertionError('offline editor imported ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
socket.socket = lambda *a, **kw: (_ for _ in ()).throw(AssertionError('network IO'))
from agent.battle_editor.bbc_import import import_bbc_document
result = import_bbc_document(pathlib.Path(sys.argv[2]), json.load(sys.stdin), '离线 BBC.json')
assert result['data']['team']['onFieldSvts'][2]['supportType'] == 'friend'
print('offline conversion ok')
'''
        result = subprocess.run([sys.executable, "-c", script, str(ROOT), str(self.root)],
                                input=json.dumps(self.config), capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("offline conversion ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
