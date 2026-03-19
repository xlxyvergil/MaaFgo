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
MFW-ChainFlow Assistant 配置
作者:overflow65537
"""


import sys
from pathlib import Path
from enum import Enum

from PySide6.QtCore import QLocale
from qfluentwidgets import (
    qconfig,
    QConfig,
    ConfigItem,
    OptionsConfigItem,
    BoolValidator,
    OptionsValidator,
    RangeConfigItem,
    RangeValidator,
    Theme,
    ConfigSerializer,
)

from app.common.__version__ import __version__


def _detect_auto_update_default() -> bool:
    """
    根据版本号决定自动更新默认值：
    - 若版本包含 ci/alpha/beta（不区分大小写），则默认关闭自动更新
    - 否则默认开启
    """
    version = (__version__ or "").lower()
    for keyword in ("ci", "alpha", "beta"):
        if keyword in version:
            return False
    return True


_AUTO_UPDATE_DEFAULT = _detect_auto_update_default()


class Language(Enum):
    """Language enumeration mapped to QLocale."""

    CHINESE_SIMPLIFIED = QLocale(QLocale.Language.Chinese, QLocale.Country.China)
    CHINESE_TRADITIONAL = QLocale(QLocale.Language.Chinese, QLocale.Country.HongKong)
    ENGLISH = QLocale(QLocale.Language.English)


def isWin11():
    return sys.platform == "win32" and sys.getwindowsversion().build >= 22000


def _detect_default_background_image() -> str:
    """查找默认背景图，依次尝试 ./background.jpg 与 ./background.png。"""
    for name in ("background.jpg", "background.png"):
        candidate = Path(name)
        if candidate.is_file():
            return str(candidate)
    return ""


class Config(QConfig):
    """Application configuration container."""

    class LanguageSerializer(ConfigSerializer):
        """序列化 Language 枚举，方便写入/读取 settting."""

        def serialize(self, value):
            return value.name

        def deserialize(self, value: str) -> Language:
            if isinstance(value, str):
                try:
                    return Language[value]
                except KeyError:
                    for lang in Language:
                        # 兼容旧版用 QLocale.name() 保存的值
                        if lang.value.name() == value or lang.name == value:
                            return lang
            return Language.CHINESE_SIMPLIFIED

    class UpdateChannel(Enum):
        """Update channel options."""

        ALPHA = 0
        BETA = 1
        STABLE = 2

    _update_channel_validator = OptionsValidator([item.value for item in UpdateChannel])

    # ===== 通用设置 =====
    proxy = ConfigItem("General", "proxy", 0)
    http_proxy = ConfigItem("General", "http_proxy", "")
    socks5_proxy = ConfigItem("General", "socks5_proxy", "")

    Mcdk = ConfigItem("General", "cdk", "")

    run_after_startup = ConfigItem(
        "General", "run_after_startup", False, BoolValidator()
    )
    auto_minimize_on_startup = ConfigItem(
        "General", "auto_minimize_on_startup", False, BoolValidator()
    )
    minimize_to_tray_on_minimize_windows = ConfigItem(
        "General", "minimize_to_tray_on_minimize_windows", False, BoolValidator()
    )
    run_after_startup_arg = ConfigItem(
        "General", "run_after_startup_arg", False, BoolValidator()
    )
    multi_resource_adaptation = ConfigItem(
        "Compatibility", "multi_resource_adaptation", False, BoolValidator()
    )
    save_screenshot = ConfigItem(
        "Compatibility", "save_screenshot", False, BoolValidator()
    )
    special_task_tutorial_shown = ConfigItem(
        "General", "special_task_tutorial_shown", False, BoolValidator()
    )

    announcement = ConfigItem("General", "announcement", "")

    # ===== 运行时标记 =====
    # 用于在 UI/逻辑层快速判断当前进程是否处于管理员权限（会在启动时刷新）
    is_admin = ConfigItem("Runtime", "is_admin", False, BoolValidator())

    auto_update = ConfigItem(
        "Update", "auto_update", _AUTO_UPDATE_DEFAULT, BoolValidator()
    )
    bundle_auto_update = ConfigItem(
        "Bundle", "bundle_auto_update", False, BoolValidator()
    )
    force_github = ConfigItem("Update", "force_github", False, BoolValidator())
    github_api_key = ConfigItem("Update", "github_api_key", "")

    resource_update_channel = OptionsConfigItem(
        "Update",
        "resource_update_channel",
        UpdateChannel.STABLE.value,
        _update_channel_validator,
    )

    # ===== 任务设置 =====
    low_power_monitoring_mode = ConfigItem(
        "Task", "low_power_monitoring_mode", True, BoolValidator()
    )  # 低功耗监控模式：使用缓存的图像而不是专用监控线程

    # ===== 日志设置 =====
    log_zip_include_images = ConfigItem(
        "Log", "log_zip_include_images", False, BoolValidator()
    )  # 是否在日志压缩包中包含图片（默认关闭）
    log_max_images = RangeConfigItem(
        "Log", "log_max_images", 25, RangeValidator(1, 10000)
    )  # 日志中保存的最大图片数量（默认25张，按200KB/张计算），同时控制界面显示和压缩包保存的数量

    # ===== 通知 =====
    Notice_DingTalk_status = ConfigItem("Notice", "DingTalk_status", False)
    Notice_DingTalk_url = ConfigItem("Notice", "DingTalk_url", "")
    Notice_DingTalk_secret = ConfigItem("Notice", "DingTalk_secret", "")

    Notice_Lark_status = ConfigItem("Notice", "Lark_status", False)
    Notice_Lark_url = ConfigItem("Notice", "Lark_url", "")
    Notice_Lark_secret = ConfigItem("Notice", "Lark_secret", "")

    Notice_Qmsg_status = ConfigItem("Notice", "Qmsg_status", False)
    Notice_Qmsg_sever = ConfigItem("Notice", "Qmsg_sever", "")
    Notice_Qmsg_key = ConfigItem("Notice", "Qmsg_key", "")
    Notice_Qmsg_user_qq = ConfigItem("Notice", "Qmsg_uesr_qq", "")
    Notice_Qmsg_robot_qq = ConfigItem("Notice", "Qmsg_robot_qq", "")

    Notice_SMTP_status = ConfigItem("Notice", "SMTP_status", False)
    Notice_SMTP_sever_address = ConfigItem("Notice", "SMTP_sever_address", "")
    Notice_SMTP_sever_port = ConfigItem("Notice", "SMTP_sever_port", "25")
    Notice_SMTP_used_ssl = ConfigItem("Notice", "SMTP_used_ssl", False)
    Notice_SMTP_user_name = ConfigItem("Notice", "SMTP_uesr_name", "")
    Notice_SMTP_password = ConfigItem("Notice", "SMTP_password", "")
    Notice_SMTP_send_mail = ConfigItem("Notice", "SMTP_send_mail", "")
    Notice_SMTP_receive_mail = ConfigItem("Notice", "SMTP_receive_mail", "")

    Notice_WxPusher_status = ConfigItem("Notice", "WxPush_status", False)
    Notice_WxPusher_SPT_token = ConfigItem("Notice", "WxPusher_SPT_token", "")

    Notice_QYWX_status = ConfigItem("Notice", "QYWX_status", False)
    Notice_QYWX_key = ConfigItem("Notice", "QYWX_key", "")

    Notice_Gotify_status = ConfigItem("Notice", "Gotify_status", False)
    Notice_Gotify_url = ConfigItem("Notice", "Gotify_url", "")
    Notice_Gotify_token = ConfigItem("Notice", "Gotify_token", "")
    Notice_Gotify_priority = ConfigItem("Notice", "Gotify_priority", "0")

    when_start_up = ConfigItem("Notice", "when_start_up", False)
    # 通知时机配置，分别控制不同场景下的通知发送
    when_flow_started = ConfigItem("Notice", "when_flow_started", False)  # 任务流启动时
    when_connect_success = ConfigItem(
        "Notice", "when_connect_success", False
    )  # 连接成功时
    when_connect_failed = ConfigItem(
        "Notice", "when_connect_failed", True
    )  # 连接失败时
    when_task_success = ConfigItem("Notice", "when_task_success", False)  # 任务成功时
    when_task_failed = ConfigItem("Notice", "when_task_failed", True)  # 任务失败时
    when_post_task = ConfigItem("Notice", "when_post_task", True)  # 任务流完成时
    when_task_timeout = ConfigItem("Notice", "when_task_timeout", True)  # 任务超时
    when_task_finished = ConfigItem("Notice", "when_task_finished", False)  # 保留兼容性
    # 外部通知发送格式：plain=纯文本，html=HTML（如邮件正文）
    notice_send_format = OptionsConfigItem(
        "Notice", "notice_send_format", "plain", OptionsValidator(["plain", "html"])
    )
    # 是否随通知发送截图（任务流发送通知时若控制器可用则附带当前截图）
    notice_send_screenshot = ConfigItem(
        "Notice", "notice_send_screenshot", False, BoolValidator()
    )

    # ===== 主窗口 =====
    micaEnabled = ConfigItem("MainWindow", "MicaEnabled", isWin11(), BoolValidator())

    dpiScale = OptionsConfigItem(
        "MainWindow",
        "DpiScale",
        "Auto",
        OptionsValidator([1, 1.25, 1.5, 1.75, 2, "Auto"]),
        restart=True,
    )

    language = OptionsConfigItem(
        "MainWindow",
        "Language",
        Language.CHINESE_SIMPLIFIED,  # 默认值（后续会被自动检测覆盖）
        OptionsValidator(Language),
        LanguageSerializer(),
        restart=True,
    )

    language_auto_detected = ConfigItem(
        "MainWindow", "LanguageAutoDetected", False, BoolValidator()
    )

    remember_window_geometry = ConfigItem(
        "MainWindow", "remember_window_geometry", False, BoolValidator()
    )
    last_window_geometry = ConfigItem("MainWindow", "LastWindowGeometry", "")

    start_task_shortcut = ConfigItem("Shortcuts", "start_task_shortcut", "Ctrl+F1")
    stop_task_shortcut = ConfigItem("Shortcuts", "stop_task_shortcut", "Alt+F1")

    show_advanced_startup_options = ConfigItem(
        "Personalization",
        "show_advanced_startup_options",
        False,
        BoolValidator(),
    )

    # ===== 背景 =====
    _default_background = _detect_default_background_image()
    background_image_path = ConfigItem(
        "Personalization", "background_image_path", _default_background
    )
    background_image_opacity = RangeConfigItem(
        "Personalization", "background_image_opacity", 10, RangeValidator(0, 100)
    )

    # ===== 材质 & 通用界面 =====
    blurRadius = RangeConfigItem(
        "Material", "AcrylicBlurRadius", 15, RangeValidator(0, 40)
    )

    # ===== 软件更新 =====
    latest_update_version = ConfigItem("Update", "LatestUpdateVersion", "")
    cdk_expired_time = ConfigItem("Update", "CdkExpiredTime", -1)

    # dev
    enable_test_interface_page = ConfigItem(
        "Dev", "enable_test_interface_page", False, BoolValidator()
    )


cfg = Config()
cfg.themeMode.value = Theme.AUTO
qconfig.load("config/config.json", cfg)


def detect_system_language() -> Language:
    """检测系统语言并返回对应的 Language 枚举

    Returns:
        Language: 根据系统语言返回对应枚举，默认简体中文
    """
    system_locale = QLocale.system()
    language = system_locale.language()
    country = system_locale.country()

    # 中文判断
    if language == QLocale.Language.Chinese:
        # 繁体
        if country in (QLocale.Country.HongKong,):
            return Language.CHINESE_TRADITIONAL
        # 简体
        return Language.CHINESE_SIMPLIFIED

    # 其他语言默认英文
    else:
        return Language.ENGLISH


def init_language_on_first_run():
    """初次运行时自动检测并设置系统语言

    仅在未设置过语言时执行（通过 language_auto_detected 标记判断）
    """
    if not cfg.get(cfg.language_auto_detected):
        detected_lang = detect_system_language()
        cfg.set(cfg.language, detected_lang)
        cfg.set(cfg.language_auto_detected, True)
        from app.utils.logger import logger

        logger.info(f"首次启动，自动检测系统语言: {detected_lang}")
    else:
        from app.utils.logger import logger

        logger.debug("已设置语言偏好，跳过自动检测")
