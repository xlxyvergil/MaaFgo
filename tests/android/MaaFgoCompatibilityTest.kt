package com.aliothmoon.maafw.project

import com.aliothmoon.maafw.BuildConfig
import com.aliothmoon.maafw.domain.*
import com.aliothmoon.maafw.runner.RunPlanBuilder
import com.aliothmoon.maafw.runner.RunPlanResult
import com.aliothmoon.maafw.runner.RuntimeTask
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.*
import org.junit.Test
import java.io.File

/** 使用真正的安卓 PI 解析器和执行计划编译器检查生成资源。 */
class MaaFgoCompatibilityTest {
    @Test fun apkConfiguresMirrorChyanIndependentlyOfDesktopPi() {
        assertEquals("MaaFgo_exec", BuildConfig.MAFW_MIRRORCHYAN_RID)
    }

    private fun definition(): ProjectDefinition {
        val root = File(requireNotNull(System.getenv("MAAFGO_COMPAT_ASSETS")))
        val loaded = ProjectLoader(DirectoryProjectSource(root)).load()
        assertTrue("项目加载失败: $loaded", loaded is ProjectLoadResult.Ready)
        val ready = loaded as ProjectLoadResult.Ready
        assertTrue("解析错误: ${ready.diagnostics}", ready.diagnostics.none { it.severity == DiagnosticSeverity.Error })
        return ready.definition
    }

    private fun compile(definition: ProjectDefinition, task: String, values: Map<String, OptionValue>): RunPlanResult.Success {
        val id = RunConfigurationId("compatibility")
        val config = UserConfiguration(
            initialized = true, activeResourceName = "日服", activeConfigurationId = id,
            configurations = listOf(RunConfiguration(id, "兼容检查", listOf(ConfiguredTask(task, enabled = true, optionValues = values)))),
        )
        val result = RunPlanBuilder.build(definition, config)
        assertTrue("任务编译失败: $result", result is RunPlanResult.Success)
        return result as RunPlanResult.Success
    }

    private fun attachPatches(task: RuntimeTask, node: String): List<JsonObject> =
        task.pipelineOverrides.mapNotNull { it[node]?.jsonObject?.get("attach")?.jsonObject }

    private fun flags(vararg values: Pair<String, Boolean>): JsonObject =
        JsonObject(values.associate { (key, value) -> key to JsonPrimitive(value) })

    private fun assertBooleanWrite(task: RuntimeTask, node: String, key: String, expected: Boolean) {
        // 检查所有有序写入，不能只查“曾写入 true”而漏掉后续默认 false 覆盖。
        // 同时要求 JSON boolean，避免字符串 "false" 被 Python 当作真值。
        val writes = attachPatches(task, node).mapNotNull { it[key] }
        assertEquals("$node.$key 的有序写入不正确", listOf(JsonPrimitive(expected)), writes)
    }

    @Test fun sixFileListsKeepIconsDefaultsAndAgentFilenames() {
        val definition = definition()
        for (prefix in listOf("普通助战-礼装", "冠位助战-1号礼装", "冠位助战-2号礼装")) {
            for (status in listOf("满破", "非满破")) {
                val name = "$prefix-$status"
                val option = definition.options[name] as OptionDefinition.Select
                assertEquals("0_空.png", option.defaultCase)
                assertEquals(47, option.cases.size)
                assertNotNull(option.cases.first().icon)
                val filename = option.cases.first { it.name != "0_空.png" }.name
                val values = mapOf(
                    "battle_mode" to OptionValue.SingleCase("native"),
                    "原生自动战斗助战方式" to OptionValue.SingleCase("自定义助战"),
                    "助战类型" to OptionValue.SingleCase(if (prefix.startsWith("普通")) "normal" else "grand"),
                    "普通助战-礼装" to OptionValue.SingleCase(status),
                    "冠位助战-1号礼装" to OptionValue.SingleCase(status),
                    "冠位助战-2号礼装" to OptionValue.SingleCase(status),
                    name to OptionValue.SingleCase(filename),
                )
                val plan = compile(definition, "直接bbc战斗", values).plan
                val attached = plan.tasks.single().pipelineOverrides.mapNotNull { patch ->
                    patch["助战action"]?.jsonObject?.get("attach")?.jsonObject?.get(name)
                }
                assertEquals("$name 未传给 Agent", filename, attached.last().jsonPrimitive.content)
            }
        }
    }

    @Test fun chaldeaTextInputIsSubstitutedAndGuideIsVisible() {
        val definition = definition()
        val source = "/sdcard/Download/队伍\"测试.json"
        val values = mapOf("Chaldea导入手动输入" to OptionValue.Inputs(mapOf("chaldea_import_source" to source)))
        val plan = compile(definition, "Chaldea 自动编队", values).plan
        val attached = plan.tasks.single().pipelineOverrides.mapNotNull { patch ->
            patch["执行自动编队"]?.jsonObject?.get("attach")?.jsonObject?.get("chaldea_import_source")
        }
        assertEquals(source, attached.last().jsonPrimitive.content)
        assertTrue(definition.task("Chaldea 自动编队")!!.description!!.contains("## 适用入口"))
    }

    @Test fun inventoryDefaultsCompileBothKindsAndAllFourFiveStarEquips() {
        val definition = definition()
        assertEquals("从者和礼装", (definition.options["个人库存构建范围"] as OptionDefinition.Select).defaultCase)
        assertEquals("全部四五星礼装", (definition.options["个人库存礼装范围"] as OptionDefinition.Select).defaultCase)

        val task = compile(definition, "构建个人从者礼装库", emptyMap()).plan.tasks.single()
        assertEquals("构建个人从者礼装库", task.entry)
        assertEquals(
            listOf(flags("scan_servants" to true, "scan_equips" to true), flags("bond_equips_only" to false)),
            attachPatches(task, "个人库存-执行构建"),
        )
    }

