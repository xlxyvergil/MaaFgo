"""真实 MaaFramework 导航链与可切换内存画面；不连接或操作模拟器。"""
from pathlib import Path
import sys
import unittest

import cv2
import numpy as np
from maa.custom_action import CustomAction
from maa.tasker import Tasker

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]
from interlude_action import InterludeRunner
from test_main_story_framework import FrameworkResources, ScreenController


def read_image(path):
    image = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise AssertionError(f"无法读取回归图片：{path}")
    return image


def template(name):
    for layer in ("cn", "base"):
        path = ROOT / "assets/resource" / layer / "image" / name
        if path.is_file():
            return read_image(path)
    raise AssertionError(f"缺少导航模板：{name}")


def synthetic_home():
    """拼接现有模板以驱动导航；该画面是合成控制器输入，不是实机样本。"""
    image = np.zeros((720, 1280, 3), np.uint8)
    for name, x, y in (("main_ui.png", 20, 15),
                       ("主界面圣晶石.png", 202, 550),
                       ("chaldea_gate/主界面页签-迦勒底之门.png", 800, 200)):
        patch = template(name)
        h, w = patch.shape[:2]
        image[y:y+h, x:x+w] = patch
    return image


class ReentryController(ScreenController):
    """只有已识别入口上的点击才能切换页面，并记录其余全部输入。"""

    def __init__(self, *, require_scroll):
        fixture_dir = ROOT / "tests/fixtures/interlude"
        self.pages = {
            "map": read_image(fixture_dir / "cn_map_without_interlude_return.png"),
            "home": synthetic_home(),
            "gate": read_image(fixture_dir / "cn_gate_interlude_tab.png"),
            "list": read_image(fixture_dir / "cn_list_direct.png"),
        }
        self.hidden_home = self.pages["home"].copy()
        self.hidden_home[180:270, 760:1060] = 0
        self.hidden_gate = self.pages["gate"].copy()
        self.hidden_gate[180:325, 640:1235] = 0
        self.state = "map"
        self.revealed = {"home": not require_scroll, "gate": not require_scroll}
        self.events, self.keycodes, self.unexpected = [], [], []
        super().__init__(self.pages["map"])

    def screencap(self):
        if self.state == "home" and not self.revealed["home"]:
            return self.hidden_home.copy()
        if self.state == "gate" and not self.revealed["gate"]:
            return self.hidden_gate.copy()
        return self.pages[self.state].copy()

    def click_key(self, keycode):
        self.keycodes.append(keycode)
        self.events.append((self.state, "key", keycode))
        if self.state == "map" and keycode == 111:
            self.state = "home"
            return True
        self.unexpected.append((self.state, "key", keycode))
        return False

    def click(self, x, y):
        self.clicks.append((x, y))
        self.events.append((self.state, "click", x, y))
        if self.state == "home" and x >= 1227 and 75 <= y < 150:
            # 共享回主界面流程已有的右上初始化动作，不改变页面。
            return True
        if (self.state == "home" and self.revealed["home"]
                and 800 <= x < 1010 and 200 <= y < 245):
            self.state = "gate"
            return True
        if (self.state == "gate" and self.revealed["gate"]
                and 776 <= x < 976 and 210 <= y < 278):
            self.state = "list"
            return True
        self.unexpected.append((self.state, "click", x, y))
        return False

    def swipe(self, x1, y1, x2, y2, duration):
        self.swipes.append((x1, y1, x2, y2, duration))
        self.events.append((self.state, "swipe", x1, y1, x2, y2))
        if self.state not in ("home", "gate"):
            self.unexpected.append((self.state, "swipe", x1, y1, x2, y2))
            return False
        if y1 > y2:
            self.revealed[self.state] = True
        return True


