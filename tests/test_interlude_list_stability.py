"""幕间列表闪烁提示与真实滚动的截图回归；不连接设备。"""
from pathlib import Path
import sys
import unittest

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]

from interlude_action import same_list


FIXTURES = ROOT / "tests/fixtures/interlude"
EXISTING_PAGES = (
    "cn_list_stage.png",
    "cn_list_direct.png",
    "cn_list_locked.png",
    "cn_list_story.png",
    "cn_map_interlude.png",
    "cn_map_merged_background.png",
    "cn_map_free_only_live.png",
    "cn_map_free_only_synthetic.png",
    "jp_list_direct_locked.png",
    "jp_list_locked.png",
    "jp_map_locked_free.png",
)
BLINK_HINT = "cn_list_blink_hint.png"
BLINK_BONUS = "cn_list_blink_bonus.png"
SCROLLED = "cn_list_blink_scrolled.png"


class InterludeListStabilityTests(unittest.TestCase):
    def fixture(self, name):
        image = cv2.imdecode(np.fromfile(FIXTURES / name, np.uint8), cv2.IMREAD_COLOR)
        self.assertIsNotNone(image, name)
        return image

    @staticmethod
    def move_list(image, offset):
        # 只平移截图中的整个右侧任务列表，模拟手势后卡片的位置变化。
        # 此区域来自样本的任务列表边界，不依赖稳定性算法的取样区域。
        moved = image.copy()
        task_list = image[95:710, 624:1234]
        moved[95:710, 624:1234] = cv2.warpAffine(
            task_list,
            np.float32([[1, 0, 0], [0, 1, offset]]),
            (task_list.shape[1], task_list.shape[0]),
            borderMode=cv2.BORDER_REFLECT_101,
        )
        return moved

    def test_real_alternating_hint_and_bonus_do_not_count_as_scroll(self):
        hint, bonus = self.fixture(BLINK_HINT), self.fixture(BLINK_BONUS)
        self.assertFalse(np.array_equal(hint, bonus))
        self.assertTrue(same_list(hint, bonus))
        self.assertTrue(same_list(bonus, hint))

    def test_real_scroll_is_different_from_both_blink_states(self):
        scrolled = self.fixture(SCROLLED)
        for name in (BLINK_HINT, BLINK_BONUS):
            with self.subTest(before=name):
                before = self.fixture(name)
                self.assertFalse(same_list(before, scrolled))
                self.assertFalse(same_list(scrolled, before))

    def test_unchanged_cn_jp_list_and_map_pages_are_stable(self):
        for name in (*EXISTING_PAGES, BLINK_HINT, BLINK_BONUS, SCROLLED):
            with self.subTest(page=name):
                image = self.fixture(name)
                self.assertTrue(same_list(image, image.copy()))

    def test_small_and_large_list_movements_remain_visible(self):
        # 包括只有绿色Free关卡的地图，不能因排除动态文字而丢失滚动证据。
        for name in (*EXISTING_PAGES, BLINK_HINT, BLINK_BONUS, SCROLLED):
            image = self.fixture(name)
            for offset in (-60, -25, -10, 10, 25, 60):
                with self.subTest(page=name, offset=offset):
                    self.assertFalse(same_list(image, self.move_list(image, offset)))

    def test_distinct_pages_do_not_count_as_stable(self):
        pages = (
            ("cn_list_stage.png", "cn_list_locked.png"),
            ("cn_list_direct.png", "cn_list_story.png"),
            ("jp_list_direct_locked.png", "jp_list_locked.png"),
            ("cn_map_interlude.png", "cn_map_free_only_live.png"),
            ("jp_map_locked_free.png", "jp_list_locked.png"),
        )
        for first, second in pages:
            with self.subTest(first=first, second=second):
                self.assertFalse(same_list(self.fixture(first), self.fixture(second)))

    def test_1080p_captures_keep_blink_and_scroll_results(self):
        def resized(name):
            return cv2.resize(self.fixture(name), (1920, 1080))

        hint, bonus, scrolled = (resized(name) for name in (BLINK_HINT, BLINK_BONUS, SCROLLED))
        self.assertTrue(same_list(hint, bonus))
        self.assertFalse(same_list(hint, scrolled))
        self.assertFalse(same_list(bonus, scrolled))

    def test_non_images_are_not_stable(self):
        image = self.fixture(BLINK_HINT)
        for value in (None, "screenshot", [], {}, object()):
            with self.subTest(value_type=type(value).__name__):
                self.assertFalse(same_list(value, image))
                self.assertFalse(same_list(image, value))

    def test_different_image_dimensions_are_not_stable(self):
        image = self.fixture(BLINK_HINT)
        for changed in (image[:-1], image[:, :-1], cv2.resize(image, (1920, 1080))):
            with self.subTest(shape=changed.shape):
                self.assertFalse(same_list(image, changed))
                self.assertFalse(same_list(changed, image))


if __name__ == "__main__":
    unittest.main()
