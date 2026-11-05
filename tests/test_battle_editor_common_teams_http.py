"""Common-team browsing keeps the authenticated editor boundary and draft-only import."""
import copy
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from agent.battle_editor.server import create_server
from agent.battle_editor.storage import EditorError


class CommonTeamsHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="common teams 中文 ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        web = self.root / "assets/web/battle-editor"
        web.mkdir(parents=True)
        (web / "index.html").write_text("editor", encoding="utf-8")
        self.server = create_server(self.root, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.stop()
        self.thread.join(5)
        self.server.server_close()

    def request(self, path, *, token=True, origin=None, project=None, method="GET", body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Editor-Token"] = self.server.token
        if origin:
            headers["Origin"] = origin
        if project:
            headers["X-Editor-Project"] = project
        conn.request(method, path, None if body is None else json.dumps(body), headers)
        response = conn.getresponse()
        result = response.status, json.loads(response.read())
        conn.close()
        return result

    def test_catalog_is_local_and_search_passes_only_bounded_parameters(self):
        catalog = {"classes": [{"id": "archer", "label": "弓"}], "quests": []}
        page = {"teams": [{"id": 16017}], "offset": 12, "total": 30, "hasMore": True}
        with patch("agent.battle_editor.common_teams.common_team_catalog", return_value=catalog) as local, \
             patch("agent.battle_editor.common_teams.search_common_teams", return_value=page) as search:
            self.assertEqual(self.request("/api/common-teams/catalog"), (200, catalog))
            local.assert_called_once_with()
            search.assert_not_called()
            self.assertEqual(self.request("/api/common-teams?quest=archer-vii&sort=created&offset=12"), (200, page))
            search.assert_called_once_with("archer-vii", sort="created", offset=12)
        self.assertFalse(list(self.server.storage.battle_dir.glob("*.json")))

    def test_auth_origin_and_project_checked_before_upstream(self):
        with patch("agent.battle_editor.common_teams.search_common_teams") as search:
            path = "/api/common-teams?quest=archer-vii"
            for options in ({"token": False}, {"origin": "https://untrusted.invalid"}, {"project": "f" * 64}):
                with self.subTest(options=options):
                    self.assertIn(self.request(path, **options)[0], (403, 409))
            search.assert_not_called()

    def test_ambiguous_or_excess_query_rejected_before_upstream(self):
        queries = ("quest=a&quest=b", "quest=a&url=https://untrusted.invalid", "quest=a&offset=-1",
                   "quest=a&offset=1.5", "quest=a&offset=9999999", "quest=a&sort=upvotes&sort=created",
                   "&".join("quest=a" for _ in range(10)))
        with patch("agent.battle_editor.common_teams.search_common_teams") as search:
            for query in queries:
                with self.subTest(query=query):
                    self.assertEqual(self.request("/api/common-teams?" + query)[0], 400)
            search.assert_not_called()

    def test_upstream_error_keeps_message_and_status(self):
        message = "Chaldea 暂时无法连接，无法查询队伍。请检查网络后点击“重试”。"
        with patch("agent.battle_editor.common_teams.search_common_teams", side_effect=EditorError("common_teams_network", message, 502)):
            status, body = self.request("/api/common-teams?quest=archer-vii")
        self.assertEqual(status, 502)
        self.assertEqual(body["error"]["message"], message)
        self.assertFalse(list(self.server.storage.battle_dir.glob("*.json")))

    def test_existing_import_preserves_unknown_fields_validation_and_does_not_save(self):
        data = {"team": {"name": "冠位队伍", "onFieldSvts": [None] * 3, "backupSvts": [None] * 3},
                "actions": [], "future": {"opaque": [None, True, "中文"]}}
        record = {"data": data, "teamId": 16017, "validation": {"valid": False, "issues": []}}
        before = copy.deepcopy(record)
        with patch.object(self.server, "import_source", return_value=record) as importer:
            source = "https://link.chaldea.center/laplace/share?id=16017"
            self.assertEqual(self.request("/api/import", method="POST", body={"source": source}), (200, before))
            importer.assert_called_once_with(source)
        self.assertEqual(record, before)
        self.assertFalse(list(self.server.storage.battle_dir.glob("*.json")))


if __name__ == "__main__":
    unittest.main()
