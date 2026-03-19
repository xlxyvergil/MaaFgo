from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Tuple

from app.utils.logger import logger

DECORATOR_PATTERN = re.compile(r'^\s*@AgentServer\.custom_(action|recognition)\("([^"]+)"\)\s*$')


@dataclass
class CustomBundle:
    """Build result that captures output locations and metadata."""

    custom_dir: Path
    custom_json: Path
    entries: dict[str, dict[str, str]]


__all__ = ["CustomBundle", "build_custom_bundle"]


def build_custom_bundle(entry_script_path: Path | str, destination_folder: Path | str) -> CustomBundle:
    """
    复制入口目录，在目标中删除装饰器并生成 custom.json。

    Args:
        entry_script_path: 入口脚本（如 agent/main.py），将复制其所在目录。
        destination_folder: 输出目录，若存在将重建。

    Returns:
        CustomBundle: 输出目录、custom.json 路径及条目映射。
    """
    entry_script = Path(entry_script_path).resolve()
    target_dir = Path(destination_folder).resolve()

    if not entry_script.exists():
        raise FileNotFoundError(f"{entry_script} 不存在")

    entry_dir = entry_script.parent

    if target_dir.exists():
        logger.info("删除已存在目标目录 %s", target_dir)
        shutil.rmtree(target_dir)

    logger.info("复制目录 %s → %s", entry_dir, target_dir)
    shutil.copytree(entry_dir, target_dir)

    decorated_entries: List[dict[str, str]] = []

    for python_file in _iter_python_files(target_dir):
        entries, cleaned_lines, modified = _parse_and_clean_file(python_file, target_dir)
        if entries:
            decorated_entries.extend(entries)
        if modified:
            python_file.write_text("\n".join(cleaned_lines) + "\n", encoding="utf-8")

    if not decorated_entries:
        logger.warning("在复制后的目录中未发现 AgentServer 装饰器")

    custom_entries = {}
    for entry in sorted(decorated_entries, key=lambda item: item["key"]):
        key = entry["key"]
        if key in custom_entries:
            logger.warning("忽略重复 custom key %s，来自 %s", key, entry["file_path"])
            continue
        custom_entries[key] = {
            "type": entry["type"],
            "class": entry["class"],
            "file_path": f"{{custom_path}}/{entry['file_path']}",
        }

    custom_json_path = target_dir / "custom.json"
    logger.info("写入 custom.json 到 %s", custom_json_path)
    custom_json_path.write_text(
        json.dumps(custom_entries, indent=4, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return CustomBundle(
        custom_dir=target_dir,
        custom_json=custom_json_path,
        entries=custom_entries,
    )


def _iter_python_files(root: Path) -> Iterable[Path]:
    """返回目录下所有 Python 文件。"""
    yield from (path for path in root.rglob("*.py") if path.is_file())


def _parse_and_clean_file(file_path: Path, base_dir: Path) -> Tuple[List[dict[str, str]], List[str], bool]:
    """
    提取文件中 AgentServer 装饰器定义并删除装饰器行。

    Returns:
        entries, cleaned_lines, modified
    """
    text = file_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    entries: List[dict[str, str]] = []
    cleaned_lines: List[str] = []
    modified = False

    for index, line in enumerate(lines):
        match = DECORATOR_PATTERN.match(line)
        if not match:
            cleaned_lines.append(line)
            continue

        decorator_type, custom_name = match.groups()
        class_name = _find_following_class(lines, index + 1)
        if not class_name:
            logger.warning("在 %s 中未找到 %s 后的类定义", file_path, custom_name)
        else:
            relative_path = file_path.relative_to(base_dir).as_posix()
            entries.append(
                {
                    "key": custom_name,
                    "type": decorator_type,
                    "class": class_name,
                    "file_path": relative_path,
                }
            )
        logger.debug("移除装饰器 %s.%s 在 %s", decorator_type, custom_name, file_path)
        modified = True

    if not modified:
        cleaned_lines = lines

    return entries, cleaned_lines, modified


def _find_following_class(lines: List[str], start_index: int) -> str | None:
    """查找装饰器后的 class 定义。"""
    for line in lines[start_index:]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("class "):
            return stripped.split()[1].split("(")[0]
        if not stripped.startswith("@"):
            break
    return None

