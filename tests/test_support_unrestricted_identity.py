"""仅礼装助战选中后的身份补识别；不连接设备。"""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_support_fallback import Context
import support_action as support

ROOT = Path(__file__).resolve().parents[1]


class UnrestrictedSupportIdentityTests(unittest.TestCase):
    def setUp(self):
        self.action = support.SupportAction()
        self.frame = np.zeros((720, 1280, 3), np.uint8)
        for level in ("info", "diagnostic", "warning", "error"):
            p = patch.object(support.mfaalog, level)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(support.sessions.stop_owner, "")

    def identify_scores(self, scores, *, aliases=False):
        first = {"id": "100100", "images": ["first.png", "second_form.png"]}
        catalog = {"100100": first, "200100": {"id": "200100", "images": ["other.png"]}}
        if aliases:
            catalog["alias"] = first
        self.action._get_servant_map = Mock(return_value=catalog)
        results = [np.array([[score]], np.float64) for score in scores]
        with patch.object(support, "_imread", return_value=np.zeros((158, 158), np.uint8)), \
                patch("cv2.matchTemplate", side_effect=results) as matcher:
            result = self.action._identify_unrestricted_support(self.frame, "faces", 0, 0)
        return result, matcher.call_count

    def test_forms_and_aliases_do_not_become_competing_identities(self):
        result, count = self.identify_scores((.91, .90, .65), aliases=True)
        self.assertEqual(result, "100100")
        self.assertEqual(count, 3)
        self.assertEqual(self.action._matched_face_evidence, (.91, "first.png"))

    def test_low_score_ambiguous_or_nonfinite_identity_remains_unknown(self):
        for scores in ((.74, .73, .20), (.91, .90, .85), (.90, .80, .90),
                       (float("nan"), .20, .20), (float("inf"), .20, .20)):
            with self.subTest(scores=scores):
                self.assertIsNone(self.identify_scores(scores)[0])

    def test_highest_different_servant_wins(self):
        self.assertEqual(self.identify_scores((.60, .65, .95))[0], "200100")

    def test_missing_or_clipped_portraits_remain_unknown(self):
        self.action._get_servant_map = Mock(return_value={
            "100100": {"id": "100100", "images": ["missing.png"]}})
        with patch.object(support, "_imread", return_value=None):
            self.assertIsNone(self.action._identify_unrestricted_support(self.frame, "faces", 0, 0))
        self.assertIsNone(self.action._identify_unrestricted_support(self.frame, "faces", 1250, 700))

    def test_first_selection_and_requested_identity_do_not_scan_all_portraits(self):
        self.action._identify_unrestricted_support = Mock()
        with patch.object(support.sessions, "awaiting_continuous_support", return_value=False), \
                patch.object(support.sessions, "support_selected") as selected:
            selected.return_value = None
            self.action._record_selected_support(1, "session", None, image=self.frame, face_dir="faces")
        with patch.object(support.sessions, "awaiting_continuous_support", return_value=True), \
                patch.object(support.sessions, "support_selected", return_value=None):
            self.action._record_selected_support(1, "session", "100100", image=self.frame, face_dir="faces")
        self.action._identify_unrestricted_support.assert_not_called()

    def test_auxiliary_recognition_failure_still_records_unknown_support(self):
        self.action._matched_face_evidence = (.99, "stale.png")
        self.action._identify_unrestricted_support = Mock(side_effect=RuntimeError("missing face"))
        with patch.object(support.sessions, "awaiting_continuous_support", return_value=True), \
                patch.object(support.sessions, "support_selected", return_value=None) as selected:
            self.action._record_selected_support(1, "session", None, image=self.frame, face_dir="faces")
        self.assertIsNone(selected.call_args.args[2])
        self.assertEqual(selected.call_args.kwargs["confidence"].value, 0.0)
        self.assertEqual(selected.call_args.kwargs["matched_template"], "")

    def test_actual_search_identifies_only_after_successful_click(self):
        context = Context(enabled=False)
        context.attach.update(class_name="any", servant="stale")
        events = []
        for name, value in {
            "_get_detector": NS(detect=lambda image: [(0, 0, 100, 100, .99)]),
            "_init_scale": None, "_shot_base": self.frame,
            "_ref_shift": (0, 0), "_match_ce": True,
        }.items():
            setattr(self.action, name, Mock(return_value=value))
        self.action._tap = Mock(side_effect=lambda *args: events.append("click"))
        self.action._identify_unrestricted_support = Mock(side_effect=lambda *args: events.append("identify"))
        with patch.object(support.sessions, "awaiting_continuous_support", return_value=True), \
                patch.object(support.sessions, "support_selected", return_value=None):
            self.assertTrue(self.action.run(context, NS(node_name="助战action", custom_action_param="{}")).success)
        self.assertEqual(events, ["click", "identify"])
        self.assertIs(self.action._identify_unrestricted_support.call_args.args[0], self.frame)
        events.clear()
        self.action._tap = Mock(return_value=False)
        self.action._identify_unrestricted_support.reset_mock()
        self.assertFalse(self.action.run(context, NS(node_name="助战action", custom_action_param="{}")).success)
        self.action._identify_unrestricted_support.assert_not_called()

    def test_real_portrait_patch_matches_the_selected_servant(self):
        directory = ROOT / "assets/resource/base/image/servant_face"
        target = support._imread(str(directory / "f_4032000.png"))
        self.assertIsNotNone(target)
        # 取正式头像的实际子区域，放入助战固定多边形对应的位置。
        self.frame[39:109, 27:167] = target[40:110, 10:150]
        self.action._get_servant_map = Mock(return_value={
            "403200": {"id": "403200", "images": ["f_4032000.png"]},
            "100100": {"id": "100100", "images": ["f_1001000.png"]},
        })
        self.assertEqual(self.action._identify_unrestricted_support(self.frame, str(directory), 0, 0), "403200")

    def test_session_replaced_after_click_stops_without_publishing_identity(self):
        context = Context(enabled=False)
        context.attach.update(class_name="any")
        for name, value in {
            "_get_detector": NS(detect=lambda image: [(0, 0, 100, 100, .99)]),
            "_init_scale": None, "_shot_base": self.frame,
            "_ref_shift": (0, 0), "_match_ce": True,
        }.items():
            setattr(self.action, name, Mock(return_value=value))
        self.action._tap = Mock(side_effect=lambda *args: support.sessions.begin(context.root))
        self.action._identify_unrestricted_support = Mock()
        with patch.object(support.sessions, "support_selected") as selected:
            result = self.action.run(context, NS(node_name="助战action", custom_action_param="{}"))
        self.assertFalse(result.success)
        self.action._identify_unrestricted_support.assert_not_called()
        selected.assert_not_called()


if __name__ == "__main__":
    unittest.main()
