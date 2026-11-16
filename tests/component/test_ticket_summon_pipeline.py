"""真实 Framework 路由/次数回归；画面叶子由状态机提供，不等同于实机视觉验收。"""
import copy
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest

import maa
import numpy as np
from maa.controller import CustomController
from maa.context import ContextEventSink
from maa.custom_recognition import CustomRecognition
from maa.define import LoggingLevelEnum
from maa.event_sink import NotificationType
from maa.library import Library
from maa.resource import Resource
from maa.tasker import Tasker

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent/custom"))
import ticket_summon_action as runtime

P = runtime.PREFIX


class SummonScreen(CustomController):
    def __init__(self, *, tickets=20, bonus=False, wrong_currency=False, confirm_text=None,
                 network=None, warehouse=None, never_result=False, no_return=False, locale="jp"):
        super().__init__()
        self.scene = "pool"
        self.tickets = tickets
        self.bonus = bonus
        self.wrong_currency = wrong_currency
        self.confirm_text = confirm_text
        self.network = dict(network or {})
        self.network_left = self.network.pop("pool", 0)
        self.warehouse = warehouse
        self.never_result = never_result
        self.no_return = no_return
        self.locale = locale
        self.opens = self.spent = self.returns = self.retries = self.skips = 0
        self.unexpected = []

    def connect(self): return True
    def request_uuid(self): return "ticket-summon-synthetic"
    def get_features(self): return 0
    def screencap(self):
        return np.full((720, 1280, 3), 64 if self.scene == "result" else 0, dtype=np.uint8)

    def advance(self, scene, stage):
        self.scene = scene
        self.network_left = self.network.pop(stage, 0)

    def click(self, x, y):
        if self.network_left and 700 <= x <= 1030 and y >= 500:
            self.network_left -= 1
            self.retries += 1
        elif self.scene == "pool" and 510 <= x <= 770 and 510 <= y <= 590:
            self.opens += 1
            self.advance("confirm", "confirm")
        elif self.scene == "confirm" and 700 <= x <= 1030 and y >= 520:
            self.spent += 1
            self.tickets -= 1
            self.advance("animation", "animation")
        elif self.scene == "animation" and x >= 1110 and y <= 140:
            self.skips += 1
            if not self.never_result:
                self.scene = "result"
        else:
            self.unexpected.append((self.scene, x, y))
            return False
        return True

    def click_key(self, key):
        if self.scene == "result" and key == 111:
            self.returns += 1
            self.advance("unknown" if self.no_return else "pool", "return")
            return True
        self.unexpected.append((self.scene, "key", key))
        return False


class TicketSummonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Library.open(Path(os.environ.get("MAAFW_BINARY_PATH", Path(maa.__file__).parent / "bin")))
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)
        cls.production = json.loads((ROOT / "assets/resource/base/pipeline/自动抽护符.json").read_text("utf-8"))

    def run_scenario(self, count=1, *, screenshot=None, **kwargs):
        screen = SummonScreen(**kwargs)
        nodes = copy.deepcopy(self.production)
        nodes["自动抽护符开始"]["attach"]["ticket_count"] = count
        task_override = {}
        # 应用界面选项的真实override，检验它能透传到CustomAction内每次run_task。
        if screenshot is not None:
            options = json.loads((ROOT / "assets/options/自动抽护符.json").read_text("utf-8"))["option"]
            setting = options[P + "抽卡完后是否保存截图"]
            case = next(case for case in setting["cases"] if case["name"] == ("yes" if screenshot else "no"))
            task_override = copy.deepcopy(case["pipeline_override"])
        screenshots_at = []

        class Actions(ContextEventSink):
            def on_node_action(self, context, noti_type, detail):
                if noti_type == NotificationType.Starting and detail.name == P + "结果截图":
                    action = context.get_node_data(detail.name)["action"]
                    if action["type"] == "Screencap":
                        screenshots_at.append((screen.scene, screen.returns))

        class Screen(CustomRecognition):
            def analyze(self, context, argv):
                cfg = json.loads(argv.custom_recognition_param)
                name = cfg["node"].removeprefix(P)
                param = cfg["param"]
                text = None
                if screen.network_left:
                    if name != "网络特征": return None
                    if cfg["kind"] == "TemplateMatch": return None
                    text = "やり直す" if param["roi"][0] >= 700 else "ダウンロードサーバーとの通信が切れました"
                elif name == "网络特征": return None
                elif name == "按钮模板":
                    return (588, 531, 114, 39) if screen.scene == "pool" and not screen.bonus and screen.locale == "jp" else None
                elif name == "按钮文字" and screen.scene == "pool":
                    text = f"召唤×{2 if screen.bonus else 1}次" if screen.locale == "cn" else ("2回召喚" if screen.bonus else "1回召喚")
                elif name == "卡池护符消耗" and screen.scene == "pool":
                    text = ("消耗呼符1 持有：" if screen.locale == "cn" else f"消費呼符1（所持：{screen.tickets}）") if screen.tickets else "消費聖晶石3"
                elif name == "确认护符消耗" and screen.scene == "confirm":
                    text = screen.confirm_text or ("聖晶石を3個使用して1回召喚します。" if screen.wrong_currency else f"呼符を1枚使用して{2 if screen.bonus else 1}回召喚します。")
                elif name == "圣晶石消耗文字" and screen.scene == "confirm":
                    text = "聖晶石を3個使用して1回召喚します。" if screen.wrong_currency else "呼符を1枚使用して1回召喚します。"
                elif name == "决定按钮":
                    return (802, 545, 69, 38) if screen.scene == "confirm" else None
                elif name == "结果特征":
                    return (800, 630, 80, 40) if screen.scene == "result" else None
                elif name == "卡池顶栏" and screen.scene == "pool": text = "灵基召唤" if screen.locale == "cn" else "霊基召喚"
                elif name == "卡池召唤按钮" and screen.scene == "pool": text = "召唤×11次" if screen.locale == "cn" else "11回召喚"
                elif screen.warehouse and screen.scene == "confirm":
                    if name == screen.warehouse + "容量类别": text = "サーヴァント" if screen.warehouse == "从者" else "概念礼装"
                    if name == screen.warehouse + "容量已满文字": text = "所持数が上限を超えています"
                if text is not None and any(re.search(pattern, text) for pattern in param.get("expected", [])):
                    if name == "按钮文字": return (588, 531, 114, 39)
                    return (740, 530, 180, 70) if name == "网络特征" else (300, 260, 600, 30)
                return None

        def instrument(recognition, name):
            kind = recognition["type"]
            if kind in ("And", "Or"):
                for child in recognition["param"]["all_of" if kind == "And" else "any_of"]:
                    if isinstance(child, dict):
                        child["recognition"] = instrument(child["recognition"], name)
                return recognition
            return {"type": "Custom", "param": {"custom_recognition": "screen", "custom_recognition_param": {
                "node": name, "kind": kind, "param": recognition.get("param", {})}}}

        for name, value in nodes.items():
            value.update(pre_delay=0, post_delay=0, timeout=60, rate_limit=1)
            if "recognition" in value:
                value["recognition"] = instrument(value["recognition"], name)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "pipeline").mkdir()
            (path / "pipeline/ticket.json").write_text(json.dumps(nodes, ensure_ascii=False), "utf-8")
            resource = Resource()
            resource.use_cpu()
            self.assertTrue(resource.post_bundle(path).wait().succeeded)
            resource.register_custom_recognition("screen", Screen())
            resource.register_custom_action("ticket_summon", runtime.TicketSummonAction())
            self.assertTrue(screen.post_connection().wait().succeeded)
            tasker = Tasker()
            tasker.add_context_sink(Actions())
            self.assertTrue(tasker.bind(resource, screen))
            Tasker.set_log_dir(path / "debug")
            result = tasker.post_task("自动抽护符开始", task_override).wait()
            success = result.succeeded
            tasker.post_stop().wait()
            # 原生日志延迟切换文件；写入持久调试目录后再释放临时目录，避免Windows文件锁。
            Tasker.set_log_dir(ROOT / ".workbuddy/runtime/ticket-summon-tests")
            tasker.post_stop().wait()
            Tasker.set_log_dir("")
            from PIL import Image
            screen.saved_screenshots = [np.asarray(Image.open(file)).copy()
                                        for file in (path / "debug/screencap").glob("*.png")]
            screen.screenshots_at = screenshots_at
        self.assertEqual(screen.unexpected, [])
        return success, screen

    def test_default_one_and_bonus_each_spend_one_ticket(self):
        for bonus in (False, True):
            with self.subTest(bonus=bonus):
                success, screen = self.run_scenario(tickets=1, bonus=bonus)
                self.assertTrue(success)
                self.assertEqual((screen.spent, screen.returns, screen.tickets), (1, 1, 0))

    def test_count_resets_single_submission_budgets_between_draws(self):
        success, screen = self.run_scenario(3)
        self.assertTrue(success)
        self.assertEqual((screen.opens, screen.spent, screen.returns), (3, 3, 3))

    def test_screenshot_default_and_off_never_save(self):
        options = json.loads((ROOT / "assets/options/自动抽护符.json").read_text("utf-8"))["option"]
        self.assertEqual(options[P + "抽卡完后是否保存截图"]["default"], "no")
        for screenshot in (None, False):
            with self.subTest(screenshot=screenshot):
                success, screen = self.run_scenario(2, screenshot=screenshot)
                self.assertTrue(success)
                self.assertEqual(screen.screenshots_at, [])
                self.assertEqual(screen.saved_screenshots, [])

    def test_screenshot_on_saves_each_result_before_return_including_bonus(self):
        for bonus in (False, True):
            with self.subTest(bonus=bonus):
                success, screen = self.run_scenario(3, screenshot=True, bonus=bonus)
                self.assertTrue(success)
                self.assertEqual(screen.screenshots_at, [("result", 0), ("result", 1), ("result", 2)])
                self.assertEqual(len(screen.saved_screenshots), 3)
                for image in screen.saved_screenshots:
                    self.assertTrue(np.all(image == 64), "截图应为返回前的结果页")

    def test_cn_confirmation_and_missing_ticket_button(self):
        success, screen = self.run_scenario(confirm_text="使用1张呼符进行1次召唤。")
        self.assertTrue(success)
        self.assertEqual(screen.spent, 1)
        success, screen = self.run_scenario(tickets=0)
        self.assertFalse(success)
        self.assertEqual(screen.opens, 0)

    def test_cn_button_order_and_cost_without_parentheses(self):
        for bonus in (False, True):
            with self.subTest(bonus=bonus):
                success, screen = self.run_scenario(2, locale="cn", bonus=bonus,
                    confirm_text=f"使用1张呼符进行{2 if bonus else 1}次召唤。")
                self.assertTrue(success)
                self.assertEqual((screen.opens, screen.spent, screen.returns), (2, 2, 2))

    def test_reconnect_each_phase_never_resubmits_consumption(self):
        success, screen = self.run_scenario(network={"pool": 1, "confirm": 1, "animation": 2, "return": 1})
        self.assertTrue(success)
        self.assertEqual((screen.retries, screen.opens, screen.spent, screen.returns), (5, 1, 1, 1))

    def test_network_exhaustion_does_not_start_another_draw(self):
        success, screen = self.run_scenario(2, network={"animation": 8})
        self.assertFalse(success)
        self.assertEqual((screen.retries, screen.opens, screen.spent), (5, 1, 1))

    def test_quartz_or_wrong_ticket_cost_is_never_confirmed(self):
        for kwargs in ({"wrong_currency": True}, {"confirm_text": "呼符を10枚使用して1回召喚します。"},
                       {"confirm_text": "使用10张呼符兑换量子：1000并获赠召唤机会。"},
                       {"confirm_text": "不使用1张护符"}):
            with self.subTest(kwargs=kwargs):
                success, screen = self.run_scenario(**kwargs)
                self.assertFalse(success)
                self.assertEqual(screen.spent, 0)

    def test_confirmation_only_requires_using_one_ticket(self):
        for text in ("使用1张呼符兑换量子：1000并获赠召唤机会。", "使用一张护符",
                     "呼符を1枚使用して11回召喚します。", "呼符を1枚使用します。"):
            with self.subTest(text=text):
                success, screen = self.run_scenario(confirm_text=text)
                self.assertTrue(success)
                self.assertEqual((screen.spent, screen.returns), (1, 1))

    def test_warehouse_full_stops_before_deciding(self):
        for warehouse in ("从者", "礼装"):
            with self.subTest(warehouse=warehouse):
                success, screen = self.run_scenario(warehouse=warehouse)
                self.assertFalse(success)
                self.assertEqual(screen.spent, 0)

    def test_tickets_exhausted_and_unconfirmed_return_stop_remaining_draws(self):
        for kwargs in ({"tickets": 1}, {"no_return": True}):
            with self.subTest(kwargs=kwargs):
                success, screen = self.run_scenario(2, **kwargs)
                self.assertFalse(success)
                self.assertEqual((screen.spent, screen.returns), (1, 1))

    def test_animation_stall_is_bounded_and_does_not_resubmit(self):
        success, screen = self.run_scenario(2, never_result=True)
        self.assertFalse(success)
        self.assertEqual((screen.spent, screen.skips, screen.returns), (1, 80, 0))

    def test_invalid_counts_do_not_touch_game(self):
        for count in (0, -1, True, 1.5, 10000, "bad"):
            with self.subTest(count=count):
                success, screen = self.run_scenario(count)
                self.assertFalse(success)
                self.assertEqual(screen.opens, 0)


if __name__ == "__main__":
    unittest.main()
