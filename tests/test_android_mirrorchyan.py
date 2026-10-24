"""发布契约：真实附件名、更新项目、ABI 分发及工作流版本/渠道选择。"""
import fnmatch
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/mirrorchyan_release.yml"


class MirrorChyanPublicationTests(unittest.TestCase):
    def setUp(self):
        self.workflow = WORKFLOW.read_text(encoding="utf8")
        # 读取显式矩阵行；不把 YAML 的 on 按布尔值解析，也不引入测试专用依赖。
        self.rows = re.findall(
            r"^          - os: (\S+)\n            arch: (\S+)\n            filename: (\S+)$",
            self.workflow, re.MULTILINE,
        )

    def test_each_platform_selects_only_its_own_release_attachment(self):
        expected = {("win", "x86_64"), ("android", "arm64"), ("android", "amd64")}
        self.assertEqual({(os_, arch) for os_, arch, _ in self.rows}, expected)
        for tag in ("v2.0.11", "v2.0.12-beta.1"):
            assets = {
                ("win", "x86_64"): f"MaaFgo-win-x86_64-{tag}-MXU.zip",
                ("android", "arm64"): f"MaaFGO-android-arm64-v8a-{tag}.apk",
                ("android", "amd64"): f"MaaFGO-android-x86_64-{tag}.apk",
            }
            files = list(assets.values()) + ["MaaFGO-android-arm64-v8a-debug.apk", "release-verification.json"]
            for os_, arch, pattern in self.rows:
                with self.subTest(tag=tag, os=os_, arch=arch):
                    self.assertEqual([name for name in files if fnmatch.fnmatchcase(name, pattern)],
                                     [assets[(os_, arch)]])

    def test_publication_and_client_share_the_project_but_not_desktop_update_fields(self):
        profile = (ROOT / "Android/profile.yaml").read_text(encoding="utf8")
        self.assertRegex(profile, r"(?m)^update:\n  mirrorchyanRid: MaaFgo$")
        self.assertIn("mirrorchyan_rid: MaaFgo", self.workflow)
        self.assertIn("${{ matrix.os }}", self.workflow)
        self.assertIn("${{ matrix.arch }}", self.workflow)
        self.assertIn('extra_zip: "false"', self.workflow)
        self.assertIn("github.repository_owner == 'xlxyvergil'", self.workflow)
        self.assertIn("${{ steps.release.outputs.tag }}", self.workflow)
        self.assertIn("${{ inputs.tag || github.event.release.tag_name }}", self.workflow)

    def test_release_resolution_and_android_channel_execute_correctly(self):
        bash = (Path("C:/Program Files/Git/bin/bash.exe") if os.name == "nt"
                else Path(shutil.which("bash") or "/missing-bash"))
        if not bash.is_file():
            self.skipTest("bash unavailable")
        script = self.workflow.split("        run: |\n", 1)[1].split("      - uses:", 1)[0]
        script = "\n".join(line[10:] for line in script.splitlines())
        # 执行真实工作流脚本；只替换 GitHub API 调用，测试不会联网或上传。
        stub = 'gh() { printf "%s\\n" "$FAKE_RELEASE_TAG"; }\n'
        for tag, channel in [("v2.0.11", "stable"), ("v2.0.12-alpha.1", "beta"),
                             ("v2.0.12-beta.2", "beta"), ("v2.0.12-rc.1", "beta"),
                             ("v2.0.12-dev.1", "beta"), ("v2.0.12-ci.20261024", "beta")]:
            for requested in (tag, ""):
                with self.subTest(tag=tag, requested=requested), tempfile.TemporaryDirectory() as tmp:
                    env = dict(os.environ, RELEASE_TAG=requested, FAKE_RELEASE_TAG=tag, GITHUB_OUTPUT="result.txt")
                    subprocess.run([str(bash), "-e", "-c", stub + script], cwd=tmp, env=env, check=True,
                                   capture_output=True, text=True)
                    values = dict(line.split("=", 1) for line in (Path(tmp) / "result.txt").read_text().splitlines())
                    self.assertEqual(values, {"tag": tag, "android_channel": channel})


if __name__ == "__main__":
    unittest.main()
