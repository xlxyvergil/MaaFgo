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

MaaFgo 是一款基于图像识别技术的 FGO（Fate/Grand Order）自动战斗工具，专为国服 B 站版本设计。通过 MWU 前端提供 Web 访问支持，让您可以在浏览器中轻松配置和监控战斗任务。

## 功能列表

- 🎮 **自动登录** - 自动启动游戏并登录账号
- 🌳 **自动种苹果** - 自动收取种苹果奖励
- ⚔️ **日常战斗** - 自动完成日常副本战斗
- 📖 **主线推图** - 支持各章节主线关卡自动推进
- 🔄 **自动战斗** - 基于 BBchannel 的智能战斗系统
- 🎯 **自定义队伍** - 支持多种预设队伍配置
- 📱 **多模拟器支持** - 支持雷电、MuMu 等主流安卓模拟器

## 使用说明

### 前置要求

- Windows 操作系统
- 安卓模拟器（雷电模拟器 / MuMu 模拟器）
- FGO 国服 B 站版本

### 快速开始

1. **下载 release 版本**
   
   前往 [Releases](https://github.com/xlxyvergil/MaaFgo/releases) 页面下载最新版本

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

## 支持的平台

| 平台 | 状态 |
|------|------|
| 雷电模拟器 | ✅ 支持 |
| MuMu 模拟器 | ✅ 支持 |
| 其他 ADB 设备 | ✅ 支持 |

## 支持的章节
- Ordeal Call

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
- **前端**: MWU (Web UI)
- **战斗核心**: BBchannel
- **图像识别**: MaaFramework Pipeline + OCR

## 鸣谢

### 核心框架

- [MaaFramework](https://github.com/MaaXYZ/MaaFramework)  
  基于图像识别的自动化黑盒测试框架

### 前端支持

- [MWU](https://github.com/ravizhan/MWU)  
  基于 Web 的 MaaFramework 通用 GUI 解决方案

### 战斗核心

- [BBchannel](https://github.com/Meowcolm024/FGO-Automata)  
  FGO 自动化战斗核心

### 开发者

感谢以下开发者对本项目作出的贡献:

[![Contributors](https://contrib.rocks/image?repo=xlxyvergil/MaaFgo&max=1000)](https://github.com/xlxyvergil/MaaFgo/graphs/contributors)

## 免责声明

本项目仅供学习交流使用，不得用于商业用途。使用本项目造成的任何后果由使用者自行承担。

FGO 版权归 TYPE-MOON / FGO PROJECT 所有，本项目与官方无关。

## 许可证

本项目基于 [MIT License](./LICENSE) 开源。

