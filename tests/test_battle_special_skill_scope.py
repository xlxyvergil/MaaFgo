"""专属弹窗按完整实际编队裁剪；不连接设备、不缩短等待。"""
import copy
from dataclasses import replace
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]
from battle.core.models import FormationSlot, InitialFormation
from battle.core.policy import StrategyProfile
from battle.runtime.runtime import AutoBattleRuntime

ENTRY = "战斗_特殊技能处理"
LILITH = "战斗_莉莉丝3技能_不赋予选择"
ASH = "战斗_马嘶1技能_无敌选择"
COMMON = ("战斗_技能使用弹窗", "战斗_技能无法使用弹窗",
          "战斗_技能目标子屏", "战斗_特殊技能_无覆盖层")
PIPELINE = json.loads((ROOT / "assets/resource/base/pipeline/自动战斗_特殊技能.json").read_text("utf-8"))


def snapshot(ids=("405900", "100500", "200100", None, None, None), *, support=1):
    return InitialFormation(
        slots=tuple(FormationSlot(i, "identified" if sid else "empty", sid,
                                  is_support=i == support if sid else False)
                    for i, sid in enumerate(ids, 1)),
        task_id=1, session_id="scope-test", revision=1, captured_at="test",
        captured_battle=1, calibration_id="verified-test",
    )


def runtime(initial=None, *, roster=None, battle=1, nodes=None):
    nodes = copy.deepcopy(PIPELINE if nodes is None else nodes)
    ctx = NS(get_node_data=Mock(side_effect=lambda name: copy.deepcopy(nodes.get(name, {}))),
             run_task=Mock(return_value=NS(status=NS(failed=False, succeeded=True),
                                         nodes=[NS(name=COMMON[-1])])),
             tasker=NS(stopping=False))
    return AutoBattleRuntime(ctx, object(), NS(plan=None), StrategyProfile(),
                             initial_formation=initial, roster_servant_ids=roster,
                             formation_battle=battle)


