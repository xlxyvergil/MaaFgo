# MaaFGO 的 MaaFwApp 外壳迁移记录

2026-10-22，原有 8 个补丁从上游基线 `bc55dac4c8231ab73e7ee42bd38c43e322838b14` 逐项重放并提交到 [dingi8528/MaaFwApp](https://github.com/dingi8528/MaaFwApp/tree/codex/maafgo) 的 `codex/maafgo` 分支。迁移末端提交为 `f37da16f529c97231e8c9e155a363cb990c28cdb`。重放结果与迁移前本地外壳的 Git 文件树完全一致，包含原先未跟踪的新增源码和测试。

主工程现直接固定 fork 中的提交。构建不再应用补丁，原 `.patch` 文件、应用器及其专用测试已移除，历史版本仍可通过 Git 查阅。今后外壳修改在 fork 中正常提交、测试并推送，再更新主工程子模块指针；构建和测试不应留下外壳源码改动。具体维护方式见 [开发构建](../README.md#开发构建)。

| 原补丁 | fork 提交 |
| --- | --- |
| 0001-preserve-user-config-on-reinstall.patch | [799e4bb：重解保留用户数据](https://github.com/dingi8528/MaaFwApp/commit/799e4bbf0e9592ba34ffe52cefef2c980932b5f6) |
| 0002-bind-screen-saver-to-default-display.patch | [22356da：主屏屏保](https://github.com/dingi8528/MaaFwApp/commit/22356daf62b9419d0af68a39118892d436341383) |
| 0003-skip-unconfigured-startup-update.patch | [57a7e24：跳过未配置的启动更新](https://github.com/dingi8528/MaaFwApp/commit/57a7e24b62146214a99780218d9c415141c1d8ae) |
| 0004-refresh-controller-for-capture-size.patch | [02e26eb：采集尺寸与控制器](https://github.com/dingi8528/MaaFwApp/commit/02e26eb7d5c96586a437b75afb4600889d758ea6) |
| 0005-fix-legacy-background-task-placement.patch | [dc3f926：后台任务迁移](https://github.com/dingi8528/MaaFwApp/commit/dc3f926786519c559bab15917a5c8c66301aec0e) |
| 0006-drain-capture-images-before-reader-close.patch | [714dc65：采集图像释放](https://github.com/dingi8528/MaaFwApp/commit/714dc65a44b1ca9b5f5c3f97e119acc2b89044ba) |
| 0007-guard-foreground-orientation-and-input.patch | [50436e8：前台方向与输入保护](https://github.com/dingi8528/MaaFwApp/commit/50436e81aceed31f3784b657b0c749d8bb7fe4b0) |
| 0008-pad-android-taps-by-fps.patch | [f37da16：低帧率点击补时](https://github.com/dingi8528/MaaFwApp/commit/f37da16f529c97231e8c9e155a363cb990c28cdb) |

以下保留各项修复的行为和历史验收边界，文中的补丁名称用于对应上表，已不代表当前构建步骤。

`0001-preserve-user-config-on-reinstall.patch` 修复手动“重新解压资源”清空本地数据的问题。它保留完整的 `pi/config/`，包括 `Inventory`、`Battle`、`BondCompletion`，其余 PI 资源和临时记录仍重新生成。`config` 不被遍历或搬运；清理其他条目时不跟随符号链接。旧版本的 `pi.roots` 即使误列了 `config`，升级和失败重试也会保留它。APK 内禁止带 `config` 根条目，避免今后误改配方覆盖用户库存；当前 `profile.yaml` 本就不打包该目录。

该补丁保留原安装器的提交标记和归档校验：只有完整解包成功才写版本标记，失败仍可重试。卸载应用、系统“清除数据”仍由 Android 删除全部应用数据，保留规则仅适用于资源更新/重解。

`0002-bind-screen-saver-to-default-display.patch` 在 AppRoot 现有主屏 Compose 树中显示屏保，不新建 Window 或 ViewRoot，避免 ROM 的悬浮窗重定向和重复开窗不绘制问题。手动与自动入口共用弱引用宿主和显示状态；仅可见主屏 Activity 可显示，隐藏/解绑/换宿主时恢复原亮度与 KEEP_SCREEN_ON 位。屏保期间主界面视觉内容及 Dialog 暂停组合，可保存状态和文件选择回调保持；虚拟屏 native 截图与任务继续运行。

`0003-skip-unconfigured-startup-update.patch` 在当前所选更新源缺配置时跳过启动自检，避免调试包每次启动弹错；不切换更新源，不更改用户设置。手动检查仍显示缺少配置的具体原因。

`0004-refresh-controller-for-capture-size.patch` 将 native controller 的缓存键扩展为实际 `displayId`、`width`、`height` 和 `forceStop`。每轮准备时只读取一次采集目标，比例检查、缓存比较与 `screen_resolution` 配置共用该快照。前台取主屏采集器的真实尺寸，后台取虚拟屏的真实配置，忽略可能过期的 payload 尺寸。同一显示器更换分辨率后重新创建 controller 和 tasker，先把存活 Agent 的 sink 登记到新对象，再销毁旧指针；连接失败时保留原对象，释放 controller 时清空缓存键。

该补丁同时在实际 `prepare` 入口检查前台画面是否为 16:9。主屏为 `2160×720` 等不支持比例时，在加载资源、Agent 或创建 native controller 前明确失败，并提示切换后台模式或调整主屏分辨率；不自动修改系统显示设置。后台仍按其虚拟屏尺寸运行，不受主屏宽高影响。这是任务准备阶段的保护，不代表已支持任务执行中的任意旋转或分辨率切换。

`0005-fix-legacy-background-task-placement.patch` 修复后台游戏漂移到主屏时的任务迁移目标。Android 10/11 的 `moveStackToDisplay` 和 `am display move-stack` 使用真实 `stackId`，不再误传 `taskId`；API 31+ 仅在确认任务没有父任务时将其 `taskId` 作为 root task ID，无法确认或存在嵌套关系时交给启动 Intent 兜底，避免误移其他任务。

该补丁还在提交后台任务前准备 watchdog 状态，并在 `StartApp` 启动游戏前登记目标包与虚拟屏，避免游戏在首次轮询前跳回主屏而漏掉跟踪。登记只接受当前后台运行的目标屏，启动失败或停止时清理跟踪；任务结束后的关闭游戏使用独立保留的清理目标。底层 `startActivity` 遇到反射或 Binder 调用异常时返回失败值，允许上层尝试 `am start --display`，不再将异常当作启动成功。

华为 ELE-AL00 / Android 10 ARM64 的 Shizuku 实测确认：修正 `stackId` 后迁回虚拟屏可生效，但 B 服经 `.EmptyClass` 启动壳跳到主屏后，迁回时的 DPI 408→240 变化仍可能触发游戏 `onDestroy` 及自行 SIG9 退出，不能只靠 watchdog 拉回。Android 资源转换器另将 B 服启动动作改为直接进入已导出的 `.UnityPlayerNativeActivity`；配合补丁的独立验收包完成登录 1 分 19 秒，Unity 在 83.69 秒的 104 次采样中均位于虚拟屏。SDK 的 AutoLogin / Welcome 活动仍曾在主屏创建独立任务，此结果不代表全部 SDK 窗口或真机战斗均已通过。详见 [TESTING.md](../TESTING.md)。

本轮 Kotlin 目标回归 30 项通过；与 CI 一致的完整外壳检查共 95 项，无失败，Windows 上两项符号链接测试因权限跳过。取消保护使用单调轮次与目标 token，慢查询和迁移在 watchdog 锁外执行；停止即令 token 失效，旧回调无法清理新轮目标。真机在启动稳定校验中停止，502ms 后明确返回取消，任务为 CANCELLED，未继续移栈或重新启动。0001–0005 在干净公开上游顺序重放成功，25 个修改文件与工作区一致。

`0006-drain-capture-images-before-reader-close.patch` 修复前台回桌面旋转时 ImageReader 仍持有图像导致的原生 SIGABRT。采集生命周期串行化，回调以 generation 拒绝过期 reader；关闭 reader 前先等待在途回调、预览队列和渲染线程持有的 AImage 释放，NDK 关闭时不持回调锁或渲染锁，避免等待 callback looper 时死锁。主屏采集 start/stop/restart 和显示变化处理共用同步入口。华为 Android 10 实测连续 8 轮横竖切换服务保持存活，每次重建新尺寸采集器；NDK 仍可能打印已排队的关闭通知警告，本轮未再出现旧的 parent ImageReader 释放 Fatal。

`0007-guard-foreground-orientation-and-input.patch` 要求前台 Start 已处于横屏 16:9，并在实际 Runner 准备入口再次检查；首页改分辨率和打开面板仍可在竖屏完成。运行期间在主屏输入前检查实际采集尺寸，尺寸变化或采集不可用时拒绝继续输入，以独立停止执行器和运行 generation 请求结束本轮，反馈明确的尺寸变化失败；恢复横屏后可重新开始。取消主屏触摸使用 ACTION_CANCEL，按键使用取消标记，按 display 隔离触点，不向新出现的应用补发点击，也不清除后台虚拟屏的手势。该保护不将整个系统旋转或折叠变化自动适配为可继续运行。

`0008-pad-android-taps-by-fps.patch` 的历史策略是在安卓自动触摸的共用抬起入口调整最短按住时长：有效帧率不超过 30 FPS 时，目标为 `min(300, ceil(3000 / FPS))` 毫秒；更高或未知帧率不补时。2026-11-16 调整为所有帧率保底 200 毫秒，有效帧率按 `max(200, min(500, ceil(3000 / FPS)))` 毫秒计算，未知、0、负数、NaN 和无穷读数均使用 200 毫秒；移除 30 FPS 补时开关，避免阈值抖动退回短按。500 毫秒是自动补时的目标上限，不截短显式配置的更长按压。只补足已经按住时间的差额，不缩短原有长按、滑动或战斗按钮的 500 毫秒配置。前台启动采集时也开启帧率监测，可接手没有经过 StartApp 的游戏；成功启动前台游戏后按游戏包重新选择帧率来源。前台、后台以及 Root/Shizuku 使用相同触摸策略。旧系统使用采集合成帧率作为近似值，不能将其等同于游戏输入采样率；不同游戏仍需分别验证参数。

验证主工程安卓转换与外壳 Kotlin 回归：

```powershell
python -m unittest discover -s tests -p "test_android*.py" -v
Android/MaaFwApp/gradlew.bat -p Android/MaaFwApp :app:testDebugUnitTest --tests "com.aliothmoon.maafw.project.PiInstaller*" --tests "com.aliothmoon.maafw.remote.MaaRunnerControllerTargetTest"
```

Kotlin 测试使用内存/ZIP 两种包，实际写入和重解文件，检查库存/队伍/羁绊数据的字节内容、资源刷新、失败重试、错误根清单和符号链接边界。Windows 主机若未启用创建符号链接权限，仅两项链接测试按 JUnit 假设跳过；其余保留测试仍实际执行。

`MaaRunnerControllerTargetTest` 直接调用真实 Runner 的准备与释放方法，显示服务和 native 接口使用替身。8 项用例覆盖同屏换尺寸、对象复用与断连重建、快照只读一次、替换连接失败、销毁顺序、宽主屏下后台仍使用虚拟屏配置、前台非 16:9 提前失败，以及采集未启动时不使用兜底尺寸；不以这些用例替代模拟器或真机运行。
