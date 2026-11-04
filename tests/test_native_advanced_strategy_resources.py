"""Validate strategy UI reachability and source isolation across native tasks."""
import json
from pathlib import Path
import copy
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
KEY = "原生自动战斗高级策略"
CONFIG = "原生自动战斗-高级策略配置"


def load(relative):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


class NativeAdvancedStrategyResourcesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.options = {}
        cls.tasks = {}
        for relative in load("assets/interface.json")["import"]:
            document = load("assets/" + relative)
            cls.options.update(document.get("option", {}))
            cls.tasks.update({task["name"]: task for task in document.get("task", [])})
        cls.shared = load("assets/options/原生自动战斗高级策略.json")["option"]

    def reachable_options(self, names, seen=None):
        seen = set() if seen is None else seen
        for name in names:
            self.assertIn(name, self.options, name)
            if name in seen:
                continue
            seen.add(name)
            option = self.options[name]
            self.reachable_options(option.get("option", []), seen)
            for case in option.get("cases", []):
                self.reachable_options(case.get("option", []), seen)
        return seen

    def test_only_manual_mode_exposes_child_options(self):
        option = self.shared[KEY]
        self.assertEqual(option["default"], "default")
        cases = {case["name"]: case for case in option["cases"]}
        self.assertEqual(list(cases), ["default", "editor", "manual"])
        self.assertEqual([case["label"] for case in cases.values()],
                         ["默认", "使用编辑器/chaldea配置", "手动"])
        for name in ("default", "editor"):
            self.assertFalse(cases[name].get("option"))
        self.assertEqual(len(cases["manual"]["option"]), 5)
        for name, case in cases.items():
            self.assertEqual(case["pipeline_override"], {
                CONFIG: {"attach": {"native_battle_strategy_source": name}},
            })
        self.reachable_options([KEY])

    def test_manual_defaults_match_shared_contract(self):
        self.assertEqual(self.shared["原生自动战斗补牌预设"]["default"], "平衡")
        self.assertEqual(self.shared["原生自动战斗开局等待关键牌"]["default_case"], "No")
        self.assertEqual(self.shared["原生自动战斗自动补放技能"]["default_case"], "Yes")
        turns = self.shared["原生自动战斗最大回合"]["inputs"][0]
        self.assertEqual((turns["default"], turns["pipeline_type"]), ("40", "int"))
        override = self.shared["原生自动战斗策略微调"]["inputs"][0]
        self.assertEqual(json.loads(override["default"]), {})
        conditions = {item["name"]: item for item in
                      self.shared["原生自动战斗开局等牌条件"]["inputs"]}
        self.assertEqual(conditions["opening_hand_servant_id"]["default"], "")
        self.assertEqual(conditions["opening_hand_servant_slot"]["default"], "0")
        self.assertEqual(conditions["opening_hand_required_colors"]["default"], "B,Q")
        self.assertEqual(conditions["opening_hand_max_wait_turns"]["default"], "2")

    def test_shared_settings_are_last_in_each_native_option_branch(self):
        for path, name in (("assets/options/战斗方式.json", "battle_mode"),
                           ("assets/options/自动推主线.json", "主线-战斗方式")):
            with self.subTest(path=path):
                option = load(path)["option"][name]
                native = next(case for case in option["cases"] if case["name"] == "native")
                self.assertEqual(native["option"][-1], KEY)
                self.assertEqual(native["option"].count(KEY), 1)
                bbc = next(case for case in option["cases"] if case["name"] == "bbc")
                self.assertNotIn(KEY, self.reachable_options(bbc.get("option", [])))
        for path in ("assets/tasks/原生自动战斗.json", "assets/tasks/地狱的试炼.json"):
            options = load(path)["task"][0]["option"]
            self.assertEqual(options[-1], KEY)
            self.assertEqual(options.count(KEY), 1)

    def test_all_native_tasks_can_reach_the_shared_option(self):
        task_files = (
            "直接战斗", "日常战斗", "冠位戴冠战", "迦勒底之门", "通用小活动", "通用大活动",
            "自动推主线", "原生自动战斗", "地狱的试炼",
        )
        for name in task_files:
            with self.subTest(task=name):
                task = load("assets/tasks/" + name + ".json")["task"][0]
                self.assertIn(KEY, self.reachable_options(task["option"]))

    def test_strategy_is_last_in_the_final_visible_default_native_list(self):
        def visible(names, selections):
            result = []
            for name in names:
                result.append(name)
                option = self.options[name]
                result.extend(visible(option.get("option", []), selections))
                cases = option.get("cases", [])
                if cases:
                    selected = selections.get(name, option.get("default", option.get("default_case")))
                    case = next((case for case in cases if case["name"] == selected), cases[0])
                    result.extend(visible(case.get("option", []), selections))
            return result

        for name in ("直接战斗", "日常战斗", "冠位戴冠战", "迦勒底之门", "通用小活动", "通用大活动",
                     "自动推主线", "原生自动战斗", "地狱的试炼"):
            task = load("assets/tasks/" + name + ".json")["task"][0]
            for gate in ("种火", "修炼场", "QP"):
                with self.subTest(task=name, gate=gate):
                    selections = {"battle_mode": "native", "主线-战斗方式": "native",
                                  "chaldea_gate_quest_type": gate}
                    self.assertEqual(visible(task["option"], selections)[-1], KEY)

    def test_only_shared_node_receives_manual_overrides(self):
        source = load("assets/resource/base/pipeline/原生自动战斗.json")
        self.assertEqual(source[CONFIG]["attach"]["native_battle_strategy_source"], "default")
        for entry in ("原生自动战斗入口", "原生自动战斗_Chaldea入口"):
            self.assertEqual(source[entry]["attach"]["native_battle_config_node"], CONFIG)
        for name, option in self.shared.items():
            for override in [option.get("pipeline_override", {}),
                             *(case.get("pipeline_override", {}) for case in option.get("cases", []))]:
                with self.subTest(option=name):
                    self.assertLessEqual(set(override), {CONFIG})

    def test_no_trial_identity_or_color_is_installed_by_a_shared_preset(self):
        for case in self.shared["原生自动战斗补牌预设"]["cases"]:
            native = case["pipeline_override"][CONFIG]["attach"]["native_battle_card_policy"]["native_strategy"]
            with self.subTest(preset=case["name"]):
                for key in ("preferred_servant_ids", "preferred_slots", "np_colors_by_servant_id",
                            "np_colors_by_slot", "opening_hand"):
                    self.assertNotIn(key, native)

    def test_editor_labels_preserve_existing_option_and_case_identifiers(self):
        source = self.options["原生自动战斗使用Chaldea队伍"]
        self.assertEqual(source["label"], "使用编辑器/chaldea配置")
        self.assertEqual([case["name"] for case in source["cases"]], ["No", "Yes"])
        support = self.options["原生自动战斗助战方式"]
        case = next(case for case in support["cases"] if case["name"] == "使用Chaldea配置")
        self.assertEqual(case["label"], "使用编辑器/chaldea配置")


