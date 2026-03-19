from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout
from cryptography.fernet import InvalidToken

from qfluentwidgets import (
    MessageBoxBase,
    PushButton,
    BodyLabel,
    LineEdit,
    PasswordLineEdit,
    SwitchButton,
    CheckBox,
    SubtitleLabel,
)

from app.utils.logger import logger
from app.common.config import cfg
from app.utils.crypto import crypto_manager
from app.utils.notice import send_thread
from app.common.signal_bus import signalBus


class BaseNoticeType(MessageBoxBase):
    """通知类型配置对话框的基类，包含公共方法"""
    
    def __init__(self, parent=None, notice_type: str = ""):
        super().__init__(parent)
        self.notice_type = notice_type
        self.testButton = PushButton(self.tr("Test"), self)
        self.testButton.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
        self.buttonLayout.insertWidget(1, self.testButton)
        self.buttonLayout.setStretch(0, 1)
        self.buttonLayout.setStretch(1, 1)
        self.buttonLayout.setStretch(2, 1)
        self.widget.setMinimumWidth(350)
        self.widget.setMinimumHeight(100)

        self.yesButton.clicked.connect(self.on_yes)
        self.testButton.clicked.connect(self.on_test)
        self.cancelButton.clicked.connect(self.on_cancel)
        signalBus.notice_finished.connect(self.notice_send_finished)

    def on_test(self):
        test_msg = {"title": "Test Title", "text": "Test Text"}
        try:
            send_thread.add_task(self.notice_type.lower(), test_msg, True)
            self.testButton.setEnabled(False)
        except Exception as e:
            logger.error(f"不支持的通知类型: {self.notice_type}")
            raise Exception(f"不支持的通知类型: {self.notice_type}")

    def notice_send_finished(self):
        self.testButton.setEnabled(True)

    def encrypt_key(self, secret: str) -> str:
        """加密密钥（返回可写入配置的 utf-8 字符串）。"""
        secret = str(secret)
        if not secret:
            return ""

        encrypted = crypto_manager.encrypt_payload(secret)
        if isinstance(encrypted, (bytes, bytearray, memoryview)):
            return bytes(encrypted).decode("utf-8")
        return str(encrypted)

    def decode_key(self, key_name) -> str:
        """解密密钥"""
        mapping = {
            "dingtalk": cfg.Notice_DingTalk_secret,
            "lark": cfg.Notice_Lark_secret,
            "smtp": cfg.Notice_SMTP_password,
            "wxpusher": cfg.Notice_WxPusher_SPT_token,
            "qmsg": cfg.Notice_Qmsg_key,
            "QYWX": cfg.Notice_QYWX_key,
            "gotify": cfg.Notice_Gotify_token,
        }
        cfg_key = mapping.get(key_name)
        if cfg_key is None:
            logger.warning("未找到密钥配置: %s", key_name)
            return ""

        encrypted_value = cfg.get(cfg_key)
        if not encrypted_value:
            return ""

        try:
            decrypted = crypto_manager.decrypt_payload(encrypted_value)
            return decrypted.decode("utf-8")
        except InvalidToken:
            logger.exception("密钥解密失败: %s", key_name)
            signalBus.info_bar_requested.emit(
                "warning",
                self.tr("decrypt notice key failed: {}，please fill in again and save.").format(key_name),
            )
        except Exception:
            logger.exception("解析密钥时发生错误: %s", key_name)
            signalBus.info_bar_requested.emit(
                "warning",
                self.tr("parse notice key error: {}，please save again.").format(key_name),
            )
        return ""

    def on_yes(self):
        self.save_fields()
        logger.info(f"保存{self.notice_type}设置")
        self.accept()

    def on_cancel(self):
        logger.info("关闭通知设置对话框")
        self.close()

    def save_fields(self):
        """子类需要实现此方法来保存字段"""
        raise NotImplementedError("子类必须实现 save_fields 方法")


