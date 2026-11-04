"""Expball orchestration boundaries using a fake Context; no input is submitted."""
import copy
from dataclasses import replace
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent/custom"))
import expball_action as runtime
from expball_policy import CardObservation, ExpballConfigError


CATALOG = {"equips": [{"id": "20", "name": "保留礼装", "name_jp": "保留礼装JP", "rarity": 3,
                        "images": ["EquipFaces/list/f_200.png"]}],
           "servants": [{"id": "100", "name": "BB", "name_jp": "BB（日文）",
                         "images": ["servant_face/f_1000.png"]}]}
FRIEND_NODES = json.loads((Path(__file__).resolve().parents[2] /
                          "assets/resource/base/pipeline/友情召唤.json").read_text("utf-8"))
NETWORK_NODES = json.loads((Path(__file__).resolve().parents[2] /
                           "assets/resource/base/pipeline/搓丸子网络.json").read_text("utf-8"))
SERVANT_NODES = json.loads((Path(__file__).resolve().parents[2] /
                           "assets/resource/base/pipeline/搓丸子从者.json").read_text("utf-8"))
SERVANT_UP_NODES = json.loads((Path(__file__).resolve().parents[2] /
                              "assets/resource/base/pipeline/强化从者.json").read_text("utf-8"))
CLEANUP_NODES = json.loads((Path(__file__).resolve().parents[2] /
                           "assets/resource/base/pipeline/搓丸子整理.json").read_text("utf-8"))
SHARED_CLEANUP_NODES = json.loads((Path(__file__).resolve().parents[2] /
                                  "assets/resource/base/pipeline/整理礼物盒.json").read_text("utf-8"))
NETWORK_RETRY = "搓丸子网络-重试"
NETWORK_EXHAUSTED = "搓丸子网络-重试耗尽"


def target(count=1, mode="count"):
    return {"target_1_kind": "servant", "target_1_id": "100", "target_1_count": count,
            "target_1_mode": mode, "draw_fp": True}


def texts(*values):
    return NS(hit=True, filtered_results=[NS(text=s) for s in values])


def boxes(*values):
    return NS(hit=True, filtered_results=[NS(box=s) for s in values])


class FakeContext:
    def __init__(self):
        self.controller = NS(post_screencap=Mock(return_value=NS(wait=lambda: NS(get=lambda: NS(copy=lambda: "frame")))))
        self.tasker = NS(stopping=False, controller=self.controller)
        self.nodes = {}
        self.hits = {}
        self.cleared = []
        self.tasks = []
        self.next_terminal = []
        self.terminals_by_entry = {}
        self.task_succeeded = True
        self.stop_after_task = False

    def add(self, entry, terminal="done"):
        self.nodes[entry] = {"next": [terminal]}
        self.nodes.setdefault(terminal, {})

    def get_node_data(self, name): return copy.deepcopy(self.nodes.get(name))
    def clear_hit_count(self, name):
        self.cleared.append(name)
        self.hits[name] = 0
        return True
    def get_hit_count(self, name): return self.hits.get(name, 0)
    def run_task(self, entry, pipeline_override=None):
        self.tasks.append((entry, copy.deepcopy(pipeline_override or {})))
        for terminal in self.terminals_by_entry.get(entry, self.next_terminal):
            self.hits[terminal] = self.hits.get(terminal, 0) + 1
        if self.stop_after_task: self.tasker.stopping = True
        return NS(status=NS(succeeded=self.task_succeeded))
    def run_recognition(self, *args, **kwargs): raise AssertionError("test must supply recognition result")


class CeGameModel:
    """A small external state model at the Pipeline/observation boundary.

    The real runtime decides eligibility, builds and rechecks the material plan,
    handles growth and uniquely relocates a finished item. This model supplies
    observations and records submissions; it never reimplements those decisions.
    """
    BOXES = ([60, 183, 128, 132], [193, 183, 128, 132], [326, 183, 128, 132],
             [459, 183, 128, 132], [592, 183, 128, 132], [725, 183, 128, 132])

    def __init__(self, runner, *, bases=(), materials=(), after=None, material_changes=None,
                 finished=(), no_base_terminal=False, unverified_lock=False, unknown_finished_level=False,
                 base_pages=None, material_pages=None, finished_pages=None, bottom_known=True,
                 storage_selected=0, storage_selection_delta=1):
        self.runner = runner
        self.bases = {tuple(self.BOXES[i]): card for i, card in enumerate(bases)}
        self.materials = {tuple(self.BOXES[i]): card for i, card in enumerate(materials)}
        self.finished = {tuple(self.BOXES[i]): card for i, card in enumerate(finished)}
        def pages(items, default):
            return [default] if items is None else [
                {tuple(self.BOXES[i]): card for i, card in enumerate(cards)} for cards in items]
        self.pages = {"bases": pages(base_pages, self.bases), "materials": pages(material_pages, self.materials),
                      "finished": pages(finished_pages, self.finished)}
        self.bottom_known = bottom_known
        self.page = 0
        self.after = after
        self.material_changes = material_changes or {}
        self.no_base_terminal = no_base_terminal
        self.unverified_lock = unverified_lock
        self.unknown_finished_level = unknown_finished_level
        self.phase = "bases"
        self.base = None
        self.opened = None
        self.chosen_base_box = None
        self.chosen_base_page = None
        self.selected_materials = []
        self.selected_material_pages = []
        self.storage_selections = []
        self.storage_selected = storage_selected
        self.storage_selection_delta = storage_selection_delta
        self.material_previews = {}
        self.events = []
        runner.task = Mock(side_effect=self.pipeline)
        runner.grid = lambda: iter([list(box) for box in self.BOXES])
        runner._has_card = self.has_card
        runner._preview = self.preview
        runner.current_base = lambda: self.base
        runner.card_detail = lambda kind: self.observations[self.opened]
        runner.close_card = lambda: None
        runner.reco = self.recognition
        runner.return_main = lambda: None
        runner.storage_capacity = lambda: True
        # Storage uses a different grid in production; keep this model's screen
        # geometry consistent while testing the runtime's all-page policy.
        runner.ctx.nodes["搓丸子-保管室切礼装"] = {"attach": {"grid": {
            "x": [box[0] for box in self.BOXES], "y": [183], "w": 128, "h": 132}}}

    @property
    def observations(self):
        pages = self.pages[self.phase]
        return pages[self.page] if self.page < len(pages) else {}

    def recognition(self, name, **kwargs):
        if name == "搓丸子整理-已选数量":
            return texts(f"{self.storage_selected}/1600")
        if name == "搓丸子-列表到底":
            return self.bottom_known and self.page >= len(self.pages[self.phase]) - 1
        return None

    def has_card(self, box):
        return tuple(box) in self.observations

    def preview(self, box, kind="底卡"):
        key = tuple(box)
        if self.phase == "finished" and self.unknown_finished_level:
            raise runtime.ExpballFailure("无法确认底卡等级")
        if self.phase == "materials":
            observation_key = self.page, key
            self.material_previews[observation_key] = self.material_previews.get(observation_key, 0) + 1
            if self.material_previews[observation_key] > 1 and key in self.material_changes:
                return self.material_changes[key]
        return self.observations[key]

    def pipeline(self, entry, terminals, override=None):
        self.events.append((entry, copy.deepcopy(override or {}), self.runner.completed))
        if entry == "搓丸子-制作一轮":
            if self.no_base_terminal:
                return "搓丸子-无底卡"
            self.phase = "finished" if self.base and self.base.complete else "bases"
            self.page = 0
            return "搓丸子-底卡候选就绪"
        if entry == "搓丸子-成品列表准备":
            self.phase, self.page = "finished", 0
            return "搓丸子-成品列表就绪"
        if entry == "搓丸子-下一页":
            self.page += 1
            return "搓丸子-翻页完成"
        if entry == "搓丸子-上一页":
            self.page -= 1
            if self.page < 0: raise AssertionError("scroll before first page")
            return "搓丸子-翻页完成"
        if entry == "搓丸子-选定底卡":
            self.chosen_base_box = tuple(override[entry]["action"]["param"]["target"])
            self.chosen_base_page = self.page
            self.base = self.observations[self.chosen_base_box]
            return "搓丸子-底卡选定"
        if entry == "搓丸子-打开素材列表":
            self.phase = "materials"
            self.page = 0
            return "搓丸子-素材候选就绪"
        if entry == "搓丸子-选择素材":
            self.selected_materials.append(tuple(override[entry]["action"]["param"]["target"]))
            self.selected_material_pages.append(self.page)
            return "搓丸子-选择素材完成"
        if entry == "搓丸子-确认素材":
            return "搓丸子-素材已选择"
        if entry == "搓丸子-提交强化":
            if self.after is not None:
                self.base = self.after
            return "搓丸子-强化一批完成"
        if entry == "搓丸子-保护半成品":
            self.base = replace(self.base, locked=True)
            return "搓丸子-半成品保护完成"
        if entry == "搓丸子-保护成品":
            self.phase, self.page = "finished", 0
            return "搓丸子-成品待标记"
        if entry == "搓丸子-预览底卡":
            self.opened = tuple(override[entry]["action"]["param"]["target"])
            return "搓丸子-底卡详情就绪"
        if entry == "搓丸子-锁定当前礼装":
            if not self.unverified_lock:
                self.observations[self.opened] = replace(self.observations[self.opened], locked=True)
            return "搓丸子-半成品保护完成"
        if entry == "搓丸子-进入标记模式":
            return "搓丸子-标记模式就绪"
        if entry == "搓丸子-标记当前候选":
            key = tuple(override["搓丸子-给候选补标记"]["action"]["param"]["target"])
            self.observations[key] = replace(self.observations[key], marked=True)
            return "搓丸子-候选标记完成"
        if entry == "搓丸子-退出标记模式":
            return "搓丸子-标记模式已退出"
        if entry == "搓丸子整理-导航保管室":
            return "搓丸子整理-保管室就绪"
        if entry == "搓丸子-保管室切礼装":
            self.phase, self.page = "finished", 0
            return "搓丸子-保管室礼装就绪"
        if entry == "搓丸子整理-选择候选":
            self.storage_selections.append((self.page, tuple(override[entry]["action"]["param"]["target"])))
            self.storage_selected += self.storage_selection_delta
            return "搓丸子整理-候选已选择"
        if entry == "搓丸子-存入成品":
            self.storage_selected = 0
            return "搓丸子-存储一批完成"
        raise AssertionError(f"unexpected Pipeline request: {entry}")

    def called(self, entry):
        return [event for event in self.events if event[0] == entry]


class ExperienceGameModel:
    """Supply page contents and detail text without deciding their eligibility."""
    def __init__(self, runner, pages, *, bottom_known=True, initial_selected=0,
                 selection_delta=1):
        self.pages, self.bottom_known, self.page = pages, bottom_known, 0
        self.events, self.selections, self.count_reads = [], [], []
        self.count_node = None
        self.selected, self.selection_delta = initial_selected, selection_delta
        runner.ctx.nodes["搓丸子整理-预览候选"] = {"attach": {"grid_centers": [[120, 248]]}}
        runner.frame = Mock(return_value="experience-screen")
        runner.task = Mock(side_effect=self.pipeline)
        runner.reco = Mock(side_effect=self.recognition)
        runner.return_main = Mock()
        runner.storage_capacity = Mock(return_value=10)

    @property
    def card(self):
        return self.pages[self.page] if self.page < len(self.pages) else None

    def recognition(self, name, **kwargs):
        if name in ("搓丸子整理-已选数量", "搓丸子整理-贩卖已选数量"):
            self.count_reads.append(name)
            return texts(f"{self.selected}/102") if name == self.count_node else None
        if name == "搓丸子整理-列表到底":
            return self.bottom_known and self.page >= len(self.pages) - 1
        if name == "搓丸子整理-列表就绪": return True
        if self.card is None: return None
        if name == "搓丸子整理-四星经验值候选": return self.card.star == 4
        if name == "搓丸子整理-五星经验值候选": return self.card.star == 5
        if name == "搓丸子整理-候选名称": return texts(self.card.name)
        if name == "搓丸子整理-候选已锁定": return self.card.locked
        if name == "搓丸子整理-候选未锁定": return not self.card.locked
        return None

    def pipeline(self, entry, terminals, override=None):
        self.events.append((entry, self.page))
        if entry == "搓丸子整理-导航保管室":
            self.count_node = "搓丸子整理-已选数量"
        elif entry == "搓丸子整理-导航贩卖":
            self.count_node = "搓丸子整理-贩卖已选数量"
        if entry == "搓丸子整理-下一页":
            self.page += 1
            return "搓丸子整理-翻页完成"
        if entry == "搓丸子整理-选择候选":
            self.selections.append(self.page)
            self.selected += self.selection_delta
        if entry in ("搓丸子整理-提交存储", "搓丸子整理-提交贩卖"):
            self.selected = 0
        endpoints = {
            "搓丸子整理-导航保管室": "搓丸子整理-保管室就绪",
            "搓丸子整理-保管室切从者": "搓丸子整理-保管室从者就绪",
            "搓丸子整理-导航贩卖": "整理礼物盒-导航到灵基变还完成",
            "搓丸子整理-筛选经验卡": "整理礼物盒-已在灵基图标大小检测",
            "搓丸子整理-预览候选": "搓丸子整理-详情就绪",
            "搓丸子整理-关闭详情": "搓丸子整理-列表就绪",
            "搓丸子整理-选择候选": "搓丸子整理-候选已选择",
            "搓丸子整理-提交存储": "搓丸子整理-存储已完成",
            "搓丸子整理-提交贩卖": "搓丸子整理-贩卖已完成",
        }
        if entry not in endpoints: raise AssertionError(f"unexpected Pipeline request: {entry}")
        return endpoints[entry]


