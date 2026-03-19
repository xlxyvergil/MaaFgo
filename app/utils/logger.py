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
MFW-ChainFlow Assistant 日志单元
作者:overflow65537
"""

import logging
import os

from logging.handlers import TimedRotatingFileHandler

# 提取日志格式为常量
LOG_FORMAT = "[%(asctime)s][%(levelname)s][%(filename)s][L%(lineno)d][%(funcName)s] | %(message)s"


class LoggerManager:
    def __init__(self, log_file_path="debug/gui.log"):
        """
        初始化日志管理器。

        Args:
            log_file_path (str): 日志文件的路径，默认为 "debug/gui.log"。
        """
        self.logger = self._create_logger(log_file_path)
        # 关闭requests模块的日志输出
        requests_logger = logging.getLogger("urllib3")
        requests_logger.setLevel(logging.CRITICAL)
        # 屏蔽 markdown 相关模块的所有输出，避免 DEBUG 级别日志
        markdown_logger = logging.getLogger("markdown")
        markdown_logger.setLevel(logging.CRITICAL)
        markdown_logger.propagate = False
        markdown_extensions_logger = logging.getLogger("markdown.extensions")
        markdown_extensions_logger.setLevel(logging.CRITICAL)
        markdown_extensions_logger.propagate = False
        markdown_core_logger = logging.getLogger("markdown.core")
        markdown_core_logger.setLevel(logging.CRITICAL)
        markdown_core_logger.propagate = False
        markdown_upper_logger = logging.getLogger("MARKDOWN")
        markdown_upper_logger.setLevel(logging.CRITICAL)
        markdown_upper_logger.propagate = False
        self._asyncify_logger = logging.getLogger("asyncify")
        self._qasync_logger = logging.getLogger("qasync")
        self._logger_state_cache: dict[str, tuple[int, bool]] = {}

    def _create_logger(self, log_file_path):
        """
        创建或重新创建日志记录器。

        Args:
            log_file_path (str): 日志文件的路径。

        Returns:
            logging.Logger: 配置好的日志记录器。
        """
        # 获取根日志记录器
        root_logger = logging.getLogger()
        # 清除现有的处理器
        for handler in root_logger.handlers[:]:
            handler.close()
            root_logger.removeHandler(handler)

        # 创建日志目录，处理 Windows 权限错误
        if log_dir := os.path.dirname(log_file_path):
            try:
                os.makedirs(log_dir, exist_ok=True)
            except OSError as e:
                # Windows Error 5: 拒绝访问，可能是目录已存在或被锁定
                # 如果目录已存在，继续执行；否则抛出异常
                if not os.path.exists(log_dir):
                    raise
                # 如果目录存在但无法访问，记录警告但继续尝试创建文件
                import warnings

                warnings.warn(
                    f"无法访问日志目录 {log_dir}: {e}，将尝试继续创建日志文件"
                )

        # 创建新的处理器
        file_handler = TimedRotatingFileHandler(
            log_file_path,
            when="midnight",
            backupCount=3,
            encoding="utf-8",
        )
        stream_handler = logging.StreamHandler()

        # 设置处理器的格式
        formatter = logging.Formatter(LOG_FORMAT)
        file_handler.setFormatter(formatter)
        stream_handler.setFormatter(formatter)

        # 配置日志记录器
        root_logger.setLevel(logging.DEBUG)
        root_logger.addHandler(file_handler)
        root_logger.addHandler(stream_handler)

        return root_logger

    def change_log_path(self, new_log_path):
        """
        在运行时更改日志的存放位置。

        Args:
            new_log_path (str): 新的日志文件路径。
        """
        self.logger = self._create_logger(new_log_path)

    def _record_logger_state(self, logger: logging.Logger) -> None:
        """记录给定 Logger 的原始 level/disabled 状态。"""
        if logger.name not in self._logger_state_cache:
            self._logger_state_cache[logger.name] = (logger.level, logger.disabled)

    def _restore_logger_state(self, logger: logging.Logger) -> None:
        """恢复先前保存的 Logger 状态（如有）。"""
        state = self._logger_state_cache.pop(logger.name, None)
        if not state:
            return
        level, disabled = state
        logger.disabled = disabled
        logger.setLevel(level)

    def _suppress_logger(self, logger: logging.Logger, level=logging.WARNING) -> None:
        """降低 Logger 级别并禁用输出，以便短期内屏蔽日志。"""
        self._record_logger_state(logger)
        logger.setLevel(level)
        logger.disabled = True

    def suppress_asyncify_logging(self) -> None:
        """Temporarily raise asyncify logger level and disable outputs."""
        self._suppress_logger(self._asyncify_logger)

    def restore_asyncify_logging(self) -> None:
        """Restore asyncify logger to its original level/disabled state."""
        self._restore_logger_state(self._asyncify_logger)

    def suppress_qasync_logging(self) -> None:
        """屏蔽 qasync 产生的调试日志，以减小日志量。"""
        self._suppress_logger(self._qasync_logger)

    def restore_qasync_logging(self) -> None:
        """恢复 qasync Logger 的原始状态。"""
        self._restore_logger_state(self._qasync_logger)


logger_manager = LoggerManager()
logger = logger_manager.logger


def suppress_asyncify_logging() -> None:
    logger_manager.suppress_asyncify_logging()


def restore_asyncify_logging() -> None:
    logger_manager.restore_asyncify_logging()


def suppress_qasync_logging() -> None:
    logger_manager.suppress_qasync_logging()


def restore_qasync_logging() -> None:
    logger_manager.restore_qasync_logging()
