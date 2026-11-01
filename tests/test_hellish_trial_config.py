"""The trial uses the shared strategy without changing its quest flow."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(relative):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


PIPELINE = load("assets/resource/base/pipeline/地狱的试炼.json")
TASK = load("assets/tasks/地狱的试炼.json")["task"][0]
OPTIONS = load("assets/options/地狱的试炼.json")["option"]
CONFIG_NODE = "原生自动战斗-高级策略配置"


class HellishTrialConfigTests(unittest.TestCase):
    def test_trial_retains_source_and_formation_options_then_shared_strategy(self):
        self.assertEqual(TASK["option"], [
            "地狱的试炼-队伍方案", "Chaldea编队方式", "原生自动战斗是否使用茶壶", "原生自动战斗助战方式",
            "原生自动战斗羁绊满停止", "原生自动战斗高级策略",
        ])
        self.assertEqual(TASK["resource"], ["日服"])
        self.assertIn(TASK["entry"], PIPELINE)

    def test_trial_points_to_shared_settings_and_keeps_its_error_exit(self):
        entry = TASK["pipeline_override"]["原生自动战斗入口"]
        self.assertEqual(entry["attach"]["native_battle_config_node"], CONFIG_NODE)
        self.assertEqual(entry["on_error"], ["地狱的试炼-运行失败判分"])
        self.assertIn(entry["on_error"][0], PIPELINE)
        self.assertEqual(TASK["pipeline_override"]["执行战斗结束_不回主界面"]["next"],
                         ["地狱的试炼-结算调度"])

    def test_trial_no_longer_installs_servant_specific_global_defaults(self):
        self.assertNotIn("地狱的试炼-战斗配置", PIPELINE)
        self.assertEqual(set(OPTIONS), {"地狱的试炼-队伍方案"})
        for document in (PIPELINE, TASK):
            text = json.dumps(document, ensure_ascii=False)
            for field in ("preferred_servant_ids", "np_colors_by_servant_id", "native_strategy_overrides"):
                self.assertNotIn(field, text)
        self.assertNotIn("默认在原计划首回合", TASK["description"])

    def test_trial_source_is_not_replaced_by_strategy_options(self):
        source = OPTIONS["地狱的试炼-队伍方案"]
        self.assertEqual(source["inputs"][0]["default"], "地狱的试炼_202610_阿兹赖尔.json")
        param = source["pipeline_override"]["原生自动战斗入口"]["action"]["param"]["custom_action_param"]
        self.assertEqual(param, {"chaldea_import_source": "{chaldea_import_source}"})
        shared = load("assets/options/原生自动战斗高级策略.json")
        text = json.dumps(shared, ensure_ascii=False)
        self.assertNotIn('"chaldea_import_source"', text)
        self.assertNotIn('"plan"', text)

    def test_trial_and_shared_options_are_imported_once(self):
        imports = load("assets/interface.json")["import"]
        for item in ("tasks/地狱的试炼.json", "options/地狱的试炼.json",
                     "options/原生自动战斗高级策略.json"):
            self.assertEqual(imports.count(item), 1)


if __name__ == "__main__":
    unittest.main()
