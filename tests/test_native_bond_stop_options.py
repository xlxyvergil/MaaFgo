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
    def test_independent_option_and_disabled_default(self):
        interface = read("assets/interface.json")
        self.assertIn("options/原生自动战斗羁绊满停止.json", interface["import"])
        option = read("assets/options/原生自动战斗羁绊满停止.json")["option"][OPTION]
        self.assertEqual(option["default"], "off")
        self.assertEqual([x["name"] for x in option["cases"]],
                         ["off", "all", *[f"slot{i}" for i in range(1, 7)]])
        native = read("assets/resource/base/pipeline/原生自动战斗.json")
        self.assertEqual(native[CONFIG]["attach"]["native_bond_stop_mode"], "off")
        nodes = read("assets/resource/base/pipeline/原生自动战斗调度.json")
        self.assertFalse(nodes[GUARD]["enabled"])
        for case in option["cases"]:
            overrides = case["pipeline_override"]
            self.assertEqual(set(overrides), {CONFIG, GUARD, "战斗_胜利"})
            mode = "slot" if case["name"].startswith("slot") else case["name"]
            slot = int(case["name"][4:]) if mode == "slot" else 0
            self.assertEqual(overrides[CONFIG]["attach"], {
                "native_bond_stop_mode": mode, "native_bond_stop_slot": slot,
            })
            self.assertEqual(overrides[GUARD]["enabled"], mode != "off")
            # Keep the existing scene recognition as one TemplateMatch; do not
            # add another per-frame recognition, OCR, or nested And/Or branch.
            victory = overrides["战斗_胜利"]["recognition"]
            self.assertEqual(victory["type"], "TemplateMatch")
            self.assertEqual(victory["param"]["template"],
                             "battle/下一步.png" if mode == "off" else "battle/bond/战后结果标题.png")
            self.assertEqual(victory["param"]["roi"],
                             [0, 0, 0, 0] if mode == "off" else [450, 0, 400, 120])
            self.assertEqual(victory["param"]["threshold"], 0.8)
        original = read("assets/resource/base/pipeline/自动战斗_感知.json")["战斗_胜利"]["recognition"]
        self.assertEqual(original["type"], "TemplateMatch")
        self.assertEqual(original["param"]["template"], "battle/下一步.png")

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
        self.assertEqual(guard["on_error"], ["结算-羁绊停止出口"])
        self.assertEqual(nodes["结算-羁绊停止出口"]["action"]["type"], "StopTask")
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
