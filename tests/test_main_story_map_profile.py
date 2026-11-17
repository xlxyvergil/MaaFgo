"""地图点被头像遮挡：确认资料页后返回、居中并刷新坐标，恢复有界。"""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from test_main_story import MAP, PANEL, NEXT, runner
from main_story_action import StoryStopped

PROFILE = "主线-御主资料返回"
TARGET = "主线-地图NEXT"
MARKER = "主线-地图NEXT标记"
CENTER = "巡-找到目标-设为中心"


class MapProfileTests(unittest.TestCase):
    def test_returns_then_centers_marker_and_retries_new_click_point(self):
        r, c = runner([{PROFILE}, set(), MAP | {MARKER},
                       MAP | {TARGET}, PANEL | {NEXT}])
        marker = NS(box=[89, 368, 108, 67])
        refreshed = NS(box=[640, 450, 1, 1])
        original_reco = r.reco

        def reco(name, image):
            if name in image and name in (MARKER, TARGET):
                return marker if name == MARKER else refreshed
            return original_reco(name, image)

        with patch.object(r, "reco", side_effect=reco), patch.object(r, "action", wraps=r.action) as action:
            r.open_panel(NS(box=[143, 502, 1, 1]))
        self.assertEqual([n for n, _ in c.actions], [TARGET, PROFILE, CENTER, TARGET])
        self.assertIs(action.call_args_list[2].args[1], marker)
        self.assertIs(action.call_args_list[3].args[1], refreshed)
        self.assertEqual((r.completed, r.battles), (0, 0))
        self.assertEqual(c.tasks, [])

    def test_profile_has_priority_over_false_panel_evidence(self):
        r, c = runner([{PROFILE} | PANEL, MAP | {MARKER}, MAP | {TARGET}, PANEL])
        r.open_panel(NS(box=[143, 502, 1, 1]))
        self.assertEqual([n for n, _ in c.actions], [TARGET, PROFILE, CENTER, TARGET])

    def test_fading_map_controls_do_not_fail_before_marker_appears(self):
        r, c = runner([{PROFILE}, MAP, MAP, MAP | {MARKER}, MAP | {TARGET}, PANEL])
        r.open_panel(NS(box=[143, 502, 1, 1]))
        self.assertEqual([n for n, _ in c.actions], [TARGET, PROFILE, CENTER, TARGET])

    def test_unknown_page_never_returns_or_drags(self):
        r, c = runner([{"主线-列表后退"}])
        with self.assertRaisesRegex(StoryStopped, "未打开副本列表"):
            r.open_panel(NS(box=[143, 502, 1, 1]))
        self.assertEqual([n for n, _ in c.actions], [TARGET])

    def test_return_must_confirm_map_before_drag(self):
        r, c = runner([{PROFILE}])
        with self.assertRaisesRegex(StoryStopped, "未能确认主线地图"):
            r.open_panel(NS(box=[143, 502, 1, 1]))
        self.assertEqual([n for n, _ in c.actions], [TARGET, PROFILE])

    def test_missing_marker_never_drags_old_click_point(self):
        r, c = runner([{PROFILE}, MAP | {TARGET}])
        with self.assertRaisesRegex(StoryStopped, "未找到刚才的主线目标"):
            r.open_panel(NS(box=[143, 502, 1, 1]))
        self.assertEqual([n for n, _ in c.actions], [TARGET, PROFILE])

    def test_drag_must_relocate_target_before_retry(self):
        r, c = runner([{PROFILE}, MAP | {MARKER}, MAP])
        with self.assertRaisesRegex(StoryStopped, "未重新找到主线目标"):
            r.open_panel(NS(box=[143, 502, 1, 1]))
        self.assertEqual([n for n, _ in c.actions], [TARGET, PROFILE, CENTER])

    def test_second_profile_stops_without_looping_recovery(self):
        r, c = runner([{PROFILE}, MAP | {MARKER}, MAP | {TARGET}, {PROFILE}])
        with self.assertRaisesRegex(StoryStopped, "居中后仍误入"):
            r.open_panel(NS(box=[143, 502, 1, 1]))
        self.assertEqual([n for n, _ in c.actions], [TARGET, PROFILE, CENTER, TARGET])

    def test_icon_recovery_rechecks_original_icon(self):
        icon = "主线-地图提亚马特"
        r, c = runner([{PROFILE}, MAP | {icon}, MAP | {icon}, PANEL])
        r.open_panel(NS(box=[100, 200, 80, 75]), icon)
        self.assertEqual([n for n, _ in c.actions], [icon, PROFILE, CENTER, icon])
        self.assertNotIn(MARKER, c.recognitions)
        self.assertNotIn(TARGET, c.recognitions)

    def test_centered_click_retries_are_still_bounded(self):
        r, c = runner([{PROFILE}, MAP | {MARKER}, MAP | {TARGET}])
        with self.assertRaisesRegex(StoryStopped, "未打开副本列表"):
            r.open_panel(NS(box=[143, 502, 1, 1]))
        self.assertEqual([n for n, _ in c.actions], [TARGET, PROFILE, CENTER] + [TARGET] * 3)


if __name__ == "__main__":
    unittest.main()
