"""Editable native strategy IDs use the same aliases as actual battle identities."""
import copy
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from formation_test_support import battle_action as action
from battle.core.enums import CardColor
from battle.core.plan_parser import _parse_battle_policy


def config(native):
    return {"card_policy": {"native_strategy": native}}


class NativeStrategyAliasTests(unittest.TestCase):
    def test_aliases_and_duplicate_ids_normalize_without_changing_source(self):
        param = config({"enabled": True,
                        "preferred_servant_ids": [505700, "505600", "1002100", 99999999, True, None, "bad"],
                        "np_colors_by_servant_id": {"505700": "A", "1002100": "Q", "99999999": "B", "bad": "A"},
                        "preferred_slots": [3], "brave_chain_weight": 13})
        param.update({"chaldea_import_source": "keep.json", "plan": {"turns": [{"np_order": [2]}]},
                      "custom_field": {"untouched": [1, 2]}})
        original = copy.deepcopy(param)
        result = action._canonical_native_strategy_param(param)
        native = _parse_battle_policy(result).card.native_strategy
        self.assertEqual(native.preferred_servant_ids, ("505600", "1002000", "99999999"))
        self.assertEqual(dict(native.np_colors_by_servant_id), {
            "505600": CardColor.ARTS, "1002000": CardColor.QUICK, "99999999": CardColor.BUSTER})
        self.assertEqual(result["plan"], original["plan"])
        self.assertEqual(result["chaldea_import_source"], "keep.json")
        self.assertEqual(result["custom_field"], original["custom_field"])
        self.assertEqual(native.preferred_slots, (3,))
        self.assertEqual(native.brave_chain_weight, 13)
        self.assertEqual(param, original)

    def test_explicit_canonical_color_wins_over_alias_regardless_of_key_order(self):
        for colors in ({"505700": "A", "505600": "B"}, {"505600": "B", "505700": "A"}):
            with self.subTest(colors=colors):
                result = action._canonical_native_strategy_param(config({
                    "enabled": True, "np_colors_by_servant_id": colors}))
                native = _parse_battle_policy(result).card.native_strategy
                self.assertEqual(dict(native.np_colors_by_servant_id), {"505600": CardColor.BUSTER})

    def test_malformed_fields_keep_existing_parser_fallback(self):
        for ids, colors in (("505700", []), (None, True), (False, "A"),
                            ([False, -1, "invalid", 10**15], {"0": "A", "505700": "arts"})):
            with self.subTest(ids=ids, colors=colors):
                param = config({"enabled": True, "preferred_servant_ids": ids,
                                "np_colors_by_servant_id": colors, "color_weights": {"A": 9}})
                result = action._canonical_native_strategy_param(param)
                self.assertEqual(_parse_battle_policy(result), _parse_battle_policy(param))

    def test_unconfigured_battle_params_remain_identical(self):
        for param in ({}, {"card_policy": None}, config(None), config({"enabled": True}),
                      config({"np_colors_by_slot": {"1": "A"}})):
            with self.subTest(param=param):
                self.assertIs(action._canonical_native_strategy_param(param), param)

    def test_real_action_normalizes_after_attached_strategy_merge_before_decider(self):
        nodes = {"battle": {"attach": {"native_battle_config_node": "settings"}},
                 "settings": {"attach": {"native_battle_config": {"card_policy": {"native_strategy": {
                     "enabled": True, "preferred_servant_ids": [505700],
                     "np_colors_by_servant_id": {"505700": "A"}}}}}}}
        context = NS(get_node_data=lambda name: nodes.get(name),
                     tasker=NS(stopping=False, controller=object()))
        param = {"chaldea_import_source": "keep.json", "plan": {"turns": []}}
        original = copy.deepcopy(param)
        with patch.object(action, "context_session", return_value=(123, None)), \
             patch.object(action, "fetch_share_data") as fetch, \
             patch.object(action, "AutoBattleRuntime") as runtime:
            runtime.return_value.run.return_value = NS(ok=True, reason="victory", turns=1)
            result = action.AutoBattleAction().run(context, NS(node_name="battle", custom_action_param=param))
        self.assertTrue(result.success)
        native = runtime.call_args.args[4].card.native_strategy
        self.assertEqual(native.preferred_servant_ids, ("505600",))
        self.assertEqual(dict(native.np_colors_by_servant_id), {"505600": CardColor.ARTS})
        self.assertEqual(param, original)
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
