"""Public team browsing stays bounded, read-only and independent of MaaFramework."""
import base64
import copy
import gzip
import io
import json
from pathlib import Path
import socket
import ssl
import unittest
from unittest.mock import MagicMock, patch
import urllib.error
import urllib.parse
import urllib.request

from agent.battle_editor import common_teams as common
from agent.battle_editor.storage import EditorError


ROOT = Path(__file__).resolve().parents[1]
QUEST = "archer-94150847"


def packed(battle, *, version=2):
    encoded = base64.urlsafe_b64encode(gzip.compress(json.dumps(battle).encode())).decode().rstrip("=")
    return "G" + encoded if version == 2 else encoded


def row(team_id=23, **changes):
    data = {
        "id": team_id, "quest_id": 94150807, "phase": 1,
        "username": "示例作者", "created_at": 1789324592, "up": 21, "down": 2,
        "data": packed({
            "team": {
                "name": "弓冠队伍",
                "onFieldSvts": [
                    {"svtId": 204300, "supportType": "friend", "grandSvt": True,
                     "equip1": {"id": 9403520}, "ceId": 999},
                    None,
                    {"supportType": "friend", "equip1": {"id": 9400400}},
                ],
                "backupSvts": [{"svtId": 201800, "ceId": 9400260}],
            },
            "actions": [{"type": "skill"}, {"type": "attack"}, {"type": "attack"}],
        }),
    }
    data.update(changes)
    return data


class CatalogueTests(unittest.TestCase):
    def test_all_classes_routes_and_difficulties_are_present(self):
        catalogue = common.common_team_catalog()
        self.assertEqual({item["id"] for item in catalogue["classes"]},
                         {"saber", "archer", "lancer", "rider", "caster", "assassin", "berserker", "ex1", "ex2", "extra"})
        self.assertEqual(len(catalogue["quests"]), 105)
        difficulties = {"100", "100+", "100++", "100+++", "100★", "100★★", "100★★★"}
        for class_id in (item["id"] for item in catalogue["classes"]):
            quests = [q for q in catalogue["quests"] if q["classId"] == class_id]
            self.assertEqual({q["phase"] for q in quests}, {1})
            self.assertEqual({q["difficulty"] for q in quests}, difficulties)
            self.assertTrue(all(q["questIds"] and all(type(qid) is int and qid > 0 for qid in q["questIds"]) for q in quests))
        for class_id, elements in (("ex1", ("火", "地")), ("ex2", ("风", "水"))):
            for element in elements:
                quests = [q for q in catalogue["quests"] if q["classId"] == class_id and "研钻" in q["label"] and element in q["label"]]
                self.assertEqual(len(quests), 7)
                self.assertEqual({q["difficulty"] for q in quests}, difficulties)

    def test_aliases_merge_reopenings_without_mixing_difficulties_or_routes(self):
        quests = {q["id"]: q for q in common.common_team_catalog()["quests"]}
        self.assertEqual(set(quests[QUEST]["questIds"]), {94150807, 94150817, 94150847})
        self.assertEqual(set(quests["caster-94151247"]["questIds"]), {94151207, 94151217, 94151247})
        self.assertEqual(set(quests["ex1-94148937"]["questIds"]), {94148907, 94148937})
        self.assertEqual(set(quests["ex1-94148947"]["questIds"]), {94148917, 94148947})
        for class_id in {q["classId"] for q in quests.values()}:
            seen = set()
            for quest in quests.values():
                if quest["classId"] == class_id:
                    self.assertFalse(seen.intersection(quest["questIds"]))
                    seen.update(quest["questIds"])

    def test_catalogue_is_independent_and_retains_source_evidence(self):
        catalogue = common.common_team_catalog()
        catalogue["quests"][0]["questIds"].append(1)
        self.assertNotIn(1, common.common_team_catalog()["quests"][0]["questIds"])
        data = json.loads((ROOT / "agent/battle_editor/data/grand_quests.json").read_text("utf-8"))
        self.assertIn("156e8e88609c7536faf4cf476ba613257e3e1206", data["sources"]["chaldeaSameQuestRemap"])
        self.assertIn("https://api.atlasacademy.io/nice/JP/war/8413", data["sources"]["atlasWars"])