class DingTalkNoticeType(BaseNoticeType):
    """钉钉通知配置对话框"""
    
    def __init__(self, parent=None):
        super().__init__(parent, "DingTalk")
        self.add_fields()

    def add_fields(self):
        """添加钉钉相关的输入框"""
        dingtalk_url_title = BodyLabel(self)
        dingtalk_secret_title = BodyLabel(self)
        dingtalk_status_title = BodyLabel(self)
        self.dingtalk_url_input = LineEdit(self)
        self.dingtalk_secret_input = PasswordLineEdit(self)
        self.dingtalk_status_switch = SwitchButton(self)

        dingtalk_url_title.setText(self.tr("DingTalk Webhook URL:"))
        dingtalk_secret_title.setText(self.tr("DingTalk Secret:"))
        dingtalk_status_title.setText(self.tr("DingTalk Status:"))

        self.dingtalk_url_input.setText(cfg.get(cfg.Notice_DingTalk_url))
        self.dingtalk_secret_input.setText(self.decode_key("dingtalk"))
        self.dingtalk_status_switch.setChecked(cfg.get(cfg.Notice_DingTalk_status))

        col1 = QVBoxLayout()
        col2 = QVBoxLayout()

        col1.addWidget(dingtalk_url_title)
        col1.addWidget(dingtalk_secret_title)
        col1.addWidget(dingtalk_status_title)

        col2.addWidget(self.dingtalk_url_input)
        col2.addWidget(self.dingtalk_secret_input)
        col2.addWidget(self.dingtalk_status_switch)

        mainLayout = QHBoxLayout()
        mainLayout.addLayout(col1)
        mainLayout.addLayout(col2)
        self.viewLayout.addLayout(mainLayout)
        self.dingtalk_url_input.textChanged.connect(self.save_fields)
        self.dingtalk_secret_input.textChanged.connect(self.save_fields)

    def save_fields(self):
        """保存钉钉相关的输入框"""
        cfg.set(cfg.Notice_DingTalk_url, self.dingtalk_url_input.text())
        cfg.set(
            cfg.Notice_DingTalk_secret,
            self.encrypt_key(self.dingtalk_secret_input.text()),
        )
        cfg.set(cfg.Notice_DingTalk_status, self.dingtalk_status_switch.isChecked())


class LarkNoticeType(BaseNoticeType):
    """飞书通知配置对话框"""
    
    def __init__(self, parent=None):
        super().__init__(parent, "Lark")
        self.add_fields()

    def add_fields(self):
        """添加飞书相关的输入框"""
        lark_url_title = BodyLabel(self)
        lark_secret_title = BodyLabel(self)
        lark_status_title = BodyLabel(self)
        self.lark_url_input = LineEdit(self)
        self.lark_secret_input = PasswordLineEdit(self)
        self.lark_status_switch = SwitchButton(self)

        lark_url_title.setText(self.tr("Lark Webhook URL:"))
        lark_secret_title.setText(self.tr("Lark App Key:"))
        lark_status_title.setText(self.tr("Lark Status:"))

        self.lark_url_input.setText(cfg.get(cfg.Notice_Lark_url))
        self.lark_secret_input.setText(self.decode_key("lark"))
        self.lark_status_switch.setChecked(cfg.get(cfg.Notice_Lark_status))

        col1 = QVBoxLayout()
        col2 = QVBoxLayout()

        col1.addWidget(lark_url_title)
        col1.addWidget(lark_secret_title)
        col1.addWidget(lark_status_title)

        col2.addWidget(self.lark_url_input)
        col2.addWidget(self.lark_secret_input)
        col2.addWidget(self.lark_status_switch)

        mainLayout = QHBoxLayout()
        mainLayout.addLayout(col1)
        mainLayout.addLayout(col2)
        self.viewLayout.addLayout(mainLayout)

        self.lark_url_input.textChanged.connect(self.save_fields)
        self.lark_secret_input.textChanged.connect(self.save_fields)

    def save_fields(self):
        """保存飞书相关的输入框"""
        cfg.set(cfg.Notice_Lark_url, self.lark_url_input.text())
        cfg.set(cfg.Notice_Lark_secret, self.encrypt_key(self.lark_secret_input.text()))
        cfg.set(cfg.Notice_Lark_status, self.lark_status_switch.isChecked())


