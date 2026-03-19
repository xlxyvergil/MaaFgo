"""
通用 i18n 服务

从 interface_manager 中抽取出来，供多个模块复用：
- 维护当前语言
- 加载翻译文件
- 提供文本/字典/列表的递归翻译能力
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import jsonc

from app.utils.logger import logger


class I18nService:
    """简单的 i18n 服务（非单例，由上层按需持有）"""

    def __init__(self, language: str = "zh_cn") -> None:
        # 当前默认语言
        self._current_language: str = language
        # 所有语言的翻译表: { language_code: { key: text } }
        self._translations: Dict[str, Dict[str, str]] = {}

    # ------------------ 语言与翻译文件 ------------------
    @property
    def language(self) -> str:
        return self._current_language

    @language.setter
    def language(self, value: str) -> None:
        if not value:
            return
        if value == self._current_language:
            return
        self._current_language = value

    def load_translations_from_interface(
        self, interface_data: Dict[str, Any], interface_dir: Path
    ) -> None:
        """
        从 interface 配置中加载当前语言的翻译文件

        Args:
            interface_data: 已解析的 interface.json(c) 字典
            interface_dir: 该配置所在目录，用于解析相对路径
        """
        if not interface_data:
            return

        lang = self._current_language
        languages = interface_data.get("languages", {})
        translation_file = languages.get(lang)
        if not translation_file:
            logger.warning("未找到语言 %s 的翻译文件配置", lang)
            return

        translation_path = interface_dir / translation_file
        try:
            with open(translation_path, "r", encoding="utf-8") as f:
                translations: Dict[str, str] = jsonc.load(f)
            self._translations[lang] = translations
            logger.debug(
                "已加载翻译文件: %s (%d 条翻译)",
                translation_path,
                len(translations),
            )
        except FileNotFoundError:
            logger.warning("未找到翻译文件: %s", translation_path)
        except jsonc.JSONDecodeError as e:
            logger.error("翻译文件格式错误: %s", e)

    # ------------------ 基础翻译能力 ------------------
    def translate_label(self, label: str, language: Optional[str] = None) -> str:
        """
        根据语言代码和原始 label 返回翻译结果。

        Args:
            label: 原始 label，可以是 "$key" 或 "key"
            language: 语言代码；为空时使用当前语言
        """
        if not label:
            return label

        lang = language or self._current_language

        if label.startswith("$"):
            key = label[1:]
        else:
            key = label

        mapping = self._translations.get(lang)
        if not mapping:
            return label

        return mapping.get(key, label)

    def translate_text(self, text: str, language: Optional[str] = None) -> str:
        """
        翻译单个文本：
        - 文本以 $ 开头时，视为 key，从翻译表中查找
        - 否则原样返回
        """
        return self.translate_label(text, language=language)

    def translate_any(self, data: Any, language: Optional[str] = None) -> Any:
        """
        递归翻译 dict / list / str 结构。

        仅用于通用 key 不敏感的场景；如果需要对特定字段名做特殊处理，
        由上层在调用前/后自行处理。
        """
        if isinstance(data, dict):
            for key, value in data.items():
                data[key] = self.translate_any(value, language=language)
            return data

        if isinstance(data, list):
            for i, item in enumerate(data):
                data[i] = self.translate_any(item, language=language)
            return data

        if isinstance(data, str):
            return self.translate_text(data, language=language)

        return data


_global_i18n_service = I18nService()


def get_i18n_service() -> I18nService:
    """获取全局 i18n 服务实例，用于简单场景下的直接翻译调用。"""
    return _global_i18n_service


__all__ = ["I18nService", "get_i18n_service"]


