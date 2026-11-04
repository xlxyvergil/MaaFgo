from pathlib import Path

import shutil
import sys
import json
import os
import hashlib
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(script_dir)

from configure import configure_ocr_model

working_dir = Path(__file__).parent.parent
install_path = working_dir / Path("install-mxu")
version = len(sys.argv) > 1 and sys.argv[1] or "v0.0.1"


def validate_editor_bundle(root, *, packaged=False):
    """Fail before packaging an incomplete offline editor; no build/network IO."""
    root = Path(root).resolve()
    static = root / ("web/battle-editor" if packaged else "assets/web/battle-editor")
    resource = root / ("resource" if packaged else "assets/resource")
    data = root / "agent/battle_editor/data"

    def required(path):
        path = path.resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise RuntimeError(f"编队编辑器打包文件缺失或越界：{path}")
        return path

    def document(path):
        try:
            value = json.loads(required(path).read_text(encoding="utf-8-sig"))
            if not isinstance(value, dict):
                raise ValueError("expected JSON object")
            return value
        except (UnicodeError, ValueError) as exc:
            raise RuntimeError(f"编队编辑器打包 JSON 无效：{path}") from exc

    def checked_asset(base, relative, metadata):
        if not isinstance(relative, str) or "\\" in relative or ":" in relative:
            raise RuntimeError(f"编队编辑器打包资源路径无效：{relative!r}")
        path = required(base / relative)
        if not path.is_relative_to(base.resolve()):
            raise RuntimeError(f"编队编辑器打包资源越界：{relative}")
        if (not isinstance(metadata, dict) or not isinstance(metadata.get("sha256"), str)
                or hashlib.sha256(path.read_bytes()).hexdigest() != metadata["sha256"]):
            raise RuntimeError(f"编队编辑器打包资源哈希不符：{path}")

    class EntryAssets(HTMLParser):
        def __init__(self):
            super().__init__()
            self.scripts, self.styles = [], []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "script" and attrs.get("src"):
                self.scripts.append(attrs["src"])
            if tag == "link" and "stylesheet" in attrs.get("rel", "").split() and attrs.get("href"):
                self.styles.append(attrs["href"])

    parser = EntryAssets()
    parser.feed(required(static / "index.html").read_text(encoding="utf-8"))
    if not parser.scripts or not parser.styles or (static / "src").exists():
        raise RuntimeError("编队编辑器静态产物缺失，请先运行 npm --prefix web/battle-editor run build")
    for reference in parser.scripts + parser.styles:
        url = urlsplit(reference)
        path = (static / unquote(url.path).lstrip("/")).resolve()
        if url.scheme or url.netloc or not path.is_relative_to(static.resolve()):
            raise RuntimeError(f"编队编辑器首页必须使用随包静态资源：{reference}")
        required(path)

    for relative in (
        "agent/battle_editor/__init__.py", "agent/battle_editor/main.py",
        "agent/battle_editor/launcher.py", "agent/battle_editor/server.py",
        "agent/battle_editor/catalog.py", "agent/battle_editor/storage.py",
        "agent/battle_editor/validation.py", "agent/battle_editor/bond_optimizer.py",
        "agent/battle_editor/special_choices.py", "agent/battle/core/special_skills.py",
        "agent/battle/core/advanced_strategy.py",
        "agent/battle_editor/equip_filters.py",
        "agent/battle_editor/np_cards.py", "agent/battle_editor/np_card_metadata.py",
        "agent/battle_editor/screenshot_import.py", "agent/battle/perception/formation_candidates.py",
        "agent/battle_editor/share_image.py",
        "agent/battle_editor/chaldea_image.py",
        "agent/battle_editor/bbc_import.py", "agent/chaldea/bbc_importer.py", "agent/chaldea/bbc_special_skills.py", "agent/chaldea/servant_types.py",
        "agent/custom/battle_editor_action.py", "agent/custom/bond_matcher.py",
        "agent/chaldea/servant_aliases.py", "agent/battle/card_composer.py",
        "agent/battle/perception/composed_card.py",
        "打开编队编辑器.cmd", "docs/HTML编队与战斗逻辑工具使用说明.md",
    ):
        required(root / relative)
    for relative in ("agent/custom/servant_list.json", "agent/custom/equip_list.json",
                     "agent/chaldea/bbc_servant_ids.json", "agent/utils/Chaldea/equip_names_CN.json",
                     "agent/custom/bond_completion_costs.json"):
        document(root / relative)
    document(data / "catalog.json")
    filter_path = data / "equip_filters.json"
    filters = document(filter_path)
    filter_sources = document(data / "equip_filters_sources.json")
    categories = document(root / "agent/custom/equip_categories.json")
    if (filters.get("schemaVersion") != 1 or not isinstance(filters.get("equips"), dict) or not filters["equips"]
            or filter_sources.get("schemaVersion") != 1
            or filter_sources.get("indexSha256") != hashlib.sha256(filter_path.read_bytes()).hexdigest()
            or categories.get("schemaVersion") != 1 or not isinstance(categories.get("equips"), dict)):
        raise RuntimeError("编队编辑器礼装筛选索引或来源校验不完整")
    commands = resource / "base/image/CommandCards"
    portraits = document(commands / "manifest")
    if not isinstance(portraits.get("servants"), dict) or not portraits["servants"]:
        raise RuntimeError("战斗合成卡面打包头像清单不完整")
    for pack, region in (("base", "JP"), ("cn", "CN")):
        composition = document(resource / pack / "image/CommandCards/_composition/manifest")
        if (composition.get("region") != region
                or any(not isinstance(composition.get(key), dict) or not composition[key]
                       for key in ("shared", "np_names"))
                or not isinstance(composition.get("np_extra_names", {}), dict)):
            raise RuntimeError(f"战斗合成卡面打包清单不完整：{region}")
    sources = document(data / "sources.json")
    if not isinstance(sources.get("media"), list):
        raise RuntimeError("编队编辑器技能 / 御主礼装资源索引不完整")
    for entry in sources["media"]:
        if not isinstance(entry, dict):
            raise RuntimeError("编队编辑器技能 / 御主礼装资源记录无效")
        checked_asset(data / "media", entry.get("file"), entry)
    ui = document(data / "ui/manifest.json")
    np_cards = document(data / "np_cards.json")
    if (ui.get("schema") != 1 or not isinstance(ui.get("files"), dict) or not ui["files"]
            or np_cards.get("schema") != 1 or not isinstance(np_cards.get("assets"), dict) or not np_cards["assets"]):
        raise RuntimeError("编队编辑器随包 UI / 指令卡资源索引不完整")
    for filename, entry in ui["files"].items():
        checked_asset(data / "ui/media", filename, entry)
    for entry in np_cards["assets"].values():
        if not isinstance(entry, dict) or entry.get("pack") not in {"base", "cn"}:
            raise RuntimeError("编队编辑器指令卡资源包无效")
        checked_asset(resource / entry["pack"] / "image/CommandCards", entry.get("relative"), entry)


