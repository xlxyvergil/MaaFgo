"""Strategy source isolation, portable documents, and actual action handoff."""
import copy
import base64
import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "agent"), str(ROOT / "agent/custom")]
from formation_test_support import battle_action as action
from battle.core.advanced_strategy import (advanced_strategy_to_param,
    default_advanced_strategy, normalize_advanced_strategy, validate_advanced_strategy)
from battle.core.enums import CardColor, Scene
from battle.core.models import BattleState, Confidence
from battle.core.plan_parser import _parse_battle_policy, _parse_strategy_profile
import chaldea


def context(mode="default", **settings):
    attach = {"native_battle_strategy_source": mode, "native_battle_config": {}, **settings}
    nodes = {"battle": {"attach": {"native_battle_config_node": "settings"}},
             "settings": {"attach": attach}}
    return NS(get_node_data=lambda name: nodes.get(name, {}),
              tasker=NS(stopping=False, controller=object()))


class StrategyContractTests(unittest.TestCase):
    def test_default_is_independent_and_matches_all_four_requested_values(self):
        config = default_advanced_strategy()
        param = advanced_strategy_to_param(config)
        policy = _parse_battle_policy(param)
        self.assertEqual(_parse_strategy_profile(param).max_turns, 40)
        self.assertTrue(policy.skill.auto_servant_skills)
        self.assertTrue(policy.card.native_strategy.enabled)
        self.assertFalse(policy.card.native_strategy.opening_hand.enabled)
        self.assertEqual(policy.card.native_strategy.brave_chain_weight, 8)
        self.assertEqual(policy.card.native_strategy.mighty_chain_weight, 12)
        self.assertEqual(policy.card.native_strategy.preferred_servant_ids, ())
        config["openingHand"]["requiredColors"].clear()
        self.assertEqual(default_advanced_strategy()["openingHand"]["requiredColors"], ["B", "Q"])

    def test_partial_fields_and_future_extensions_are_preserved_without_mutation(self):
        config = {"maxTurns": 77, "future": {"keep": [1]}, "openingHand": {"maxWaitTurns": 0}}
        before = copy.deepcopy(config)
        normalized = normalize_advanced_strategy(config)
        self.assertEqual(normalized["maxTurns"], 77)
        self.assertEqual(normalized["future"], {"keep": [1]})
        self.assertTrue(normalized["autoServantSkills"])
        self.assertEqual(config, before)

    def test_presets_translate_to_distinct_existing_policies(self):
        for preset, expected in (("balanced", (8, 12)), ("mighty", (4, 32)),
                                 ("brave", (32, 8)), ("np", (6, 10))):
            with self.subTest(preset=preset):
                policy = _parse_battle_policy(advanced_strategy_to_param({"preset": preset}))
                self.assertEqual((policy.card.native_strategy.brave_chain_weight,
                                  policy.card.native_strategy.mighty_chain_weight), expected)
        self.assertFalse(_parse_battle_policy(advanced_strategy_to_param({"preset": "legacy"})).card.native_strategy.enabled)

    def test_invalid_types_bounds_and_incomplete_waiting_are_visible_errors(self):
        invalid = [None, [], {"preset": []}, {"maxTurns": True}, {"maxTurns": 101},
                   {"autoServantSkills": "false"}, {"openingHand": {"enabled": True}},
                   {"openingHand": {"maxWaitTurns": -1}},
                   {"nativeStrategyOverrides": {"preferred_slots": [4]}},
                   {"nativeStrategyOverrides": {"brave_chain_weight": float("nan")}},
                   {"nativeStrategyOverrides": {"brave_chain_weight": 10 ** 1000}},
                   {"nativeStrategyOverrides": {"np_colors_by_slot": {"1": "green"}}}]
        for config in invalid:
            with self.subTest(config=str(config)[:150]):
                self.assertTrue(validate_advanced_strategy(config))

    def test_opening_overrides_target_with_independent_switch_and_budget(self):
        param = advanced_strategy_to_param({
            "openingHand": {"enabled": True, "maxWaitTurns": 1},
            "nativeStrategyOverrides": {"opening_hand": {
                "servant_id": "605300", "required_colors": ["A", "A"],
                "max_wait_turns": 5, "enabled": False}}})
        opening = _parse_battle_policy(param).card.native_strategy.opening_hand
        self.assertTrue(opening.enabled)
        self.assertEqual((opening.servant_id, opening.required_colors, opening.max_wait_turns),
                         ("605300", (CardColor.ARTS, CardColor.ARTS), 1))


