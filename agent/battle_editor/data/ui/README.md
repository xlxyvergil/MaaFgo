# 本地 FGO 界面素材

由 `tools/build_battle_editor_ui_assets.py` 从用户已有的 FGO 解包目录离线生成。编辑器运行时只读取本目录，不依赖整包路径、Atlas 网络或开发机缓存。

```powershell
python tools/build_battle_editor_ui_assets.py --source-root "F:\Game_AUTO\MaaFGO\FGO整包" --contact-sheet ".workbuddy\editor-evidence\fgo-ui-assets-contact.png"
```

脚本读取工程内已有 `catalog.json`，以及 `config/BattleEditor/catalog-build` 中缓存的 JP/CN `nice_servant.json`。这些缓存只在维护时需要，不随工具运行加载。没有缓存时仍可按完全一致的从者数字 ID 提取旧窄条竖图；完整编队卡面与追加技能的准确对应需要缓存。

- 从者窄图来源为 `NarrowFigure` 三帧图集。优先依据缓存 `extraAssets.narrowFigure.ascension` 的明确文件和帧号选择第 3 灵基；即使完整卡面可用，仍先读取此映射来确定选中灵基。只有完整卡面缺失时才裁出并登记原生 150×376 窄图作为 `portrait` 回退，不为每名从者重复保存窄图。缺图时保留缺失记录，不猜名字或借其他人物图片。
- 编队主图为完整 `formationPortrait`。依据缓存 `extraAssets.charaGraph.ascension` 的准确 Atlas URL，只接受同一从者 ID 的 `CharaGraph/<ID>/<ID>a@1/@2` 或 `b@1/@2`；两帧分别裁原图左、右原生 512×724 区域，排除 y=724 以下的姓名文字和空白。优先沿用已选窄条映射的灵基，再按 3、1、2、4 尝试该从者明确登记的形态；不按姓名、邻近 ID 或文件名前缀猜图，缺少映射或源文件写入 `missing.formationPortraits`。完整卡面仅供展示，裁切后**先直接转RGB、保留透明像素下面的真实颜色**，再用LANCZOS将宽高各乘0.7并四舍五入为358×507，最后编码为有损WebP Q90、method 6。不使用现有256色PNG转码，不铺白底，也不能先按RGBA缩图：部分整包CharaGraph近黑像素被错误标成透明，RGBA缩图会丢失这些位置的RGB，使黑发、线条出现透底白洞。必要窄图回退继续保持150×376及原PNG/Alpha流程，其他素材沿用原处理，不重新缩放或量化。前端继续采用512:724图片框、原来的卡片尺寸和横向填满规则，由浏览器适应较小原图，不缩小页面卡片。contact sheet 优先完整卡面，再用必要窄图回退。
- 完整卡面的文件名为 `formation_<ID>_<来源路径/来源哈希/裁切参数/缩放配方/WebP配方指纹>.webp`，用于隔离浏览器缓存；`formationPortraitAscension` 记录选中灵基。每个文件的 `source` 还保留 `atlasURL`、灵基编号、源 SHA-256 与准确crop坐标，以及 `resize: {width: 358, height: 507, scale: 0.7, resample: "LANCZOS", mode: "RGB"}`。顶层`mode: "RGB"`和`encoding`记录`format: "WEBP"`、`lossless: false`、`quality: 90`、`method: 6`、实际`libwebp_version`、`input_mode: "RGB"`、`alpha: "discardBeforeResizePreserveRGB"`。`width/height`的源字段仍描述原图集，文件顶层尺寸描述缩图结果。源图、缩放、透明处理或编码器配方变化会新增指纹文件；缓存复用也核对完整配方，生成器不自动删除旧文件。WebP输出仅允许上述完整卡面名称和同ID原始CharaGraph左右帧，不允许生成的PNG/WebP媒体作为输入。正式发布前可按旧manifest、当前引用及文件哈希明确归档退出登记的旧指纹图片，备份放在打包目录外，避免旧原图继续增加包体积。只生成单张卡面，不把原始双卡/四卡图集整张放入工具。
- 礼装直接复用本安装`EquipFaces/team`已筛选的编队识别模板（152×86 P模式），按实际尺寸完整显示；生成器不再从整包补充任何独立概念礼装PNG，也不回退到旧UI补图。完整名称资料可以保留，但没有识别清单及team模板的项目不可新选入。共用识别礼装不重复量化，list模板仍专用于列表识别。
- 职阶使用整包原生颜色版本，不经 CSS 染色：筛选器统一金色 `class3_<classId>.png`；编队卡片按工程运行时星级选择 1～2 星铜色 `class1`、3 星银色 `class2`、4～5 星金色 `class3`，0 星优先独立 `class0`。优先使用整包已处理的透明图标，特殊职阶保留原图的真实配色。`classes[classKey]` 是筛选器金色图，`classVariants[classKey]["0".."5"]` 是按星级使用的文件名；这样可以正确处理工程默认 3 星、JP 数据为 4 星的玛修。某颜色无精确资源时只回退到同职阶金色，并记录在 `missing.classVariants`，不借其他职阶图。
- 技能使用实际游戏显示图标；主动技能保留精确技能 ID，对追加技能保留从者及第 1～5 项的精确 ID 对应，中文名优先匹配同一个技能 ID。
- 助战旗帜和星级图有些导出丢失透明通道，仅将与图片边缘相连且 RGB 各通道不超过 8 的黑色变为透明，保留图标内部细节。该操作在每条来源中记录为 `alphaRepair`。
- 回合页宝具框使用 Battle 原生 `card_bg_nparts/npbuster/npquick`（140×177），御主按钮/面板使用对应 `btn_master_*`、`frame_master_bg` 与 `img_frame_master`；仅新增这些小图。`common.npFrames` 和 `common.masterSkill/masterSkillDisabled/masterMenu/masterFrame/masterEquipFrame` 记录文件名，API 转换成 `uiAssets` 中的安全图片 URL。
- 回合页从者战斗头像不额外复制进此目录。API `servants[].statusImage` 通过安全的扁平 URL `/media/status/status_<svtId>_<variant>.png` 复用工程或打包版的 `ServantStatus/<svtId>/status_servant_<variant>.png`；只用同一精确从者数字 ID，默认依次尝试 3、1、2，不借其他人物或别名形态。路径和文件名均由十进制 ID 重建，拒绝任意目录、编码路径、文件流及逃逸符号链接。缺图时返回 null，页面可使用既有头像。

