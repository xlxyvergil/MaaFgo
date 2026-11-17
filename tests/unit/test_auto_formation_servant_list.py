"""从者仓库卡位特征的合成回归；真实准确率仍以 MaaMCP 实机结果为准。"""
import sys
import unittest
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from formation_test_support import formation as f


ROOT = Path(__file__).resolve().parents[2]


def read_image(path):
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)


class ServantListFeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        face_dir = ROOT / "assets/resource/base/image/servant_face"
        cls.grand = read_image(face_dir / "f_1036300.png")
        cls.normal = read_image(face_dir / "f_1036002.png")

    def page_with_target(self, origin=(472, 202)):
        rng = np.random.default_rng(1036300)
        page = rng.integers(25, 90, (f.BASE_H, f.BASE_W, 3), dtype=np.uint8)
        x, y = origin
        page[y:y + f.SERVANT_LIST_FACE_SIZE, x:x + f.SERVANT_LIST_FACE_SIZE] = self.grand
        strip_y = y + f.SERVANT_LIST_FACE_SIZE
        for card_x in f.SERVANT_LIST_FACE_X:
            page[strip_y:strip_y + 10, card_x:card_x + f.SERVANT_LIST_FACE_SIZE] = (0, 210, 255)
        return page

    def test_grand_face_matches_only_complete_card_positions(self):
        page = self.page_with_target()
        # 整张模板出现在标题栏时，旧的全屏滑窗会优先命中；卡位算法必须忽略它。
        page[0:158, 425:583] = self.normal
        match = f._match_servant_list_cards(
            page,
            [("f_1036002.png", self.normal), ("f_1036300.png", self.grand)],
        )
        self.assertIsNotNone(match)
        self.assertGreaterEqual(match[0], f.SERVANT_LIST_FEATURE_THRESHOLD)
        self.assertGreaterEqual(match[3], f.SERVANT_LIST_FEATURE_MARGIN)
        self.assertEqual(match[1], (551, 281))
        self.assertEqual(match[2], "f_1036300.png")

    def test_partial_edge_row_is_not_returned_as_click_target(self):
        page = self.page_with_target(origin=(472, 80))
        match = f._match_servant_list_cards(page, [("f_1036300.png", self.grand)])
        self.assertTrue(match is None or match[1] != (551, 159))

    def test_sparse_last_row_is_found_below_a_full_row(self):
        for count in (1, 2):
            with self.subTest(last_row_cards=count):
                page = self.page_with_target(origin=(472, 317))
                page[317:475, 472:630] = self.normal
                page[517:675, 97:255] = self.grand
                for x in f.SERVANT_LIST_FACE_X[:count]:
                    page[675:693, x:x + 158] = (0, 210, 255)
                match = f._match_servant_list_cards(page, [("target", self.grand)])
                self.assertTrue(f.AutoFormationFromChaldea._servant_list_match_accepted(match))
                self.assertEqual(match[1], (176, 596))

    def test_jp_halloween_elizabeth_in_two_card_last_row(self):
        page = self.page_with_target(origin=(472, 317))
        crop = read_image(ROOT / "tests/fixtures/servant_list/jp_last_two_cards.png")
        self.assertIsNotNone(crop)
        page[508:698, 90:450] = crop
        face_dir = ROOT / "assets/resource/base/image/servant_face"
        templates = [(f"f_502600{i}.png", read_image(face_dir / f"f_502600{i}.png"))
                     for i in range(5)]
        for image, scale in ((page, 1.0), (cv2.resize(page, (2560, 1440)), 2.0)):
            with self.subTest(scale=scale):
                match = f._match_servant_list_cards(image, templates, scale, scale)
                self.assertTrue(f.AutoFormationFromChaldea._servant_list_match_accepted(match))
                self.assertGreater(match[0], 0.95)
                self.assertEqual(match[1], (int(363 * scale), int(596 * scale)))
                self.assertEqual(match[2], "f_5026003.png")

    def test_bottom_clipped_last_row_is_not_a_click_target(self):
        page = self.page_with_target(origin=(472, 317))
        page[617:720, 97:255] = self.grand[:103]
        _, centers = f._visible_servant_list_features(page)
        self.assertNotIn((176, 696), centers)

    def test_yellow_fragments_in_different_columns_do_not_form_a_row(self):
        page = self.page_with_target(origin=(472, 317))
        for index, x in enumerate(f.SERVANT_LIST_FACE_X):
            page[675 + index:677 + index, x:x + 158] = (0, 210, 255)
        _, centers = f._visible_servant_list_features(page)
        self.assertNotIn((176, 596), centers)

    def test_two_matching_card_positions_report_duplicate_candidates(self):
        page = self.page_with_target()
        page[202:360, 97:255] = self.grand
        for image, scale in ((page, 1.0), (cv2.resize(page, (2560, 1440)), 2.0)):
            with self.subTest(scale=scale):
                match = f._match_servant_list_cards(
                    image, [("f_1036300.png", self.grand)], scale, scale)
                self.assertTrue(f.AutoFormationFromChaldea._servant_list_has_duplicate_candidates(match))

    def test_multiple_templates_for_one_card_are_not_duplicate_candidates(self):
        match = f._match_servant_list_cards(self.page_with_target(), [
            ("first-variant.png", self.grand), ("same-card-variant.png", self.grand.copy()),
        ])
        self.assertTrue(f.AutoFormationFromChaldea._servant_list_match_accepted(match))
        self.assertFalse(f.AutoFormationFromChaldea._servant_list_has_duplicate_candidates(match))


if __name__ == "__main__":
    unittest.main()
