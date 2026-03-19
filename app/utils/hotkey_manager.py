"""
全局快捷键管理器，负责注册系统级组合键并调度任务启动/停止。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import sys
from typing import Any, Callable, Coroutine, List, Optional

from app.common.config import cfg
from app.utils.logger import logger

try:
    import keyboard
except ImportError:  # pragma: no cover
    keyboard = None

MODIFIER_MAP = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "shift": "shift",
    "win": "windows",
    "windows": "windows",
    "meta": "windows",
    "cmd": "command",
    "command": "command",
    "super": "windows",
}

KEY_ALIASES = {
    "`": "grave",
    "grave": "grave",
    "tilde": "grave",
    "backtick": "grave",
    "esc": "esc",
    "escape": "esc",
    "ret": "enter",
    "return": "enter",
    "enter": "enter",
    "space": "space",
    "spacebar": "space",
    "del": "delete",
    "delete": "delete",
    "tab": "tab",
    "capslock": "caps lock",
    "pageup": "page up",
    "pagedown": "page down",
    "home": "home",
    "end": "end",
    "insert": "insert",
    "printscreen": "print screen",
    "prtsc": "print screen",
    "left": "left",
    "right": "right",
    "up": "up",
    "down": "down",
}


class GlobalHotkeyManager:
    """负责将配置中的组合键注册为 keyboard 事件，并在触发时调度对应协程。"""

    def __init__(self, loop: asyncio.AbstractEventLoop | None):
        self._loop = loop
        self._registered_hotkeys: List = []
        self._keyboard: Any = keyboard
        self._start_factory: Callable[[], Coroutine[Any, Any, Any]] | None = None
        self._stop_factory: Callable[[], Coroutine[Any, Any, Any]] | None = None
        self._permission_available: bool | None = None  # None 表示未检测，True/False 表示检测结果

    def setup(
        self,
        start_factory: Callable[[], Coroutine[Any, Any, Any]],
        stop_factory: Callable[[], Coroutine[Any, Any, Any]],
    ) -> None:
        """配置要调度的协程，并应用当前的配置值。"""
        self._start_factory = start_factory
        self._stop_factory = stop_factory
        self.reload()

    def check_permission(self) -> bool:
        """检测 macOS/Linux 平台是否有权限使用全局快捷键。
        
        Returns:
            bool: True 表示有权限或不需要权限（Windows），False 表示无权限
        """
        # Windows 平台不需要权限检测
        if sys.platform == "win32":
            return True
        
        # 如果已经检测过，直接返回结果
        if self._permission_available is not None:
            return self._permission_available
        
        if not self._keyboard:
            self._permission_available = False
            return False
        
        # macOS/Linux 平台需要尝试注册一个测试快捷键来检测权限
        if sys.platform in ("darwin", "linux"):
            try:
                # 尝试注册一个测试快捷键
                test_hotkey = self._keyboard.add_hotkey(
                    "ctrl+shift+alt+f12",  # 使用一个不太可能被使用的组合键
                    lambda: None,
                    suppress=False,
                )
                # 如果成功注册，立即移除
                self._keyboard.remove_hotkey(test_hotkey)
                self._permission_available = True
                logger.info("全局快捷键权限检测成功")
                return True
            except Exception as exc:
                # 注册失败通常表示权限不足
                self._permission_available = False
                logger.warning("全局快捷键权限检测失败: %s", exc)
                return False
        
        # 其他平台默认返回 True
        self._permission_available = True
        return True

    def reload(self) -> None:
        """重新注册组合键（例如配置修改后调用）。"""
        if not self._keyboard:
            logger.warning(
                "keyboard 库未安装，全局快捷键功能已禁用。"
            )
            return

        if not self._loop:
            logger.warning("未提供事件循环，全局快捷键无法调度任务。")
            return

        # 检测权限（仅在 macOS/Linux 平台）
        if not self.check_permission():
            logger.warning("全局快捷键权限不足，无法注册快捷键")
            return

        self._clear()
        self._try_register("start task", cfg.get(cfg.start_task_shortcut), self._start_factory)
        self._try_register("stop task", cfg.get(cfg.stop_task_shortcut), self._stop_factory)

    def shutdown(self) -> None:
        """卸载所有注册的快捷键。"""
        self._clear()
        # Unhook all keyboard hooks to prevent access violations on Windows
        if self._keyboard:
            try:
                self._keyboard.unhook_all()
                logger.debug("已卸载所有 keyboard 钩子")
            except Exception as exc:  # pragma: no cover
                logger.warning("卸载 keyboard 钩子失败: %s", exc)

    def _clear(self) -> None:
        if not self._keyboard:
            return

        for hotkey in self._registered_hotkeys:
            try:
                self._keyboard.remove_hotkey(hotkey)
            except Exception as exc:  # pragma: no cover
                logger.warning("卸载快捷键失败: %s", exc)
        self._registered_hotkeys.clear()

    def _try_register(
        self,
        label: str,
        raw_combination: str,
        coro_factory: Callable[[], Coroutine[Any, Any, Any]] | None,
    ) -> None:
        if not raw_combination:
            return

        if not coro_factory:
            return

        hotkey = self._normalize(raw_combination)
        if not hotkey:
            logger.warning("组合键字符串 \"%s\" 无效，跳过 %s 注册。", raw_combination, label)
            return

        try:
            handle = self._keyboard.add_hotkey(
                hotkey,
                self._make_handler(label, coro_factory),
                suppress=False,
            )
            self._registered_hotkeys.append(handle)
            logger.info("注册全局快捷键：%s -> %s", hotkey, label)
        except Exception as exc:  # pragma: no cover
            logger.exception("注册全局快捷键失败 (%s): %s", label, exc)

    def _make_handler(
        self, label: str, coro_factory: Callable[[], Coroutine[Any, Any, Any]]
    ) -> Callable[[], None]:
        def _handler():
            if not self._loop:
                return
            try:
                future = asyncio.run_coroutine_threadsafe(coro_factory(), self._loop)

                def _log_errors(f: concurrent.futures.Future[Any]) -> None:
                    if f.cancelled():
                        return
                    exc = f.exception()
                    if exc:
                        logger.exception("Hotkey %s 触发时协程异常: %s", label, exc)

                future.add_done_callback(_log_errors)
            except Exception as exc:  # pragma: no cover
                logger.exception("Hotkey %s 调度失败: %s", label, exc)

        return _handler

    @staticmethod
    def _normalize(raw: str) -> Optional[str]:
        """把用户输入的组合键（如 \"Ctrl+`\"）转换为 keyboard 库支持的格式。"""
        tokens = [token.strip() for token in raw.replace(",", "+").split("+") if token.strip()]
        if not tokens:
            return None

        normalized: List[str] = []
        for token in tokens[:-1]:
            modifier = MODIFIER_MAP.get(token.lower(), token.lower())
            if modifier not in {"ctrl", "alt", "shift", "windows", "command"}:
                logger.warning("组合键中不支持的修饰键：%s（忽略）", token)
                continue
            normalized.append(modifier)

        key_token = tokens[-1].lower()
        key = KEY_ALIASES.get(key_token, key_token)
        if not key:
            return None

        modifier_keys = {"ctrl", "alt", "shift", "windows", "command"}
        if key in modifier_keys:
            logger.warning("组合键必须包含一个非修饰键：%s", raw)
            return None

        if not normalized:
            return key

        normalized.append(key)
        return "+".join(normalized)

