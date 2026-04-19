from pathlib import Path

import shutil
import sys
import subprocess
import os
import urllib.request
import json

from configure import configure_ocr_model


working_dir = Path(__file__).parent.parent.resolve()
install_path = working_dir / Path("install")
version = len(sys.argv) > 1 and sys.argv[1] or "v0.0.1"

# the first parameter is self name
if sys.argv.__len__() < 4:
    print("Usage: python install.py <version> <os> <arch>")
    print("Example: python install.py v1.0.0 win x86_64")
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

    configure_ocr_model()

    # 确保 install_path 目录存在
    install_path.mkdir(parents=True, exist_ok=True)

    shutil.copytree(
        working_dir / "assets" / "resource",
        install_path / "resource",
        dirs_exist_ok=True,
    )
    shutil.copy2(
        working_dir / "assets" / "interface.json",
        install_path,
    )

    # Copy options and i18n directories
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
    
    # 复制 restart_mfa.exe 和 restart_config.json 到根目录
    if (working_dir / "assets" / "restart_mfa.exe").exists():
        shutil.copy2(
            working_dir / "assets" / "restart_mfa.exe",
            install_path,
        )
    if (working_dir / "assets" / "restart_config.json").exists():
        shutil.copy2(
            working_dir / "assets" / "restart_config.json",
            install_path,
        )

    with open(install_path / "interface.json", "r", encoding="utf-8") as f:
        interface = json.load(f)

    interface["version"] = version

    # 设置 agent 使用内置 Python
    if os_name == "win":
        interface["agent"]["child_exec"] = r"./python/python.exe"
    elif os_name == "macos":
        interface["agent"]["child_exec"] = r"./python/bin/python3"
    else:
        interface["agent"]["child_exec"] = r"python3"

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


def setup_embedded_python():
    """M9A 模式：依赖由 CI 工作流通过 pip install -r requirements.txt 安装到嵌入式 Python"""
    py_dir = install_path / "python"
    if not py_dir.exists():
        print("Error: Python directory not found in install. Ensure CI prepares it first.")
    else:
        # 确保 get-pip.py 存在，如果不存在则下载
        get_pip_path = py_dir / "get-pip.py"
        if not get_pip_path.exists():
            print("Downloading get-pip.py...")
            import urllib.request
            urllib.request.urlretrieve("https://bootstrap.pypa.io/get-pip.py", str(get_pip_path))
        
        # 执行 pip 安装
        python_exe = py_dir / "python.exe" if os_name == "win" else py_dir / "bin" / "python3"
        subprocess.run([str(python_exe), str(get_pip_path)], check=True)
        subprocess.run([str(python_exe), "-m", "pip", "install", "--upgrade", "pip"], check=True)
        subprocess.run([str(python_exe), "-m", "pip", "install", "-r", str(working_dir / "requirements.txt")], check=True)


def install_agent():
    # 复制 agent 目录
    shutil.copytree(
        working_dir / "agent",
        install_path / "agent",
        dirs_exist_ok=True,
    )


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


if __name__ == "__main__":
    install_deps()
    install_resource()
    install_chores()
    setup_embedded_python()  # M9A 模式：在构建时安装依赖到嵌入式 Python
    install_agent()
    install_bbcdll()
    install_tasks()

    print(f"Install to {install_path} successfully.")
