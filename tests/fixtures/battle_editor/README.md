# 固定 Chaldea 格式核验样本

`official_contract_golden.json` 是手工依据官方生成序列化器构造的匿名字段样例，来源固定为 [Chaldea e656fa4b3e23228805efdec851358e0ab319aeeb](https://github.com/chaldea-center/chaldea/tree/e656fa4b3e23228805efdec851358e0ab319aeeb)。未复制个人队伍、分享 ID、账号数据或实战记录；`appBuild: 1000` 只是合成测试值。它用于结构和保真回归，不作为可实际通关的推荐队伍。

对照范围与结论：

- [生成序列化器](https://github.com/chaldea-center/chaldea/blob/e656fa4b3e23228805efdec851358e0ab319aeeb/lib/generated/models/userdata/battle.g.dart) 定义的 `team`、3+3 槽位、礼装对象、`none/friend/npc`、御主礼装 `mysticCodeId/level`、`skill/attack`、四项动作 `options` 和换人 `delegate`，与编辑器新增结构一致。御主技能可省略 `svt`，空攻击使用 `attacks: []`。
- [模型及缺省规则](https://github.com/chaldea-center/chaldea/blob/e656fa4b3e23228805efdec851358e0ab319aeeb/lib/models/userdata/battle.dart) 允许省略数值属性和可空对象；编辑器不把官方模拟器补出的满技能、宝具默认值回写为玩家要求。`skillLvs/appendLvs` 的元素是整数，`skillIds` 的元素可为 `null`。
- [CardTypeConverter](https://github.com/chaldea-center/chaldea/blob/e656fa4b3e23228805efdec851358e0ab319aeeb/lib/models/gamedata/common.dart) 输出整数，`Arts=1 / Buster=2 / Quick=3`。样例混排面卡和宝具，普通 `card` 指向从者五张牌组，宝具用 `isTD: true, card: -1`。当前执行器不支持的其他色种仍阻断正式保存。

`official_source_manifest.json` 记录原始来源及 SHA-256。测试检查字段形状，并经真实 HTTP 保存、读取、修改与草稿恢复后深比较原始树；未知字段另作保真回归。

本次核验没有运行官方 Dart 模型，也没有执行真实 Chaldea App 导入。JSON 结构兼容不等于 App 提供直接选取原始 JSON 文件的入口，更不表示实战已经通过。

`dynamic_card_color_golden.json` 单独覆盖技能改色：换入阿尔托莉雅后使用技能 2，再选择 `card: 1/2, cardType: 2`。它们在基础牌组中原为 Arts，但当回合可由技能改成 Buster。编辑器对这种基础牌组差异给出明确警告并保留原记录，避免错误阻止当前引擎支持的方案；非法索引、同一具体面卡重复和宝具重复仍然阻断保存。此样例同样不包含个人数据或实战通过声明。