def install_deps():
    """安装 MaaFramework 依赖到 maafw 目录（MXU 要求的目录结构）

    MXU 要求将 MaaFramework 的 bin 文件夹内容解压到 maafw 文件夹中。
    参考: https://github.com/MistEO/MXU#依赖文件
    """

    # MaaFramework 运行库 → maafw/
    shutil.copytree(
        working_dir / "deps" / "bin",
        install_path / "maafw",
        ignore=shutil.ignore_patterns(
            "*MaaDbgControlUnit*",
            "*MaaThriftControlUnit*",
            "*MaaRpc*",
            "*MaaHttp*",
            "*.node",
            "*MaaPiCli*",
        ),
        dirs_exist_ok=True,
    )
    shutil.copytree(
        working_dir / "deps" / "share" / "MaaAgentBinary",
        install_path / "maafw" / "MaaAgentBinary",
        dirs_exist_ok=True,
    )


def install_resource():
    validate_editor_bundle(working_dir)
    # 配置 OCR 模型
    configure_ocr_model()

    # 确保 install_path 目录存在
    install_path.mkdir(parents=True, exist_ok=True)

    editor_static = working_dir / "assets" / "web" / "battle-editor"
    if not (editor_static / "index.html").is_file():
        raise RuntimeError("编队编辑器静态产物缺失，请先运行 npm --prefix web/battle-editor run build")
    shutil.copytree(editor_static, install_path / "web" / "battle-editor", dirs_exist_ok=True)

    # 复制 resource 目录
    shutil.copytree(
        working_dir / "assets" / "resource",
        install_path / "resource",
        dirs_exist_ok=True,
    )
    
    # 复制 options 目录（MaaFgo 特有）- 直接复制，不走 bbc_config_helper
    if (working_dir / "assets" / "options").exists():
        shutil.copytree(
            working_dir / "assets" / "options",
            install_path / "options",
            dirs_exist_ok=True,
        )
    
    if (working_dir / "assets" / "i18n").exists():
        shutil.copytree(
            working_dir / "assets" / "i18n",
            install_path / "i18n",
            dirs_exist_ok=True,
        )
    
    # 复制 tasks 目录（MaaFgo 特有）
    if (working_dir / "assets" / "tasks").exists():
        shutil.copytree(
            working_dir / "assets" / "tasks",
            install_path / "tasks",
            dirs_exist_ok=True,
        )
    
    # 复制 interface.json
    shutil.copy2(
        working_dir / "assets" / "interface.json",
        install_path,
    )
    
    # MXU: bbc_team_config.json 已统一放入 options 目录, 由上方 options 复制覆盖

    # 更新 interface.json 中的版本号和 mirrorchyan 配置
    with open(install_path / "interface.json", "r", encoding="utf-8") as f:
        interface = json.load(f)

    interface["version"] = version
    interface["mirrorchyan_rid"] = "MaaFgo"

    with open(install_path / "interface.json", "w", encoding="utf-8") as f:
        json.dump(interface, f, ensure_ascii=False, indent=4)


