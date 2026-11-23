<!-- markdownlint-disable MD033 MD041 -->
<div align="center">

<img alt="LOGO" src="https://raw.githubusercontent.com/xlxyvergil/MaaFgo/main/1.png" width="256" height="256" />

# MaaFgo

基于图像识别的 FGO 自动化助手，帮你刷本、配队、编排打法和完成日常养成。<br>
提供 Windows 桌面版与 Android 测试版，支持国服与日服资源。<br>
由 [MaaFramework](https://github.com/MaaXYZ/MaaFramework) 强力驱动。<br>
<a href="https://github.com/xlxyvergil/MaaFgo" target="_blank" style="font-weight: bold;">🔗 项目仓库</a><br>
🌟 觉得好用就在仓库右上角点个 Star 吧 🌟

</div>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3776AB?logo=python&logoColor=white">
  <img alt="platform" src="https://img.shields.io/badge/platform-Windows%20%7C%20Android-blueviolet">
  <img alt="license" src="https://img.shields.io/github/license/xlxyvergil/MaaFgo">
  <br>
  <img alt="commit" src="https://img.shields.io/github/commit-activity/m/xlxyvergil/MaaFgo">
  <img alt="stars" src="https://img.shields.io/github/stars/xlxyvergil/MaaFgo?style=social">
</p>

---

**快速导航：** [下载](#下载与版本选择) · [功能一览](#功能一览) · [Windows 上手](#windows--mxu-快速开始) · [Android 上手](#android--apk-快速开始) · [常见问题](#常见问题) · [使用与维护文档](#使用与维护文档)

## 下载与版本选择

在 [GitHub Releases](https://github.com/xlxyvergil/MaaFgo/releases) 的 **Assets（附件）** 中按设备下载。

| 设备 | 下载文件 |
| --- | --- |
| Windows 电脑（x86_64） | `MaaFgo-win-x86_64-<版本号>-MXU.zip`，解压后运行 |
| Android 手机（测试版） | `MaaFGO-android-arm64-v8a-<版本号>.apk` |
| MuMu 12 等 x86_64 模拟器（Android 测试版） | `MaaFGO-android-x86_64-<版本号>.apk` |

Android 安装和授权见 [安卓使用说明](./Android/README.md)。已有 CDK 也可在 [Mirror酱](https://mirrorchyan.com/zh/projects) 下载已同步的版本。

---

## 功能一览

### ⚔️ 关卡与战斗

- **日常刷本** · 循环刷取已适配的自由本、白纸化地球和迦勒底之门的种火、修炼场、QP 本，可设置场次和苹果。
- **活动与高难** · 刷取已适配的大小活动副本，支持冠位戴冠战和日服「地狱的试炼」。
- **主线、幕间与强化本** · 自动推进当前章节的主线，或从列表连续挑战已开放的幕间物语和强化任务。
- **自动清 Free 本** · 补完当前章节的自由本，确认 CLEAR 后切换到下一处。
- **自动战斗** · 可用原生策略直接作战，也可按方案放技能、换人、开宝具和出卡，支持羁绊满时停止。已选好关卡时可用「芥学姐连续代打」，Windows 也可选用 BBC。
- **助战筛选** · 按从者、礼装、满破和技能条件查找，支持同槽多个礼装候选，也可读取 Chaldea 配置。

### 🧩 自动编队与羁绊优化

- **Chaldea 自动编队** · 导入方案后还原从者、助战位置与御主礼装，可选自动编成概念礼装。[自动编队说明](./docs/Chaldea自动编队使用说明.md)
- **羁绊补齐** · 用剩余 COST 给导入方案或当前队伍补齐从者和羁绊礼装。扫描个人从者与礼装库后，可加快后续编队。

### 🎨 编队与战斗编辑器（Windows）

- **编队与回合编排** · 双击 `打开编队编辑器.cmd`，在浏览器中配置六人队伍和逐回合打法。
- **方案导入与分享** · 支持导入 Chaldea、BBC 方案或编队截图，也可导出编队与逐回合打法的一图流。[编辑器使用说明](./docs/HTML编队与战斗逻辑工具使用说明.md)
- **礼装筛选与扩展** · 按初始 NP、COST、卡色等效果筛选礼装，可安装扩展包添加助战礼装候选。[助战礼装扩展说明](./extras/support-ce/README.md)

### 🛠 基础流程与日常养成

- **登录与种树** · 自动启动、登录或关闭游戏，也可用体力种植蓝苹果。
- **召唤** · 支持每日 1 石、免费友情 10 连、常驻每日 1 抽，也可在当前卡池按张数抽呼符。
- **从者养成** · 强化主动和追加技能，自动使用经验值卡、进行灵基再临。
- **礼物盒与礼装经验** · 按星级或堆叠数领取经验值卡，支持用低星礼装制作经验素材（搓丸子，待实机校准）。[搓丸子使用说明](./docs/搓丸子使用与维护说明.md)

---

## 支持范围

### 资源包与渠道

各渠道共用对应区服的功能，具体见上方“功能一览”。运行前按游戏区服选择 **B 服（含小米 / 应用宝 / 享游服）或日服**资源包；登录和关闭游戏时，再选择实际安装的渠道包名。享游服的游戏包名及“启动 BBC”的 MuMu 游戏包名均选择“B服”。

资源覆盖不代表所有渠道和关卡均已实机验收；安卓版的验证范围见[安卓测试记录](./Android/TESTING.md)。

### 运行要求

| 项目 | Windows / MXU | Android / APK |
| --- | --- | --- |
| 系统与设备 | Windows，雷电 / MuMu 模拟器或手动 ADB 连接 | 支持目标为 Android 10+；ARM64 手机或 x86_64 模拟器 |
| 控制权限 | 连接模拟器的 ADB | **正在运行且已授权的 Shizuku**，或设备已有 Root 权限；前后台模式均需要其中一种 |
| 游戏画面 | 要求**横屏 16:9**，推荐 **1280 × 720** | 后台使用 **1280 × 720** 虚拟屏；前台要求**横屏 16:9**，推荐 **1280 × 720** |
| 战斗方式 | 原生自动战斗（默认推荐） / BBC | 原生自动战斗 |
| 其他 | Windows 系统区域为**中文（简体，中国）** | 前台操作面板需要悬浮窗权限；未支持仅靠无障碍服务运行 |

超宽屏手机建议先用后台模式。例如 **2160 × 720** 主屏可保持原尺寸，游戏在独立的 1280 × 720 虚拟屏运行；前台暂不支持以该超宽比例直接执行任务。设备的虚拟屏能力与厂商限制仍可能影响后台运行。

---

## Windows / MXU 快速开始

### 1. 下载并解压

前往 [Releases](https://github.com/xlxyvergil/MaaFgo/releases) 下载 `MaaFgo-win-x86_64-<版本号>-MXU.zip`，完整解压后双击 `MaaFgo.exe`。

### 2. 可选：安装 BBchannel

使用 BBC 战斗时需要此步骤；选择原生自动战斗可跳过。

1. 将 `BBchannel` 文件夹放入 MaaFgo 根目录。
2. 将 `bbcdll` 文件夹内的文件复制到 `BBchannel\dist\BBchannel64`，替换同名文件。

视频教程：

- [完整使用教程](https://www.bilibili.com/video/BV1GsjW6wEej/)
- [BBchannel 安装方法](https://www.bilibili.com/video/BV1c3DgBWEjN)

### 3. 配置模拟器

1. 将模拟器画面设为横屏 `16:9`，推荐分辨率为 `1280 × 720`。
2. 启动游戏并保持画面无遮挡。
3. 在 MaaFgo 中选择对应资源包和渠道包名。
4. 使用雷电 / MuMu 自动检测，或手动填写 ADB 端口后连接。

### 4. 选择任务并启动

使用 BBC 时先运行“启动 BBC”；使用原生自动战斗无需启动 BBC。

1. 首次只勾选“登录”，确认连接、资源包和渠道选择正确，再按需添加其他任务。
2. 刷自由本选择“日常战斗”；种火、修炼场或 QP 本选择“迦勒底之门”，配置目标关卡与战斗方式。
3. 使用“原生自动战斗”时，配置次数、跳过剧情和助战方式；首次建议只打 1 场，确认后再增加次数与苹果用量。
4. 需要执行队伍方案时，先开启“使用编辑器/chaldea配置”，再将分享链接、队伍 ID、关卡 ID、本地 JSON 文件或缓存名称填入“Chaldea 队伍导入”。
5. 选择“编队方式”：**默认“手动”核对已有从者、调整位置并校验御主礼装**；选择“自动”才会按方案选人、更换指定御主礼装，并可开启“自动编成礼装”和羁绊补齐。冠位礼装仍需在游戏内预配。

#### 白纸化地球进本

在“日常战斗”中选择“OrdealCall”，再设置“进图方式”。

- **混合方式（默认）** · 先从地图进本。开战前失败时，回主界面从图鉴重试一次。
- **固定入口** · 也可单独选择“从地图进本”或“从图鉴进本”。

#### 助战、编队与原生战斗设置

- **按方案找助战** · 将“助战方式”设为“使用编辑器/chaldea配置”。
- **单独运行「Chaldea 自动编队」** · 先手动选好助战，再从编队页启动。自动编队无法按等级或技能等级区分同名自有从者，可先把不希望选中的个体放入保管室。[Chaldea 自动编队使用说明](./docs/Chaldea自动编队使用说明.md)
- **高级策略** · 选“使用编辑器/chaldea配置”可读取方案中的全局策略，选“手动”可在任务里调整。方案中明确安排的技能与出卡顺序优先执行。[全局高级策略](./docs/HTML编队与战斗逻辑工具使用说明.md#全局高级策略)
  - 默认“补牌优先级”为“优先连携”，“连携策略”为“平衡：三色与勇猛链”。开局等牌关闭，自动补放从者技能开启，回合上限为 40。
- **茶壶** · “是否使用茶壶”默认选“不检测”，沿用游戏内状态。要关掉游戏内茶壶，选“不使用”。

#### 副本任务从哪里启动

| 任务 | 起始页面 | 推进或计数方式 |
| --- | --- | --- |
| 自动推主线 | 目标章节地图、副本列表或编队确认页 | 每个战斗关和纯剧情关各计 1 关，首次建议将推进上限设为 1 |
| 自动幕间副本 | 国服“幕间物语”或日服“幕間の物語”列表、故事舞台，也可选关后从助战或编队确认页开始 | 每完成一个小关计一次，含纯剧情和启动时已选的小关。优先继续同一幕间，地图型幕间完成后返回列表选关 |
| 挑战强化本 | 国服“强化任务”或日服“強化クエスト”列表，也可选关后从助战或编队确认页开始 | 每完成一个小关计一次，含启动时已选的小关。战后返回列表选关，达到上限后在列表停止 |
| 自动清free本 | 当前章节地图、Free Quest 详情、助战或编队确认页 | 有进度时只补剩余场次，确认 CLEAR 后切换目标，可设置战斗场次上限 |
| 地狱的试炼 | 主界面、试炼入口或本期关卡内 | 按“重复挑战次数”出击，默认 1 次。通关后返回活动页，按设置继续挑战 |

#### 各副本的设置与注意事项

- **主线** · 原生模式默认处理已支持的编队限制，限制关临时使用默认打法，下一关恢复方案。灵脉石续关默认关闭，开启后每关最多使用 3 次，不消耗令咒或圣晶石。特殊解谜、分支与跨章节导航需手动处理。
- **幕间与强化本** · 均可设置挑战上限、苹果、助战、编队和战斗策略，无法确认界面或关卡开放状态时会保留现场停止。幕间可设置跳过剧情，强化本可设置茶壶和羁绊满停止，按实际 AP 消耗挑战，兼容 0 AP、半减和普通 AP 关卡。[自动幕间副本说明](./docs/自动幕间副本.md)
  - 两项任务默认开启“特殊助战情况处理”。依赖指定助战或固定打法时，请关闭此选项。
- **自由本** · 复用苹果、助战与编辑器 / Chaldea 配置，不处理主线编队限制。换日等原因返回终端时会保留现场并停止，回到章节地图后可重新启动。
- **地狱的试炼** · 开打前核对内置示例的阵容和打法，或选择自己的方案。战败或识别失败时停止。[第四期试炼方案](./docs/战斗方案/地狱的试炼第四期/README.md)

其他任务的起始页面以界面说明为准。运行期间避免手动点击或滚动画面。

### 5. 可选：制作或导入自己的战斗方案

1. 双击 **打开编队编辑器.cmd**，等待本地服务启动并打开网页。
2. 新建方案，或导入 Chaldea / BBC 文件、游戏编队截图、本工具导出的一图流；核对六个位置、助战及礼装。
3. 在“回合逻辑”安排技能、目标、换人和出卡，在“检查与保存”处理阻断问题并点击“保存方案”。
4. 复制保存的文件名，填入战斗任务的“Chaldea 队伍导入”，按上一步设置编队方式。

截图导入后需自行补充养成要求与战斗回合。分享方案时可导出一图流，也可直接分享 `config/Battle/` 下的 JSON；BBC 导入的转换提示请逐项核对。

### 6. 可选：启用快速羁绊编队

1. 从游戏主界面运行“构建个人从者礼装库”。
2. 选择扫描“从者和礼装”“仅从者”或“仅礼装”；礼装可扫描全部 4/5 星或仅羁绊礼装。
3. 等待任务完整扫描到列表底部。结果保存在 `config/Inventory/player_servants.json` 和 `config/Inventory/player_equips.json`。
4. 在羁绊补齐任务中开启对应的本地库选项。

扫描采用完成后原子替换；任务中止、超时或识别失败时，会保留已有的有效库存文件。

---

## Android / APK 快速开始

### 1. 下载并安装 APK

打开 [Releases](https://github.com/xlxyvergil/MaaFgo/releases)，在对应版本的 **Assets** 中选择 APK，下载后直接安装：

| 设备 | 下载文件 |
| --- | --- |
| 常见 ARM64 安卓手机 | `MaaFGO-android-arm64-v8a-<版本号>.apk` |
| MuMu 12 等 x86_64 模拟器 | `MaaFGO-android-x86_64-<版本号>.apk` |

文件名中的 `<版本号>` 对应所选版本。部分旧版本没有 APK，请选择包含 APK 附件的版本；需要开发版时，可按 [安卓指南](./Android/README.md) 从 Actions 下载。

### 2. 启动并授权 Shizuku / Root

未 Root 的设备先安装并启动 Shizuku，再向 MaaFGO 授权。请按 Shizuku 应用内的引导或 [官方启动指南](https://shizuku.rikka.app/zh-hans/guide/setup/) 操作；如果界面只提供“通过连接电脑启动（使用 adb）”，点“查看指令”，在电脑上执行它为当前设备显示的命令。**只安装 Shizuku 不等于服务已启动**，手机重启后通常需要重新启动服务。

已有 Root 的设备也可在 MaaFGO 选择 Root 后端并授权。随后确认 MaaFGO 已连接后端，选择正确的服务器资源和游戏渠道包名。

### 3. 选择运行方式

| 方式 | 启动步骤 | 画面与限制 |
| --- | --- | --- |
| 后台（建议先试） | 切换到“后台模式”，仅勾选“登录”，从任务页开始 | 游戏运行在独立虚拟屏，MaaFGO 顶部显示预览，主屏无需改为横屏；部分渠道 SDK 的登录或授权窗口仍可能短暂出现在主屏 |
| 前台 | 切换到“前台模式”，选择“悬浮球模式”；必要时在首页点“修改分辨率”；点“打开操作面板”并授予悬浮窗权限；手动打开游戏，等横屏 16:9 稳定后，从悬浮球面板点“开始任务” | 直接操作主屏；任务页的开始按钮用于后台模式。用完可点“重置分辨率”恢复系统显示尺寸 |

先确认“登录”可以完成，再逐项启用其他任务。任务运行期间避免手动操作游戏或旋转屏幕；前台运行中尺寸变化会停止任务，恢复横屏后需重新开始。后台“屏保”是应用内黑底与降低亮度，系统息屏后持续运行尚未完成验收。

### 4. 使用 Chaldea、助战与本地仓库

- **Chaldea 队伍**：开启“使用编辑器/chaldea配置”，将分享链接或队伍 ID 粘贴到“Chaldea 队伍导入”。手机可直接联网下载并缓存，也支持关卡 ID、缓存文件名及手机可读的本地 JSON 路径；电脑路径不能直接用于手机，`content://` 文件 URI 暂不自动转换。编队方式与高级策略设置同上，完整游戏内编队仍需按实际账号验证。
- **助战礼装**：使用标准选项列表选择 APK 已内置的图片模板。助战与关卡 YOLO 模型均已随包提供；添加自己的识别图片需要重新打包，暂不支持桌面版的文件夹动态扫描与大图预览。
- **仓库统计**：运行“构建个人从者礼装库”，等待扫描完成，再在羁绊补齐任务中启用对应本地库。手机库存保存在应用文件目录的 `pi/config/Inventory/`，Chaldea 缓存位于 `pi/config/Battle/`；不会自动与电脑的 `config/` 同步。详细目录见 [安卓使用说明](./Android/README.md)。

### 5. 更新与数据保留

使用**相同签名**的 APK 覆盖安装时会保留配置、库存、队伍缓存和羁绊进度。同一 Git 提交反复构建的 APK 版本码相同，覆盖后需到“设置 → 项目资源 → 重新解压资源”刷新任务与图片；该操作保留 `pi/config/`。

**卸载应用或清除数据会删除这些文件。** 本地与 CI 调试包可能签名不同，无法覆盖安装时请先备份数据或使用相同签名的构建。“导出配置”不包含库存、队伍缓存与羁绊进度，这些文件需另行备份 `pi/config/`。安卓更新可选 GitHub 或 Mirror酱；Mirror酱下载需要有效 CDK，安卓与桌面安装包按系统和架构分别提供。

完整安装指引、兼容差异与已知问题见[安卓使用说明](./Android/README.md)。Windows 的网页编辑器与 BBC 运行环境不随 APK 提供；在电脑上保存的 Chaldea JSON 可手动复制到手机再使用。

---

## 常见问题

| 情况 | 处理方式 |
| --- | --- |
| 没有安装 BBC，能否使用？ | 可以。刷本任务选择“原生自动战斗”，并取消勾选“启动 BBC”；安卓直接使用原生战斗 |
| 导入 Chaldea 后没有自动选人或配礼装 | 检查“编队方式”是否为“自动”，以及“自动编成礼装”是否开启；“手动”编队只核对从者、调整位置并校验御主礼装 |
| 方案里的御主礼装没有自动更换 | “手动”编队仅校验，需自行更换；“自动”编队会更换方案指定的御主礼装，不受“自动编成礼装”开关影响 |
| 编辑器保存的全局高级策略没有生效 | 在战斗任务的“高级策略”中选择“使用编辑器/chaldea配置”；保留“默认”时不会读取方案里的全局策略 |
| 只想找指定礼装，不限制助战从者 | 选择自定义助战，将英灵设为“0-不要求英灵”，再配置礼装与满破条件；此时不检查从者、技能、宝具或等级 |
| 编辑器链接打不开 | 先运行软件目录的 CMD 或编辑器启动任务。服务默认使用 `127.0.0.1:18765`，同机一次只运行一份安装目录的编辑器 |
| 保存或导入方案后，实战动作与预期不同 | 核对御主礼装、实际编队、养成与累计回合。指定普通卡未抽到时由所选补牌策略补齐；新方案先观察一场，BBC 转换还需核对转换提醒 |
| 召唤、出售或切换账号后，本地库不准确 | 重新运行“构建个人从者礼装库”；库存不按账号或区服自动隔离 |
| 任务失败需要反馈 | 附客户端版本、区服、任务与选项、起始页面截图及日志；安卓另附设备型号、系统、架构、Shizuku / Root 和前台 / 后台模式。QQ群：`1102496882` |

“特殊助战情况处理”在普通任务中默认关闭，在“自动幕间副本”和“挑战强化本”中默认开启。开启后，刷新 5 次并查完最后一轮仍找不到目标时，会选择首个可用助战，跳过本场 Chaldea 编队、羁绊补齐与战斗计划，使用原生默认策略。

---

## 使用与维护文档

### 功能使用

- [安卓使用、构建与平台差异](./Android/README.md)
- [编队与战斗编辑器：截图导入、BBC 转换与一图流分享](./docs/HTML编队与战斗逻辑工具使用说明.md)
- [Chaldea 自动编队使用说明](./docs/Chaldea自动编队使用说明.md)
- [BBC 配置转 Chaldea 本地队伍](./docs/BBC配置转Chaldea本地队伍.md)
- [个人从者与礼装库构建](./docs/个人从者礼装库构建说明.md)
- [搓丸子使用、保护规则与验收范围](./docs/搓丸子使用与维护说明.md)

### 开发与验证

- [安卓兼容方案与 MXU 影响核验](./Android/COMPATIBILITY.md)
- [安卓模拟器与真机验收记录](./Android/TESTING.md)
- [编队与战斗编辑器维护说明](./docs/HTML编队与战斗编辑器维护说明.md)
- [自动推主线设计与实施记录](./docs/自动推主线.md)
- [自动幕间副本设计与实测记录](./docs/自动幕间副本.md)
- [Chaldea 羁绊补齐自动编队](./docs/Chaldea羁绊补齐自动编队方案.md)
- [当前编队羁绊最大化补齐](./docs/当前编队羁绊最大化补齐任务方案.md)
- [羁绊补齐测试验证清单](./docs/Chaldea羁绊补齐自动编队测试验证清单.md)
- [羁绊数据维护工具](./docs/羁绊数据维护工具.md)
- [识图美术资源目录约定](./docs/识图美术资源目录约定.md)

---

## 开发相关

### 项目结构

```text
MaaFgo/
├── Android/        # 安卓外壳、补丁、构建配置与验收记录
├── agent/          # Python Agent 与 Custom Action
├── assets/         # 图片、Pipeline、任务及选项配置
├── config/         # 用户本地配置与运行时生成的数据（默认不提交）
├── web/            # 编队与战斗编辑器前端源码
├── docs/           # 功能设计、验证记录与维护说明
├── BBchannel/      # BBchannel 战斗核心
├── bbcdll/         # BBC 接口补丁
├── deps/           # MaaFramework 依赖
└── tools/          # 数据维护和开发工具
```

### 技术栈

- **核心框架**：[MaaFramework](https://github.com/MaaXYZ/MaaFramework) — 图像识别与自动化流程
- **桌面客户端**：[MXU](https://github.com/MistEO/MXU) — 基于 Tauri 2 + React 的通用 GUI
- **安卓客户端**：[MaaFwApp](https://github.com/Aliothmoon/MaaFwApp) — Shizuku / Root 控制与前后台运行
- **战斗执行**：共享 Python 原生自动战斗；Windows 另支持 [BBchannel](https://github.com/Meowcolm024/FGO-Automata)
- **编队与战斗编辑器**：本地 Python 服务与 React / TypeScript / Vite 前端，使用 Chaldea JSON 保存方案
- **地图导航**：基于 YOLO / ONNX 的地图目标检测与轮巡导航

提交资源或数据改动前，请先阅读对应的维护文档，并运行相关 JSON 校验与 `git diff --check`。

### 回归验证

修改 Pipeline 或选项 JSON 后，可先运行不依赖 MaaFramework 或设备的快速检查：

```powershell
python tools/run_tests.py --suite static
```

该入口检查 `assets/` 下除 `web/` 外所有 JSON 中的内联 OCR 名称。`And` / `Or` 的
`all_of` / `any_of` 中，每个内联 OCR 项（包括多层嵌套）必须有非空且全局唯一的
`sub_name`，与 `recognition` 同级，而不是放进 OCR 的 `param`。名称用于区分批量
OCR 缓存，采用 `资源层/文件名/节点名/OCR序号`，例如
`base/进本流程/进本-断网重连/OCR1`；复制识别项或新增资源层、选项覆盖时也须使用唯一名称。
所有回归套件会先执行此检查，失败后立即退出。

使用 Python 3.12 安装测试依赖后，可运行不依赖个人 BBC 安装目录或账号配置的回归：

```powershell
python -m pip install -r tests/requirements.txt
python tools/run_tests.py
python tools/validate_schema.py --schema-dir deps/tools --resource-dirs assets/resource --exclude-dirs assets/resource/announcement --interface-files assets/interface.json --task-dirs assets/tasks assets/options --require-tracked-imports
git diff --check
```

`--suite unit` 和 `--suite component` 可分别运行单元与 MaaFramework 组件测试。组件测试
使用内存控制器，不会连接游戏；默认使用 maafw wheel 自带的动态库，也可通过
`MAAFW_BINARY_PATH` 指定库目录。Linux 运行原生框架前需安装 `libpipewire-0.3-0`。
GitHub Actions 已配置这两个测试作业；实机识图与操作仍需单独验证。

资源检查固定使用 MaaFramework 5.14.3，检查工具失败时最多重试三次，最终失败仍会阻止检查通过；JSON Schema 校验会独立继续执行，便于区分资源错误与工具依赖下载问题。检查日志若只到 `preparing maafw / download-binary / finish`，表示框架准备失败，尚未开始资源检查，不能据此认定缺图。

发布校验要求主界面的所有导入文件已加入 Git 索引。仅本机使用的开发入口可保存在被忽略的
`assets/interface.local.json`，由开发启动方式显式加载；正式发布使用 `assets/interface.json`。
本地副本不会自动跟随主配置更新，不要直接把它复制到打包版覆盖正式入口。

---

## 鸣谢

| 项目 | 描述 |
| :--- | :--- |
| [**MaaFramework**](https://github.com/MaaXYZ/MaaFramework) | 图像识别自动化框架 |
| [**MXU**](https://github.com/MistEO/MXU) | 轻量级跨平台通用 GUI |
| [**MaaFwApp**](https://github.com/Aliothmoon/MaaFwApp) / [**MaaAgentCoreAndroid**](https://github.com/Aliothmoon/MaaAgentCoreAndroid) | 安卓客户端与 Python 运行环境 |
| [**MaaEnd**](https://github.com/MaaEnd/MaaEnd) | 安卓打包接入参考 |
| [**BBchannel**](https://github.com/Meowcolm024/FGO-Automata) | FGO 自动化战斗核心 |
| [**FGO-py**](https://github.com/hgjazhgj/FGO-py) | 地图导航图像匹配算法参考 |

[![Contributors](https://contrib.rocks/image?repo=xlxyvergil/MaaFgo&max=1000)](https://github.com/xlxyvergil/MaaFgo/graphs/contributors)

---

## 免责声明

本项目面向学习与交流，使用本项目造成的任何后果由使用者自行承担。

FGO 版权归 TYPE-MOON / FGO PROJECT 所有，本项目与官方无关。

## 许可证

本项目基于 [AGPL-3.0 License](./LICENSE) 开源。