class StrategySourceTests(unittest.TestCase):
    def resolve(self, ctx, param=None, share=None):
        return action._battle_param_with_attached_config(ctx, "battle", param or {}, share)

    def test_default_ignores_stale_manual_settings_and_editor_extension(self):
        ctx = context(native_battle_max_turns=7, native_strategy_overrides="broken",
                      native_battle_opening_hand_enabled=True)
        param = {"plan": {"turns": [{"np_order": [2]}]}, "chaldea_import_source": "keep.json",
                 "max_turns": 2, "card_policy": {"np_first": False}}
        before = copy.deepcopy(param)
        result = self.resolve(ctx, param, {"maaAdvancedStrategy": {"maxTurns": 1}})
        self.assertEqual(result["max_turns"], 40)
        self.assertTrue(_parse_battle_policy(result).card.np_first)
        self.assertEqual(result["plan"], param["plan"])
        self.assertEqual(result["chaldea_import_source"], "keep.json")
        self.assertEqual(param, before)

    def test_manual_ignores_editor_values_and_retains_preset_then_overrides(self):
        ctx = context("manual", native_battle_max_turns=61,
            native_battle_auto_servant_skills=False,
            native_battle_card_policy={"native_strategy": {"mighty_chain_weight": 32}},
            native_strategy_overrides='{"mighty_chain_weight":51,"preferred_slots":[2]}')
        result = self.resolve(ctx, share={"maaAdvancedStrategy": {"maxTurns": 1}})
        policy = _parse_battle_policy(result)
        self.assertEqual(result["max_turns"], 61)
        self.assertFalse(policy.skill.auto_servant_skills)
        self.assertEqual(policy.card.native_strategy.mighty_chain_weight, 51)
        self.assertEqual(policy.card.native_strategy.preferred_slots, (2,))

    def test_manual_opening_text_fields_and_json_target_work(self):
        for target in ({"native_battle_opening_hand_servant_id": "605300"},
                       {"native_strategy_overrides": '{"opening_hand":{"servant_slot":2}}'}):
            result = self.resolve(context("manual", native_battle_opening_hand_enabled=True,
                native_battle_opening_hand_required_colors="B,Q",
                native_battle_opening_hand_max_wait_turns=0, **target))
            opening = _parse_battle_policy(result).card.native_strategy.opening_hand
            self.assertTrue(opening.enabled)
            self.assertEqual(opening.required_colors, (CardColor.BUSTER, CardColor.QUICK))
            self.assertEqual(opening.max_wait_turns, 0)

    def test_editor_ignores_hidden_manual_and_falls_back_field_by_field(self):
        ctx = context("editor", native_battle_max_turns=3, native_strategy_overrides="bad")
        result = self.resolve(ctx, share={"maaAdvancedStrategy": {"preset": "np", "maxTurns": 88}})
        policy = _parse_battle_policy(result)
        self.assertEqual(result["max_turns"], 88)
        self.assertTrue(policy.skill.auto_servant_skills)
        self.assertFalse(policy.card.native_strategy.opening_hand.enabled)
        for share in (None, {}, {"actions": []}):
            self.assertEqual(self.resolve(ctx, share=share)["max_turns"], 40)

    def test_editor_invalid_extension_and_manual_json_fail_early(self):
        for value in (None, [], {"maxTurns": "40"}):
            with self.assertRaises(ValueError):
                self.resolve(context("editor"), share={"maaAdvancedStrategy": value})
        for raw in ("{broken", "null", "[]"):
            with self.assertRaises(ValueError):
                self.resolve(context("manual", native_strategy_overrides=raw))


