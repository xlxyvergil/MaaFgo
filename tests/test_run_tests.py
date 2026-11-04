"""Static checks must fail before importing native dependencies or running regressions."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("repository_test_runner", ROOT / "tools/run_tests.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class TestRunnerPreflightTests(unittest.TestCase):
    def test_static_check_needs_no_maafw_and_propagates_status(self):
        for status in (0, 1):
            with (
                self.subTest(status=status),
                patch.object(runner.sys, "argv", ["run_tests.py", "--suite", "static"]),
                patch.dict("sys.modules", {"maa": None}),
                patch.object(runner.subprocess, "run", return_value=SimpleNamespace(returncode=status)) as run,
            ):
                self.assertEqual(runner.main(), status)
                run.assert_called_once()
                self.assertEqual(Path(run.call_args.args[0][-1]), ROOT / "tests/test_inline_ocr_names.py")

    def test_failed_static_check_stops_every_regression_suite(self):
        for suite in ("all", "unit", "component"):
            with (
                self.subTest(suite=suite),
                patch.object(runner.sys, "argv", ["run_tests.py", "--suite", suite]),
                patch.dict("sys.modules", {"maa": None}),
                patch.object(runner.subprocess, "run", return_value=SimpleNamespace(returncode=1)) as run,
            ):
                self.assertEqual(runner.main(), 1)
                run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
