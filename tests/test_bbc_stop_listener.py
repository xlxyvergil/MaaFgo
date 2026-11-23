"""Cover the stop event sequence without touching real processes."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent" / "custom"))
import bbc_stop_listener as listener


class BbcStopListenerTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(listener.mfaalog, "info"))

    def test_stop_event_sequence_scans_once_even_without_bbc(self):
        sink = listener.BbcStopOnTaskStop()
        stop = SimpleNamespace(entry="MaaTaskerPostStop")
        unrelated = SimpleNamespace(entry="自动推主线调度")
        with patch.object(listener.psutil, "process_iter", return_value=[]) as scan:
            sink.on_tasker_task(None, listener.NotificationType.Starting, unrelated)
            scan.assert_not_called()

            sink.on_tasker_task(None, listener.NotificationType.Starting, stop)
            scan.assert_called_once_with(["name"])

            sink.on_tasker_task(None, listener.NotificationType.Succeeded, stop)
            scan.assert_called_once_with(["name"])

    def test_kills_only_matching_executable_names(self):
        gone = SimpleNamespace(
            pid=41, info={"name": "BBchannel.exe"},
            kill=Mock(side_effect=listener.psutil.NoSuchProcess(41)),
        )
        bbc = SimpleNamespace(pid=42, info={"name": "BBchannel64_DEBUG.EXE"}, kill=Mock())
        unrelated = SimpleNamespace(pid=43, info={"name": "BBchannel-helper.exe"}, kill=Mock())
        unavailable = SimpleNamespace(pid=44, info={"name": None}, kill=Mock())
        with patch.object(listener.psutil, "process_iter", return_value=[gone, bbc, unrelated, unavailable]):
            self.assertEqual(listener._kill_bbc_processes(), 1)

        gone.kill.assert_called_once_with()
        bbc.kill.assert_called_once_with()
        unrelated.kill.assert_not_called()
        unavailable.kill.assert_not_called()
