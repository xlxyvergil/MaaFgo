"""Chaldea 队伍身份回归：候补槽位和别名合并不连接设备。"""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from formation_test_support import battle_action as action


def members(*servant_ids):
    return [{"svtId": servant_id} if servant_id is not None else None
            for servant_id in servant_ids]


def snapshot(*servant_ids, statuses=None):
    statuses = statuses or ["identified"] * len(servant_ids)
    return NS(slots=tuple(NS(servant_id=servant_id, status=status)
                          for servant_id, status in zip(servant_ids, statuses)))


class ChaldeaRosterTests(unittest.TestCase):
    def test_three_front_and_three_backup_members_keep_six_slots(self):
        share = {"team": {
            "onFieldSvts": members(505300, 2500900, 402700),
            "backupSvts": members(505600, 1002000, 100100),
        }}
        self.assertEqual(action._roster_from_share(share),
                         ("505300", "2500900", "402700", "505600", "1002000", "100100"))

    def test_short_front_does_not_shift_backup_slots(self):
        share = {"team": {
            "onFieldSvts": members(505300),
            "backupSvts": members(505600, None, 100100),
        }}
        self.assertEqual(action._roster_from_share(share),
                         ("505300", None, None, "505600", None, "100100"))

    def test_empty_or_missing_front_keeps_backup_in_slots_four_to_six(self):
        for front in ({}, {"onFieldSvts": []}, {"onFieldSvts": None}):
            with self.subTest(front=front):
                share = {"team": {**front, "backupSvts": members(505600, 1002000)}}
                self.assertEqual(action._roster_from_share(share),
                                 (None, None, None, "505600", "1002000", None))

    def test_each_member_list_is_truncated_to_three_slots(self):
        share = {"team": {
            "onFieldSvts": members(505300, 2500900, 402700, 100100),
            "backupSvts": members(505600, 1002000, 100100, 402700),
        }}
        self.assertEqual(action._roster_from_share(share),
                         ("505300", "2500900", "402700", "505600", "1002000", "100100"))

    def test_absent_or_invalid_lists_have_no_roster_evidence(self):
        for share in ({}, {"team": {}}, {"team": None},
                      {"team": {"onFieldSvts": {}, "backupSvts": "invalid"}}):
            with self.subTest(share=share):
                self.assertIsNone(action._roster_from_share(share))

    def test_valid_empty_list_preserves_six_unknown_slots(self):
        for team in ({"onFieldSvts": []}, {"backupSvts": []}):
            with self.subTest(team=team):
                self.assertEqual(action._roster_from_share({"team": team}), (None,) * 6)

    def test_share_normalizes_known_aliases_and_keeps_unknown_positive_ids(self):
        share = {"team": {
            "onFieldSvts": members(505700, " 01002100 ", 99999999),
            "backupSvts": members("505700", 1002100, "00099999999"),
        }}
        self.assertEqual(action._roster_from_share(share),
                         ("505600", "1002000", "99999999", "505600", "1002000", "99999999"))

    def test_invalid_member_ids_remain_unknown(self):
        share = {"team": {
            "onFieldSvts": members(True, 0, -1),
            "backupSvts": members("", "invalid", 1.5),
        }}
        self.assertEqual(action._roster_from_share(share), (None,) * 6)


class MergeRosterTests(unittest.TestCase):
    def setUp(self):
        warning = patch.object(action.mfaalog, "warning")
        self.warning = warning.start()
        self.addCleanup(warning.stop)

    def test_canonical_visual_and_share_alias_are_the_same_identity(self):
        initial = snapshot("505600", "1002000", None, None, None, None)
        planned = ("505700", "1002100", None, None, None, None)
        self.assertEqual(action._merge_rosters(initial, planned),
                         ("505600", "1002000", None, None, None, None))
        self.warning.assert_not_called()

    def test_visual_alias_and_canonical_share_are_the_same_identity(self):
        initial = snapshot("505700", "1002100", None, None, None, None)
        planned = ("505600", "1002000", None, None, None, None)
        self.assertEqual(action._merge_rosters(initial, planned),
                         ("505600", "1002000", None, None, None, None))
        self.warning.assert_not_called()

    def test_real_conflict_still_disables_only_the_conflicting_slot(self):
        initial = snapshot("505700", "100100", None, None, None, None)
        planned = ("505600", "402700", None, "1002100", None, None)
        self.assertEqual(action._merge_rosters(initial, planned),
                         ("505600", None, None, "1002000", None, None))
        self.warning.assert_called_once()
        self.assertIn("slot 2", self.warning.call_args.args[0])

    def test_missing_visual_preserves_front_and_backup_identities(self):
        planned = ("505300", "2500900", "402700", "505700", "1002100", "99999999")
        self.assertEqual(action._merge_rosters(None, planned),
                         ("505300", "2500900", "402700", "505600", "1002000", "99999999"))

    def test_missing_share_still_normalizes_visual_identities(self):
        initial = snapshot("505700", "1002100", "99999999", None, None, None)
        self.assertEqual(action._merge_rosters(initial, None),
                         ("505600", "1002000", "99999999", None, None, None))

    def test_unidentified_visual_slot_does_not_override_share_identity(self):
        initial = snapshot("100100", None, None, None, None, None,
                           statuses=["conflict", "unknown", "empty", "unknown", "unknown", "unknown"])
        planned = ("505700", None, None, "1002100", None, None)
        self.assertEqual(action._merge_rosters(initial, planned),
                         ("505600", None, None, "1002000", None, None))
        self.warning.assert_not_called()

    def test_no_identity_evidence_keeps_six_unknown_slots(self):
        self.assertEqual(action._merge_rosters(None, None), (None,) * 6)


class BattleRosterEntryTests(unittest.TestCase):
    def test_chaldea_share_passes_all_six_normalized_slots_to_runtime(self):
        share = {"team": {
            "onFieldSvts": members(505700, 2500900, 402700),
            "backupSvts": members(1002100, 100100, 99999999),
        }, "actions": []}
        ctx = NS(tasker=NS(stopping=False, controller=object()))
        argv = NS(custom_action_param=json.dumps({"chaldea_share": share}))
        with patch.object(action, "context_session", return_value=(123, None)), \
                patch.object(action, "AutoBattleRuntime") as runtime, \
                patch.object(action.mfaalog, "info"):
            runtime.return_value.run.return_value = NS(ok=True, reason="victory", turns=1)
            result = action.AutoBattleAction().run(ctx, argv)
        self.assertTrue(result.success)
        self.assertEqual(runtime.call_args.kwargs["roster_servant_ids"],
                         ("505600", "2500900", "402700", "1002000", "100100", "99999999"))
        self.assertIsNone(runtime.call_args.kwargs["initial_formation"])


if __name__ == "__main__":
    unittest.main()
