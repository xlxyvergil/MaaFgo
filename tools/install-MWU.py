from pathlib import Path

import shutil
import sys
import subprocess
import os
import urllib.request
import json

from configure import configure_ocr_model


working_dir = Path(__file__).parent.parent.resolve()
install_path = working_dir / Path("build")
version = len(sys.argv) > 1 and sys.argv[1] or "v0.0.1"

# the first parameter is self name
if sys.argv.__len__() < 4:
    print("Usage: python install-MWU.py <version> <os> <arch>")
    print("Example: python install-MWU.py v1.0.0 win x86_64")
    sys.exit(1)

os_name = sys.argv[2]
arch = sys.argv[3]


def get_dotnet_platform_tag():
    """自动检测当前平台并返回对应的dotnet平台标签"""
    if os_name == "win" and arch == "x86_64":
        platform_tag = "win-x64"
    elif os_name == "win" and arch == "aarch64":
        platform_tag = "win-arm64"
    elif os_name == "macos" and arch == "x86_64":
        platform_tag = "osx-x64"
    elif os_name == "macos" and arch == "aarch64":
        platform_tag = "osx-arm64"
    elif os_name == "linux" and arch == "x86_64":
        platform_tag = "linux-x64"
    elif os_name == "linux" and arch == "aarch64":
        platform_tag = "linux-arm64"
    else:
        print("Unsupported OS or architecture.")
        print("available parameters:")
        print("version: e.g., v1.0.0")
        print("os: [win, macos, linux, android]")
        print("arch: [aarch64, x86_64]")
        sys.exit(1)

    return platform_tag


def install_deps():
    if not (working_dir / "deps" / "bin").exists():
        print('Please download the MaaFramework to "deps" first.')
        sys.exit(1)

    if os_name == "android":
        shutil.copytree(
            working_dir / "deps" / "bin",
            install_path,
            dirs_exist_ok=True,
        )
        shutil.copytree(
            working_dir / "deps" / "share" / "MaaAgentBinary",
            install_path / "MaaAgentBinary",
            dirs_exist_ok=True,
        )
    else:
        shutil.copytree(
            working_dir / "deps" / "bin",
            install_path / "runtimes" / get_dotnet_platform_tag() / "native",
            ignore=shutil.ignore_patterns(
                "*MaaDbgControlUnit*",
                "*MaaThriftControlUnit*",
                "*MaaRpc*",
                "*MaaHttp*",
                "plugins",
                "*.node",
                "*MaaPiCli*",
            ),
            dirs_exist_ok=True,
        )
        shutil.copytree(
            working_dir / "deps" / "share" / "MaaAgentBinary",
            install_path / "libs" / "MaaAgentBinary",
            dirs_exist_ok=True,
        )
        shutil.copytree(
            working_dir / "deps" / "bin" / "plugins",
            install_path / "plugins" / get_dotnet_platform_tag(),
            dirs_exist_ok=True,
        )


def install_resource():
    # 配置 OCR 模型
    configure_ocr_model()

    # Copy options and i18n directories (这些是我们特有的目录)
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
    
    # MWU: 复制 bbc_team_config.json 到根目录
    if (working_dir / "assets" / "bbc_team_config.json").exists():
        shutil.copy2(
            working_dir / "assets" / "bbc_team_config.json",
            install_path / "bbc_team_config.json",
        )

    # 更新 interface.json 中的版本号和 agent 配置
    with open(install_path / "interface.json", "r", encoding="utf-8") as f:
        interface = json.load(f)

    interface["version"] = version
    # 保持黑魔法模式的配置
    interface["agent"] = {
        "child_exec": "python",
        "child_args": [
            "./agent/main.py"
        ]
    }

    with open(install_path / "interface.json", "w", encoding="utf-8") as f:
        json.dump(interface, f, ensure_ascii=False, indent=2)


def install_chores():
    shutil.copy2(
        working_dir / "README.md",
        install_path,
    )
    shutil.copy2(
        working_dir / "LICENSE",
        install_path,
    )
    # 复制公告文件（MaaUI 欢迎页）
    announcement = working_dir / "assets" / "Announcement.md"
    if announcement.exists():
        shutil.copy2(announcement, install_path)


def install_bbcdll():
    """复制 bbcdll 目录"""
    shutil.copytree(
        working_dir / "bbcdll",
        install_path / "bbcdll",
        dirs_exist_ok=True,
    )


def install_tasks():
    """复制 tasks 目录"""
    if (working_dir / "assets" / "tasks").exists():
        shutil.copytree(
            working_dir / "assets" / "tasks",
            install_path / "tasks",
            dirs_exist_ok=True,
        )


def fix_cv2_path():
    """修复 cv2 模块路径：从 deps/cv2 移动到根目录"""
    cv2_src = install_path / "deps" / "cv2"
    cv2_dst = install_path / "cv2"
    
    if cv2_src.exists() and not cv2_dst.exists():
        print(f"Moving cv2 from {cv2_src} to {cv2_dst}")
        shutil.move(str(cv2_src), str(cv2_dst))


if __name__ == "__main__":
    install_deps()
    install_resource()
    install_chores()
    install_bbcdll()  # 复制 bbcdll 目录
    install_tasks()  # 复制 tasks 目录
    fix_cv2_path()  # 修复 cv2 模块路径

    print(f"Install to {install_path} successfully.")