class StrategyActionHandoffTests(unittest.TestCase):
    def run_action(self, ctx, param, *, fallback=False):
        with patch.object(action, "context_session", return_value=(11, "token" if fallback else None)), \
             patch.object(action.sessions, "read", return_value=(None, 1)), \
             patch.object(action.sessions, "support_fallback", return_value=fallback), \
             patch.object(action, "AutoBattleRuntime") as runtime:
            runtime.return_value.run.return_value = NS(ok=True, reason="victory", turns=1)
            result = action.AutoBattleAction().run(ctx, NS(node_name="battle", custom_action_param=json.dumps(param)))
        return result, runtime

    def test_no_configuration_no_plan_and_no_formation_use_new_defaults(self):
        ctx = NS(get_node_data=lambda name: {}, tasker=NS(stopping=False, controller=object()))
        result, runtime = self.run_action(ctx, {})
        self.assertTrue(result.success)
        self.assertEqual(runtime.call_args.args[3].max_turns, 40)
        self.assertTrue(runtime.call_args.args[4].card.native_strategy.enabled)
        self.assertTrue(runtime.call_args.args[4].skill.auto_servant_skills)
        self.assertIsNone(runtime.call_args.args[2].plan)
        self.assertEqual(runtime.call_args.kwargs["roster_servant_ids"], (None,) * 6)

    def test_single_fetch_supplies_editor_config_and_keeps_explicit_plan(self):
        share = {"maaAdvancedStrategy": {"maxTurns": 76, "autoServantSkills": False},
                 "actions": [{"type": "attack", "attacks": []}]}
        param = {"chaldea_import_source": "local.json", "plan": {"turns": [{"np_order": [2]}]}}
        with patch.object(action, "fetch_share_data", return_value=(share, "0", "local")) as fetch:
            result, runtime = self.run_action(context("editor"), param)
        self.assertTrue(result.success)
        fetch.assert_called_once_with("local.json")
        self.assertEqual(runtime.call_args.args[3].max_turns, 76)
        self.assertEqual(runtime.call_args.args[2].plan.turns[0].np_order, (2,))

    def test_old_actions_share_uses_defaults_and_keeps_empty_fixed_turn(self):
        share = {"actions": [{"type": "attack", "attacks": []}]}
        result, runtime = self.run_action(context("editor"), {"chaldea_share": share})
        self.assertTrue(result.success)
        decider = runtime.call_args.args[2]
        self.assertIsNotNone(decider.plan.active_turn(0))
        state = BattleState(Scene.MAIN_BATTLE, Confidence(1), (), (), ())
        self.assertEqual(decider.decide(state).servant_skills, ())

    def test_strategy_only_document_runs_without_plan_and_skips_enabled_waiting(self):
        share = {"maaAdvancedStrategy": {"maxTurns": 62, "openingHand": {
            "enabled": True, "servantSlot": 2}}}
        result, runtime = self.run_action(context("editor"), {"chaldea_share": share})
        self.assertTrue(result.success)
        self.assertIsNone(runtime.call_args.args[2].plan)
        self.assertEqual(runtime.call_args.args[3].max_turns, 62)
        self.assertFalse(runtime.call_args.args[4].card.native_strategy.opening_hand.enabled)

    def test_fallback_and_formation_reset_drop_old_editor_source(self):
        for fallback in (False, True):
            with self.subTest(fallback=fallback), patch.object(action, "fetch_share_data") as fetch:
                result, runtime = self.run_action(context("editor"), {
                    "chaldea_import_source": "stale.json", "native_battle_formation_reset": True,
                    "chaldea_share": {"maaAdvancedStrategy": {"maxTurns": 5}}}, fallback=fallback)
                self.assertTrue(result.success)
                self.assertEqual(runtime.call_args.args[3].max_turns, 40)
                self.assertIsNone(runtime.call_args.args[2].plan)
                fetch.assert_not_called()

    def test_manual_fallback_keeps_nonidentity_strategy_but_clears_old_identity(self):
        ctx = context("manual", native_battle_max_turns=63,
            native_battle_opening_hand_enabled=True, native_battle_opening_hand_servant_id="605300",
            native_strategy_overrides=json.dumps({"preferred_servant_ids": ["605300"],
                "preferred_slots": [2], "mighty_chain_weight": 55,
                "np_colors_by_servant_id": {"605300": "A"}, "np_colors_by_slot": {"2": "A"}}))
        result, runtime = self.run_action(ctx, {"chaldea_import_source": "stale.json"}, fallback=True)
        self.assertTrue(result.success)
        native = runtime.call_args.args[4].card.native_strategy
        self.assertEqual(native.mighty_chain_weight, 55)
        self.assertEqual(runtime.call_args.args[3].max_turns, 63)
        self.assertEqual((native.preferred_servant_ids, native.preferred_slots,
                          native.np_colors_by_servant_id, native.np_colors_by_slot), ((), (), (), ()))
        self.assertFalse(native.opening_hand.enabled)

    def test_invalid_configuration_never_starts_runtime(self):
        result, runtime = self.run_action(context("editor"), {"chaldea_share": {"maaAdvancedStrategy": None}})
        self.assertFalse(result.success)
        runtime.assert_not_called()


class StrategyOnlyLocalFileTests(unittest.TestCase):
    def test_actions_and_strategy_only_files_are_recognized_but_arbitrary_json_is_not(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(chaldea, "CACHE_DIR", directory):
            for index, document in enumerate(({"actions": []},
                {"maaAdvancedStrategy": {"maxTurns": 54}}, {"unrelated": True})):
                name = f"sample{index}.json"
                (Path(directory) / name).write_text(json.dumps(document), encoding="utf-8")
                loaded = chaldea._load_local_file(name)
                self.assertEqual(loaded, document if index < 2 else None)

    def test_strategy_only_and_actions_only_documents_roundtrip_local_and_offline_sources(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(chaldea, "CACHE_DIR", directory):
            for index, document in enumerate(({"actions": []},
                {"maaAdvancedStrategy": {"maxTurns": 54}})):
                name = f"battle{index}.json"
                (Path(directory) / name).write_text(json.dumps(document), encoding="utf-8")
                self.assertEqual(chaldea.fetch_share_data(name)[0], document)
                encoded = "G" + base64.urlsafe_b64encode(gzip.compress(json.dumps(document).encode())).decode()
                self.assertEqual(chaldea.fetch_share_data(encoded)[0], document)


if __name__ == "__main__":
    unittest.main()
