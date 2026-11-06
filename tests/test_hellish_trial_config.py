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
            "地狱的试炼-队伍方案", "地狱的试炼-挑战次数", "Chaldea编队方式", "原生自动战斗是否使用茶壶", "原生自动战斗助战方式",
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
        self.assertEqual(set(OPTIONS), {"地狱的试炼-队伍方案", "地狱的试炼-挑战次数"})
        for document in (PIPELINE, TASK):
            text = json.dumps(document, ensure_ascii=False)
            for field in ("preferred_servant_ids", "np_colors_by_servant_id", "native_strategy_overrides"):
                self.assertNotIn(field, text)
        self.assertNotIn("默认在原计划首回合", TASK["description"])

    def test_trial_source_is_not_replaced_by_strategy_options(self):
        source = OPTIONS["地狱的试炼-队伍方案"]
        self.assertEqual(source["inputs"][0]["default"], "地狱的试炼_202611_天魔轰临_枪AE双核.json")
        param = source["pipeline_override"]["原生自动战斗入口"]["action"]["param"]["custom_action_param"]
        self.assertEqual(param, {"chaldea_import_source": "{chaldea_import_source}"})
        shared = load("assets/options/原生自动战斗高级策略.json")
        text = json.dumps(shared, ensure_ascii=False)
        self.assertNotIn('"chaldea_import_source"', text)
        self.assertNotIn('"plan"', text)

    def test_repeat_count_defaults_to_one_and_does_not_replace_battle_source(self):
        count = OPTIONS["地狱的试炼-挑战次数"]
        self.assertEqual(count["inputs"][0]["default"], "1")
        self.assertEqual(count["inputs"][0]["pipeline_type"], "int")
        self.assertEqual(count["pipeline_override"], {
            "地狱的试炼-入口": {"attach": {"battle_count": "{battle_count}"}},
        })
        wrapper = PIPELINE[TASK["entry"]]
        self.assertEqual(wrapper["action"]["param"]["custom_action"], "auto_battle_repeat")
        params = wrapper["action"]["param"]["custom_action_param"]
        self.assertEqual(params["battle_entry_node"], "地狱的试炼-单场入口")
        self.assertIn("地狱的试炼-已在助战编队战斗", PIPELINE[params["battle_entry_node"]]["next"])
        self.assertEqual(wrapper["attach"]["battle_count"], 1)
        self.assertEqual(wrapper["on_error"], [])

    def test_each_trial_gets_fresh_navigation_and_settlement_hit_budgets(self):
        params = PIPELINE[TASK["entry"]]["action"]["param"]["custom_action_param"]
        reset = params["reset_hit_nodes"]
        self.assertNotIn(TASK["entry"], reset)
        self.assertEqual(len(reset), len(set(reset)))
        for name, node in PIPELINE.items():
            if "max_hit" in node and name != TASK["entry"]:
                self.assertIn(name, reset)
        self.assertEqual(PIPELINE["地狱的试炼-已返回活动页"]["next"], [])
        self.assertEqual(PIPELINE["地狱的试炼-失败后提示"]["next"], ["原生自动战斗-失败出口"])

    def test_trial_popups_reuse_all_support_modes_without_auto_formation(self):
        support = load("assets/options/原生自动战斗助战方式.json")["option"]["原生自动战斗助战方式"]
        aliases = {
            "进本-攻略提示关闭": "地狱的试炼-攻略告知关闭",
            "进本-编成条件提示": "地狱的试炼-冠位编成告知关闭",
        }
        generic = load("assets/resource/base/pipeline/进本流程.json")
        for alias, leaf in aliases.items():
            override = TASK["pipeline_override"][alias]
            self.assertEqual(override["recognition"]["param"]["all_of"], [leaf])
            self.assertEqual(override["next"], [])
            self.assertEqual(override["on_error"], [])
            self.assertEqual(override["action"], {"type": "Click", "param": {"target": True}})
            self.assertEqual(generic[alias]["action"]["type"], "Click")
            self.assertEqual(PIPELINE[leaf]["next"], [])
            self.assertEqual(PIPELINE[leaf]["action"]["type"], "Click")
            for case in support["cases"]:
                self.assertIn("[JumpBack]" + alias, case["pipeline_override"]["进本流程"]["next"])
                self.assertNotIn(alias, case["pipeline_override"])
        for leaf, count in ((aliases["进本-攻略提示关闭"], 3), (aliases["进本-编成条件提示"], 5)):
            combined = PIPELINE[leaf]["recognition"]["param"]
            self.assertEqual(len(combined["all_of"]), count)
            self.assertEqual(combined["box_index"], count - 1)
            self.assertEqual(combined["all_of"][-1]["recognition"]["param"]["expected"], ["^閉じる$"])

    def test_trial_and_shared_options_are_imported_once(self):
        imports = load("assets/interface.json")["import"]
        for item in ("tasks/地狱的试炼.json", "options/地狱的试炼.json",
                     "options/原生自动战斗高级策略.json"):
            self.assertEqual(imports.count(item), 1)


if __name__ == "__main__":
    unittest.main()