`manifest.json` 是媒体白名单，列出每个输出PNG/WebP的尺寸、SHA-256、源相对路径、源 SHA-256，以及图集裁切坐标/缩放配方/透明修复参数；量化PNG另记`mode: P`、`quantization`配方（含实际imagequant版本）及`quantizationMetrics`护栏统计，完整卡面WebP则记上述`mode: RGB`和`encoding`。`sources`记录输入数据缓存的哈希，`missing`明确缺失资源。输出没有混入1024/2048像素多卡图集。

必要窄图回退等保留的量化PNG复用工程`tools/command_card_palette.py`：libimagequant至多256色、无抖动，PNG `optimize=True, compress_level=9`；量化前后全透明位置和Alpha128阈值位置保持一致，颜色及半透明数值仍有损。完整卡面WebP不调用该量化工具，其不透明RGB转换只作用于原始CharaGraph卡面，不能套用到技能、职阶、透明徽章或识别资源。离线维护环境需安装`tools/requirements-command-card-assets.txt`，Pillow还必须带libwebp编码支持，缺依赖即使存在可用缓存也明确失败，不降级格式或算法；编辑器运行时不需要imagequant或Python编码库。技能、职阶、公共UI保持原字节，不改NP图片绑定或FGO原始整包。

生成时先写同目录临时文件再原子替换PNG/WebP和manifest，避免浏览器读到写入一半的图片。整批更新完成后重启本地服务，刷新已缓存的目录信息。

相同来源哈希、处理参数、量化/编码配方及输出哈希均一致的图片会直接复用，不重新写入；manifest字节相同也不改mtime。输出缺失或损坏时从原始来源重新生成，禁止把成品作为再次量化或WebP转码输入。正式批量更新先暂存、核验和备份，再停服务切换；重启后刷新页面，同名旧图可用Ctrl+F5清除浏览器60秒缓存。

2026-10-24精简后登记1,854张PNG、90,245,986字节（约86.065MiB）。已被完整卡面替代的462张`servant_*.png`已按哈希归档到打包目录外的`.workbuddy/editor-evidence/bond-editor-20261024/retired-narrow/`，减少15,681,943字节（约14.956MiB）；仅保留缺完整卡面的`servant_1002100.png`。生成器不会自动删除历史文件，后续也须按准确旧登记、当前引用和哈希归档，不能一概忽略或删除全部`servant_*.png`。

随后按用户要求退出全部1,222张独立礼装补图，归档到`.workbuddy/editor-evidence/equip-team-only-20261024/retired-equip/`，再减少10,421,946字节（约9.939MiB）。当时独立UI目录登记632张PNG、79,824,040字节（约76.126MiB），没有概念礼装补图；上段1,854张为此次调整前的历史数量。工程team/list另增量补齐12种常用礼装，按既有配方152×86/147×56 P模式制作，其余模板原字节保持。

完整卡面先按宽高70%缩至358×507，随后正式切换不透明RGB WebP Q90/method6。当前目录为462张WebP与170张保留PNG，共632张、30,302,972字节。WebP卡面为26,995,872字节，较缩小后的P图45,919,791字节再减少41.211%；其他170张PNG字节/mtime不变，旧卡面PNG已按准确manifest和哈希归档到`.workbuddy/editor-evidence/webp-migration-20261024/retired-palette-png/`。原源RGBA裁区先丢错误Alpha再缩放，修复深色区域漏浅底；不修改源整包、必要窄图的透明通道或识别模板。完整配方、哈希及验证记录见该证据目录。

2026-10-30增量补齐 Kingprotea·Alter（1102300／图鉴479）的完整编队立绘。本地整包没有该ID的CharaGraph纹理，虽有从者和技能资料，原清单仍登记缺图，页面因此回退到小头像。此次按Atlas JP该ID明确声明的第三灵基 `1102300b@1.png` 补齐，保留358×507、RGB、WebP Q90/method6配方；原图与元数据保存在 `.workbuddy/runtime/kingprotea-alter-portrait-20261030/`，官方SHA1及输入输出SHA256均经校验，manifest记录 `source.kind=atlas` 与精确来源。当前登记464张WebP、170张PNG，总计634张、30,419,160字节。其余633个文件字节保持不变；只清除此ID的完整卡图缺失记录，未伪造窄图来源。

游戏素材版权归 TYPE-MOON / FGO PROJECT 等原权利方所有，本目录不改变其授权。
