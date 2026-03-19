import os
import platform
from pathlib import Path
from typing import Optional, Union

from cryptography.fernet import Fernet

from app.utils.logger import logger

APP_NAME = "MFW-ChainFlow Assistant"


def get_app_support_dir(app_name: str = APP_NAME) -> Path:
    """返回操作系统推荐的应用支持目录，确保目录存在。"""
    sys_name = platform.system()
    if sys_name == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or Path.home() / "AppData" / "Local")
    elif sys_name == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")

    target = base / app_name
    target.mkdir(parents=True, exist_ok=True)
    return target


class CryptoManager:
    """提供密钥加载与数据的加解密能力。"""

    KEY_FILE = get_app_support_dir() / "k.ey"

    def __init__(self) -> None:
        self._fernet: Optional[Fernet] = None

    def ensure_key_exists(self, path: str | Path | None = None) -> bytes:
        """确保密钥文件存在并返回密钥内容。"""
        key_path = Path(path) if path is not None else self.KEY_FILE
        key_path.parent.mkdir(parents=True, exist_ok=True)
        if not key_path.exists():
            logger.debug("生成密钥文件: %s", key_path)
            key = Fernet.generate_key()
            with key_path.open("wb") as key_file:
                key_file.write(key)
            return key
        logger.debug("加载密钥文件成功: %s", key_path)
        return key_path.read_bytes()

    def get_fernet(self, path: str | Path | None = None) -> Fernet:
        """返回用于加密/解密的 Fernet 实例。"""
        if self._fernet is None:
            self._fernet = Fernet(self.ensure_key_exists(path))
        return self._fernet

    def encrypt_payload(self, value: Union[bytes, str]) -> bytes:
        """将字符串或字节数据加密后返回字节串。"""
        data = value if isinstance(value, bytes) else value.encode("utf-8")
        return self.get_fernet().encrypt(data)

    def decrypt_payload(self, value: Union[bytes, str]) -> bytes:
        """将密文还原为原始字节串。"""
        token = value if isinstance(value, bytes) else value.encode("utf-8")
        return self.get_fernet().decrypt(token)


crypto_manager = CryptoManager()
