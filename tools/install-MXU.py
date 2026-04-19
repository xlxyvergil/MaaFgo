from pathlib import Path

import shutil
import sys
import json
import os

script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(script_dir)

from configure import configure_ocr_model

working_dir = Path(__file__).parent.parent
install_path = working_dir / Path("install-mxu")
version = len(sys.argv) > 1 and sys.argv[1] or "v0.0.1"


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
    # 配置 OCR 模型
    configure_ocr_model()

    # 确保 install_path 目录存在
    install_path.mkdir(parents=True, exist_ok=True)

    # 复制 resource 目录
    shutil.copytree(
        working_dir / "assets" / "resource",
        install_path / "resource",
        dirs_exist_ok=True,
    )
    
    # 复制 options 和 i18n 目录（MaaFgo 特有）
    if (working_dir / "assets" / "options").exists():
        # 复制 options 目录，但排除 bbc_team_config.json
        for item in (working_dir / "assets" / "options").iterdir():
            if item.name == "bbc_team_config.json":
                continue  # 跳过 bbc_team_config.json
            if item.is_dir():
                shutil.copytree(item, install_path / "options" / item.name, dirs_exist_ok=True)
            else:
                shutil.copy2(item, install_path / "options" / item.name)
        
        # 复制 bbc_team_config_nomwu.json 并重命名为 bbc_team_config.json
        nomwu_config = working_dir / "assets" / "options" / "bbc_team_config_nomwu.json"
        if nomwu_config.exists():
            shutil.copy2(nomwu_config, install_path / "options" / "bbc_team_config.json")
    
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
    
    # 复制 restart_mfa.exe 到根目录
    if (working_dir / "assets" / "restart_mfa.exe").exists():
        shutil.copy2(
            working_dir / "assets" / "restart_mfa.exe",
            install_path,
        )
    
    # 复制 restart_config.json 并修改 target_exe 为 MaaFgo.exe
    if (working_dir / "assets" / "restart_config.json").exists():
        with open(working_dir / "assets" / "restart_config.json", 'r', encoding='utf-8') as f:
            config = json.load(f)
        config['target_exe'] = 'MaaFgo.exe'
        with open(install_path / "restart_config.json", 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

    # 更新 interface.json 中的版本号和 mirrorchyan 配置
    with open(install_path / "interface.json", "r", encoding="utf-8") as f:
        interface = json.load(f)

    interface["version"] = version
    interface["mirrorchyan_rid"] = "MaaFgo-MXU"
    interface["mirrorchyan_multiplatform"] = True

    with open(install_path / "interface.json", "w", encoding="utf-8") as f:
        json.dump(interface, f, ensure_ascii=False, indent=4)


def install_chores():
    for file in ["README.md", "LICENSE"]:
        if (working_dir / file).exists():
            shutil.copy2(
                working_dir / file,
                install_path,
            )


def install_agent():
    """复制 agent 目录并配置"""
    shutil.copytree(
        working_dir / "agent",
        install_path / "agent",
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

    print(f"Install MXU to {install_path} successfully.")
