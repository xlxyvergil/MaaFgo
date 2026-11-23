"""Only story/trial may abandon a broken preset; source plans remain isolated."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

from test_main_story_restricted_formation import runner


ROOT = Path(__file__).resolve().parents[1]
FLAG = "native_battle_preset_fallback"
ENTRY = "原生自动战斗入口"


class StoryPresetFallbackScopeTests(unittest.TestCase):
    def prepare(self, *, restricted=False, config=None, frames=()):
        runtime, context = runner(frames, config)
        runtime.current_restricted = restricted
        runtime.reach_formation = Mock()
        runtime._start_formation_battle = Mock()
        # A normal preset must survive enabling runtime recovery unchanged.
        context.nodes[ENTRY]["action"]["param"]["custom_action_param"] = {
            "chaldea_import_source": "unchanged-plan.json"}
        return runtime, context

    def battle_nodes(self, context):
        return [nodes[ENTRY] for name, nodes in context.executions if name == ENTRY]

    def test_normal_story_enables_recovery_without_resetting_preset_or_parent(self):
        runtime, context = self.prepare()
        parent = copy.deepcopy(context.nodes)
        runtime.fight()
        [battle] = self.battle_nodes(context)
        self.assertIs(battle["attach"][FLAG], True)
        self.assertEqual(battle["action"], parent[ENTRY]["action"])
        self.assertEqual(context.nodes, parent)
        self.assertIs(runtime.ctx, context)

    def test_restricted_story_keeps_default_reset_and_parent_source(self):
        runtime, context = self.prepare(restricted=True)
        parent = copy.deepcopy(context.nodes)
        runtime.fight()
        [battle] = self.battle_nodes(context)
        self.assertIs(battle["attach"][FLAG], True)
        self.assertIs(battle["attach"]["native_battle_formation_restricted"], True)
        param = json.loads(battle["action"]["param"]["custom_action_param"])
        self.assertEqual(param, {"native_battle_formation_reset": True})
        self.assertEqual(context.nodes, parent)

    def test_continued_battle_keeps_scope_and_replaces_preset_only_after_defeat(self):
        runtime, context = self.prepare(
            config={"use_spirit_stone": True}, frames=[{"战斗失败_不回主界面"}, set()])
        runtime.continue_with_spirit_stone = Mock()
        parent = copy.deepcopy(context.nodes)
        runtime.fight()
        first, resumed = self.battle_nodes(context)
        self.assertIs(first["attach"][FLAG], True)
        self.assertIs(resumed["attach"][FLAG], True)
        self.assertNotIn("native_battle_formation_restricted", resumed["attach"])
        self.assertEqual(first["action"], parent[ENTRY]["action"])
        param = json.loads(resumed["action"]["param"]["custom_action_param"])
        self.assertEqual(param, {"native_battle_formation_reset": True})
        self.assertEqual(context.nodes, parent)
        runtime.continue_with_spirit_stone.assert_called_once()

    def test_bbc_does_not_enable_native_fallback(self):
        runtime, context = self.prepare(config={"battle_mode": "bbc"})
        parent = copy.deepcopy(context.nodes)
        runtime.fight()
        self.assertEqual(self.battle_nodes(context), [])
        self.assertEqual(context.tasks[0][0], "bbc战斗")
        self.assertEqual(context.nodes, parent)


class TrialPresetFallbackResourceScopeTests(unittest.TestCase):
    def test_only_trial_resource_enables_preset_recovery(self):
        found = []

        def visit(value, location):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key == FLAG:
                        found.append((location + (key,), child))
                    visit(child, location + (key,))
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    visit(child, location + (index,))

        for path in (ROOT / "assets").rglob("*.json"):
            if "web" not in path.relative_to(ROOT / "assets").parts:
                visit(json.loads(path.read_text("utf-8-sig")), (path.relative_to(ROOT).as_posix(),))
        self.assertEqual(found, [(("assets/tasks/地狱的试炼.json", "task", 0,
                                 "pipeline_override", ENTRY, "attach", FLAG), True)])

    def test_shared_native_entry_remains_opt_in(self):
        resource = json.loads((ROOT / "assets/resource/base/pipeline/原生自动战斗.json").read_text("utf-8"))
        self.assertNotIn(FLAG, resource[ENTRY].get("attach", {}))


if __name__ == "__main__":
    unittest.main()
