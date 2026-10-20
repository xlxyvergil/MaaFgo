"""资源检查工作流的重试和失败传播；不下载依赖或连接设备。"""
from pathlib import Path
import re
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


def find_bash():
    executable = shutil.which("bash")
    if executable:
        return executable
    git = shutil.which("git")
    if git:
        executable = Path(git).resolve().parents[1] / "bin/bash.exe"
        if executable.is_file():
            return str(executable)
    return None


BASH = find_bash()


class ResourceWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = (ROOT / ".github/workflows/check.yml").read_text("utf-8")

    def test_checker_version_and_framework_are_pinned(self):
        self.assertIn("npx --yes @nekosu/maa-tools@1.1.3 check", self.workflow)
        config = (ROOT / "maatools.config.mts").read_text("utf-8")
        self.assertIn("maaVersion: '5.14.3'", config)
        self.assertNotIn("maaVersion: 'latest'", config)
        self.assertEqual(self.workflow.count('- "maatools.config.mts"'), 2)

    def test_schema_check_continues_after_checker_failure(self):
        for name in ("Install maafw", "Validate JSON Schema"):
            self.assertIn(f"- name: {name}\n        if: ${{{{ !cancelled() }}}}", self.workflow)
        self.assertNotIn("continue-on-error", self.workflow)

    @unittest.skipUnless(BASH, "bash required for workflow shell verification")
    def test_retry_shell_preserves_success_and_failure(self):
        match = re.search(
            r"- name: Run maa-checker\n        run: \|\n(?P<run>(?:          .*\n|\n)*)",
            self.workflow,
        )
        self.assertIsNotNone(match)
        script = "\n".join(line[10:] for line in match.group("run").splitlines())
        for failures, expected_calls, expected_code in ((0, 1, 0), (1, 2, 0), (3, 3, 1)):
            with self.subTest(failures=failures):
                # 以 shell 函数替换外部程序，执行工作流原始脚本验证实际退出码。
                stub = f"""set -e
calls=0
npm() {{ return 0; }}
sleep() {{ :; }}
npx() {{
  calls=$((calls + 1))
  echo "CHECKER_CALL_$calls"
  if [ "$calls" -le {failures} ]; then return 1; fi
  return 0
}}
"""
                result = subprocess.run(
                    [BASH, "--noprofile", "--norc"], input=stub + script,
                    capture_output=True, text=True, encoding="utf-8", timeout=10,
                )
                self.assertEqual(result.returncode, expected_code, result.stdout + result.stderr)
                self.assertEqual(result.stdout.count("CHECKER_CALL_"), expected_calls)


if __name__ == "__main__":
    unittest.main()
