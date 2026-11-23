"""手机慢速 OCR 的完整复核预算，以及桌面/安卓资源隔离。"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]
import formation_action as formation
import bond_completion_action as bond


class MobileWaitTests(unittest.TestCase):
    def test_platform_budgets_in_fresh_processes(self):
        script = """
import json,sys
from pathlib import Path
sys.path[:0]=[str(Path('agent').resolve()),str(Path('agent/custom').resolve())]
import formation_action as f, bond_completion_action as b, inventory_scan_action as i, box_action as box
from battle.runtime import runtime as r
print('BUDGETS='+json.dumps([f.SLOT_LAYOUT_VERIFY_TIMEOUT_SECONDS,b.SERVANT_BOND_OCR_TIMEOUT_SECONDS,b.COST_VERIFY_TIMEOUT_SECONDS,i.ICON_LAYOUT_TRANSITION_TIMEOUT_SECONDS,box.LJBH_NAV_TIMEOUT,r._SKILL_ANIM_TIMEOUT_S,r.AutoBattleRuntime._wait_until_enemy_targeted.__defaults__[0]]))
"""
        for platform, expected in [("desktop", [5,5,5,2,10,60,3]), ("android", [30,30,20,8,20,60,20])]:
            with self.subTest(platform=platform):
                env = dict(os.environ, MAAFGO_PLATFORM=platform, PYTHONUTF8="1")
                output = subprocess.check_output([sys.executable, "-X", "utf8", "-c", script], cwd=ROOT, env=env, text=True)
                values = json.loads(next(line[8:] for line in output.splitlines() if line.startswith("BUDGETS=")))
                self.assertEqual(values, expected)

    def clock(self):
        now = [0.0]
        def sleep(seconds):
            now[0] += seconds
        return now, sleep

    def test_slow_slot_layout_gets_two_complete_observations(self):
        action = formation.AutoFormationFromChaldea()
        action.context = SimpleNamespace(tasker=SimpleNamespace(stopping=False))
        now, sleep = self.clock()
        slots = [{"kind": "LOCAL", "svt_id": "1"}]
        def detect():
            now[0] += 12
            return slots
        action._detect_slots = Mock(side_effect=detect)
        with patch.object(formation, "SLOT_LAYOUT_VERIFY_TIMEOUT_SECONDS", 30), patch.object(formation.time, "monotonic", lambda: now[0]), patch.object(formation.time, "sleep", sleep):
            self.assertEqual(action._detect_slots_stable(), slots)
        self.assertEqual(action._detect_slots.call_count, 2)

    def test_slow_cost_ocr_can_confirm_consistency(self):
        action = bond.CompleteBondFormation()
        now, sleep = self.clock()
        def ocr():
            now[0] += 6
            return (100, 114)
        action._ocr_cost_once = Mock(side_effect=ocr)
        with patch.object(bond, "COST_VERIFY_TIMEOUT_SECONDS", 20), patch.object(bond.time, "monotonic", lambda: now[0]), patch.object(bond.time, "sleep", sleep):
            self.assertEqual(action._read_cost_consistent(), (100, 114))
        self.assertEqual(action._ocr_cost_once.call_count, 2)

    def test_bond_ocr_allows_nested_stability_checks(self):
        action = bond.CompleteBondFormation()
        action.context = SimpleNamespace(tasker=SimpleNamespace(stopping=False))
        now, sleep = self.clock()
        def ocr():
            now[0] += 6
            return ("available", "1234")
        action._read_servant_bond_state_once = Mock(side_effect=ocr)
        # 内层两帧一致后，外层还须再确认一次，总计至少三轮。
        with patch.object(bond, "SERVANT_BOND_OCR_TIMEOUT_SECONDS", 30), patch.object(bond.time, "monotonic", lambda: now[0]), patch.object(bond.time, "sleep", sleep):
            self.assertEqual(action._read_servant_bond_state(), ("available", "1234"))
        self.assertEqual(action._read_servant_bond_state_once.call_count, 3)

    def test_wait_still_returns_immediately_when_stopped(self):
        action = formation.AutoFormationFromChaldea()
        action.context = SimpleNamespace(tasker=SimpleNamespace(stopping=True))
        predicate = Mock()
        self.assertFalse(action._wait_for(predicate, 30))
        predicate.assert_not_called()

    def test_android_pipeline_adaptation_is_scoped_and_preserves_larger_limits(self):
        spec = importlib.util.spec_from_file_location("mobile_prepare", ROOT / "tools/prepare_android.py")
        android = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(android)
        for relative, limits in android.ANDROID_TRANSITION_TIMEOUTS.items():
            path = ROOT / "assets/resource" / relative
            original = path.read_bytes()
            data = json.loads(original)
            for name in limits:
                self.assertIn(name, data, relative)
            android.adapt_transition_timeouts(data, relative)
            for name, limit in limits.items():
                self.assertGreaterEqual(data[name]["timeout"], limit)
            self.assertEqual(path.read_bytes(), original)
        data = {"战斗_特殊技能处理": {"timeout": 60000}, "failure": {"timeout": 1}, "optional": {"timeout": 2000}}
        android.adapt_transition_timeouts(data, "base/pipeline/自动战斗_特殊技能.json")
        self.assertEqual([n["timeout"] for n in data.values()], [60000, 1, 2000])


if __name__ == "__main__":
    unittest.main()
