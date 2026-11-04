"""御主礼装资源完整性、国日服真实截图及相似黑服合成负例回归。"""
from hashlib import sha256
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

import cv2
import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent/custom"))
import mystic_code as m


class MysticCodeResourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = json.loads((ROOT / "agent/custom/mystic_code_data.json").read_text(encoding="utf-8"))["mysticCodes"]
        cls.editor = json.loads((ROOT / "agent/battle_editor/data/catalog.json").read_text(encoding="utf-8"))["mysticCodes"]

    def test_native_catalog_covers_every_editor_choice_once(self):
        ids = [item["id"] for item in self.records]
        self.assertEqual(len(ids), len(set(ids)), "御主礼装清单存在重复 ID")
        self.assertTrue(all(type(code_id) is int and code_id > 0 for code_id in ids))
        self.assertEqual(set(ids), {item["id"] for item in self.editor},
                         "编辑器御主礼装更新后，必须同步原生识别资源")
        image_ids = [item[gender + "ImageId"] for item in self.records
                     for gender in ("male", "female")]
        self.assertEqual(len(image_ids), len(set(image_ids)), "不同御主礼装/性别不能共用 imageId")

    def assert_icon(self, path, source):
        self.assertTrue(path.is_file(), f"缺失御主礼装模板：{path}")
        self.assertEqual(sha256(path.read_bytes()).hexdigest(), source["sha256"],
                         f"模板哈希与来源清单不符：{path}")
        self.assertRegex(source["sourceSha256"], r"^[0-9a-f]{64}$")
        self.assertTrue(source["source"])
        with Image.open(path) as image:
            image.load()
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.mode, "RGBA", "识别模板必须保留透明掩码")
            self.assertEqual(list(image.size), source["size"])
            self.assertTrue(all(20 <= value <= 150 for value in image.size))
            alpha = np.asarray(image.getchannel("A"))
            self.assertEqual(int(alpha.min()), 0, "模板不能保留不透明 atlas 黑底")
            self.assertEqual(int(alpha.max()), 255)
            self.assertGreater(int(np.count_nonzero(alpha >= 245)), 100)

    def test_both_genders_and_cn_overrides_match_manifest(self):
        for item in self.records:
            for gender in ("male", "female"):
                with self.subTest(code_id=item["id"], gender=gender):
                    image_id = item[gender + "ImageId"]
                    relative = Path("MysticCodes") / f"masterequip{image_id:05d}.png"
                    self.assert_icon(ROOT / "assets/resource/base/image" / relative,
                                     item["sources"][gender])
                    if gender + "Cn" in item["sources"]:
                        self.assert_icon(ROOT / "assets/resource/cn/image" / relative,
                                         item["sources"][gender + "Cn"])


class MysticCodeScreenshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_dir = ROOT / "tests/fixtures/mystic_code"
        cls.fixtures = json.loads((cls.fixture_dir / "fixtures.json").read_text(encoding="utf-8"))

    def test_equipped_icons_reject_other_uniforms(self):
        width, height = self.fixtures["canvasSize"]
        catalog = m._catalog()
        for fixture in self.fixtures["scenes"]:
            with self.subTest(scene=fixture["scene"], gender=fixture.get("gender"),
                              image=fixture["image"], resource_layer=fixture.get("resourceLayer", "base")):
                resource_layer = fixture.get("resourceLayer", "base")
                self.assertIn(resource_layer, ("base", "cn"))
                roots = [ROOT / "assets/resource/base/image"]
                if resource_layer == "cn":
                    roots.insert(0, ROOT / "assets/resource/cn/image")
                formation = SimpleNamespace(image_roots=roots)
                crop = cv2.imdecode(np.fromfile(self.fixture_dir / fixture["image"],
                                                dtype=np.uint8), cv2.IMREAD_COLOR)
                self.assertIsNotNone(crop)
                x, y, roi_width, roi_height = fixture["roi"]
                self.assertEqual(crop.shape[:2], (roi_height, roi_width))
                canvas = np.zeros((height, width, 3), dtype=np.uint8)
                canvas[y:y + roi_height, x:x + roi_width] = crop
                expected_id = fixture.get("expectedId", self.fixtures["expectedId"])
                confusable_id = fixture.get("confusableId", self.fixtures["confusableId"])
                expected = m.match_icon(canvas, m._icons(formation, catalog[expected_id]),
                                        fixture["scene"])
                wrong = m.match_icon(canvas, m._icons(formation, catalog[confusable_id]),
                                     fixture["scene"])
                self.assertIsNotNone(expected)
                self.assertGreaterEqual(expected.score, m.THRESHOLD)
                if fixture.get("expectInnerFallback"):
                    self.assertIsNotNone(expected.primary_score)
                    self.assertGreaterEqual(expected.primary_score, 0.88)
                    self.assertLess(expected.primary_score, m.THRESHOLD,
                                    "失败实机样本必须覆盖受限内区回退")
                self.assertTrue(x <= expected.center[0] < x + roi_width)
                self.assertTrue(y <= expected.center[1] < y + roi_height)
                if fixture.get("gender"):
                    image_id = catalog[expected_id][fixture["gender"] + "ImageId"]
                    self.assertEqual(Path(expected.template).name,
                                     f"masterequip{image_id:05d}.png",
                                     "实机性别必须命中对应男/女款模板")
                    if resource_layer == "cn" and (roots[0] / "MysticCodes" /
                            f"masterequip{image_id:05d}.png").is_file():
                        self.assertEqual(Path(expected.template).parent, roots[0] / "MysticCodes",
                                         "国服截图应优先使用国服模板覆盖")
                self.assertIsNotNone(wrong)
                self.assertLess(wrong.score, m.THRESHOLD,
                                "当前御主礼装不能被误识别成其他御主服")
                for other_id, other_item in catalog.items():
                    if other_id in (expected_id, confusable_id):
                        continue
                    other = m.match_icon(canvas, m._icons(formation, other_item),
                                         fixture["scene"])
                    self.assertIsNotNone(other)
                    self.assertLess(other.score, m.THRESHOLD,
                                    f"真实截图不能误识别为御主礼装 {other_id}")

    def test_screenshot_fixtures_remain_small_crops(self):
        fixtures = self.fixtures["scenes"] + self.fixtures.get("pageMarkers", [])
        paths = [self.fixture_dir / fixture["image"] for fixture in fixtures]
        # 国日服男女实机服装、页面标记与相似黑服负例，均只保留必要裁区。
        self.assertLess(sum(path.stat().st_size for path in paths), 180_000)
        for path in paths:
            with self.subTest(path=path.name), Image.open(path) as image:
                self.assertLess(image.width * image.height, 160 * 160,
                                "只保留识别 ROI，不引入完整游戏截图")

    def test_localized_page_markers_match_their_resource_layer(self):
        for fixture in self.fixtures["pageMarkers"]:
            with self.subTest(resource_layer=fixture["resourceLayer"]):
                crop = cv2.imdecode(np.fromfile(self.fixture_dir / fixture["image"],
                                                dtype=np.uint8), cv2.IMREAD_COLOR)
                self.assertIsNotNone(crop)
                self.assertEqual(crop.shape[:2], (fixture["roi"][3], fixture["roi"][2]))
                for layer in ("base", "cn"):
                    path = ROOT / "assets/resource" / layer / "image/MysticCodes/选择界面.png"
                    self.assertTrue(path.is_file(), f"缺失页面标记：{path}")
                    template = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
                    self.assertIsNotNone(template)
                    scores = cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED)
                    score = float(cv2.minMaxLoc(scores)[1])
                    if layer == fixture["resourceLayer"]:
                        self.assertGreaterEqual(score, 0.90,
                            "国服技能1/日服SKILL1标记必须在对应页面识别通过")
                    else:
                        self.assertLess(score, 0.90,
                            "不能用另一服的本地化标签代替当前资源层标记")


if __name__ == "__main__":
    unittest.main()
