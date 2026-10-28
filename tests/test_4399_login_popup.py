"""4399 登录弹窗资源接线：模板、固定关闭坐标、默认禁用和 4399 选项启用。"""
import json
from pathlib import Path
import unittest

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
LOGIN = json.loads((ROOT / "assets/resource/cn/pipeline/login.json").read_text("utf-8"))
POPUP = json.loads((ROOT / "assets/resource/cn/pipeline/4399登录弹窗.json").read_text("utf-8"))
OPTIONS = json.loads((ROOT / "assets/options/包名填写.json").read_text("utf-8"))

FEATURES = {
    "4399-关闭弹窗-前往游戏盒领取-小": {
        "template": "4399/4399-前往游戏盒领取-小.png",
        "size": (121, 31),
        "threshold": 0.9,
        "target": [770, 219, 12, 14],
    },
    "4399-关闭弹窗-前往游戏盒领取-大": {
        "template": "4399/4399-前往游戏盒领取-大.png",
        "size": (161, 33),
        "threshold": 0.9,
        "target": [815, 170, 16, 20],
    },
    "4399-关闭弹窗-我知道了前往查看": {
        "template": "4399/4399-我知道了前往查看.png",
        "size": (144, 21),
        "threshold": 0.78,
        "roi": [500, 440, 300, 80],
        "target": [769, 252, 15, 11],
    },
    "4399-关闭弹窗-我知道了前往查看-资料更新": {
        "template": "4399/4399-我知道了前往查看.png",
        "size": (144, 21),
        "threshold": 0.78,
        "roi": [500, 380, 300, 60],
        "target": [768, 236, 16, 16],
    },
}
LOGIN_ENTRIES = (
    "关闭公告", "关闭公告后异常处理添加", "关闭观看", "可能已经完成登录",
    "处理完成，等待动画", "开始更新", "开始游戏", "打开游戏", "更新",
    "特殊情况1", "登录", "阶段判断循环",
)


class LoginPopupWiringTests(unittest.TestCase):
    def test_each_feature_has_template_fixed_close_and_loop_back(self):
        for name, expected in FEATURES.items():
            with self.subTest(node=name):
                node = POPUP[name]
                self.assertIs(node["enabled"], False)
                self.assertEqual(node["recognition"]["type"], "TemplateMatch")
                self.assertEqual(node["recognition"]["param"]["template"], expected["template"])
                self.assertEqual(node["recognition"]["param"]["threshold"], expected["threshold"])
                self.assertEqual(node["recognition"]["param"].get("roi"), expected.get("roi"))
                self.assertEqual(node["action"]["type"], "Click")
                self.assertEqual(node["action"]["param"]["target"], expected["target"])
                self.assertEqual(node["rate_limit"], 200)
                self.assertEqual(node["max_hit"], 5)
                self.assertEqual(node["next"], ["阶段判断循环"])
                template = ROOT / "assets/resource/cn/image" / expected["template"]
                with Image.open(template) as image:
                    self.assertEqual(image.mode, "RGB")
                    self.assertEqual(image.size, expected["size"])

    def test_login_stages_check_all_popup_nodes_before_acting(self):
        for entry in LOGIN_ENTRIES:
            with self.subTest(entry=entry):
                self.assertEqual(LOGIN[entry]["next"][:4], list(FEATURES))

    def test_only_4399_package_case_enables_the_features(self):
        case = next(c for c in OPTIONS["option"]["包名"]["cases"] if c["name"] == "4399服")
        for name in FEATURES:
            self.assertIs(case["pipeline_override"][name]["enabled"], True)
        for other in OPTIONS["option"]["包名"]["cases"]:
            if other["name"] == "4399服":
                continue
            for name in FEATURES:
                self.assertNotIn(name, other["pipeline_override"])

    def test_no_jumpback_dispatcher_remains(self):
        for entry in LOGIN.values():
            for name in entry.get("next", []):
                self.assertNotIn(name, ("4399-关闭弹窗", "[JumpBack]4399-关闭弹窗"))


if __name__ == "__main__":
    unittest.main()
