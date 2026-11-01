"""编队采集按实际素材层取图，任务旧标记不得串用国日服模板。"""
import copy
import shutil
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_formation_identity as fixtures
from formation_test_support import formation as f


class FormationResourceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.IdentityTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.project = self.fixture.root / "project"
        self.base = self.project / "assets/resource/base/image"
        self.cn = self.project / "assets/resource/cn/image"
        self.base.mkdir(parents=True)
        self.cn.mkdir(parents=True)

    def helper(self, attach):
        before = copy.deepcopy(attach)
        helper = f.AutoFormationFromChaldea()
        helper.context = NS(get_node_data=lambda _name: {"attach": attach})
        with patch.object(f, "_PROJECT_DIR", str(self.project)):
            helper._init_paths()
        self.assertEqual(attach, before)
        return helper

    def test_conflicting_option_still_reads_correct_page_and_all_six_identities(self):
        for loaded, configured, correct, wrong in (
                ("base", "cn", self.base, self.cn),
                ("cn", "base", self.cn, self.base)):
            with self.subTest(loaded=loaded):
                for target in (correct, wrong):
                    shutil.copytree(self.fixture.root / "battle", target / "battle", dirs_exist_ok=True)
                    shutil.copytree(self.fixture.root / "NarrowFigures", target / "NarrowFigures", dirs_exist_ok=True)
                for name in ("配置变更", "开始任务", "战斗开始"):
                    fixtures.image_write(wrong / "battle" / f"{name}.png",
                                np.random.default_rng(19).integers(0, 255, (20, 35, 3), dtype=np.uint8))
                helper = self.helper({"resource_package": configured, "loaded_resource_package": loaded})
                reader = f.FormationIdentityReader(helper.image_roots, self.fixture.calibration, self.fixture.catalog)
                self.assertEqual(Path(reader.page_marker_paths["配置变更"]), correct / "battle/配置变更.png")
                slots = reader.read_frame(self.fixture.image, full_audit=True)
                self.assertEqual([slot.servant_id for slot in slots], ["1000"] * 6)

    def test_legacy_options_and_per_file_fallback_remain_supported(self):
        for value, expected in (("cn", self.cn), ("base", self.base), ("jp", self.base), (None, self.base)):
            helper = self.helper({"resource_package": value})
            self.assertEqual(Path(helper.image_roots[0]), expected)
        fixtures.image_write(self.base / "battle/开始任务.png", self.fixture.images["开始任务"])
        helper = self.helper({"loaded_resource_package": "cn"})
        self.assertEqual(Path(helper._template_path("battle/开始任务.png")), self.base / "battle/开始任务.png")

    def test_invalid_loaded_marker_fails_before_any_template_selection(self):
        with self.assertRaisesRegex(ValueError, "编队实际资源包标记无效"):
            self.helper({"resource_package": "cn", "loaded_resource_package": "invalid"})

    def test_failed_page_check_reports_exact_paths_scores_and_threshold(self):
        reader = self.fixture.reader()
        with patch.object(f.mfaalog, "warning") as warning:
            with self.assertRaisesRegex(ValueError, "not_on_formation_confirmation"):
                reader.read_frame(np.zeros_like(self.fixture.image))
        messages = [call.args[0] for call in warning.call_args_list]
        self.assertEqual(len(messages), 3)
        for name, message in zip(("配置变更", "开始任务", "战斗开始"), messages):
            self.assertIn(str(self.fixture.root / "battle" / f"{name}.png"), message)
            self.assertIn("score=", message)
            self.assertIn("/0.8", message)


if __name__ == "__main__":
    unittest.main()
