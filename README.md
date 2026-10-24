<!-- markdownlint-disable MD033 MD041 -->
<div align="center">

<img alt="LOGO" src="https://raw.githubusercontent.com/xlxyvergil/MaaFgo/main/1.png" width="256" height="256" />

# MaaFgo

基于图像识别的 FGO 自动化助手，覆盖关卡导航、战斗、日常养成与智能编队，提供 Windows 桌面版与 Android APK。<br>
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

## 下载与版本选择

| 客户端 | 适用场景 | 下载与说明 |
| --- | --- | --- |
| Windows / MXU | 在电脑上通过 ADB 操作模拟器；可选 BBC 或原生自动战斗 | [桌面版 Releases](https://github.com/xlxyvergil/MaaFgo/releases)，下载 `*-MXU.zip` |
| Android / APK（测试版） | 直接在手机或模拟器内运行；使用 Shizuku / Root，支持前台与后台模式 | [安卓版 Releases](https://github.com/xlxyvergil/MaaFgo/releases)，下载对应架构的 `.apk`；详见 [安卓使用说明](./Android/README.md) |

安卓包与 Windows 包在同一个 Release 中提供。安卓手机通常选择 **`arm64-v8a`**，MuMu 12 选择 **`x86_64`**。APK 已包含 Python、识别模型和原生战斗运行环境，无需另装 BBC 或电脑端 Python；可从 Releases 下载新版 APK，已同步的版本也可通过 Mirror酱下载和更新。

---

## 功能一览

### ⚔️ 关卡与战斗

| 任务 | 说明 |
| --- | --- |
| 日常战斗 | 主线自由本刷取，覆盖冬木至梅塔特洛尼俄斯共 27 个章节 |
| 迦勒底之门 | 种火、修炼场、QP 本等日替副本 |
| 冠位戴冠战 | 支持职阶和难度选择，含国服枪冠位 |
| 大活动 / 小活动 | 适配有独立地图或无地图的活动，支持自动寻找副本与次数检测 |
| 直接 BBC 战斗（Windows） | 跳过地图导航，从助战或编队界面直接交给 BBchannel 战斗 |
| 芥学姐代打（测试） | 原生自动战斗，可从助战、编队或战斗界面启动；支持单次、连续出击、跳过剧情以及幕间物语 / 强化任务 |

### 🧩 编队与羁绊优化

| 任务 | 说明 |
| --- | --- |
| Chaldea 自动编队 | 导入 Chaldea 分享链接、队伍 ID、关卡 ID、本地队伍 JSON 或已下载的缓存名称，自动还原从者、助战与礼装 |
| Chaldea 羁绊补齐 | 作为自动编队的可选第二阶段，在不改动 Chaldea 指定位置和助战的前提下，按 COST 补齐从者与常驻羁绊礼装 |
| 当前编队羁绊最大化 | 从编队确认页识别当前队伍，保留已有从者，在 COST 上限内补空位并优化羁绊礼装 |
| 构建个人从者礼装库 | 从灵基一览扫描本账号持有的从者和礼装，供两种羁绊补齐流程快速规划 |

羁绊补齐默认使用实时扫描。先运行“构建个人从者礼装库”，再分别开启“使用本地从者库”和“使用本地礼装库”，可显著减少仓库扫描时间。库存文件缺失、损坏或扫描未完成时会自动回退到实时模式；召唤、出售或礼装状态变化后建议重新构建，切换账号或区服后应重新构建。

> “当前编队羁绊最大化”只允许从**编队确认页**启动；“修改所有礼装”默认关闭，因此已有礼装默认会被保留。助战及其礼装始终不会被编辑。

### 🛠 基础流程与日常养成

| 任务 | 说明 |
| --- | --- |
| 登录 / 关闭游戏 | 自动启动、登录或关闭所选渠道的游戏包 |
| 启动 BBC（Windows） | 连接 BBchannel 战斗核心，支持雷电、MuMu 和手动 ADB |
| 每日召唤 | 每日 1 石、免费友情 10 连、常驻每日 1 抽 |
| 技能升级 | 强化从者主动技能与追加技能，支持目标等级配置 |
| 强化从者 | 自动喂经验值卡并突破；可循环从礼物盒补充经验值卡 |
| 整理礼物盒 | 按星级或堆叠数量领取经验值卡，并可循环贩卖、处理背包或 QP 上限 |
| 种树 | 自动收集蓝苹果兑换体力 |

此外支持自定义队伍、助战从者与礼装、战斗次数，以及金 / 银 / 铜 / 蓝苹果补充策略。

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
| 战斗方式 | BBC / 原生自动战斗 | 原生自动战斗 |
| 其他 | Windows 系统区域为**中文（简体，中国）** | 前台操作面板需要悬浮窗权限；未支持仅靠无障碍服务运行 |

超宽屏手机建议先用后台模式。例如 **2160 × 720** 主屏可保持原尺寸，游戏在独立的 1280 × 720 虚拟屏运行；前台暂不支持以该超宽比例直接执行任务。设备的虚拟屏能力与厂商限制仍可能影响后台运行。

---

## Windows / MXU 快速开始

### 1. 下载并解压

前往 [Releases](https://github.com/xlxyvergil/MaaFgo/releases) 下载对应版本的 `*-MXU.zip`，完整解压后运行。MXU 是桌面客户端，无需浏览器。

### 2. 可选：安装 BBchannel

使用 BBC 战斗时需要此步骤；选择原生自动战斗可跳过。

1. 将 BBC 文件夹放入 MaaFgo 根目录。
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

普通刷本选择“原生自动战斗”后，可依次配置战斗次数、跳过剧情、是否使用 Chaldea
队伍和助战方式。开启“是否使用 Chaldea 队伍”后，再将分享链接、队伍 ID、关卡 ID、
本地 JSON 文件或缓存名称填入“Chaldea 队伍导入”；流程会先选择助战，到达编队页后
自动应用队伍，再进入战斗。关闭该开关时保持原有进本行为。

自动编队不会按等级或技能等级区分同名从者。运行前请将不希望选中的同名低等级从者
移出从者列表（例如放入保管室），否则可能选中低等级从者并导致战斗方案无法正常执行。

部分任务对起始页面有要求，请以界面中的任务说明为准。自动化运行期间不要手动点击或滚动画面。

### 5. 可选：启用快速羁绊编队

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

- **Chaldea 队伍**：开启“是否使用 Chaldea 队伍”，将分享链接或队伍 ID 粘贴到“Chaldea 队伍导入”。手机可直接联网下载并缓存，也支持关卡 ID、缓存文件名及手机可读的本地 JSON 路径；电脑路径不能直接用于手机，`content://` 文件 URI 暂不自动转换。队伍获取与完整游戏内编队是不同步骤，后者仍需按实际账号验证。
- **助战礼装**：使用标准选项列表选择 APK 已内置的图片模板。助战与关卡 YOLO 模型均已随包提供；添加自己的识别图片需要重新打包，暂不支持桌面版的文件夹动态扫描与大图预览。
- **仓库统计**：运行“构建个人从者礼装库”，等待扫描完成，再在羁绊补齐任务中启用对应本地库。手机库存保存在应用文件目录的 `pi/config/Inventory/`，Chaldea 缓存位于 `pi/config/Battle/`；不会自动与电脑的 `config/` 同步。详细目录见 [安卓使用说明](./Android/README.md)。

### 5. 更新与数据保留

使用**相同签名**的 APK 覆盖安装时会保留配置、库存、队伍缓存和羁绊进度。同一 Git 提交反复构建的 APK 版本码相同，覆盖后需到“设置 → 项目资源 → 重新解压资源”刷新任务与图片；该操作保留 `pi/config/`。

**卸载应用或清除数据会删除这些文件。** 本地与 CI 调试包可能签名不同，无法覆盖安装时请先备份数据或使用相同签名的构建。“导出配置”不包含库存、队伍缓存与羁绊进度，这些文件需另行备份 `pi/config/`。安卓更新可选 GitHub 或 Mirror酱；Mirror酱下载需要有效 CDK，安卓与桌面安装包按系统和架构分别提供。

完整安装指引、兼容差异与已知问题见 [Android/README.md](./Android/README.md)。

---

## 维护文档

- [安卓使用、构建与平台差异](./Android/README.md)
- [安卓兼容方案与 MXU 影响核验](./Android/COMPATIBILITY.md)
- [安卓模拟器与真机验收记录](./Android/TESTING.md)
- [Chaldea 自动编队使用说明](./docs/Chaldea自动编队使用说明.md)
- [个人从者与礼装库构建](./docs/个人从者礼装库构建说明.md)
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
- **地图导航**：基于 YOLO / ONNX 的地图目标检测与轮巡导航

提交资源或数据改动前，请先阅读对应的维护文档，并运行相关 JSON 校验与 `git diff --check`。

### 回归验证

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

本项目仅供学习交流使用，不得用于商业用途。使用本项目造成的任何后果由使用者自行承担。

FGO 版权归 TYPE-MOON / FGO PROJECT 所有，本项目与官方无关。

## 许可证

本项目基于 [AGPL-3.0 License](./LICENSE) 开源。