class QmsgNoticeType(BaseNoticeType):
    """Qmsg 通知配置对话框"""
    
    def __init__(self, parent=None):
        super().__init__(parent, "Qmsg")
        self.add_fields()

    def add_fields(self):
        """添加 Qmsg 相关的输入框"""
        sever_title = BodyLabel(self)
        key_title = BodyLabel(self)
        user_qq_title = BodyLabel(self)
        robot_qq_title = BodyLabel(self)
        qmsg_status_title = BodyLabel(self)

        self.sever_input = LineEdit(self)
        self.key_input = PasswordLineEdit(self)
        self.user_qq_input = LineEdit(self)
        self.robot_qq_input = LineEdit(self)
        self.qmsg_status_switch = SwitchButton(self)

        sever_title.setText(self.tr("Server:"))
        key_title.setText(self.tr("Key:"))
        user_qq_title.setText(self.tr("User QQ:"))
        robot_qq_title.setText(self.tr("Robot QQ:"))
        qmsg_status_title.setText(self.tr("Qmsg Status:"))

        self.sever_input.setText(cfg.get(cfg.Notice_Qmsg_sever))
        self.key_input.setText(self.decode_key("qmsg"))
        self.user_qq_input.setText(cfg.get(cfg.Notice_Qmsg_user_qq))
        self.robot_qq_input.setText(cfg.get(cfg.Notice_Qmsg_robot_qq))
        self.qmsg_status_switch.setChecked(cfg.get(cfg.Notice_Qmsg_status))

        col1 = QVBoxLayout()
        col2 = QVBoxLayout()

        col1.addWidget(sever_title)
        col1.addWidget(key_title)
        col1.addWidget(user_qq_title)
        col1.addWidget(robot_qq_title)
        col1.addWidget(qmsg_status_title)

        col2.addWidget(self.sever_input)
        col2.addWidget(self.key_input)
        col2.addWidget(self.user_qq_input)
        col2.addWidget(self.robot_qq_input)
        col2.addWidget(self.qmsg_status_switch)

        mainLayout = QHBoxLayout()
        mainLayout.addLayout(col1)
        mainLayout.addLayout(col2)
        self.viewLayout.addLayout(mainLayout)

        self.sever_input.textChanged.connect(self.save_fields)
        self.key_input.textChanged.connect(self.save_fields)
        self.user_qq_input.textChanged.connect(self.save_fields)
        self.robot_qq_input.textChanged.connect(self.save_fields)

    def save_fields(self):
        """保存 Qmsg 相关的输入框"""
        cfg.set(cfg.Notice_Qmsg_sever, self.sever_input.text())
        cfg.set(cfg.Notice_Qmsg_key, self.encrypt_key(self.key_input.text()))
        cfg.set(cfg.Notice_Qmsg_user_qq, self.user_qq_input.text())
        cfg.set(cfg.Notice_Qmsg_robot_qq, self.robot_qq_input.text())
        cfg.set(cfg.Notice_Qmsg_status, self.qmsg_status_switch.isChecked())


