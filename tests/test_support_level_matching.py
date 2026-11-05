"""真实国日服等级裁图与属性显示失败回归；不连接设备。

国服等级样本来自 2026-09-26、09-27 和 10-13 的本地截图；攻击力样本
来自 2026-11-02 国服助战报错截图。日服样本来自 2026-10-24。
仅保留头像上方 118x27 区域，不包含玩家名。11-04 日志没有对应助战截图，
这里只证明属性显示能复现相同的等级读取失败，不能据此确认该用户的排序。
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom"), str(ROOT / "tests")]
from test_support_fallback import Context
import support_action as support


class SupportLevelMatchingTests(unittest.TestCase):
    fixtures = ROOT / "tests/fixtures/support_level"

    def setUp(self):
        for name in ("info", "warning", "error", "debug"):
            p = patch.object(support.mfaalog, name)
            p.start()
            self.addCleanup(p.stop)

    def crop(self, name):
        image = support._imread(str(self.fixtures / name))
        self.assertIsNotNone(image)
        self.assertEqual(image.shape, (27, 118, 3))
        return image

    def frame(self, name):
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        x, y, w, h = support.LEVEL_ROI
        image[y:y + h, x:x + w] = self.crop(name)
        return image

    def test_real_cn_and_jp_levels_keep_minimum_requirement(self):
        for name, level in (("cn_level_90.png", 90), ("cn_level_100.png", 100),
                            ("cn_level_120.png", 120), ("jp_level_120.png", 120)):
            with self.subTest(name=name):
                self.assertEqual(support.SupportAction._ocr_level_text(self.crop(name)), level)
                action = support.SupportAction()
                frame = self.frame(name)
                self.assertTrue(action._match_level(frame, 0, 0, level))
                self.assertFalse(action._match_level(frame, 0, 0, level + 1))

    def test_attack_screenshot_warns_without_interrupting_matching(self):
        action = support.SupportAction()
        self.assertFalse(action._match_level(self.frame("cn_attack.png"), 0, 0, 90))
        message = support.mfaalog.warning.call_args.args[0]
        for text in ("等级识别失败", "至少90级", "助战选择", "是否已开启从者等级显示", "继续查找"):
            self.assertIn(text, message)
        support.mfaalog.error.assert_not_called()

    def test_other_attribute_labels_cannot_be_read_as_levels(self):
        for text in ("攻击20.254", "攻击17765", "攻撃20,254", "ATK20,254", "HP16,000", "生命16,000"):
            with self.subTest(text=text), patch.object(support.SupportAction, "_ocr_run", return_value=text):
                self.assertIsNone(support.SupportAction._ocr_level_text(self.crop("cn_attack.png")))

    def test_unknown_ocr_keeps_raw_text_without_claiming_sorting_cause(self):
        with patch.object(support.SupportAction, "_ocr_run", return_value="级lOO?lOO"):
            self.assertIsNone(support.SupportAction._ocr_level_text(self.crop("cn_level_100.png")))
        self.assertIn("级lOO?lOO", support.mfaalog.debug.call_args.args[0])

    def test_zero_requirement_does_not_read_attribute_display(self):
        with patch.object(support.SupportAction, "_ocr_level_text") as ocr:
            self.assertTrue(support.SupportAction()._match_level(self.frame("cn_attack.png"), 0, 0, 0))
        ocr.assert_not_called()
        support.mfaalog.warning.assert_not_called()

    def make_search(self, *, fallback=False):
        ctx = Context(enabled=fallback)
        self.addCleanup(support.sessions.stop_owner, "")
        ctx.attach["level"] = 90
        action = support.SupportAction()
        for name, result in {
            "_get_servant_map": {"test": {"images": ["test.png"]}},
            "_get_detector": NS(detect=lambda image: [(0, 0, 1180, 195, .99)]),
            "_init_scale": None, "_shot_base": self.frame("cn_attack.png"),
            "_ref_shift": (0, 0), "_match_servant": True, "_match_ce": True,
            "_match_np": True, "_tap": None, "_swipe": None,
        }.items():
            setattr(action, name, Mock(return_value=result))
        action._is_connecting = Mock(return_value=False)
        return ctx, action

    def run_search(self, ctx, action):
        with patch.object(support.time, "sleep"), patch.object(support, "MAX_SWIPE_BEFORE_REFRESH", 1):
            return action.run(ctx, NS(node_name="助战action", custom_action_param="{}"))

    def level_warnings(self):
        return [c.args[0] for c in support.mfaalog.warning.call_args_list
                if "等级识别失败" in c.args[0]]

    def test_attribute_display_continues_search_and_success_has_no_final_reminder(self):
        ctx, action = self.make_search()
        action._shot_base.side_effect = [self.frame("cn_attack.png"), self.frame("cn_level_100.png")]
        self.assertTrue(self.run_search(ctx, action).success)
        action._swipe.assert_called_once()
        self.assertEqual(ctx.entries, [])
        self.assertEqual(len(self.level_warnings()), 1)
        self.assertIn("正在继续查找", self.level_warnings()[0])
        support.mfaalog.error.assert_not_called()

    def test_refresh_failure_adds_final_reminder_and_repeated_misses_do_not_spam(self):
        ctx, action = self.make_search()
        ctx.fail_entry = "助战刷新"
        result = self.run_search(ctx, action)
        self.assertFalse(result.success)
        action._swipe.assert_called_once()
        self.assertEqual(ctx.refreshes, 1)
        warnings = self.level_warnings()
        self.assertEqual(len(warnings), 2)
        self.assertIn("正在继续查找", warnings[0])
        self.assertIn("本次助战任务失败", warnings[1])
        self.assertIn("是否已开启从者等级显示", warnings[1])
        support.mfaalog.error.assert_called_once()

    def test_existing_fallback_keeps_running_and_only_failure_adds_reminder(self):
        for failed in (False, True):
            with self.subTest(failed=failed):
                support.mfaalog.warning.reset_mock()
                ctx, action = self.make_search(fallback=True)
                if failed:
                    ctx.fail_entry = "助战兜底-选择首个助战"
                self.assertEqual(self.run_search(ctx, action).success, not failed)
                self.assertEqual(ctx.refreshes, 10)
                self.assertEqual(ctx.entries[-1], "助战兜底-选择首个助战")
                self.assertEqual(len(self.level_warnings()), 2 if failed else 1)

    def test_run_resets_previous_failure_and_known_low_level_does_not_warn(self):
        ctx, action = self.make_search()
        ctx.fail_entry = "助战刷新"
        self.assertFalse(self.run_search(ctx, action).success)
        support.mfaalog.warning.reset_mock()
        ctx.attach["level"] = 100
        action._shot_base.return_value = self.frame("cn_level_90.png")
        self.assertFalse(self.run_search(ctx, action).success)
        self.assertEqual(self.level_warnings(), [])

    def test_empty_ocr_and_missing_roi_also_warn(self):
        for frame, text in ((self.frame("cn_level_90.png"), None),
                            (np.zeros((1, 1, 3), dtype=np.uint8), "90/90")):
            with self.subTest(text=text), patch.object(support.SupportAction, "_ocr_run", return_value=text):
                support.mfaalog.warning.reset_mock()
                self.assertFalse(support.SupportAction()._match_level(frame, 0, 0, 90))
                self.assertEqual(len(self.level_warnings()), 1)

    def test_user_stop_does_not_claim_task_failure(self):
        ctx, action = self.make_search()
        action._swipe.side_effect = lambda *args: setattr(ctx.tasker, "stopping", True)
        self.assertFalse(self.run_search(ctx, action).success)
        self.assertEqual(len(self.level_warnings()), 1)


if __name__ == "__main__":
    unittest.main()
