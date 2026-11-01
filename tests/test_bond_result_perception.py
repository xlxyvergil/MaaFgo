"""Bond-result mapping and page gating; no synthetic frame is device evidence."""
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from battle.core.models import FormationSlot
from battle.perception.bond_result import (
    BOND_ANY_MAX_NODE, BOND_MAX_NODE, BOND_PAGE_NODE, BOND_UPGRADE_NODE,
    read_any_bond_max, read_bond_max, read_bond_page, read_bond_upgrade,
    result_formation_order, result_slot_for_formation,
)


def formation(support=3, empty=()):
    return tuple(FormationSlot(number, "empty" if number in empty else "unknown",
                               is_support=False if number in empty else number == support)
                 for number in range(1, 7))


def context(*, page=True, maxima=(), upgrade=False):
    values = {BOND_PAGE_NODE: NS(hit=page), BOND_ANY_MAX_NODE: NS(hit=bool(maxima)),
              BOND_UPGRADE_NODE: NS(hit=upgrade)}
    values.update({BOND_MAX_NODE.format(column=column): NS(hit=column in maxima)
                   for column in range(1, 6)})
    return NS(run_recognition=Mock(side_effect=lambda node, image: values[node]))


class BondResultPerceptionTests(unittest.TestCase):
    def test_support_at_every_original_position_preserves_own_order(self):
        for support in range(1, 7):
            with self.subTest(support=support):
                slots = formation(support=support)
                own = tuple(number for number in range(1, 7) if number != support)
                self.assertEqual(result_formation_order(slots), own)
                for column, target in enumerate(own, 1):
                    self.assertEqual(result_slot_for_formation(slots, target), column)
                with self.assertRaisesRegex(ValueError, "target_not_own_servant"):
                    result_slot_for_formation(slots, support)

    def test_unknown_names_are_occupied_and_confirmed_empty_slots_are_excluded(self):
        slots = formation(support=2, empty=(3, 5))
        self.assertTrue(all(slot.servant_id is None for slot in slots))
        self.assertEqual(result_formation_order(slots), (1, 4, 6))
        self.assertEqual(result_slot_for_formation(slots, 6), 3)
        for target in (2, 3, 5):
            with self.assertRaisesRegex(ValueError, "target_not_own_servant"):
                result_slot_for_formation(slots, target)

    def test_serialized_lightweight_slots_need_no_servant_identity(self):
        slots = tuple({"slot": slot.slot, "status": slot.status, "is_support": slot.is_support}
                      for slot in formation(support=6))
        self.assertEqual(result_slot_for_formation(slots, 5), 5)

    def test_unknown_support_or_occupancy_disagreement_cannot_shift_target(self):
        slots = [{"slot": slot.slot, "status": slot.status, "is_support": slot.is_support}
                 for slot in formation()]
        slots[0]["is_support"] = None
        with self.assertRaisesRegex(ValueError, "support_unknown:1"):
            result_slot_for_formation(slots, 6)
        slots[0]["is_support"] = False
        slots[0]["reason"] = "formation_frame_disagreement"
        with self.assertRaisesRegex(ValueError, "occupancy_unknown:1"):
            result_formation_order(slots)

    def test_corrupt_or_incomplete_layout_is_not_assumed_to_have_support_at_three(self):
        layouts = [None, (), formation()[:-1], formation()[::-1], formation() + formation()[:1]]
        for slots in layouts:
            with self.subTest(slots=slots), self.assertRaises(ValueError):
                result_slot_for_formation(slots, 1)
        with self.assertRaisesRegex(ValueError, "layout_not_calibrated:6"):
            result_formation_order(formation(support=None))
        slots = [{"slot": number, "status": "unknown", "is_support": number in (2, 3)}
                 for number in range(1, 7)]
        with self.assertRaisesRegex(ValueError, "multiple_supports"):
            result_formation_order(slots)
        for target in (True, False, 0, 7, "3", None):
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, "target_slot_invalid"):
                result_slot_for_formation(formation(), target)

    def test_only_bond_page_maxima_are_reported_using_the_same_frame(self):
        frame = object()
        ctx = context(maxima=(2, 5))
        self.assertEqual(read_bond_max(ctx, frame), (2, 5))
        self.assertIs(ctx.run_recognition.call_args_list[0].args[1], frame)
        self.assertTrue(all(call.args[1] is frame for call in ctx.run_recognition.call_args_list))
        # An upgrade dialog or servant-level MAX cannot pass the page gate.
        other_page = context(page=False, maxima=(1, 2, 3, 4, 5))
        with self.assertRaisesRegex(ValueError, "page_not_visible"):
            read_bond_max(other_page, frame)
        other_page.run_recognition.assert_called_once_with(BOND_PAGE_NODE, frame)

    def test_real_negative_is_distinct_from_unavailable_recognition(self):
        ctx = context()
        self.assertEqual(read_bond_max(ctx, object()), ())
        self.assertFalse(read_bond_page(context(page=False), object()))
        for broken in (None, NS(), NS(hit=None)):
            with self.subTest(broken=broken):
                ctx = NS(run_recognition=Mock(return_value=broken))
                with self.assertRaisesRegex(ValueError, "recognition_unavailable"):
                    read_bond_page(ctx, object())
        ctx = context()
        with self.assertRaisesRegex(ValueError, "image_missing"):
            read_bond_page(ctx, None)
        ctx.run_recognition.assert_not_called()

    def test_all_mode_needs_no_formation_and_keeps_page_gating(self):
        frame = object()
        ctx = context(maxima=(6,))
        self.assertTrue(read_any_bond_max(ctx, frame))
        self.assertEqual([call.args[0] for call in ctx.run_recognition.call_args_list],
                         [BOND_PAGE_NODE, BOND_ANY_MAX_NODE])
        self.assertFalse(read_any_bond_max(context(), frame))
        wrong_page = context(page=False, maxima=(1,))
        with self.assertRaisesRegex(ValueError, "page_not_visible"):
            read_any_bond_max(wrong_page, frame)
        wrong_page.run_recognition.assert_called_once_with(BOND_PAGE_NODE, frame)

    def test_short_and_six_own_layouts_are_explicitly_uncalibrated(self):
        ctx = context(maxima=(1,))
        for columns in (0, 1, 2, 3, 4, 6, True, "5", None):
            with self.subTest(columns=columns), self.assertRaisesRegex(ValueError, "layout_not_calibrated"):
                read_bond_max(ctx, object(), columns=columns)
        ctx.run_recognition.assert_not_called()

    def test_upgrade_presentation_does_not_prove_max_or_bypass_page_gate(self):
        frame = object()
        ctx = context(page=False, upgrade=True, maxima=(1,))
        self.assertTrue(read_bond_upgrade(ctx, frame))
        ctx.run_recognition.assert_called_once_with(BOND_UPGRADE_NODE, frame)
        with self.assertRaisesRegex(ValueError, "page_not_visible"):
            read_any_bond_max(ctx, frame)
        with self.assertRaisesRegex(ValueError, "page_not_visible"):
            read_bond_max(ctx, frame)
        self.assertFalse(read_bond_upgrade(context(), frame))

    def test_temporary_regional_templates_have_provenance_and_fit_pipeline_rois(self):
        pipeline = json.loads((ROOT / "assets/resource/base/pipeline/羁绊结算识别.json").read_text(encoding="utf-8"))
        manifest = json.loads((ROOT / "docs/verification/bond_stop_templates.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "mixed_local_screenshots_and_temporary_bbc_fallbacks")
        for fixture in manifest["regression_fixtures"]:
            self.assertEqual(hashlib.sha256((ROOT / fixture["file"]).read_bytes()).hexdigest(),
                             fixture["sha256"])
        outputs = {item["output"]: item for item in manifest["templates"]}
        self.assertEqual(len(outputs), len(manifest["templates"]), "duplicate template provenance")
        leaves = [node["recognition"]["param"] for node in pipeline.values()
                  if node["recognition"]["type"] == "TemplateMatch"]
        for layer in ("base", "cn"):
            for param in leaves:
                relative = f"assets/resource/{layer}/image/{param['template']}"
                asset = ROOT / relative
                self.assertEqual(hashlib.sha256(asset.read_bytes()).hexdigest(), outputs[relative]["output_sha256"])
                with Image.open(asset) as image:
                    self.assertLessEqual(image.width, param["roi"][2])
                    self.assertLessEqual(image.height, param["roi"][3])
            header_relative = f"assets/resource/{layer}/image/battle/bond/战后结果标题.png"
            header = ROOT / header_relative
            self.assertEqual(hashlib.sha256(header.read_bytes()).hexdigest(),
                             outputs[header_relative]["output_sha256"])
            with Image.open(header) as image:
                self.assertLessEqual(image.width, 400)
                self.assertLessEqual(image.height, 120)


if __name__ == "__main__":
    unittest.main()