    @Test fun inventoryRangesKeepExplicitChoicesAndIgnoreHiddenEquipFilter() {
        val definition = definition()
        val ranges = listOf(
            Triple("从者和礼装", true, true),
            Triple("仅从者", true, false),
            Triple("仅礼装", false, true),
        )
        for ((range, servants, equips) in ranges) {
            for ((filter, bondOnly) in listOf("全部四五星礼装" to false, "仅羁绊礼装" to true)) {
                val values = mapOf(
                    "个人库存构建范围" to OptionValue.SingleCase(range),
                    // 仅从者时也保留这项旧配置，验证隐藏子选项不会覆盖当前范围。
                    "个人库存礼装范围" to OptionValue.SingleCase(filter),
                )
                val task = compile(definition, "构建个人从者礼装库", values).plan.tasks.single()
                val expected = listOfNotNull(
                    flags("scan_servants" to servants, "scan_equips" to equips),
                    if (equips) flags("bond_equips_only" to bondOnly) else null,
                )
                assertEquals("$range / $filter 的有序参数不正确", expected, attachPatches(task, "个人库存-执行构建"))
            }
        }
    }

    @Test fun chaldeaLocalInventoriesRequireEnabledParentAndKeepIndependentSwitches() {
        val definition = definition()
        for (name in listOf("自动补齐从者和羁绊礼装", "使用本地从者库", "使用本地礼装库")) {
            assertEquals("$name 默认值不正确", "No", (definition.options[name] as OptionDefinition.Choice).defaultCase)
        }
        val node = "执行羁绊补齐自动编队"
        val storedChildren = mapOf(
            "使用本地从者库" to OptionValue.SingleCase("Yes"),
            "使用本地礼装库" to OptionValue.SingleCase("Yes"),
        )
        for (values in listOf(emptyMap(), storedChildren + ("自动补齐从者和羁绊礼装" to OptionValue.SingleCase("No")))) {
            val task = compile(definition, "Chaldea 自动编队", values).plan.tasks.single()
            assertBooleanWrite(task, node, "bond_completion_enabled", false)
            assertTrue(
                "关闭自动补齐时不得编译保存的本地库子选项",
                attachPatches(task, node).none { "use_local_servant_inventory" in it || "use_local_equip_inventory" in it },
            )
        }

        val enabled = mapOf("自动补齐从者和羁绊礼装" to OptionValue.SingleCase("Yes"))
        val defaults = compile(definition, "Chaldea 自动编队", enabled).plan.tasks.single()
        assertBooleanWrite(defaults, node, "bond_completion_enabled", true)
        assertBooleanWrite(defaults, node, "use_local_servant_inventory", false)
        assertBooleanWrite(defaults, node, "use_local_equip_inventory", false)

        for ((servantCase, servants) in listOf("No" to false, "Yes" to true)) {
            for ((equipCase, equips) in listOf("No" to false, "Yes" to true)) {
                val values = enabled + mapOf(
                    "使用本地从者库" to OptionValue.SingleCase(servantCase),
                    "使用本地礼装库" to OptionValue.SingleCase(equipCase),
                )
                val task = compile(definition, "Chaldea 自动编队", values).plan.tasks.single()
                assertBooleanWrite(task, node, "bond_completion_enabled", true)
                assertBooleanWrite(task, node, "use_local_servant_inventory", servants)
                assertBooleanWrite(task, node, "use_local_equip_inventory", equips)
                assertTrue("Chaldea 选项不得写入独立补齐任务", attachPatches(task, "当前编队羁绊补齐").isEmpty())
            }
        }
    }

    @Test fun currentTeamLocalInventoriesKeepDefaultsIndependentAndTaskScoped() {
        val definition = definition()
        val taskName = "自动补齐从者礼装（按照羁绊最大化）"
        val node = "当前编队羁绊补齐"
        for (name in listOf("当前编队使用本地从者库", "当前编队使用本地礼装库")) {
            assertEquals("$name 默认值不正确", "No", (definition.options[name] as OptionDefinition.Choice).defaultCase)
        }
        val defaults = compile(definition, taskName, emptyMap()).plan.tasks.single()
        assertEquals(node, defaults.entry)
        assertBooleanWrite(defaults, node, "use_local_servant_inventory", false)
        assertBooleanWrite(defaults, node, "use_local_equip_inventory", false)
        assertBooleanWrite(defaults, node, "modify_all_equips", false)

        for ((servantCase, servants) in listOf("No" to false, "Yes" to true)) {
            for ((equipCase, equips) in listOf("No" to false, "Yes" to true)) {
                val values = mapOf(
                    "当前编队使用本地从者库" to OptionValue.SingleCase(servantCase),
                    "当前编队使用本地礼装库" to OptionValue.SingleCase(equipCase),
                    // 两个入口的标签相同，但选项名不同；对方的保存值必须不生效。
                    "使用本地从者库" to OptionValue.SingleCase(if (servants) "No" else "Yes"),
                    "使用本地礼装库" to OptionValue.SingleCase(if (equips) "No" else "Yes"),
                )
                val task = compile(definition, taskName, values).plan.tasks.single()
                assertBooleanWrite(task, node, "use_local_servant_inventory", servants)
                assertBooleanWrite(task, node, "use_local_equip_inventory", equips)
                assertBooleanWrite(task, node, "modify_all_equips", false)
                assertTrue("独立补齐选项不得写入 Chaldea 任务", attachPatches(task, "执行羁绊补齐自动编队").isEmpty())
            }
        }
    }
}