class InterludeReentryFrameworkTests(FrameworkResources, unittest.TestCase):
    def run_reentry(self, *, require_scroll):
        resource = self.resource("cn")
        controller = ReentryController(require_scroll=require_scroll)
        self.assertTrue(controller.post_connection().wait().succeeded)
        outcomes, errors = [], []

        class Probe(CustomAction):
            def run(self, context, argv):
                try:
                    runtime = InterludeRunner(context)
                    original = runtime.ctx
                    home_before = original.get_node_data("主界面判断-返回")
                    support_before = original.get_node_data("Chaldea助战action")
                    runtime.completed, runtime.battles = 3, 2
                    runtime._map_opened_at = 2
                    runtime._skip_next_reset = True
                    runtime.reenter_from_home()
                    outcomes.append({
                        "completed": runtime.completed,
                        "battles": runtime.battles,
                        "limit": runtime.limit,
                        "restrictions": runtime.auto_handle_restrictions,
                        "map_opened_at": runtime._map_opened_at,
                        "skip_next_reset": runtime._skip_next_reset,
                        "reentered_at": runtime._reentered_at,
                        "restored_context": runtime.ctx is original,
                        "home_unchanged": original.get_node_data("主界面判断-返回") == home_before,
                        "support_unchanged": original.get_node_data("Chaldea助战action") == support_before,
                        "list_confirmed": runtime.total_list(runtime.frame()),
                    })
                    return True
                except Exception as exc:
                    errors.append(repr(exc))
                    return False

        resource.register_custom_action("interlude_reentry_probe", Probe())
        # 仅缩短测试等待和复位重复次数；保留正式识别、JumpBack查找及动作路径。
        fast_nodes = ("主界面判断-main-ui", "主界面判断-返回", "无弹窗", "有弹窗返回",
                      "幕间-导航列表归位", "幕间-进入迦勒底之门",
                      "幕间-主界面查找下滑", "幕间-确认并选择页签",
                      "幕间-迦门查找下滑")
        overrides = {name: {"post_delay": 0, "repeat_delay": 0, "repeat": 1,
                            "pre_wait_freezes": 0, "post_wait_freezes": 0}
                     for name in fast_nodes}
        overrides["幕间-配置"] = {"attach": {
            "quest_limit": 7, "transition_timeout": 10, "auto_handle_restrictions": False,
        }}
        overrides["Chaldea助战action"] = {"attach": {"chaldea_enabled": True}}
        overrides["interlude_reentry_probe"] = {
            "action": {"type": "Custom", "param": {"custom_action": "interlude_reentry_probe"}},
            "on_error": [],
        }
        tasker = Tasker()
        self.assertTrue(tasker.bind(resource, controller))
        try:
            status = tasker.post_task("interlude_reentry_probe", overrides).wait()
            self.assertTrue(status.succeeded, errors)
            self.assertEqual(outcomes, [{
                "completed": 3, "battles": 2, "limit": 7, "restrictions": False,
                "map_opened_at": None, "skip_next_reset": False, "reentered_at": 3,
                "restored_context": True, "home_unchanged": True, "support_unchanged": True,
                "list_confirmed": True,
            }])
            self.assertEqual(controller.state, "list")
            self.assertEqual(controller.keycodes, [111])
            self.assertEqual(controller.unexpected, [])
            # 导航只在home/gate产生点击和滑动，进入总列表即停止，没有选关或开战。
            self.assertFalse(any(event[0] == "list" for event in controller.events))
            return controller.events
        finally:
            tasker.post_stop().wait()

    def test_real_pipeline_reenters_list_and_preserves_progress_and_options(self):
        events = self.run_reentry(require_scroll=False)
        self.assertEqual([event[0] for event in events if event[1] == "swipe"], ["home", "gate"])

    def test_jumpback_search_scrolls_both_lists_before_entry_becomes_visible(self):
        events = self.run_reentry(require_scroll=True)
        for page in ("home", "gate"):
            swipes = [event for event in events if event[0] == page and event[1] == "swipe"]
            self.assertEqual(len(swipes), 2)
            self.assertLess(swipes[0][3], swipes[0][5])
            self.assertGreater(swipes[1][3], swipes[1][5])


if __name__ == "__main__":
    unittest.main()
