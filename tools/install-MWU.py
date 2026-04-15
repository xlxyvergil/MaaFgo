from pathlib import Path

import shutil
import sys

try:
    import jsonc
except ModuleNotFoundError as e:
    raise ImportError(
        "Missing dependency 'json-with-comments' (imported as 'jsonc').\n"
        f"Install it with:\n  {sys.executable} -m pip install json-with-comments\n"
        "Or add it to your project's requirements."
    ) from e

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

    configure_ocr_model()

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
        # 复制 options 目录，但排除 Avalonia 版本的 bbc_team_config.json
        shutil.copytree(
            working_dir / "assets" / "options",
            install_path / "options",
            ignore=shutil.ignore_patterns("bbc_team_config-Avalonia.json"),
            dirs_exist_ok=True,
        )
        # 将 bbc_team_config-MWU.json 重命名为 bbc_team_config.json
        mwu_config = install_path / "options" / "bbc_team_config-MWU.json"
        target_config = install_path / "options" / "bbc_team_config.json"
        if mwu_config.exists():
            shutil.move(str(mwu_config), str(target_config))
        # 删除 MWU artifact 中可能存在的 Avalonia 配置文件
        avalonia_config = install_path / "options" / "bbc_team_config-Avalonia.json"
        if avalonia_config.exists():
            avalonia_config.unlink()
            print(f"Removed Avalonia config: {avalonia_config}")
    if (working_dir / "assets" / "i18n").exists():
        shutil.copytree(
            working_dir / "assets" / "i18n",
            install_path / "i18n",
            dirs_exist_ok=True,
        )

    with open(install_path / "interface.json", "r", encoding="utf-8") as f:
        interface = jsonc.load(f)

    interface["version"] = version

    # 设置 agent 使用内置 Python
    if os_name == "win":
        interface["agent"]["child_exec"] = r"./python/python.exe"
    elif os_name == "macos":
        interface["agent"]["child_exec"] = r"./python/bin/python3"
    else:
        interface["agent"]["child_exec"] = r"python3"

    with open(install_path / "interface.json", "w", encoding="utf-8") as f:
        jsonc.dump(interface, f, ensure_ascii=False, indent=4)


def install_chores():
    shutil.copy2(
        working_dir / "README.md",
        install_path,
    )
    shutil.copy2(
        working_dir / "LICENSE",
        install_path,
    )


def install_agent_deps():
    """将 site-packages 中的 cv2 等库移动到 agent/libs/"""
    libs_dir = install_path / "agent" / "libs"
    libs_dir.mkdir(parents=True, exist_ok=True)
    site_packages = install_path / "python" / "Lib" / "site-packages"

    print(f"Moving dependencies from site-packages to {libs_dir}...")
    for item in site_packages.iterdir():
        # 移动 cv2, numpy, PIL 等导航所需的库
        if item.name.startswith(("cv2", "numpy", "PIL", "pillow")):
            dest = libs_dir / item.name
            if dest.exists():
                shutil.rmtree(dest) if dest.is_dir() else dest.unlink()
            shutil.move(str(item), str(dest))
            print(f"  Moved: {item.name}")

def install_agent():
    # 复制 agent 目录，但排除 Avalonia 版本文件
    shutil.copytree(
        working_dir / "agent",
        install_path / "agent",
        ignore=shutil.ignore_patterns("main-Avalonia.py", "bbc_action-Avalonia.py"),
        dirs_exist_ok=True,
    )
    
    # MWU 特殊处理：将 bbc_action-mwu.py 覆盖为 bbc_action.py
    mwu_bbc = install_path / "agent" / "custom" / "bbc_action-mwu.py"
    target_bbc = install_path / "agent" / "custom" / "bbc_action.py"
    if mwu_bbc.exists():
        shutil.move(str(mwu_bbc), str(target_bbc))
        print(f"MWU: Moved bbc_action-mwu.py to bbc_action.py")


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
    install_agent_deps()  # 新增：安装 Agent 依赖到 libs/
    install_agent()
    install_bbcdll()
    install_tasks()

    print(f"Install to {install_path} successfully.")
