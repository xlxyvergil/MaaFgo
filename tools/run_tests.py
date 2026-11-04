"""Run repository-only regressions; each suite gets an isolated interpreter."""
import argparse
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("all", "static", "unit", "component"), default="all")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONUTF8="1")
    # Check inline OCR cache keys before loading MaaFramework or running slower suites.
    result = subprocess.run(
        [sys.executable, "-X", "utf8", str(root / "tests/test_inline_ocr_names.py")],
        cwd=root, env=env,
    )
    if result.returncode or args.suite == "static":
        return result.returncode
    # maafw wheels include native binaries; retain an explicitly configured path.
    import maa
    env.setdefault("MAAFW_BINARY_PATH", str(Path(maa.__file__).parent / "bin"))
    suites = []
    if args.suite in ("all", "unit"):
        suites += [
            ["discover", "-s", "tests", "-p", "test_*.py"],
            ["discover", "-s", "tests/unit", "-p", "test_*.py"],
            ["test_special_skill_option_pipeline"],
        ]
    if args.suite in ("all", "component"):
        suites.append(["discover", "-s", "tests/component", "-p", "test_*.py"])
    for suite in suites:
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "unittest", *suite], cwd=root, env=env,
        )
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
