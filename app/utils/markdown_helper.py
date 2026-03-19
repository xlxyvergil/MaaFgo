"""
提供统一的 Markdown 渲染与文件读取。
"""

from __future__ import annotations

import hashlib
import importlib
import re
from pathlib import Path
from typing import Union
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import markdown
from PySide6.QtCore import QRunnable, QThreadPool, Qt, Signal, QObject
from PySide6.QtGui import QImage

from app.utils.logger import logger

_IMG_PATTERN = re.compile(
    r'<img\s+([^>]*?)src=["\']([^"\']+)["\']([^>]*)>',
    re.IGNORECASE,
)

# 表格相关的正则表达式
_TABLE_PATTERN = re.compile(
    r'<table(\s[^>]*)?>',
    re.IGNORECASE,
)
_TABLE_CELL_PATTERN = re.compile(
    r'<(td|th)(\s[^>]*)?>',
    re.IGNORECASE,
)

# 列表相关的正则表达式
_UL_PATTERN = re.compile(r'<ul(\s[^>]*)?>', re.IGNORECASE)
_OL_PATTERN = re.compile(r'<ol(\s[^>]*)?>', re.IGNORECASE)
_LI_PATTERN = re.compile(r'<li(\s[^>]*)?>', re.IGNORECASE)

_MAX_IMAGE_WIDTH = 500
_REMOTE_IMAGE_CACHE_DIR = Path.cwd() / "resource" / "announcement" / "_cache"

# 缓存一次探测到的可用 Markdown 扩展，避免重复 import
_AVAILABLE_MD_EXTENSIONS: list[str] | None = None


class _RemoteImageDownloadTask(QRunnable):
    def __init__(self, url: str, target_path: Path, cache_owner: "RemoteImageCache"):
        super().__init__()
        self.url = url
        self.target_path = target_path
        self.cache_owner = cache_owner

    def run(self):
        try:
            req = Request(
                self.url,
                headers={"User-Agent": "MFW-Announcement/1.0"},
            )
            with urlopen(req, timeout=8) as resp:
                data = resp.read()
            if not self.target_path.parent.exists():
                self.target_path.parent.mkdir(parents=True, exist_ok=True)
            image = QImage.fromData(data)
            if not image.isNull():
                if image.width() > _MAX_IMAGE_WIDTH:
                    image = image.scaledToWidth(
                        _MAX_IMAGE_WIDTH, Qt.TransformationMode.SmoothTransformation
                    )
                image.save(str(self.target_path))
            else:
                self.target_path.write_bytes(data)
        except Exception as exc:
            logger.warning("网络图片缓存失败: %s (%s)", self.url, exc)
        finally:
            self.cache_owner._notify_download_complete(self.url)


class RemoteImageCache(QObject):
    image_cached = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cache_dir = _REMOTE_IMAGE_CACHE_DIR
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._thread_pool = QThreadPool.globalInstance()
        self._in_progress: set[str] = set()

    def _url_to_path(self, url: str) -> Path:
        parsed = urlparse(url)
        suffix = Path(parsed.path).suffix or ".img"
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self._cache_dir / f"{key}{suffix}"

    def get_cached_uri(self, url: str) -> str | None:
        if not url:
            return None
        target = self._url_to_path(url)
        if target.exists():
            return target.as_uri()
        return None

    def ensure_cached(self, url: str) -> None:
        if (
            not url
            or not url.lower().startswith(("http://", "https://"))
            or url in self._in_progress
        ):
            return
        self._in_progress.add(url)
        task = _RemoteImageDownloadTask(url, self._url_to_path(url), self)
        self._thread_pool.start(task)

    def _notify_download_complete(self, url: str) -> None:
        self._in_progress.discard(url)
        if self.get_cached_uri(url):
            self.image_cached.emit(url)


remote_image_cache = RemoteImageCache()


def _wrap_image(match: re.Match[str]) -> str:
    before_src = match.group(1)
    src = match.group(2)
    after_src = match.group(3)
    img_tag = f'<img {before_src}src="{src}"{after_src} style="cursor: pointer;">'
    return f'<a href="image:{src}" style="text-decoration: none;">{img_tag}</a>'


