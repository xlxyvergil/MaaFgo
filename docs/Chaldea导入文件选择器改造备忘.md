# Chaldea 队伍导入：文件选择器配置

状态：MXU v2.5.5 起已支持，MaaFGO 的两个导入入口均已配置。发行包需要同时包含新版 MXU 可执行文件与这两份选项 JSON；只更新其中一侧不会启用完整功能。

## 使用行为

「Chaldea 队伍导入」点击文件按钮时，默认打开本安装目录的 `config/Battle`，优先显示 `.json`，也可切换到「所有文件」。目录不存在、不是目录或不可访问时，回退到软件目录，不自动创建目录。选择文件后仍把绝对路径写回原输入框。

输入框仍可手动填写 Chaldea 分享链接、队伍 ID、关卡 ID、本地文件路径或已缓存的队伍文件名。已有输入值、默认值、校验正则和导入脚本不变。对话框位置来自项目配置，不把输入框中的链接、ID 或旧安装路径当作默认目录。WebUI 保持文本输入，不在浏览器中弹出宿主电脑的原生文件对话框。

## 配置契约

MXU `InputItem` 增加两个可选字段，位于 `input_type: "file"` 同级：

```json
"file_dialog_dir": "config/Battle",
"file_dialog_filters": [
  {"name": "JSON", "extensions": ["json"]},
  {"name": "所有文件", "extensions": ["*"]}
]
```

`file_dialog_dir` 相对于 `interface.json`／MXU 软件根目录解析，与进程当前工作目录无关。中文、空格目录通过 Tauri 路径 API 组合；绝对路径、盘符、UNC 或父目录跳转不作为有效的项目相对目录使用。

`file_dialog_filters` 的首项是默认类型，扩展名不带前导点，`*` 表示所有文件。未设置这些字段的其他文件输入维持原行为，包括通用启动程序选择器的可执行文件筛选。两个 Chaldea 入口分别是：

- `assets/options/Chaldea导入手动输入.json` → `Chaldea导入手动输入.inputs`
- `assets/options/chaldea_team_config.json` → `chaldea_team_config.inputs`

客户端的 `OptionEditor` 把配置和项目 `basePath` 传给 `FileInput`；后者解析可用的默认目录，再把 `defaultPath` 与筛选传给 Tauri `open()`。MaaFGO 的 `deps/tools/interface.schema.json` 已接受新字段，并校验筛选结构。安卓转换将文件输入降为文本时，同时删除目录和筛选字段，不把电脑路径配置带进 APK。

## 发布与验收边界

客户端工程为 `xlxyvergil/MXU-scan_select-`，功能已通过 PR #3 合入并发布于 v2.5.5。MaaFGO 的 `.github/workflows/install.yml` 从该仓库最新正式 Release 下载客户端，无需更换下载来源。发布 MaaFGO 前还须确认本文件列出的两份选项配置已进入所打的 tag。

专项回归覆盖两个源入口、Schema 对有效与错误字段的判断、安卓剔除及原有链接／ID／路径输入兼容。修改选项后需运行 `python tools/run_tests.py --suite static`，客户端还需通过前端构建与目录回退测试。真正的系统文件对话框需要在新版 MXU 可执行文件上确认起始目录和默认筛选，不能用浏览器模拟的参数检查代替整包实点验收。
