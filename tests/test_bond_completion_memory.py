import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent/custom"), str(ROOT / "agent")]

from bond_completion_action import CompleteBondFormation  # noqa: E402
from bond_completion_memory import (  # noqa: E402
    BondCompletionMemory,
    build_task_key,
    formation_signature,
)


class BondCompletionMemoryTests(unittest.TestCase):
    def setUp(self):
        self.expected = [
            {
                "slot": slot,
                "kind": "LOCAL" if slot == 0 else "EMPTY",
                "svt_id": 100100 if slot == 0 else None,
                "equip_id": 930000 if slot == 0 else None,
                "equip_limit_break": slot == 0,
                "grand_svt": False,
            }
            for slot in range(6)
        ]
        self.settings = {
            "bond_base": 1200,
            "preferred_rarity": 5,
            "modify_unspecified_servants": True,
        }

    def test_task_key_is_stable_and_changes_with_effective_setting(self):
        first = build_task_key(self.expected, self.settings)
        second = build_task_key(
            [dict(item) for item in self.expected], dict(self.settings)
        )
        self.assertEqual(first, second)
        changed = dict(self.settings, bond_base=1300)
        self.assertNotEqual(first, build_task_key(self.expected, changed))

    def test_memory_round_trip_and_invalid_document(self):
        signature = formation_signature(
            ["local:100100", "empty", "empty", "empty", "empty", "empty"],
            ["equip:930000", "none", "none", "none", "none", "none"],
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auto_memory.json"
            memory = BondCompletionMemory(path)
            self.assertIsNone(memory.get("task"))
            memory.put("task", signature)
            self.assertEqual(memory.get("task"), signature)
            document = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(document["schema_version"], 1)
            path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "版本"):
                memory.get("task")

    def test_expired_memory_does_not_freeze_realtime_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            memory = BondCompletionMemory(path)
            signature = formation_signature(["empty"] * 6, ["none"] * 6)
            memory.put("task", signature)
            data = json.loads(path.read_text("utf-8"))
            data["entries"]["task"]["updated_at"] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertIsNone(memory.get("task"))

    def test_effective_inventory_catalog_and_cost_invalidate_memory(self):
        action = CompleteBondFormation()
        action.auto_memory_enabled = True
        action.expected = self.expected
        action.preferred_rarity, action.bond_base, action.max_cost = 5, 1200, 114
        action.use_local_servant_inventory = action.use_local_equip_inventory = True
        action.modify_unspecified_servants = action.modify_unspecified_equips = True
        action.use_support_substitution = False
        action.quest_type = action.grand_class = ""
        action.local_servant_inventory_active = action.local_equip_inventory_active = True
        action.local_servant_ids, action.local_equip_ids = {"100100"}, {"9400010"}
        action.servant_database = {"100100": {"cost": 16}}
        action.equip_database = {"9400010": {"cost": 12}}
        with tempfile.TemporaryDirectory() as temp, patch("bond_completion_action._BOND_MEMORY_PATH", str(Path(temp) / "memory.json")):
            action._prepare_auto_memory()
            first = action.memory_task_key
            action._prepare_auto_memory()
            self.assertEqual(first, action.memory_task_key)
            for change in (
                lambda: action.local_servant_ids.add("200100"),
                lambda: action.local_equip_ids.add("9407480"),
                lambda: action.equip_database["9400010"].update(cost=9),
                lambda: setattr(action, "local_servant_inventory_active", False),
                lambda: setattr(action, "max_cost", 115),
            ):
                before = action.memory_task_key
                change()
                action._prepare_auto_memory()
                self.assertNotEqual(before, action.memory_task_key)

    def test_extra_class_groups_invalidate_only_legacy_extra_memory(self):
        action = CompleteBondFormation()
        vars(action).update(
            auto_memory_enabled=True, expected=self.expected,
            preferred_rarity=5, bond_base=1200, max_cost=114,
            use_local_servant_inventory=True, use_local_equip_inventory=True,
            modify_unspecified_servants=True, modify_unspecified_equips=True,
            use_support_substitution=False, servant_database={}, equip_database={},
        )
        signature = formation_signature(["empty"] * 6, ["none"] * 6)
        with tempfile.TemporaryDirectory() as directory, \
                patch("bond_completion_action._BOND_MEMORY_PATH", str(Path(directory) / "memory.json")), \
                patch("bond_completion_action.build_memory_task_key", wraps=build_task_key) as build:
            for quest_type, grand_class, allowed in (
                ("", "", None), ("grand", "saber", None),
                ("grand", "ex1", {"ruler", "avenger", "moonCancer", "shielder"}),
                ("grand", "ex2", {"alterEgo", "foreigner", "pretender", "beast"}),
            ):
                with self.subTest(grand_class=grand_class):
                    action.quest_type, action.grand_class = quest_type, grand_class
                    action._prepare_auto_memory()
                    settings = dict(build.call_args.args[1])
                    legacy = dict(settings)
                    actual = legacy.pop("grand_candidate_classes", None)
                    self.assertEqual(actual, sorted(allowed) if allowed else None)
                    legacy_key = build_task_key(self.expected, legacy)
                    action.memory_store.put(legacy_key, signature)
                    action._prepare_auto_memory()
                    if allowed:
                        self.assertNotEqual(action.memory_task_key, legacy_key)
                        self.assertIsNone(action.remembered_signature)
                    else:
                        self.assertEqual(action.memory_task_key, legacy_key)
                        self.assertEqual(action.remembered_signature, signature)

    def test_option_defaults_to_enabled_and_pipeline_has_safe_default(self):
        options = json.loads(
            (ROOT / "assets/options/Chaldea羁绊补齐.json").read_text(
                encoding="utf-8"
            )
        )["option"]
        pipeline = json.loads(
            (ROOT / "assets/resource/base/pipeline/羁绊补齐自动编队.json").read_text(
                encoding="utf-8"
            )
        )
        enabled = next(
            case
            for case in options["自动补齐从者和羁绊礼装"]["cases"]
            if case["name"] == "Yes"
        )
        self.assertIn("自动记忆", enabled["option"])
        self.assertEqual(options["自动记忆"]["default_case"], "Yes")
        self.assertIs(
            pipeline["执行羁绊补齐自动编队"]["attach"]["auto_memory_enabled"],
            True,
        )

    def test_memory_hit_full_servant_reoptimizes_when_replacement_allowed(self):
        action = CompleteBondFormation.__new__(CompleteBondFormation)
        action.remembered_signature = {"servants": [], "equips": []}
        action.unspecified_servants_by_slot = {
            2: {"id": "300100", "name": "测试从者", "slot": 2}
        }
        action.modify_unspecified_servants = True
        action.full_existing_slots = set()
        action.unavailable_servants = set()
        action._build_auto_memory_signature = lambda *args: action.remembered_signature
        action._check_new_servant_bond = lambda slot, servant: "full"
        result = action._try_auto_memory_hit([], [], {}, [], [])
        self.assertIsNone(result)
        self.assertEqual(action.full_existing_slots, {2})
        self.assertEqual(action.unavailable_servants, {"300100"})

    def test_memory_hit_full_servant_continues_when_replacement_locked(self):
        action = CompleteBondFormation.__new__(CompleteBondFormation)
        action.remembered_signature = {"servants": [], "equips": []}
        action.unspecified_servants_by_slot = {
            2: {"id": "300100", "name": "测试从者", "slot": 2}
        }
        action.modify_unspecified_servants = False
        action.full_existing_slots = set()
        action.unavailable_servants = set()
        action._build_auto_memory_signature = lambda *args: action.remembered_signature
        action._check_new_servant_bond = lambda slot, servant: "full"
        action._finish_auto_memory_hit = lambda slots: tuple(slots)
        self.assertEqual(
            action._try_auto_memory_hit([], [], {}, [], []),
            (2,),
        )


if __name__ == "__main__":
    unittest.main()
