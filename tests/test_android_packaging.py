"""Android PI 转换回归：BBC 隔离、选项依赖、动态图片选择及源文件保护。"""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("prepare_android", ROOT / "tools/prepare_android.py")
android = importlib.util.module_from_spec(spec)
spec.loader.exec_module(android)


class AndroidInterfaceTests(unittest.TestCase):
    def test_mxu_defaults_become_pi_default_case(self):
        source = self.fixture()
        source["option"]["native_setting"] = {
            "type": "select", "default": "first_support",
            "cases": [{"name": "custom_support"}, {"name": "first_support"}],
        }
        converted = android.adapt_interface(source, ROOT / "assets")["option"]["native_setting"]
        self.assertEqual(converted["default_case"], "first_support")
        self.assertNotIn("default", converted)
        self.assertEqual(source["option"]["native_setting"]["default"], "first_support")

    def test_direct_hit_wait_has_an_explicit_area(self):
        nodes = {
            "navigation": {"post_wait_freezes": 1000},
            "explicit": {"pre_wait_freezes": {"time": 500, "target": [10, 20, 30, 40]}},
            "matched": {"recognition": {"type": "TemplateMatch"}, "post_wait_freezes": 800},
            "disabled": {"post_wait_freezes": 0},
        }
        android.adapt_wait_freezes(nodes)
        self.assertEqual(nodes["navigation"]["post_wait_freezes"], {"time": 1000, "target": [0, 0, 1280, 720]})
        self.assertEqual(nodes["explicit"]["pre_wait_freezes"]["target"], [10, 20, 30, 40])
        self.assertEqual(nodes["matched"]["post_wait_freezes"], 800)
        self.assertEqual(nodes["disabled"]["post_wait_freezes"], 0)

    def test_bilibili_launch_conversion_is_limited_to_known_start_actions(self):
        def action(kind, package):
            return {"action": {"type": kind, "param": {"package": package}}}

        nodes = {
            "cold_start": action("StartApp", "com.bilibili.fatego/.EmptyClass"),
            "retry_start": action("StartApp", "com.bilibili.fatego/com.bilibili.fatego.EmptyClass"),
            "already_unity": action("StartApp", "com.bilibili.fatego/.UnityPlayerNativeActivity"),
            "stop": action("StopApp", "com.bilibili.fatego"),
            "stop_component": action("StopApp", "com.bilibili.fatego/.EmptyClass"),
            "other_bilibili_activity": action("StartApp", "com.bilibili.fatego/.OtherActivity"),
            "jp": action("StartApp", "com.aniplex.fategrandorder/jp.delightworks.Fgo.player.AndroidPlugin"),
            "metadata": {"attach": {"package": "com.bilibili.fatego"}},
        }
        original = copy.deepcopy(nodes)
        android.adapt_android_start_apps(nodes)
        for name in ("cold_start", "retry_start"):
            self.assertEqual(nodes[name]["action"]["param"]["package"], android.BILIBILI_ANDROID_GAME_ACTIVITY)
        for name in set(nodes) - {"cold_start", "retry_start"}:
            self.assertEqual(nodes[name], original[name], name)
        converted = copy.deepcopy(nodes)
        android.adapt_android_start_apps(nodes)
        self.assertEqual(nodes, converted)

    def test_prepare_routes_bilibili_starts_to_unity_and_preserves_source_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.fixture()
            source["option"]["native_setting"]["pipeline_override"] = {
                "repeat_launch": {"action": {"type": "StartApp", "param": {"package": "com.bilibili.fatego"}}},
            }
            self.write_prepare_support(root)
            android.write_json(root / "assets/interface.json", source)
            cn_login = {
                "打开游戏": {"action": {"type": "StartApp", "param": {"package": "com.bilibili.fatego"}},
                           "next": ["阶段判断循环"]},
                "登录-关闭游戏": {"action": {"type": "StopApp", "param": {"package": "com.bilibili.fatego"}}},
            }
            android.write_json(root / "assets/resource/cn/pipeline/login.json", cn_login)
            jp_login = {"打开游戏": {"action": {"type": "StartApp", "param": {
                "package": "com.aniplex.fategrandorder/jp.delightworks.Fgo.player.AndroidPlugin"}}}}
            android.write_json(root / "assets/resource/jp/pipeline/run_FGO.json", jp_login)
            originals = {path: path.read_bytes() for path in (root / "assets").rglob("*") if path.is_file()}

            output = root / "Android/assets"
            android.prepare(root, output)
            converted = android.read_json(output / "resource/cn/pipeline/login.json")
            self.assertEqual(converted["打开游戏"]["action"]["param"]["package"], android.BILIBILI_ANDROID_GAME_ACTIVITY)
            self.assertEqual(converted["打开游戏"]["next"], cn_login["打开游戏"]["next"])
            self.assertEqual(converted["登录-关闭游戏"], cn_login["登录-关闭游戏"])
            self.assertEqual(android.read_json(output / "resource/jp/pipeline/run_FGO.json"), jp_login)
            self.assertEqual(android.read_json(output / "resource/cn/pipeline/关闭游戏.json")["关闭游戏"]["action"]["param"]["package"],
                             "com.bilibili.fatego")
            interface = android.read_json(output / "interface.json")
            repeat = interface["option"]["native_setting"]["pipeline_override"]["repeat_launch"]
            self.assertEqual(repeat["action"]["param"]["package"], android.BILIBILI_ANDROID_GAME_ACTIVITY)
            self.assertEqual(interface["version"], source["version"])
            for path, data in originals.items():
                self.assertEqual(path.read_bytes(), data, str(path.relative_to(root)))

    def write_prepare_support(self, root):
        android.write_json(root / "assets/resource/base/pipeline/日常战斗.json", {"通用战斗调度": {}})
        for server, package in (("cn", "com.bilibili.fatego"), ("jp", "com.aniplex.fategrandorder")):
            android.write_json(root / f"assets/resource/{server}/pipeline/关闭游戏.json", {
                "关闭游戏": {"action": {"type": "StopApp", "param": {"package": package}}, "next": ["关闭bbc"]},
                "关闭bbc": {},
            })
        for directory in ("assets/i18n", "agent", "Android", "assets/resource/model/ocr"):
            (root / directory).mkdir(parents=True, exist_ok=True)
        (root / "Android/Announcement.md").write_text("fixture", encoding="utf-8")
        (root / "LICENSE").write_text("fixture", encoding="utf-8")
        for name in ("rec.onnx", "det.onnx", "keys.txt"):
            (root / "assets/resource/model/ocr" / name).write_bytes(b"fixture")

    def test_prepare_omits_desktop_editor_but_preserves_native_formation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_prepare_support(root)
            source = self.fixture()
            source["task"].append({
                "name": "打开编队与战斗编辑器", "option": ["编队编辑器操作"],
                "doc": "docs/desktop-only-guide.md",
            })
            source["preset"][0]["task"].append({"name": "打开编队与战斗编辑器"})
            source["option"]["编队编辑器操作"] = {"type": "select", "cases": [{"name": "open"}]}
            source["task"].append({
                "name": "__MXU_KILLPROC__", "entry": "MXU_KILLPROC",
                "option": ["__MXU_KILLPROC_SELF_OPTION__"],
            })
            source["preset"][0]["task"].append({"name": "__MXU_KILLPROC__"})
            source["option"]["__MXU_KILLPROC_SELF_OPTION__"] = {
                "type": "switch", "default_case": "Yes",
                "cases": [{"name": "Yes"}, {"name": "No"}],
            }
            description = "保留移动端说明\n\n[打开编队编辑器](http://127.0.0.1:18765/)"
            source["task"][0]["description"] = description
            source["option"]["native_setting"]["description"] = description
            android.write_json(root / "assets/interface.json", source)
            android.write_json(root / "assets/resource/base/pipeline/编队与战斗编辑器.json", {
                "打开编队与战斗编辑器": {"action": {"type": "Custom", "param": {
                    "custom_action": "open_battle_editor"}}},
            })
            desktop_files = (
                "assets/web/battle-editor/index.html", "assets/web/battle-editor/assets/app.js",
                "agent/custom/battle_editor_action.py", "agent/battle_editor/server.py",
                "agent/battle_editor/chaldea_image.py",
                "agent/battle_editor/common_teams.py", "agent/battle_editor/data/grand_quests.json",
                "agent/battle_editor/data/catalog.json", "agent/battle_editor/data/np_cards.json",
                "agent/battle_editor/data/ui/media/formation_100100_0123456789abcdef.webp",
                "打开编队编辑器.cmd",
            )
            runtime_files = (
                "agent/custom/formation_action.py", "agent/custom/bond_completion_action.py",
                "agent/custom/auto_battle_action.py", "agent/battle/runtime/formation_session.py",
                "agent/battle/core/validator.py", "agent/chaldea/bbc_formatter.py",
                "assets/resource/base/image/ServantFaces/team/100100.png",
                "assets/resource/base/image/EquipFaces/team/f_94000100.png",
            )
            for relative in desktop_files + runtime_files:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")
            for name in ("自动编队", "羁绊补齐自动编队"):
                android.write_json(root / f"assets/resource/base/pipeline/{name}.json", {name: {}})
            originals = {path: path.read_bytes() for folder in (root / "assets", root / "agent")
                         for path in folder.rglob("*") if path.is_file()}

            output = root / "Android/assets"
            android.prepare(root, output)
            for relative in ("web", "agent/battle_editor", "agent/custom/battle_editor_action.py",
                             "resource/base/pipeline/编队与战斗编辑器.json", "打开编队编辑器.cmd"):
                self.assertFalse((output / relative).exists(), relative)
            for relative in runtime_files:
                packaged = relative.removeprefix("assets/")
                self.assertEqual((output / packaged).read_bytes(), (root / relative).read_bytes(), packaged)
            for name in ("自动编队", "羁绊补齐自动编队"):
                self.assertTrue((output / f"resource/base/pipeline/{name}.json").is_file())
            converted = android.read_json(output / "interface.json")
            self.assertNotIn("打开编队与战斗编辑器", {task["name"] for task in converted["task"]})
            self.assertNotIn("打开编队与战斗编辑器", {task["name"] for task in converted["preset"][0]["task"]})
            self.assertNotIn("编队编辑器操作", converted["option"])
            self.assertNotIn("__MXU_KILLPROC__", {task["name"] for task in converted["task"]})
            self.assertNotIn("__MXU_KILLPROC__", {task["name"] for task in converted["preset"][0]["task"]})
            self.assertNotIn("__MXU_KILLPROC_SELF_OPTION__", converted["option"])
            self.assertNotIn("127.0.0.1:18765", json.dumps(converted, ensure_ascii=False))
            self.assertEqual(converted["task"][0]["description"], "保留移动端说明")
            self.assertEqual(converted["option"]["native_setting"]["description"], "保留移动端说明")
            self.assertEqual(converted["version"], source["version"])
            for path, data in originals.items():
                self.assertEqual(path.read_bytes(), data, str(path.relative_to(root)))

    def fixture(self):
        return {
            "version": "v9.8.7", "mirrorchyan_rid": "MaaFgo", "mirrorchyan_multiplatform": True,
            "controller": [{"name": "安卓设备", "type": "Adb"}],
            "task": [{"name": "登录"}, {"name": "启动bbc"}, {"name": "关闭bbc"},
                     {"name": "BBC 配置转 Chaldea 本地队伍"},
                     {"name": "战斗", "option": ["battle_mode", "主线-战斗方式"]}],
            "preset": [{"name": "default", "task": [{"name": "登录"}, {"name": "启动bbc"}, {"name": "战斗"}]}],
            "option": {
                "battle_mode": {"default": "bbc", "cases": [
                    {"name": "bbc", "option": ["windows_path"]}, {"name": "native", "option": ["native_setting"]}]},
                "主线-战斗方式": {"default": "native", "cases": [{"name": "native"}, {"name": "bbc"}]},
                "native_setting": {"type": "input", "inputs": []},
                "windows_path": {"type": "input", "inputs": []},
            },
        }

    def test_native_only_and_does_not_mutate_desktop(self):
        source = self.fixture()
        before = copy.deepcopy(source)
        result = android.adapt_interface(source, ROOT / "assets")
        self.assertEqual(source, before)
        self.assertEqual(result["version"], "v9.8.7")
        self.assertEqual([t["name"] for t in result["task"]], ["登录", "战斗"])
        self.assertEqual([t["name"] for t in result["preset"][0]["task"]], ["登录", "战斗"])
        self.assertNotIn("windows_path", result["option"])
        self.assertNotIn("mirrorchyan_rid", result)
        self.assertEqual(result["option"]["battle_mode"]["default_case"], "native")
        self.assertEqual([c["name"] for c in result["option"]["battle_mode"]["cases"]], ["native"])

    def test_missing_option_fails_build(self):
        source = self.fixture()
        del source["option"]["native_setting"]
        with self.assertRaisesRegex(ValueError, "native_setting"):
            android.adapt_interface(source, ROOT / "assets")

    def test_new_backend_selector_prunes_bbc_before_scanning_files(self):
        # 新任务不能依赖转换脚本维护选项名白名单；干净 checkout 没有 BBC 配置目录。
        for name in ("Free-战斗方式", "新增任务-战斗方式"):
            with self.subTest(selector=name), tempfile.TemporaryDirectory() as tmp:
                source = self.fixture()
                source["task"][-1]["option"].append(name)
                native = {"name": "native", "option": ["native_setting"],
                          "pipeline_override": {"Free-配置": {"attach": {"battle_mode": "native"}}}}
                source["option"][name] = {"type": "select", "default": "bbc", "default_case": "bbc",
                                          "cases": [{"name": "bbc", "option": ["bbc_files"]}, native]}
                source["option"]["bbc_files"] = {
                    "type": "scan_select", "scan_dir": "BBchannel/settings", "scan_filter": "*.json"}
                original = copy.deepcopy(source)
                converted = android.adapt_interface(source, Path(tmp))
                option = converted["option"][name]
                self.assertEqual(option["cases"], [native])
                self.assertEqual(option["default_case"], "native")
                self.assertNotIn("default", option)
                self.assertNotIn("bbc_files", converted["option"])
                self.assertEqual(source, original)

    def test_scan_select_becomes_standard_pi_with_real_filenames(self):
        with tempfile.TemporaryDirectory() as tmp:
            assets = Path(tmp)
            folder = assets / "resource/base/image/lizhuang/满破"
            folder.mkdir(parents=True)
            for name in ("礼装.png", "0_空.png"):
                (folder / name).write_bytes(b"fixture")
            source = self.fixture()
            source["option"]["native_setting"] = {
                "type": "scan_select", "scan_dir": "resource/base/image/lizhuang/满破", "scan_filter": "*.png",
                "pipeline_override": {"助战action": {"attach": {"native_setting": "{native_setting}"}}},
            }
            option = android.adapt_interface(source, assets)["option"]["native_setting"]
            self.assertEqual(option["type"], "select")
            self.assertEqual(option["default_case"], "0_空.png")
            self.assertEqual(option["cases"][0]["label"], "不限")
            self.assertEqual(option["cases"][1]["icon"], "resource/base/image/lizhuang/满破/礼装.png")
            self.assertEqual(option["cases"][1]["pipeline_override"]["助战action"]["attach"]["native_setting"], "礼装.png")
            self.assertNotIn("scan_dir", option)

    def test_scan_default_and_mxu_attach_assignment_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            assets = Path(tmp)
            (assets / "images").mkdir()
            (assets / "images/0_空.png").write_bytes(b"fixture")
            (assets / "images/目标.png").write_bytes(b"fixture")
            (assets / "images/目录.png").mkdir()
            source = self.fixture()
            source["option"]["native_setting"] = {
                "type": "scan_select", "scan_dir": "images", "scan_filter": "*.png", "default": "目标.png",
                "pipeline_override": {"node": {"attach": {"native_setting": "", "other": 7}}},
            }
            converted = android.adapt_interface(source, assets)["option"]["native_setting"]
            self.assertEqual(converted["default_case"], "目标.png")
            self.assertEqual(len(converted["cases"]), 2)
            self.assertEqual(converted["cases"][1]["pipeline_override"]["node"]["attach"],
                             {"native_setting": "目标.png", "other": 7})
            source["option"]["native_setting"]["default"] = "已删除.png"
            with self.assertRaisesRegex(ValueError, "默认文件不存在"):
                android.adapt_interface(source, assets)

    def test_file_button_falls_back_to_standard_text_input(self):
        source = self.fixture()
        source["option"]["native_setting"] = {
            "type": "input", "inputs": [{"name": "source", "input_type": "file", "default": "12345"}],
            "pipeline_override": {"node": {"attach": {"source": "{source}"}}},
        }
        converted = android.adapt_interface(source, ROOT / "assets")["option"]["native_setting"]
        self.assertNotIn("input_type", converted["inputs"][0])
        self.assertEqual(converted["inputs"][0]["pipeline_type"], "string")
        self.assertEqual(converted["inputs"][0]["default"], "12345")
        self.assertIn("手机", converted["inputs"][0]["description"])
        self.assertEqual(converted["pipeline_override"], source["option"]["native_setting"]["pipeline_override"])

    def test_mxu_task_doc_becomes_standard_task_description(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "assets").mkdir()
            (root / "docs").mkdir()
            (root / "docs/guide.md").write_text("# 使用说明\n正文", encoding="utf-8")
            source = self.fixture()
            source["task"][0].update(description="任务简介", doc="docs/guide.md")
            converted = android.adapt_interface(source, root / "assets")
            self.assertEqual(converted["task"][0]["description"], "任务简介\n\n# 使用说明\n正文")
            self.assertNotIn("doc", converted["task"][0])

    def test_project_extensions_are_all_converted(self):
        source = android.load_interface(ROOT / "assets")
        converted = android.adapt_interface(source, ROOT / "assets")
        free = converted["option"]["Free-战斗方式"]
        self.assertEqual(free["default_case"], "native")
        self.assertEqual([case["name"] for case in free["cases"]], ["native"])
        self.assertNotIn("bbc_team_config", converted["option"])
        self.assertIn("自动清free本", {task["name"] for task in converted["task"]})
        report = android.compatibility_report(source, converted)
        self.assertEqual(len(report["file_lists"]), 6)
        self.assertEqual(
            {(item["option"], item["input"]) for item in report["file_inputs_as_text"]},
            {("Chaldea导入手动输入", "chaldea_import_source"),
             ("地狱的试炼-队伍方案", "chaldea_import_source")},
        )
        for item in report["file_inputs_as_text"]:
            name, field_name = item["option"], item["input"]
            original = source["option"][name]
            option = converted["option"][name]
            original_field = next(field for field in original["inputs"] if field["name"] == field_name)
            field = next(field for field in option["inputs"] if field["name"] == field_name)
            for desktop_key in ("input_type", "file_dialog_dir", "file_dialog_filters"):
                self.assertNotIn(desktop_key, field)
            self.assertEqual(field["pipeline_type"], "string")
            self.assertEqual(field["default"], original_field["default"])
            self.assertEqual(option["pipeline_override"], original["pipeline_override"])
        self.assertEqual(len(report["task_docs_in_description"]), 1)
        for name, option in converted["option"].items():
            self.assertIn(option.get("type", "select"), android.ANDROID_OPTION_TYPES)
            if source["option"][name].get("type") != "scan_select":
                continue
            for case in option["cases"]:
                self.assertTrue((ROOT / "assets" / case["icon"]).is_file())
                self.assertEqual(case["pipeline_override"]["助战action"]["attach"][name], case["name"])

    def test_project_interface_omits_editor_without_mutating_desktop(self):
        source = android.load_interface(ROOT / "assets")
        original = copy.deepcopy(source)
        self.assertIn("打开编队与战斗编辑器", {task["name"] for task in source["task"]})
        self.assertIn("编队编辑器操作", source["option"])
        self.assertIn("127.0.0.1:18765", json.dumps(source, ensure_ascii=False))

        converted = android.adapt_interface(source, ROOT / "assets")
        self.assertEqual(source, original)
        self.assertEqual(android.load_interface(ROOT / "assets"), original)
        self.assertEqual(converted["version"], source["version"])
        self.assertNotIn("打开编队与战斗编辑器", {task["name"] for task in converted["task"]})
        self.assertNotIn("编队编辑器操作", converted["option"])
        for marker in ("127.0.0.1:18765", "open_battle_editor", "打开编队与战斗编辑器"):
            self.assertNotIn(marker, json.dumps(converted, ensure_ascii=False))
        self.assertIn("Chaldea 自动编队", {task["name"] for task in converted["task"]})

    def test_unsupported_option_type_fails_instead_of_disappearing(self):
        source = self.fixture()
        source["option"]["native_setting"] = {"type": "desktop_extension"}
        with self.assertRaisesRegex(ValueError, "安卓外壳不支持"):
            android.adapt_interface(source, ROOT / "assets")

    def test_cyclic_import_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            assets = Path(tmp)
            (assets / "interface.json").write_text(json.dumps({"import": ["interface.json"]}), encoding="utf8")
            with self.assertRaisesRegex(ValueError, "循环"):
                android.load_interface(assets)

    def test_import_cannot_escape_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            assets = Path(tmp)
            (assets / "interface.json").write_text(json.dumps({"import": ["../private.json"]}), encoding="utf8")
            with self.assertRaisesRegex(ValueError, "越出"):
                android.load_interface(assets)

    def test_rejects_output_outside_generated_directory(self):
        with self.assertRaisesRegex(ValueError, "输出目录"):
            # 不需要读取当前仓库中尚未提交的本地 import。
            from unittest.mock import patch
            with patch.object(android, "load_interface", return_value=self.fixture()):
                android.prepare(ROOT, ROOT / "assets")

    def test_only_windows_actions_are_excluded_from_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            for name in ("custom/bbc_start.py", "custom/bbc_connection_manager.py",
                         "custom/auto_battle_action.py", "chaldea/bbc_formatter.py", "chaldea/bbc_importer.py",
                         "chaldea/bbc_servant_ids.json"):
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("# fixture", encoding="utf8")
            target = Path(tmp) / "target"
            android.copy_agent(source, target)
            self.assertFalse((target / "custom/bbc_start.py").exists())
            self.assertFalse((target / "custom/bbc_connection_manager.py").exists())
            self.assertTrue((target / "custom/auto_battle_action.py").exists())
            self.assertTrue((target / "chaldea/bbc_formatter.py").exists())
            self.assertTrue((target / "chaldea/bbc_importer.py").exists())
            self.assertFalse((target / "chaldea/bbc_servant_ids.json").exists())


if __name__ == "__main__":
    unittest.main()