def _add_table_styles(html: str) -> str:
    """为表格添加内联样式，确保在Qt RichText中正确显示"""
    # Qt RichText可能不完全支持<thead>和<tbody>，所以需要简化表格结构
    # 移除<thead>和<tbody>标签，但保留其内容
    html = re.sub(r'</?thead>', '', html, flags=re.IGNORECASE)
    html = re.sub(r'</?tbody>', '', html, flags=re.IGNORECASE)
    
    # 为table标签添加样式（如果没有style属性）
    def add_table_style(match: re.Match[str]) -> str:
        attrs = match.group(1) or ""
        if 'style=' in attrs.lower():
            # 如果已有style，追加样式
            html_str = match.group(0)
            return re.sub(
                r'style="([^"]*)"',
                r'style="\1; border-collapse: collapse; border: 1px solid #ccc; margin: 4px 0;"',
                html_str,
                flags=re.IGNORECASE
            )
        else:
            return f'<table{attrs} style="border-collapse: collapse; border: 1px solid #ccc; margin: 4px 0;">'
    
    html = _TABLE_PATTERN.sub(add_table_style, html)
    
    # 为td和th标签添加样式
    def add_cell_style(match: re.Match[str]) -> str:
        tag = match.group(1)  # td or th
        attrs = match.group(2) or ""
        base_style = "border: 1px solid #ccc; padding: 4px 8px; text-align: left;"
        
        if tag.lower() == 'th':
            base_style += " font-weight: bold; background-color: #f0f0f0;"
        
        if 'style=' in attrs.lower():
            # 如果已有style，追加样式
            return re.sub(
                r'style="([^"]*)"',
                lambda m: f'style="{m.group(1)}; {base_style}"',
                match.group(0),
                flags=re.IGNORECASE
            )
        else:
            return f'<{tag}{attrs} style="{base_style}">'
    
    html = _TABLE_CELL_PATTERN.sub(add_cell_style, html)
    
    return html


def _add_list_styles(html: str) -> str:
    """为列表添加内联样式，确保在Qt RichText中正确显示"""
    # Qt RichText对列表支持有限，使用更兼容的方式
    # 使用简单的连字符作为无序列表的项目符号（更兼容）
    
    # 处理无序列表：将<ul>和<li>转换为带样式的<p>或<div>
    # 匹配整个<ul>...</ul>块
    def convert_ul(match: re.Match[str]) -> str:
        ul_content = match.group(0)
        # 提取所有<li>内容
        li_items = re.findall(r'<li[^>]*>(.*?)</li>', ul_content, re.IGNORECASE | re.DOTALL)
        # 生成带项目符号的div（使用简单的-符号，确保兼容性）
        result_parts = []
        for item in li_items:
            # 使用更简单的样式，确保Qt RichText能正确渲染
            result_parts.append(
                '<div style="margin: 4px 0; padding-left: 20px;">- ' + item + '</div>'
            )
        return ''.join(result_parts)
    
    # 匹配<ul>...</ul>
    ul_pattern = re.compile(r'<ul[^>]*>.*?</ul>', re.IGNORECASE | re.DOTALL)
    html = ul_pattern.sub(convert_ul, html)
    
    # 处理有序列表：将<ol>和<li>转换为带编号的<div>
    def convert_ol(match: re.Match[str]) -> str:
        ol_content = match.group(0)
        # 提取所有<li>内容
        li_items = re.findall(r'<li[^>]*>(.*?)</li>', ol_content, re.IGNORECASE | re.DOTALL)
        # 生成带编号的div
        result_parts = []
        for i, item in enumerate(li_items, 1):
            result_parts.append(
                f'<div style="margin: 4px 0; padding-left: 20px;">{i}. {item}</div>'
            )
        return ''.join(result_parts)
    
    # 匹配<ol>...</ol>
    ol_pattern = re.compile(r'<ol[^>]*>.*?</ol>', re.IGNORECASE | re.DOTALL)
    html = ol_pattern.sub(convert_ol, html)
    
    return html


def _detect_markdown_extensions() -> list[str]:
    """
    探测当前环境中可用的官方 Markdown 扩展，只返回实际存在的模块。
    这样可以避免因为缺失某些扩展（例如 fenced_code）而导致导入报错。
    """
    global _AVAILABLE_MD_EXTENSIONS
    if _AVAILABLE_MD_EXTENSIONS is not None:
        return _AVAILABLE_MD_EXTENSIONS

    candidates = [
        "markdown.extensions.tables",
        "markdown.extensions.sane_lists",
    ]

    available: list[str] = []
    for ext in candidates:
        try:
            importlib.import_module(ext)
        except Exception:
            continue
        else:
            available.append(ext)

    if not available:
        logger.warning(
            "Markdown: 未检测到可用的可选扩展，使用核心功能渲染。"
        )

    _AVAILABLE_MD_EXTENSIONS = available
    return _AVAILABLE_MD_EXTENSIONS


