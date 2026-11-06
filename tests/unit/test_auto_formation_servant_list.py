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
