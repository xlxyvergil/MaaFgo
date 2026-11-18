"""Position-only skill detection must not invent servant identity evidence."""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, call, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]

from battle.core.decider import RuleDecider
from battle.core.enums import CardColor, PrimitiveKind, Scene
from battle.core.models import (ActorBinding, BattleAction, BattlePlan, Confidence,
                                PlannedCard, ServantSkillAction, TurnPlan)
from battle.core.policy import BattlePolicy, SkillPolicy, StrategyProfile
from battle.core.validator import skip_unusable_servant_skills, validate_main_action
from battle.execution import coords
from battle.perception import config, perception
from battle.perception.frontline import FrontlineIdentity
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


def catalog_report(*members):
    return NS(elapsed_seconds=.5, slots=tuple(
        NS(slot=slot, servant_id=servant_id, best_score=.97, margin=.1, matched_template="portrait")
        for slot, servant_id in members
    ))


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
        for args in rt._run_special_skill_pipeline.call_args_list:
            self.assertEqual(args.args[0], {
                f"战斗_技能目标子屏_选从者{slot}": {} if slot == 1 else {"enabled": False}
                for slot in (1, 2, 3)
            })

    @patch("battle.runtime.runtime.time.sleep")
    @patch("battle.runtime.runtime.StatusCatalogRecognizer")
    def test_startup_uses_partial_portrait_evidence_for_skills_and_np(self, catalog, _sleep):
        rt = runtime()
        image = NS(ndim=3)
        rt.controller.post_screencap.return_value.wait.return_value.get.return_value = image
        catalog.return_value.detect.return_value = NS(elapsed_seconds=.5, slots=(
            NS(slot=1, servant_id="504500", best_score=.96, margin=.1, matched_template="support"),
            NS(slot=2, servant_id="100100", best_score=.97, margin=.1, matched_template="attacker"),
            NS(slot=3, servant_id=None),
        ))
        state = rt._observe(settle_main=True)
        self.assertEqual([servant.servant_id for servant in state.servants], ["504500", "100100", None])
        skills = rt.decider.decide(state).servant_skills
        self.assertEqual([(s.servant_slot, s.skill_index) for s in skills], AVAILABLE)
        self.assertEqual({s.target_ally for s in skills}, {2})
        self.assertEqual(rt._semantic_roster(), (None,) * 6)
        self.assertIsNone(rt._special_skill_servant_ids)

        np = NS(ui_slot=2, owner_slot=2, servant_id="100100", visual_present=True,
                authoritative=True, color=CardColor.BUSTER, reason="identified",
                confidence=Confidence(.96, "composed_card"), matched_template="np")
        with patch.object(perception, "_detect_scene", return_value=(Scene.COMMAND_SELECTION, 1.0)), \
                patch.object(perception.ComposedCardRecognizer, "default") as composer:
            composer.return_value.detect_all.return_value = ((), (np,))
            rt.ctx.run_task = Mock(return_value=NS(status=NS(succeeded=True)))
            state = rt._observe(settle_main=True)
            composer.return_value.detect_all.assert_called_once_with(image, ("504500", "100100", None))
        self.assertEqual(rt.decider.decide(state).picks[0].kind, PrimitiveKind.SELECT_NP)
        self.assertEqual(state.np_cards[0].owner_servant_id, "100100")

        # The old slot 2 portrait no longer matches. It must stop informing
        # card recognition, even if a hit appears in a previously unknown slot.
        rt.servant_recognizer = NS(detect=Mock(return_value=(
            FrontlineIdentity(1, "504500", Confidence(.95)),
            FrontlineIdentity(2, None, Confidence(.3)),
            FrontlineIdentity(3, "100100", Confidence(.95)),
        )))
        state = rt._observe(settle_main=True)
        self.assertEqual(rt.roster_servant_ids[:3], ("504500", None, None))
        skills = rt.decider.decide(state).servant_skills
        self.assertEqual([(s.servant_slot, s.skill_index) for s in skills], AVAILABLE)
        self.assertEqual({s.target_ally for s in skills}, {1})
        self.assertEqual(catalog.return_value.detect.call_args_list, [call(image), call(image, slots=(2,))])

    @patch("battle.runtime.runtime.time.sleep")
    @patch("battle.runtime.runtime.StatusCatalogRecognizer")
    def test_portrait_recovery_updates_only_missing_slot_with_new_identity(self, catalog, _sleep):
        rt = runtime()
        image = NS(ndim=3)
        rt.controller.post_screencap.return_value.wait.return_value.get.return_value = image
        catalog.return_value.detect.side_effect = (
            catalog_report((1, "504500"), (2, "100100")),
            catalog_report((2, "304800")),
        )
        rt._observe(settle_main=True)
        rt.servant_recognizer = NS(detect=Mock(return_value=(
            FrontlineIdentity(1, "504500", Confidence(.95)),
            FrontlineIdentity(2, None, Confidence(.3)),
        )))
        state = rt._observe(settle_main=True)
        self.assertEqual(rt.roster_servant_ids[:3], ("504500", "304800", None))
        self.assertEqual([servant.servant_id for servant in state.servants], ["504500", "304800", None])
        self.assertEqual({skill.target_ally for skill in rt.decider.decide(state).servant_skills}, {2})
        self.assertEqual(rt._semantic_roster(), (None,) * 6)
        self.assertEqual(catalog.return_value.detect.call_args_list, [call(image), call(image, slots=(2,))])

        rt.servant_recognizer.detect.return_value = (
            FrontlineIdentity(1, "504500", Confidence(.95)),
            FrontlineIdentity(2, "304800", Confidence(.95)),
        )
        rt._observe(settle_main=True)
        rt.servant_recognizer.detect.assert_called_with(image, ("504500", "304800", None))
        self.assertEqual(catalog.return_value.detect.call_count, 2)

    @patch("battle.runtime.runtime.time.sleep")
    @patch("battle.runtime.runtime.StatusCatalogRecognizer")
    def test_portrait_recovery_limits_attempts_per_slot_even_after_failure(self, catalog, _sleep):
        rt = runtime()
        image = NS(ndim=3)
        rt.controller.post_screencap.return_value.wait.return_value.get.return_value = image
        catalog.return_value.detect.side_effect = (
            catalog_report((1, "504500"), (2, "100100")),
            ValueError("portrait catalog unavailable"),
            catalog_report((2, "100100")),
            catalog_report((1, "304800")),
        )
        rt._observe(settle_main=True)
        rt.servant_recognizer = NS(detect=Mock(return_value=(
            FrontlineIdentity(1, "504500", Confidence(.95)),
            FrontlineIdentity(2, None, Confidence(.3)),
        )))
        for _ in range(3):
            state = rt._observe(settle_main=True)
            self.assertEqual(rt.roster_servant_ids[:3], ("504500", None, None))
            self.assertEqual([(s.servant_slot, s.skill_index)
                              for s in rt.decider.decide(state).servant_skills], AVAILABLE)
        rt.servant_recognizer.detect.return_value = ()
        rt._observe(settle_main=True)
        self.assertEqual(rt.roster_servant_ids[:3], ("304800", None, None))
        self.assertEqual(catalog.return_value.detect.call_args_list, [
            call(image), call(image, slots=(2,)), call(image, slots=(2,)), call(image, slots=(1,)),
        ])

    @patch("battle.runtime.runtime.time.sleep")
    @patch("battle.runtime.runtime.StatusCatalogRecognizer")
    def test_original_roster_is_preserved_when_a_frontline_replacement_is_found(self, catalog, _sleep):
        roster = ("504500", "100100", None, "503900", None, None)
        rt = runtime(roster=roster)
        image = NS(ndim=3)
        rt.controller.post_screencap.return_value.wait.return_value.get.return_value = image
        rt.servant_recognizer = NS(detect=Mock(return_value=(
            FrontlineIdentity(1, "504500", Confidence(.95)),
            FrontlineIdentity(2, "100100", Confidence(.95)),
        )))
        rt._observe(settle_main=True)
        catalog.assert_not_called()
        rt.servant_recognizer.detect.return_value = (
            FrontlineIdentity(1, "504500", Confidence(.95)),
            FrontlineIdentity(2, None, Confidence(.3)),
        )
        catalog.return_value.detect.return_value = catalog_report((2, "304800"))
        state = rt._observe(settle_main=True)
        catalog.return_value.detect.assert_called_once_with(image, slots=(2,))
        self.assertEqual(rt.roster_servant_ids[:3], ("504500", "304800", None))
        self.assertIn("304800", rt.candidate_servant_ids)
        self.assertEqual(rt._semantic_roster(), roster)
        self.assertFalse(rt._actor_matches(ActorBinding(1, "100100"), 2))
        self.assertEqual([(s.servant_slot, s.skill_index)
                          for s in rt.decider.decide(state).servant_skills if s.servant_slot == 2],
                         [(2, 2), (2, 3)])

    @patch("battle.runtime.runtime.time.sleep")
    @patch("battle.runtime.runtime.StatusCatalogRecognizer")
    def test_startup_portrait_failure_falls_back_and_is_not_retried(self, catalog, _sleep):
        for failure in ("exception", "no_match"):
            with self.subTest(failure=failure):
                catalog.reset_mock(side_effect=True)
                catalog.return_value.detect.side_effect = (
                    ValueError("no usable portrait assets") if failure == "exception" else None
                )
                catalog.return_value.detect.return_value = NS(elapsed_seconds=.5, slots=(
                    NS(slot=1, servant_id=None), NS(slot=2, servant_id=None), NS(slot=3, servant_id=None),
                ))
                rt = runtime()
                rt.controller.post_screencap.return_value.wait.return_value.get.return_value = NS(ndim=3)
                for _ in range(2):
                    state = rt._observe(settle_main=True)
                    self.assertEqual(rt.roster_servant_ids, (None,) * 6)
                    self.assertEqual([(s.servant_slot, s.skill_index)
                                      for s in rt.decider.decide(state).servant_skills], AVAILABLE)
                catalog.assert_called_once_with()

    @patch("battle.runtime.runtime.time.sleep")
    @patch("battle.runtime.runtime.StatusCatalogRecognizer")
    def test_starting_in_card_selection_waits_for_main_battle_to_search(self, catalog, _sleep):
        rt = runtime()
        rt.controller.post_screencap.return_value.wait.return_value.get.return_value = NS(ndim=3)
        rt.ctx.run_task = Mock(return_value=NS(status=NS(succeeded=True)))
        catalog.return_value.detect.return_value = NS(elapsed_seconds=.5, slots=())
        with patch.object(perception, "_detect_scene", return_value=(Scene.COMMAND_SELECTION, 1.0)), \
                patch.object(perception.ComposedCardRecognizer, "default") as composer:
            composer.return_value.detect_all.return_value = ((), ())
            self.assertIs(rt._observe(settle_main=True).scene, Scene.COMMAND_SELECTION)
        catalog.assert_not_called()
        self.assertIs(rt._observe(settle_main=True).scene, Scene.MAIN_BATTLE)
        catalog.assert_called_once_with()

    def test_auto_skill_targets_leftmost_confirmed_attack_np_then_leftmost_frontline(self):
        for frontline, expected in (
            (("100100", "504500", "304800"), 1),
            (("504500", "304800", "100100"), 2),
            (("2300600", "504500", "100100"), 3),
            (("504500", "9999999", "503900"), 1),
            ((None, "504500", "503900"), 2),
        ):
            with self.subTest(frontline=frontline), patch("battle.runtime.runtime.time.sleep"):
                rt = runtime(roster=(*frontline, None, None, None))
                rt.servant_recognizer = NS(detect=Mock(return_value=tuple(
                    NS(slot=slot, servant_id=sid, confidence=Confidence(.98),
                       margin=.2, matched_template="portrait")
                    for slot, sid in enumerate(frontline, 1)
                )))
                state = rt._observe()
                action = rt.decider.decide(state)
                self.assertEqual({skill.target_ally for skill in action.servant_skills}, {expected})
                self.assertTrue(rt._execute_skills(action))
                for args in rt._run_special_skill_pipeline.call_args_list:
                    self.assertEqual(args.args[0], {
                        f"战斗_技能目标子屏_选从者{slot}": {} if slot == expected else {"enabled": False}
                        for slot in (1, 2, 3)
                    })

    def test_fixed_target_is_kept_until_the_plan_ends(self):
        planned_skill = ServantSkillAction(2, 2, target_ally=3)
        rt = runtime(plan=BattlePlan((TurnPlan(servant_skills=(planned_skill,)),)))
        self.assertEqual(rt.decider.decide(rt._observe()).servant_skills, (planned_skill,))
        rt._turn_index = 1
        action = rt.decider.decide(rt._observe(), turn_index=1)
        self.assertEqual({skill.target_ally for skill in action.servant_skills}, {1})

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
