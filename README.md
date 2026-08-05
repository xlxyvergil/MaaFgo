<!-- markdownlint-disable MD033 MD041 -->
<div align="center">

<img alt="LOGO" src="https://raw.githubusercontent.com/xlxyvergil/MaaFgo/main/1.png" width="256" height="256" />

# MaaFgo

基于图像识别的 FGO 自动战斗助手，解放双手！  
由 [MaaFramework](https://github.com/MaaXYZ/MaaFramework) 强力驱动！  
<a href="https://github.com/xlxyvergil/MaaFgo" target="_blank" style="font-weight: bold;">🔗 项目仓库</a><br>
🌟 觉得好用就在仓库右上角点个 Star 吧 🌟

</div>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3776AB?logo=python&logoColor=white">
  <img alt="platform" src="https://img.shields.io/badge/platform-Android%20Emulator-blueviolet">
  <img alt="license" src="https://img.shields.io/github/license/xlxyvergil/MaaFgo">
  <br>
  <img alt="commit" src="https://img.shields.io/github/commit-activity/m/xlxyvergil/MaaFgo">
  <img alt="stars" src="https://img.shields.io/github/stars/xlxyvergil/MaaFgo?style=social">
</p>

---

## 功能列表

### ⚔️ 战斗相关

| 任务 | 说明 |
| ------ | ------ |
| 日常战斗 | 主线自由本刷取，覆盖冬木至梅塔特洛尼俄斯共 27 个章节 |
| 迦勒底之门 | 种火、修炼场、QP 本等日替副本 |
| 冠位戴冠战 | 支持职阶和难度选择，含国服最新枪冠位 |
| 通用大活动 | 适配有独立地图的大型活动，自动找上次挑战副本 |
| 通用小活动 | 适配无地图的小型活动，支持倒数选择副本 |
| 直接 BBC 战斗 | 跳过导航，在助战/编队界面直接启动 |
| Chaldea 联动 | 粘贴 Chaldea 分享链接，一键配置队伍并战斗 |

### 🛠 基础流程

| 任务 | 说明 |
| ------ | ------ |
| 登录 | 自动启动游戏，处理更新弹窗和选择服务器 |
| 启动 BBC | 连接 BBchannel 战斗核心，支持雷电/MuMu/ADB |
| 关闭游戏 | 任务结束后自动关闭游戏 |

### 🗓 日常行为

| 任务 | 说明 |
| ------ | ------ |
| 每日 1 石 | 自动抽每日付费召唤卡池，石头不足自动跳过 |
| 免费友情 10 连 | 自动抽每日免费友情池 |
| 常驻每日 1 抽 | 常驻卡池每日免费一抽 |
| 种树 | 自动收集蓝苹果兑换体力 |

### 🍎 辅助功能

- **智能吃苹果**：体力不足时自动补充，支持金/银/铜/蓝苹果
- **自定义队伍**：支持预设多套队伍配置，自由切换
- **多模拟器**：支持雷电、MuMu、新版 MuMu 及手动 ADB 连接

---

## 支持的平台

| 服务器 | 主线 | Ordeal Call | 迦勒底之门 | 冠位戴冠战 |
| :--- | :---: | :---: | :---: | :---: |
| B 服（小米 / 应用宝） | ✅ | ✅ | ✅ | ✅ |
| 享游服 | ✅ | ✅ | ✅ | ✅ |
| 日服 | ✅ | ✅ | ✅ | ✅ |

---

## 快速开始

### 前置要求

- Windows 操作系统
- 安卓模拟器（雷电 / MuMu）

### 1. 下载安装

前往 [Releases](https://github.com/xlxyvergil/MaaFgo/releases) 下载最新版本。提供两种版本：

| 版本 | 文件名标识 | 说明 |
|------|-----------|------|
| **MXU**（推荐） | `MXU.zip` | 桌面客户端，原生窗口体验，启动更快，无需浏览器 |
| MWU | `MWU.zip` | Web 界面版，通过浏览器访问，兼容性好 |

> 推荐使用 MXU 桌面版，体验更流畅。

### 2. 安装 BBchannel

视频教程：

- [完整使用教程](https://www.bilibili.com/video/BV1GsjW6wEej/)
- [BBchannel 安装方法](https://www.bilibili.com/video/BV1c3DgBWEjN)

1. 将 BBC 文件夹放入 MaaFgo 根目录
2. 将 `bbcdll` 文件夹内的文件放入 `BBchannel\dist\BBchannel64` 目录下（替换同名文件）

### 3. 连接模拟器

支持雷电、MuMu 自动检测，也支持手动输入 ADB 端口。

### 4. 配置并启动

通过界面选择任务，设置章节/关卡、队伍、战斗次数和苹果策略，点击开始即可。

---

## 开发相关

### 项目结构

```
MaaFgo/
├── agent/          # Python 代理程序
├── assets/         # 资源文件（图片、Pipeline、任务配置）
├── BBchannel/      # BBchannel 战斗核心
├── bbcdll/         # BBC 接口补丁
├── deps/           # MaaFramework 依赖
└── tools/          # 开发工具
```

### 技术栈

- **核心框架**: [MaaFramework](https://github.com/MaaXYZ/MaaFramework) — 图像识别自动化
- **前端**: MWU / MXU — Web 界面与桌面客户端
- **战斗核心**: [BBchannel](https://github.com/Meowcolm024/FGO-Automata)
- **地图导航**: [FGO-py](https://github.com/hgjazhgj/FGO-py) — 图像匹配与坐标导航

---

## 鸣谢

### 开源项目

| 项目 | 描述 |
|:---|:---|
| [**MaaFramework**](https://github.com/MaaXYZ/MaaFramework) | 图像识别自动化框架 |
| [**MWU**](https://github.com/ravizhan/MWU) | 基于 Vue + FastAPI 的轻量级跨平台通用 WebUI |
| [**MXU**](https://github.com/MistEO/MXU) | 基于 Tauri 2 + React 的轻量级跨平台通用 GUI |
| [**BBchannel**](https://github.com/Meowcolm024/FGO-Automata) | FGO 自动化战斗核心 |

### 开发者

[![Contributors](https://contrib.rocks/image?repo=xlxyvergil/MaaFgo&max=1000)](https://github.com/xlxyvergil/MaaFgo/graphs/contributors)

---

## 免责声明

本项目仅供学习交流使用，不得用于商业用途。使用本项目造成的任何后果由使用者自行承担。

FGO 版权归 TYPE-MOON / FGO PROJECT 所有，本项目与官方无关。

## 许可证

本项目基于 [AGPL-3.0 License](./LICENSE) 开源。
