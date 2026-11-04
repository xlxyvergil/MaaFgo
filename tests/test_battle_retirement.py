"""Natural departures preserve initial member identities and never kill by click."""
import copy
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]

from battle.core.enums import PrimitiveKind, Scene
from battle.core.models import (ActorBinding, BattleAction, BattlePlan, BattleState,
                                Confidence, PlannedCard, RetireAction, ServantSkillAction,
                                ServantState, SkillState, TurnPlan)
from battle.core.policy import StrategyProfile
from battle.core.validator import skip_unusable_servant_skills, validate_main_action
from battle.data.chaldea_converter import convert_chaldea_actions_to_battle_plan
from battle.runtime.runtime import AutoBattleRuntime
from battle.runtime.special_skills import SpecialSkillDispatcher
from battle.core.special_skills import SpecialSkillChoices, SpecialSkillChoiceError
from battle_editor.storage import BattleStorage
from battle_editor.validation import validate_battle
from chaldea.bbc_formatter import chaldea_to_bbc
from auto_battle_action import _build_plan_from_share

IDS = (100100, 100200, 100300, 100400, 100500, 100600)


def binding(initial=0, sid=None):
    return {"initialSlot": initial, "svtId": IDS[initial] if sid is None else sid}


def retire(slot=0, initial=0):
    return {"type": "retire", "svt": slot, "actor": binding(initial)}


def skill(slot=0, initial=3):
    return {"type": "skill", "svt": slot, "skill": 0, "actor": binding(initial)}


def battle(actions, ids=IDS):
    members = [{"svtId": sid} if sid else None for sid in ids]
    return {"team": {"onFieldSvts": members[:3], "backupSvts": members[3:]}, "actions": actions}


def codes(data):
    return {issue["code"] for issue in validate_battle(data)["issues"] if issue["severity"] == "error"}


def state(sid, confidence=1, scene=Scene.MAIN_BATTLE):
    return BattleState(scene, Confidence(1), (), (), (), servants=(
        ServantState(1, (SkillState(True, Confidence(1)),) * 3,
                     Confidence(confidence), str(sid) if sid else None),))


def frontline_state(sids, confidences=(1, 1, 1)):
    return BattleState(Scene.MAIN_BATTLE, Confidence(1), (), (), (), servants=tuple(
        ServantState(slot, (SkillState(True, Confidence(1)),) * 3,
                     Confidence(confidence), str(sid) if sid else None)
        for slot, (sid, confidence) in enumerate(zip(sids, confidences), start=1)))


