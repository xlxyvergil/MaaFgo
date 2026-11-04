# 编队编辑器离线补充目录

`catalog.json` 由 `tools/build_battle_editor_catalog.py` 构建，提供本地识别目录中没有的五张面卡牌组、技能、宝具、完整礼装基本信息及御主礼装。日常打开网页或服务时不会联网更新。

维护时在工程根目录运行：

```powershell
python tools/build_battle_editor_catalog.py --refresh
```

导出缓存位于 `config/BattleEditor/catalog-build`，不需要打包；提交和打包本目录的 `catalog.json`、`sources.json`、`media/`。不带 `--refresh` 时只用缓存重新构建，不联网。`sources.json` 记录各导出文件的官方 URL、SHA-256、Atlas 数据版本，以及每张下载图片的来源与哈希。

若元数据已完整缓存，仅需补下载失败的图片，可运行 `python tools/build_battle_editor_catalog.py --download-images`，不会重新下载大型从者导出。

2026-10-25按准确ID增量加入No.483「タマモアリア」(`406000`)及No.2707羁绊礼装的轻量资料，原有从者、礼装及御主礼装事实保持；单条Atlas元数据来源及SHA-256见`sources.json.incrementalUpdates`。独立`np_cards.json`同步追加该从者的3种指令卡头像、Arts宝具`406001`及有效名称第0行绑定，国服元数据与名称图尚不可用。维护时不可直接用此前不含该从者的旧缓存全量覆盖；应先刷新所需Atlas元数据，或将已留存的单条元数据与旧缓存按精确ID合并、核对原记录后暂存重建。原图与增量证据位于工程`.workbuddy/resource-update-20261025/`，不随资源包发布。

维护下载遇到代理断连时，可为本次命令加 `--direct` 使用直连，或用 `--image-workers 1` 降低并发；不修改系统代理设置。图片写入前核验 PNG 完整块与 CRC，保存后记录 SHA-256。

若 Python 的 TLS 链路持续断开，可加 `--curl` 使用系统 curl 下载小图（仍校验证书与 PNG 完整性），例如 `python tools/build_battle_editor_catalog.py --download-images --direct --curl`；仅维护时需要此可选工具，玩家运行编辑器不依赖 curl。

从者头像复用`resource/base/image/servant_face`，概念礼装图片只复用本安装`resource/base/image/EquipFaces/team`，不补下载或从整包复制编辑器专用礼装图。轻量完整名称资料仍用于旧方案读取；礼装选择器只显示`equip_list.json`有记录且team模板实际存在的项目，`recognizable`同时表示这两个条件成立。取消“常用礼装”仅浏览工程可识别范围。技能图标与御主礼装图片仍可独立补充，个人库存来自本安装实例的`config/Inventory`，不包含在随包数据中。

技能 `target` 是从 Atlas 函数目标类别保守计算的 `ally`（需要我方单体目标）、`none`（已知自动或无需我方目标）、`unknown`（选择分支、候补目标等不确定情形），不根据技能名称猜测。`skills` 每个技能槽展示最新强化版本供参考；导入有 `skillIds` / `tdId` 时应从 `skillVariants` / `npVariants` 按 ID 匹配，不能把默认展示当作玩家实际灵基与强化状态。

默认 `np` 优先从包含非空实际函数记录的 Nice 宝具中选择，再按 `priority/id` 排序；所有候选都没有函数时沿旧规则回退。`npVariants` 完整保留，明确指定的 `tdId` 仍优先准确匹配。仅离线修正默认项可运行 `python tools/build_battle_editor_catalog.py --np-defaults-only`，只读取 JP/CN Nice 缓存并更新 `catalog.json` 的默认字段，不改版本、variants、媒体和来源文件；无变化不重写。当前服务进程的内存目录需由维护者安排重启后重新读取，单独刷新网页不会清空服务缓存。

未知牌组返回空数组；缺图返回 `null`；不使用虚构索引、远程热链或代用人物图。Atlas 的 CardType 为 Arts=1、Buster=2、Quick=3，与 Chaldea 正式整数保持一致。

从者高级筛选另从既有 `agent/custom/servant_list.json` 投影 `bond` 和 `npFilter`，不覆盖这里的 Atlas `np/npVariants`。这些羁绊、宝具类型与色卡记录已和用户 `servant_list.csv` 核对，运行时不依赖外部 CSV；更新资料前先比较精确 ID 和原始字段。类型与色卡须由同一条宝具记录满足，灵基/灵衣条件独立保留，不做无依据的全局并集。具体规则、来源哈希及维护回归见 `docs/HTML编队与战斗编辑器维护说明.md`。

数据来源：[Atlas Academy API](https://api.atlasacademy.io/docs)。图片为 Fate/Grand Order 游戏资产，沿用工程现有游戏资源使用方式；Atlas 的公开数据接口不意味着游戏图片版权归本工程所有。


礼装筛选另使用 `equip_filters.json`（基础/满破效果、条件、类型/来源、属性与羁绊持有者）及 `equip_filters_sources.json`（官方来源版本与哈希）。维护运行 `python tools/build_battle_editor_equip_filters.py` 离线重建，`--refresh` 更新官方缓存；仅处理运行清单既有的礼装，不下载图片。原始整包缓存不随包发布，用户运行完全离线。470条旧羁绊分类的两字段修正通过 `agent/custom/equip_categories.json` 精确映射维护，源 CSV/Excel 本次未修改；完整规则见编辑器维护说明。
