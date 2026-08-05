<!-- markdownlint-disable MD033 MD041 -->
<div align="center">

<img alt="LOGO" src="https://raw.githubusercontent.com/xlxyvergil/MaaFgo/main/1.png" width="256" height="256" />

# MaaFgo

基于全新架构的 FGO 自动战斗助手。图像技术 + 模拟控制，解放双手！  
由 [MaaFramework](https://github.com/MaaXYZ/MaaFramework) 强力驱动！  
<a href="https://github.com/xlxyvergil/MaaFgo" target="_blank" style="font-weight: bold;">🔗 本项目 GitHub 仓库</a><br>
🌟喜欢本项目就在仓库右上角点个星星吧🌟

</div>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3776AB?logo=python&logoColor=white">
  <img alt="platform" src="https://img.shields.io/badge/platform-Android%20Emulator-blueviolet">
  <img alt="license" src="https://img.shields.io/github/license/xlxyvergil/MaaFgo">
  <br>
  <img alt="commit" src="https://img.shields.io/github/commit-activity/m/xlxyvergil/MaaFgo">
  <img alt="stars" src="https://img.shields.io/github/stars/xlxyvergil/MaaFgo?style=social">
</p>

<div align="center">

[简体中文](./README.md)

</div>

## 简介

MaaFgo 是一款基于图像识别技术的 FGO（Fate/Grand Order）自动战斗工具。通过 MWU 前端提供 Web 访问支持，让您可以在浏览器中轻松配置和监控战斗任务。

## 功能列表

- 🎮 **自动登录** - 自动启动游戏，处理更新弹窗和登录流程
- ⚔️ **主线自由本** - 支持大部分主线章节和 Ordeal Call 白纸化关卡的自动刷取
- 🔥 **限时活动** - 支持有地图和无地图两类限时活动的自动战斗
- 🗡️ **冠位戴冠战** - 日服冠位戴冠战自动刷取，支持职阶和难度选择
- 🚪 **迦勒底之门** - 种火、修炼场、QP 本等日替副本自动刷取
- ⚡ **快速战斗** - 跳过导航，在当前助战 / 编队界面直接启动战斗
- 💎 **每日1石** - 自动抽取每日付费召唤卡池
- 🌳 **自动收树** - 自动收取蓝苹果（青铜树苗）兑换体力
- 🔗 **Chaldea 联动** - 粘贴 Chaldea 分享链接直接配置队伍并启动战斗
- 🎯 **自定义队伍** - 支持预设多套队伍配置，自由切换
- 🍎 **智能吃苹果** - 体力不足时自动补充，支持金 / 银 / 铜 / 蓝苹果
- 📱 **多模拟器支持** - 支持雷电、MuMu 等主流安卓模拟器，也可手动指定 ADB 连接

## 使用说明

### 前置要求

- Windows 操作系统
- 安卓模拟器（雷电模拟器 / MuMu 模拟器）

### 快速开始

1. **下载 release 版本**

   前往 [Releases](https://github.com/xlxyvergil/MaaFgo/releases) 页面下载最新版本

2. **正确放置bbc**
    视频教程：
   - bbchannel的安装方法请查看 <https://www.bilibili.com/video/BV1c3DgBWEjN> 。全版本使用bbc的方式一致。
   
   
    文字版说明：
    -  a.将BBC放入maaFGO的根目录
    -  b.将bbcdll文件夹内的文件放入BBchannel\\dist\\BBchannel64目录下【替换】（非64位的放BBchannel文件夹下）

2. **连接模拟器**

   支持多种连接方式：
   - 雷电模拟器自动检测
   - MuMu 模拟器自动检测
   - 手动输入 ADB 端口

3. **配置任务**

   通过 Web 界面配置需要执行的任务：
   - 选择章节和关卡
   - 设置队伍配置
   - 配置战斗次数和苹果使用策略

4. **启动任务**

   点击开始按钮，让 MaaFgo 自动完成战斗

## 支持的平台与内容

| 服务器 | 主线 | Ordeal Call 白纸化 | 迦勒底之门日替 | 冠位戴冠战 |
|:---|:---:|:---:|:---:|:---:|
| 国服 B 服 | ✅ | ✅ | ✅ | ✅ |
| 国服享游服 | ✅ | ✅ | ✅ | ✅ |
| 日服 | ✅ | ✅ | ✅ | ✅ |

> 主线覆盖冬木至梅塔特洛尼俄斯共 27 个章节

## 开发相关

### 项目结构

```
MaaFgo/
├── agent/          # Python 代理程序
├── assets/         # 资源文件（图片、配置、Pipeline）
├── BBchannel/      # BBchannel 战斗核心
├── deps/           # MaaFramework 依赖
└── tools/          # 开发工具
```

### 技术栈

- **核心框架**: [MaaFramework](https://github.com/MaaXYZ/MaaFramework)
- **前端**: MWU / MXU
- **战斗核心**: BBchannel
- **图像识别**: MaaFramework Pipeline + OCR

## 鸣谢

### 核心框架

- [MaaFramework](https://github.com/MaaXYZ/MaaFramework)  
  基于图像识别的自动化黑盒测试框架

### 前端支持

- [MWU](https://github.com/ravizhan/MWU)  
  基于 Vue + FastAPI 的轻量级跨平台通用 WebUI。由 MaaFramework 强力驱动！
- [MXU](https://github.com/MistEO/MXU)  
  基于 Tauri 2 + React 的轻量级跨平台通用 GUI。由 MaaFramework 强力驱动！

### 战斗核心

- [BBchannel](https://github.com/Meowcolm024/FGO-Automata)  
  FGO 自动化战斗核心

## 🙏 致谢

### 开源项目

| 项目 | 描述 |
|:---|:---|
| [**MaaFramework**](https://github.com/MaaXYZ/MaaFramework) | 图像识别自动化框架 |
| [**MWU**](https://github.com/ravizhan/MWU) | 基于 Vue + FastAPI 的轻量级跨平台通用 WebUI |
| [**MXU**](https://github.com/MistEO/MXU) | 基于 Tauri 2 + React 的轻量级跨平台通用 GUI |
| [**BBchannel**](https://github.com/Meowcolm024/FGO-Automata) | FGO 自动化战斗核心 |

### 开发者

感谢以下开发者对本项目作出的贡献:

[![Contributors](https://contrib.rocks/image?repo=xlxyvergil/MaaFgo&max=1000)](https://github.com/xlxyvergil/MaaFgo/graphs/contributors)

## 免责声明

本项目仅供学习交流使用，不得用于商业用途。使用本项目造成的任何后果由使用者自行承担。

FGO 版权归 TYPE-MOON / FGO PROJECT 所有，本项目与官方无关。

## 许可证

本项目基于 [AGPL-3.0 License](./LICENSE) 开源。
