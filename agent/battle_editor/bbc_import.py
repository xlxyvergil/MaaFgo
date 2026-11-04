"""Read-only BBC upload adapter, without Chaldea's online package bootstrap."""
from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
import sys
import threading
from types import ModuleType

from .storage import EditorError, battle_name


_IMPORT_LOCK = threading.Lock()
_PACKAGE = "_maafgo_editor_bbc_chaldea"


def _converter():
    # chaldea/__init__.py imports game-data/network fallbacks and BBC launch
    # helpers. A private namespace loads the same pure converter and constants
    # without running those unrelated initializers in the offline editor.
    with _IMPORT_LOCK:
        if _PACKAGE not in sys.modules:
            package = ModuleType(_PACKAGE)
            package.__path__ = [str(Path(__file__).resolve().parent.parent / "chaldea")]
            package.__package__ = _PACKAGE
            sys.modules[_PACKAGE] = package
        return importlib.import_module(f"{_PACKAGE}.bbc_importer")


def import_bbc_document(root: Path, config: object, name: object = None) -> dict:
    """Convert a JSON object into a preview; never read an uploaded path or save."""
    if not isinstance(config, dict):
        raise EditorError("invalid_bbc", "BBC 配置的 JSON 顶层必须是对象", 422)
    filename = battle_name("BBC配置.json" if name is None else name)
    try:
        module = _converter()
        data, warnings = module.convert_bbc_document(config, root=Path(root).resolve())
    except (ImportError, FileNotFoundError) as exc:
        raise EditorError("bbc_unavailable", "BBC 转换所需的离线资料缺失，请重新安装完整资源包", 503) from exc
    except ValueError as exc:
        raise EditorError("bbc_conversion_failed", str(exc), 422) from exc
    canonical = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    data["team"].setdefault("name", Path(filename).stem)
    data["_bbcSource"] = {"file": filename, "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}
    data["_conversionWarnings"] = list(warnings)
    return {"data": data, "warnings": list(warnings), "sourceName": filename}
