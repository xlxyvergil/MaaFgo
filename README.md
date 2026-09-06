<!-- markdownlint-disable MD033 MD041 -->
<div align="center">

<img alt="LOGO" src="https://raw.githubusercontent.com/xlxyvergil/MaaFgo/main/1.png" width="256" height="256" />

# MaaFgo

基于图像识别的 FGO 自动化助手，覆盖关卡导航、战斗、日常养成与智能编队。<br>
由 [MaaFramework](https://github.com/MaaXYZ/MaaFramework) 强力驱动。<br>
<a href="https://github.com/xlxyvergil/MaaFgo" target="_blank" style="font-weight: bold;">🔗 项目仓库</a><br>
🌟 觉得好用就在仓库右上角点个 Star 吧 🌟

</div>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3776AB?logo=python&logoColor=white">
  <img alt="platform" src="https://img.shields.io/badge/platform-Windows%20%7C%20Android%20Emulator-blueviolet">
  <img alt="license" src="https://img.shields.io/github/license/xlxyvergil/MaaFgo">
  <br>
  <img alt="commit" src="https://img.shields.io/github/commit-activity/m/xlxyvergil/MaaFgo">
  <img alt="stars" src="https://img.shields.io/github/stars/xlxyvergil/MaaFgo?style=social">
</p>

---

## 功能概览

### ⚔️ 关卡与战斗

| 任务 | 说明 |
| --- | --- |
| 日常战斗 | 主线自由本刷取，覆盖冬木至梅塔特洛尼俄斯共 27 个章节 |
| 迦勒底之门 | 种火、修炼场、QP 本等日替副本 |
| 冠位戴冠战 | 支持职阶和难度选择，含国服枪冠位 |
| 大活动 / 小活动 | 适配有独立地图或无地图的活动，支持自动寻找副本与次数检测 |
| 直接 BBC 战斗 | 跳过地图导航，从助战或编队界面直接交给 BBchannel 战斗 |
| 芥学姐代打（测试） | 原生自动战斗，可从助战、编队或战斗界面启动；支持单次、连续出击、跳过剧情以及幕间物语 / 强化任务 |

### 🧩 编队与羁绊优化

| 任务 | 说明 |
| --- | --- |
| Chaldea 自动编队 | 导入 Chaldea 分享链接、队伍 ID、关卡 ID、本地队伍 JSON 或已下载的缓存名称，自动还原从者、助战与礼装 |
| Chaldea 羁绊补齐 | 作为自动编队的可选第二阶段，在不改动 Chaldea 指定位置和助战的前提下，按 COST 补齐从者与常驻羁绊礼装 |
| 当前编队羁绊最大化 | 从编队确认页识别当前队伍，保留已有从者，在 COST 上限内补空位并优化羁绊礼装 |
| 构建个人从者礼装库 | 从灵基一览扫描本账号持有的从者和礼装，供两种羁绊补齐流程快速规划 |

羁绊补齐默认使用实时扫描。先运行“构建个人从者礼装库”，再分别开启“使用本地从者库”和“使用本地礼装库”，可显著减少仓库扫描时间。库存文件缺失、损坏或扫描未完成时会自动回退到实时模式；召唤、出售或礼装状态变化后建议重新构建。

> “当前编队羁绊最大化”只允许从**编队确认页**启动；“修改所有礼装”默认关闭，因此已有礼装默认会被保留。助战及其礼装始终不会被编辑。

### 🛠 基础流程与日常养成

| 任务 | 说明 |
| --- | --- |
| 登录 / 关闭游戏 | 自动启动、登录或关闭所选渠道的游戏包 |
| 启动 BBC | 连接 BBchannel 战斗核心，支持雷电、MuMu 和手动 ADB |
| 每日召唤 | 每日 1 石、免费友情 10 连、常驻每日 1 抽 |
| 技能升级 | 强化从者主动技能与追加技能，支持目标等级配置 |
| 强化从者 | 自动喂经验值卡并突破；可循环从礼物盒补充经验值卡 |
| 整理礼物盒 | 按星级或堆叠数量领取经验值卡，并可循环贩卖、处理背包或 QP 上限 |
| 种树 | 自动收集蓝苹果兑换体力 |

此外支持自定义队伍、助战从者与礼装、战斗次数，以及金 / 银 / 铜 / 蓝苹果补充策略。

---

## 支持范围

### 资源包

| 服务器资源 | 主线 | Ordeal Call | 迦勒底之门 | 冠位戴冠战 |
| :--- | :---: | :---: | :---: | :---: |
| B 服（含小米 / 应用宝等渠道） | ✅ | ✅ | ✅ | ✅ |
| 享游服 | ✅ | ✅ | ✅ | ✅ |
| 日服 | ✅ | ✅ | ✅ | ✅ |

登录和关闭游戏目前提供 B 服、4399 服、vivo 服、享游服、应用宝服、日服和小米服包名选项。不同渠道仍需选择与游戏画面对应的资源包。

### 运行要求

- Windows 操作系统
- 雷电或 MuMu 安卓模拟器，也可手动填写 ADB 端口
- 模拟器分辨率必须为 **1280 × 720**
- Windows 系统区域必须为**中文（简体，中国）**

> ⚠️ 雷电 14 上部分功能可能不可用，例如自动启动游戏、任务结束后自动关闭游戏。建议使用雷电 9 或 MuMu 获得完整体验。

---

## 快速开始

### 1. 下载并解压

前往 [Releases](https://github.com/xlxyvergil/MaaFgo/releases) 下载最新版 `MXU.zip`，完整解压后运行。MXU 是桌面客户端，无需浏览器。

### 2. 安装 BBchannel

1. 将 BBC 文件夹放入 MaaFgo 根目录。
2. 将 `bbcdll` 文件夹内的文件复制到 `BBchannel\dist\BBchannel64`，替换同名文件。

视频教程：

- [完整使用教程](https://www.bilibili.com/video/BV1GsjW6wEej/)
- [BBchannel 安装方法](https://www.bilibili.com/video/BV1c3DgBWEjN)

### 3. 配置模拟器

1. 将模拟器分辨率固定为 `1280 × 720`。
2. 启动游戏并保持画面无遮挡。
3. 在 MaaFgo 中选择对应资源包和渠道包名。
4. 使用雷电 / MuMu 自动检测，或手动填写 ADB 端口后连接。

### 4. 选择任务并启动

普通刷本可依次配置章节 / 关卡、队伍、战斗次数、助战和苹果策略。需要 Chaldea 编队时，将分享链接、队伍 ID、关卡 ID、本地 JSON 文件或缓存名称填入“Chaldea 队伍导入”。

部分任务对起始页面有要求，请以界面中的任务说明为准。自动化运行期间不要手动点击或滚动画面。

### 5. 可选：启用快速羁绊编队

1. 从游戏主界面运行“构建个人从者礼装库”。
2. 选择扫描“从者和礼装”“仅从者”或“仅礼装”；礼装可扫描全部 4/5 星或仅羁绊礼装。
3. 等待任务完整扫描到列表底部。结果保存在 `config/Inventory/player_servants.json` 和 `config/Inventory/player_equips.json`。
4. 在羁绊补齐任务中开启对应的本地库选项。

扫描采用完成后原子替换；任务中止、超时或识别失败时，会保留已有的有效库存文件。

---

## 维护文档

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
- **战斗核心**：[BBchannel](https://github.com/Meowcolm024/FGO-Automata)
- **地图导航**：基于 YOLO / ONNX 的地图目标检测与轮巡导航

提交资源或数据改动前，请先阅读对应的维护文档，并运行相关 JSON 校验与 `git diff --check`。

---

## 鸣谢

| 项目 | 描述 |
| :--- | :--- |
| [**MaaFramework**](https://github.com/MaaXYZ/MaaFramework) | 图像识别自动化框架 |
| [**MXU**](https://github.com/MistEO/MXU) | 轻量级跨平台通用 GUI |
| [**BBchannel**](https://github.com/Meowcolm024/FGO-Automata) | FGO 自动化战斗核心 |
| [**FGO-py**](https://github.com/hgjazhgj/FGO-py) | 地图导航图像匹配算法参考 |

[![Contributors](https://contrib.rocks/image?repo=xlxyvergil/MaaFgo&max=1000)](https://github.com/xlxyvergil/MaaFgo/graphs/contributors)

---

## 免责声明

本项目仅供学习交流使用，不得用于商业用途。使用本项目造成的任何后果由使用者自行承担。

FGO 版权归 TYPE-MOON / FGO PROJECT 所有，本项目与官方无关。

## 许可证

本项目基于 [AGPL-3.0 License](./LICENSE) 开源。
