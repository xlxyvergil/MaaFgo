"""Compressed share data must not expand into an unbounded issue response."""
import copy
import unittest

from agent.battle_editor.validation import MAX_VALIDATION_ISSUES, validate_battle


def battle():
    return {"team": {"onFieldSvts": [{"svtId": 100100, "supportType": "friend"}, None, None],
                     "backupSvts": [None, None, None]}, "actions": []}


class ValidationBudgetTests(unittest.TestCase):
    def test_large_invalid_skill_array_returns_bounded_blocking_report_without_mutating_data(self):
        data = battle()
        data["team"]["onFieldSvts"][0]["skillLvs"] = [-1] * 25000
        before = copy.deepcopy(data)
        report = validate_battle(data)
        self.assertFalse(report["valid"])
        self.assertEqual(len(report["issues"]), MAX_VALIDATION_ISSUES + 1)
        self.assertEqual((report["issues"][-1]["code"], report["issues"][-1]["severity"]), ("issues_truncated", "error"))
        self.assertGreater(report["summary"]["omittedIssues"], 20000)
        self.assertEqual(data, before)

    def test_many_warnings_keep_the_original_nonblocking_save_semantics(self):
        data = battle()
        data.update({f"future{index}": None for index in range(500)})
        report = validate_battle(data)
        self.assertTrue(report["valid"], report)
        self.assertEqual(len(report["issues"]), MAX_VALIDATION_ISSUES + 1)
        self.assertEqual(report["issues"][-1]["severity"], "warning")
        self.assertIn("不阻止保存", report["issues"][-1]["message"])
        self.assertEqual(len(data), 502)

    def test_error_found_after_warning_limit_still_blocks_saving(self):
        data = battle()
        data.update({f"future{index}": None for index in range(500)})
        data["team"]["onFieldSvts"] = [None, None, None]
        report = validate_battle(data)
        self.assertFalse(report["valid"])
        self.assertTrue(all(issue["severity"] == "warning" for issue in report["issues"][:-1]))
        self.assertEqual(report["issues"][-1]["severity"], "error")
        self.assertIn("阻断问题", report["issues"][-1]["message"])


if __name__ == "__main__":
    unittest.main()