class SMTPNoticeType(BaseNoticeType):
    """SMTP 通知配置对话框"""
    
    def __init__(self, parent=None):
        super().__init__(parent, "SMTP")
        self.add_fields()

    def add_fields(self):
        """添加 SMTP 相关的输入框"""
        server_address_title = BodyLabel(self)
        server_port_title = BodyLabel(self)
        user_name_title = BodyLabel(self)
        password_title = BodyLabel(self)
        receive_mail_title = BodyLabel(self)
        smtp_status_title = BodyLabel(self)

        self.server_address_input = LineEdit(self)
        self.server_port_input = LineEdit(self)
        self.used_ssl = CheckBox(self.tr("Use SSL"), self)
        self.user_name_input = LineEdit(self)
        self.password_input = PasswordLineEdit(self)
        self.receive_mail_input = LineEdit(self)
        self.smtp_status_switch = SwitchButton(self)

        server_address_title.setText(self.tr("Server Address:"))
        server_port_title.setText(self.tr("Server Port:"))
        user_name_title.setText(self.tr("User Name:"))
        password_title.setText(self.tr("Password:"))
        receive_mail_title.setText(self.tr("Receive Mail:"))
        smtp_status_title.setText(self.tr("SMTP Status:"))

        self.server_address_input.setText(cfg.get(cfg.Notice_SMTP_sever_address))
        self.server_port_input.setText(cfg.get(cfg.Notice_SMTP_sever_port))
        self.used_ssl.setChecked(cfg.get(cfg.Notice_SMTP_used_ssl))
        self.user_name_input.setText(cfg.get(cfg.Notice_SMTP_user_name))
        self.password_input.setText(self.decode_key("smtp"))
        self.receive_mail_input.setText(cfg.get(cfg.Notice_SMTP_receive_mail))
        self.smtp_status_switch.setChecked(cfg.get(cfg.Notice_SMTP_status))

        self.port_field = QHBoxLayout()
        self.port_field.addWidget(self.server_port_input)
        self.port_field.addWidget(self.used_ssl)

        col1 = QVBoxLayout()
        col2 = QVBoxLayout()

        col1.addWidget(server_address_title)
        col1.addWidget(server_port_title)
        col1.addWidget(user_name_title)
        col1.addWidget(password_title)
        col1.addWidget(receive_mail_title)
        col1.addWidget(smtp_status_title)

        col2.addWidget(self.server_address_input)
        col2.addLayout(self.port_field)
        col2.addWidget(self.user_name_input)
        col2.addWidget(self.password_input)
        col2.addWidget(self.receive_mail_input)
        col2.addWidget(self.smtp_status_switch)

        mainLayout = QHBoxLayout()
        mainLayout.addLayout(col1)
        mainLayout.addLayout(col2)

        self.viewLayout.addLayout(mainLayout)

        self.server_address_input.textChanged.connect(self.save_fields)
        self.server_port_input.textChanged.connect(self.save_fields)
        self.used_ssl.stateChanged.connect(self.save_fields)
        self.user_name_input.textChanged.connect(self.save_fields)
        self.password_input.textChanged.connect(self.save_fields)
        self.receive_mail_input.textChanged.connect(self.save_fields)

    def save_fields(self):
        """保存 SMTP 相关的输入框"""
        cfg.set(cfg.Notice_SMTP_sever_address, self.server_address_input.text())
        cfg.set(cfg.Notice_SMTP_sever_port, self.server_port_input.text())
        cfg.set(cfg.Notice_SMTP_used_ssl, self.used_ssl.isChecked())
        cfg.set(cfg.Notice_SMTP_user_name, self.user_name_input.text())
        cfg.set(cfg.Notice_SMTP_password, self.encrypt_key(self.password_input.text()))
        cfg.set(cfg.Notice_SMTP_receive_mail, self.receive_mail_input.text())
        cfg.set(cfg.Notice_SMTP_status, self.smtp_status_switch.isChecked())


class WxPusherNoticeType(BaseNoticeType):
    """WxPusher 通知配置对话框"""
    
    def __init__(self, parent=None):
        super().__init__(parent, "WxPusher")
        self.add_fields()

    def add_fields(self):
        """添加 WxPusher 相关的输入框"""
        wxpusher_spt_title = BodyLabel(self)
        wxpusher_status_title = BodyLabel(self)

        self.wxpusher_spt_input = PasswordLineEdit(self)
        self.wxpusher_status_switch = SwitchButton(self)

        wxpusher_spt_title.setText(self.tr("WxPusher Spt:"))
        wxpusher_status_title.setText(self.tr("WxPusher Status:"))

        self.wxpusher_spt_input.setText(self.decode_key("wxpusher"))
        self.wxpusher_status_switch.setChecked(cfg.get(cfg.Notice_WxPusher_status))

        col1 = QVBoxLayout()
        col2 = QVBoxLayout()

        col1.addWidget(wxpusher_spt_title)
        col1.addWidget(wxpusher_status_title)

        col2.addWidget(self.wxpusher_spt_input)
        col2.addWidget(self.wxpusher_status_switch)

        mainLayout = QHBoxLayout()
        mainLayout.addLayout(col1)
        mainLayout.addLayout(col2)

        self.viewLayout.addLayout(mainLayout)
        self.wxpusher_spt_input.textChanged.connect(self.save_fields)

    def save_fields(self):
        """保存 WxPusher 相关的输入框"""
        cfg.set(
            cfg.Notice_WxPusher_SPT_token,
            self.encrypt_key(self.wxpusher_spt_input.text()),
        )
        cfg.set(cfg.Notice_WxPusher_status, self.wxpusher_status_switch.isChecked())


