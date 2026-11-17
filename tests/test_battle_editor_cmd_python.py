"""Exercise CMD interpreter selection through real, isolated Windows processes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import venv


ROOT = Path(__file__).resolve().parents[1]
CMD_NAME = "打开编队编辑器.cmd"


@unittest.skipUnless(os.name == "nt", "CMD interpreter selection is Windows-only")
class CmdPythonSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.version_info < (3, 12):
            raise unittest.SkipTest("A real Python 3.12+ is needed for successful interpreter fixtures")
        cls.sandbox = tempfile.TemporaryDirectory(prefix="MaaFGO CMD Python 中文 ")
        cls.base = Path(cls.sandbox.name)
        cls.template = cls.base / "runtime-template"
        venv.EnvBuilder(with_pip=False).create(cls.template)
        cls.system = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        cls.compiler = next((p for p in (
            cls.system / "Microsoft.NET/Framework64/v4.0.30319/csc.exe",
            cls.system / "Microsoft.NET/Framework/v4.0.30319/csc.exe",
        ) if p.is_file()), None)
        cls.stub = None
        if cls.compiler:
            source = cls.base / "StubPython.cs"
            source.write_text(r'''
using System;
using System.IO;
using System.Diagnostics;
using System.Linq;
class StubPython {
    static int Main(string[] args) {
        string marker = Environment.GetEnvironmentVariable("EDITOR_CMD_TEST_MARKER");
        if (!String.IsNullOrEmpty(marker))
            File.AppendAllText(marker, String.Join("|", Environment.GetCommandLineArgs()) + "\n");
        string realPython = Environment.GetEnvironmentVariable("EDITOR_CMD_TEST_FORWARD_PYTHON");
        if (!String.IsNullOrEmpty(realPython) && args.Length > 0 && args[0] == "-3") {
            var start = new ProcessStartInfo(realPython,
                String.Join(" ", args.Skip(1).Select(value => "\"" + value.Replace("\"", "\\\"") + "\"")));
            start.UseShellExecute = false;
            using (var child = Process.Start(start)) { child.WaitForExit(); return child.ExitCode; }
        }
        Console.Error.WriteLine("STORE_ALIAS_DIAGNOSTIC_MUST_BE_HIDDEN");
        return Int32.Parse(Environment.GetEnvironmentVariable("EDITOR_CMD_TEST_EXIT") ?? "9009");
    }
}
''', encoding="utf-8")
            cls.stub = cls.base / "StubPython.exe"
            completed = subprocess.run(
                [str(cls.compiler), "/nologo", "/target:exe", "/out:" + str(cls.stub), str(source)],
                capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if completed.returncode:
                raise RuntimeError("Cannot build the test-only interpreter stub: " + completed.stdout.decode(errors="replace"))

    @classmethod
    def tearDownClass(cls):
        cls.sandbox.cleanup()

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="case ", dir=self.base))
        self.root = self.folder / "源码 中文 !"
        self.root.mkdir()
        shutil.copy2(ROOT / CMD_NAME, self.root / CMD_NAME)
        self.outside = self.folder / "其他工作目录"
        self.outside.mkdir()
        self.marker = self.folder / "interpreter-probes.txt"
        self.env = dict(os.environ)
        self.env.update({
            "PATH": str(self.system / "System32"),
            "LOCALAPPDATA": str(self.folder / "empty-profile"),
            "ProgramW6432": str(self.folder / "empty-program64"),
            "ProgramFiles": str(self.folder / "empty-program-files"),
            "ProgramFiles(x86)": str(self.folder / "empty-program32"),
            "EDITOR_CMD_TEST_MARKER": str(self.marker),
            "EDITOR_CMD_TEST_EXIT": "9009",
        })
        self.entry = self.root / "agent/battle_editor/launcher.py"
        self.entry.parent.mkdir(parents=True)
        self.entry.write_text(
            "import json,sys\nprint(json.dumps({'executable':sys.executable,'args':sys.argv[1:]}))\n",
            encoding="utf-8",
        )

    def source_layout(self):
        (self.root / "assets").mkdir(exist_ok=True)
        (self.root / "assets/interface.json").write_text("{}", encoding="utf-8")

    def release_layout(self):
        (self.root / "interface.json").write_text("{}", encoding="utf-8")

    def virtual_environment(self, relative):
        destination = self.root / relative
        shutil.copytree(self.template, destination)
        return destination / "Scripts/python.exe"

    def portable_runtime(self):
        # Venv's native Windows redirector also finds pyvenv.cfg one level above
        # the executable. This gives a real interpreter at the release basename.
        return self.runtime_at(self.root / "python")

    def runtime_at(self, destination):
        destination.mkdir(parents=True)
        for executable in (self.template / "Scripts").glob("python*.exe"):
            shutil.copy2(executable, destination / executable.name)
        shutil.copy2(self.template / "pyvenv.cfg", destination.parent / "pyvenv.cfg")
        return destination / "python.exe"

    def stub_interpreter(self, folder):
        if self.stub is None:
            self.skipTest("The Windows .NET compiler is unavailable for negative interpreter fixtures")
        destination = self.root / folder
        destination.mkdir(parents=True, exist_ok=True)
        executable = destination / "python.exe"
        shutil.copy2(self.stub, executable)
        return executable

    def path(self, *folders):
        self.env["PATH"] = os.pathsep.join([*(str(p) for p in folders), str(self.system / "System32")])

    def run_cmd(self, *arguments, cmd_executable=None):
        return subprocess.run(
            [cmd_executable or os.environ.get("COMSPEC", str(self.system / "System32/cmd.exe")),
             "/d", "/c", str(self.root / CMD_NAME), *arguments],
            cwd=self.outside, env=self.env, input="", capture_output=True,
            encoding="utf-8", errors="replace", timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def selected(self, completed):
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        record = next((json.loads(line) for line in completed.stdout.splitlines() if line.startswith("{")), None)
        self.assertIsNotNone(record, completed.stdout + completed.stderr)
        return record

    def failure(self, completed):
        self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        output = completed.stdout + completed.stderr
        self.assertNotIn("service.log", output)
        self.assertNotIn("STORE_ALIAS_DIAGNOSTIC_MUST_BE_HIDDEN", output)
        self.assertNotIn("is not recognized", output)
        return output

    def test_missing_entry_stops_before_interpreter_discovery(self):
        self.source_layout()
        self.entry.unlink()
        stub = self.stub_interpreter("path-python")
        self.path(stub.parent)
        output = self.failure(self.run_cmd())
        self.assertIn("入口", output)
        self.assertIn("完整", output)
        self.assertFalse(self.marker.exists())

    def test_release_missing_runtime_never_borrows_source_or_path_python(self):
        self.release_layout()
        self.source_layout()
        interpreter = self.virtual_environment(".venv")
        self.path(interpreter.parent)
        output = self.failure(self.run_cmd())
        self.assertIn(r"python\python.exe", output)
        self.assertIn("重新解压完整", output)

    def test_release_runtime_wins_without_system_python_and_preserves_arguments(self):
        self.release_layout()
        self.source_layout()
        interpreter = self.portable_runtime()
        self.virtual_environment(".venv")
        record = self.selected(self.run_cmd("--port", "19499", "--no-browser"))
        self.assertEqual(Path(record["executable"]), interpreter)
        self.assertEqual(record["args"], ["--port", "19499", "--no-browser"])

    def test_broken_release_runtime_reports_bundle_error_without_fallback(self):
        self.release_layout()
        self.stub_interpreter("python")
        self.path(Path(sys.executable).parent)
        output = self.failure(self.run_cmd())
        self.assertIn("随包 Python 未能正常运行", output)
        self.assertIn("重新解压完整", output)
        self.assertTrue(self.marker.is_file())

    def test_source_dot_venv_wins_over_venv_and_path(self):
        self.source_layout()
        interpreter = self.virtual_environment(".venv")
        self.virtual_environment("venv")
        self.path(Path(sys.executable).parent)
        record = self.selected(self.run_cmd("--help"))
        self.assertEqual(Path(record["executable"]), interpreter)
        self.assertEqual(record["args"], ["--help"])

    def test_source_local_appdata_install_works_without_python_or_py_on_path(self):
        self.source_layout()
        interpreter = self.runtime_at(Path(self.env["LOCALAPPDATA"]) / "Programs/Python/Python312")
        record = self.selected(self.run_cmd("--no-browser"))
        self.assertEqual(Path(record["executable"]), interpreter)
        self.assertEqual(record["args"], ["--no-browser"])

    def test_broken_appdata_install_falls_through_to_program_files(self):
        self.source_layout()
        appdata = Path(self.env["LOCALAPPDATA"]) / "Programs/Python/Python312"
        appdata.mkdir(parents=True)
        if self.stub is None:
            self.skipTest("The Windows .NET compiler is unavailable for a failed AppData install fixture")
        shutil.copy2(self.stub, appdata / "python.exe")
        # Native 64-bit cmd normalizes ProgramFiles to ProgramW6432 on startup.
        self.env["ProgramW6432"] = self.env["ProgramFiles"]
        interpreter = self.runtime_at(Path(self.env["ProgramFiles"]) / "Python312")
        completed = self.run_cmd()
        record = self.selected(completed)
        self.assertEqual(Path(record["executable"]), interpreter)
        self.assertTrue(self.marker.exists())
        self.assertNotIn("STORE_ALIAS_DIAGNOSTIC_MUST_BE_HIDDEN", completed.stdout + completed.stderr)

    def test_programw6432_install_is_checked_before_program_files_and_path(self):
        self.source_layout()
        interpreter = self.runtime_at(Path(self.env["ProgramW6432"]) / "Python312")
        self.runtime_at(Path(self.env["ProgramFiles"]) / "Python312")
        self.path(Path(sys.executable).parent)
        record = self.selected(self.run_cmd())
        self.assertEqual(Path(record["executable"]), interpreter)

    def test_32bit_cmd_can_use_program_files_when_programw6432_is_missing(self):
        cmd = self.system / "SysWOW64/cmd.exe"
        if not cmd.is_file():
            self.skipTest("32-bit CMD is not available on this Windows installation")
        self.source_layout()
        # WOW64 cmd maps ProgramFiles to ProgramFiles(x86), allowing the third
        # standard-location candidate to be exercised without host installs.
        self.env["ProgramFiles"] = self.env["ProgramFiles(x86)"]
        interpreter = self.runtime_at(Path(self.env["ProgramFiles"]) / "Python312")
        record = self.selected(self.run_cmd(cmd_executable=str(cmd)))
        self.assertEqual(Path(record["executable"]), interpreter)

    def test_project_venv_wins_over_standard_installs(self):
        self.source_layout()
        interpreter = self.virtual_environment("venv")
        self.runtime_at(Path(self.env["LOCALAPPDATA"]) / "Programs/Python/Python312")
        self.runtime_at(Path(self.env["ProgramW6432"]) / "Python312")
        record = self.selected(self.run_cmd())
        self.assertEqual(Path(record["executable"]), interpreter)

    def test_unusable_dot_venv_can_fall_through_to_real_venv(self):
        self.source_layout()
        self.stub_interpreter(".venv/Scripts")
        interpreter = self.virtual_environment("venv")
        completed = self.run_cmd()
        record = self.selected(completed)
        self.assertEqual(Path(record["executable"]), interpreter)
        self.assertNotIn("STORE_ALIAS_DIAGNOSTIC_MUST_BE_HIDDEN", completed.stdout + completed.stderr)

    def test_broken_alias_does_not_mask_later_real_path_python(self):
        self.source_layout()
        alias = self.stub_interpreter("Microsoft/WindowsApps")
        interpreter = self.virtual_environment("path-runtime")
        self.path(alias.parent, interpreter.parent)
        completed = self.run_cmd("--help")
        record = self.selected(completed)
        self.assertEqual(Path(record["executable"]), interpreter)
        self.assertNotIn("STORE_ALIAS_DIAGNOSTIC_MUST_BE_HIDDEN", completed.stdout + completed.stderr)
        self.assertTrue(self.marker.exists())

    def test_only_broken_store_alias_reports_actionable_missing_python_message(self):
        self.source_layout()
        alias = self.stub_interpreter("Microsoft/WindowsApps")
        self.path(alias.parent)
        output = self.failure(self.run_cmd())
        self.assertIn("未找到可用", output)
        self.assertIn("Python 3.12", output)
        self.assertIn("完整 MaaFGO MXU", output)
        self.assertIn(".venv", output)

    def test_older_python_is_distinguished_from_an_unusable_alias(self):
        self.source_layout()
        interpreter = self.stub_interpreter("old-python")
        self.path(interpreter.parent)
        self.env["EDITOR_CMD_TEST_EXIT"] = "2"
        output = self.failure(self.run_cmd())
        self.assertIn("版本低于 3.12", output)
        self.assertNotIn("未找到可用", output)

    def test_generic_py3_can_locate_a_runtime_when_specific_py312_is_unavailable(self):
        self.source_layout()
        executable = self.stub_interpreter("launcher")
        shutil.copy2(executable, executable.parent / "py.exe")
        self.path(executable.parent)
        self.env["EDITOR_CMD_TEST_FORWARD_PYTHON"] = sys.executable
        completed = self.run_cmd("--no-browser")
        record = self.selected(completed)
        self.assertEqual(Path(record["executable"]), Path(sys.executable))
        self.assertEqual(record["args"], ["--no-browser"])
        probes = self.marker.read_text(encoding="utf-8-sig")
        self.assertIn("|-3.12|-c|", probes)
        self.assertIn("|-3|-c|", probes)
        self.assertNotIn("STORE_ALIAS_DIAGNOSTIC_MUST_BE_HIDDEN", completed.stdout + completed.stderr)

    def test_registered_py_launcher_can_work_when_path_python_is_only_an_alias(self):
        launcher = self.system / "py.exe"
        if not launcher.is_file():
            self.skipTest("Classic Windows Python launcher is not installed")
        probe = subprocess.run(
            [str(launcher), "-3.12", "-c", "import sys;print(sys.executable)"],
            capture_output=True, encoding="utf-8", errors="replace", timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if probe.returncode:
            self.skipTest("Python 3.12 is not registered with the classic launcher")
        expected = Path(probe.stdout.strip())
        self.source_layout()
        alias = self.stub_interpreter("Microsoft/WindowsApps")
        self.path(alias.parent, self.system)
        record = self.selected(self.run_cmd("--help"))
        self.assertEqual(Path(record["executable"]), expected)
        self.assertFalse(self.marker.exists(), "A registered runtime should be selected before the alias")

    def test_a_started_entry_failure_only_mentions_an_existing_service_log(self):
        self.source_layout()
        self.virtual_environment(".venv")
        self.entry.write_text("raise RuntimeError('ENTRY_FAILURE_FIXTURE')\n", encoding="utf-8")
        completed = self.run_cmd()
        output = self.failure(completed)
        self.assertIn("ENTRY_FAILURE_FIXTURE", output)
        self.assertIn("Python 已启动", output)
        logfile = self.root / "config/BattleEditor/service.log"
        logfile.parent.mkdir(parents=True)
        logfile.write_text("fixture", encoding="utf-8")
        completed = self.run_cmd()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("service.log", completed.stdout)


if __name__ == "__main__":
    unittest.main()
