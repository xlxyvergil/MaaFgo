"""安卓截图提示使用外壳实际日志目录，桌面资源和版本保持不变。"""
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("prepare_android_screenshot", ROOT / "tools/prepare_android.py")
android = importlib.util.module_from_spec(spec)
spec.loader.exec_module(android)


class AndroidScreenshotPathTests(unittest.TestCase):
    def test_task_description_uses_android_log_path_without_mutating_source(self):
        source = android.load_interface(ROOT / "assets")
        original = copy.deepcopy(source)
        converted = android.adapt_interface(source, ROOT / "assets")
        source_descriptions = {task["name"]: task.get("description") for task in source["task"]}
        android_descriptions = {task["name"]: task.get("description") for task in converted["task"]}
        for name in ("截图", "自动抽护符", "每日1石", "常驻每日1抽"):
            with self.subTest(task=name):
                self.assertIn("debug/screencap/", source_descriptions[name])
                self.assertIn("log/screencap/", android_descriptions[name])
                self.assertNotIn("debug/screencap/", android_descriptions[name])
        self.assertEqual(converted["version"], source["version"])
        self.assertEqual(source, original)

    def test_prepare_converts_screenshot_messages_and_keeps_desktop_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            screenshot_task = android.read_json(ROOT / "assets/tasks/截图.json")["task"][0]
            summon_task = android.read_json(ROOT / "assets/tasks/自动抽护符.json")["task"][0]
            summon_options = android.read_json(ROOT / "assets/options/自动抽护符.json")["option"]
            source = {
                "version": "v9.8.7",
                "task": [screenshot_task, summon_task],
                "option": summon_options,
            }
            android.write_json(root / "assets/interface.json", source)
            android.write_json(root / "assets/resource/base/pipeline/日常战斗.json", {"通用战斗调度": {}})
            pipeline = android.read_json(ROOT / "assets/resource/base/pipeline/截图.json")
            source_pipeline = root / "assets/resource/base/pipeline/截图.json"
            android.write_json(source_pipeline, pipeline)
            for server in ("cn", "jp"):
                android.write_json(root / f"assets/resource/{server}/pipeline/关闭游戏.json", {"关闭游戏": {}})
            for folder in ("assets/i18n", "agent", "Android", "assets/resource/model/ocr"):
                (root / folder).mkdir(parents=True, exist_ok=True)
            for name in ("rec.onnx", "det.onnx", "keys.txt"):
                (root / "assets/resource/model/ocr" / name).write_bytes(b"fixture")
            (root / "Android/Announcement.md").write_text("fixture", encoding="utf-8")
            (root / "LICENSE").write_text("fixture", encoding="utf-8")
            originals = {path: path.read_bytes() for path in (root / "assets").rglob("*") if path.is_file()}

            output = root / "Android/assets"
            android.prepare(root, output)

            converted = android.read_json(output / "resource/base/pipeline/截图.json")
            message = converted["截图"]["focus"]["Node.Action.Succeeded"]
            self.assertEqual(message, "截图已保存至 log/screencap/")
            self.assertEqual(converted["截图"]["action"], pipeline["截图"]["action"])
            interface = android.read_json(output / "interface.json")
            task_descriptions = {task["name"]: task["description"] for task in interface["task"]}
            option_name = "自动抽护符-抽卡完后是否保存截图"
            summon_screenshot = interface["option"][option_name]
            for description in (
                task_descriptions["截图"],
                task_descriptions["自动抽护符"],
                summon_screenshot["description"],
            ):
                with self.subTest(description=description):
                    self.assertIn("log/screencap/", description)
                    self.assertNotIn("debug/screencap/", description)
            self.assertEqual(summon_screenshot["default_case"], "no")
            self.assertEqual(summon_screenshot["cases"], summon_options[option_name]["cases"])
            self.assertEqual(interface["version"], source["version"])
            for path, content in originals.items():
                self.assertEqual(path.read_bytes(), content, str(path.relative_to(root)))


if __name__ == "__main__":
    unittest.main()
