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
        source_task = next(task for task in source["task"] if task["name"] == "截图")
        android_task = next(task for task in converted["task"] if task["name"] == "截图")
        self.assertIn("debug/screencap/", source_task["description"])
        self.assertIn("log/screencap/", android_task["description"])
        self.assertNotIn("debug/screencap/", android_task["description"])
        self.assertEqual(converted["version"], source["version"])
        self.assertEqual(source, original)

    def test_prepare_converts_pipeline_focus_and_keeps_desktop_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            screenshot_task = android.read_json(ROOT / "assets/tasks/截图.json")["task"][0]
            source = {
                "version": "v9.8.7",
                "task": [screenshot_task],
                "option": {name: {"cases": [{"name": "native"}]} for name in ("battle_mode", "主线-战斗方式")},
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
            self.assertIn("log/screencap/", interface["task"][0]["description"])
            self.assertEqual(interface["version"], source["version"])
            for path, content in originals.items():
                self.assertEqual(path.read_bytes(), content, str(path.relative_to(root)))


if __name__ == "__main__":
    unittest.main()
