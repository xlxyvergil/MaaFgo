"""Early-turn NP reminders are advisory and preserve formal-save round trips."""
import copy
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from agent.battle_editor.server import create_server
from agent.battle_editor.validation import validate_battle


def attack(cards=None):
    return {"type": "attack", "attacks": [] if cards is None else cards}


def np_card():
    return {"svt": 0, "isTD": True, "card": -1}


def plan(actions):
    return {
        "team": {
            "name": "前三回合宝具提醒",
            "onFieldSvts": [{"svtId": 100100, "supportType": "friend"}, None, None],
            "backupSvts": [None, None, None],
        },
        "actions": actions,
    }


def np_warnings(report):
    return [issue for issue in report["issues"] if issue["code"] == "early_turn_no_np"]


class EarlyTurnNpWarningTests(unittest.TestCase):
    def test_empty_and_single_turn_plans_have_no_multi_turn_reminder(self):
        for actions in ([], None, [attack()], [{"type": "skill", "svt": 0, "skill": 0}],
                        [{"type": "skill", "svt": 0, "skill": 0}, attack()]):
            with self.subTest(actions=actions):
                report = validate_battle(plan(actions))
                self.assertTrue(report["valid"], report)
                self.assertEqual(np_warnings(report), [])

    def test_two_turns_warn_only_the_actual_missing_np_turn(self):
        for missing in (0, 1):
            with self.subTest(missing=missing):
                actions = [attack([np_card()]), attack([np_card()])]
                actions[missing] = attack()
                report = validate_battle(plan(actions))
                self.assertTrue(report["valid"], report)
                self.assertEqual(report["summary"]["turns"], 2)
                self.assertEqual(np_warnings(report), [{
                    "severity": "warning", "path": f"actions[{missing}].attacks",
                    "code": "early_turn_no_np", "actionIndex": missing,
                    "message": f"第{missing + 1}回合未安排宝具，将按配置或默认策略出普通卡。请确认是否符合预期；此提醒不影响保存。",
                }])

    def test_all_three_missing_turns_receive_separate_reminders(self):
        report = validate_battle(plan([attack(), attack(), attack()]))
        self.assertTrue(report["valid"], report)
        self.assertEqual([issue["actionIndex"] for issue in np_warnings(report)], [0, 1, 2])
        self.assertEqual([issue["path"] for issue in np_warnings(report)],
                         ["actions[0].attacks", "actions[1].attacks", "actions[2].attacks"])

    def test_fourth_and_later_turns_are_outside_the_reminder_window(self):
        actions = [attack([np_card()]) for _ in range(3)] + [attack(), attack()]
        report = validate_battle(plan(actions))
        self.assertTrue(report["valid"], report)
        self.assertEqual(report["summary"]["turns"], 5)
        self.assertEqual(np_warnings(report), [])

    def test_ordinary_and_default_face_cards_do_not_count_as_np(self):
        normal = {"svt": 0, "isTD": False, "card": 0, "cardType": 2}
        for cards in ([normal], [{"isAuto": True}], [normal, {"isAuto": True}]):
            with self.subTest(cards=cards):
                report = validate_battle(plan([attack(cards), attack([np_card()])]))
                self.assertTrue(report["valid"], report)
                self.assertEqual([issue["actionIndex"] for issue in np_warnings(report)], [0])

    def test_np_after_one_or_two_default_cards_counts_as_arranged(self):
        for prefix in (1, 2):
            with self.subTest(default_cards=prefix):
                cards = [{"isAuto": True} for _ in range(prefix)] + [np_card()]
                report = validate_battle(plan([attack(cards), attack([np_card()])]))
                self.assertTrue(report["valid"], report)
                self.assertEqual(np_warnings(report), [])

    def test_null_and_omitted_attack_lists_are_default_face_turns(self):
        for empty in ({"type": "attack"}, {"type": "attack", "attacks": None}):
            with self.subTest(empty=empty):
                report = validate_battle(plan([empty, attack([np_card()])]))
                self.assertTrue(report["valid"], report)
                self.assertEqual([issue["path"] for issue in np_warnings(report)], ["actions[0].attacks"])

    def test_truthy_non_boolean_np_flags_do_not_suppress_warning(self):
        for value in (1, "true"):
            with self.subTest(value=value):
                card = {"svt": 0, "isTD": value, "card": 0, "cardType": 2}
                report = validate_battle(plan([attack([card]), attack([np_card()])]))
                self.assertFalse(report["valid"])
                self.assertTrue(any(issue["code"] == "invalid_boolean" for issue in report["issues"]))
                self.assertEqual([issue["actionIndex"] for issue in np_warnings(report)], [0])

    def test_conflicting_auto_np_is_not_a_real_np(self):
        card = {"isAuto": True, **np_card()}
        report = validate_battle(plan([attack([card]), attack([np_card()])]))
        self.assertFalse(report["valid"])
        self.assertTrue(any(issue["code"] == "conflicting_auto_card" for issue in report["issues"]))
        self.assertEqual([issue["actionIndex"] for issue in np_warnings(report)], [0])

    def test_malformed_attack_lists_keep_their_error_without_guessing_no_np(self):
        for malformed in ({}, "cards", False, 0):
            with self.subTest(malformed=malformed):
                bad = {"type": "attack", "attacks": malformed}
                report = validate_battle(plan([bad, attack([np_card()])]))
                self.assertFalse(report["valid"])
                self.assertTrue(any(issue["code"] == "invalid_cards" for issue in report["issues"]))
                self.assertEqual(np_warnings(report), [])

    def test_action_location_is_attack_boundary_not_skill_or_turn_index(self):
        data = plan([
            {"type": "skill", "svt": 0, "skill": 0}, attack([np_card()]),
            {"type": "skill", "svt": 0, "skill": 1},
            {"type": "skill", "svt": 0, "skill": 2}, attack(),
            attack([np_card()]),
        ])
        before = copy.deepcopy(data)
        report = validate_battle(data)
        self.assertTrue(report["valid"], report)
        self.assertEqual([(issue["actionIndex"], issue["path"]) for issue in np_warnings(report)],
                         [(4, "actions[4].attacks")])
        self.assertIn("第2回合", np_warnings(report)[0]["message"])
        self.assertEqual(data, before)

    def test_trailing_skills_count_as_default_face_turn_and_locate_first_skill(self):
        data = plan([
            attack([np_card()]), {"type": "skill", "svt": 0, "skill": 0},
            {"type": "skill", "svt": 0, "skill": 1},
        ])
        report = validate_battle(data)
        self.assertTrue(report["valid"], report)
        self.assertEqual(report["summary"]["turns"], 2)
        self.assertTrue(any(issue["code"] == "trailing_skills" for issue in report["issues"]))
        self.assertEqual([(issue["actionIndex"], issue["path"]) for issue in np_warnings(report)],
                         [(1, "actions[1]")])
        self.assertIn("第2回合", np_warnings(report)[0]["message"])

    def test_fourth_implicit_skill_turn_does_not_receive_early_turn_reminder(self):
        data = plan([attack([np_card()]) for _ in range(3)] + [{"type": "skill", "svt": 0, "skill": 0}])
        report = validate_battle(data)
        self.assertTrue(report["valid"], report)
        self.assertEqual(report["summary"]["turns"], 4)
        self.assertEqual(np_warnings(report), [])

    def test_legacy_np_without_card_index_still_counts_without_mutation(self):
        data = plan([attack([{"svt": 0, "isTD": True}]), attack([np_card()])])
        before = copy.deepcopy(data)
        report = validate_battle(data)
        self.assertTrue(report["valid"], report)
        self.assertEqual(np_warnings(report), [])
        self.assertTrue(any(issue["code"] == "legacy_np_card" for issue in report["issues"]))
        self.assertEqual(data, before)


class EarlyTurnNpWarningHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="NP warning HTTP ")
        self.root = Path(self.temp.name)
        web = self.root / "assets" / "web" / "battle-editor"
        web.mkdir(parents=True)
        (web / "index.html").write_text("<!doctype html><title>NP warning</title>", encoding="utf-8")
        self.server = create_server(self.root, 0)
        self.server._catalog = {"servants": []}
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def tearDown(self):
        self.server.stop()
        self.thread.join(5)
        self.server.server_close()
        self.temp.cleanup()

    def request(self, method, path, payload=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        headers = {"X-Editor-Token": self.server.token}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_warning_only_plan_can_create_update_and_read_back_unchanged(self):
        data = plan([attack(), attack([np_card()]), attack()])
        before = copy.deepcopy(data)
        status, checked = self.request("POST", "/api/validate", {"data": data})
        self.assertEqual(status, 200, checked)
        self.assertTrue(checked["valid"], checked)
        self.assertEqual([issue["actionIndex"] for issue in np_warnings(checked)], [0, 2])
        status, saved = self.request("POST", "/api/battles", {"name": "local_early_np.json", "data": data})
        self.assertEqual(status, 201, saved)
        self.assertTrue(saved["validation"]["valid"], saved)
        self.assertEqual(np_warnings(saved["validation"]), np_warnings(checked))
        self.assertEqual(saved["data"], before)
        status, loaded = self.request("GET", "/api/battles/local_early_np.json")
        self.assertEqual(status, 200, loaded)
        self.assertEqual(loaded["data"], before)
        edited = copy.deepcopy(data)
        edited["actions"][2]["attacks"] = [{"isAuto": True}, {"isAuto": True}, np_card()]
        status, updated = self.request("PUT", "/api/battles/local_early_np.json", {
            "data": edited, "expectedHash": saved["hash"],
        })
        self.assertEqual(status, 200, updated)
        self.assertEqual([issue["actionIndex"] for issue in np_warnings(updated["validation"])], [0])
        self.assertEqual(updated["data"], edited)
        self.assertEqual(self.request("GET", "/api/battles/local_early_np.json")[1]["data"], edited)
        self.assertEqual(json.loads((self.root / "config" / "Battle" / "local_early_np.json").read_text(encoding="utf-8")), edited)
        self.assertEqual(data, before)


if __name__ == "__main__":
    unittest.main()