class SpecialSkillScopeTests(unittest.TestCase):
    def test_keeps_only_actual_servant_branches_in_original_order(self):
        r = runtime(snapshot())
        self.assertEqual(r._special_skill_pipeline_override()[ENTRY]["next"],
                         [*COMMON[:2], LILITH, *COMMON[2:]])

    def test_support_and_reserves_are_included_and_name_does_not_define_identity(self):
        # 两种职阶的莉莉丝不能按同名合并；后排马嘶与助战骑莉莉丝均保留。
        r = runtime(snapshot(("705100", "405900", "200100", "203300", None, None), support=2))
        kept = r._special_skill_pipeline_override()[ENTRY]["next"]
        self.assertIn(LILITH, kept)
        self.assertIn(ASH, kept)
        r = runtime(snapshot(("705100", "100500", "200100", None, None, None)))
        self.assertEqual(r._special_skill_pipeline_override()[ENTRY]["next"], list(COMMON))

    def test_transformation_aliases_preserve_all_selected_option_nodes(self):
        for ids in (("505700", "1002100", "200100", None, None, None),
                    ("505600", "1002000", "200100", None, None, None)):
            with self.subTest(ids=ids):
                r = runtime(snapshot(ids))
                kept = r._special_skill_pipeline_override()[ENTRY]["next"]
                self.assertIn("战斗_普雷拉蒂1技能_不换形态选择", kept)
                self.assertIn("战斗_芙萝拉3技能_通常状态选择", kept)
                self.assertNotIn(ASH, kept)

    def test_all_ordinary_members_leave_only_common_checks(self):
        r = runtime(snapshot(("100100", "200100", "300100", "400100", "500100", "600100")))
        self.assertEqual(r._special_skill_pipeline_override()[ENTRY]["next"], list(COMMON))

    def test_direct_battle_and_chaldea_only_roster_do_not_exclude_any_branch(self):
        for roster in (None, ("405900", "100500", "200100", None, None, None)):
            r = runtime(roster=roster)
            self.assertEqual(r._special_skill_pipeline_override(), {})
            r.ctx.get_node_data.assert_not_called()

    def test_partial_unknown_uncalibrated_and_reused_snapshots_use_full_fallback(self):
        original = snapshot()
        partial = replace(original, slots=(*original.slots[:3],
                            FormationSlot(4, "unknown", is_support=False), *original.slots[4:]))
        for initial, battle in ((partial, 1), (replace(original, calibration_id="unverified"), 1),
                                (replace(original, source="expected_plan"), 1), (original, 2)):
            with self.subTest(initial=initial, battle=battle):
                self.assertEqual(runtime(initial, battle=battle)._special_skill_pipeline_override(), {})

    def test_complete_verified_chaldea_result_can_scope_but_unknown_or_reused_cannot(self):
        initial = replace(snapshot(), source="chaldea_formation_verified", calibration_id="chaldea-final-check-v1")
        self.assertEqual(runtime(initial)._special_skill_pipeline_override()[ENTRY]["next"],
                         [*COMMON[:2], LILITH, *COMMON[2:]])
        partial = replace(initial, slots=(*initial.slots[:5], FormationSlot(6, "unknown")))
        self.assertEqual(runtime(partial)._special_skill_pipeline_override(), {})
        self.assertEqual(runtime(initial, battle=2)._special_skill_pipeline_override(), {})

    def test_merged_roster_conflict_or_extra_member_does_not_prove_absence(self):
        for roster in ((None, "100500", "200100", None, None, None),
                       ("405900", "100500", "200100", "203300", None, None)):
            self.assertEqual(runtime(snapshot(), roster=roster)._special_skill_pipeline_override(), {})

    def test_future_unmapped_and_malformed_metadata_branches_are_kept(self):
        for value in (None, [], "203300", ["bad"], [None]):
            nodes = copy.deepcopy(PIPELINE)
            nodes[ENTRY]["next"].insert(2, "future")
            nodes[ENTRY]["attach"]["special_skill_servant_ids"]["future"] = value
            r = runtime(snapshot(), nodes=nodes)
            self.assertIn("future", r._special_skill_pipeline_override()[ENTRY]["next"])

    def test_common_nodes_cannot_be_filtered_even_with_incorrect_owner_metadata(self):
        nodes = copy.deepcopy(PIPELINE)
        nodes[ENTRY]["attach"]["special_skill_servant_ids"].update({name: ["203300"] for name in COMMON})
        kept = runtime(snapshot(), nodes=nodes)._special_skill_pipeline_override()[ENTRY]["next"]
        self.assertTrue(all(name in kept for name in COMMON))

    def test_missing_old_bundle_metadata_preserves_original_pipeline(self):
        nodes = copy.deepcopy(PIPELINE)
        nodes[ENTRY].pop("attach")
        self.assertEqual(runtime(snapshot(), nodes=nodes)._special_skill_pipeline_override(), {})

    def test_metadata_read_failure_still_runs_full_detection(self):
        r = runtime(snapshot())
        r.ctx.get_node_data.side_effect = RuntimeError("metadata unavailable")
        with patch("battle.runtime.runtime.mfaalog.warning"):
            self.assertEqual(r._run_special_skill_pipeline(), COMMON[-1])
        r.ctx.run_task.assert_called_once_with(ENTRY, pipeline_override={})

    def test_framework_expanded_next_objects_are_kept_without_reordering_or_mutation(self):
        nodes = copy.deepcopy(PIPELINE)
        nodes[ENTRY]["next"] = [{"name": name, "jump_back": False, "anchor": False}
                                for name in nodes[ENTRY]["next"]]
        original = copy.deepcopy(nodes)
        kept = runtime(snapshot(), nodes=nodes)._special_skill_pipeline_override()[ENTRY]["next"]
        self.assertEqual([b["name"] for b in kept], [*COMMON[:2], LILITH, *COMMON[2:]])
        self.assertEqual(nodes, original)

    def test_scope_does_not_shrink_when_frontline_changes_or_reserve_dies(self):
        r = runtime(snapshot(("405900", "100500", "200100", "203300", None, None)))
        before = r._special_skill_pipeline_override()
        r.roster_servant_ids = ("203300", None, "200100", None, None, None)
        self.assertEqual(r._special_skill_pipeline_override(), before)

    def test_subtask_override_preserves_target_selection_and_does_not_disable_nodes(self):
        r = runtime(snapshot())
        target = {f"战斗_技能目标子屏_选从者{s}": {} if s == 2 else {"enabled": False}
                  for s in (1, 2, 3)}
        self.assertEqual(r._run_special_skill_pipeline(target), COMMON[-1])
        sent = r.ctx.run_task.call_args.kwargs["pipeline_override"]
        self.assertEqual({k: sent[k] for k in target}, target)
        self.assertNotIn(ASH, sent[ENTRY]["next"])
        self.assertNotIn(LILITH, sent)
        self.assertEqual(target[f"战斗_技能目标子屏_选从者2"], {})

    def test_each_runtime_uses_its_own_scope_without_polluting_later_direct_battle(self):
        first = runtime(snapshot())
        first._run_special_skill_pipeline()
        second = runtime()
        second._run_special_skill_pipeline()
        self.assertEqual(second.ctx.run_task.call_args.kwargs["pipeline_override"], {})
        self.assertEqual(PIPELINE[ENTRY]["next"].count(ASH), 1)

    def test_registry_covers_every_current_special_root_and_only_valid_nodes(self):
        owners = PIPELINE[ENTRY]["attach"]["special_skill_servant_ids"]
        self.assertEqual(set(PIPELINE[ENTRY]["next"]) - set(COMMON),
                         set(PIPELINE[ENTRY]["next"]) & owners.keys())
        for name, ids in owners.items():
            self.assertIn(name, PIPELINE)
            self.assertTrue(ids)
            self.assertTrue(all(isinstance(sid, str) and sid.isdecimal() and int(sid) > 0 for sid in ids))


if __name__ == "__main__":
    unittest.main()
