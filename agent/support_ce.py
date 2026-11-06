"""Optional support CE packs shared by recognition and the offline editor.

Installed files are data, never Python code or Pipeline overrides. The caller's
installation root is authoritative; neither ZIP names nor manifests select it.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import threading
import zipfile

MAX_PACK_BYTES = 64 * 1024 * 1024
MAX_FILES = 10000
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_IMAGE_BYTES = 512 * 1024
STATES = ("满破", "非满破")
_INSTALL_LOCK = threading.Lock()
_ALNUM = str.maketrans({c: c - 0xFEE0 for start, end in
                        ((0xFF10, 0xFF19), (0xFF21, 0xFF3A), (0xFF41, 0xFF5A))
                        for c in range(start, end + 1)})


class SupportCEError(ValueError):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _invalid(reason):
    return SupportCEError("support_ce_pack", f"礼装扩展包无法安装：{reason}。请重新下载完整的礼装扩展 ZIP，再点击“安装礼装扩展”重试。")


def normalized_name(value):
    return str(value).translate(_ALNUM).replace("/", "／").replace("\\", "＼")


def extension_directory(root):
    return Path(root).resolve() / "config" / "SupportCE"


def _safe_relative(value):
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise _invalid("包含无法识别的文件路径")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} or ":" in part
                               or part.endswith((" ", ".")) for part in path.parts):
        raise _invalid("文件路径超出礼装扩展目录")
    if str(path) != value or any(any(ord(c) < 32 for c in part) for part in path.parts):
        raise _invalid("包含无法识别的文件路径")
    if any(re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part) for part in path.parts):
        raise _invalid("包含系统保留文件名")
    return path


def _parse_manifest(raw):
    if len(raw) > MAX_MANIFEST_BYTES:
        raise _invalid("礼装清单超过 4 MiB")
    try:
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("重复字段")
                result[key] = value
            return result
        document = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise _invalid("manifest.json 礼装清单损坏") from exc
    if (not isinstance(document, dict) or type(document.get("schemaVersion")) is not int
            or document.get("schemaVersion") != 1 or document.get("type") != "maafgo-support-ce"):
        raise _invalid("所选 ZIP 不是受支持的 MaaFGO 礼装扩展包")
    if any(not isinstance(document.get(field), str) or not document[field].strip()
           or len(document[field]) > 160 for field in ("name", "version")):
        raise _invalid("缺少扩展包名称或版本")
    entries = document.get("equips")
    if not isinstance(entries, list) or not entries or len(entries) > MAX_FILES // 2:
        raise _invalid("礼装清单为空或礼装数量过多")
    ids, paths = set(), set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise _invalid("礼装清单条目无法读取")
        eid, name, templates = entry.get("id"), entry.get("name"), entry.get("templates")
        if type(eid) is not int or not 0 < eid <= 9999999999 or eid in ids:
            raise _invalid("礼装 ID 缺失或重复")
        ids.add(eid)
        if not isinstance(name, str) or not name.strip() or len(name) > 160:
            raise _invalid(f"礼装 {eid} 缺少名称")
        if not isinstance(templates, dict) or not templates or set(templates) - set(STATES):
            raise _invalid(f"礼装「{name}」没有可用的满破或非满破图片")
        for state_name, relative in templates.items():
            path = _safe_relative(relative)
            if len(path.parts) != 2 or path.parts[0] != state_name or path.suffix.lower() != ".png":
                raise _invalid(f"礼装「{name}」图片没有放在对应满破状态目录")
            key = relative.casefold()
            if key in paths:
                raise _invalid("多张礼装引用了同一图片路径")
            paths.add(key)
    hashes = document.get("sha256")
    if hashes is not None:
        expected = {path for entry in entries for path in entry["templates"].values()}
        if (not isinstance(hashes, dict) or set(hashes) != expected
                or any(not isinstance(v, str) or re.fullmatch(r"[a-fA-F0-9]{64}", v) is None
                       for v in hashes.values())):
            raise _invalid("图片哈希清单缺失条目或无法读取")
    return document


@lru_cache(maxsize=8)
def _cached_manifest(path, modified, size):
    try:
        return _parse_manifest(Path(path).read_bytes())
    except (SupportCEError, OSError):
        return {}


def read_manifest(root):
    directory = extension_directory(root)
    path = directory / "manifest.json"
    try:
        if not directory.resolve().is_relative_to(Path(root).resolve()) or directory.is_symlink():
            return {}
        if not path.resolve().is_relative_to(directory.resolve()):
            return {}
        info = path.stat()
        if info.st_size > MAX_MANIFEST_BYTES:
            return {}
        return _cached_manifest(str(path), info.st_mtime_ns, info.st_size)
    except OSError:
        return {}


def _installed_path(root, relative):
    directory = extension_directory(root)
    path = directory.joinpath(*_safe_relative(relative).parts)
    if path.is_file() and path.resolve().is_relative_to(directory.resolve()):
        return path
    return None


def extension_entries(root):
    return read_manifest(root).get("equips", [])


def resolve_external_templates(root, ce_name, status, equip_id=None):
    states = STATES if status == "任意" else (status,)
    if equip_id is None and isinstance(ce_name, str) and re.fullmatch(r"support-ce:[1-9][0-9]{0,9}", ce_name):
        equip_id = ce_name.split(":", 1)[1]
    filename = normalized_name(ce_name)
    matches = []
    for entry in extension_entries(root):
        if equip_id is not None:
            if str(entry["id"]) != str(equip_id):
                continue
        elif normalized_name(entry["name"]) + ".png" != filename:
            continue
        matches.append(entry)
    # Never guess an ID when distinct records have an ambiguous display name.
    if equip_id is None and len(matches) != 1:
        return []
    return [path for entry in matches for state_name in states
            if (relative := entry["templates"].get(state_name))
            and (path := _installed_path(root, relative)) is not None]


def _validate_image(raw, name):
    from PIL import Image
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise _invalid(f"图片「{name}」为空或超过 512 KiB")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != "PNG" or image.size != (158, 44) or getattr(image, "n_frames", 1) != 1:
                raise _invalid(f"图片「{name}」不是 158×44 的静态 PNG")
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
    except SupportCEError:
        raise
    except (OSError, ValueError, SyntaxError) as exc:
        raise _invalid(f"图片「{name}」损坏") from exc


def _validated_files(payload):
    if not payload or len(payload) > MAX_PACK_BYTES:
        raise SupportCEError("support_ce_size", "礼装扩展包为空或超过 64 MiB，无法安装。请重新下载不超过 64 MiB 的礼装扩展 ZIP 后重试。", 413)
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > MAX_FILES:
                raise _invalid("ZIP 为空或文件数量过多")
            files, seen, total = {}, set(), 0
            for info in entries:
                name = info.filename
                trimmed = name[:-1] if info.is_dir() else name
                _safe_relative(trimmed)
                key = trimmed.casefold()
                if key in seen:
                    raise _invalid("ZIP 内有重复或仅大小写不同的路径")
                seen.add(key)
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                    raise _invalid("ZIP 内含有链接或特殊文件")
                if info.flag_bits & 1:
                    raise _invalid("ZIP 已加密")
                if info.is_dir():
                    if trimmed not in STATES:
                        raise _invalid("ZIP 包含礼装目录之外的文件夹")
                    continue
                total += info.file_size
                limit = MAX_MANIFEST_BYTES if name == "manifest.json" else MAX_IMAGE_BYTES
                if info.file_size > limit or total > MAX_PACK_BYTES:
                    raise _invalid("ZIP 解压后的体积超过限制")
                if name != "manifest.json" and (len(PurePosixPath(name).parts) != 2
                    or PurePosixPath(name).parts[0] not in STATES or not name.lower().endswith(".png")):
                    raise _invalid("ZIP 包含礼装清单和图片之外的文件")
                files[name] = archive.read(info)
            if "manifest.json" not in files:
                raise _invalid("ZIP 根目录缺少 manifest.json")
            manifest = _parse_manifest(files["manifest.json"])
            expected = {p for item in manifest["equips"] for p in item["templates"].values()}
            if set(files) != expected | {"manifest.json"}:
                raise _invalid("礼装清单与 ZIP 内的图片不一致")
            for name in expected:
                raw = files[name]
                _validate_image(raw, name)
                if manifest.get("sha256") and hashlib.sha256(raw).hexdigest() != manifest["sha256"][name].lower():
                    raise _invalid(f"图片「{name}」校验失败")
            return manifest, files
    except SupportCEError:
        raise
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError, ValueError) as exc:
        raise _invalid("ZIP 文件损坏或压缩格式不受支持") from exc


def install_extension(root, payload):
    manifest, files = _validated_files(payload)
    root = Path(root).resolve()
    config = root / "config"
    destination, backup = config / "SupportCE", config / "SupportCE.backup"
    with _INSTALL_LOCK:
        try:
            config.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SupportCEError("support_ce_write", "无法创建本次 MaaFGO 的 config 目录，礼装扩展尚未安装。请检查目录写入权限及磁盘空间后重试。", 500) from exc
        if (not config.resolve().is_relative_to(root)
                or any(p.is_symlink() or not p.resolve().is_relative_to(config.resolve())
                       for p in (destination, backup))):
            raise SupportCEError("support_ce_directory", "礼装扩展安装目录指向了其他位置，无法安全写入。请检查本次 MaaFGO 目录下的 config/SupportCE 和 SupportCE.backup，移除目录链接后重试。")
        try:
            staging = Path(tempfile.mkdtemp(prefix=".SupportCE-", dir=config))
        except OSError as exc:
            raise SupportCEError("support_ce_write", "无法在 config 目录创建安装临时文件，礼装扩展尚未安装。请检查目录写入权限及磁盘空间后重试。", 500) from exc
        moved_previous = False
        try:
            for name, raw in files.items():
                target = staging.joinpath(*PurePosixPath(name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
            if destination.exists():
                if not destination.is_dir():
                    raise SupportCEError("support_ce_directory", "config/SupportCE 已被同名文件占用，无法安装礼装扩展。请将该文件改名或移走后重试。")
                if backup.exists():
                    if backup.is_dir():
                        shutil.rmtree(backup)
                    else:
                        backup.unlink()
                destination.replace(backup)
                moved_previous = True
            try:
                staging.replace(destination)
            except OSError:
                if moved_previous:
                    backup.replace(destination)
                raise
            _cached_manifest.cache_clear()
        except SupportCEError:
            raise
        except OSError as exc:
            raise SupportCEError("support_ce_write", "礼装扩展包已校验，但写入本次 MaaFGO 的 config/SupportCE 失败，无法完成安装。请检查目录写入权限、磁盘空间及文件占用后重试。", 500) from exc
        finally:
            if staging.exists() and staging.resolve().is_relative_to(config.resolve()):
                shutil.rmtree(staging)
    return {"installed": len(manifest["equips"]), "name": manifest["name"], "version": manifest["version"],
            "directory": "config/SupportCE", "images": len(files) - 1}