class QYWXNoticeType(BaseNoticeType):
    """企业微信机器人通知配置对话框"""
    
    def __init__(self, parent=None):
        super().__init__(parent, "QYWX")
        self.add_fields()

    def add_fields(self):
        """添加 企业微信机器人 相关的输入框"""
        qywx_key_title = BodyLabel(self)
        qywx_status_title = BodyLabel(self)

        self.qywx_key_input = PasswordLineEdit(self)
        self.qywx_status_switch = SwitchButton(self)

        qywx_key_title.setText(self.tr("QYWXbot Key:"))
        qywx_status_title.setText(self.tr("QYWXbot Status:"))

        self.qywx_key_input.setText(self.decode_key("QYWX"))
        self.qywx_status_switch.setChecked(cfg.get(cfg.Notice_QYWX_status))

        col1 = QVBoxLayout()
        col2 = QVBoxLayout()

        col1.addWidget(qywx_key_title)
        col1.addWidget(qywx_status_title)

        col2.addWidget(self.qywx_key_input)
        col2.addWidget(self.qywx_status_switch)

        mainLayout = QHBoxLayout()
        mainLayout.addLayout(col1)
        mainLayout.addLayout(col2)

        self.viewLayout.addLayout(mainLayout)
        self.qywx_key_input.textChanged.connect(self.save_fields)

    def save_fields(self):
        """保存 QYWX 相关的输入框"""
        cfg.set(cfg.Notice_QYWX_key, self.encrypt_key(self.qywx_key_input.text()))
        cfg.set(cfg.Notice_QYWX_status, self.qywx_status_switch.isChecked())


class GotifyNoticeType(BaseNoticeType):
    """Gotify通知配置对话框"""
    
    def __init__(self, parent=None):
        super().__init__(parent, "Gotify")
        self.add_fields()

    def add_fields(self):
        """添加 Gotify 相关的输入框"""
        gotify_url_title = BodyLabel(self)
        gotify_token_title = BodyLabel(self)
        gotify_priority_title = BodyLabel(self)
        gotify_status_title = BodyLabel(self)

        self.gotify_url_input = LineEdit(self)
        self.gotify_token_input = PasswordLineEdit(self)
        self.gotify_priority_input = LineEdit(self)
        self.gotify_priority_input.setPlaceholderText("0-10")
        # 使用 qfluentwidgets 的错误态能力进行手动验证
        self.gotify_priority_input.textChanged.connect(self._validate_priority_silent)
        self.gotify_priority_input.editingFinished.connect(self._validate_priority_loud)
        self.gotify_status_switch = SwitchButton(self)

        gotify_url_title.setText(self.tr("Gotify Server URL:"))
        gotify_token_title.setText(self.tr("Gotify App Token:"))
        gotify_priority_title.setText(self.tr("Notification Priority (0-10):"))
        gotify_status_title.setText(self.tr("Gotify Status:"))

        self.gotify_url_input.setText(cfg.get(cfg.Notice_Gotify_url))
        self.gotify_token_input.setText(self.decode_key("gotify"))
        self.gotify_priority_input.setText(cfg.get(cfg.Notice_Gotify_priority))
        self.gotify_status_switch.setChecked(cfg.get(cfg.Notice_Gotify_status))

        col1 = QVBoxLayout()
        col2 = QVBoxLayout()

        col1.addWidget(gotify_url_title)
        col1.addWidget(gotify_token_title)
        col1.addWidget(gotify_priority_title)
        col1.addWidget(gotify_status_title)

        col2.addWidget(self.gotify_url_input)
        col2.addWidget(self.gotify_token_input)
        col2.addWidget(self.gotify_priority_input)
        col2.addWidget(self.gotify_status_switch)

        mainLayout = QHBoxLayout()
        mainLayout.addLayout(col1)
        mainLayout.addLayout(col2)

        self.viewLayout.addLayout(mainLayout)
        self.gotify_url_input.textChanged.connect(self.save_fields)
        self.gotify_token_input.textChanged.connect(self.save_fields)
        self.gotify_priority_input.textChanged.connect(self.save_fields)

    def _is_priority_valid(self, text: str) -> bool:
        """判断优先级是否为 0-10 的整数。"""
        t = text.strip()
        if not t or not t.isdigit():
            return False
        value = int(t)
        return 0 <= value <= 10

    def _validate_priority(self, *, show_hint: bool) -> bool:
        """手动验证优先级，并用 LineEdit.setError 展示错误态。"""
        text = self.gotify_priority_input.text()
        valid = self._is_priority_valid(text)
        self.gotify_priority_input.setError(not valid)
        return valid

    def _validate_priority_silent(self):
        """实时校验：只更新错误态，不弹提示。"""
        self._validate_priority(show_hint=False)

    def _validate_priority_loud(self):
        """失焦校验：更新错误态并弹提示。"""
        self._validate_priority(show_hint=True)

    def save_fields(self):
        """保存 Gotify 相关的输入框"""
        cfg.set(cfg.Notice_Gotify_url, self.gotify_url_input.text())
        cfg.set(cfg.Notice_Gotify_token, self.encrypt_key(self.gotify_token_input.text()))
        # priority 只有校验通过才写入，避免非法值落盘后仍可“照样运行”
        if self._validate_priority(show_hint=False):
            cfg.set(cfg.Notice_Gotify_priority, self.gotify_priority_input.text().strip())
        cfg.set(cfg.Notice_Gotify_status, self.gotify_status_switch.isChecked())


