import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent" / "custom"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

from bond_completion_action import CompleteBondFormation, _truthy  # noqa: E402
from formation_action import (  # noqa: E402
    AutoFormationFromChaldea,
    ValidateFormationFromChaldea,
)


class BondCompletionOptionTest(unittest.TestCase):
    def test_battle_chaldea_formation_mode_defaults_to_manual_and_routes_safely(self):
        root = Path(__file__).resolve().parents[1]
        with (root / "assets/options/Chaldea导入手动输入.json").open(
            encoding="utf-8"
        ) as stream:
            options = json.load(stream)["option"]
        with (root / "assets/resource/base/pipeline/自动编队.json").open(
            encoding="utf-8"
        ) as stream:
            pipeline = json.load(stream)

        enabled = next(
            case
            for case in options["原生自动战斗使用Chaldea队伍"]["cases"]
            if case["name"] == "Yes"
        )
        self.assertEqual(
            enabled["option"], ["Chaldea导入手动输入", "Chaldea编队方式"]
        )

        mode = options["Chaldea编队方式"]
        self.assertEqual(mode["default"], "手动")
        cases = {case["name"]: case for case in mode["cases"]}
        self.assertNotIn("option", cases["手动"])
        self.assertEqual(
            cases["手动"]["pipeline_override"]["进本-编队处理"]["next"],
            ["执行手动编队检查"],
        )
        self.assertEqual(
            cases["自动"]["pipeline_override"]["进本-编队处理"]["next"],
            ["执行自动编队"],
        )
        self.assertEqual(
            cases["自动"]["option"],
            [
                "自动编队助战替代",
                "自动编队礼装开关",
                "自动补齐从者和羁绊礼装",
            ],
        )
        equip_on = next(case for case in options["自动编队礼装开关"]["cases"]
                        if case["name"] == "Yes")
        self.assertEqual(equip_on["option"], ["自动编队礼装缺失处理"])
        self.assertEqual(options["自动编队礼装缺失处理"]["default"], "查找非满破礼装")
        self.assertEqual(pipeline["执行手动编队检查"]["on_error"], [])

    def test_local_inventory_options_default_to_legacy_realtime_mode(self):
        root = Path(__file__).resolve().parents[1]
        with (root / "assets/options/Chaldea羁绊补齐.json").open(
            encoding="utf-8"
        ) as stream:
            options = json.load(stream)["option"]
        with (root / "assets/resource/base/pipeline/羁绊补齐自动编队.json").open(
            encoding="utf-8"
        ) as stream:
            pipeline = json.load(stream)

        self.assertEqual(options["使用本地从者库"]["default_case"], "No")
        self.assertEqual(options["使用本地礼装库"]["default_case"], "No")
        attach = pipeline["执行羁绊补齐自动编队"]["attach"]
        self.assertIs(attach["use_local_servant_inventory"], False)
        self.assertIs(attach["use_local_equip_inventory"], False)
        self.assertEqual(attach["quest_type"], "")
        self.assertEqual(attach["grand_class"], "")

    def test_grand_task_and_class_options_inject_bond_completion_context(self):
        root = Path(__file__).resolve().parents[1]
        with (root / "assets/tasks/冠位戴冠战.json").open(
            encoding="utf-8"
        ) as stream:
            task = json.load(stream)["task"][0]
        with (root / "assets/options/选择冠位职介.json").open(
            encoding="utf-8"
        ) as stream:
            cases = json.load(stream)["option"]["选择冠位职介"]["cases"]

        self.assertEqual(
            task["pipeline_override"]["执行羁绊补齐自动编队"]["attach"],
            {"quest_type": "grand"},
        )
        expected = {
            "剑": "saber",
            "弓": "archer",
            "枪": "lancer",
            "骑": "rider",
            "术": "caster",
            "杀": "assassin",
            "狂": "berserker",
            "EX1": "ex1",
            "EX2": "ex2",
        }
        actual = {
            case["name"]: case["pipeline_override"]["执行羁绊补齐自动编队"]
            ["attach"]["grand_class"]
            for case in cases
        }
        self.assertEqual(actual, expected)

    def test_grand_context_requires_a_known_class(self):
        self.assertEqual(
            CompleteBondFormation._parse_quest_context(
                {"quest_type": " GRAND ", "grand_class": " EX1 "}
            ),
            ("grand", "ex1"),
        )
        with self.assertRaisesRegex(ValueError, "戴冠战职介无效"):
            CompleteBondFormation._parse_quest_context({"quest_type": "grand"})

    def test_grand_servant_candidates_match_regular_or_extra_class(self):
        action = CompleteBondFormation.__new__(CompleteBondFormation)
        action.expected = []
        action.unspecified_servants_by_slot = {}
        action.added_servants = {}
        action.unavailable_servants = set()
        action._servant_templates = lambda _servant_id, for_list=True: [object()]
        action.servant_database = {
            class_name: {
                "id": class_name,
                "name": class_name,
                "class": class_name,
                "rarity": 5,
                "cost": 12,
                "bond": {"tags": [f"{class_name}_class"]},
            }
            for class_name in (
                "saber",
                "archer",
                "berserker",
                "ruler",
                "alterEgo",
                "foreigner",
                "shielder",
                "unBeast",
            )
        }

        action.quest_type = "grand"
        action.grand_class = "saber"
        self.assertEqual(
            {item["class"] for item in action._servant_candidates(5)},
            {"saber"},
        )

        for extra_group in ("ex1", "ex2"):
            action.grand_class = extra_group
            self.assertEqual(
                {item["class"] for item in action._servant_candidates(5)},
                {"ruler", "alterEgo", "foreigner", "shielder", "unBeast"},
            )

        action.quest_type = ""
        action.grand_class = ""
        self.assertEqual(len(action._servant_candidates(5)), 8)

    def test_valid_local_inventory_document_returns_unique_ids(self):
        document = {
            "schema_version": 1,
            "kind": "servants",
            "scan_complete": True,
            "count": 2,
            "servants": [{"id": "10"}, {"id": 20}],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "player_servants.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            ids, loaded = CompleteBondFormation._read_player_inventory(
                path, "servants", "servants"
            )

        self.assertEqual(ids, {"10", "20"})
        self.assertEqual(loaded["count"], 2)

    def test_incomplete_local_inventory_document_is_rejected(self):
        document = {
            "schema_version": 1,
            "kind": "equips",
            "scan_complete": False,
            "count": 1,
            "equips": [{"id": "10"}],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "player_equips.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "scan_complete"):
                CompleteBondFormation._read_player_inventory(path, "equips", "equips")

    def test_local_equip_inventory_is_a_candidate_whitelist(self):
        action = CompleteBondFormation.__new__(CompleteBondFormation)
        action.bond_equips = [{"id": "10"}, {"id": "20"}, {"id": "30"}]
        action.unavailable_equips = {"30"}
        action.local_equip_inventory_active = True
        action.local_equip_ids = {"20", "30"}

        self.assertEqual(action._candidate_bond_equips(), [{"id": "20"}])

    def test_disabled_local_equip_inventory_keeps_legacy_candidates(self):
        action = CompleteBondFormation.__new__(CompleteBondFormation)
        action.bond_equips = [{"id": "10"}, {"id": "20"}, {"id": "30"}]
        action.unavailable_equips = {"30"}
        action.local_equip_inventory_active = False
        action.local_equip_ids = {"20"}

        self.assertEqual(
            action._candidate_bond_equips(), [{"id": "10"}, {"id": "20"}]
        )

    def test_legacy_equip_mode_still_runs_preflight_and_restores_servant_list(self):
        action = CompleteBondFormation.__new__(CompleteBondFormation)
        action.available_equips = set()
        action.unavailable_equips = set()
        calls = []
        action._leave_servant_select = lambda: calls.append("leave") or True
        action._ensure_plan_equips_available = (
            lambda equips: calls.append(("preflight", [item["id"] for item in equips]))
            or "available"
        )
        action._enter_servant_select_new = (
            lambda slot: calls.append(("enter", slot)) or True
        )
        action._filter_servant_rarity = (
            lambda rarity: calls.append(("rarity", rarity)) or True
        )

        result = action._preflight_servant_plan_equips(
            [{"id": "10", "name": "待预检礼装"}], 2, 5
        )

        self.assertEqual(result, "available")
        self.assertEqual(
            calls,
            ["leave", ("preflight", ["10"]), ("enter", 2), ("rarity", 5)],
        )

    def test_missing_local_servant_target_collects_all_seen_candidates(self):
        action = CompleteBondFormation.__new__(CompleteBondFormation)
        action._run_pipeline = lambda node: True
        frames = iter([
            ({"20": (0.95, (1, 1), "20.png")}, object()),
            ({"30": (0.94, (2, 2), "30.png")}, object()),
        ])
        action._stable_servant_hits = lambda candidates: next(frames)

        with patch("bond_completion_action.MAX_SCAN_SWIPES", 1):
            result = action._select_scanned_servant(
                {"id": "10"}, [{"id": "10"}, {"id": "20"}, {"id": "30"}]
            )

        self.assertEqual(result, "not_found")
        self.assertEqual(action.last_servant_search_seen, {"20", "30"})

    def test_navigation_click_targets_and_unequip_timeouts(self):
        root = Path(__file__).resolve().parents[1]
        with (root / "assets/resource/base/pipeline/自动编队.json").open(
            encoding="utf-8"
        ) as stream:
            auto_pipeline = json.load(stream)
        with (root / "assets/resource/base/pipeline/羁绊补齐自动编队.json").open(
            encoding="utf-8"
        ) as stream:
            bond_pipeline = json.load(stream)

        self.assertEqual(
            auto_pipeline["自动编队-打开配置"]["action"]["param"]["target"],
            [222, 678, 1, 1],
        )
        self.assertEqual(
            auto_pipeline["清空二次确认"]["action"]["param"]["target"],
            [839, 601, 1, 1],
        )
        self.assertEqual(auto_pipeline["清空二次确认"]["timeout"], 5000)
        self.assertEqual(auto_pipeline["清空二次确认"]["on_error"], [])
        self.assertEqual(
            bond_pipeline["羁绊补齐-打开配置"]["action"]["param"]["target"],
            [222, 678, 1, 1],
        )
        self.assertEqual(bond_pipeline["羁绊补齐-卸下当前礼装"]["timeout"], 6000)
        self.assertEqual(bond_pipeline["羁绊补齐-确认卸下礼装"]["timeout"], 6000)
        detail_open = bond_pipeline["羁绊补齐-打开从者详情"]
        self.assertEqual(detail_open["action"]["type"], "LongPress")
        self.assertEqual(detail_open["action"]["param"]["duration"], 3000)
        self.assertEqual(detail_open["post_delay"], 3000)
        self.assertEqual(
            bond_pipeline["羁绊补齐-关闭从者详情"]["action"]["param"]["key"],
            [111],
        )

    def test_new_servant_bond_ocr_accepts_digits_and_always_closes_detail(self):
        class Result:
            text = "12,345"

        class Detail:
            all_results = [Result()]

        class Context:
            def __init__(self):
                self.override = None

            def override_pipeline(self, override):
                self.override = override

            @staticmethod
            def run_recognition_direct(*_args):
                return Detail()

        action = CompleteBondFormation.__new__(CompleteBondFormation)
        action.context = Context()
        action._slot_roi = lambda slot: (10, 20, 40, 40)
        action._focus_user = lambda *_args: None
        action._shot = lambda: object()
        action._scale_roi = lambda roi: roi
        state = {"detail": False}
        calls = []

        def run_pipeline(node):
            calls.append(node)
            state["detail"] = node == "羁绊补齐-打开从者详情"
            return True

        action._run_pipeline = run_pipeline
        action._in_formation_edit = lambda: not state["detail"]
        action._wait_for = lambda predicate, timeout: predicate() or predicate()

        status = action._check_new_servant_bond(
            2, {"id": "10", "name": "候选从者"}
        )

        self.assertEqual(status, "available")
        self.assertEqual(calls, ["羁绊补齐-打开从者详情", "羁绊补齐-关闭从者详情"])
        node = action.context.override["羁绊补齐-打开从者详情"]
        self.assertEqual(node["action"]["param"]["target"], [30, 40, 1, 1])
        self.assertEqual(node["action"]["param"]["duration"], 3000)

    def test_new_servant_detail_waits_past_stale_formation_frames(self):
        class Result:
            text = "123"

        class Detail:
            all_results = [Result()]

        class Context:
            @staticmethod
            def override_pipeline(_override):
                return None

            @staticmethod
            def run_recognition_direct(*_args):
                return Detail()

        action = CompleteBondFormation.__new__(CompleteBondFormation)
        action.context = Context()
        action.servant_detail_open = False
        action._slot_roi = lambda slot: (0, 0, 10, 10)
        action._focus_user = lambda *_args: None
        action._shot = lambda: object()
        action._scale_roi = lambda roi: roi
        state = {"page": "detail", "detail_checks": 0}

        def run_pipeline(node):
            if node == "羁绊补齐-关闭从者详情":
                state["page"] = "formation"
            return True

        def in_formation_edit():
            if state["page"] == "formation":
                return True
            state["detail_checks"] += 1
            return state["detail_checks"] < 3

        def wait_for(predicate, _timeout):
            for _ in range(5):
                if predicate():
                    return True
            return False

        action._run_pipeline = run_pipeline
        action._in_formation_edit = in_formation_edit
        action._wait_for = wait_for

        status = action._check_new_servant_bond(
            4, {"id": "304800", "name": "妖精骑士兰斯洛特"}
        )

        self.assertEqual(status, "available")
        self.assertEqual(state["detail_checks"], 3)
        self.assertFalse(action.servant_detail_open)

    def test_new_servant_detail_uses_confirmed_ui_when_pipeline_reports_failure(self):
        class Result:
            text = "123"

        class Detail:
            all_results = [Result()]

        class Context:
            @staticmethod
            def override_pipeline(_override):
                return None

            @staticmethod
            def run_recognition_direct(*_args):
                return Detail()

        action = CompleteBondFormation.__new__(CompleteBondFormation)
        action.context = Context()
        action.servant_detail_open = False
        action._slot_roi = lambda slot: (0, 0, 10, 10)
        action._focus_user = lambda *_args: None
        action._shot = lambda: object()
        action._scale_roi = lambda roi: roi
        state = {"detail": False}

        def run_pipeline(node):
            state["detail"] = node == "羁绊补齐-打开从者详情"
            return node != "羁绊补齐-打开从者详情"

        def wait_for(predicate, _timeout):
            for _ in range(4):
                if predicate():
                    return True
            return False

        action._run_pipeline = run_pipeline
        action._in_formation_edit = lambda: not state["detail"]
        action._wait_for = wait_for

        self.assertEqual(
            action._check_new_servant_bond(
                0, {"id": "10", "name": "候选从者"}
            ),
            "available",
        )
        self.assertFalse(action.servant_detail_open)

    def test_new_servant_slot_verification_retries_until_match(self):
        action = CompleteBondFormation.__new__(CompleteBondFormation)
        template = np.zeros((158, 158, 3), dtype=np.uint8)
        action._servant_templates = lambda servant_id, for_list: [("face.png", template)]
        action._shot = lambda: object()
        scores = iter((0.65, 0.74, 0.81))
        action._match_servant = lambda image, templates, roi: (
            next(scores), (0, 0), "face.png"
        )
        action._slot_roi = lambda slot: (0, 0, 10, 10)

        def wait_for(predicate, _timeout):
            for _ in range(5):
                if predicate():
                    return True
            return False

        action._wait_for = wait_for

        self.assertTrue(
            action._verify_servant_slot(
                4, {"id": "304800", "name": "妖精骑士兰斯洛特"}
            )
        )

    def test_new_servant_slot_verification_retries_directly_with_80px_crop(self):
        action = CompleteBondFormation.__new__(CompleteBondFormation)
        template = np.zeros((158, 158, 3), dtype=np.uint8)
        action._servant_templates = lambda servant_id, for_list: [("face.png", template)]
        action._shot = lambda: object()
        action._slot_roi = lambda slot: (0, 0, 10, 10)
        heights = []

        def match_servant(image, templates, roi):
            height = templates[0][1].shape[0]
            heights.append(height)
            score = 0.98 if height == 78 else 0.70
            return score, (0, 0), "face.png"

        def wait_for(predicate, _timeout):
            return any(predicate() for _ in range(2))

        action._match_servant = match_servant
        action._wait_for = wait_for

        self.assertTrue(
            action._verify_servant_slot(
                3, {"id": "2501000", "name": "雅克·德·莫莱"}
            )
        )
        self.assertEqual(heights, [158, 158, 78])

    def test_new_servant_slot_verification_fails_when_cropped_template_is_insufficient(self):
        action = CompleteBondFormation.__new__(CompleteBondFormation)
        template = np.zeros((158, 158, 3), dtype=np.uint8)
        action._servant_templates = lambda servant_id, for_list: [("face.png", template)]
        action._shot = lambda: object()
        action._slot_roi = lambda slot: (0, 0, 10, 10)
        action._match_servant = lambda image, templates, roi: (
            0.77, (0, 0), "face.png"
        )
        action._wait_for = lambda predicate, _timeout: any(
            predicate() for _ in range(2)
        )

        self.assertFalse(
            action._verify_servant_slot(
                3, {"id": "2501000", "name": "雅克·德·莫莱"}
            )
        )

    def test_final_state_accepts_added_servant_with_80px_crop(self):
        action = CompleteBondFormation.__new__(CompleteBondFormation)
        template = np.zeros((158, 158, 3), dtype=np.uint8)
        action.expected = []
        action.added_servants = {
            3: {"id": "2501000", "name": "雅克·德·莫莱"}
        }
        action.unspecified_servants_by_slot = {}
        action.locked_unspecified_equips = {}
        action.added_equips = {}
        action._servant_templates = lambda servant_id, for_list: [
            ("face.png", template)
        ]
        action._slot_roi = lambda slot: (0, 0, 10, 10)
        heights = []

        def match_servant(image, templates, roi):
            height = templates[0][1].shape[0]
            heights.append(height)
            score = 0.98 if height == 78 else 0.70
            return score, (0, 0), "face.png"

        action._match_servant = match_servant

        self.assertTrue(action._verify_final_state_once(object()))
        self.assertEqual(heights, [158, 78])

    def test_final_state_rejects_wrong_servant_after_80px_crop(self):
        action = CompleteBondFormation.__new__(CompleteBondFormation)
        template = np.zeros((158, 158, 3), dtype=np.uint8)
        action.expected = []
        action.added_servants = {
            3: {"id": "2501000", "name": "雅克·德·莫莱"}
        }
        action.unspecified_servants_by_slot = {}
        action.locked_unspecified_equips = {}
        action.added_equips = {}
        action._servant_templates = lambda servant_id, for_list: [
            ("face.png", template)
        ]
        action._slot_roi = lambda slot: (0, 0, 10, 10)
        action._match_servant = lambda image, templates, roi: (
            0.77, (0, 0), "face.png"
        )

        self.assertFalse(action._verify_final_state_once(object()))

    def test_unspecified_servants_get_two_full_identification_rounds(self):
        action = CompleteBondFormation.__new__(CompleteBondFormation)
        action._shot = lambda: object()
        identified = {3: {"id": "2300500", "name": "Archetype：Earth"}}
        calls = []

        def identify(image, slots, report_errors):
            calls.append((tuple(slots), report_errors))
            return identified

        action._identify_unspecified_servants = identify

        with patch("bond_completion_action.time.sleep", return_value=None):
            self.assertEqual(
                action._identify_unspecified_servants_stable([3]), identified
            )
        self.assertEqual(calls, [((3,), False), ((3,), False)])

    def test_cost_verification_retries_until_two_consecutive_values_match(self):
        action = CompleteBondFormation.__new__(CompleteBondFormation)
        readings = iter((None, (47, 117), (47, 117)))
        action._ocr_cost_once = lambda: next(readings)

        with patch("bond_completion_action.time.sleep", return_value=None):
            self.assertEqual(action._read_cost_consistent(), (47, 117))

    def test_unspecified_servant_identity_requires_two_matching_results(self):
        action = CompleteBondFormation.__new__(CompleteBondFormation)
        first = {4: {"id": "10"}}
        stable = {4: {"id": "20"}}
        results = iter((first, stable, stable))
        action._shot = lambda: object()
        action._identify_unspecified_servants = (
            lambda image, slots, report_errors=False: next(results)
        )

        def wait_for(predicate, _timeout):
            for _ in range(5):
                if predicate():
                    return True
            return False

        action._wait_for = wait_for

        self.assertEqual(
            action._identify_unspecified_servants_stable([4]), stable
        )

    def test_equip_slot_verification_retries_after_low_score(self):
        action = CompleteBondFormation.__new__(CompleteBondFormation)
        scores = iter((0.60, 0.79, 0.85))
        action._shot = lambda: object()
        action._match_equip_id = lambda image, equip_id, slot: (
            next(scores), (0, 0)
        )

        def wait_for(predicate, _timeout):
            for _ in range(5):
                if predicate():
                    return True
            return False

        action._wait_for = wait_for

        verified, best = action._wait_for_equip_slot_match(4, "9401970")

        self.assertTrue(verified)
        self.assertEqual(best, 0.85)

    def test_final_state_verification_retries_whole_snapshot(self):
        action = CompleteBondFormation.__new__(CompleteBondFormation)
        results = iter((False, False, True))
        action._shot = lambda: object()
        action._verify_final_state_once = lambda image: next(results)

        def wait_for(predicate, _timeout):
            for _ in range(5):
                if predicate():
                    return True
            return False

        action._wait_for = wait_for

        self.assertTrue(action._verify_final_state())

    def test_auto_formation_layout_requires_two_consecutive_matching_frames(self):
        action = AutoFormationFromChaldea.__new__(AutoFormationFromChaldea)
        action.context = SimpleNamespace(tasker=SimpleNamespace(stopping=False))
        stale = [{"kind": "OTHER", "svt_id": None}]
        stable = [{"kind": "LOCAL", "svt_id": "10"}]
        frames = iter((stale, stable, stable))
        action._detect_slots = lambda: next(frames)

        with patch("formation_action.time.sleep", return_value=None):
            self.assertEqual(action._detect_slots_stable(), stable)

    def test_grand_restricted_empty_slot_ignores_colored_lower_text(self):
        action = AutoFormationFromChaldea.__new__(AutoFormationFromChaldea)
        action.sx = 1.0
        action.sy = 1.0
        roi = (854, 160, 187, 276)
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        x, y, width, height = roi
        image[y:y + height, x:x + width] = 128
        # 模拟戴冠战空槽下部的黄色职阶限制文字与装饰，使整卡统计不再满足
        # 旧判定；上方无遮挡灰色卡面仍应可靠地判为空槽。
        image[y + 160:y + 250, x:x + width] = (0, 210, 245)

        self.assertTrue(action._is_empty_slot(image, roi))

    def test_colored_servant_card_is_not_treated_as_empty_by_core_probe(self):
        action = AutoFormationFromChaldea.__new__(AutoFormationFromChaldea)
        action.sx = 1.0
        action.sy = 1.0
        roi = (854, 160, 187, 276)
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        x, y, width, height = roi
        gradient = np.linspace(20, 230, width, dtype=np.uint8)
        card = np.zeros((height, width, 3), dtype=np.uint8)
        card[:, :, 0] = gradient
        card[:, :, 1] = np.flip(gradient)
        card[:, :, 2] = 170
        image[y:y + height, x:x + width] = card

        self.assertFalse(action._is_empty_slot(image, roi))

    def test_shared_wait_requires_two_consecutive_positive_frames(self):
        action = AutoFormationFromChaldea.__new__(AutoFormationFromChaldea)
        action.context = SimpleNamespace(tasker=SimpleNamespace(stopping=False))
        states = iter((False, True, False, True, True))
        calls = {"count": 0}

        def predicate():
            calls["count"] += 1
            return next(states)

        with patch("formation_action.time.sleep", return_value=None):
            self.assertTrue(action._wait_for(predicate, 1.0))
        self.assertEqual(calls["count"], 5)

    def test_manual_formation_accepts_same_servants_in_different_positions(self):
        action = ValidateFormationFromChaldea.__new__(ValidateFormationFromChaldea)
        action.expected = [
            {"kind": "LOCAL", "svt_id": 10},
            {"kind": "LOCAL", "svt_id": 20},
            {"kind": "EMPTY", "svt_id": None},
        ]
        action._fail = lambda _message: False

        self.assertTrue(action._manual_identities_match([
            {"kind": "LOCAL", "svt_id": 20},
            {"kind": "EMPTY", "svt_id": None},
            {"kind": "LOCAL", "svt_id": 10},
        ]))

    def test_manual_formation_rejects_unknown_or_wrong_servants(self):
        action = ValidateFormationFromChaldea.__new__(ValidateFormationFromChaldea)
        action.expected = [
            {"kind": "LOCAL", "svt_id": 10},
            {"kind": "LOCAL", "svt_id": 20},
            {"kind": "EMPTY", "svt_id": None},
        ]
        failures = []
        action._fail = lambda message: failures.append(message) is None

        self.assertFalse(action._manual_identities_match([
            {"kind": "LOCAL", "svt_id": 10},
            {"kind": "OTHER", "svt_id": None},
            {"kind": "EMPTY", "svt_id": None},
        ]))
        self.assertIn("manual_formation_servant_mismatch", failures[0])

    def test_manual_formation_ignores_extra_servant_in_unspecified_slot(self):
        action = ValidateFormationFromChaldea.__new__(ValidateFormationFromChaldea)
        action.expected = [
            {"kind": "LOCAL", "svt_id": 10},
            {"kind": "EMPTY", "svt_id": None},
        ]
        action._fail = lambda _message: False

        self.assertTrue(action._manual_identities_match([
            {"kind": "LOCAL", "svt_id": 10},
            {"kind": "OTHER", "svt_id": None},
        ]))

    def test_manual_formation_requires_duplicate_servant_count(self):
        action = ValidateFormationFromChaldea.__new__(ValidateFormationFromChaldea)
        action.expected = [
            {"kind": "LOCAL", "svt_id": 10},
            {"kind": "LOCAL", "svt_id": 10},
            {"kind": "EMPTY", "svt_id": None},
        ]
        failures = []
        action._fail = failures.append

        self.assertFalse(action._manual_identities_match([
            {"kind": "LOCAL", "svt_id": 10},
            {"kind": "LOCAL", "svt_id": 20},
            {"kind": "OTHER", "svt_id": None},
        ]))
        self.assertIn("缺少={10: 1}", failures[0])

    def test_new_servant_bond_ocr_treats_empty_or_non_digit_as_full(self):
        class Context:
            def __init__(self, text):
                self.text = text

            @staticmethod
            def override_pipeline(_override):
                return None

            def run_recognition_direct(self, *_args):
                result = type("Result", (), {"text": self.text})()
                return type("Detail", (), {"all_results": [result] if self.text else []})()

        for text in ("", "MAX"):
            with self.subTest(text=text):
                action = CompleteBondFormation.__new__(CompleteBondFormation)
                action.context = Context(text)
                action._slot_roi = lambda slot: (0, 0, 10, 10)
                action._focus_user = lambda *_args: None
                action._shot = lambda: object()
                action._scale_roi = lambda roi: roi
                state = {"detail": False}

                def run_pipeline(node):
                    state["detail"] = node == "羁绊补齐-打开从者详情"
                    return True

                action._run_pipeline = run_pipeline
                action._in_formation_edit = lambda: not state["detail"]
                action._wait_for = lambda predicate, timeout: predicate() or predicate()

                self.assertEqual(
                    action._check_new_servant_bond(
                        0, {"id": "10", "name": "候选从者"}
                    ),
                    "full",
                )

    def test_new_servant_bond_ocr_ignores_one_stale_empty_frame(self):
        texts = iter(("", "123", "123"))

        class Context:
            @staticmethod
            def override_pipeline(_override):
                return None

            @staticmethod
            def run_recognition_direct(*_args):
                text = next(texts)
                result = type("Result", (), {"text": text})()
                return type("Detail", (), {"all_results": [result] if text else []})()

        action = CompleteBondFormation.__new__(CompleteBondFormation)
        action.context = Context()
        action._slot_roi = lambda slot: (0, 0, 10, 10)
        action._focus_user = lambda *_args: None
        action._shot = lambda: object()
        action._scale_roi = lambda roi: roi
        state = {"detail": False}

        def run_pipeline(node):
            state["detail"] = node == "羁绊补齐-打开从者详情"
            return True

        def wait_for(predicate, _timeout):
            for _ in range(5):
                if predicate():
                    return True
            return False

        action._run_pipeline = run_pipeline
        action._in_formation_edit = lambda: not state["detail"]
        action._wait_for = wait_for

        self.assertEqual(
            action._check_new_servant_bond(
                0, {"id": "10", "name": "候选从者"}
            ),
            "available",
        )

    def test_full_bond_servant_is_excluded_before_selecting_next_candidate(self):
        action = CompleteBondFormation.__new__(CompleteBondFormation)
        candidates = [
            {"id": "10", "name": "已满", "cost": 16},
            {"id": "20", "name": "未满", "cost": 16},
        ]
        action.servant_list_prepared = False
        action.replaceable_slots = set()
        action.unspecified_servants_by_slot = {}
        action.rarity_order = [5]
        action.owned_servants_by_rarity = {5: {"10", "20"}}
        action.local_servant_inventory_active = True
        action.unavailable_servants = set()
        action.available_equips = set()
        action.unavailable_equips = set()
        action.max_cost = 117
        action.used_cost = 0
        action.bond_base = 1200
        action.empty_equip_slots = []
        action.truly_empty_servant_slots = {0}
        action.fixed_equips = []
        action.added_servants = {}
        action.local_templates = {}
        action._enter_servant_select_new = lambda slot: True
        action._leave_servant_select = lambda: True
        action._filter_servant_rarity = lambda rarity: True
        action._servant_candidates = lambda rarity: candidates
        action._candidate_bond_equips = lambda: []
        action._preflight_servant_plan_equips = lambda equips, slot, rarity: "available"
        selected = []
        action._select_scanned_servant = (
            lambda servant, rarity_candidates: selected.append(str(servant["id"]))
            or "selected"
        )
        action._verify_servant_slot = lambda slot, servant: True
        action._check_new_servant_bond = (
            lambda slot, servant: "full" if str(servant["id"]) == "10" else "available"
        )
        action._read_cost_consistent = lambda: (16, 117)
        action._servant_templates = lambda servant_id, for_list: []
        action._shot = lambda: object()
        action._is_empty_equip_slot = lambda image, slot: False
        action._focus_user = lambda *_args: None

        def fake_rank(servants, *_args):
            return [
                {
                    "servant": servant,
                    "plan": SimpleNamespace(score=100, equips=[]),
                    "matched_equips": 0,
                }
                for servant in servants
            ]

        with (
            patch("bond_completion_action.rank_servants", side_effect=fake_rank),
            patch("bond_completion_action.team_bond_score", return_value=0),
        ):
            result = action._fill_servants([0], [])

        self.assertEqual(selected, ["10", "20"])
        self.assertEqual(action.unavailable_servants, {"10"})
        self.assertEqual(result[0]["id"], "20")
        self.assertEqual(action.added_servants[0]["id"], "20")

    def test_boolean_option_values(self):
        self.assertTrue(_truthy(True))
        self.assertTrue(_truthy("是"))
        self.assertFalse(_truthy(False))
        self.assertFalse(_truthy("否"))

    def test_preserved_unspecified_servants_join_current_team(self):
        action = CompleteBondFormation.__new__(CompleteBondFormation)
        action.expected = [
            {"kind": "LOCAL", "svt_id": 1, "slot": 0},
            {"kind": "EMPTY", "svt_id": None, "slot": 1},
        ]
        action.servant_database = {
            "1": {"id": "1", "name": "固定从者", "bond": {"tags": ["all"]}}
        }
        action.unspecified_servants_by_slot = {
            1: {"id": "2", "name": "其他从者", "slot": 1, "bond": {"tags": ["evil"]}}
        }
        action.added_servants = {}

        current = action._current_known_servants()

        self.assertEqual([(item["id"], item["slot"]) for item in current], [("1", 0), ("2", 1)])

    def test_clear_unspecified_equip_releases_cost_and_slot(self):
        action = CompleteBondFormation.__new__(CompleteBondFormation)
        action.expected = [{"kind": "EMPTY", "equip_id": None}]
        action.used_cost = 30
        action.max_cost = 117

        def unequip(slot):
            self.assertEqual(slot, 0)
            action.used_cost -= 9
            return True

        action._unequip_slot = unequip
        equip = {
            "id": "10",
            "name": "旧礼装",
            "bond": {"bonus_type": "percent", "bonus": 5, "target": "all"},
        }

        result = action._clear_unspecified_equips(
            [{"kind": "OTHER"}], [], [], {0: equip}
        )

        self.assertEqual(result, ([0], [], {}))
        self.assertEqual(action.used_cost, 21)

    def test_identify_unspecified_servant_requires_clear_winner(self):
        action = CompleteBondFormation.__new__(CompleteBondFormation)
        action.expected = [{"kind": "EMPTY", "svt_id": None}]
        action.modify_unspecified_servants = True
        action.servant_database = {
            "1": {"id": "1", "name": "目标", "cost": 16, "bond": {"tags": ["lawful"]}},
            "2": {"id": "2", "name": "次选", "cost": 12, "bond": {"tags": ["evil"]}},
        }
        action._servant_templates = lambda servant_id, for_list: [
            (f"{servant_id}.png", servant_id)
        ]
        action._slot_roi = lambda slot: (slot, 0, 1, 1)
        action._match_servant = lambda image, templates, roi: (
            0.91 if templates[0][1] == "1" else 0.60,
            (0, 0),
            templates[0][0],
        )

        identified = action._identify_unspecified_servants(object(), [0])

        self.assertEqual(identified[0]["id"], "1")
        self.assertEqual(identified[0]["slot"], 0)

    def test_identify_unspecified_servant_rejects_ambiguous_match(self):
        action = CompleteBondFormation.__new__(CompleteBondFormation)
        action.expected = [{"kind": "EMPTY", "svt_id": None}]
        action.modify_unspecified_servants = False
        action.servant_database = {
            "1": {"id": "1", "name": "候选一", "bond": {"tags": ["lawful"]}},
            "2": {"id": "2", "name": "候选二", "bond": {"tags": ["evil"]}},
        }
        action._servant_templates = lambda servant_id, for_list: [
            (f"{servant_id}.png", servant_id)
        ]
        action._slot_roi = lambda slot: (slot, 0, 1, 1)
        action._match_servant = lambda image, templates, roi: (
            0.85 if templates[0][1] == "1" else 0.80,
            (0, 0),
            templates[0][0],
        )

        self.assertIsNone(action._identify_unspecified_servants(object(), [0]))

    def test_missing_preflight_equip_triggers_replan_and_cache(self):
        action = CompleteBondFormation.__new__(CompleteBondFormation)
        action.available_equips = set()
        action.unavailable_equips = set()
        calls = []

        def preflight(equip):
            calls.append(equip["id"])
            action.unavailable_equips.add(str(equip["id"]))
            return "not_found"

        action._preflight_equip = preflight
        planned = [{"id": "10", "name": "不存在礼装"}]

        self.assertEqual(action._ensure_plan_equips_available(planned), "replan")
        self.assertEqual(action.unavailable_equips, {"10"})
        self.assertEqual(calls, ["10"])

    def test_available_preflight_equip_is_not_scanned_twice(self):
        action = CompleteBondFormation.__new__(CompleteBondFormation)
        action.available_equips = {"10"}
        action.unavailable_equips = set()
        action._preflight_equip = lambda equip: self.fail("不应重复预检")

        result = action._ensure_plan_equips_available(
            [{"id": "10", "name": "已有礼装"}]
        )

        self.assertEqual(result, "available")

    def test_debug_failure_mode_preserves_current_screen(self):
        action = CompleteBondFormation.__new__(CompleteBondFormation)
        action.debug_preserve_failure = True
        action._in_equip_select = lambda: self.fail("不应执行恢复识别")

        result = action._abort_safe("test_failure")

        self.assertFalse(result.success)

    def test_abort_safe_closes_known_servant_detail_before_canceling_edit(self):
        action = CompleteBondFormation.__new__(CompleteBondFormation)
        action.debug_preserve_failure = False
        action.servant_detail_open = True
        action._focus_user = lambda *_args: None
        calls = []

        def close_detail(slot=None):
            calls.append("关闭详情")
            action.servant_detail_open = False
            return True

        action._close_servant_detail = close_detail
        action._in_equip_select = lambda: False
        action._in_servant_select = lambda: False
        action._in_formation_edit = lambda: True
        action._run_pipeline = lambda node: calls.append(node) or True
        action._on_confirm_page = lambda: True
        action._wait_for = lambda predicate, timeout: predicate()

        result = action._abort_safe("test_failure")

        self.assertTrue(result.success)
        self.assertEqual(
            calls,
            ["关闭详情", "羁绊补齐-取消配置"],
        )

    def test_abort_safe_waits_for_transition_without_waiting_for_nonexistent_dialog(self):
        action = CompleteBondFormation.__new__(CompleteBondFormation)
        action.debug_preserve_failure = False
        action.servant_detail_open = False
        action._focus_user = lambda *_args: None
        action._in_equip_select = lambda: False
        action._in_servant_select = lambda: False
        action._in_formation_edit = lambda: True
        action._confirmed_now = lambda predicate: predicate()
        states = iter((False, True, True))
        action._on_confirm_page = lambda: next(states)
        calls = []
        action._run_pipeline = lambda node: calls.append(node) or True
        action._wait_for = lambda predicate, timeout: predicate() or predicate()
        self.assertTrue(action._abort_safe("transition").success)
        self.assertEqual(calls, ["羁绊补齐-取消配置"])

    def test_abort_safe_confirms_discard_dialog_when_not_back_on_confirm_page(self):
        action = CompleteBondFormation.__new__(CompleteBondFormation)
        action.debug_preserve_failure = False
        action.servant_detail_open = False
        action._focus_user = lambda *_args: None
        action._in_equip_select = lambda: False
        action._in_servant_select = lambda: False
        action._in_formation_edit = lambda: True
        confirm_states = iter((False, True))
        action._on_confirm_page = lambda: next(confirm_states)
        calls = []
        action._run_pipeline = lambda node: calls.append(node) or True
        action._confirmed_now = lambda predicate: predicate()
        action._wait_for = lambda predicate, timeout: predicate()

        result = action._abort_safe("test_failure")

        self.assertTrue(result.success)
        self.assertEqual(
            calls,
            ["羁绊补齐-取消配置", "羁绊补齐-取消变更确认"],
        )

    def test_empty_equip_allows_real_ui_saturation_variation(self):
        action = CompleteBondFormation.__new__(CompleteBondFormation)
        hsv = np.zeros((80, 187, 3), dtype=np.uint8)
        hsv[:, :, 2] = 128
        hsv.reshape(-1, 3)[: int(hsv.shape[0] * hsv.shape[1] * 0.27), 1] = 80
        region = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
        action._equip_slot_snapshot = lambda image, slot: region

        self.assertTrue(action._is_empty_equip_slot(object(), 2))

    def test_auto_formation_releases_target_equip_from_unspecified_slot(self):
        action = AutoFormationFromChaldea.__new__(AutoFormationFromChaldea)
        action.expected = [
            {"kind": "LOCAL", "equip_id": 10, "equip_status": "ready"},
            {"kind": "EMPTY", "equip_id": None},
        ]
        action._detect_slots_stable = lambda: [
            {"kind": "LOCAL"},
            {"kind": "OTHER"},
        ]
        action._equip_matches_slot_stable = (
            lambda slot, equip_id: (slot == 1 and equip_id == 10, (0.95, (0, 0), "x"))
        )
        released = []
        action._unequip_relocatable_equip = (
            lambda slot: released.append(slot) or "released"
        )

        result = action._release_equip_held_in_other_slot(
            0, 10, {"name": "目标礼装"}, set(), {0}, set()
        )

        self.assertEqual(result, ("released", 1))
        self.assertEqual(released, [1])

    def test_auto_formation_does_not_steal_correct_duplicate_equip(self):
        action = AutoFormationFromChaldea.__new__(AutoFormationFromChaldea)
        action.expected = [
            {"kind": "LOCAL", "equip_id": 10, "equip_status": "ready"},
            {"kind": "LOCAL", "equip_id": 10, "equip_status": "ready"},
        ]
        action._detect_slots_stable = lambda: [
            {"kind": "LOCAL"},
            {"kind": "LOCAL"},
        ]
        action._equip_matches_slot_stable = lambda *_args: self.fail(
            "已正确的同名礼装槽不应被再次检查或卸下"
        )
        action._unequip_relocatable_equip = lambda *_args: self.fail(
            "不应抢走另一目标槽已正确配置的同名礼装"
        )

        result = action._release_equip_held_in_other_slot(
            0, 10, {"name": "目标礼装"}, {1}, {0}, set()
        )

        self.assertEqual(result, ("none", None))

    def test_auto_formation_restores_relocated_equip_after_target_miss(self):
        action = AutoFormationFromChaldea.__new__(AutoFormationFromChaldea)
        action._confirmed_now = lambda predicate: predicate()
        action._in_formation_edit = lambda: True
        action._in_equip_select = lambda: False
        selected = []
        action._select_equip_for_slot = (
            lambda slot, equip, require_limit_break: selected.append(
                (slot, require_limit_break)
            ) or "selected"
        )
        action._wait_for_equip_replace_verify = lambda slot, equip_id: (True, 0.95)

        result = action._restore_relocated_equip(
            2, 1, {"id": 10, "name": "目标礼装"}
        )

        self.assertTrue(result)
        self.assertEqual(selected, [(2, False)])

    def test_bond_completion_keeps_missing_protected_equip_slot_empty(self):
        action = CompleteBondFormation.__new__(CompleteBondFormation)
        action.expected = [{"kind": "LOCAL", "equip_id": 10}]
        action.grand_equip_slots = set()
        action._grand_fixed_applied_slots = set()
        action.grand_equips_by_slot = {}
        action.equip_database = {"10": {"id": "10", "name": "目标礼装"}}
        action._match_equip_id = lambda *_args: None
        action._is_empty_equip_slot = lambda *_args: True

        fixed, empty, unknown, by_slot = action._classify_current_equips(
            object(), [{"kind": "LOCAL"}]
        )

        self.assertEqual(fixed, [])
        self.assertEqual(empty, [])
        self.assertEqual(unknown, [])
        self.assertEqual(by_slot, {})
        self.assertEqual(action._last_classified_empty_protected_slots, {0})

    def test_bond_completion_still_rejects_wrong_protected_equip(self):
        action = CompleteBondFormation.__new__(CompleteBondFormation)
        action.expected = [{"kind": "LOCAL", "equip_id": 10}]
        action.grand_equip_slots = set()
        action._grand_fixed_applied_slots = set()
        action.grand_equips_by_slot = {}
        action.equip_database = {"10": {"id": "10", "name": "目标礼装"}}
        action._match_equip_id = lambda *_args: (0.1, (0, 0))
        action._is_empty_equip_slot = lambda *_args: False

        fixed, empty, unknown, by_slot = action._classify_current_equips(
            object(), [{"kind": "LOCAL"}], report_errors=False
        )

        self.assertIsNone(fixed)
        self.assertEqual((empty, unknown, by_slot), ([], [], {}))

    def test_chaldea_grand_servant_keeps_identity_but_skips_equip_target(self):
        action = AutoFormationFromChaldea.__new__(AutoFormationFromChaldea)
        action._fail = lambda message: self.fail(message)
        expected = action._build_expected({
            "team": {
                "onFieldSvts": [{
                    "svtId": 404300,
                    "grandSvt": True,
                    "equip1": {"id": 9408220, "limitBreak": True},
                    "equip2": {"id": 9306260},
                    "equip3": {"id": 9408800},
                }],
                "backupSvts": [],
            }
        })

        self.assertEqual(expected[0]["kind"], "LOCAL")
        self.assertEqual(expected[0]["svt_id"], 404300)
        self.assertTrue(expected[0]["grand_svt"])
        self.assertEqual(expected[0]["equip_id"], 9408220)

        action.expected = expected
        action._shot = lambda: object()
        action._equip_matches_slot_stable = lambda *_args: self.fail(
            "冠位槽不应检查普通礼装模板"
        )
        self.assertTrue(action._replace_equips())

    def test_clear_timeout_in_edit_page_falls_back_to_incremental_formation(self):
        action = AutoFormationFromChaldea.__new__(AutoFormationFromChaldea)
        failed_status = SimpleNamespace(succeeded=False, failed=True)
        action.context = SimpleNamespace(
            tasker=SimpleNamespace(stopping=False),
            run_task=lambda name: SimpleNamespace(status=failed_status),
        )
        action._in_formation_edit = lambda: True
        action._wait_for = lambda predicate, timeout: predicate()

        self.assertEqual(action._try_clear_formation(), "skipped")

    def test_clear_failure_outside_edit_page_remains_fatal(self):
        action = AutoFormationFromChaldea.__new__(AutoFormationFromChaldea)
        failed_status = SimpleNamespace(succeeded=False, failed=True)
        action.context = SimpleNamespace(
            tasker=SimpleNamespace(stopping=False),
            run_task=lambda name: SimpleNamespace(status=failed_status),
        )
        action._in_formation_edit = lambda: False
        action._on_formation_confirm_page = lambda: False
        action._wait_for = lambda predicate, timeout: predicate()

        self.assertEqual(action._try_clear_formation(), "failed")

    def test_clear_reported_success_outside_edit_page_remains_fatal(self):
        action = AutoFormationFromChaldea.__new__(AutoFormationFromChaldea)
        succeeded_status = SimpleNamespace(succeeded=True, failed=False)
        action.context = SimpleNamespace(
            tasker=SimpleNamespace(stopping=False),
            run_task=lambda name: SimpleNamespace(status=succeeded_status),
        )
        action._in_formation_edit = lambda: False
        action._wait_for = lambda predicate, timeout: predicate()

        self.assertEqual(action._try_clear_formation(), "failed")

    def test_clear_timeout_on_grand_confirm_page_skips_disband(self):
        action = AutoFormationFromChaldea.__new__(AutoFormationFromChaldea)
        failed_status = SimpleNamespace(succeeded=False, failed=True)
        action.context = SimpleNamespace(
            tasker=SimpleNamespace(stopping=False),
            run_task=lambda name: SimpleNamespace(status=failed_status),
        )
        action.direct_formation_mode = False
        action._in_formation_edit = lambda: False
        action._on_formation_confirm_page = lambda: True
        action._wait_for = lambda predicate, timeout: predicate()

        self.assertEqual(action._try_clear_formation(), "skipped")
        self.assertFalse(action.direct_formation_mode)

    def test_dynamic_grand_popup_is_treated_as_satisfied(self):
        action = AutoFormationFromChaldea.__new__(AutoFormationFromChaldea)
        action._last_equip_entry_state = "grand"
        action._enter_equip_select = lambda slot: False

        result = action._select_equip_for_slot(
            1, {"name": "目标礼装"}, require_limit_break=False
        )

        self.assertEqual(result, "grand")

    def test_grand_popup_recognizes_at_most_three_distinct_bond_equips(self):
        action = CompleteBondFormation.__new__(CompleteBondFormation)
        action.sx = 1.0
        action.bond_equips = [
            {
                "id": str(index),
                "name": f"礼装{index}",
                "bond": {"bonus_type": "percent", "bonus": 5, "target": "all"},
            }
            for index in range(1, 5)
        ]
        templates = {index: np.zeros((86, 152, 3), dtype=np.uint8) + index for index in range(1, 5)}
        action.equip_team_templates = {
            index: (f"f_{index}0.png", template)
            for index, template in templates.items()
        }
        matches = {
            1: (0.98, (350, 300)),
            2: (0.97, (590, 300)),
            3: (0.96, (830, 300)),
            4: (0.90, (354, 300)),
        }
        action._match_template = lambda image, template, roi: matches[int(template[0, 0, 0])]

        equips = action._recognize_grand_popup_equips(object())

        self.assertEqual([equip["id"] for equip in equips], ["1", "2", "3"])

    def test_grand_slot_is_never_cleared_by_bond_optimization(self):
        action = CompleteBondFormation.__new__(CompleteBondFormation)
        action.expected = [{"kind": "EMPTY", "equip_id": None}]
        action.grand_equip_slots = {0}
        action.used_cost = 30
        action.max_cost = 117
        action._unequip_slot = lambda slot: self.fail("冠位礼装不应卸下")
        equip = {
            "id": "10",
            "name": "冠位预设礼装",
            "bond": {"bonus_type": "percent", "bonus": 5, "target": "all"},
        }

        result = action._clear_unspecified_equips(
            [{"kind": "OTHER"}], [], [], {0: equip}
        )

        self.assertEqual(result, ([], [], {0: equip}))


if __name__ == "__main__":
    unittest.main()