def render_markdown(
    content: str | None, base_path: Path | None = None
) -> str:
    """
    将 Markdown/HTML 内容渲染成 HTML，并为 <img> 自动添加点击链接，为表格和列表添加样式。

    仅使用当前环境中实际存在的官方扩展，避免因为缺失扩展导致导入错误。
    """
    if not content:
        return ""

    processed = content.replace("\r\n", "\n")
    stripped = processed.strip()

    try:
        if stripped.startswith("<") and stripped.endswith(">"):
            html = (
                processed.replace("\n", "<br>") if "\n" in processed else processed
            )
        else:
            # 使用 python-markdown 渲染 Markdown，仅启用当前环境中可用的官方扩展
            extensions = _detect_markdown_extensions()
            if extensions:
                html = markdown.markdown(processed, extensions=extensions)
            else:
                html = markdown.markdown(processed)

        # 为表格添加样式
        html = _add_table_styles(html)
        # 为列表添加样式
        html = _add_list_styles(html)
        # 为图片添加点击链接并处理相对路径
        html = _IMG_PATTERN.sub(
            _create_image_wrapper(base_path, remote_image_cache), html
        )

        return html
    except Exception as exc:
        # 防御性处理：Markdown 渲染出错时不影响主流程，记录错误并回退到原始文本
        logger.error("Markdown 渲染失败，将返回原始文本: %s", exc, exc_info=True)
        return content or ""


def _create_image_wrapper(
    base_path: Path | None, remote_cache: RemoteImageCache
):
    """根据 base_path 生成可以处理相对路径的 img 包裹器。"""

    def resolve_src(src: str) -> str:
        trimmed = src.strip()
        if not trimmed:
            return trimmed
        normalized = trimmed.replace("\\", "/")
        if normalized.startswith(("http://", "https://")):
            cached = remote_cache.get_cached_uri(normalized)
            if cached:
                return cached
            remote_cache.ensure_cached(normalized)
            return normalized
        if normalized.startswith(("file://", "data:")):
            file_path = _path_from_uri(normalized)
            if file_path and file_path.exists():
                scaled = _scale_local_image(file_path)
                return scaled or file_path.as_uri()
            return normalized
        relative = Path(normalized.lstrip("/"))
        target = relative
        if not relative.is_absolute() and base_path:
            target = (base_path / relative).resolve()
        if target.exists():
            scaled = _scale_local_image(target)
            return scaled or target.as_uri()
        return trimmed

    def wrapper(match: re.Match[str]) -> str:
        before_src = match.group(1)
        src = match.group(2)
        after_src = match.group(3)
        resolved_src = resolve_src(src)
        style_attrs = (
            "cursor: pointer; max-width: "
            f"{_MAX_IMAGE_WIDTH}px; height: auto; display: block;"
        )
        img_tag = f'<img {before_src}src="{resolved_src}"{after_src} style="{style_attrs}">'
        return f'<a href="image:{resolved_src}" style="text-decoration: none;">{img_tag}</a>'

    return wrapper


def _path_from_uri(uri: str) -> Path | None:
    try:
        parsed = urlparse(uri)
    except Exception:
        return None
    if parsed.scheme != "file":
        return None
    return Path(parsed.path)


def _scale_local_image(path: Path) -> str | None:
    key = hashlib.sha256(str(path).encode("utf-8")).hexdigest()
    suffix = path.suffix or ".img"
    target = _REMOTE_IMAGE_CACHE_DIR / "local" / f"{key}{suffix}"
    if target.exists():
        return target.as_uri()
    image = QImage(str(path))
    if image.isNull():
        return None
    if image.width() <= _MAX_IMAGE_WIDTH:
        return path.as_uri()
    scaled = image.scaledToWidth(
        _MAX_IMAGE_WIDTH, Qt.TransformationMode.SmoothTransformation
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    scaled.save(str(target))
    return target.as_uri()


def load_markdown_file(path: Union[str, Path]) -> str:
    """
    读取 Markdown 文件并返回原始文本。
    """
    file_path = Path(path)
    return file_path.read_text(encoding="utf-8")