class ExpballRuntimeTests(unittest.TestCase):
    def runner(self, attach=None):
        context = FakeContext()
        context.nodes.update(copy.deepcopy(FRIEND_NODES))
        context.nodes.update(copy.deepcopy(NETWORK_NODES))
        context.nodes.update(copy.deepcopy(SERVANT_NODES))
        return runtime.ExpballRunner(context, attach or {}, catalog=CATALOG), context

    def test_new_runner_clears_previous_network_attempt_and_exhaustion_marks(self):
        context = FakeContext()
        context.hits.update({NETWORK_RETRY: 3, NETWORK_EXHAUSTED: 1, "previous_business_end": 7})
        runner = runtime.ExpballRunner(context, {}, catalog=CATALOG)
        self.assertEqual(context.hits[NETWORK_RETRY], 0)
        self.assertEqual(context.hits[NETWORK_EXHAUSTED], 0)
        self.assertEqual(context.hits["previous_business_end"], 7)
        self.assertEqual(context.cleared, [NETWORK_RETRY, NETWORK_EXHAUSTED])
        runner.check_stop()
        self.assertEqual(context.tasks, [])
        context.controller.post_screencap.assert_not_called()

    def test_network_exhaustion_wins_over_task_status_business_end_and_user_stop(self):
        for succeeded in (False, True):
            for stopping in (False, True):
                for business_end in (False, True):
                    with self.subTest(succeeded=succeeded, stopping=stopping, business_end=business_end):
                        runner, c = self.runner()
                        c.nodes["submit"] = {"next": ["[JumpBack]" + NETWORK_RETRY,
                                                     NETWORK_EXHAUSTED, "done"]}
                        c.nodes["done"] = {}
                        c.next_terminal = [NETWORK_EXHAUSTED] + (["done"] if business_end else [])
                        c.task_succeeded, c.stop_after_task = succeeded, stopping
                        with self.assertRaisesRegex(runtime.ExpballFailure, "网络重试.*用尽") as failure:
                            runner.task("submit", ["done"])
                        self.assertIs(type(failure.exception), runtime.ExpballFailure)
                        self.assertEqual(len(c.tasks), 1)
                        # A later attempt in this runner must stop before clearing the
                        # exhaustion mark or resubmitting the consuming operation.
                        with self.assertRaisesRegex(runtime.ExpballFailure, "网络重试.*用尽"):
                            runner.task("submit", ["done"])
                        self.assertEqual(len(c.tasks), 1)
                        self.assertEqual(c.hits[NETWORK_EXHAUSTED], 1)
                        c.controller.post_screencap.assert_not_called()

    def test_network_retry_requires_explicit_business_terminal(self):
        for business_end in (False, True):
            with self.subTest(business_end=business_end):
                runner, c = self.runner()
                c.nodes["waiting"] = {"next": ["[JumpBack]" + NETWORK_RETRY,
                                              NETWORK_EXHAUSTED, "done"]}
                c.nodes["done"] = {}
                c.next_terminal = [NETWORK_RETRY] + (["done"] if business_end else [])
                if business_end:
                    self.assertEqual(runner.task("waiting", ["done"]), "done")
                else:
                    with self.assertRaisesRegex(runtime.ExpballFailure, "状态不明确"):
                        runner.task("waiting", ["done"])
                self.assertEqual(len(c.tasks), 1)
                overrides = c.tasks[0][1]
                self.assertEqual(overrides[NETWORK_RETRY]["on_error"], [])
                self.assertEqual(overrides[NETWORK_EXHAUSTED]["on_error"], [])
                self.assertEqual(c.hits[NETWORK_EXHAUSTED], 0)

    def test_card_presence_requires_level_evidence_after_empty_probe_misses(self):
        for empty, level, expected in ((True, True, False), (False, True, True),
                                        (False, False, False)):
            with self.subTest(empty=empty, level=level):
                runner, context = self.runner()
                context.nodes["搓丸子-候选等级标志"] = {
                    "recognition": {"param": {"roi": [74, 190, 117, 38]}}}
                runner.frame = Mock(return_value="same-frame")
                values = {"搓丸子-列表标志": True, "搓丸子-候选为空": empty,
                          "搓丸子-候选等级标志": level}
                runner.reco = Mock(side_effect=lambda name, **kw: values[name])
                box = [74, 190, 117, 128]
                self.assertEqual(runner._has_card(box), expected)
                names = ["搓丸子-列表标志", "搓丸子-候选为空"]
                if not empty:
                    names.append("搓丸子-候选等级标志")
                self.assertEqual([call.args[0] for call in runner.reco.call_args_list],
                                 names)
                for call in runner.reco.call_args_list:
                    self.assertEqual(call.kwargs["image"], "same-frame")
                probe = names[-1]
                expected_roi = box if empty else [74, 190, 117, 38]
                self.assertEqual(runner.reco.call_args.kwargs["override"][probe]["recognition"]["param"]["roi"], expected_roi)
        runner, _ = self.runner()
        runner.frame = Mock(return_value="unknown-page")
        runner.reco = Mock(return_value=None)
        with self.assertRaisesRegex(runtime.ExpballFailure, "列表身份"):
            runner._has_card([74, 190, 117, 128])
        self.assertEqual(runner.reco.call_count, 1)

    def test_missing_card_header_probe_stops_without_previewing(self):
        runner, context = self.runner()
        runner.frame = Mock(return_value="same-frame")
        runner.reco = Mock(side_effect=[True, None])
        with self.assertRaisesRegex(runtime.ExpballFailure, "缺少候选等级识别区域"):
            runner._has_card([207, 332, 117, 128])
        self.assertEqual(context.tasks, [])

    def test_cancelled_base_is_relocated_once_without_material_submission(self):
        for always_cancel in (False, True):
            with self.subTest(always_cancel=always_cancel):
                runner, _ = self.runner()
                base = CardObservation("20", 1, 1, 10, False, False)
                game = CeGameModel(runner, bases=[base])
                selections = []

                def select(entry, terminals, override=None):
                    terminal = game.pipeline(entry, terminals, override)
                    if entry == "搓丸子-选定底卡":
                        selections.append(override[entry]["action"]["param"]["target"])
                        if len(selections) == 1 or always_cancel:
                            return "搓丸子-底卡已取消"
                    return terminal

                runner.task.side_effect = select
                if always_cancel:
                    with self.assertRaisesRegex(runtime.ExpballFailure, "连续取消选中"):
                        runner.select_base()
                else:
                    self.assertEqual(runner.select_base(), base)
                self.assertEqual(len(selections), 2)
                self.assertEqual(sum(entry == "搓丸子-制作一轮" for entry, _, _ in game.events), 2)
                self.assertFalse(any(entry in ("搓丸子-打开素材列表", "搓丸子-提交强化")
                                     for entry, _, _ in game.events))

    def test_already_selected_base_closes_list_instead_of_tapping_remove(self):
        runner, _ = self.runner()
        base = CardObservation("20", 1, 1, 10, False, False)
        game = CeGameModel(runner, bases=[base])
        runner.reco = lambda name, **kwargs: name == "搓丸子-候选为当前底卡"

        def select(entry, terminals, override=None):
            if entry == "搓丸子-复用当前底卡":
                game.events.append((entry, {}, 0))
                return "搓丸子-底卡选定"
            return game.pipeline(entry, terminals, override)

        runner.task.side_effect = select
        self.assertEqual(runner.select_base(), base)
        entries = [entry for entry, _, _ in game.events]
        self.assertIn("搓丸子-复用当前底卡", entries)
        self.assertNotIn("搓丸子-选定底卡", entries)
        self.assertNotIn("搓丸子-提交强化", entries)

    def test_rejects_hundred_with_target_before_input_or_capture(self):
        context = FakeContext()
        with self.assertRaises(ExpballConfigError):
            runtime.ExpballRunner(context, {**target(), "draw_batch": 100}, catalog=CATALOG)
        context.controller.post_screencap.assert_not_called()
        self.assertEqual(context.tasks, [])

    def test_unknown_terminal_is_failure_even_when_task_succeeded(self):
        runner, c = self.runner()
        c.add("entry", "done")
        c.hits["done"] = 8
        with self.assertRaisesRegex(runtime.ExpballFailure, "状态不明确"):
            runner.task("entry", ["done"])
        self.assertEqual(c.hits["done"], 0)

    def test_multiple_terminals_or_framework_failure_are_rejected(self):
        for failed_framework, terminals in ((False, ["one", "two"]), (True, ["one"])):
            with self.subTest(failed_framework=failed_framework):
                runner, c = self.runner()
                c.add("entry", "one")
                c.next_terminal = terminals
                c.task_succeeded = not failed_framework
                with self.assertRaises(runtime.ExpballFailure): runner.task("entry", ["one", "two"])

    def test_missing_node_and_user_stop_happen_before_task(self):
        runner, c = self.runner()
        with self.assertRaisesRegex(runtime.ExpballFailure, "缺少"):
            runner.task("missing", ["done"])
        self.assertEqual(c.tasks, [])
        c.tasker.stopping = True
        with self.assertRaises(runtime.ExpballStopped): runner.task("anything", ["done"])
        c.controller.post_screencap.assert_not_called()

    def test_cross_round_counts_reset_and_local_overrides_do_not_leak(self):
        runner, c = self.runner()
        c.add("entry", "old")
        c.nodes["done"] = {}
        before = copy.deepcopy(c.nodes)
        c.hits.update(entry=4, done=4)
        c.next_terminal = ["done"]
        self.assertEqual(runner.task("entry", ["done"], {"entry": {"next": ["done"]}}), "done")
        self.assertEqual(c.nodes, before)
        self.assertEqual(c.hits["done"], 1)
        self.assertIn("entry", c.cleared)
        c.next_terminal = []
        with self.assertRaises(runtime.ExpballFailure):
            runner.task("entry", ["done"], {"entry": {"next": ["done"]}})

    def test_stop_after_nested_task_is_not_business_success(self):
        runner, c = self.runner()
        c.add("entry", "done")
        c.next_terminal, c.stop_after_task = ["done"], True
        with self.assertRaisesRegex(runtime.ExpballStopped, "用户已停止"):
            runner.task("entry", ["done"])
        self.assertEqual(c.hits[NETWORK_EXHAUSTED], 0)
        with self.assertRaises(runtime.ExpballStopped):
            runner.task("entry", ["done"])
        self.assertEqual(len(c.tasks), 1)

    def test_unknown_balance_never_submits_summon(self):
        for value in (None, texts("??"), texts("100", "200"), texts("余额2000")):
            with self.subTest(value=value):
                runner, _ = self.runner({"draw_fp": True})
                runner.task = Mock(return_value="友情召唤-已到友情池")
                runner.reco = Mock(side_effect=lambda name, **kw: None if name == "友情召唤-免费十连可用" else value)
                with self.assertRaises(runtime.ExpballFailure): runner.summon_once()
                self.assertEqual([call.args[0] for call in runner.task.call_args_list], ["友情召唤-导航到友情池"])

    def test_budget_preserves_required_reserve_before_paid_draw(self):
        runner, _ = self.runner({"draw_fp": True, "friend_points_reserve": 1000})
        runner.task = Mock(return_value="友情召唤-已到友情池")
        runner.reco = Mock(side_effect=lambda name, **kw: None if name == "友情召唤-免费十连可用" else texts("2,500"))
        self.assertEqual(runner.summon_once(), "budget_reached")
        self.assertEqual(runner.batches, 0)
        self.assertEqual(runner.task.call_count, 1)

    def test_free_ten_at_zero_balance_does_not_spend_reserve(self):
        runner, c = self.runner({"draw_fp": True, "draw_batch": 100})
        before = copy.deepcopy(c.nodes)
        runner.task = Mock(side_effect=["友情召唤-已到友情池", "友情召唤-结果就绪", "友情召唤-离开结果完成"])
        runner.lock_targets = Mock()
        runner.reco = Mock(side_effect=lambda name, **kw: NS(hit=True) if name == "友情召唤-免费十连可用" else texts("0"))
        self.assertEqual(runner.summon_once(), "drawn")
        summon_call = runner.task.call_args_list[1]
        override = summon_call.args[2] if len(summon_call.args) >= 3 else summon_call.kwargs.get("override", {})
        configured = c.nodes["友情召唤-导航到友情池"]["attach"]["free_10_override"]
        for name, patch in configured.items():
            self.assertEqual(override[name], patch)
        for name in ("友情召唤-确认使用友情点", "友情召唤-抽取数量确认"):
            self.assertNotIn(name, override)
        self.assertEqual(override["友情召唤-点击抽取"]["action"]["param"]["target"],
                         c.nodes["友情召唤-点击抽取"]["action"]["param"]["target"])
        self.assertEqual(override["友情召唤-点击抽取"]["recognition"]["param"]["all_of"],
                         ["友情召唤-免费十连可用"])
        for name in ("友情召唤-已验证免费十连授权", "友情召唤-免费确认安全后提交"):
            self.assertFalse(c.nodes[name]["enabled"])
            self.assertTrue(override[name]["enabled"])
            self.assertEqual(c.nodes[name]["max_hit"], 1)
        self.assertEqual([call.args[0] for call in runner.reco.call_args_list], ["友情召唤-免费十连可用"])
        self.assertEqual(runner.batches, 1)
        self.assertEqual(c.nodes, before)

    def test_free_ten_missing_required_local_configuration_never_starts_draw(self):
        required = (None, "友情召唤-点击抽取", "友情召唤-等待抽取确认",
                    "友情召唤-已验证免费十连授权", "友情召唤-免费确认安全后提交")
        for missing in required:
            with self.subTest(missing=missing):
                runner, c = self.runner({"draw_fp": True, "draw_batch": 100})
                attach = c.nodes["友情召唤-导航到友情池"]["attach"]
                if missing is None:
                    del attach["free_10_override"]
                else:
                    del attach["free_10_override"][missing]
                runner.task = Mock(return_value="友情召唤-已到友情池")
                runner.reco = Mock(return_value=NS(hit=True))
                with self.assertRaisesRegex(runtime.ExpballFailure, "免费"):
                    runner.summon_once()
                self.assertEqual([call.args[0] for call in runner.task.call_args_list],
                                 ["友情召唤-导航到友情池"])
                self.assertEqual(runner.batches, 0)

    def test_paid_draw_does_not_load_missing_free_configuration_or_enable_free_submission(self):
        for batch in (10, 100):
            with self.subTest(batch=batch):
                runner, c = self.runner({"draw_fp": True, "draw_batch": batch})
                del c.nodes["友情召唤-导航到友情池"]["attach"]["free_10_override"]
                runner.task = Mock(side_effect=["友情召唤-已到友情池", "友情召唤-结果就绪", "友情召唤-离开结果完成"])
                runner.lock_targets = Mock()
                runner.reco = Mock(side_effect=lambda name, **kw: None if name == "友情召唤-免费十连可用"
                                   else texts("100,000"))
                self.assertEqual(runner.summon_once(), "drawn")
                paid = runner.task.call_args_list[1].args[2]
                for name in ("友情召唤-已验证免费十连授权", "友情召唤-免费确认安全后提交"):
                    self.assertFalse(paid.get(name, {}).get("enabled", False))
                self.assertNotIn("友情召唤-等待抽取确认", paid)
                if batch == 100:
                    configured = c.nodes["友情召唤-导航到友情池"]["attach"]["batch_100_override"]
                    for name, patch in configured.items():
                        self.assertEqual(paid[name], patch)

    def test_free_ten_rejects_wrong_button_authority_or_currency_check_order_before_draw(self):
        invalid = ("ordinary_button_guard", "hundred_target", "disabled_authority", "disabled_submit",
                   "missing_currency_check", "missing_authority", "currency_check_after_authority")
        for change in invalid:
            with self.subTest(change=change):
                runner, c = self.runner({"draw_fp": True, "draw_batch": 100})
                free = c.nodes["友情召唤-导航到友情池"]["attach"]["free_10_override"]
                waits = free["友情召唤-等待抽取确认"]["next"]
                authority, forbidden = "友情召唤-已验证免费十连授权", "友情召唤-确认出现圣晶石或呼符"
                if change == "ordinary_button_guard":
                    free["友情召唤-点击抽取"]["recognition"]["param"]["all_of"] = ["友情召唤-友情池页面"]
                elif change == "hundred_target":
                    free["友情召唤-点击抽取"]["action"]["param"]["target"] = [793, 513, 174, 20]
                elif change == "disabled_authority":
                    free[authority]["enabled"] = False
                elif change == "disabled_submit":
                    free["友情召唤-免费确认安全后提交"]["enabled"] = False
                elif change == "missing_currency_check":
                    waits.remove(forbidden)
                elif change == "missing_authority":
                    waits.remove(authority)
                else:
                    waits.remove(forbidden)
                    waits.append(forbidden)
                runner.task = Mock(return_value="友情召唤-已到友情池")
                runner.reco = Mock(return_value=NS(hit=True))
                with self.assertRaisesRegex(runtime.ExpballFailure, "免费"):
                    runner.summon_once()
                self.assertEqual([call.args[0] for call in runner.task.call_args_list],
                                 ["友情召唤-导航到友情池"])
                self.assertEqual(runner.batches, 0)

    def test_hundred_draw_uses_both_pipeline_guards_without_mutating_source(self):
        runner, c = self.runner({"draw_fp": True, "draw_batch": 100})
        before = copy.deepcopy(c.nodes)
        override = runner._friend_overrides()
        configured = c.nodes["友情召唤-导航到友情池"]["attach"]["batch_100_override"]
        for name in ("友情召唤-点击抽取", "友情召唤-确认使用友情点", "友情召唤-抽取数量确认"):
            self.assertEqual(override[name], configured[name])
        patterns = override["友情召唤-确认使用友情点"]["recognition"]["param"]["expected"]
        self.assertTrue(any(re.search(p, "フレンドポイントを20,000pt使用して100回召喚します。") for p in patterns))
        self.assertFalse(any(re.search(p, "フレンドポイントを2,000pt使用して10回召喚します。") for p in patterns))
        self.assertEqual(c.nodes, before)

    def test_missing_hundred_draw_guard_stops_before_navigation_or_capture(self):
        for missing in ("友情召唤-点击抽取", "友情召唤-确认使用友情点", "友情召唤-抽取数量确认"):
            with self.subTest(missing=missing):
                runner, c = self.runner({"draw_fp": True, "draw_batch": 100})
                del c.nodes["友情召唤-导航到友情池"]["attach"]["batch_100_override"][missing]
                with self.assertRaisesRegex(runtime.ExpballFailure, "100抽"):
                    runner.summon_once()
                self.assertEqual(c.tasks, [])
                c.controller.post_screencap.assert_not_called()

    def test_free_ten_then_paid_hundred_restores_each_batch_guards(self):
        runner, c = self.runner({"draw_fp": True, "draw_batch": 100})
        before = copy.deepcopy(c.nodes)
        runner.task = Mock(side_effect=["友情召唤-已到友情池", "友情召唤-结果就绪", "友情召唤-离开结果完成"] * 2)
        runner.lock_targets = Mock()
        free = iter([True, False])
        runner.reco = Mock(side_effect=lambda name, **kw: (
            NS(hit=True) if next(free) else None) if name == "友情召唤-免费十连可用" else texts("100,000"))
        self.assertEqual(runner.summon_once(), "drawn")
        self.assertEqual(runner.summon_once(), "drawn")
        ten = runner.task.call_args_list[1].args[2]
        hundred = runner.task.call_args_list[4].args[2]
        configured_free = c.nodes["友情召唤-导航到友情池"]["attach"]["free_10_override"]
        configured = c.nodes["友情召唤-导航到友情池"]["attach"]["batch_100_override"]
        for name, patch in configured_free.items():
            self.assertEqual(ten[name], patch)
        for name in ("友情召唤-确认使用友情点", "友情召唤-抽取数量确认"):
            self.assertNotIn(name, ten)
            self.assertEqual(hundred[name], configured[name])
        for name in ("友情召唤-已验证免费十连授权", "友情召唤-免费确认安全后提交"):
            self.assertFalse(hundred.get(name, {}).get("enabled", False))
        self.assertNotIn("友情召唤-等待抽取确认", hundred)
        self.assertEqual(hundred["友情召唤-点击抽取"], configured["友情召唤-点击抽取"])
        self.assertEqual(runner.batches, 2)
        self.assertEqual(c.nodes, before)

    def test_quota_rechecked_per_candidate_and_name_is_exact(self):
        runner, _ = self.runner(target(count=1))
        runner.reco = Mock(return_value=boxes([100, 100, 80, 100], [300, 100, 80, 100]))
        runner.task = Mock(return_value="友情召唤-锁定完成")
        runner.lock_targets()
        self.assertEqual(runner.locked, 1)
        self.assertEqual(runner.targets[0].remaining, 0)
        self.assertEqual(runner.task.call_count, 1)
        override = runner.task.call_args.args[2]
        expected = override["友情召唤-候选名称"]["recognition"]["param"]["expected"]
        self.assertTrue(any(re.search(p, "BB") for p in expected))
        self.assertFalse(any(re.search(p, "BB·夏") for p in expected))

    def test_original_lock_is_not_counted_and_duplicate_boxes_are_skipped(self):
        runner, _ = self.runner(target(count=1))
        runner.reco = Mock(return_value=boxes([100, 100, 80, 100], [100, 100, 80, 100], [300, 100, 80, 100]))
        runner.task = Mock(side_effect=["友情召唤-原已锁定", "友情召唤-锁定完成"])
        runner.lock_targets()
        self.assertEqual(runner.task.call_count, 2)
        self.assertEqual(runner.targets[0].confirmed, 1)
        self.assertEqual(runner.locked, 1)

    def test_failed_lock_does_not_debit_quota(self):
        runner, _ = self.runner(target(count=1))
        runner.reco = Mock(return_value=boxes([100, 100, 80, 100]))
        runner.task = Mock(side_effect=runtime.ExpballFailure("identity guard"))
        with self.assertRaises(runtime.ExpballFailure): runner.lock_targets()
        self.assertEqual(runner.targets[0].remaining, 1)
        self.assertEqual(runner.locked, 0)

    def test_material_identity_must_exist_uniquely_in_catalog(self):
        runner, _ = self.runner()
        runner.frame = Mock(return_value="same-frame")
        def recognition(name, **kwargs):
            if name == "搓丸子-材料名称": return texts("未知礼装")
            return None
        runner.reco = Mock(side_effect=recognition)
        with self.assertRaisesRegex(runtime.ExpballFailure, "身份"):
            runner.card_detail("材料")
        self.assertEqual(runner.material_batches, 0)

    def test_material_ocr_unknown_level_or_conflicting_lock_is_rejected(self):
        for level, locked, unlocked in [("?", True, False), ("1/60", True, True), ("1/60", False, False)]:
            with self.subTest(level=level, locked=locked, unlocked=unlocked):
                runner, _ = self.runner()
                runner.frame = Mock(return_value="same-frame")
                def recognition(name, **kwargs):
                    if name == "搓丸子-材料名称": return texts("保留礼装")
                    if name == "搓丸子-材料等级": return texts(level)
                    if name == "搓丸子-详情已锁定": return NS(hit=True) if locked else None
                    if name == "搓丸子-详情未锁定": return NS(hit=True) if unlocked else None
                    return None
                runner.reco = Mock(side_effect=recognition)
                with self.assertRaises(runtime.ExpballFailure): runner.card_detail("材料")

    def test_no_base_terminal_never_selects_a_card(self):
        runner, _ = self.runner()
        world = CeGameModel(runner, no_base_terminal=True)
        self.assertEqual(runner.make_one(), "no_base")
        self.assertEqual([x[0] for x in world.events], ["搓丸子-制作一轮"])
        self.assertEqual(runner.completed, 0)

    def test_unknown_bottom_never_reports_no_base_or_materials_exhausted(self):
        for has_base in (False, True):
            with self.subTest(has_base=has_base):
                runner, _ = self.runner()
                bases = [CardObservation("10", 1, 1, 50, False, False)] if has_base else []
                world = CeGameModel(runner, bases=bases, bottom_known=False)
                with self.assertRaisesRegex(runtime.ExpballFailure, "12页上限"):
                    runner.make_one()
                self.assertFalse(world.called("搓丸子-提交强化"))
                self.assertFalse(world.called("搓丸子-保护半成品"))
                self.assertEqual(runner.completed, 0)

    def test_base_can_be_found_after_scrolling_past_protected_first_page(self):
        runner, _ = self.runner()
        base = CardObservation("10", 1, 1, 20, False, False)
        world = CeGameModel(runner, base_pages=[[replace(base, locked=True)], [base]])
        self.assertEqual(runner.make_one(), "materials_exhausted")
        self.assertEqual(world.chosen_base_page, 1)
        self.assertEqual(len(world.called("搓丸子-下一页")), 1)
        self.assertTrue(world.base.locked)

    def test_material_on_later_page_is_found_before_exhaustion(self):
        runner, _ = self.runner()
        base = CardObservation("10", 1, 1, 50, False, False)
        material = CardObservation("20", 2, 1, 55, False, False)
        complete = replace(base, current=50)
        world = CeGameModel(runner, bases=[base], material_pages=[[replace(material, locked=True)], [material]],
                            after=complete, finished=[complete])
        self.assertEqual(runner.make_one(), "completed")
        self.assertEqual(world.selected_material_pages, [1])
        self.assertEqual(len(world.called("搓丸子-提交强化")), 1)
        self.assertFalse(world.called("搓丸子-保护半成品"))

    def test_selected_base_must_match_initial_observation_before_materials(self):
        base = CardObservation("10", 1, 1, 50, False, False)
        for actual in (replace(base, identity="11"), replace(base, locked=True),
                       replace(base, marked=True), replace(base, current=2)):
            with self.subTest(actual=actual):
                runner, _ = self.runner()
                world = CeGameModel(runner, bases=[base])
                runner.current_base = Mock(return_value=actual)
                with self.assertRaises(runtime.ExpballFailure):
                    runner.make_one()
                self.assertFalse(world.called("搓丸子-打开素材列表"))
                self.assertFalse(world.called("搓丸子-提交强化"))

    def test_historical_lock_mark_and_upgraded_bases_are_left_untouched(self):
        runner, _ = self.runner()
        clean = CardObservation("10", 1, 1, 20, False, False)
        world = CeGameModel(runner, bases=[replace(clean, locked=True), replace(clean, marked=True),
                                          replace(clean, current=2)])
        self.assertEqual(runner.make_one(), "no_base")
        self.assertFalse(world.called("搓丸子-选定底卡"))
        self.assertFalse(world.called("搓丸子-提交强化"))
        self.assertEqual(runner.completed, 0)

    def test_fresh_base_is_chosen_after_protected_history_then_half_is_locked(self):
        runner, _ = self.runner()
        clean = CardObservation("10", 1, 1, 20, False, False)
        world = CeGameModel(runner, bases=[replace(clean, locked=True), replace(clean, marked=True),
                                          replace(clean, current=2), clean])
        self.assertEqual(runner.make_one(), "materials_exhausted")
        self.assertEqual(world.chosen_base_box, tuple(world.BOXES[3]))
        self.assertTrue(world.base.locked)
        self.assertFalse(world.called("搓丸子-提交强化"))

    def test_material_plan_rechecks_identity_before_any_commit(self):
        runner, _ = self.runner()
        base = CardObservation("10", 1, 1, 50, False, False)
        material = CardObservation("20", 2, 1, 55, False, False)
        world = CeGameModel(runner, bases=[base], materials=[material],
                            material_changes={tuple(CeGameModel.BOXES[0]): replace(material, identity="30")})
        with self.assertRaisesRegex(runtime.ExpballFailure, "素材列表发生变化"):
            runner.make_one()
        self.assertFalse(world.called("搓丸子-选择素材"))
        self.assertFalse(world.called("搓丸子-确认素材"))
        self.assertFalse(world.called("搓丸子-提交强化"))
        self.assertEqual(runner.completed, 0)

    def test_later_material_change_cancels_batch_after_only_selection(self):
        runner, _ = self.runner()
        base = CardObservation("10", 1, 1, 50, False, False)
        material = CardObservation("20", 2, 1, 55, False, False)
        second = replace(material, identity="30")
        world = CeGameModel(runner, bases=[base], materials=[material, second],
                            material_changes={tuple(CeGameModel.BOXES[1]): replace(second, locked=True)})
        with self.assertRaisesRegex(runtime.ExpballFailure, "素材列表发生变化"):
            runner.make_one()
        self.assertEqual(world.selected_materials, [tuple(world.BOXES[0])])
        self.assertFalse(world.called("搓丸子-确认素材"))
        self.assertFalse(world.called("搓丸子-提交强化"))

    def test_batch_growth_and_unique_finished_card_are_verified_before_counting(self):
        runner, _ = self.runner()
        base = CardObservation("10", 1, 1, 50, False, False)
        material = CardObservation("20", 2, 1, 55, False, False)
        complete = replace(base, current=50)
        world = CeGameModel(runner, bases=[replace(base, locked=True), base],
                            materials=[replace(material, locked=True), replace(material, marked=True),
                                       replace(material, current=2), replace(material, rarity=3), material],
                            after=complete,
                            # A previously protected same-ID finished card must be ignored.
                            finished=[replace(complete, locked=True, marked=True),
                                      replace(complete, identity="99"), complete])
        self.assertEqual(runner.make_one(), "completed")
        self.assertEqual(world.selected_materials, [tuple(world.BOXES[4])])
        self.assertEqual(len(world.called("搓丸子-提交强化")), 1)
        self.assertEqual(runner.material_batches, 1)
        self.assertEqual(runner.completed, 1)
        # Sorting moved the new finished item away from its original base slot.
        self.assertEqual(world.chosen_base_box, tuple(world.BOXES[1]))
        self.assertTrue(world.finished[tuple(world.BOXES[2])].locked)
        self.assertTrue(world.finished[tuple(world.BOXES[2])].marked)
        self.assertFalse(world.finished[tuple(world.BOXES[1])].marked)
        self.assertTrue(all(count == 0 for _, _, count in world.events))

    def test_selected_two_star_never_silently_falls_back_to_one_star(self):
        runner, _ = self.runner({"ball_star": 2})
        world = CeGameModel(runner, bases=[CardObservation("10", 1, 1, 50, False, False)])
        self.assertEqual(runner.make_one(), "no_base")
        self.assertFalse(world.called("搓丸子-选定底卡"))
        override = world.called("搓丸子-制作一轮")[0][1]
        template = override["搓丸子-底卡只选丸子星级"]["recognition"]["param"]["template"]
        self.assertIn("2星", template)

    def test_success_animation_without_growth_is_not_a_completed_ball(self):
        runner, _ = self.runner()
        base = CardObservation("10", 1, 1, 50, False, False)
        material = CardObservation("20", 2, 1, 55, False, False)
        world = CeGameModel(runner, bases=[base], materials=[material], after=base)
        with self.assertRaisesRegex(runtime.ExpballFailure, "未确认等级或上限增长"):
            runner.make_one()
        self.assertEqual(len(world.called("搓丸子-提交强化")), 1)
        self.assertEqual(runner.material_batches, 0)
        self.assertEqual(runner.completed, 0)

    def test_high_star_authorization_requires_actual_rechecked_high_star_materials(self):
        for rarity in (2, 3):
            with self.subTest(rarity=rarity):
                runner, _ = self.runner({"eat3": True})
                base = CardObservation("10", 1, 1, 50, False, False)
                complete = replace(base, current=50)
                material = CardObservation("20", rarity, 1, 55, False, False)
                world = CeGameModel(runner, bases=[base], materials=[material], after=complete,
                                    finished=[complete])
                self.assertEqual(runner.make_one(), "completed")
                self.assertEqual(world.material_previews[(0, tuple(world.BOXES[0]))], 2)
                confirm = world.called("搓丸子-确认素材")[0][1]
                submit = world.called("搓丸子-提交强化")[0][1]
                if rarity == 3:
                    self.assertEqual(confirm["搓丸子-高星素材决定确认"]["next"], ["搓丸子-高星素材决定授权确认"])
                    self.assertEqual(submit["搓丸子-高星材料确认"]["next"], ["搓丸子-高星材料授权确认"])
                else:
                    self.assertEqual(confirm, {})
                    self.assertEqual(submit, {})

    def test_two_unmarked_same_id_finished_cards_are_not_marked_by_guessing(self):
        runner, _ = self.runner()
        complete = CardObservation("10", 1, 50, 50, False, False)
        world = CeGameModel(runner, finished=[complete, complete])
        world.base = complete
        with self.assertRaisesRegex(runtime.ExpballFailure, "唯一重定位"):
            runner.finish_ball(complete)
        self.assertFalse(world.called("搓丸子-锁定当前礼装"))
        self.assertFalse(world.called("搓丸子-进入标记模式"))
        self.assertFalse(world.called("搓丸子-标记当前候选"))
        self.assertEqual(runner.completed, 0)

    def test_same_id_finished_items_across_pages_are_still_ambiguous(self):
        runner, _ = self.runner()
        complete = CardObservation("10", 1, 50, 50, False, False)
        world = CeGameModel(runner, finished_pages=[[complete], [complete]])
        world.base = complete
        with self.assertRaisesRegex(runtime.ExpballFailure, "唯一重定位"):
            runner.finish_ball(complete)
        self.assertEqual(len(world.called("搓丸子-下一页")), 1)
        self.assertFalse(world.called("搓丸子-锁定当前礼装"))
        self.assertFalse(world.called("搓丸子-标记当前候选"))

    def test_unique_finished_item_on_earlier_page_is_relocated_before_mark(self):
        runner, _ = self.runner()
        complete = CardObservation("10", 1, 50, 50, False, False)
        other = replace(complete, identity="99")
        world = CeGameModel(runner, finished_pages=[[complete], [other]])
        world.base = complete
        self.assertEqual(runner.finish_ball(complete), "completed")
        self.assertEqual(len(world.called("搓丸子-下一页")), 1)
        self.assertEqual(len(world.called("搓丸子-上一页")), 1)
        self.assertTrue(world.pages["finished"][0][tuple(world.BOXES[0])].marked)
        self.assertFalse(world.pages["finished"][1][tuple(world.BOXES[0])].marked)

    def test_storage_capacity_remaining_total_uses_available_count_directly(self):
        for observed, expected in (("102/1600", 102), ("0/1600", 0), ("1,600/1,600", 1600)):
            with self.subTest(observed=observed):
                runner, c = self.runner()
                c.nodes["搓丸子整理-保管室容量"] = {"attach": {"capacity_semantics": "remaining_total"}}
                runner.reco = Mock(return_value=texts(observed))
                self.assertEqual(runner.storage_capacity(), expected)

    def test_storage_capacity_used_total_and_legacy_default_remain_supported(self):
        for node in ({}, {"attach": {"capacity_semantics": "used_total"}}):
            with self.subTest(node=node):
                runner, c = self.runner()
                c.nodes["搓丸子整理-保管室容量"] = node
                runner.reco = Mock(return_value=texts("1498/1600"))
                self.assertEqual(runner.storage_capacity(), 102)

    def test_storage_capacity_unknown_semantics_fails_before_recognition(self):
        for semantics in ("unknown", "", None):
            with self.subTest(semantics=semantics):
                runner, c = self.runner()
                c.nodes["搓丸子整理-保管室容量"] = {"attach": {"capacity_semantics": semantics}}
                runner.reco = Mock(return_value=texts("102/1600"))
                with self.assertRaisesRegex(runtime.ExpballFailure, "容量语义"):
                    runner.storage_capacity()
                runner.reco.assert_not_called()
                self.assertEqual(c.tasks, [])

    def test_storage_capacity_unknown_invalid_or_conflicting_readings_fail(self):
        for readings in ((), ("空き不明",), ("1601/1600",), ("102/1600", "436/1600")):
            with self.subTest(readings=readings):
                runner, c = self.runner()
                c.nodes["搓丸子整理-保管室容量"] = {"attach": {"capacity_semantics": "remaining_total"}}
                runner.reco = Mock(return_value=texts(*readings))
                with self.assertRaisesRegex(runtime.ExpballFailure, "无法确认保管室容量"):
                    runner.storage_capacity()
                self.assertEqual(c.tasks, [])

    def test_selected_count_reads_fraction_first_number_and_accepts_agreement(self):
        for readings, expected in ((("0/102",), 0), (("1/102", "1/102"), 1)):
            with self.subTest(readings=readings):
                runner, _ = self.runner()
                runner.reco = Mock(return_value=texts(*readings))
                self.assertEqual(runner.selected_count(), expected)
                runner.reco.assert_called_once_with("搓丸子整理-已选数量")

    def test_selected_count_unknown_invalid_or_conflicting_readings_fail(self):
        for readings in ((), ("選択済み不明",), ("103/102",), ("0/102", "1/102")):
            with self.subTest(readings=readings):
                runner, c = self.runner()
                runner.reco = Mock(return_value=texts(*readings))
                with self.assertRaisesRegex(runtime.ExpballFailure, "无法确认已选对象总数"):
                    runner.selected_count()
                self.assertEqual(c.tasks, [])

    def test_storage_preselected_objects_stop_before_scanning_or_selection(self):
        runner, _ = self.runner()
        complete = CardObservation("10", 1, 50, 50, True, True)
        world = CeGameModel(runner, finished=[complete], storage_selected=1)
        runner._preview = Mock(side_effect=AssertionError("must not inspect preselected inventory"))
        with self.assertRaisesRegex(runtime.ExpballFailure, "已选对象总数不等于0"):
            runner.store_ball(complete)
        runner._preview.assert_not_called()
        self.assertFalse(world.storage_selections)
        self.assertFalse(world.called("搓丸子-存入成品"))
        self.assertEqual(runner.stored, 0)

    def test_storage_unexpected_selection_count_stops_before_submission(self):
        for delta in (0, 2):
            with self.subTest(delta=delta):
                runner, _ = self.runner()
                complete = CardObservation("10", 1, 50, 50, True, True)
                world = CeGameModel(runner, finished=[complete], storage_selection_delta=delta)
                with self.assertRaisesRegex(runtime.ExpballFailure, "已选对象总数不等于1"):
                    runner.store_ball(complete)
                self.assertEqual(len(world.storage_selections), 1)
                self.assertFalse(world.called("搓丸子-存入成品"))
                self.assertEqual(runner.stored, 0)

    def test_storage_checks_all_pages_for_same_id_before_any_selection(self):
        runner, _ = self.runner()
        complete = CardObservation("10", 1, 50, 50, True, True)
        world = CeGameModel(runner, finished_pages=[[complete], [complete]])
        with self.assertRaisesRegex(runtime.ExpballFailure, "无法唯一确认"):
            runner.store_ball(complete)
        self.assertFalse(world.storage_selections)
        self.assertFalse(world.called("搓丸子-存入成品"))
        self.assertEqual(runner.stored, 0)

    def test_storage_unique_previous_page_is_rechecked_and_selected(self):
        runner, _ = self.runner()
        complete = CardObservation("10", 1, 50, 50, True, True)
        other = replace(complete, identity="99")
        world = CeGameModel(runner, finished_pages=[[complete], [other]])
        runner.store_ball(complete)
        self.assertEqual(world.storage_selections, [(0, tuple(world.BOXES[0]))])
        self.assertEqual(len(world.called("搓丸子-上一页")), 1)
        self.assertEqual(runner.stored, 1)

    def test_storage_rechecks_protection_immediately_before_selection(self):
        runner, _ = self.runner()
        complete = CardObservation("10", 1, 50, 50, True, True)
        world = CeGameModel(runner, finished=[complete])
        runner._preview = Mock(side_effect=[complete, replace(complete, locked=False)])
        with self.assertRaisesRegex(runtime.ExpballFailure, "提交前成品身份或保护状态变化"):
            runner.store_ball(complete)
        self.assertFalse(world.storage_selections)
        self.assertFalse(world.called("搓丸子-存入成品"))
        self.assertEqual(runner.stored, 0)

    def test_experience_cleanup_finds_and_verifies_second_page_before_submission(self):
        for store in (False, True):
            with self.subTest(store=store):
                runner, _ = self.runner({"store4" if store else "sell4": True})
                world = ExperienceGameModel(runner, [None, NS(star=4, name="叡智の猛火", locked=False)])
                self.assertIs(runner.cleanup_experience_once(store=store), True)
                self.assertEqual(world.selections, [1])
                self.assertEqual(runner.stored if store else runner.sold, 1)
                submit = "搓丸子整理-提交存储" if store else "搓丸子整理-提交贩卖"
                self.assertIn((submit, 1), world.events)
                count_node = "搓丸子整理-已选数量" if store else "搓丸子整理-贩卖已选数量"
                self.assertEqual(world.count_reads, [count_node, count_node])
                if store:
                    switch = world.events.index(("搓丸子整理-保管室切从者", 0))
                    prepare = world.events.index(("搓丸子整理-筛选经验卡", 0))
                    self.assertLess(switch, prepare)

    def test_experience_sale_filter_override_is_local_and_never_installed_for_storage(self):
        entry = "搓丸子整理-筛选经验卡"
        search = "整理礼物盒-筛卖找经验值"
        for order in ((False, True), (True, False)):
            with self.subTest(order=order):
                runner, c = self.runner({"make_ball": False, "store4": True, "sell4": True})
                c.nodes[entry] = copy.deepcopy(CLEANUP_NODES[entry])
                c.nodes[search] = copy.deepcopy(SHARED_CLEANUP_NODES[search])
                for store in order:
                    world = ExperienceGameModel(runner, [None])
                    before = copy.deepcopy(c.nodes)
                    self.assertIs(runner.cleanup_experience_once(store=store), False)
                    calls = [call for call in runner.task.call_args_list if call.args[0] == entry]
                    self.assertEqual(len(calls), 1)
                    override = calls[0].args[2]
                    if store:
                        self.assertNotIn(search, override)
                    else:
                        self.assertEqual(override[search], CLEANUP_NODES[entry]["attach"]["sale_filter_override"][search])
                    self.assertEqual(override["整理礼物盒-筛卖选4星"]["next"], ["整理礼物盒-筛卖点4星"])
                    self.assertEqual(c.nodes, before)
                    self.assertFalse(world.selections)
                    self.assertEqual((runner.stored, runner.sold), (0, 0))

    def test_experience_filter_failure_never_previews_selects_or_submits(self):
        entry = "搓丸子整理-筛选经验卡"
        for store in (False, True):
            with self.subTest(store=store):
                route = "store" if store else "sell"
                runner, c = self.runner({"make_ball": False, f"{route}4": True})
                c.nodes[entry] = copy.deepcopy(CLEANUP_NODES[entry])
                world = ExperienceGameModel(runner, [NS(star=4, name="睿智的猛火", locked=False)])

                def fail_filter(name, terminals, override=None):
                    if name == entry:
                        raise runtime.ExpballFailure("经验值筛选未完成")
                    return world.pipeline(name, terminals, override)

                runner.task.side_effect = fail_filter
                with self.assertRaisesRegex(runtime.ExpballFailure, "经验值筛选未完成"):
                    runner.cleanup_experience_once(store=store)
                runner.reco.assert_not_called()
                self.assertFalse(world.selections)
                self.assertFalse(any(name in ("搓丸子整理-预览候选", "搓丸子整理-选择候选",
                                               "搓丸子整理-提交存储", "搓丸子整理-提交贩卖")
                                     for name, _page in world.events))
                self.assertEqual((runner.stored, runner.sold), (0, 0))

    def test_sale_filter_task_resets_reposition_and_swipe_limits_per_call(self):
        runner, c = self.runner({"make_ball": False, "sell4": True})
        c.nodes.update(copy.deepcopy(SHARED_CLEANUP_NODES))
        c.nodes.update(copy.deepcopy(CLEANUP_NODES))
        entry = "搓丸子整理-筛选经验卡"
        terminal = "整理礼物盒-已在灵基图标大小检测"
        reposition = "搓丸子整理-经验筛选滚动定位"
        swipe = "整理礼物盒-筛卖下滑找经验值"
        override = copy.deepcopy(CLEANUP_NODES[entry]["attach"]["sale_filter_override"])
        before = copy.deepcopy(c.nodes)
        c.next_terminal = [terminal]
        for _ in range(2):
            c.hits.update({reposition: 1, swipe: 10})
            c.cleared.clear()
            self.assertEqual(runner.task(entry, [terminal], override), terminal)
            self.assertIn(reposition, c.cleared)
            self.assertIn(swipe, c.cleared)
            self.assertEqual(c.hits[reposition], 0)
            self.assertEqual(c.hits[swipe], 0)
            self.assertEqual(c.nodes, before)

    def test_experience_cleanup_chinese_four_and_five_star_names_use_requested_route(self):
        for store in (False, True):
            for star, name in ((4, "睿智的猛火"), (5, "睿智的业火")):
                with self.subTest(store=store, star=star, name=name):
                    route = "store" if store else "sell"
                    runner, _ = self.runner({"make_ball": False, f"{route}{star}": True})
                    world = ExperienceGameModel(runner, [None, NS(star=star, name=name, locked=False)])
                    self.assertIs(runner.cleanup_experience_once(store=store), True)
                    self.assertEqual(world.selections, [1])
                    self.assertEqual((runner.stored, runner.sold), (1, 0) if store else (0, 1))
                    submit = "搓丸子整理-提交存储" if store else "搓丸子整理-提交贩卖"
                    other_submit = "搓丸子整理-提交贩卖" if store else "搓丸子整理-提交存储"
                    self.assertEqual([event for event in world.events if event[0] == submit], [(submit, 1)])
                    self.assertFalse(any(entry == other_submit for entry, _page in world.events))
                    count_node = "搓丸子整理-已选数量" if store else "搓丸子整理-贩卖已选数量"
                    self.assertEqual(world.count_reads, [count_node, count_node])
                    preview = world.events.index(("搓丸子整理-预览候选", 1))
                    close = world.events.index(("搓丸子整理-关闭详情", 1))
                    select = world.events.index(("搓丸子整理-选择候选", 1))
                    self.assertLess(preview, close)
                    self.assertLess(close, select)
                    self.assertLess(select, world.events.index((submit, 1)))

    def test_experience_cleanup_chinese_star_enabled_only_for_other_route_is_not_processed(self):
        for store in (False, True):
            for star, name in ((4, "睿智的猛火"), (5, "睿智的业火")):
                with self.subTest(store=store, star=star):
                    route, other_route = ("store", "sell") if store else ("sell", "store")
                    other_star = 5 if star == 4 else 4
                    runner, _ = self.runner({"make_ball": False, f"{route}{other_star}": True,
                                             f"{other_route}{star}": True})
                    world = ExperienceGameModel(runner, [NS(star=star, name=name, locked=False)])
                    self.assertIs(runner.cleanup_experience_once(store=store), False)
                    self.assertFalse(world.selections)
                    self.assertFalse(any(entry in ("搓丸子整理-预览候选", "搓丸子整理-选择候选",
                                                    "搓丸子整理-提交存储", "搓丸子整理-提交贩卖")
                                         for entry, _page in world.events))
                    self.assertEqual((runner.stored, runner.sold), (0, 0))

    def test_experience_cleanup_unknown_or_near_chinese_names_never_select_or_submit(self):
        for store in (False, True):
            for star, name, other_name in ((4, "睿智的猛火", "睿智的业火"),
                                           (5, "睿智的业火", "睿智的猛火")):
                for observed in ("", "未知经验值卡", name[:-1], "前缀" + name, name + "EX", other_name):
                    with self.subTest(store=store, star=star, observed=observed):
                        route = "store" if store else "sell"
                        runner, _ = self.runner({"make_ball": False, f"{route}{star}": True})
                        world = ExperienceGameModel(runner, [NS(star=star, name=observed, locked=False)])
                        self.assertIs(runner.cleanup_experience_once(store=store), False)
                        self.assertIn(("搓丸子整理-预览候选", 0), world.events)
                        self.assertIn(("搓丸子整理-关闭详情", 0), world.events)
                        self.assertFalse(world.selections)
                        self.assertFalse(any(entry in ("搓丸子整理-选择候选", "搓丸子整理-提交存储",
                                                        "搓丸子整理-提交贩卖")
                                             for entry, _page in world.events))
                        self.assertEqual((runner.stored, runner.sold), (0, 0))

    def test_experience_cleanup_locked_chinese_four_and_five_star_cards_are_preserved(self):
        for store in (False, True):
            for star, name in ((4, "睿智的猛火"), (5, "睿智的业火")):
                with self.subTest(store=store, star=star):
                    route = "store" if store else "sell"
                    runner, _ = self.runner({"make_ball": False, f"{route}{star}": True})
                    world = ExperienceGameModel(runner, [NS(star=star, name=name, locked=True)])
                    self.assertIs(runner.cleanup_experience_once(store=store), False)
                    self.assertIn(("搓丸子整理-关闭详情", 0), world.events)
                    self.assertFalse(world.selections)
                    self.assertFalse(any(entry in ("搓丸子整理-选择候选", "搓丸子整理-提交存储",
                                                    "搓丸子整理-提交贩卖")
                                         for entry, _page in world.events))
                    self.assertEqual((runner.stored, runner.sold), (0, 0))

    def test_experience_cleanup_preselected_objects_never_preview_select_or_submit(self):
        for store in (False, True):
            with self.subTest(store=store):
                runner, _ = self.runner({"store4" if store else "sell4": True})
                world = ExperienceGameModel(
                    runner, [NS(star=4, name="叡智の猛火", locked=False)], initial_selected=1)
                with self.assertRaisesRegex(runtime.ExpballFailure, "已选对象总数不等于0"):
                    runner.cleanup_experience_once(store=store)
                self.assertFalse(world.selections)
                self.assertFalse(any(name in ("搓丸子整理-预览候选", "搓丸子整理-提交存储", "搓丸子整理-提交贩卖")
                                     for name, _page in world.events))
                self.assertEqual(runner.stored + runner.sold, 0)

    def test_experience_cleanup_unexpected_selection_count_never_submits(self):
        for store in (False, True):
            for delta in (0, 2):
                with self.subTest(store=store, delta=delta):
                    runner, _ = self.runner({"store4" if store else "sell4": True})
                    world = ExperienceGameModel(
                        runner, [NS(star=4, name="叡智の猛火", locked=False)], selection_delta=delta)
                    with self.assertRaisesRegex(runtime.ExpballFailure, "已选对象总数不等于1"):
                        runner.cleanup_experience_once(store=store)
                    self.assertEqual(world.selections, [0])
                    self.assertFalse(any(name in ("搓丸子整理-提交存储", "搓丸子整理-提交贩卖")
                                         for name, _page in world.events))
                    self.assertEqual(runner.stored + runner.sold, 0)

    def test_experience_cleanup_unknown_bottom_never_reports_exhaustion(self):
        for store in (False, True):
            with self.subTest(store=store):
                runner, _ = self.runner({"store4" if store else "sell4": True})
                world = ExperienceGameModel(runner, [None], bottom_known=False)
                with self.assertRaisesRegex(runtime.ExpballFailure, "12页上限"):
                    runner.cleanup_experience_once(store=store)
                self.assertFalse(world.selections)
                self.assertEqual(runner.stored + runner.sold, 0)
                self.assertFalse(any(name in ("搓丸子整理-提交存储", "搓丸子整理-提交贩卖")
                                     for name, _page in world.events))

    def test_servant_upgrade_uses_boolean_business_result_and_clears_its_counters(self):
        for result in ("target_reached", "dogfood_empty", "no_material"):
            with self.subTest(result=result):
                runner, c = self.runner({"make_ball": False, "levelup": True,
                                         "level_servant_id": "100", "target_level": 90})
                runner.return_main = Mock()
                runner.reco = Mock()
                runner.clear_test_servant_materials = Mock()
                for entry, end in [("强化从者-导航到强化界面", "强化从者-导航完成"),
                                   ("强化从者-筛选准备", "筛选完成"),
                                   ("强化从者-强化一次", "强化从者-强化二次确认")]:
                    c.add(entry, end)
                c.hits["强化从者-强化二次确认"] = 99
                before = copy.deepcopy(c.nodes)
                isolated = NS(override_pipeline=Mock(return_value=True))
                c.clone = Mock(return_value=isolated)
                action = NS(_init_dirs=Mock(), _init_scale=Mock(),
                            _resolve_servant=Mock(return_value={"id": "100", "class": "saber", "rarity": 1}),
                            _levelup_servant=Mock(return_value=result))
                with patch.dict(sys.modules, {"servant_up_action": NS(ExecuteServantUp=lambda: action)}):
                    self.assertIs(runner.level_servant(), True)
                runner.return_main.assert_called_once_with()
                runner.reco.assert_not_called()
                runner.clear_test_servant_materials.assert_not_called()
                self.assertEqual(runner.reason, "servant_" + result)
                self.assertEqual(c.get_hit_count("强化从者-强化二次确认"), 0)
                self.assertIs(action._context, isolated)
                self.assertIs(action._controller, runner.controller)
                self.assertEqual(action._network_check, runner.wait_network)
                self.assertEqual(action._in_levelup, runner.selected_servant_page_ready)
                self.assertEqual(action._list_reset, runner.reset_servant_list)
                self.assertEqual(action._enter_select, runner.enter_servant_select)
                self.assertEqual(action._candidate_locator, runner.find_servant_candidate)
                self.assertEqual(action._candidate_selector, runner.select_servant_candidate)
                self.assertEqual(action._verify_selected, runner.verify_selected_servant)
                self.assertEqual(action._face_threshold, 0.90)
                self.assertEqual(action._face_roi, (60, 160, 960, 560))
                self.assertFalse(hasattr(action, "_max_find_rounds"))
                applied = isolated.override_pipeline.call_args.args[0]
                for entry in ("强化从者-导航到强化界面", "强化从者-筛选准备", "强化从者-强化一次"):
                    self.assertEqual(applied[entry]["next"],
                                     ["[JumpBack]" + NETWORK_RETRY, NETWORK_EXHAUSTED] + before[entry]["next"])
                    self.assertEqual(applied[entry]["on_error"], [])
                self.assertEqual(c.nodes, before)

    def test_servant_location_test_keeps_network_overrides_and_never_submits_materials(self):
        runner, c = self.runner({"make_ball": False, "levelup": True,
                                 "level_servant_id": "100", "target_level": 90})
        runner.return_main = Mock()
        runner.clear_test_servant_materials = Mock()
        c.run_recognition = Mock(return_value=None)
        c.add("强化从者-导航到强化界面", "强化从者-导航完成")
        c.add("强化从者-筛选准备", "筛选完成")
        c.next_terminal = ["强化从者-导航完成"]
        c.terminals_by_entry["搓丸子网络-等待恢复"] = ["搓丸子网络-界面无通信弹窗"]
        before = copy.deepcopy(c.nodes)
        isolated = NS(override_pipeline=Mock(return_value=True))
        c.clone = Mock(return_value=isolated)
        servant = {"id": "100", "class": "saber", "rarity": 1}
        action = NS(_init_dirs=Mock(), _init_scale=Mock(), _resolve_servant=Mock(return_value=servant),
                    _enter_servant_select=Mock(), _select_servant=Mock(return_value=True),
                    _levelup_servant=Mock())
        with patch.dict(sys.modules, {"servant_up_action": NS(ExecuteServantUp=lambda: action)}):
            self.assertIs(runner.level_servant(test=True), True)
        self.assertEqual(runner.reason, "servant_found")
        self.assertEqual(action._max_find_rounds, 6)
        self.assertEqual(action._in_levelup, runner.selected_servant_page_ready)
        runner.return_main.assert_called_once_with()
        runner.clear_test_servant_materials.assert_called_once_with()
        self.assertEqual([entry for entry, _ in c.tasks], ["搓丸子网络-等待恢复", "强化从者-导航到强化界面"])
        applied = isolated.override_pipeline.call_args.args[0]
        for entry in ("强化从者-导航到强化界面", "强化从者-筛选准备"):
            self.assertEqual(applied[entry]["next"],
                             ["[JumpBack]" + NETWORK_RETRY, NETWORK_EXHAUSTED] + before[entry]["next"])
            self.assertEqual(c.tasks[1][1][entry]["next"], applied[entry]["next"])
        action._enter_servant_select.assert_called_once_with()
        action._select_servant.assert_called_once_with(servant)
        action._levelup_servant.assert_not_called()
        self.assertEqual(c.nodes, before)

    def test_servant_location_navigation_exhaustion_stops_before_selection(self):
        runner, c = self.runner({"make_ball": False, "levelup": True,
                                 "level_servant_id": "100", "target_level": 90})
        runner.return_main = Mock()
        runner.clear_test_servant_materials = Mock()
        c.run_recognition = Mock(return_value=None)
        c.add("强化从者-导航到强化界面", "强化从者-导航完成")
        c.add("强化从者-筛选准备", "筛选完成")
        c.next_terminal = [NETWORK_EXHAUSTED]
        c.terminals_by_entry["搓丸子网络-等待恢复"] = ["搓丸子网络-界面无通信弹窗"]
        c.clone = lambda: NS(override_pipeline=lambda overrides: True)
        action = NS(_init_dirs=Mock(), _init_scale=Mock(),
                    _resolve_servant=Mock(return_value={"id": "100", "class": "saber", "rarity": 1}),
                    _enter_servant_select=Mock(), _select_servant=Mock(), _levelup_servant=Mock())
        with patch.dict(sys.modules, {"servant_up_action": NS(ExecuteServantUp=lambda: action)}):
            with self.assertRaisesRegex(runtime.ExpballFailure, "网络重试.*用尽"):
                runner.level_servant(test=True)
        action._enter_servant_select.assert_not_called()
        action._select_servant.assert_not_called()
        action._levelup_servant.assert_not_called()
        runner.return_main.assert_called_once_with()
        runner.clear_test_servant_materials.assert_not_called()
        self.assertEqual([entry for entry, _ in c.tasks], ["搓丸子网络-等待恢复", "强化从者-导航到强化界面"])

    def test_servant_location_existing_exact_target_skips_navigation_and_selection(self):
        for observed in ("BB", "BB（日文）"):
            with self.subTest(observed=observed):
                runner, c = self.runner({"make_ball": False, "levelup": True,
                                         "level_servant_id": "100", "target_level": 90})
                runner.return_main = Mock()
                runner.clear_test_servant_materials = Mock()
                c.add("强化从者-导航到强化界面", "强化从者-导航完成")
                c.add("强化从者-筛选准备", "筛选完成")
                c.next_terminal = ["搓丸子网络-界面无通信弹窗"]
                c.clone = lambda: NS(override_pipeline=lambda overrides: True)
                servant = {"id": "100", "class": "saber", "rarity": 1,
                           "name": "BB", "name_jp": "BB（日文）"}
                action = NS(_init_dirs=Mock(), _init_scale=Mock(), _resolve_servant=Mock(return_value=servant),
                            _enter_servant_select=Mock(), _select_servant=Mock(), _levelup_servant=Mock())

                def recognize(name, image, pipeline_override):
                    self.assertEqual([entry for entry, _ in c.tasks], ["搓丸子网络-等待恢复"])
                    if name == "搓丸子-从者强化标题":
                        return texts("从者强化")
                    patterns = pipeline_override[name]["recognition"]["param"]["expected"]
                    self.assertEqual(patterns, ["^BB$", "^BB（日文）$"])
                    return texts(observed) if any(re.search(pattern, observed) for pattern in patterns) else None

                c.run_recognition = Mock(side_effect=recognize)
                with patch.dict(sys.modules, {"servant_up_action": NS(ExecuteServantUp=lambda: action)}):
                    self.assertIs(runner.level_servant(test=True), True)
                self.assertEqual(runner.reason, "servant_found")
                self.assertEqual(action._in_levelup, runner.selected_servant_page_ready)
                self.assertEqual([entry for entry, _ in c.tasks], ["搓丸子网络-等待恢复"])
                self.assertEqual([call.args[0] for call in c.run_recognition.call_args_list],
                                 ["搓丸子-从者强化标题", "搓丸子-已选从者名称"])
                c.controller.post_screencap.assert_called_once_with()
                self.assertIs(c.run_recognition.call_args_list[0].args[1],
                              c.run_recognition.call_args_list[1].args[1])
                runner.return_main.assert_not_called()
                runner.clear_test_servant_materials.assert_called_once_with()
                action._enter_servant_select.assert_not_called()
                action._select_servant.assert_not_called()
                action._levelup_servant.assert_not_called()

    def test_servant_title_guard_accepts_cn_jp_but_rejects_ce_labels_in_fixed_top_roi(self):
        recognition = SERVANT_NODES["搓丸子-从者强化标题"]["recognition"]
        self.assertEqual(recognition["type"], "OCR")
        param = recognition["param"]
        self.assertEqual(param["roi"], [680, 0, 600, 100])
        self.assertEqual(param["threshold"], .8)
        for label in ("从者强化", "從者強化", "サーヴァント強化"):
            self.assertTrue(any(re.search(pattern, label) for pattern in param["expected"]), label)
        for label in ("概念礼装强化", "概念礼装強化", "灵基保管室", "BB"):
            self.assertFalse(any(re.search(pattern, label) for pattern in param["expected"]), label)

    def test_servant_location_cleanup_failure_never_reports_found_at_either_success_exit(self):
        for shortcut in (False, True):
            with self.subTest(shortcut=shortcut):
                runner, c = self.runner({"make_ball": False, "levelup": True,
                                         "level_servant_id": "100", "target_level": 90})
                runner.return_main = Mock()
                runner.clear_test_servant_materials = Mock(side_effect=runtime.ExpballFailure("测试素材未清空"))
                c.add("强化从者-导航到强化界面", "强化从者-导航完成")
                c.add("强化从者-筛选准备", "筛选完成")
                c.terminals_by_entry.update({
                    "搓丸子网络-等待恢复": ["搓丸子网络-界面无通信弹窗"],
                    "强化从者-导航到强化界面": ["强化从者-导航完成"],
                })
                c.run_recognition = Mock(return_value=texts("BB") if shortcut else None)
                c.clone = lambda: NS(override_pipeline=Mock(return_value=True))
                servant = {"id": "100", "class": "saber", "rarity": 1, "name": "BB"}
                action = NS(_init_dirs=Mock(), _init_scale=Mock(), _resolve_servant=Mock(return_value=servant),
                            _enter_servant_select=Mock(), _select_servant=Mock(return_value=True), _levelup_servant=Mock())
                with patch.dict(sys.modules, {"servant_up_action": NS(ExecuteServantUp=lambda: action)}):
                    with self.assertRaisesRegex(runtime.ExpballFailure, "测试素材未清空"):
                        runner.level_servant(test=True)
                runner.clear_test_servant_materials.assert_called_once_with()
                self.assertEqual(runner.reason, "not_started")
                action._levelup_servant.assert_not_called()
                if shortcut:
                    runner.return_main.assert_not_called()
                    action._select_servant.assert_not_called()
                    self.assertEqual([entry for entry, _ in c.tasks], ["搓丸子网络-等待恢复"])
                else:
                    runner.return_main.assert_called_once_with()
                    action._select_servant.assert_called_once_with(servant)
                    self.assertEqual([entry for entry, _ in c.tasks],
                                     ["搓丸子网络-等待恢复", "强化从者-导航到强化界面"])

    def test_clear_test_servant_materials_requires_pipeline_clear_terminal_and_never_repeats_after_network_failure(self):
        for terminal in ("搓丸子-测试从者素材已清空", NETWORK_EXHAUSTED, "unrelated_terminal"):
            with self.subTest(terminal=terminal):
                runner, c = self.runner()
                c.next_terminal = [terminal]
                if terminal == "搓丸子-测试从者素材已清空":
                    self.assertIsNone(runner.clear_test_servant_materials())
                else:
                    with self.assertRaises(runtime.ExpballFailure):
                        runner.clear_test_servant_materials()
                self.assertEqual([entry for entry, _ in c.tasks], ["搓丸子-清除测试从者素材"])
                if terminal == NETWORK_EXHAUSTED:
                    before = list(c.tasks)
                    with self.assertRaisesRegex(runtime.ExpballFailure, "网络重试.*用尽"):
                        runner.clear_test_servant_materials()
                    self.assertEqual(c.tasks, before)
                self.assertEqual(runner.reason, "not_started")

    def test_servant_location_preflight_network_exhaustion_prevents_shortcut_observation_and_navigation(self):
        runner, c = self.runner({"make_ball": False, "levelup": True,
                                 "level_servant_id": "100", "target_level": 90})
        runner.return_main = Mock()
        runner.clear_test_servant_materials = Mock()
        c.add("强化从者-导航到强化界面", "强化从者-导航完成")
        c.add("强化从者-筛选准备", "筛选完成")
        c.clone = lambda: NS(override_pipeline=Mock(return_value=True))
        c.next_terminal = [NETWORK_EXHAUSTED]
        c.run_recognition = Mock()
        servant = {"id": "100", "class": "saber", "rarity": 1, "name": "BB"}
        action = NS(_init_dirs=Mock(), _init_scale=Mock(), _resolve_servant=Mock(return_value=servant),
                    _enter_servant_select=Mock(), _select_servant=Mock(), _levelup_servant=Mock())
        with patch.dict(sys.modules, {"servant_up_action": NS(ExecuteServantUp=lambda: action)}):
            with self.assertRaisesRegex(runtime.ExpballFailure, "网络重试.*用尽"):
                runner.level_servant(test=True)
        self.assertEqual([entry for entry, _ in c.tasks], ["搓丸子网络-等待恢复"])
        self.assertEqual(c.get_hit_count(NETWORK_EXHAUSTED), 1)
        c.run_recognition.assert_not_called()
        c.controller.post_screencap.assert_not_called()
        runner.return_main.assert_not_called()
        runner.clear_test_servant_materials.assert_not_called()
        action._enter_servant_select.assert_not_called()
        action._select_servant.assert_not_called()
        action._levelup_servant.assert_not_called()

    def test_servant_location_ce_lookalike_or_wrong_name_returns_main_before_navigation(self):
        cases = (("ce_list_lookalike", "概念礼装强化", "其它礼装"),
                 ("same_name_ce", "概念礼装强化", "BB"),
                 ("servant_wrong_name", "从者强化", "BB Dubai"))
        for scene, title, observed in cases:
            with self.subTest(scene=scene):
                runner, c = self.runner({"make_ball": False, "levelup": True,
                                         "level_servant_id": "100", "target_level": 90})
                events = []
                runner.return_main = Mock(side_effect=lambda: events.append("return_main"))
                runner.clear_test_servant_materials = Mock()
                c.add("强化从者-导航到强化界面", "强化从者-导航完成")
                c.add("强化从者-筛选准备", "筛选完成")
                c.next_terminal = ["强化从者-导航完成"]
                c.terminals_by_entry["搓丸子网络-等待恢复"] = ["搓丸子网络-界面无通信弹窗"]
                original_run_task = c.run_task

                def run_task(entry, pipeline_override=None):
                    events.append(entry)
                    return original_run_task(entry, pipeline_override)

                c.run_task = run_task
                c.clone = lambda: NS(override_pipeline=lambda overrides: True)
                servant = {"id": "100", "class": "saber", "rarity": 1, "name": "BB", "name_jp": "BB（日文）"}
                action = NS(_init_dirs=Mock(), _init_scale=Mock(), _resolve_servant=Mock(return_value=servant),
                            _enter_servant_select=Mock(), _select_servant=Mock(return_value=True), _levelup_servant=Mock())

                def recognize(name, image, pipeline_override):
                    if name == "强化从者-已在强化列表":
                        # A shared image may resemble a CE list; it cannot prove
                        # this is the servant enhancement page for the shortcut.
                        return texts("list_template_hit")
                    if name == "搓丸子-从者强化标题":
                        patterns = c.nodes[name]["recognition"]["param"]["expected"]
                        return texts(title) if any(re.search(pattern, title) for pattern in patterns) else None
                    if name == "搓丸子-已选从者名称":
                        patterns = pipeline_override[name]["recognition"]["param"]["expected"]
                        return texts(observed) if any(re.search(pattern, observed) for pattern in patterns) else None
                    raise AssertionError(f"unexpected recognition: {name}")

                c.run_recognition = Mock(side_effect=recognize)
                with patch.dict(sys.modules, {"servant_up_action": NS(ExecuteServantUp=lambda: action)}):
                    self.assertIs(runner.level_servant(test=True), True)
                runner.return_main.assert_called_once_with()
                runner.clear_test_servant_materials.assert_called_once_with()
                self.assertLess(events.index("return_main"), events.index("强化从者-导航到强化界面"))
                self.assertEqual(events[0], "搓丸子网络-等待恢复")
                action._enter_servant_select.assert_called_once_with()
                action._select_servant.assert_called_once_with(servant)
                action._levelup_servant.assert_not_called()
                if title == "概念礼装强化":
                    self.assertEqual([call.args[0] for call in c.run_recognition.call_args_list],
                                     ["搓丸子-从者强化标题"])

    def test_servant_invalid_target_or_class_stops_before_return_main_network_or_navigation(self):
        for test in (False, True):
            for servant in (None, {"id": "100", "class": "unknown", "rarity": 1, "name": "BB"}):
                with self.subTest(test=test, servant=servant):
                    runner, c = self.runner({"make_ball": False, "levelup": True,
                                             "level_servant_id": "100", "target_level": 90})
                    runner.return_main, runner.wait_network, runner.task, runner.reco = Mock(), Mock(), Mock(), Mock()
                    runner.clear_test_servant_materials = Mock()
                    isolated = NS(override_pipeline=Mock(return_value=True))
                    c.clone = Mock(return_value=isolated)
                    action = NS(_init_dirs=Mock(), _init_scale=Mock(), _resolve_servant=Mock(return_value=servant),
                                _enter_servant_select=Mock(), _select_servant=Mock(), _levelup_servant=Mock())
                    with patch.dict(sys.modules, {"servant_up_action": NS(ExecuteServantUp=lambda: action)}):
                        with self.assertRaises(runtime.ExpballFailure):
                            runner.level_servant(test=test)
                    runner.return_main.assert_not_called()
                    runner.wait_network.assert_not_called()
                    runner.task.assert_not_called()
                    runner.reco.assert_not_called()
                    runner.clear_test_servant_materials.assert_not_called()
                    isolated.override_pipeline.assert_not_called()
                    action._enter_servant_select.assert_not_called()
                    action._select_servant.assert_not_called()
                    action._levelup_servant.assert_not_called()
                    c.controller.post_screencap.assert_not_called()

    def test_reused_flow_entries_do_not_clear_current_network_exhaustion(self):
        for method in ("return_main", "level_servant"):
            with self.subTest(method=method):
                runner, c = self.runner({"make_ball": False, "levelup": True,
                                         "level_servant_id": "100", "target_level": 90})
                c.add("回主界面")
                for entry in ("强化从者-导航到强化界面", "强化从者-筛选准备", "强化从者-强化一次"):
                    c.add(entry)
                c.clone = Mock(return_value=NS(override_pipeline=Mock(return_value=True)))
                c.hits[NETWORK_EXHAUSTED] = 1
                cleared_before = list(c.cleared)
                action = NS(_init_dirs=Mock(), _init_scale=Mock(),
                            _resolve_servant=Mock(return_value={"id": "100", "class": "saber", "rarity": 1}),
                            _levelup_servant=Mock(return_value="target_reached"))
                with patch.dict(sys.modules, {"servant_up_action": NS(ExecuteServantUp=lambda: action)}):
                    with self.assertRaisesRegex(runtime.ExpballFailure, "网络重试.*用尽"):
                        getattr(runner, method)()
                self.assertEqual(c.hits[NETWORK_EXHAUSTED], 1)
                self.assertEqual(c.cleared, cleared_before)
                self.assertEqual(c.tasks, [])
                action._levelup_servant.assert_not_called()

    def test_return_main_adds_network_waits_locally_and_rejects_exhaustion(self):
        for exhausted in (False, True):
            with self.subTest(exhausted=exhausted):
                runner, c = self.runner()
                c.add("回主界面", "关闭窗口")
                c.add("关闭窗口", "无弹窗")
                c.nodes["关闭窗口"]["on_error"] = ["unsafe_recovery"]
                c.terminals_by_entry["搓丸子网络-等待恢复"] = ["搓丸子网络-界面无通信弹窗"]
                c.terminals_by_entry["回主界面"] = [NETWORK_EXHAUSTED] if exhausted else ["无弹窗"]
                c.run_recognition = Mock(return_value=None)
                image = object()
                runner.frame = Mock(return_value=image)
                before = copy.deepcopy(c.nodes)
                if exhausted:
                    with self.assertRaisesRegex(runtime.ExpballFailure, "网络重试.*用尽"):
                        runner.return_main()
                else:
                    runner.return_main()
                self.assertEqual([entry for entry, _ in c.tasks], ["搓丸子网络-等待恢复", "回主界面"])
                self.assertEqual([call.args[0] for call in c.run_recognition.call_args_list],
                                 ["强化从者-已在强化列表", "强化从者-已在强化界面",
                                  "搓丸子-从者强化已选页面", "搓丸子-详情判定"])
                runner.frame.assert_called_once_with()
                self.assertTrue(all(call.args[1] is image for call in c.run_recognition.call_args_list))
                applied = c.tasks[1][1]
                for back in ("主界面判断-返回", "有弹窗返回"):
                    self.assertEqual(applied[back]["max_hit"], 6)
                for entry in ("回主界面", "关闭窗口"):
                    self.assertEqual(applied[entry]["next"],
                                     ["[JumpBack]" + NETWORK_RETRY, NETWORK_EXHAUSTED] + before[entry]["next"])
                    self.assertEqual(applied[entry]["on_error"], [])
                self.assertEqual(c.nodes, before)

    def test_return_main_leaves_servant_pages_using_local_template_back_flow(self):
        guards = ("强化从者-已在强化列表", "强化从者-已在强化界面",
                  "搓丸子-从者强化已选页面", "搓丸子-详情判定")
        cases = (("old_servant_list", guards[0]), ("old_servant_page", guards[1]),
                 ("selected_servant_empty_materials", guards[2]),
                 ("selected_servant_nonempty_materials", guards[2]), ("ce_enhancement", guards[3]))
        for scene, recognized in cases:
            with self.subTest(scene=scene, recognized=recognized):
                runner, c = self.runner()
                image = object()
                runner.frame = Mock(return_value=image)
                c.add("回主界面", "无弹窗")
                names = ("强化从者-狗粮消耗完毕", "强化从者-消耗完毕后退",
                         "强化从者-回到有菜单的界面", "强化从者-狗粮消耗完毕结束")
                c.nodes.update({name: copy.deepcopy(SERVANT_UP_NODES[name]) for name in names})
                c.terminals_by_entry.update({
                    "搓丸子网络-等待恢复": ["搓丸子网络-界面无通信弹窗"],
                    "强化从者-狗粮消耗完毕": ["强化从者-狗粮消耗完毕结束"],
                    "回主界面": ["无弹窗"],
                })
                c.hits["强化从者-消耗完毕后退"] = 99
                c.hits["强化从者-狗粮消耗完毕结束"] = 99
                c.run_recognition = Mock(side_effect=lambda name, *args, **kwargs:
                                         texts("servant-screen") if name == recognized else None)
                before = copy.deepcopy(c.nodes)
                runner.return_main()
                self.assertEqual([entry for entry, _ in c.tasks], ["搓丸子网络-等待恢复",
                                 "强化从者-狗粮消耗完毕", "回主界面"])
                self.assertEqual([call.args[0] for call in c.run_recognition.call_args_list],
                                 list(guards[:guards.index(recognized) + 1]))
                self.assertTrue(all(call.args[1] is image for call in c.run_recognition.call_args_list))
                runner.frame.assert_called_once_with()
                applied = c.tasks[1][1]
                self.assertEqual(applied["强化从者-消耗完毕后退"]["max_hit"], 4)
                self.assertEqual(SERVANT_UP_NODES["强化从者-消耗完毕后退"]["action"]["type"], "Click")
                self.assertEqual(c.get_hit_count("强化从者-消耗完毕后退"), 0)
                self.assertEqual(c.get_hit_count("强化从者-狗粮消耗完毕结束"), 1)
                for name in names:
                    self.assertEqual(applied[name]["on_error"], [])
                for name in ("强化从者-狗粮消耗完毕", "强化从者-回到有菜单的界面"):
                    self.assertEqual(applied[name]["next"][:2],
                                     ["[JumpBack]" + NETWORK_RETRY, NETWORK_EXHAUSTED])
                self.assertEqual(applied["强化从者-狗粮消耗完毕"]["next"][2:],
                                 before["强化从者-狗粮消耗完毕"]["next"])
                self.assertEqual(c.tasks[2][1]["无弹窗"]["next"], [])
                for back in ("主界面判断-返回", "有弹窗返回"):
                    self.assertEqual(c.tasks[2][1][back]["max_hit"], 6)
                self.assertEqual(c.nodes, before)

    def test_return_main_requires_exit_and_main_business_terminals(self):
        for failed_stage in ("强化从者-狗粮消耗完毕", "回主界面"):
            with self.subTest(failed_stage=failed_stage):
                runner, c = self.runner()
                c.add("回主界面", "无弹窗")
                for name in ("强化从者-狗粮消耗完毕", "强化从者-消耗完毕后退",
                             "强化从者-回到有菜单的界面", "强化从者-狗粮消耗完毕结束"):
                    c.nodes[name] = copy.deepcopy(SERVANT_UP_NODES[name])
                c.run_recognition = Mock(return_value=texts("servant-screen"))
                c.terminals_by_entry.update({
                    "搓丸子网络-等待恢复": ["搓丸子网络-界面无通信弹窗"],
                    "强化从者-狗粮消耗完毕": ["强化从者-狗粮消耗完毕结束"],
                    "回主界面": ["无弹窗"],
                })
                c.terminals_by_entry[failed_stage] = []
                with self.assertRaisesRegex(runtime.ExpballFailure, "流程结束状态不明确"):
                    runner.return_main()
                self.assertTrue(c.task_succeeded)
                if failed_stage == "强化从者-狗粮消耗完毕":
                    self.assertNotIn("回主界面", [entry for entry, _ in c.tasks])

    def test_return_main_servant_exit_exhaustion_stops_before_generic_back(self):
        for recognized in ("强化从者-已在强化列表", "搓丸子-从者强化已选页面", "搓丸子-详情判定"):
            for failed_stage in ("搓丸子网络-等待恢复", "强化从者-狗粮消耗完毕"):
                with self.subTest(recognized=recognized, failed_stage=failed_stage):
                    runner, c = self.runner()
                    c.add("回主界面", "无弹窗")
                    for name in ("强化从者-狗粮消耗完毕", "强化从者-消耗完毕后退",
                                 "强化从者-回到有菜单的界面", "强化从者-狗粮消耗完毕结束"):
                        c.nodes[name] = copy.deepcopy(SERVANT_UP_NODES[name])
                    c.run_recognition = Mock(side_effect=lambda name, *args, **kwargs:
                                             texts("enhancement-screen") if name == recognized else None)
                    runner.frame = Mock(return_value="shared-enhancement-frame")
                    c.terminals_by_entry["搓丸子网络-等待恢复"] = ["搓丸子网络-界面无通信弹窗"]
                    c.terminals_by_entry["强化从者-狗粮消耗完毕"] = [NETWORK_EXHAUSTED]
                    c.terminals_by_entry[failed_stage] = [NETWORK_EXHAUSTED]
                    with self.assertRaisesRegex(runtime.ExpballFailure, "网络重试.*用尽"):
                        runner.return_main()
                    self.assertEqual(c.get_hit_count(NETWORK_EXHAUSTED), 1)
                    expected = ["搓丸子网络-等待恢复"]
                    if failed_stage == "强化从者-狗粮消耗完毕":
                        expected.append("强化从者-狗粮消耗完毕")
                        runner.frame.assert_called_once_with()
                    else:
                        runner.frame.assert_not_called()
                        c.run_recognition.assert_not_called()
                    self.assertEqual([entry for entry, _ in c.tasks], expected)
                    before = list(c.tasks)
                    reco_before = list(c.run_recognition.call_args_list)
                    with self.assertRaisesRegex(runtime.ExpballFailure, "网络重试.*用尽"):
                        runner.return_main()
                    self.assertEqual(c.tasks, before)
                    self.assertEqual(c.run_recognition.call_args_list, reco_before)

    def test_servant_callback_stops_before_selection_scan_or_upgrade_input(self):
        import servant_up_action as servant_runtime

        for stage in ("enter_selection", "after_filter", "face_scan", "upgrade_loop"):
            with self.subTest(stage=stage):
                runner, c = self.runner()
                action = servant_runtime.ExecuteServantUp()
                action._context, action._controller = c, NS(post_click=Mock(), post_swipe=Mock())
                action._network_check = runner.wait_network
                action._in_servant_select, action._tap = Mock(return_value=False), Mock()
                action._match, action._read_level = Mock(), Mock(return_value=1)
                action._tpl = lambda name: name
                servant = {"id": "100", "name": "BB", "images": ["f_1000.png"]}
                if stage == "after_filter":
                    c.next_terminal = [NETWORK_EXHAUSTED]
                    action._find_servant = Mock()
                else:
                    c.hits[NETWORK_EXHAUSTED] = 1
                with patch.object(servant_runtime.os.path, "isfile", side_effect=lambda p: p == "servant_face/f_1000.png"):
                    with self.assertRaisesRegex(runtime.ExpballFailure, "网络重试.*用尽"):
                        if stage == "enter_selection":
                            action._enter_servant_select()
                        elif stage == "after_filter":
                            action._select_servant(servant)
                        elif stage == "face_scan":
                            action._find_servant(servant)
                        else:
                            action._levelup_loop(servant, 90)
                action._tap.assert_not_called()
                action._match.assert_not_called()
                action._read_level.assert_not_called()
                action._controller.post_click.assert_not_called()
                action._controller.post_swipe.assert_not_called()
                if stage == "after_filter":
                    action._find_servant.assert_not_called()
                    self.assertEqual([entry for entry, _ in c.tasks], ["强化从者-筛选准备"])
                else:
                    self.assertEqual(c.tasks, [])

    def test_servant_enter_and_reset_hooks_use_named_pipeline_tasks(self):
        import servant_up_action as servant_runtime

        runner, c = self.runner()
        action = servant_runtime.ExecuteServantUp()
        action._enter_select = runner.enter_servant_select
        action._in_servant_select, action._tap = Mock(), Mock()
        c.next_terminal = ["搓丸子-从者选择列表已到"]
        action._enter_servant_select()
        c.next_terminal = ["搓丸子-从者列表复位完成"]
        runner.reset_servant_list()
        self.assertEqual([entry for entry, _ in c.tasks],
                         ["搓丸子-进入从者选择", "搓丸子-从者列表复位顶部"])
        action._in_servant_select.assert_not_called()
        action._tap.assert_not_called()

    def test_servant_filter_resets_before_lookup_and_legacy_path_remains_optional(self):
        import servant_up_action as servant_runtime

        servant = {"id": "100", "name": "BB"}
        for configured in (False, True):
            with self.subTest(configured=configured):
                events = []
                action = servant_runtime.ExecuteServantUp()
                action._context = NS(run_task=Mock(side_effect=lambda entry: events.append("filter")))
                action._find_servant = Mock(side_effect=lambda target: events.append("lookup") or True)
                if configured:
                    action._network_check = Mock(side_effect=lambda: events.append("network"))
                    action._list_reset = Mock(side_effect=lambda: events.append("reset"))
                self.assertIs(action._select_servant(servant), True)
                self.assertEqual(events, ["filter", "network", "reset", "lookup"] if configured
                                 else ["filter", "lookup"])
                action._find_servant.assert_called_once_with(servant)

        action = servant_runtime.ExecuteServantUp()
        action._context = NS(run_task=Mock())
        action._list_reset = Mock(side_effect=runtime.ExpballFailure("列表复位未确认"))
        action._find_servant = Mock()
        with self.assertRaisesRegex(runtime.ExpballFailure, "列表复位未确认"):
            action._select_servant(servant)
        action._find_servant.assert_not_called()

    def test_servant_candidate_merges_only_target_templates_and_deduplicates_positions(self):
        for identity in ("100", "704000"):
            with self.subTest(identity=identity):
                runner, c = self.runner()
                runner.frame = Mock(return_value="same-candidate-frame")
                original = f"servant_face/f_{identity}.png"
                extra = SERVANT_NODES["搓丸子-从者列表复位顶部"]["attach"]["servant_templates"].get(identity, [])
                if extra:
                    c.nodes["搓丸子-从者列表复位顶部"]["attach"]["servant_templates"][identity] = [original, *extra, *extra]
                low = NS(score=0.91, box=[96, 200, 48, 48])
                best = NS(score=0.96, box=[96, 200, 48, 48])
                c.run_recognition = Mock(return_value=NS(hit=True, all_results=[low, best],
                                                        filtered_results=[low, best]))
                before = copy.deepcopy(c.nodes)
                self.assertEqual(runner.find_servant_candidate({"id": identity, "name": "目标"},
                                                              [original, original]),
                                 (0.96, 120, 224, "Pipeline:升级从者头像候选"))
                runner.frame.assert_called_once_with()
                call = c.run_recognition.call_args
                self.assertEqual(call.args, ("搓丸子-升级从者头像候选", "same-candidate-frame"))
                candidate = call.kwargs["pipeline_override"]["搓丸子-升级从者头像候选"]
                self.assertIs(candidate["enabled"], True)
                self.assertEqual(candidate["recognition"]["param"], {
                    "template": [original, *extra], "threshold": 0.90, "roi": [60, 160, 960, 560]})
                self.assertEqual(c.nodes, before)
                self.assertEqual(c.tasks, [])

    def test_servant_candidate_ambiguous_positions_stop_before_click_or_scroll(self):
        import servant_up_action as servant_runtime

        runner, c = self.runner()
        runner.frame = Mock(return_value="same-candidate-frame")
        hits = [NS(score=0.96, box=[96, 200, 48, 48]), NS(score=0.93, box=[250, 200, 48, 48])]
        c.run_recognition = Mock(return_value=NS(hit=True, all_results=hits, filtered_results=hits))
        action = servant_runtime.ExecuteServantUp()
        action._context, action._controller = c, NS(post_click=Mock(), post_swipe=Mock())
        action._candidate_locator = runner.find_servant_candidate
        action._face_threshold, action._max_find_rounds = 0.90, 6
        action._tpl = lambda name: name
        action._match = Mock()
        with patch.object(servant_runtime.os.path, "isfile", side_effect=lambda p: p == "servant_face/f_1000.png"):
            with self.assertRaisesRegex(runtime.ExpballFailure, "候选不唯一"):
                action._find_servant({"id": "100", "name": "BB", "images": ["f_1000.png"]})
        action._match.assert_not_called()
        action._controller.post_click.assert_not_called()
        action._controller.post_swipe.assert_not_called()
        self.assertEqual(c.run_recognition.call_count, 1)

    def test_selected_servant_name_requires_exact_chinese_or_japanese_match(self):
        servant = {"id": "704000", "name": "摩根", "name_jp": "モルガン"}
        for observed in ("摩根", "モルガン", "阿尔托莉雅", "摩根〔冬之女王〕", "前缀モルガン", ""):
            with self.subTest(observed=observed):
                runner, c = self.runner()
                runner.frame = Mock(return_value="selected-servant-frame")
                c.terminals_by_entry.update({
                    "搓丸子网络-等待恢复": ["搓丸子网络-界面无通信弹窗"],
                    "搓丸子-打开当前从者资料": ["搓丸子-从者资料就绪"],
                })

                def recognize(name, image, pipeline_override):
                    if name == "搓丸子-从者强化标题":
                        return texts("从者强化")
                    patterns = pipeline_override[name]["recognition"]["param"]["expected"]
                    return texts(observed) if any(re.search(pattern, observed) for pattern in patterns) else None

                c.run_recognition = Mock(side_effect=recognize)
                if observed in ("摩根", "モルガン"):
                    runner.verify_selected_servant(servant)
                else:
                    with self.assertRaisesRegex(runtime.ExpballFailure, "已选从者名称未确认"):
                        runner.verify_selected_servant(servant)
                calls = c.run_recognition.call_args_list
                self.assertEqual(calls[0].args, ("搓丸子-从者强化标题", "selected-servant-frame"))
                self.assertEqual(calls[1].args, ("搓丸子-已选从者名称", "selected-servant-frame"))
                self.assertEqual(calls[1].kwargs["pipeline_override"]["搓丸子-已选从者名称"]
                                 ["recognition"]["param"]["expected"], ["^摩根$", "^モルガン$"])
                if observed in ("摩根", "モルガン"):
                    self.assertEqual(len(calls), 2)
                    runner.frame.assert_called_once_with()
                    self.assertEqual([entry for entry, _ in c.tasks], ["搓丸子网络-等待恢复"])
                else:
                    self.assertEqual(calls[2].args, ("搓丸子-资料从者名称", "selected-servant-frame"))
                    self.assertEqual(calls[2].kwargs["pipeline_override"]["搓丸子-资料从者名称"]
                                     ["recognition"]["param"]["expected"], ["^摩根$", "^モルガン$"])
                    self.assertEqual([entry for entry, _ in c.tasks],
                                     ["搓丸子网络-等待恢复", "搓丸子-打开当前从者资料"])
                self.assertEqual(runner.reason, "not_started")

        runner, c = self.runner()
        c.next_terminal = ["搓丸子网络-界面无通信弹窗"]
        c.run_recognition = Mock()
        with self.assertRaisesRegex(runtime.ExpballFailure, "已选从者名称未确认"):
            runner.verify_selected_servant({"id": "100"})
        c.run_recognition.assert_not_called()
        c.controller.post_screencap.assert_not_called()
        self.assertEqual([entry for entry, _ in c.tasks], ["搓丸子网络-等待恢复"])

    def test_selected_servant_unreadable_small_name_uses_exact_profile_name_then_closes(self):
        servant = {"id": "704000", "name": "摩根", "name_jp": "モルガン"}
        for observed in ("摩根", "モルガン"):
            with self.subTest(observed=observed):
                runner, c = self.runner()
                runner.frame = Mock(side_effect=["enhancement-frame", "profile-frame"])
                c.terminals_by_entry.update({
                    "搓丸子网络-等待恢复": ["搓丸子网络-界面无通信弹窗"],
                    "搓丸子-打开当前从者资料": ["搓丸子-从者资料就绪"],
                    "搓丸子-关闭当前从者资料": ["搓丸子-从者资料已关闭"],
                })

                def recognize(name, image, pipeline_override):
                    if name == "搓丸子-从者强化标题":
                        return texts("从者强化")
                    patterns = pipeline_override[name]["recognition"]["param"]["expected"]
                    self.assertEqual(patterns, ["^摩根$", "^モルガン$"])
                    if name == "搓丸子-已选从者名称":
                        return None
                    self.assertEqual(name, "搓丸子-资料从者名称")
                    self.assertEqual(image, "profile-frame")
                    return texts(observed) if any(re.search(pattern, observed) for pattern in patterns) else None

                c.run_recognition = Mock(side_effect=recognize)
                self.assertIsNone(runner.verify_selected_servant(servant))
                self.assertEqual([entry for entry, _ in c.tasks],
                                 ["搓丸子网络-等待恢复", "搓丸子-打开当前从者资料", "搓丸子-关闭当前从者资料"])
                self.assertEqual([call.args[0] for call in c.run_recognition.call_args_list],
                                 ["搓丸子-从者强化标题", "搓丸子-已选从者名称", "搓丸子-资料从者名称"])
                self.assertIs(c.run_recognition.call_args_list[0].args[1],
                              c.run_recognition.call_args_list[1].args[1])

    def test_selected_servant_wrong_page_title_never_opens_profile_even_with_correct_name(self):
        runner, c = self.runner()
        c.next_terminal = ["搓丸子网络-界面无通信弹窗"]
        c.run_recognition = Mock(return_value=None)
        with self.assertRaisesRegex(runtime.ExpballFailure, "页面身份未确认"):
            runner.verify_selected_servant({"id": "100", "name": "BB"})
        self.assertEqual([call.args[0] for call in c.run_recognition.call_args_list], ["搓丸子-从者强化标题"])
        self.assertEqual([entry for entry, _ in c.tasks], ["搓丸子网络-等待恢复"])

    def test_selected_servant_profile_network_exhaustion_never_reopens_or_recloses(self):
        for stage in ("open", "close"):
            with self.subTest(stage=stage):
                runner, c = self.runner()
                c.terminals_by_entry.update({
                    "搓丸子网络-等待恢复": ["搓丸子网络-界面无通信弹窗"],
                    "搓丸子-打开当前从者资料": [NETWORK_EXHAUSTED] if stage == "open" else ["搓丸子-从者资料就绪"],
                    "搓丸子-关闭当前从者资料": [NETWORK_EXHAUSTED],
                })

                def recognize(name, image, pipeline_override):
                    return None if name == "搓丸子-已选从者名称" else texts("BB")

                c.run_recognition = Mock(side_effect=recognize)
                with self.assertRaisesRegex(runtime.ExpballFailure, "网络重试.*用尽"):
                    runner.verify_selected_servant({"id": "100", "name": "BB"})
                expected = ["搓丸子网络-等待恢复", "搓丸子-打开当前从者资料"]
                if stage == "close":
                    expected.append("搓丸子-关闭当前从者资料")
                self.assertEqual([entry for entry, _ in c.tasks], expected)
                before = list(c.tasks)
                with self.assertRaisesRegex(runtime.ExpballFailure, "网络重试.*用尽"):
                    runner.verify_selected_servant({"id": "100", "name": "BB"})
                self.assertEqual(c.tasks, before)
                self.assertEqual(runner.reason, "not_started")

    def test_selected_servant_name_waits_for_network_before_ocr(self):
        runner, c = self.runner()
        c.next_terminal = [NETWORK_EXHAUSTED]
        c.run_recognition = Mock()
        with self.assertRaisesRegex(runtime.ExpballFailure, "网络重试.*用尽"):
            runner.verify_selected_servant({"id": "100", "name": "BB"})
        c.run_recognition.assert_not_called()
        self.assertEqual([entry for entry, _ in c.tasks], ["搓丸子网络-等待恢复"])

    def test_servant_candidate_selector_preserves_selected_card_or_clicks_pipeline_coordinates(self):
        import servant_up_action as servant_runtime

        for observed, selected in (("はずす", True), ("卸除", True), ("卸除XX", False), ("未知", False)):
            for x, y in ((120, 248), (65, 690)):
                with self.subTest(observed=observed, selected=selected, x=x, y=y):
                    runner, c = self.runner()
                    runner.frame = Mock(return_value="candidate-selection-frame")
                    patterns = c.nodes["搓丸子-候选已是当前从者"]["recognition"]["param"]["expected"]
                    c.run_recognition = Mock(return_value=texts(observed) if any(re.search(p, observed) for p in patterns) else None)
                    c.controller.post_click, c.controller.post_swipe = Mock(), Mock()
                    c.next_terminal = ["搓丸子-关闭从者选择执行" if selected else "搓丸子-点击升级从者候选执行"]
                    before = copy.deepcopy(c.nodes)
                    action = servant_runtime.ExecuteServantUp()
                    action._context, action._controller = c, c.controller
                    action._candidate_locator = Mock(return_value=(0.96, x, y, "Pipeline:候选"))
                    action._candidate_selector = runner.select_servant_candidate
                    action._face_threshold = 0.90
                    action._in_levelup = Mock(return_value=True)
                    action._verify_selected = Mock()
                    action._tpl = lambda name: name
                    servant = {"id": "100", "name": "BB", "images": ["f_1000.png"]}
                    with patch.object(servant_runtime.os.path, "isfile", side_effect=lambda p: p == "servant_face/f_1000.png"), \
                            patch.object(servant_runtime.time, "sleep"):
                        self.assertIs(action._find_servant(servant), True)
                    c.controller.post_click.assert_not_called()
                    c.controller.post_swipe.assert_not_called()
                    action._verify_selected.assert_called_once_with(servant)
                    call = c.run_recognition.call_args
                    self.assertEqual(call.args, ("搓丸子-候选已是当前从者", "candidate-selection-frame"))
                    expected_roi = [max(60, x - 100), min(610, y + 30), 200, 110]
                    self.assertEqual(call.kwargs["pipeline_override"]["搓丸子-候选已是当前从者"]
                                     ["recognition"]["param"]["roi"], expected_roi)
                    self.assertEqual(len(c.tasks), 1)
                    entry, override = c.tasks[0]
                    if selected:
                        self.assertEqual(entry, "搓丸子-关闭从者选择")
                        self.assertNotIn("搓丸子-点击升级从者候选执行", override)
                    else:
                        self.assertEqual(entry, "搓丸子-点击升级从者候选")
                        self.assertEqual(override["搓丸子-点击升级从者候选执行"]["action"]["param"]["target"],
                                         [x, y, 1, 1])
                    self.assertEqual(c.nodes, before)

    def test_servant_pipeline_locator_obeys_six_round_limit_and_name_verification(self):
        import servant_up_action as servant_runtime

        servant = {"id": "100", "name": "BB", "images": ["f_1000.png"]}
        for outcome in ("low_score", "wrong_name", "verified"):
            with self.subTest(outcome=outcome):
                action = servant_runtime.ExecuteServantUp()
                action._context = FakeContext()
                action._controller = NS(post_click=Mock(return_value=NS(wait=Mock())),
                                        post_swipe=Mock(return_value=NS(wait=Mock())))
                action._tpl = lambda name: name
                action._px = action._py = lambda position: position
                action._face_threshold, action._max_find_rounds = 0.90, 6
                action._candidate_locator = Mock(return_value=(0.89 if outcome == "low_score" else 0.95,
                                                              120, 248, "Pipeline:候选"))
                action._match = Mock()
                action._in_levelup = Mock(return_value=True)
                action._verify_selected = Mock(side_effect=runtime.ExpballFailure("已选从者名称未确认")
                                               if outcome == "wrong_name" else None)
                with patch.object(servant_runtime.os.path, "isfile", side_effect=lambda p: p == "servant_face/f_1000.png"), \
                        patch.object(servant_runtime.time, "sleep"):
                    if outcome == "wrong_name":
                        with self.assertRaisesRegex(runtime.ExpballFailure, "已选从者名称未确认"):
                            action._find_servant(servant)
                    else:
                        self.assertEqual(action._find_servant(servant), outcome == "verified")
                action._match.assert_not_called()
                if outcome == "low_score":
                    self.assertEqual(action._candidate_locator.call_count, 6)
                    self.assertEqual(action._controller.post_swipe.call_count, 6)
                    action._controller.post_click.assert_not_called()
                    action._verify_selected.assert_not_called()
                else:
                    action._candidate_locator.assert_called_once_with(servant, ["servant_face/f_1000.png"])
                    action._controller.post_click.assert_called_once_with(120, 248)
                    action._controller.post_swipe.assert_not_called()
                    action._verify_selected.assert_called_once_with(servant)

    def test_servant_pipeline_ready_replaces_missing_legacy_marker_and_keeps_missing_page_bounded(self):
        import servant_up_action as servant_runtime

        for ready in (True, False):
            with self.subTest(ready=ready):
                runner, c = self.runner()
                c.next_terminal = ["搓丸子网络-界面无通信弹窗"]
                c.run_recognition = Mock(return_value=texts("selected-servant-page") if ready else None)
                action = servant_runtime.ExecuteServantUp()
                action._context = c
                action._controller = NS(post_click=Mock(return_value=NS(wait=Mock())),
                                        post_swipe=Mock(return_value=NS(wait=Mock())))
                action._tpl = lambda name: name
                action._px = action._py = lambda position: position
                action._candidate_locator = Mock(return_value=(.96, 120, 248, "Pipeline:候选"))
                action._face_threshold, action._max_find_rounds = .90, 1
                action._match = Mock(return_value=None)
                self.assertIs(action._in_levelup(), False)
                action._match.reset_mock()
                action._network_check = runner.wait_network
                action._in_levelup = runner.selected_servant_page_ready
                action._verify_selected = Mock()
                servant = {"id": "100", "name": "BB", "images": ["f_1000.png"]}
                with patch.object(servant_runtime.os.path, "isfile", side_effect=lambda p: p == "servant_face/f_1000.png"), \
                        patch.object(servant_runtime.time, "sleep"):
                    self.assertIs(action._find_servant(servant), ready)
                action._match.assert_not_called()
                action._candidate_locator.assert_called_once_with(servant, ["servant_face/f_1000.png"])
                action._controller.post_click.assert_called_once_with(120, 248)
                self.assertTrue(all(call.args[0] == "搓丸子-从者强化已选页面"
                                    for call in c.run_recognition.call_args_list))
                if ready:
                    self.assertEqual(c.run_recognition.call_count, 1)
                    action._verify_selected.assert_called_once_with(servant)
                    action._controller.post_swipe.assert_not_called()
                else:
                    self.assertEqual(c.run_recognition.call_count, 12)
                    action._verify_selected.assert_not_called()
                    action._controller.post_swipe.assert_called_once()

    def test_servant_pipeline_ready_network_exhaustion_stops_before_verify_or_repeated_click(self):
        import servant_up_action as servant_runtime

        for stage in ("before_ready", "ready_wait", "after_ready"):
            with self.subTest(stage=stage):
                runner, c = self.runner()
                action = servant_runtime.ExecuteServantUp()
                action._context = c
                action._controller = NS(post_click=Mock(return_value=NS(wait=Mock())),
                                        post_swipe=Mock(return_value=NS(wait=Mock())))
                action._tpl = lambda name: name
                action._px = action._py = lambda position: position
                action._candidate_locator = Mock(return_value=(.96, 120, 248, "Pipeline:候选"))
                action._face_threshold, action._max_find_rounds = .90, 1
                action._network_check = runner.wait_network
                action._in_levelup = runner.selected_servant_page_ready
                action._verify_selected = Mock()
                action._match = Mock(return_value=None)

                def run_task(entry, pipeline_override=None):
                    failure_at = 2 if stage == "before_ready" else 3
                    c.next_terminal = ([NETWORK_EXHAUSTED] if stage != "after_ready" and len(c.tasks) + 1 == failure_at
                                       else ["搓丸子网络-界面无通信弹窗"])
                    return FakeContext.run_task(c, entry, pipeline_override)

                def recognize(name, image, pipeline_override):
                    self.assertEqual(name, "搓丸子-从者强化已选页面")
                    c.hits[NETWORK_EXHAUSTED] = 1
                    return texts("selected-servant-page")

                c.run_task = Mock(side_effect=run_task)
                c.run_recognition = Mock(side_effect=recognize)
                servant = {"id": "100", "name": "BB", "images": ["f_1000.png"]}
                with patch.object(servant_runtime.os.path, "isfile", side_effect=lambda p: p == "servant_face/f_1000.png"), \
                        patch.object(servant_runtime.time, "sleep"):
                    with self.assertRaisesRegex(runtime.ExpballFailure, "网络重试.*用尽"):
                        action._find_servant(servant)
                action._controller.post_click.assert_called_once_with(120, 248)
                action._controller.post_swipe.assert_not_called()
                action._verify_selected.assert_not_called()
                action._match.assert_not_called()
                self.assertEqual(c.run_recognition.call_count, 1 if stage == "after_ready" else 0)
                self.assertEqual(c.get_hit_count(NETWORK_EXHAUSTED), 1)

    def test_servant_pipeline_ready_hook_does_not_change_new_legacy_instances(self):
        import servant_up_action as servant_runtime

        runner, _ = self.runner()
        local = servant_runtime.ExecuteServantUp()
        local._in_levelup = runner.selected_servant_page_ready
        legacy = servant_runtime.ExecuteServantUp()
        self.assertEqual(local._in_levelup, runner.selected_servant_page_ready)
        self.assertIs(legacy._in_levelup.__func__, servant_runtime.ExecuteServantUp._in_levelup)
        self.assertNotIn("_in_levelup", legacy.__dict__)
        legacy._match = Mock(return_value=None)
        self.assertIs(legacy._in_levelup(), False)
        legacy._match.assert_called_once_with(servant_runtime.TPL_LEVELUP, roi=servant_runtime.LEVELUP_ROI)

    def test_servant_face_settings_are_instance_scoped_and_legacy_hook_is_optional(self):
        import servant_up_action as servant_runtime

        for restricted in (False, True):
            with self.subTest(restricted=restricted):
                action = servant_runtime.ExecuteServantUp()
                action._context = FakeContext()
                action._controller = NS(post_click=Mock(return_value=NS(wait=Mock())),
                                        post_swipe=Mock(return_value=NS(wait=Mock())))
                action._tpl = lambda name: name
                action._px = action._py = lambda position: position
                action._match = Mock(return_value=(0.60, 120, 248))
                action._in_levelup = Mock(return_value=True)
                if restricted:
                    action._face_threshold, action._face_roi = 0.90, (60, 160, 960, 560)
                servant = {"id": "100", "name": "BB", "images": ["f_1000.png"]}
                with patch.object(servant_runtime.os.path, "isfile", side_effect=lambda p: p == "servant_face/f_1000.png"), \
                        patch.object(servant_runtime.time, "sleep"), \
                        patch.object(servant_runtime, "MAX_FIND_SERVANT_ROUND", 1):
                    self.assertEqual(action._find_servant(servant), not restricted)
                if restricted:
                    action._match.assert_called_once_with("servant_face/f_1000.png", threshold=0.0,
                                                        roi=(60, 160, 960, 560))
                else:
                    action._match.assert_called_once_with("servant_face/f_1000.png", threshold=0.0)
                self.assertEqual(action._context.tasks, [])
                if restricted:
                    action._controller.post_click.assert_not_called()
                    self.assertEqual(action._controller.post_swipe.call_count, 1)
                else:
                    action._controller.post_click.assert_called_once_with(120, 248)
                    action._controller.post_swipe.assert_not_called()

    def test_servant_callback_exhaustion_after_click_or_upgrade_never_repeats_input(self):
        import servant_up_action as servant_runtime

        for stage in ("after_face_click", "after_upgrade"):
            with self.subTest(stage=stage):
                runner, c = self.runner()
                action = servant_runtime.ExecuteServantUp()
                click = Mock(return_value=NS(wait=Mock()))
                action._context, action._controller = c, NS(post_click=click, post_swipe=Mock())
                action._network_check = runner.wait_network
                action._face_threshold, action._face_roi = 0.90, (60, 160, 960, 560)
                action._tpl = lambda name: name
                action._in_levelup = Mock(return_value=True)
                action._read_level = Mock(return_value=1)
                action._match = Mock(return_value=(0.95, 120, 248))

                def run_task(entry, pipeline_override=None):
                    # The first network wait sees a clear screen. The next
                    # input's response carries the exhausted network marker.
                    c.next_terminal = (["搓丸子网络-界面无通信弹窗"] if not c.tasks
                                       else [NETWORK_EXHAUSTED])
                    return FakeContext.run_task(c, entry, pipeline_override)

                c.run_task = Mock(side_effect=run_task)
                servant = {"id": "100", "name": "BB", "images": ["f_1000.png"]}
                with patch.object(servant_runtime.os.path, "isfile", side_effect=lambda p: p == "servant_face/f_1000.png"), \
                        patch.object(servant_runtime.time, "sleep"):
                    with self.assertRaisesRegex(runtime.ExpballFailure, "网络重试.*用尽"):
                        if stage == "after_face_click":
                            action._find_servant(servant)
                        else:
                            action._levelup_loop(servant, 90)
                action._in_levelup.assert_not_called()
                action._controller.post_swipe.assert_not_called()
                if stage == "after_face_click":
                    click.assert_called_once_with(120, 248)
                    action._match.assert_called_once_with("servant_face/f_1000.png", threshold=0.0,
                                                        roi=(60, 160, 960, 560))
                    self.assertEqual([entry for entry, _ in c.tasks], ["搓丸子网络-等待恢复"] * 2)
                else:
                    click.assert_not_called()
                    action._match.assert_not_called()
                    action._read_level.assert_called_once_with()
                    self.assertEqual([entry for entry, _ in c.tasks],
                                     ["搓丸子网络-等待恢复", "强化从者-强化一次"])

    def test_servant_failed_unknown_and_stopped_results_are_not_truthy_success(self):
        for result in ("failed", "max_round", "stopped", "unexpected", None):
            with self.subTest(result=result):
                runner, c = self.runner({"make_ball": False, "levelup": True,
                                         "level_servant_id": "100", "target_level": 90})
                runner.return_main = Mock()
                for entry in ("强化从者-导航到强化界面", "强化从者-筛选准备", "强化从者-强化一次"):
                    c.add(entry)
                c.clone = lambda: NS(override_pipeline=lambda overrides: True)
                action = NS(_init_dirs=Mock(), _init_scale=Mock(),
                            _resolve_servant=Mock(return_value={"id": "100", "class": "saber", "rarity": 1}),
                            _levelup_servant=Mock(return_value=result))
                with patch.dict(sys.modules, {"servant_up_action": NS(ExecuteServantUp=lambda: action)}):
                    with self.assertRaises(runtime.ExpballFailure): runner.level_servant()
                runner.return_main.assert_called_once_with()
                action._levelup_servant.assert_called_once()

    def test_servant_stop_state_wins_even_when_inner_result_says_target_reached(self):
        runner, c = self.runner({"make_ball": False, "levelup": True,
                                 "level_servant_id": "100", "target_level": 90})
        runner.return_main = Mock()
        for entry in ("强化从者-导航到强化界面", "强化从者-筛选准备", "强化从者-强化一次"):
            c.add(entry)
        c.clone = lambda: NS(override_pipeline=lambda overrides: True)
        def stop(*args):
            c.tasker.stopping = True
            return "target_reached"
        action = NS(_init_dirs=Mock(), _init_scale=Mock(),
                    _resolve_servant=Mock(return_value={"id": "100", "class": "saber", "rarity": 1}),
                    _levelup_servant=Mock(side_effect=stop))
        with patch.dict(sys.modules, {"servant_up_action": NS(ExecuteServantUp=lambda: action)}):
            with self.assertRaises(runtime.ExpballStopped): runner.level_servant()
        runner.return_main.assert_called_once_with()
        action._levelup_servant.assert_called_once()

    def test_servant_network_exhaustion_wins_over_inner_business_success(self):
        runner, c = self.runner({"make_ball": False, "levelup": True,
                                 "level_servant_id": "100", "target_level": 90})
        runner.return_main = Mock()
        for entry in ("强化从者-导航到强化界面", "强化从者-筛选准备", "强化从者-强化一次"):
            c.add(entry)
        c.clone = lambda: NS(override_pipeline=lambda overrides: True)

        def exhaust(*args):
            c.hits[NETWORK_EXHAUSTED] = 1
            return "target_reached"

        action = NS(_init_dirs=Mock(), _init_scale=Mock(),
                    _resolve_servant=Mock(return_value={"id": "100", "class": "saber", "rarity": 1}),
                    _levelup_servant=Mock(side_effect=exhaust))
        with patch.dict(sys.modules, {"servant_up_action": NS(ExecuteServantUp=lambda: action)}):
            with self.assertRaisesRegex(runtime.ExpballFailure, "网络重试.*用尽") as failure:
                runner.level_servant()
        self.assertIs(type(failure.exception), runtime.ExpballFailure)
        self.assertEqual(runner.reason, "not_started")
        runner.return_main.assert_called_once_with()
        action._levelup_servant.assert_called_once()

    def test_capacity_recovery_uses_new_confirmation_count_after_inner_reset(self):
        runner, c = self.runner({"make_ball": False, "levelup": True,
                                 "level_servant_id": "100", "target_level": 90})
        c.hits["强化从者-强化二次确认"] = 99
        def inner_without_progress():
            c.hits["强化从者-强化二次确认"] = 0
            return True
        runner.level_servant = Mock(side_effect=inner_without_progress)
        self.assertFalse(runner.recover_capacity("servant_full"))
        c.hits["强化从者-强化二次确认"] = 99
        def inner_with_progress():
            c.hits["强化从者-强化二次确认"] = 1
            return True
        runner.level_servant = Mock(side_effect=inner_with_progress)
        self.assertTrue(runner.recover_capacity("servant_full"))

    def test_unknown_finished_level_is_not_counted_or_marked(self):
        runner, _ = self.runner()
        complete = CardObservation("10", 1, 50, 50, False, False)
        world = CeGameModel(runner, finished=[complete], unknown_finished_level=True)
        world.base = complete
        with self.assertRaisesRegex(runtime.ExpballFailure, "等级"):
            runner.finish_ball(complete)
        self.assertFalse(world.called("搓丸子-标记当前候选"))
        self.assertEqual(runner.completed, 0)

    def test_unconfirmed_lock_never_increments_finished_count(self):
        runner, _ = self.runner()
        complete = CardObservation("10", 1, 50, 50, False, False)
        world = CeGameModel(runner, finished=[complete], unverified_lock=True)
        world.base = complete
        with self.assertRaisesRegex(runtime.ExpballFailure, "锁定和标记未确认"):
            runner.finish_ball(complete)
        self.assertEqual(runner.completed, 0)


if __name__ == "__main__":
    unittest.main()
