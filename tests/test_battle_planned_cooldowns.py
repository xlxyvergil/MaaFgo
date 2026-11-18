"""Planned turns avoid CD reads; fallback and waiting use current cooldowns."""
from pathlib import Path
from dataclasses import replace
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]

from battle.core.decider import RuleDecider
from battle.core.enums import Scene
from battle.core.models import BattlePlan, Confidence, ServantSkillAction, TurnPlan
from battle.core.policy import StrategyProfile
from battle.core.validator import skip_unusable_servant_skills
from battle.perception import config, perception
from battle.runtime.runtime import AutoBattleRuntime


PLAN = BattlePlan((TurnPlan(servant_skills=(ServantSkillAction(1, 1),)),))
ROSTER = ("300500", "605300", "504500", None, None, None)
COOLDOWN_NODES = {
    config.SERVANT_SKILL_CD_NODE.format(servant_slot=slot, skill_index=index)
    for slot in (1, 2, 3) for index in (1, 2, 3)
} | {config.MASTER_SKILL_CD_NODE.format(skill_index=index) for index in (1, 2, 3)}


def runtime(plan=PLAN, *, known_roster=False):
    def recognize(name, image):
        return NS(hit=name in {"战斗_主界面", "战斗_敌人1", "战斗_敌人1_选中",
                              "战斗_从者1技能1_CD", "战斗_御主技能1_CD"},
                  best_result=NS(score=.99))

    context = NS(tasker=NS(stopping=False), run_recognition=Mock(side_effect=recognize))
    controller = Mock()
    controller.post_screencap.return_value.wait.return_value.get.side_effect = lambda: object()
    result = AutoBattleRuntime(context, controller, RuleDecider(plan=plan), StrategyProfile(),
                               roster_servant_ids=ROSTER if known_roster else None)
    if known_roster:
        # Slot 2 has no reliable identity in this frame and must stay absent.
        result.servant_recognizer = NS(detect=Mock(return_value=tuple(
            NS(slot=slot, servant_id=sid, confidence=Confidence(.99),
               margin=.2, matched_template="portrait")
            for slot, sid in ((1, ROSTER[0]), (2, None), (3, ROSTER[2]))
        )))
    return result


def cooldown_calls(run):
    return [call for call in run.ctx.run_recognition.call_args_list
            if call.args[0] in COOLDOWN_NODES]


class PlannedCooldownTests(unittest.TestCase):
    def test_plan_skips_all_cooldowns_but_keeps_identity_enemy_and_empty_slot_checks(self):
        run = runtime(known_roster=True)
        state = run._observe()
        self.assertEqual(cooldown_calls(run), [])
        run.servant_recognizer.detect.assert_called_once()
        self.assertEqual([(servant.slot, servant.servant_id) for servant in state.servants],
                         [(1, ROSTER[0]), (3, ROSTER[2])])
        self.assertIsNone(run.roster_servant_ids[1])
        self.assertEqual([(enemy.slot, enemy.targeted) for enemy in state.enemies], [(1, True)])
        action, skipped = skip_unusable_servant_skills(run.decider.decide(state), state, run.profile)
        self.assertEqual(skipped, ())
        self.assertEqual(action.servant_skills, PLAN.turns[0].servant_skills)
        self.assertTrue(all(skill.available for servant in state.servants for skill in servant.skills))
        self.assertTrue(all(skill.available for skill in state.master_skills))

    def test_without_identity_evidence_skip_does_not_invent_servant_ids(self):
        run = runtime()
        state = run._observe()
        self.assertEqual(cooldown_calls(run), [])
        self.assertEqual([servant.servant_id for servant in state.servants], [None] * 3)
        self.assertTrue(all(servant.confidence.value == 0 for servant in state.servants))
        self.assertEqual(run.roster_servant_ids, (None,) * 6)

    def test_no_plan_empty_plan_and_out_of_plan_turns_read_actual_cooldowns(self):
        for plan, turn in ((None, 0), (BattlePlan(), 0), (PLAN, 1), (PLAN, 8), (PLAN, -1)):
            with self.subTest(plan=plan, turn=turn):
                run = runtime(plan)
                run._turn_index = turn
                state = run._observe()
                self.assertEqual({call.args[0] for call in cooldown_calls(run)}, COOLDOWN_NODES)
                self.assertFalse(state.servants[0].skills[0].available)
                self.assertFalse(state.master_skills[0].available)
                action = run.decider.decide(state, turn_index=turn)
                self.assertNotIn(ServantSkillAction(1, 1, target_ally=1), action.servant_skills)
                self.assertIn(ServantSkillAction(1, 2, target_ally=1), action.servant_skills)

    def test_opening_wait_reads_real_cd_then_plan_resumes_without_reusing_cd(self):
        run = runtime()
        run._opening_hand_pending = True
        waiting = run._observe()
        self.assertEqual(len(cooldown_calls(run)), 12)
        self.assertFalse(waiting.servants[0].skills[0].available)
        run.ctx.run_recognition.reset_mock()
        run._opening_hand_pending = False
        planned = run._observe()
        self.assertEqual(cooldown_calls(run), [])
        self.assertTrue(planned.servants[0].skills[0].available)

    def test_first_extra_turn_reads_cd_on_its_own_new_frame(self):
        run = runtime()
        run._observe()
        self.assertEqual(cooldown_calls(run), [])
        planned_frame = run.ctx.run_recognition.call_args_list[0].args[1]
        run.ctx.run_recognition.reset_mock()
        run._turn_index = 1
        state = run._observe()
        calls = cooldown_calls(run)
        self.assertEqual(len(calls), 12)
        self.assertTrue(all(call.args[1] is not planned_frame for call in calls))
        self.assertFalse(state.servants[0].skills[0].available)

    def test_perception_defaults_to_reading_cooldowns(self):
        run = runtime()
        state = perception.build(run.ctx, object())
        self.assertEqual(len(cooldown_calls(run)), 12)
        self.assertFalse(state.servants[0].skills[0].available)

    def test_deferred_cd_read_preserves_identity_enemies_and_missing_slots_on_same_frame(self):
        run = runtime(known_roster=True)
        state = replace(run._observe(), unknown_fields=("servant[1].skill[1].available", "other_unknown"))
        frame = run._observation_image
        run.ctx.run_recognition.reset_mock()
        observed = perception.read_skill_cooldowns(run.ctx, frame, state)
        self.assertEqual(len(cooldown_calls(run)), 9)  # Two visible servants and three Master skills.
        self.assertEqual(run.ctx.run_recognition.call_count, 9)
        self.assertTrue(all(call.args[1] is frame for call in cooldown_calls(run)))
        self.assertEqual([(servant.slot, servant.servant_id) for servant in observed.servants],
                         [(1, ROSTER[0]), (3, ROSTER[2])])
        self.assertFalse(observed.servants[0].skills[0].available)
        self.assertFalse(observed.master_skills[0].available)
        self.assertIs(observed.enemies, state.enemies)
        self.assertEqual(observed.unknown_fields, ("other_unknown",))
        run.servant_recognizer.detect.assert_called_once()

    def test_deferred_cd_read_does_not_read_non_main_scenes(self):
        run = runtime()
        state = replace(run._observe(), scene=Scene.VICTORY)
        run.ctx.run_recognition.reset_mock()
        self.assertIs(perception.read_skill_cooldowns(run.ctx, object(), state), state)
        run.ctx.run_recognition.assert_not_called()


if __name__ == "__main__":
    unittest.main()