class RetirementContractTests(unittest.TestCase):
    def test_newcomer_skill_and_card_validate_and_roundtrip_without_mutation(self):
        data = battle([retire(), skill(), {"type": "attack", "attacks": [
            {"svt": 0, "isTD": True, "card": -1, "actor": binding(3)}]}])
        before = copy.deepcopy(data)
        self.assertEqual(codes(data), set())
        with tempfile.TemporaryDirectory() as folder:
            storage = BattleStorage(folder)
            storage.save_battle("retirement.json", data, create=True)
            self.assertEqual(storage.read_battle("retirement.json")["data"], data)
        self.assertEqual(data, before)
        turn = convert_chaldea_actions_to_battle_plan(data["actions"]).turns[0]
        self.assertIsInstance(turn.skill_sequence[0], RetireAction)
        self.assertEqual(turn.servant_skills[0].actor, ActorBinding(3, str(IDS[3])))
        self.assertEqual(turn.card_sequence[0].actor, ActorBinding(3, str(IDS[3])))

    def test_retired_old_skills_and_np_do_not_become_newcomer_actions(self):
        for action in [skill(initial=0), {"type": "attack", "attacks": [
                {"svt": 0, "isTD": True, "card": -1, "actor": binding(0)}]}]:
            data = battle([retire(), action])
            self.assertIn("retired_actor", codes(data))
            with self.assertRaisesRegex(ValueError, "已退场"):
                convert_chaldea_actions_to_battle_plan(data["actions"])

    def test_instance_binding_distinguishes_equal_ids_and_rejects_wrong_initial_slot(self):
        data = battle([skill(initial=1)], ids=(100100, 100100, *IDS[2:]))
        data["actions"][0]["actor"]["svtId"] = 100100
        self.assertIn("actor_position_mismatch", codes(data))
        for actor in [None, {}, {"initialSlot": True, "svtId": 100100}, binding(sid=0)]:
            data = battle([{"type": "retire", "svt": 0, "actor": actor}])
            self.assertIn("invalid_actor_binding", codes(data))

    def test_no_backup_unknown_backup_and_identical_replacement_are_blocked(self):
        self.assertIn("no_retirement_replacement", codes(battle([retire()], (*IDS[:3], None, None, None))))
        data = battle([retire()])
        data["team"]["backupSvts"][0] = {"supportType": "friend"}
        self.assertIn("unknown_retirement_replacement", codes(data))
        self.assertIn("ambiguous_retirement_replacement", codes(battle([retire()], (*IDS[:3], IDS[0], *IDS[4:]))))

    def test_empty_backup_is_skipped_and_dead_members_cannot_reenter(self):
        data = battle([retire(), skill(initial=4)], (*IDS[:3], None, IDS[4], IDS[5]))
        self.assertEqual(codes(data), set())
        turn = convert_chaldea_actions_to_battle_plan(
            data["actions"], initial_servant_ids=tuple(str(sid) if sid else None for sid in (*IDS[:3], None, IDS[4], IDS[5]))).turns[0]
        self.assertEqual(turn.servant_skills[0].actor.initial_slot, 4)
        self.assertEqual(_build_plan_from_share(data).turns[0].servant_skills[0].actor.initial_slot, 4)
        data = battle([retire(), retire(initial=3), retire(initial=4), retire(initial=5)])
        self.assertIn("no_retirement_replacement", codes(data))
        data = battle([retire(), {"type": "skill", "svt": None, "skill": 2}])
        data["team"]["mysticCode"] = {"mysticCodeId": 210}
        data["delegate"] = {"replaceMemberIndexes": [[1, 2]]}
        self.assertIn("retired_swap", codes(data))

    def test_order_change_and_retirement_share_initial_instance_order(self):
        data = battle([{"type": "skill", "svt": None, "skill": 2},
                       retire(initial=3), skill(initial=0)])
        data["team"]["mysticCode"] = {"mysticCodeId": 210}
        data["delegate"] = {"replaceMemberIndexes": [[0, 0]]}
        self.assertEqual(codes(data), set())
        turn = convert_chaldea_actions_to_battle_plan(data["actions"], data["delegate"], 210).turns[0]
        self.assertEqual(turn.servant_skills[0].actor.initial_slot, 0)

    def test_legacy_actions_keep_current_slot_semantics_and_marker_only_tail_is_kept(self):
        data = battle([retire(), {"type": "skill", "svt": 0, "skill": 1}])
        self.assertEqual(codes(data), set())
        self.assertIsNone(convert_chaldea_actions_to_battle_plan(data["actions"]).turns[0].servant_skills[0].actor)
        self.assertIsInstance(convert_chaldea_actions_to_battle_plan([retire()]).turns[0].skill_sequence[0], RetireAction)

    def test_bbc_export_cannot_silently_discard_departure(self):
        with self.assertRaisesRegex(ValueError, "退场.*原生"):
            chaldea_to_bbc(battle([retire()]))


