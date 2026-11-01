"""所有战斗方式选择统一推荐原生，保留 BBC 内部键和分支。"""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def read(relative):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


class BattleModeDefaultTests(unittest.TestCase):
    def test_all_backend_selectors_default_to_native_with_recommendation(self):
        found = set()
        for path in (ROOT / "assets/options").rglob("*.json"):
            for key, option in json.loads(path.read_text(encoding="utf-8")).get("option", {}).items():
                cases = {case["name"]: case for case in option.get("cases", [])}
                if not {"bbc", "native"} <= cases.keys():
                    continue
                found.add(key)
                self.assertEqual(option.get("default_case", option.get("default")), "native", key)
                self.assertEqual(cases["native"]["label"], "原生自动战斗（推荐）", key)
        self.assertTrue({"battle_mode", "主线-战斗方式", "Free-战斗方式"} <= found)

    def test_shared_and_main_story_branches_keep_backend_keys_and_routing(self):
        common = read("assets/options/战斗方式.json")["option"]["battle_mode"]
        cases = {case["name"]: case for case in common["cases"]}
        self.assertEqual(set(cases), {"bbc", "native"})
        self.assertIn("[JumpBack]原生自动战斗_多次入口", cases["native"]["pipeline_override"]["通用战斗调度"]["next"])
        self.assertIn("[JumpBack]执行bbc战斗", cases["bbc"]["pipeline_override"]["通用战斗调度"]["next"])
        self.assertIn("原生自动战斗使用Chaldea队伍", cases["native"]["option"])
        self.assertIn("team_config", cases["bbc"]["option"])
        story = read("assets/options/自动推主线.json")["option"]["主线-战斗方式"]
        for case in story["cases"]:
            self.assertEqual(case["pipeline_override"]["主线-配置"]["attach"]["battle_mode"], case["name"])
        free = read("assets/options/自动清free本.json")["option"]["Free-战斗方式"]
        self.assertEqual({case["name"] for case in free["cases"]}, {"bbc", "native"})
        for case in free["cases"]:
            self.assertEqual(case["pipeline_override"]["Free-配置"]["attach"]["battle_mode"], case["name"])

    def test_battle_tasks_reference_shared_mode_and_story_keeps_its_own_mode(self):
        for name in ("日常战斗", "直接战斗", "冠位戴冠战", "通用大活动", "通用小活动"):
            tasks = read(f"assets/tasks/{name}.json")["task"]
            self.assertTrue(any("battle_mode" in task["option"] for task in tasks), name)
        door = read("assets/options/迦勒底之门-副本类型.json")["option"]
        self.assertTrue(any("battle_mode" in case.get("option", [])
                            for option in door.values() for case in option.get("cases", [])))
        story = read("assets/tasks/自动推主线.json")["task"]
        self.assertTrue(any("主线-战斗方式" in task["option"] for task in story))
        free = read("assets/tasks/自动清free本.json")["task"]
        self.assertTrue(any("Free-战斗方式" in task["option"] for task in free))


if __name__ == "__main__":
    unittest.main()
