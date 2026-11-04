"""区域服头像逐文件覆盖与完整仓库头像兼容；只读临时资源，不连接设备。"""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from maa.agent.agent_server import AgentServer


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
from formation_test_support import formation

with patch.object(AgentServer, "custom_action", lambda *args: lambda cls: cls):
    import servant_up_action as servant_up


class ServantFaceResourceOverrideTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.names = [f"f_200600{stage}.png" for stage in range(5)]
        self.other_name = "f_1001000.png"
        self.rng = np.random.default_rng(200600)

    def image(self, shape=(158, 158)):
        return self.rng.integers(0, 256, (*shape, 3), dtype=np.uint8)

    def image_root(self, layer, layout="development"):
        resource = "assets/resource" if layout == "development" else "resource"
        return self.root / layout / resource / layer / "image"

    def write(self, layer, name, image, *, layout="development", family="servant_face"):
        path = self.image_root(layer, layout) / family / name
        path.parent.mkdir(parents=True, exist_ok=True)
        ok, data = cv2.imencode(".png", image)
        self.assertTrue(ok)
        path.write_bytes(data.tobytes())
        return path

    def strengthening(self, package, layout="development"):
        action = servant_up.ExecuteServantUp()
        action._context = NS(get_node_data=lambda _: {"attach": {"resource_package": package}})
        script = self.root / layout / "agent/custom/servant_up_action.py"
        with patch.object(servant_up, "__file__", str(script)):
            action._init_dirs()
        return action

    def formation_action(self):
        action = formation.AutoFormationFromChaldea()
        action._get_servant_info = lambda _: {"id": "200600", "images": self.names}
        return action

    def test_cn_strengthening_prefers_partial_override_and_falls_back_per_file(self):
        for layout in ("development", "packaged"):
            with self.subTest(layout=layout):
                paths = {name: self.write("base", name, self.image(), layout=layout)
                         for name in [*self.names, self.other_name]}
                crop = self.image((65, 119))
                cn = self.write("cn", self.names[2], crop, layout=layout)
                action = self.strengthening("cn", layout)
                for name, base in paths.items():
                    resolved = Path(action._tpl("servant_face/" + name))
                    self.assertEqual(resolved, cn if name == self.names[2] else base)
                    self.assertTrue(resolved.is_file())
                np.testing.assert_array_equal(servant_up._read_tpl(action._tpl(
                    "servant_face/" + self.names[2])), crop)

    def test_jp_strengthening_uses_base_even_when_cn_overrides_exist(self):
        for layout in ("development", "packaged"):
            with self.subTest(layout=layout):
                base = self.write("base", self.names[2], self.image(), layout=layout)
                self.write("cn", self.names[2], self.image((65, 119)), layout=layout)
                action = self.strengthening("jp", layout)
                self.assertEqual(Path(action._tpl("servant_face/" + self.names[2])), base)
                self.assertEqual(servant_up._read_tpl(str(base)).shape[:2], (158, 158))

    def test_strengthening_missing_in_both_layers_stays_unresolved(self):
        self.write("base", self.names[0], self.image())
        self.write("cn", self.names[2], self.image((65, 119)))
        action = self.strengthening("cn")
        self.assertEqual(action._tpl("servant_face/f_9999999.png"), "servant_face/f_9999999.png")

    def test_formation_partial_cn_face_falls_back_to_same_complete_base_stage(self):
        full = self.image()
        self.write("base", self.names[2], full)
        self.write("cn", self.names[2], self.image((65, 119)))
        directories = [str(self.image_root(layer) / "servant_face") for layer in ("cn", "base")]
        loaded = self.formation_action()._load_servant_templates("200600", directories)
        self.assertEqual([name for name, _ in loaded], [self.names[2]])
        np.testing.assert_array_equal(loaded[0][1], full)
        np.testing.assert_array_equal(formation._normalized_servant_list_feature(loaded[0][1]),
                                      formation._normalized_servant_list_feature(full))

    def test_formation_complete_cn_face_keeps_priority_over_same_base_stage(self):
        self.write("base", self.names[2], self.image())
        cn = self.image()
        self.write("cn", self.names[2], cn)
        directories = [str(self.image_root(layer) / "servant_face") for layer in ("cn", "base")]
        loaded = self.formation_action()._load_servant_templates("200600", directories)
        self.assertEqual([name for name, _ in loaded], [self.names[2]])
        np.testing.assert_array_equal(loaded[0][1], cn)

    def test_formation_never_stretches_partial_face_or_restricts_narrow_templates(self):
        crop = self.image((65, 119))
        self.write("cn", self.names[2], crop)
        self.write("base", self.names[2], crop, family="NarrowFigures")
        action = self.formation_action()
        with patch.object(formation.cv2, "resize", side_effect=AssertionError("must not stretch a partial face")):
            self.assertEqual(action._load_servant_templates("200600", [
                str(self.image_root("cn") / "servant_face")]), [])
            narrow = action._load_servant_templates("200600", [
                str(self.image_root("base") / "NarrowFigures")])
        self.assertEqual([name for name, _ in narrow], [self.names[2]])
        np.testing.assert_array_equal(narrow[0][1], crop)


if __name__ == "__main__":
    unittest.main()
