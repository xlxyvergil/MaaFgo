"""Verify the production CE completion and optional storage orchestration."""

from dataclasses import replace
import unittest
from unittest.mock import Mock

from tests.unit.test_expball_runtime import CATALOG, CardObservation, CeGameModel, FakeContext, runtime, texts


PROTECT = "搓丸子-保护成品"
LOCK = "搓丸子-锁定当前礼装"
MARK = "搓丸子-标记当前候选"
LEAVE_MARK = "搓丸子-退出标记模式"
SELECT_STORAGE = "搓丸子整理-选择候选"
STORE = "搓丸子-存入成品"


class FinishGameModel(CeGameModel):
    """Model only physical UI changes, while the production runner makes the decisions."""

    def __init__(self, runner, before, after, *, selection_clears, duplicate=False):
        old_ball = replace(after, identity="99", locked=True, marked=True)
        unfinished = replace(before, locked=True, marked=True)
        finished = [old_ball, unfinished, after]
        if duplicate:
            finished.append(replace(after, locked=True, marked=True))
        material = CardObservation(before.identity, before.rarity, 1, {1: 10, 2: 15}[before.rarity], False, False)
        super().__init__(runner, materials=[material], after=after, finished=finished)
        self.base = before
        self.result_box = tuple(self.BOXES[2])
        self.selection_clears = selection_clears
        self.stored_item = None
        # Begin after the selected bottom card has reached the final batch.
        runner.select_base = Mock(return_value=before)
        runner.current_base = self.current_base

    def current_base(self):
        if self.result_slot_is_empty():
            raise AssertionError("The game cleared the selected bottom card")
        return self.base

    def result_slot_is_empty(self):
        return self.selection_clears and bool(self.called("搓丸子-提交强化"))

    def recognition(self, name, **kwargs):
        if name == "搓丸子-当前底卡为空":
            return self.result_slot_is_empty()
        if name == "搓丸子-底卡选定复查":
            return not self.result_slot_is_empty()
        if name == "搓丸子-强化页所需量子":
            return texts("300")
        return super().recognition(name, **kwargs)

    def pipeline(self, entry, terminals, override=None):
        if entry == PROTECT:
            self.finished[self.result_box] = replace(self.base, locked=True)
        if entry == STORE:
            page, box = self.storage_selections[-1]
            self.stored_item = self.pages["finished"][page].pop(box)
        return super().pipeline(entry, terminals, override)


class ExpballFinishedBallTests(unittest.TestCase):
    def setup_final_batch(self, rarity, maximum, *, selection_clears, store, duplicate=False):
        before = CardObservation(str(rarity), rarity, maximum - 1, maximum, False, False)
        after = replace(before, current=maximum)
        config = {"ball_star": rarity, "store_balls": store, "max_material_batches": 1, "max_ce_qp": 300}
        runner = runtime.ExpballRunner(FakeContext(), config, catalog=CATALOG)
        game = FinishGameModel(runner, before, after, selection_clears=selection_clears, duplicate=duplicate)
        return runner, game, replace(after, locked=True, marked=True)

    def test_one_and_two_star_completions_lock_mark_and_optionally_store(self):
        for rarity, maximum in ((1, 50), (2, 55)):
            for selection_clears in (False, True):
                for store in (False, True):
                    with self.subTest(rarity=rarity, selection_clears=selection_clears, store=store):
                        runner, game, protected = self.setup_final_batch(
                            rarity, maximum, selection_clears=selection_clears, store=store)
                        self.assertEqual(runner.make_one(), "completed")
                        self.assertEqual(runner.completed, 1)
                        self.assertEqual(runner.stored, int(store))
                        self.assertEqual(len(game.called("搓丸子-提交强化")), 1)
                        self.assertEqual(runner.ce_submitted_batches, 1)
                        self.assertEqual(runner.ce_committed_qp, 300)
                        lock = LOCK if selection_clears else PROTECT
                        other_lock = PROTECT if selection_clears else LOCK
                        steps = [entry for entry, _, _ in game.events]
                        self.assertEqual(steps.count(lock), 1)
                        self.assertNotIn(other_lock, steps)
                        self.assertEqual(steps.count(MARK), 1)
                        self.assertLess(steps.index(lock), steps.index(MARK))
                        self.assertEqual(game.called(MARK)[0][2], 0)
                        mark = game.called(MARK)[0][1]["搓丸子-给候选补标记"]["action"]["param"]["target"]
                        self.assertEqual(tuple(mark), game.result_box)
                        if store:
                            self.assertNotIn(game.result_box, game.finished)
                            self.assertEqual(game.stored_item, protected)
                            self.assertEqual(game.storage_selections, [(0, game.result_box)])
                            self.assertLess(steps.index(LEAVE_MARK), steps.index(SELECT_STORAGE))
                            self.assertEqual(game.called(STORE)[0][2], 1)
                            self.assertEqual(steps.count(STORE), 1)
                        else:
                            self.assertEqual(game.finished[game.result_box], protected)
                            self.assertIsNone(game.stored_item)
                            self.assertNotIn(SELECT_STORAGE, steps)
                            self.assertNotIn(STORE, steps)

    def test_same_identity_historical_finished_card_prevents_ambiguous_storage(self):
        for rarity, maximum in ((1, 50), (2, 55)):
            with self.subTest(rarity=rarity):
                runner, game, protected = self.setup_final_batch(
                    rarity, maximum, selection_clears=True, store=True, duplicate=True)
                with self.assertRaisesRegex(runtime.ExpballFailure, "无法唯一确认本轮成品"):
                    runner.make_one()
                self.assertEqual(game.finished[game.result_box], protected)
                self.assertEqual(runner.completed, 1)
                self.assertEqual(runner.stored, 0)
                self.assertFalse(game.storage_selections)
                self.assertFalse(game.called(STORE))

    def test_cleared_slot_does_not_treat_lower_break_limit_as_finished(self):
        for rarity, maximum in ((1, 40), (2, 45)):
            with self.subTest(rarity=rarity):
                runner, game, _ = self.setup_final_batch(rarity, maximum, selection_clears=True, store=True)
                with self.assertRaisesRegex(runtime.ExpballFailure, "无法唯一重定位本轮成品"):
                    runner.make_one()
                self.assertEqual(runner.completed, 0)
                self.assertEqual(runner.stored, 0)
                self.assertFalse(game.called(LOCK))
                self.assertFalse(game.called(MARK))
                self.assertFalse(game.called(STORE))


if __name__ == "__main__":
    unittest.main()
