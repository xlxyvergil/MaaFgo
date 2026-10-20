# MaaFGO 安卓版

在 Android 手机上直接运行 MaaFGO，支持 Shizuku / Root 授权、前台操作和后台虚拟屏。安卓版与 Windows / MXU 共用任务和 Python Agent，战斗使用原生自动战斗，手机无需安装 BBC、Python 或额外的模型运行环境。

首次使用请按下面的安装和授权步骤操作；开发构建与 MXU 适配说明在文末。具体实测记录见 [TESTING.md](TESTING.md)。

## 下载与设备要求

前往 [GitHub Releases](https://github.com/xlxyvergil/MaaFgo/releases)，在对应版本的 **Assets** 中下载适合设备的 APK，直接安装。安卓包与 Windows 包在同一个版本中提供；部分旧版本没有 APK，请选择包含 APK 附件的版本。

| 设备 | Releases 下载文件 | 使用要求 |
| --- | --- | --- |
| 常见 ARM64 安卓手机 | `MaaFGO-android-arm64-v8a-<版本号>.apk` | 目标系统 Android 10+；Shizuku 或已具备 Root 的设备 |
| MuMu 12 等 x86_64 模拟器 | `MaaFGO-android-x86_64-<版本号>.apk` | 选择模拟器实际架构，并启用 Shizuku 或 Root |

文件名中的 `<版本号>` 对应所选版本。正式版和 beta 均使用同一证书签名的 Release APK；旧版调试包可能带有 `-debug` 后缀。安卓暂未接入 Mirror酱更新源，更新时请下载新版 APK。

需要开发版时，可从 [GitHub Actions](https://github.com/xlxyvergil/MaaFgo/actions/workflows/android.yml) 下载：登录 GitHub，选择一次成功的 PR 或手动构建，在 **Artifacts** 中下载 `MaaFGO-android-<架构>-debug` 附件，解压后安装其中的 APK。维护者可通过“Run workflow”选择分支手动构建；普通分支 push 不再自动生成 APK。

外壳安装下限为 Android 9 / API 28，后台模式要求 Android 10 / API 29 以上；当前支持和测试目标为 Android 10+，能安装不代表所有功能已验收。MuMu 对普通 ARM 应用的兼容不能代替原生库兼容：本机 ARM64 包在 x86_64 MuMu 上虽能安装和打开界面，特权服务及部分推理未通过，请使用 x86_64 包。

## 首次授权与配置

1. **准备控制权限。** 未 Root 的手机先打开 Shizuku，按其界面提示或[官方启动指南](https://shizuku.rikka.app/zh-hans/guide/setup/)启动服务，再允许 MaaFGO 使用 Shizuku。Android 10 通常需要电脑 ADB，请使用 Shizuku 内“查看指令”显示的当前命令；启动命令的路径会随安装变化。手机重启后通常需要重新启动 Shizuku。
2. **选择启动模式。** 在 MaaFGO 的“设置 → 其他设置 → 启动模式”中选择 Shizuku；已有 Root 的设备也可选择 Root。回到首页“权限管理”点“请求权限”并授权；仅开启无障碍服务不能代替这两种控制权限。
3. **选择服务器和任务。** 在首页选择对应服务器，核对登录任务中的游戏包名，等待项目资源和服务就绪。若服务尚未连接，可在“服务入口”点“重新加载服务”。APK 已包含 OCR、助战与关卡 YOLO 模型。
4. **先单独测试登录。** 首次只勾选登录任务，按下方对应模式启动；成功后再添加仓库统计、日常战斗等任务。战斗先用少量次数，并核对苹果、助战和队伍选项。

## 后台与前台怎么选

| 模式 | 游戏画面 | 启动入口 | 分辨率 |
| --- | --- | --- | --- |
| 后台模式 | 游戏运行在独立虚拟屏，在 MaaFGO 上方预览 | 任务页“开始任务” | 固定 `1280×720 / DPI 240`，主屏可保持原比例 |
| 前台模式 | 游戏显示在手机主屏，由悬浮面板操作 | 游戏横屏稳定后，从悬浮面板“开始任务” | 主屏游戏画面须为横屏 16:9，推荐 1280 × 720 |

**超宽屏优先使用后台模式。** `2160×720` 等主屏不需要修改，后台仍以独立的 1280×720 游戏画面识别和点击。设备或系统限制虚拟屏时，再尝试前台模式。后台模式中的部分游戏 SDK 登录或授权窗口仍可能短暂出现在主屏；不能保证所有窗口都留在预览中。

### 后台模式

在首页将“运行模式”设为“后台模式”，配置任务后从任务页开始。运行时可查看顶部预览；MuMu 已验证返回桌面后继续执行。厂商省电和后台清理策略可能影响持续运行，请按应用提示设置后台权限。

应用内屏保会显示黑色界面并降低窗口亮度，任务继续在虚拟屏运行。它需要 MaaFGO 界面可见，不等同于按电源键后的系统息屏；真息屏和长时间连续运行尚未完成验收。

### 前台模式

1. 在首页切换“前台模式”。若主屏不是 16:9，先点“修改分辨率”；这会影响整个系统，用完可点“重置分辨率”。
2. 选择“悬浮球模式”，按提示授予悬浮窗权限，点“打开操作面板”。
3. **手动打开 FGO，等待游戏进入稳定的横屏 16:9 画面。**
4. 点屏幕边缘的悬浮球，从面板选择“开始任务”；需要中止时从同一面板停止。

任务页的开始按钮用于后台模式。竖屏或 `2160×720` 等原生超宽前台画面会被拦截，不能仅靠游戏两侧黑边适配。调整尺寸、旋转或切换运行模式前先停止任务；运行中检测到尺寸变化会拒绝后续输入并结束本轮，恢复横屏后重新开始。

## 战斗、助战与本地数据

### 原生战斗与礼装选择

安卓仅提供原生自动战斗，保留技能、宝具、指令卡策略与 Chaldea 参数转换；桌面版仍可使用 BBC。原生策略不保证逐张复现分享队伍中指定的面卡顺序，请先验证目标关卡。

自定义助战礼装使用标准图片文件列表，选择已有礼装和满破条件即可，不依赖修改版 MXU。列表来自 APK 内置模板；桌面上的目录刷新和大图预览未移植，添加自定义图片需要重新打包。模型已随包提供，但 ARM64 的模型加载和离线推理通过不等于所有助战条件的实屏筛选均已验收。

### 从 Chaldea 获取队伍

在战斗任务中将“是否使用 Chaldea 队伍”设为“是”，向“Chaldea 队伍导入”粘贴分享链接；独立“Chaldea 自动编队”使用同一输入方式。手机会直接下载并缓存，不需要先在电脑下载 JSON。

输入框支持分享链接、队伍 ID、关卡 ID、已有缓存文件名和手机实际可读的 JSON 文件路径。Windows 文件路径不能直接使用，Android 文件选择器给出的 `content://` 地址也不会自动转换。网络获取、缓存读取与原生计划转换已验证，完整游戏自动编队及攻略实战仍需按队伍单独测试。

### 构建个人从者礼装库

运行“构建个人从者礼装库”，选择从者、礼装或两者。结果保存在本机应用外部文件目录，供羁绊补齐等功能读取：

```text
Android/data/<应用包名>/files/pi/config/
├── Inventory/player_servants.json
├── Inventory/player_equips.json
├── Battle/          # Chaldea 队伍缓存
└── BondCompletion/  # 羁绊进度
```

Android 对 `Android/data` 的访问限制因系统而异；这些是手机数据，不会自动同步桌面。库存保存去重后的 ID，不记录持有张数，默认礼装范围不含一至三星；当前不按账号或区服隔离，切换账号或区服后应重新构建。扫描被遮挡、停止或失败时，应检查任务结果，不能把已有文件当作本次已完整扫描的证明。

“设置 → 备份与恢复 → 导出配置”只导出运行配置，导出的日志也不包含上述库存和队伍文件。备份本地数据需另行复制 `pi/config/`；请在卸载、清除数据或更换签名前确认备份可读取。

## 更新与常见问题

| 情况 | 处理方式 |
| --- | --- |
| 覆盖安装后仍是旧任务或旧资源 | 同一 Git 提交的开发构建可能使用相同 `versionCode`。停止任务后，在“设置 → 项目资源 → 重新解压资源”手动刷新；APK 版本码变化后会自动准备资源 |
| 担心库存、队伍缓存丢失 | 同签名覆盖升级及手动重解会保留运行配置、应用日志与完整 `pi/config/`；其他 PI 资源和临时记录会重新生成。重要数据仍应另行备份 |
| 提示签名冲突，无法覆盖安装 | 旧调试包与正式签名包的证书不同，通常无法直接覆盖安装；后续正式版和 beta 共用固定证书。切换前先备份本地库存、队伍缓存与配置。卸载应用或系统“清除数据”会删除应用数据 |
| Shizuku 已安装但无法连接 | 确认 Shizuku 显示运行中，且已允许 MaaFGO 使用。只有安装管理器、没有启动服务时无法控制游戏 |
| 前台有点击但位置不对，或任务因尺寸变化停止 | 先停止任务，确认游戏处于横屏 16:9，重新从悬浮面板开始；超宽屏可改用后台 |
| 任务失败需要反馈 | 从“设置 → 日志 → 导出日志”保存或分享日志，附手机型号、安卓版本、APK 架构、Shizuku/Root 和前台/后台模式 |

安卓版资源和 Agent 随 APK 更新，桌面 Mirror酱 项目 ID 不用于安卓更新。升级保留规则不包含卸载后重装，也不会自动导入桌面的个人配置。

## 已验收范围

| 环境 | 已完成 |
| --- | --- |
| MuMu 12 / Android 12 / x86_64 | Root、Shizuku 连接；日服登录和冬木 X-A 原生单场战斗；后台预览、应用内屏保、返回桌面继续运行；从者与礼装库存落盘及真实读库；Chaldea 获取与离线缓存；2160×720 逻辑主屏下的后台任务 |
| 华为 ELE-AL00 / Android 10 / ARM64，Shizuku | B 服前后台登录；稳定横屏前台开始、手动停止、旋转中止与恢复；从者库扫描和真实读库；Chaldea 手机下载、缓存及计划转换；两套 YOLO 模型的离线原生推理；覆盖更新与重解后数据保留 |

真机礼装扫描、真机战斗、完整 Chaldea 自动编队、复杂自定义助战、连续战斗、系统息屏及其他 ROM 尚需独立验收。模型探针使用全零输入，只验证运行环境；库存识别结果也未逐项核对全账号准确率。版本、原始证据和各轮边界见 [TESTING.md](TESTING.md)。

## 开发构建

本工程参考 [MaaEnd Android 接入](https://github.com/MaaEnd/MaaEnd/tree/v2/Android)，使用固定公开提交的 [MaaFwApp](https://github.com/Aliothmoon/MaaFwApp) 子模块。安卓已合入主干，Windows / MXU 继续使用原来的打包流程。

需要完整 JDK 17+（含 `javac`、`jlink`，本机实测 JDK 21）、Python 3.12+、Android SDK。设置 `JAVA_HOME`、`ANDROID_HOME`（或 `ANDROID_SDK_ROOT`）；NDK 默认从 SDK 下寻找，也可设置 `ANDROID_NDK_ROOT`。以下命令在项目根目录执行：

```powershell
git submodule update --init Android/MaaFwApp assets/MaaCommonAssets
sdkmanager --install "platforms;android-37.0" "build-tools;37.0.0" "cmake;3.22.1" "ndk;29.0.14206865"
python tools/build_android.py --check-env
# ARM64 手机
python tools/build_android.py
# MuMu 12 等 x86_64 模拟器
python tools/build_android.py --abi x86_64
```

SDK Manager 使用 command-line tools 19.0（13114758），与 CI 一致；MaaFramework、Python 内核及 NDK 版本见 [versions.json](versions.json)。MaaFramework 与 Python 内核中的 maafw 版本必须匹配。

构建自动应用 [0001–0007 外壳补丁](patches/README.md)，重复运行跳过已应用项，冲突明确报错。直接运行子模块 Gradle 前须先执行 `python tools/apply_android_patches.py`。补丁应用后子模块显示有修改是预期状态；主工程记录公开上游提交和补丁，不依赖本机私有提交。

产物为 `Android/out/MaaFGO-android-<abi>-debug.apk`，已用 Android 调试证书签名。只改任务、图片或 Python 代码且已有同架构运行时时，可用 `--skip-runtime`：

```powershell
python tools/build_android.py --skip-runtime
python tools/build_android.py --abi x86_64 --skip-runtime
```

正式签名包使用 `python tools/build_android.py --release`，需设置 `KEYSTORE_PATH`、`KEYSTORE_PASSWORD`、`KEY_ALIAS`、`KEY_PASSWORD`。tag 构建通过 GitHub Secrets 使用固定正式密钥，签名及验证说明见 [signing/README.md](signing/README.md)。安卓应用内更新源和 Mirror酱发布仍需另行配置。

## Tag 构建与发布

推送 `v*` tag 会由统一发布流程同时构建 Windows / MXU、ARM64 安卓和 x86_64 安卓。三个构建均通过后，Windows ZIP 和两个 APK 一起上传到同一个 GitHub Release；`beta` 等预发布标记与 Windows 包一致。任一架构构建或回归失败时，本次 Release 不会发布。

安卓下载文件为 `MaaFGO-android-arm64-v8a-<tag>.apk` 和 `MaaFGO-android-x86_64-<tag>.apk`，手机选 ARM64，MuMu 等 x86_64 模拟器选 x86_64。正式版和 beta tag 均使用正式签名的 Release APK，并验证签名、非调试标志、架构及版本。

与 MXU 一致，普通分支 push 不打包，相关 PR 保留 debug APK 构建；`v*` tag 通过统一发布流程构建正式签名包。手动安卓构建默认 debug，维护者可启用 `release` 输入验证正式签名构建，产物保存在 Actions 附件。已发布的旧 tag 不会自动补上 APK；该流程在修改合入后推送的新 tag 上生效，也可在包含修改的 tag 上手动运行统一发布流程。

## 从 MXU 移植时的维护要点

采用“共享任务与 Agent，打包时生成安卓资源副本”的方式维护两个平台。修改源文件或转换器，不要直接修改下次会被覆盖的安卓生成目录。

| 层面 | 适配方式 |
| --- | --- |
| 界面与选项 | 展开 `interface.json` imports；`scan_select` 转标准图片文件列表，保留完整文件名、默认项及 attach 参数；`default` 转 `default_case`；Chaldea 文件按钮退回文本输入，任务说明并入 description |
| Windows 依赖 | 安卓生成副本移除 BBC 入口、路径、DLL、预设步骤和进程监听；安卓 Agent 不导入 BBC/psutil，公共 Chaldea 解析仍保留，桌面入口和默认配置不变 |
| Python 与原生库 | APK 内置 CPython、NumPy、maafw 和 NDK 编译的 cv2，配置运行路径；不能直接复用 Windows wheel。`psutil`、`highspy` 不进入手机运行时 |
| YOLO 与 OCR | 打包 `support_det.onnx`、`quest_detect.onnx` 和 OCR 模型；安卓复用 APK 的 `libonnxruntime.so`，经 C API v17 做单输入 float32 图像的 CPU 推理；桌面继续使用官方 Python wheel |
| 控制与后台 | Shizuku/Root 采集和注入触摸，独立虚拟屏运行游戏；修复任务迁移、焦点和取消保护。B 服安卓副本直接启动 `.UnityPlayerNativeActivity`，桌面源启动动作不变 |
| 分辨率与生命周期 | 后台固定 1280×720 / DPI 240；前台横屏 16:9，尺寸改变后下一轮重建 controller/tasker；运行中尺寸变化拒绝输入并停止，先释放在途图像再关闭采集器 |
| 屏保 | 在主 Activity 的 Compose 树显示，隐藏时恢复亮度与唤醒标记；不新建可能遮挡虚拟屏的窗口 |
| 数据与文件输入 | Agent 工作目录对齐 PI 根目录；数据放 `pi/config/`，升级与手动重解保留；手机路径独立处理。库存失败明确报错，不用不完整结果覆盖已有有效数据 |
| 共享 Pipeline | 检查 DirectHit 的显式区域、转场确认、零尺寸点击和失败传播；共享修复会同时影响桌面，应保留组件回归 |
| 构建与更新 | 分别构建 ARM64/x86_64，递增 APK versionCode；安卓签名与更新源独立配置。源 interface 版本保留，桌面打包同步仍遵守原版本保护约定 |

新增 MXU 专属字段时，同时补充 `tools/prepare_android.py` 的转换、打包回归与真实 Kotlin PI/执行计划检查；新增外壳定制更新补丁。逐项兼容行为和主干合并时的桌面核验见 [COMPATIBILITY.md](COMPATIBILITY.md)。

### 目录与依赖

| 位置 | 内容 |
| --- | --- |
| `MaaFwApp/` | 固定提交的安卓外壳，包含 Shizuku / Root、虚拟屏、任务配置 UI |
| `profile.yaml` | 资源、Python 启动参数、图标、应用名和分辨率 |
| `assets/` | 从根目录 `assets/` 生成的安卓副本，忽略提交 |
| `agent-dist/<abi>/bundle/` | CPython、NumPy、maafw、交叉编译的 cv2 |
| `patches/` | 可重放的 MaaFwApp 定制补丁 |
| `.cache/`、`sdk/` | 本地下载、编译缓存和可选 SDK，忽略提交 |
| `out/` | APK、构建审计与资源 SHA-256 清单，忽略提交 |

Python 使用 [MaaAgentCoreAndroid](https://github.com/Aliothmoon/MaaAgentCoreAndroid) 预构建内核。OpenCV 采用官方 4.12.0 源码和 Chaquopy CPython 3.13 目标头文件，由 NDK 编译 `core/imgproc/imgcodecs` 与 Python 绑定，静态链接 OpenCV 并保留第三方许可；构建脚本处理 Android Python 绑定与 NDK r29 链接参数兼容。

### 开发验证

```powershell
python -m pip install numpy opencv-python-headless onnxruntime
python -m unittest discover -s tests -p "test_android*.py" -v
python tools/check_android_interface.py
python tools/run_tests.py --suite all
```

构建脚本检查 APK 中的 MaaFramework、AgentServer、ONNX、C++ 动态库、Python 入口与 cv2，生成资源 SHA-256 清单。静态检查不代替实机：按授权连接 → 登录 → 低成本任务 → 停止 → 本地数据 → 单场战斗 → 复杂编队逐步验收，Shizuku、Root、前台和后台分别记录结果。
