"""Position-only skill detection must not invent servant identity evidence."""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, call, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]

from battle.core.decider import RuleDecider
from battle.core.enums import PrimitiveKind, Scene
from battle.core.models import (ActorBinding, BattleAction, BattlePlan, Confidence,
                                PlannedCard, ServantSkillAction, TurnPlan)
from battle.core.policy import BattlePolicy, SkillPolicy, StrategyProfile
from battle.core.validator import skip_unusable_servant_skills, validate_main_action
from battle.execution import coords
from battle.perception import config, perception
from battle.runtime.runtime import AutoBattleRuntime


COOLDOWN = {(1, 2), (2, 1), (3, 3)}
SKILL_NODES = {
    (slot, skill): config.SERVANT_SKILL_CD_NODE.format(servant_slot=slot, skill_index=skill)
    for slot in (1, 2, 3) for skill in (1, 2, 3)
}
AVAILABLE = [key for key in SKILL_NODES if key not in COOLDOWN]


def context():
    cooldown_nodes = {SKILL_NODES[key] for key in COOLDOWN}
    return NS(tasker=NS(stopping=False), run_recognition=Mock(
        side_effect=lambda node, _: NS(
            hit=node == "战斗_主界面" or node in cooldown_nodes,
            best_result=NS(score=.99),
        )))


def runtime(*, plan=None, roster=None):
    controller = Mock()
    controller.post_screencap.return_value.wait.return_value.get.return_value = object()
    result = AutoBattleRuntime(context(), controller, RuleDecider(plan=plan),
                               StrategyProfile(), roster_servant_ids=roster)
    result._run_special_skill_pipeline = Mock(return_value=result._SKILL_NO_OVERLAY_BRANCH)
    result._wait_until = Mock(return_value=True)
    return result


class WithoutFormationTests(unittest.TestCase):
    def test_missing_candidates_collects_all_nine_cooldowns_without_claiming_identity(self):
        for candidates in ((), (None,) * 6):
            with self.subTest(candidates=candidates):
                ctx = context()
                recognizer = Mock()
                servants = perception._detect_servants(ctx, object(), candidates, recognizer=recognizer)
                self.assertEqual([servant.slot for servant in servants], [1, 2, 3])
                self.assertTrue(all(servant.servant_id is None for servant in servants))
                self.assertTrue(all(servant.confidence.value == 0 for servant in servants))
                self.assertEqual(
                    [(servant.slot, index) for servant in servants
                     for index, skill in enumerate(servant.skills, start=1) if skill.available],
                    AVAILABLE,
                )
                self.assertEqual([args.args[0] for args in ctx.run_recognition.call_args_list],
                                 list(SKILL_NODES.values()))
                recognizer.detect.assert_not_called()

    def test_skill_detection_only_runs_on_main_battle_screen(self):
        for scene in Scene:
            with self.subTest(scene=scene):
                ctx = context()
                composer = NS(detect_all=lambda *_: ((), ()))
                with patch.object(perception, "_detect_scene", return_value=(scene, 1.0)):
                    observed = perception.build(ctx, object(), composed_recognizer=composer)
                detected = {args.args[0] for args in ctx.run_recognition.call_args_list}
                if scene is Scene.MAIN_BATTLE:
                    self.assertEqual(len(observed.servants), 3)
                    self.assertTrue(set(SKILL_NODES.values()).issubset(detected))
                else:
                    self.assertEqual(observed.servants, ())
                    self.assertTrue(set(SKILL_NODES.values()).isdisjoint(detected))

    def test_main_screen_takeover_casts_available_position_skills_and_keeps_identity_unknown(self):
        rt = runtime()
        with patch("battle.runtime.runtime.time.sleep"):
            observed = rt._observe(settle_main=True)
            action = rt.decider.decide(observed)
            action, skipped = skip_unusable_servant_skills(action, observed, rt.profile)
            self.assertEqual(skipped, ())
            self.assertTrue(validate_main_action(action, observed, rt.profile).ok)
            self.assertTrue(rt._execute_skills(action))
        self.assertEqual(rt.controller.post_click.call_args_list,
                         [call(*coords.SERVANT_SKILL_CLICK[key]) for key in AVAILABLE])
        self.assertEqual(rt.controller.post_screencap.call_count, 2)
        self.assertEqual(rt.roster_servant_ids, (None,) * 6)
        self.assertEqual(rt.candidate_servant_ids, (None,) * 6)
        self.assertEqual(rt._semantic_roster(), (None,) * 6)
        self.assertIsNone(rt._special_skill_servant_ids)
        for args, key in zip(rt._run_special_skill_pipeline.call_args_list, AVAILABLE):
            selected = key[0]
            self.assertEqual(args.args[0], {
                f"战斗_技能目标子屏_选从者{slot}": {} if slot == selected else {"enabled": False}
                for slot in (1, 2, 3)
            })

    def test_auto_skill_switch_still_disables_position_only_casts(self):
        rt = runtime()
        observed = rt._observe()
        decider = RuleDecider(BattlePolicy(skill=SkillPolicy(auto_servant_skills=False)))
        self.assertEqual(decider.decide(observed).servant_skills, ())

    def test_failed_identity_match_with_candidates_does_not_enable_position_fallback(self):
        ctx = context()
        recognizer = Mock()
        recognizer.detect.return_value = tuple(
            NS(slot=slot, servant_id=None, confidence=Confidence(.2),
               margin=0, matched_template="", visible_top=35)
            for slot in (1, 2, 3)
        )
        self.assertEqual(perception._detect_servants(
            ctx, object(), ("100100", None, None, None, None, None), recognizer=recognizer), ())
        recognizer.detect.assert_called_once()
        ctx.run_recognition.assert_not_called()

    def test_bound_plan_skill_unknown_or_wrong_identity_stops_before_click(self):
        bound = ServantSkillAction(1, 1, actor=ActorBinding(0, "100100"))
        plan = BattlePlan(turns=(TurnPlan(servant_skills=(bound,)),))
        for roster in (None, ("100200", None, None, None, None, None)):
            with self.subTest(roster=roster):
                rt = runtime(plan=plan, roster=roster)
                if roster is None:
                    observed = rt._observe()
                    action = rt.decider.decide(observed)
                else:
                    action = BattleAction(None, (), servant_skills=(bound,))
                self.assertFalse(rt._execute_skills(action))
                rt.controller.post_click.assert_not_called()
                rt._run_special_skill_pipeline.assert_not_called()

    def test_bound_plan_card_identity_is_not_satisfied_by_position_only_observation(self):
        card = PlannedCard(PrimitiveKind.SELECT_NP, 1, actor=ActorBinding(0, "100100"))
        rt = runtime(plan=BattlePlan(turns=(TurnPlan(card_sequence=(card,)),)))
        rt._observe()
        self.assertFalse(rt._check_card_actor_bindings())
        rt.controller.post_click.assert_not_called()


if __name__ == "__main__":
    unittest.main()
