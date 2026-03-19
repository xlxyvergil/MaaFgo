#   This file is part of MFW-ChainFlow Assistant.

#   MFW-ChainFlow Assistant is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published
#   by the Free Software Foundation, either version 3 of the License,
#   or (at your option) any later version.

#   MFW-ChainFlow Assistant is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty
#   of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See
#   the GNU General Public License for more details.

#   You should have received a copy of the GNU General Public License
#   along with MFW-ChainFlow Assistant. If not, see <https://www.gnu.org/licenses/>.

#   Contact: err.overflow@gmail.com
#   Copyright (C) 2024-2025  MFW-ChainFlow Assistant. All rights reserved.


"""
MFW-ChainFlow Assistant
MFW-ChainFlow Assistant 信号总线
作者:overflow65537
"""
from PySide6.QtCore import Signal, QObject


class SignalBus(QObject):
    """Signal bus"""

    micaEnableChanged = Signal(bool)  # Mica效果开关信号

    # 主布局中的配置切换和选项切换
    change_task_flow = Signal(dict)  # 切换任务列表
    show_option = Signal(dict)  # 显示选项
    agent_info = Signal(dict)  # 智能体信息
    title_changed = Signal()  # 窗口标题改变
    # maa sink 发送信号
    callback = Signal(dict)

    # 输出到日志组件
    log_output = Signal(str, str)  # (level,text)
    background_image_changed = Signal(str)
    background_opacity_changed = Signal(int)

    # 显示 InfoBar 的请求
    info_bar_requested = Signal(str, str)  # (level, message)

    # focus display 渠道信号
    focus_toast = Signal(str)  # 应用内轻提示（message）
    focus_notification = Signal(str)  # 系统级通知（message）
    focus_dialog = Signal(str)  # 非阻塞式对话框（message）
    focus_modal = Signal(str)  # 阻塞式弹窗（message）
    log_zip_started = Signal()  # 日志打包开始
    log_zip_finished = Signal()  # 日志打包结束

    config_changed = Signal(str)  # (config_id)
    log_clear_requested = Signal()

    # 外部通知发送完成
    notice_finished = Signal(int, str)  # (result, send_func.__name__)

    # 由信息输出组件发射，外部模块处理
    request_log_zip = Signal()  # 请求生成日志压缩包

    # 下载相关进度
    start_update = Signal()  # 开始更新
    update_progress = Signal(int, int)  # 下载进度条(downloaded, total)
    update_stopped = Signal(
        int
    )  # 更新停止(0:手动停止, 1:热更新完成, 2:更新包下载完成,需要重启安装)

    hotkey_shortcuts_changed = Signal()

    # 服务协调器重新初始化
    fs_reinit_requested = Signal()  # 热更新完成后请求服务协调器重新初始化

    # 多资源适配启用后通知主界面等组件初始化相关 UI
    multi_resource_adaptation_enabled = Signal()

    # 更新相关信号
    check_auto_run_after_update_cancel = Signal()  # 更新取消后检查是否需要自动运行
    all_updates_completed = Signal()  # 所有更新（设置更新 + bundle更新）完成

    # 任务状态信号
    task_status_changed = Signal(str, str)  # (task_id, status) status: "running", "completed", "failed", "restart_success", "waiting"

    # 任务流结束信号：无论正常结束/异常/手动停止/中止，都会在任务流退出时发射
    # payload: dict（包含原因/标志位等，字段可扩展）
    task_flow_finished = Signal(dict)


signalBus = SignalBus()