class CommonTeamSearchTests(unittest.TestCase):
    def setUp(self):
        common._cache.clear()
        common._latest_totals.clear()

    def search(self, response=None, **kwargs):
        with patch.object(common, "_request_json", return_value=response or {"total": 30, "data": [row()]}) as request:
            result = common.search_common_teams(QUEST, **kwargs)
        return result, request

    def test_query_combines_equivalent_ids_and_keeps_phase_sort_and_pagination(self):
        result, request = self.search(sort="upvotes", offset=12)
        query = request.call_args.args[0]
        self.assertEqual({v for k, v in query if k == "questId"}, {94150807, 94150817, 94150847})
        self.assertEqual([(k, v) for k, v in query if k != "questId"], [("phase", 1), ("sort", "upvotes"), ("limit", 12), ("offset", 12)])
        self.assertEqual((result["offset"], result["pageSize"], result["total"], result["nextOffset"]), (12, 12, 30, 24))
        self.assertTrue(result["hasMore"])

    def test_latest_reverses_the_apis_ascending_order_across_three_pages(self):
        query_log = []
        def fetch(query):
            values = dict(query)
            query_log.append(values)
            start, count = values["offset"], values["limit"]
            return {"total": 30, "data": [row(i + 1, created_at=1_700_000_000 + i) for i in range(start, min(start + count, 30))]}
        with patch.object(common, "_request_json", side_effect=fetch):
            pages = [common.search_common_teams(QUEST, sort="created", offset=offset) for offset in (0, 12, 24)]
        self.assertEqual([[t["id"] for t in p["teams"]] for p in pages],
                         [list(range(30, 18, -1)), list(range(18, 6, -1)), list(range(6, 0, -1))])
        self.assertEqual([(q["limit"], q["offset"]) for q in query_log], [(1, 0), (12, 18), (12, 6), (6, 0)])
        self.assertEqual([p["nextOffset"] for p in pages], [12, 24, None])
        times = [t["createdAt"] for p in pages for t in p["teams"]]
        self.assertEqual(times, sorted(times, reverse=True))

    def test_latest_realigns_after_deletion_and_does_not_page_beyond_results(self):
        common._latest_totals[QUEST] = (common.time.monotonic(), 30)
        responses = [{"total": 20, "data": [row(19), row(20)]},
                     {"total": 20, "data": [row(i) for i in range(9, 21)]}]
        with patch.object(common, "_request_json", side_effect=responses) as request:
            result = common.search_common_teams(QUEST, sort="created")
            empty = common.search_common_teams(QUEST, sort="created", offset=24)
        self.assertEqual([team["id"] for team in result["teams"]], list(range(20, 8, -1)))
        self.assertEqual(result["total"], 20)
        self.assertEqual(empty["teams"], [])
        self.assertEqual(request.call_count, 2)

    def test_basic_preview_keeps_six_positions_and_support_equip_only(self):
        result, _ = self.search()
        team = result["teams"][0]
        self.assertEqual({k: team[k] for k in ("id", "name", "author", "createdAt", "upvotes", "downvotes", "turnCount")},
                         {"id": 23, "name": "弓冠队伍", "author": "示例作者", "createdAt": 1789324592, "upvotes": 21, "downvotes": 2, "turnCount": 2})
        self.assertEqual(team["servants"], [
            {"svtId": 204300, "support": True, "equipId": 9403520, "grandSvt": True}, None,
            {"svtId": 0, "support": True, "equipId": 9400400, "grandSvt": False},
            {"svtId": 201800, "support": False, "equipId": 9400260, "grandSvt": False}, None, None,
        ])
        self.assertNotIn("data", team)
        self.assertNotIn("actions", team)

    def test_only_requested_quest_and_phase_are_shown_and_ids_are_deduplicated(self):
        result, _ = self.search({"total": 5, "data": [row(), row(), row(24, quest_id=99), row(25, phase=2), row(True)]})
        self.assertEqual([team["id"] for team in result["teams"]], [23])

    def test_malformed_quest_fields_do_not_crash_or_match_requested_quest(self):
        bad = [row(index + 1, quest_id=value) for index, value in enumerate(({}, [], "94150807", True))]
        bad.extend([row(10, phase=True), row(11, phase={}), row(12, phase=[])])
        result, _ = self.search({"total": len(bad) + 1, "data": bad + [row()]})
        self.assertEqual([team["id"] for team in result["teams"]], [23])

    def test_bad_deflate_payload_does_not_break_other_team_previews(self):
        damaged = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff" + b"\xff" * 32
        encoded = "G" + base64.urlsafe_b64encode(damaged).decode().rstrip("=")
        result, _ = self.search({"total": 2, "data": [row(22, data=encoded), row()]})
        self.assertEqual([team["id"] for team in result["teams"]], [22, 23])
        self.assertEqual(result["teams"][0]["servants"], [None] * 6)
        self.assertEqual(result["teams"][1]["turnCount"], 2)

    def test_empty_result_does_not_imply_network_failure(self):
        result, _ = self.search({"total": 0, "data": []})
        self.assertEqual(result["teams"], [])
        self.assertFalse(result["hasMore"])
        self.assertIsNone(result["nextOffset"])

    def test_cache_is_short_lived_and_defensively_copied(self):
        with patch.object(common, "_request_json", return_value={"total": 1, "data": [row()]}) as request, patch.object(common.time, "monotonic", return_value=100):
            first = common.search_common_teams(QUEST)
            first["teams"][0]["servants"][0]["svtId"] = 1
            self.assertEqual(common.search_common_teams(QUEST)["teams"][0]["servants"][0]["svtId"], 204300)
            self.assertEqual(request.call_count, 1)
        with patch.object(common, "_request_json", return_value={"total": 0, "data": []}) as request, patch.object(common.time, "monotonic", return_value=161):
            self.assertEqual(common.search_common_teams(QUEST)["teams"], [])
            self.assertEqual(request.call_count, 1)

    def test_invalid_inputs_never_access_the_network(self):
        requests = [(("https://example.test",), {}), ((None,), {}), ((QUEST,), {"sort": "usage"})]
        requests.extend(((QUEST,), {"offset": value}) for value in [-1, 1, 12_012, True, "12"])
        with patch.object(common, "_request_json") as network:
            for args, kwargs in requests:
                with self.subTest(args=args, kwargs=kwargs), self.assertRaises(EditorError):
                    common.search_common_teams(*args, **kwargs)
        network.assert_not_called()

    def test_malformed_pages_fail_with_an_actionable_error(self):
        for response in ({"total": True, "data": []}, {"total": -1, "data": []}, {"total": 1, "data": {}}, {"total": 20, "data": [row(i + 1) for i in range(13)]}):
            with self.subTest(response_type=type(response["data"]).__name__), self.assertRaisesRegex(EditorError, "重试"):
                self.search(response)

    def test_missing_preview_is_bounded_and_does_not_hide_valid_import_id(self):
        for content in ("invalid", "G!invalid!", packed({"name": "x" * (common.MAX_DECODED_BYTES + 10)}), "G" * (common.MAX_CONTENT_CHARS + 1)):
            common._cache.clear()
            result, _ = self.search({"total": 1, "data": [row(data=content)]})
            self.assertEqual(result["teams"][0]["id"], 23)
            self.assertEqual(result["teams"][0]["servants"], [None] * 6)
            self.assertIsNone(result["teams"][0]["turnCount"])

    def test_legacy_encoded_data_and_large_strings(self):
        decoded = common._battle_data({"content": packed({"team": {"name": "旧版"}}, version=1)})
        self.assertEqual(decoded["team"]["name"], "旧版")
        summary = common._summary(row(username="a" * 1000, data=packed({"team": {"name": "b" * 1000}})))
        self.assertEqual((len(summary["author"]), len(summary["name"])), (80, 160))


