"""Pure policy boundaries: configuration, quotas and material eligibility."""
from dataclasses import replace
import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent/custom"))
from expball_policy import (CardObservation, ExpballConfig, ExpballConfigError,
                            TargetQuota, boolean, material_allowed, parse_targets,
                            read_level, read_number)


def target(index=1, *, kind="servant", identity="100", count=1, mode="count"):
    return {f"target_{index}_kind": kind, f"target_{index}_id": identity,
            f"target_{index}_count": count, f"target_{index}_mode": mode}


class ExpballPolicyTests(unittest.TestCase):
    def test_false_string_is_false_and_ambiguous_values_are_rejected(self):
        config = ExpballConfig.parse({"eat3": "false", "eat4": "true", "draw_fp": "false"})
        self.assertFalse(config.eat3)
        self.assertTrue(config.eat4)
        self.assertFalse(config.draw_fp)
        for value in (0, 1, "0", "1", "yes", "False", None, []):
            with self.subTest(value=value), self.assertRaises(ExpballConfigError):
                boolean(value, "option")

    def test_hundred_draw_with_targets_is_rejected_before_runtime(self):
        with self.assertRaisesRegex(ExpballConfigError, "100抽"):
            ExpballConfig.parse({"draw_fp": True, "draw_batch": 100, **target()})
        self.assertEqual(ExpballConfig.parse({"draw_fp": True, "draw_batch": 100}).draw_batch, 100)

    def test_invalid_batches_numbers_and_empty_work_are_rejected(self):
        for patch in ({"draw_batch": 50}, {"ball_star": 3}, {"max_cycles": 0},
                      {"max_draw_batches": True}, {"friend_points_reserve": "-1"},
                      {"make_ball": False}):
            with self.subTest(patch=patch), self.assertRaises(ExpballConfigError):
                ExpballConfig.parse(patch)

    def test_ce_budgets_default_independently_of_other_limits(self):
        config = ExpballConfig.parse({"max_cycles": 1, "max_draw_batches": 2, "friend_points_reserve": 3})
        self.assertEqual(config.max_material_batches, 10)
        self.assertEqual(config.max_ce_qp, 100000)
        config = ExpballConfig.parse({"max_material_batches": "0", "max_ce_qp": "0"})
        self.assertEqual(config.max_material_batches, 0)
        self.assertEqual(config.max_ce_qp, 0)

    def test_ce_budget_options_match_entry_and_parser_boundaries(self):
        options = json.loads((ROOT / "assets/options/搓丸子.json").read_text("utf-8"))["option"]
        entry = json.loads((ROOT / "assets/resource/base/pipeline/搓丸子入口.json").read_text("utf-8"))
        auto_ce = options["搓丸子-自动搓丸子"]
        active = next(case for case in auto_ce["cases"] if case["name"] == "on")
        inactive = next(case for case in auto_ce["cases"] if case["name"] == "off")
        for option, field, limit in (
            ("搓丸子-礼装强化批次上限", "max_material_batches", 9999),
            ("搓丸子-礼装强化QP上限", "max_ce_qp", 999999999),
        ):
            with self.subTest(field=field):
                self.assertIn(option, active["option"])
                self.assertNotIn(option, inactive.get("option", []))
                config_input, = options[option]["inputs"]
                self.assertEqual(config_input["name"], field)
                self.assertEqual(config_input["pipeline_type"], "int")
                attach = options[option]["pipeline_override"]["执行搓丸子"]["attach"]
                self.assertEqual(attach, {field: "{" + field + "}"})
                self.assertEqual(int(config_input["default"]), getattr(ExpballConfig.parse({}), field))
                self.assertEqual(int(config_input["default"]), entry["执行搓丸子"]["attach"][field])
                for value in ("0", str(limit)):
                    self.assertIsNotNone(re.fullmatch(config_input["verify"], value))
                    self.assertEqual(getattr(ExpballConfig.parse({field: value}), field), int(value))
                for value in ("-1", str(limit + 1), "1.5", "", True):
                    self.assertIsNone(re.fullmatch(config_input["verify"], str(value)))
                    with self.assertRaisesRegex(ExpballConfigError, config_input["label"]):
                        ExpballConfig.parse({field: value})

    def test_same_identity_finite_targets_merge_but_types_stay_separate(self):
        entries = parse_targets({**target(1, count=2), **target(2, count=3),
                                 **target(3, kind="equip", count=4)})
        self.assertEqual([(x.kind, x.identity, x.remaining) for x in entries],
                         [("servant", "100", 5), ("equip", "100", 4)])

    def test_unlimited_merge_is_explicit_and_never_negative(self):
        for a, b in (("all", "count"), ("count", "all")):
            with self.subTest(a=a, b=b):
                entry, = parse_targets({**target(1, mode=a), **target(2, mode=b)})
                self.assertIsNone(entry.count)
                for _ in range(4): self.assertTrue(entry.record())
                self.assertIsNone(entry.remaining)
                self.assertEqual(entry.confirmed, 4)

    def test_finite_quota_never_overflows_to_unlimited(self):
        entry = TargetQuota("servant", "100", 1)
        self.assertTrue(entry.record())
        for _ in range(5): self.assertFalse(entry.record())
        self.assertEqual(entry.remaining, 0)
        self.assertEqual(entry.confirmed, 1)
        self.assertFalse(entry.needed)

    def test_stop_all_requires_valid_finite_targets_and_friend_mode(self):
        for patch in ({"stop_targets": True},
                      {"draw_fp": True, "stop_targets": True, **target(mode="all")},
                      {**target()}, {"draw_fp": True, **target(count=0)}):
            with self.subTest(patch=patch), self.assertRaises(ExpballConfigError):
                ExpballConfig.parse(patch)
        config = ExpballConfig.parse({"draw_fp": True, "stop_targets": True, **target()})
        self.assertTrue(config.stop_targets)

    def test_ocr_missing_invalid_or_partial_is_not_zero(self):
        for value in ("", None, "12OO", "1,23", "余额 2000", "2000/9999", "?"):
            with self.subTest(value=value): self.assertIsNone(read_number(value))
        for value, expected in [("0", 0), ("1,234", 1234), ("１，２３４", 1234), ("12 345", 12345)]:
            with self.subTest(value=value): self.assertEqual(read_number(value), expected)

    def test_level_requires_current_and_maximum_in_range(self):
        self.assertEqual(read_level("Lv. 1 / 50"), (1, 50))
        self.assertEqual(read_level("５０／５０"), (50, 50))
        for value in ("50", "0/50", "51/50", "1/999", "unknown"):
            with self.subTest(value=value): self.assertIsNone(read_level(value))

    def test_material_rules_preserve_locks_marks_experience_and_targets(self):
        config = ExpballConfig.parse({})
        base = CardObservation("10", 1, 10, 50, False, False)
        fresh = CardObservation("20", 2, 1, 55, False, False)
        self.assertTrue(material_allowed(fresh, base, config))
        for card in (replace(fresh, locked=True), replace(fresh, marked=True),
                     replace(fresh, current=2), replace(fresh, current=55),
                     replace(fresh, rarity=3), replace(fresh, rarity=4), replace(fresh, rarity=5)):
            with self.subTest(card=card): self.assertFalse(material_allowed(card, base, config))
        self.assertFalse(material_allowed(fresh, base, config, protected_ids={"20"}))

    def test_three_and_four_star_material_switches_are_independent(self):
        base = CardObservation("10", 1, 10, 50, False, False)
        three = CardObservation("30", 3, 1, 60, False, False)
        four = CardObservation("40", 4, 1, 80, False, False)
        config = ExpballConfig.parse({"eat4": True})
        self.assertFalse(material_allowed(three, base, config))
        self.assertTrue(material_allowed(four, base, config))
        self.assertFalse(material_allowed(replace(four, rarity=5), base, config))

    def test_unbroken_base_only_accepts_same_identity(self):
        base = CardObservation("10", 1, 1, 20, False, False)
        config = ExpballConfig.parse({"eat3": True})
        self.assertTrue(material_allowed(CardObservation("10", 1, 1, 20, False, False), base, config))
        self.assertFalse(material_allowed(CardObservation("20", 1, 1, 20, False, False), base, config))


if __name__ == "__main__":
    unittest.main()
