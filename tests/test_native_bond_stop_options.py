"""Public option wiring and settlement stop boundaries for native battle."""
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
OPTION = "原生自动战斗羁绊满停止"
GUARD = "结算-羁绊满停止"
CONFIG = "原生自动战斗-羁绊停止配置"


def read(relative):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


class NativeBondOptionTests(unittest.TestCase):
    def test_master_switch_and_default_all_multiselect(self):
        interface = read("assets/interface.json")
        self.assertIn("options/原生自动战斗羁绊满停止.json", interface["import"])
        options = read("assets/options/原生自动战斗羁绊满停止.json")["option"]
        option = options[OPTION]
        self.assertEqual(option["type"], "switch")
        self.assertEqual(option["default_case"], "No")
        self.assertEqual([x["name"] for x in option["cases"]], ["No", "Yes"])
        selection = options["原生自动战斗羁绊检查号位"]
        self.assertEqual(selection["type"], "checkbox")
        self.assertEqual(selection["default_case"], [f"slot{i}" for i in range(1, 7)])
        self.assertEqual(option["cases"][1]["option"], ["原生自动战斗羁绊检查号位"])
        for enabled, case in enumerate(option["cases"]):
            overrides = case["pipeline_override"]
            self.assertEqual(overrides[CONFIG]["attach"]["native_bond_stop_mode"], "slots" if enabled else "off")
            self.assertEqual(overrides[GUARD]["enabled"], bool(enabled))
            for i in range(1, 7):
                self.assertIs(overrides[CONFIG]["attach"][f"native_bond_stop_slot_{i}"], False)
            victory = overrides["战斗_胜利"]["recognition"]
            if enabled:
                self.assertEqual(victory["type"], "Or")
                self.assertEqual(victory["param"]["any_of"], ["战斗_战后RESULT", "战斗_战利品弹窗"])
            else:
                self.assertEqual(victory["type"], "TemplateMatch")
                self.assertEqual(victory["param"]["template"], "battle/下一步.png")
                self.assertEqual(victory["param"]["roi"], [0, 0, 0, 0])
        for i, case in enumerate(selection["cases"], 1):
            self.assertEqual(case["pipeline_override"], {CONFIG: {"attach": {f"native_bond_stop_slot_{i}": True}}})
        native = read("assets/resource/base/pipeline/原生自动战斗.json")
        self.assertEqual(native[CONFIG]["attach"]["native_bond_stop_mode"], "off")
        nodes = read("assets/resource/base/pipeline/原生自动战斗调度.json")
        self.assertFalse(nodes[GUARD]["enabled"])

    def test_all_five_native_entry_points_expose_bond_before_advanced(self):
        entries = [
            read("assets/tasks/原生自动战斗.json")["task"][0]["option"],
            read("assets/tasks/地狱的试炼.json")["task"][0]["option"],
        ]
        for path, name in (("战斗方式", "battle_mode"), ("自动推主线", "主线-战斗方式"),
                           ("自动清free本", "Free-战斗方式")):
            item = read(f"assets/options/{path}.json")["option"][name]
            native = next(x for x in item["cases"] if x["name"] == "native")
            entries.append(native["option"])
            bbc = next(x for x in item["cases"] if x["name"] == "bbc")
            self.assertNotIn(OPTION, bbc.get("option", []))
        for entry in entries:
            self.assertEqual(entry.count(OPTION), 1)
            self.assertLess(entry.index(OPTION), entry.index("原生自动战斗高级策略"))

    def test_guard_precedes_all_next_step_continue_and_blind_candidates(self):
        nodes = read("assets/resource/base/pipeline/原生自动战斗调度.json")
        for name in ("作战成功_结束战斗", "作战成功_结束战斗_继续",
                     "好友申请界面_不回主界面", "好友申请界面_连续出击继续",
                     "结束战斗_不回主界面", "结束战斗_连续出击继续",
                     "结算推进_点击右上角", "结算推进_点击右上角_继续"):
            with self.subTest(name=name):
                self.assertEqual(nodes[name]["next"][0], GUARD)
        trial = read("assets/resource/base/pipeline/地狱的试炼.json")
        self.assertEqual(trial["地狱的试炼-结算调度"]["next"][0], GUARD)
        guard = nodes[GUARD]
        self.assertEqual(guard["next"], ["结算-羁绊停止出口"])
        self.assertEqual(guard["on_error"], ["结算-羁绊停止异常出口"])
        self.assertTrue(set(guard["next"]).isdisjoint(guard["on_error"]))
        for exit_name in (*guard["next"], *guard["on_error"]):
            self.assertEqual(nodes[exit_name]["action"]["type"], "StopTask")
            self.assertEqual(nodes[exit_name]["next"], [])
            self.assertEqual(nodes[exit_name]["on_error"], [])
        self.assertGreaterEqual(guard["attach"]["check_attempts"], 2)
        wait = nodes[guard["attach"]["check_wait_node"]]
        self.assertGreater(wait["pre_delay"], 0)
        self.assertEqual(wait["action"]["type"], "DoNothing")
        freezes = wait["post_wait_freezes"]
        self.assertGreaterEqual(freezes["time"], 1000)
        self.assertGreaterEqual(freezes["timeout"], freezes["time"])
        self.assertEqual(freezes["target"], [80, 450, 1120, 100])
        for prefix, expected_delay in (("page_wait", 50), ("positive", 100)):
            key = f"{prefix}_node" if prefix == "page_wait" else "positive_wait_node"
            polling = nodes[guard["attach"][key]]
            self.assertEqual(polling["pre_delay"], expected_delay)
            self.assertEqual(polling["post_delay"], 0)
            self.assertEqual(polling["action"]["type"], "DoNothing")
            self.assertEqual(polling["next"], [])


if __name__ == "__main__":
    unittest.main()