class NativeAdvancedStrategyResourceIntegrationTests(unittest.TestCase):
    """Exercise actual UI overrides through the runtime configuration reader."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "tests"))
        from formation_test_support import battle_action
        from battle.core.advanced_strategy import advanced_strategy_to_param
        from battle.core.plan_parser import _parse_battle_policy, _parse_strategy_profile
        cls.action = battle_action
        cls.convert = staticmethod(advanced_strategy_to_param)
        cls.policy = staticmethod(_parse_battle_policy)
        cls.profile = staticmethod(_parse_strategy_profile)
        cls.options = load("assets/options/原生自动战斗高级策略.json")["option"]

    def context(self, mode, additional=None):
        nodes = copy.deepcopy(load("assets/resource/base/pipeline/原生自动战斗.json"))
        nodes[CONFIG]["attach"].update({"native_battle_strategy_source": mode,
                                          **(additional or {})})
        return SimpleNamespace(get_node_data=lambda name: nodes.get(name))

    def case_attach(self, name, case):
        selected = next(value for value in self.options[name]["cases"] if value["name"] == case)
        return copy.deepcopy(selected["pipeline_override"][CONFIG]["attach"])

    def input_attach(self, name, values=None):
        option = self.options[name]
        inputs = {item["name"]: item for item in option["inputs"]}
        selected = {key: (values or {}).get(key, item["default"]) for key, item in inputs.items()}
        attach = {}
        for key, placeholder in option["pipeline_override"][CONFIG]["attach"].items():
            field = placeholder[1:-1]
            raw = selected[field]
            attach[key] = int(raw) if inputs[field].get("pipeline_type") == "int" else raw
        return attach

    def manual_defaults(self, preset="平衡"):
        return {
            **self.case_attach("原生自动战斗补牌预设", preset),
            **self.case_attach("原生自动战斗开局等待关键牌", "No"),
            **self.case_attach("原生自动战斗自动补放技能", "Yes"),
            **self.input_attach("原生自动战斗最大回合"),
            **self.input_attach("原生自动战斗策略微调"),
        }

    def read(self, context, param=None, share=None):
        return self.action._battle_param_with_attached_config(
            context, "原生自动战斗入口", param or {}, share_data=share,
        )

    def test_every_manual_preset_matches_the_shared_editor_preset(self):
        for manual, persisted in (("平衡", "balanced"), ("三色", "mighty"),
                                  ("勇猛", "brave"), ("回收", "np"), ("旧策略", "legacy")):
            with self.subTest(preset=manual):
                actual = self.read(self.context("manual", self.manual_defaults(manual)))
                expected = self.convert({"preset": persisted})
                self.assertEqual(self.policy(actual), self.policy(expected))
                self.assertEqual(self.profile(actual).max_turns, 40)

    def test_default_and_editor_ignore_invalid_hidden_manual_values(self):
        hidden = {"native_strategy_overrides": "{broken", "native_battle_max_turns": 1000,
                  "native_battle_opening_hand_enabled": True,
                  "native_battle_auto_servant_skills": "invalid"}
        source = {"chaldea_import_source": "retained.json", "plan": {"turns": []}}
        for mode, share, expected in (
            ("default", {"maaAdvancedStrategy": {"maxTurns": 99}}, self.convert()),
            ("editor", {}, self.convert()),
            ("editor", {"maaAdvancedStrategy": {"preset": "brave", "maxTurns": 53}},
             self.convert({"preset": "brave", "maxTurns": 53})),
        ):
            with self.subTest(mode=mode, share=share):
                actual = self.read(self.context(mode, hidden), source, share)
                self.assertEqual(self.policy(actual), self.policy(expected))
                self.assertEqual(self.profile(actual).max_turns, expected["max_turns"])
                self.assertEqual(actual["chaldea_import_source"], source["chaldea_import_source"])
                self.assertEqual(actual["plan"], source["plan"])

    def test_independent_opening_fields_win_while_empty_target_preserves_json(self):
        manual = {
            **self.manual_defaults("三色"),
            **self.case_attach("原生自动战斗开局等待关键牌", "Yes"),
            **self.input_attach("原生自动战斗开局等牌条件", {
                "opening_hand_servant_id": "205500", "opening_hand_servant_slot": "3",
                "opening_hand_required_colors": "Q,Q", "opening_hand_max_wait_turns": "5",
            }),
            **self.input_attach("原生自动战斗策略微调", {"native_strategy_overrides": json.dumps({
                "mighty_chain_weight": 47, "opening_hand": {"enabled": False,
                    "servant_id": "605300", "servant_slot": 1,
                    "required_colors": ["A"], "max_wait_turns": 0},
            })}),
        }
        selected = self.policy(self.read(self.context("manual", manual))).card.native_strategy
        self.assertEqual(selected.mighty_chain_weight, 47)
        self.assertTrue(selected.opening_hand.enabled)
        self.assertEqual(selected.opening_hand.servant_id, "205500")
        self.assertEqual(selected.opening_hand.servant_slot, 3)
        self.assertEqual([color.value for color in selected.opening_hand.required_colors], ["Q", "Q"])
        self.assertEqual(selected.opening_hand.max_wait_turns, 5)
        manual.update(self.input_attach("原生自动战斗开局等牌条件", {
            "opening_hand_required_colors": "B,Q", "opening_hand_max_wait_turns": "0",
        }))
        selected = self.policy(self.read(self.context("manual", manual))).card.native_strategy
        self.assertEqual(selected.opening_hand.servant_id, "605300")
        self.assertEqual(selected.opening_hand.servant_slot, 1)
        self.assertEqual(selected.opening_hand.max_wait_turns, 0)


if __name__ == "__main__":
    unittest.main()
