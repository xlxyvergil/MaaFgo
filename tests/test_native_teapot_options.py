"""原生茶壶设置的展示范围和编队汇合路由，不连接设备。"""
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from battle.runtime.formation_session import session_override


OPTION = "原生自动战斗是否使用茶壶"
CONFIG = "原生战斗-茶壶配置"
CHECK = "原生战斗-校验茶壶"
FALLBACK_CHECK = "原生战斗-兜底校验茶壶"


def read(relative):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def pipeline_nodes():
    nodes = {}
    for path in (ROOT / "assets/resource/base/pipeline").glob("*.json"):
        nodes.update(json.loads(path.read_text(encoding="utf-8")))
    return nodes


class NativeTeapotOptionTests(unittest.TestCase):
    def test_default_off_changes_configuration_without_disabling_detection(self):
        option = read(f"assets/options/{OPTION}.json")["option"][OPTION]
        self.assertEqual(option["type"], "switch")
        self.assertEqual(option["label"], "是否使用茶壶")
        self.assertEqual(option["default_case"], "No")
        cases = {case["name"]: case for case in option["cases"]}
        self.assertEqual(set(cases), {"No", "Yes"})
        for name, expected in (("No", False), ("Yes", True)):
            self.assertEqual(cases[name]["pipeline_override"], {
                CONFIG: {"attach": {"use_teapot": expected}},
            })
        nodes = pipeline_nodes()
        self.assertIs(nodes[CONFIG]["attach"]["use_teapot"], False)
        for name in (CHECK, FALLBACK_CHECK):
            self.assertIsNot(nodes[name].get("enabled"), False)
            self.assertEqual(nodes[name]["action"]["param"]["custom_action"],
                             "ensure_native_teapot")
            self.assertEqual(nodes[name]["on_error"], [])

    def test_option_is_imported_once(self):
        self.assertEqual(read("assets/interface.json")["import"].count(
            f"options/{OPTION}.json"), 1)

    def test_each_native_selector_exposes_teapot_at_its_first_option_level(self):
        found = set()
        for path in (ROOT / "assets/options").glob("*.json"):
            for name, option in json.loads(path.read_text(encoding="utf-8")).get("option", {}).items():
                cases = {case["name"]: case for case in option.get("cases", [])}
                if not {"native", "bbc"} <= cases.keys():
                    continue
                found.add(name)
                with self.subTest(selector=name):
                    self.assertEqual(cases["native"].get("option", []).count(OPTION), 1)
                    self.assertNotIn(OPTION, cases["bbc"].get("option", []))
        self.assertTrue({"battle_mode", "主线-战斗方式", "Free-战斗方式"} <= found)

    def test_native_only_tasks_expose_teapot_without_chaldea_dependency(self):
        for path in ("assets/tasks/原生自动战斗.json", "assets/tasks/地狱的试炼.json"):
            for task in read(path)["task"]:
                with self.subTest(task=task["name"]):
                    self.assertEqual(task["option"].count(OPTION), 1)
        chaldea = read("assets/options/Chaldea导入手动输入.json")["option"]
        for case in chaldea["原生自动战斗使用Chaldea队伍"]["cases"]:
            self.assertNotIn(OPTION, case.get("option", []))

    def test_native_session_inserts_checks_after_both_formation_completion_modes(self):
        chaldea = read("assets/options/Chaldea导入手动输入.json")["option"]
        native = session_override("teapot-routing-test")
        for case in chaldea["Chaldea编队方式"]["cases"]:
            endpoint = "手动编队检查完成" if case["name"] == "手动" else "自动编队完成"
            self.assertEqual(case["pipeline_override"][endpoint]["next"], ["进本-点击队伍确认"])
        self.assertEqual(native["进本-点击队伍确认"]["next"], [CHECK])
        self.assertEqual(native[CHECK]["next"], ["编队身份-采集"])
        self.assertEqual(pipeline_nodes()["编队身份-采集"]["next"], ["进本-实际点击队伍确认"])

    def test_support_fallback_still_checks_teapot_without_capturing_identities(self):
        native = session_override("teapot-routing-test")
        self.assertEqual(native["进本-助战兜底跳过编队"]["next"], [FALLBACK_CHECK])
        self.assertEqual(native[FALLBACK_CHECK]["next"], ["进本-实际点击队伍确认"])

    def test_shared_bbc_pipeline_keeps_its_original_direct_start_path(self):
        nodes = pipeline_nodes()
        self.assertEqual(nodes["进本-点击队伍确认"]["next"], ["进本-实际点击队伍确认"])
        self.assertEqual(nodes["进本-助战兜底跳过编队"]["next"], ["进本-实际点击队伍确认"])


if __name__ == "__main__":
    unittest.main()