class RetirementRuntimeTests(unittest.TestCase):
    def runtime(self, observations=None):
        runtime = AutoBattleRuntime(NS(tasker=NS(stopping=False)), object(), NS(plan=None),
                                    StrategyProfile(), roster_servant_ids=tuple(map(str, IDS)))
        runtime.executor = Mock()
        runtime._wait_order_change_settled = Mock(return_value=True)
        runtime._observe = Mock(side_effect=observations or [state(IDS[3])])
        runtime._execute_skill_cast = Mock(side_effect=lambda _label, cast, *_args, **_kwargs: cast() is not False)
        return runtime

    def test_natural_replacement_waits_then_casts_newcomer_without_retirement_click(self):
        runtime = self.runtime()
        new = ServantSkillAction(1, 1, actor=ActorBinding(3, str(IDS[3])))
        departure = RetireAction(1, ActorBinding(0, str(IDS[0])))
        self.assertTrue(runtime._execute_skills(BattleAction(None, (), (new,), skill_sequence=(departure, new))))
        runtime._wait_order_change_settled.assert_called_once()
        runtime.executor.cast_servant_skill.assert_called_once_with(1, 1)
        self.assertEqual(runtime.executor.mock_calls, [unittest.mock.call.cast_servant_skill(1, 1)])
        self.assertEqual(runtime._member_order, [3, 1, 2, 4, 5, 0])
        self.assertEqual(runtime.roster_servant_ids, (str(IDS[3]), None, None, str(IDS[4]), str(IDS[5]), str(IDS[0])))
        self.assertNotIn(str(IDS[0]), runtime.candidate_servant_ids)
        self.assertIsNone(runtime.servant_recognizer)

    def test_early_observation_does_not_double_remove_reserve(self):
        runtime = self.runtime()
        runtime._update_frontline((NS(slot=1, servant_id=str(IDS[3])), NS(slot=2, servant_id=str(IDS[1])), NS(slot=3, servant_id=str(IDS[2]))))
        self.assertTrue(runtime._execute_retirement(RetireAction(1, ActorBinding(0, str(IDS[0])))))
        self.assertEqual(runtime.roster_servant_ids[3:], (str(IDS[4]), str(IDS[5]), str(IDS[0])))

    def test_simultaneous_departure_cannot_restore_old_other_front_identity(self):
        runtime = self.runtime([frontline_state((IDS[3], IDS[4], IDS[2]))])
        old_other = ServantSkillAction(2, 1, actor=ActorBinding(1, str(IDS[1])))
        departure = RetireAction(1, ActorBinding(0, str(IDS[0])))
        self.assertFalse(runtime._execute_skills(BattleAction(None, (), (old_other,),
                                                            skill_sequence=(departure, old_other))))
        self.assertEqual(runtime.roster_servant_ids[:3], (str(IDS[3]), str(IDS[4]), str(IDS[2])))
        self.assertEqual(runtime.executor.mock_calls, [])

    def test_unknown_other_front_cannot_authorize_an_old_bound_skill(self):
        for sid, confidence in [(None, 1), (IDS[1], .1)]:
            with self.subTest(sid=sid, confidence=confidence):
                runtime = self.runtime([frontline_state((IDS[3], sid, IDS[2]), (1, confidence, 1))])
                old_other = ServantSkillAction(2, 1, actor=ActorBinding(1, str(IDS[1])))
                departure = RetireAction(1, ActorBinding(0, str(IDS[0])))
                self.assertFalse(runtime._execute_skills(BattleAction(None, (), (old_other,),
                                                                    skill_sequence=(departure, old_other))))
                self.assertIsNone(runtime.roster_servant_ids[1])
                self.assertEqual(runtime.executor.mock_calls, [])

    def test_two_markers_confirm_already_observed_simultaneous_replacements_once(self):
        observed = frontline_state((IDS[3], IDS[4], IDS[2]))
        runtime = self.runtime([observed, observed])
        first = RetireAction(1, ActorBinding(0, str(IDS[0])))
        second = RetireAction(2, ActorBinding(1, str(IDS[1])))
        newcomer = ServantSkillAction(2, 1, actor=ActorBinding(4, str(IDS[4])))
        self.assertTrue(runtime._execute_skills(BattleAction(None, (), (newcomer,),
                                                           skill_sequence=(first, second, newcomer))))
        self.assertEqual(runtime._member_order, [3, 4, 2, 5, 0, 1])
        self.assertEqual(runtime._retired_instances, {0, 1})
        self.assertEqual(runtime.roster_servant_ids, tuple(str(IDS[i]) for i in runtime._member_order))
        self.assertEqual(runtime.executor.mock_calls, [unittest.mock.call.cast_servant_skill(2, 1)])

    def test_unchanged_old_frame_or_low_confidence_cannot_authorize_new_skill(self):
        for observation in [state(IDS[0]), state(IDS[3], confidence=.1), state(None), state(IDS[3], scene=Scene.UNKNOWN)]:
            with self.subTest(observation=observation):
                runtime = self.runtime([observation])
                runtime._wait_order_change_settled.side_effect = [True, False]
                departure = RetireAction(1, ActorBinding(0, str(IDS[0])))
                self.assertFalse(runtime._execute_retirement(departure))
                self.assertEqual(runtime._member_order, list(range(6)))
                self.assertEqual(runtime.executor.mock_calls, [])

    def test_cancelled_wait_does_not_commit_or_click(self):
        runtime = self.runtime()
        runtime.ctx.tasker.stopping = True
        self.assertFalse(runtime._execute_retirement(RetireAction(1, ActorBinding(0, str(IDS[0])))))
        self.assertEqual(runtime._retired_instances, set())
        runtime._observe.assert_not_called()
        self.assertEqual(runtime.executor.mock_calls, [])

    def test_cancellation_during_confirmation_does_not_commit(self):
        runtime = self.runtime()
        def cancelled_observation():
            runtime.ctx.tasker.stopping = True
            return state(IDS[3])
        runtime._observe.side_effect = cancelled_observation
        self.assertFalse(runtime._execute_retirement(RetireAction(1, ActorBinding(0, str(IDS[0])))))
        self.assertEqual(runtime._member_order, list(range(6)))
        self.assertEqual(runtime._retired_instances, set())

    def test_old_actor_is_rejected_before_skill_or_card_click(self):
        runtime = self.runtime()
        old = ServantSkillAction(1, 1, actor=ActorBinding(0, str(IDS[0])))
        self.assertTrue(runtime._execute_retirement(RetireAction(1, old.actor)))
        self.assertFalse(runtime._execute_skills(BattleAction(None, (), (old,))))
        runtime.executor.cast_servant_skill.assert_not_called()
        for initial, expected in [(0, False), (3, True)]:
            runtime.decider.plan = BattlePlan(turns=(TurnPlan(card_sequence=(
                PlannedCard(PrimitiveKind.SELECT_NP, 1, actor=ActorBinding(initial, str(IDS[initial]))),)),))
            self.assertIs(runtime._check_card_actor_bindings(), expected)

    def test_malformed_retirement_is_rejected_by_safety_gate(self):
        before = BattleState(Scene.MAIN_BATTLE, Confidence(1), (), (), ())
        for departure in [RetireAction(0, ActorBinding(0, str(IDS[0]))), RetireAction(1, None)]:
            self.assertEqual(validate_main_action(BattleAction(None, (), skill_sequence=(departure,)),
                                                  before, StrategyProfile()).reason, "invalid_retirement")

    def test_retirement_is_a_pre_frame_skill_filter_boundary(self):
        new = ServantSkillAction(1, 1, actor=ActorBinding(3, str(IDS[3])))
        departure = RetireAction(1, ActorBinding(0, str(IDS[0])))
        action = BattleAction(None, (), (new,), skill_sequence=(departure, new))
        before = BattleState(Scene.MAIN_BATTLE, Confidence(1), (), (), ())
        kept, skipped = skip_unusable_servant_skills(action, before, StrategyProfile())
        self.assertEqual(kept, action)
        self.assertFalse(skipped)
        self.assertTrue(validate_main_action(kept, before, StrategyProfile()).ok)

    def test_special_choice_filter_simulates_incoming_identity(self):
        departure = RetireAction(1, ActorBinding(0, str(IDS[0])))
        special = ServantSkillAction(1, 2)
        dispatcher = SpecialSkillDispatcher(SpecialSkillChoices((2,)))
        turn = TurnPlan(servant_skills=(special,), skill_sequence=(departure, special))
        with self.assertRaises(SpecialSkillChoiceError):
            dispatcher.check_skipped(turn, BattleAction(None, ()), (str(IDS[0]), None, None, "1100900", None, None))


if __name__ == "__main__":
    unittest.main()
