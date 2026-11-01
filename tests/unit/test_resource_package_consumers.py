"""检查各Python识图入口使用实际加载层；不连接设备或发送游戏输入。"""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]
from maa.agent.agent_server import AgentServer
from resource_package import resolve_resource_package

with patch.object(AgentServer, "custom_action", lambda *args: lambda cls: cls):
    import box_action as box
    import servant_up_action as upgrade
    import general_navigation_action as navigation


class ResourceConsumerTests(unittest.TestCase):
    def test_box_and_strengthening_directories_follow_loaded_layer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for layer in ("base", "cn"):
                for folder in ("整理礼物盒", "强化从者", "servant_face"):
                    (root / "resource" / layer / "image" / folder).mkdir(parents=True)
            for loaded, configured in (("base", "cn"), ("cn", "base")):
                context = NS(get_node_data=lambda _name: {"attach": {
                    "resource_package": configured, "loaded_resource_package": loaded}})
                for module, cls, folder in ((box, box.ExecuteBoxTask, "整理礼物盒"),
                                             (upgrade, upgrade.ExecuteServantUp, "强化从者")):
                    with self.subTest(loaded=loaded, consumer=folder):
                        action = cls()
                        action._context = context
                        with patch.object(module, "__file__", str(root / "agent/custom/action.py")):
                            action._init_dirs()
                        self.assertEqual(Path(action.tpl_dir), root / "resource" / loaded / "image" / folder)
                        self.assertEqual(Path(action.tpl_base_dir), root / "resource/base/image" / folder)
                        if module is upgrade:
                            self.assertEqual(Path(action.face_dir), root / "resource" / loaded / "image/servant_face")

    def test_navigation_selects_loaded_layer_before_loading_or_acting(self):
        for loaded, configured in (("base", "cn"), ("cn", "base")):
            def node(name):
                return {"资源包配置": {"attach": {"resource_package": configured,
                                                     "loaded_resource_package": loaded}},
                        "关卡选择": {"recognition": {"param": {"template": ["map/test.png"]}}},
                        "导航入口": {"attach": {"quests": "test"}}}[name]
            with self.subTest(loaded=loaded), \
                    patch.object(navigation, "resolve_quest_dir", side_effect=ValueError("read-only probe")) as directory, \
                    patch.object(navigation, "load_quest_templates") as load:
                navigation.GeneralNavigationAction().run(NS(get_node_data=node), NS(node_name="导航入口"))
                self.assertEqual(directory.call_args.args[1], loaded)
                load.assert_not_called()

    def test_common_resolver_preserves_legacy_options_and_rejects_bad_loaded_marker(self):
        for attach, expected in (({}, "base"), ({"resource_package": "cn"}, "cn"),
                                 ({"resource_package": "jp"}, "base")):
            self.assertEqual(resolve_resource_package(NS(get_node_data=lambda _: {"attach": attach}),
                                                      caller="测试"), expected)
        with self.assertRaisesRegex(ValueError, "实际资源包标记无效"):
            resolve_resource_package(NS(get_node_data=lambda _: {"attach": {"loaded_resource_package": "invalid"}}),
                                     caller="测试")


if __name__ == "__main__":
    unittest.main()