def install_chores():
    for file in ["README.md", "LICENSE", "打开编队编辑器.cmd"]:
        if (working_dir / file).exists():
            shutil.copy2(
                working_dir / file,
                install_path,
            )
    # 复制公告文件（MaaUI 欢迎页）
    announcement = working_dir / "assets" / "Announcement.md"
    if announcement.exists():
        shutil.copy2(announcement, install_path)

    editor_doc = working_dir / "docs" / "HTML编队与战斗逻辑工具使用说明.md"
    if editor_doc.is_file():
        (install_path / "docs").mkdir(parents=True, exist_ok=True)
        shutil.copy2(editor_doc, install_path / "docs" / editor_doc.name)


def install_agent():
    """复制 agent 目录并配置"""
    shutil.copytree(
        working_dir / "agent",
        install_path / "agent",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".pytest_cache"),
        dirs_exist_ok=True,
    )

    with open(install_path / "interface.json", "r", encoding="utf-8") as f:
        interface = json.load(f)

    # MXU 使用内嵌 Python，路径相对于 install-mxu 目录
    if sys.platform.startswith("win"):
        interface["agent"]["child_exec"] = r"./python/python.exe"
    elif sys.platform.startswith("darwin"):
        interface["agent"]["child_exec"] = r"./python/bin/python3"
    elif sys.platform.startswith("linux"):
        interface["agent"]["child_exec"] = r"python3"

    interface["agent"]["child_args"] = ["-u", r"./agent/main.py"]

    with open(install_path / "interface.json", "w", encoding="utf-8") as f:
        json.dump(interface, f, ensure_ascii=False, indent=4)


def install_bbcdll():
    """复制 bbcdll 目录（MaaFgo 特有）"""
    if (working_dir / "bbcdll").exists():
        shutil.copytree(
            working_dir / "bbcdll",
            install_path / "bbcdll",
            dirs_exist_ok=True,
        )


if __name__ == "__main__":
    install_deps()
    install_resource()
    install_chores()
    install_agent()
    install_bbcdll()  # 复制 bbcdll 目录（MaaFgo 特有）
    validate_editor_bundle(install_path, packaged=True)

    print(f"Install MXU to {install_path} successfully.")
