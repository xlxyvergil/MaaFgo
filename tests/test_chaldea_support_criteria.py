import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent" / "custom"), str(ROOT / "agent")]

from chaldea.support_criteria import build_support_criteria  # noqa: E402
from support_action import SupportAction  # noqa: E402


class ChaldeaSupportCriteriaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        servant_list = json.loads((ROOT / "agent/custom/servant_list.json").read_text(encoding="utf-8"))
        cls.servant_map = {servant["id"]: servant for servant in servant_list["servants"]}

    def _sample(self, name):
        return json.loads((ROOT / "tests/fixtures/chaldea" / name).read_text(encoding="utf-8"))

    def test_normal_support_from_chaldea(self):
        result = build_support_criteria(self._sample("雨暗阿_21086_94061640.json"), self.servant_map)
        self.assertEqual(result["support_type"], "normal")
        self.assertEqual(result["servant_id"], "505300")
        self.assertEqual(result["class_name"], "魔术师")
        self.assertEqual(result["ce_specs"], [])
        self.assertEqual(result["active"], [10, 10, 10])
        self.assertEqual(result["passive"], [0, 10, 0, 0, 0])
        self.assertEqual(result["np_level"], 1)
        self.assertEqual(result["level"], 90)

    def test_grand_support_keeps_each_ce_state(self):
        result = build_support_criteria(self._sample("太女尼_78119_94149837.json"), self.servant_map)
        self.assertEqual(result["support_type"], "grand")
        self.assertEqual([(ce["id"], ce["status"], ce["slot"], ce["source_slot"])
                          for ce in result["ce_specs"]],
                         [(9408220, "满破", 1, 1), (9408800, "满破", 2, 3)])
        self.assertEqual(result["ce_bond"], "original")

    def test_grand_support_uses_bond_slot_state_without_matching_its_card(self):
        data = self._sample("太女尼_78119_94149837.json")
        support = data["team"]["onFieldSvts"][0]
        support["classBoardData"]["grandBondEquipSkillChange"] = True
        result = build_support_criteria(data, self.servant_map)
        self.assertEqual([ce["source_slot"] for ce in result["ce_specs"]], [1, 3])
        self.assertEqual(result["ce_bond"], "50np")

        support.pop("equip2")
        result = build_support_criteria(data, self.servant_map)
        self.assertEqual(result["ce_bond"], "50np")
        support["classBoardData"]["grandBondEquipSkillChange"] = False
        self.assertEqual(build_support_criteria(data, self.servant_map)["ce_bond"], "original")
        support.pop("classBoardData")
        result = build_support_criteria(data, self.servant_map)
        self.assertEqual(result["ce_bond"], "any")

    def test_absent_limit_break_keeps_any_state_while_explicit_false_requires_non_mlb(self):
        for source_key, break_key in (("equip1", "limitBreak"), ("ceId", "ceLimitBreak")):
            for value, expected in ((None, "任意"), (True, "满破"), (False, "非满破")):
                with self.subTest(source_key=source_key, value=value):
                    data = self._sample("雨暗阿_21086_94061640.json")
                    support = data["team"]["onFieldSvts"][0]
                    support.pop("equip1", None)
                    support.pop("ceId", None)
                    if source_key == "equip1":
                        support["equip1"] = {"id": 9400340}
                        if value is not None:
                            support["equip1"][break_key] = value
                    else:
                        support["ceId"] = 9400340
                        if value is not None:
                            support[break_key] = value
                    result = build_support_criteria(data, self.servant_map, resolve_name=lambda _: "万华镜")
                    self.assertEqual(result["ce_specs"][0]["status"], expected)

    def test_equip_name_normalizes_fullwidth_characters_for_template_path(self):
        data = self._sample("太女尼_78119_94149837.json")
        result = build_support_criteria(
            data,
            self.servant_map,
            resolve_name=lambda equip_id: "来自ＮＦＦ的爱" if equip_id == 9408220 else "名侦探芙尔摩斯",
        )
        self.assertEqual([ce["name"] for ce in result["ce_specs"]],
                         ["来自NFF的爱.png", "名侦探芙尔摩斯.png"])

        result = build_support_criteria(
            data,
            self.servant_map,
            resolve_name=lambda equip_id: "限制/零毁" if equip_id == 9408220 else "名侦探芙尔摩斯",
        )
        self.assertEqual(result["ce_specs"][0]["name"], "限制／零毁.png")

    def test_missing_or_multiple_support_fails(self):
        data = self._sample("雨暗阿_21086_94061640.json")
        data["team"]["onFieldSvts"][0]["supportType"] = "none"
        with self.assertRaisesRegex(ValueError, "没有标记助战.*重新导出并导入.*自定义助战"):
            build_support_criteria(data, self.servant_map)
        data["team"]["onFieldSvts"][0]["supportType"] = "friend"
        data["team"]["onFieldSvts"][1]["supportType"] = "friend"
        with self.assertRaisesRegex(ValueError, "标记了 2 位助战.*只保留一位"):
            build_support_criteria(data, self.servant_map)

    def test_missing_optional_fields_do_not_add_conditions(self):
        data = self._sample("雨暗阿_21086_94061640.json")
        support = data["team"]["onFieldSvts"][0]
        for key in ("lv", "tdLv", "skillLvs", "appendLvs"):
            support.pop(key)
        result = build_support_criteria(data, self.servant_map)
        self.assertEqual((result["level"], result["np_level"]), (0, 0))
        self.assertEqual(result["active"], [0, 0, 0])
        self.assertEqual(result["passive"], [0] * 5)

    def test_chaldea_guard_rejects_disabled_or_blank_source(self):
        action = SupportAction()
        argv = SimpleNamespace(node_name="Chaldea助战action",
                               custom_action_param='{"source_mode":"chaldea"}')
        context = SimpleNamespace(get_node_data=lambda name: {"attach": {"chaldea_enabled": False}})
        with patch("support_action.mfaalog.error") as log_error:
            self.assertFalse(action.run(context, argv).success)
            message = log_error.call_args[0][0]
            self.assertIn("使用编辑器/chaldea配置", message)
            self.assertIn("自定义助战", message)
        context = SimpleNamespace(get_node_data=lambda name: {"attach": {"chaldea_enabled": True,
                                                                            "chaldea_import_source": ""}})
        with patch("support_action.mfaalog.error") as log_error:
            self.assertFalse(action.run(context, argv).success)
            self.assertIn("分享链接、队伍 ID、关卡 ID", log_error.call_args[0][0])

    def test_invalid_source_or_missing_ce_template_fails_before_search(self):
        action = SupportAction()
        argv = SimpleNamespace(node_name="Chaldea助战action",
                               custom_action_param='{"source_mode":"chaldea"}')
        context = SimpleNamespace(get_node_data=lambda name: {
            "attach": ({"chaldea_enabled": True, "chaldea_import_source": "test"}
                       if name == "Chaldea助战action" else {"resource_package": "base"})})
        with patch("support_action.fetch_share_data", return_value=(None, None, None)):
            self.assertFalse(action.run(context, argv).success)
        grand = self._sample("太女尼_78119_94149837.json")
        with patch("support_action.fetch_share_data", return_value=(grand, None, None)), \
             patch("support_action.os.path.isfile", return_value=False), \
             patch("support_action.mfaalog.error") as log_error, \
             patch.object(action, "_get_detector", side_effect=AssertionError("must not search")):
            self.assertFalse(action.run(context, argv).success)
            log_error.assert_called_with(
                "[SupportAction] 找不到礼装「秘密任务」的满破助战图片，无法按此条件选择助战。"
                "请打开编队编辑器，在“选择概念礼装”中点击“安装礼装扩展”，"
                "选择包含该礼装的扩展包 ZIP，安装后重试；也可重新选择已有识别图片的礼装。"
            )

    def test_option_route_and_failure_propagation(self):
        options = json.loads((ROOT / "assets/options/原生自动战斗助战方式.json").read_text(encoding="utf-8"))["option"]
        cases = {case["name"]: case for case in options["原生自动战斗助战方式"]["cases"]}
        self.assertEqual(cases["使用Chaldea配置"]["pipeline_override"]["进本流程"]["next"][1],
                         "Chaldea助战action")
        pipeline = json.loads((ROOT / "assets/resource/base/pipeline/进本流程.json").read_text(encoding="utf-8"))
        self.assertEqual(pipeline["Chaldea助战action"]["on_error"], [])


if __name__ == "__main__":
    unittest.main()
