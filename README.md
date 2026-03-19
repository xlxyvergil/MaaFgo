<!-- markdownlint-disable MD033 MD041 -->
<p align="center">
  <img alt="LOGO" src="app\assets\icons\logo.png" width="256" height="256" />
</p>
<div align="center">

# MFW-ChainFlow Assistant(链程助手)

**[简体中文](./README.md) | [English](./README-en.md)**

基于 **[PySide6](https://doc.qt.io/qtforpython-6)** 与 **[MaaFramework](https://github.com/MaaXYZ/MaaFramework)** 的跨平台 GUI，完整支持 interface v2 协议，开箱即用地编排、运行和扩展自动化流程。
</div>

<p align="center">
  <img alt="license" src="https://img.shields.io/github/license/overflow65537/MFW-PyQt6">
  <img alt="Python" src="https://img.shields.io/badge/Python-3776AB?logo=python&logoColor=white">
  <img alt="platform" src="https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-blueviolet">
  <img alt="commit" src="https://img.shields.io/github/commit-activity/m/overflow65537/MFW-PyQt6">
</p>

## 目录

- [简介](#简介)
- [功能亮点](#功能亮点)
- [速通模式](#速通模式)
- [常用命令行参数](#常用命令行参数)
- [外部通知](#外部通知)
- [计划任务](#计划任务)
- [热更新](#热更新)
- [动态加载自定义动作/识别器](#动态加载自定义动作识别器)
- [使用 GitHub Action 自动构建](#使用-github-action-自动构建)
- [许可证](#许可证)
- [致谢](#致谢)

## 简介

MFW-ChainFlow Assistant 旨在为 MaaFramework 用户提供开箱即用的可视化运行器，覆盖配置管理、任务调度、通知联动以及自定义扩展，降低自动化流程的开发和运维成本。

## 功能亮点

- 完整支持 [interface v2 协议](https://github.com/MaaXYZ/MaaFramework/blob/main/docs/zh_cn/3.3-ProjectInterfaceV2%E5%8D%8F%E8%AE%AE.md)
- 跨平台支持：Windows / Linux / macOS
- 可带参数启动：指定配置 ID 与自动执行任务
- 外部通知：钉钉、飞书、SMTP、WxPusher、企业微信机器人、Gotify
- 内置计划任务：单次 / 每日 / 每周 / 每月，多策略执行
- 动态加载自定义动作与识别器的同时支持Agent，适配个性化流程
- 嵌入式 Agent：在 agent 字段中启用内置模式，自动转换为 custom 加载方式，使用 UI 内部环境，更小更轻盈
- 速通模式：按日 / 周 / 月限制运行次数与最小间隔，避免重复执行
- 热更新：资源仓库与本地 update_flag.txt 一致时自动启用，速度更快且无需重启

## 速通模式

- 在 `interface.json` 的任务节点下添加 `speedrun` 块定义周期与次数控制，并在 UI/CLI 打开 speedrun 模式后生效。
- 支持 daily / weekly / monthly，配置运行次数与最小间隔，超限时自动跳过。
- 详细字段与示例见 [docs/speedrun_mode.md](docs/speedrun_mode.md)。

## 常用命令行参数

- `-c <config_id>`：使用指定配置 ID 启动（可用于 `python main.py` 或打包后的 `MFW.exe`）
- `-d`：启动后直接运行任务（同样适用于 `MFW.exe`）
- `-dev` ：启用调试模式

## 外部通知

当前支持：钉钉、飞书、SMTP、WxPusher、企业微信机器人、Gotify，可按需在配置中启用。

## 计划任务

支持单次、每日、每周、每月的定时运行，可选择强制启动或按队列执行，列表中可直接启用/禁用或删除计划。

## 热更新

当资源仓库中包含的 `update_flag.txt` 文件内容和本地的 `update_flag.txt` 内容不一致的时候，会启动热更新模式，速度更快并且无需重启。

## 动态加载自定义动作/识别器

参考 MaaFramework 的[自定义动作/识别器说明](https://github.com/MaaXYZ/MaaFramework/blob/main/docs/zh_cn/1.1-%E5%BF%AB%E9%80%9F%E5%BC%80%E5%A7%8B.md#%E4%BD%BF%E7%94%A8-json-%E4%BD%8E%E4%BB%A3%E7%A0%81%E7%BC%96%E7%A8%8B%E4%BD%86%E5%AF%B9%E5%A4%8D%E6%9D%82%E4%BB%BB%E5%8A%A1%E4%BD%BF%E7%94%A8%E8%87%AA%E5%AE%9A%E4%B9%89%E9%80%BB%E8%BE%91)：

1. 自定义动作/识别器需使用 Python 3.12。
2. 若包含第三方库，请将依赖安装到 `_internal` 目录。
3. 在 `custom.json` 中声明自定义对象，并在 `interface.json` 的 `custom` 键指出 `custom.json` 路径（`{custom_path}` 默认为仓库根目录下的 `custom/`）。
4. Pipeline 中通过名字引用自定义动作/识别器。

示例 `custom.json` 片段：

```json
{
  "动作名字1": {
    "file_path": "{custom_path}/任意位置/任意名字_动作1.py",
    "class": "动作对象1",
    "type": "action"
  },
  "识别器名字1": {
    "file_path": "{custom_path}/任意位置/任意名字_识别器1.py",
    "class": "识别器对象1",
    "type": "recognition"
  }
}
```

在 pipeline 中引用：

```json
"我的自定义任务": {
  "recognition": "Custom",
  "custom_recognition": "识别器名字1",
  "action": "Custom",
  "custom_action": "动作名字1"
}
```

自定义类示例：

```python
class 动作对象1(CustomAction):
    def apply(self, context, ...):
        image = context.tasker.controller.cached_image
        # 在此处进行图像处理并返回结果
```

更多示例可参考仓库：[MAA_Punish/assets](https://github.com/overflow65537/MAA_Punish/tree/main/assets)。

### 嵌入式 Agent

在 `interface.json` 的 `agent` 字段中设置 `embedded: true`，系统会自动将 agent 转换为 custom 加载方式。这种方式使用 UI 内部环境运行，无需独立进程，资源占用更小、启动更快。

示例 `interface.json` 片段：

```json
{
  "agent": {
    "embedded": true,
    "child_args": ["{PROJECT_DIR}/agent/main.py"]
  }
}
```

启用内置模式后，系统会自动：

1. 复制 agent 入口目录
2. 自动生成对应的 `custom.json` 配置
3. 移除 `agent` 字段，改用 `custom` 字段加载

## 使用 GitHub Action 自动构建

0. 注意:此方案只适用于使用maafw模板构建的项目
1. 将 `deploy/install.yml` 中的 `MaaXXX` 替换为你的项目名。
2. 提交并推送到 GitHub 仓库的 `.github/workflows` 目录。
3. 推送新版本后，GitHub Action 会自动构建发布。

## 自行打包

1. 根据自身需求下载对应系统和架构的项目资产
2. 将`interface.json`,maafw资源等代码或者描述文件放入资产根目录(MFW执行方式同级)
3. 运行

## 许可证

**MFW-PyQt6** 使用 **[GPL-3.0 许可证](./LICENSE)** 开源。

## 致谢

### 开源项目

- [PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets)\
    A fluent design widgets library based on C++ Qt/PyQt/PySide. Make Qt Great Again.
- [MaaFramework](https://github.com/MaaAssistantArknights/MaaFramework)\
    基于图像识别的自动化黑盒测试框架。
- [MaaDebugger](https://github.com/MaaXYZ/MaaDebugger)\
    基于 MaaFramework 的调试器，用于查看和分析 MaaFramework 运行时的状态。
- [AutoMAA](https://github.com/DLmaster361/AUTO_MAA)\
    明日方舟 MAA 插件，多账号管理与自动化工具。

### 思路灵感

- [MFAAvalonia](https://github.com/SweetSmellFox/MFAAvalonia)\
    基于 Avalonia 的 MAAFramework 通用 GUI 项目 | A universal GUI project for MAAFramework based on Avalonia\
  **MFW-ChainFlow Assistant 参考了 MFAAvalonia 的布局,但未使用其任何源代码。**

- [MFWPH](https://github.com/TanyaShue/MFWPH)\
    基于 MaaFramework 的 UI 启动器，可加载与管理多种自动化资源脚本\
  **MFW-ChainFlow Assistant 参考了 MFWPH 的布局,但未使用其任何源代码。**

### 其他支持

- [MirrorChyan](https://github.com/MirrorChyan/docs)\
    Mirror酱更新服务\
  **MFW-ChainFlow Assistant 使用了 MirrorChyan 的更新服务。**

### 开发者

感谢所有为 **MFW-PyQt6** 做出贡献的开发者。

<a href="https://github.com/overflow65537/PYQT-MAA/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=overflow65537/PYQT-MAA&max=1000" alt="Contributors to MFW-PyQt6"/>
</a>