class NoticeTimingDialog(MessageBoxBase):
    """通知时机设置对话框，包含6个复选框控制不同通知时机的开启/关闭"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # 设置对话框标题
        self.titleLabel = SubtitleLabel(self.tr("Notification Timing Settings"), self)
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addSpacing(10)
        
        self.widget.setMinimumWidth(400)
        self.widget.setMinimumHeight(300)
        self.add_fields()
        self.yesButton.clicked.connect(self.on_yes)
        self.cancelButton.clicked.connect(self.on_cancel)
        
    def add_fields(self):
        """添加6个复选框"""
        layout = QVBoxLayout()
        
        # 创建6个复选框
        self.flow_started_checkbox = CheckBox(self.tr("Notify when task flow starts"), self)
        self.connect_success_checkbox = CheckBox(self.tr("Notify when device connects successfully"), self)
        self.connect_failed_checkbox = CheckBox(self.tr("Notify when device connection fails"), self)
        self.task_success_checkbox = CheckBox(self.tr("Notify when task completes successfully"), self)
        self.task_failed_checkbox = CheckBox(self.tr("Notify when task fails"), self)
        self.post_task_checkbox = CheckBox(self.tr("Notify when task flow completes"), self)
        
        # 从配置中读取当前状态
        self.flow_started_checkbox.setChecked(cfg.get(cfg.when_flow_started))
        self.connect_success_checkbox.setChecked(cfg.get(cfg.when_connect_success))
        self.connect_failed_checkbox.setChecked(cfg.get(cfg.when_connect_failed))
        self.task_success_checkbox.setChecked(cfg.get(cfg.when_task_success))
        self.task_failed_checkbox.setChecked(cfg.get(cfg.when_task_failed))
        self.post_task_checkbox.setChecked(cfg.get(cfg.when_post_task))
        
        # 添加到布局
        layout.addWidget(self.flow_started_checkbox)
        layout.addWidget(self.connect_success_checkbox)
        layout.addWidget(self.connect_failed_checkbox)
        layout.addWidget(self.task_success_checkbox)
        layout.addWidget(self.task_failed_checkbox)
        layout.addWidget(self.post_task_checkbox)
        layout.addStretch()
        
        self.viewLayout.addLayout(layout)
        
    def save_fields(self):
        """保存复选框状态到配置"""
        cfg.set(cfg.when_flow_started, self.flow_started_checkbox.isChecked())
        cfg.set(cfg.when_connect_success, self.connect_success_checkbox.isChecked())
        cfg.set(cfg.when_connect_failed, self.connect_failed_checkbox.isChecked())
        cfg.set(cfg.when_task_success, self.task_success_checkbox.isChecked())
        cfg.set(cfg.when_task_failed, self.task_failed_checkbox.isChecked())
        cfg.set(cfg.when_post_task, self.post_task_checkbox.isChecked())
        logger.info("保存通知时机设置")
        
    def on_yes(self):
        """确认按钮点击事件"""
        self.save_fields()
        self.accept()
        
    def on_cancel(self):
        """取消按钮点击事件"""
        logger.info("取消通知时机设置")
        self.close()