class CommonTeamNetworkTests(unittest.TestCase):
    def response(self, raw):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = raw
        opener = MagicMock()
        opener.open.return_value = response
        return opener, response

    def test_fixed_https_origin_verifies_certificates_and_bounds_body(self):
        opener, response = self.response(b'{"data": [], "total": 0}')
        with patch.object(common.urllib.request, "build_opener", return_value=opener) as build:
            result = common._request_json([("questId", 94150847), ("offset", 0)])
        request = opener.open.call_args.args[0]
        self.assertEqual(urllib.parse.urlsplit(request.full_url).netloc, "api.chaldea.center")
        self.assertEqual(urllib.parse.urlsplit(request.full_url).scheme, "https")
        self.assertEqual(opener.open.call_args.kwargs["timeout"], common.NETWORK_TIMEOUT)
        response.read.assert_called_once_with(common.MAX_RESPONSE_BYTES + 1)
        context = build.call_args.args[0]._context
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertEqual(result["total"], 0)

    def test_large_invalid_and_nonfinite_responses_are_rejected(self):
        for raw in (b"x" * (common.MAX_RESPONSE_BYTES + 1), b"not json", b'{"total":NaN}', b"[]"):
            opener, _ = self.response(raw)
            with self.subTest(size=len(raw)), patch.object(common.urllib.request, "build_opener", return_value=opener), self.assertRaises(EditorError):
                common._request_json([])

    def test_remote_failure_timeout_and_network_have_distinct_messages(self):
        cases = [
            (urllib.error.HTTPError(common.API_URL, 429, "rate limited", {}, io.BytesIO()), "查询次数"),
            (urllib.error.HTTPError(common.API_URL, 503, "unavailable", {}, io.BytesIO()), "稍后"),
            (socket.timeout(), "超时"),
            (urllib.error.URLError("no route"), "网络或代理"),
        ]
        for error, message in cases:
            opener = MagicMock()
            opener.open.side_effect = error
            with self.subTest(error=type(error).__name__), patch.object(common.urllib.request, "build_opener", return_value=opener), self.assertRaisesRegex(EditorError, message):
                common._request_json([])

    def test_redirects_cannot_turn_search_into_an_arbitrary_proxy(self):
        handler = common._ChaldeaRedirectHandler()
        request = urllib.request.Request(common.API_URL)
        for target in ("http://api.chaldea.center/api/v1/teams/", "https://evil.test/api/v1/teams/", "https://api.chaldea.center:444/api/v1/teams/", "https://user@api.chaldea.center/api/v1/teams/", "https://api.chaldea.center/api/v1/users/"):
            with self.subTest(target=target), self.assertRaises(EditorError):
                handler.redirect_request(request, None, 302, "Found", {}, target)


if __name__ == "__main__":
    unittest.main()
