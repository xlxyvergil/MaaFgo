#   This file is part of MFW-ChainFlow Assistant.

#   MFW-ChainFlow Assistant is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published
#   by the Free Software Foundation, either version 3 of the License,
#   or (at your option) any later version.

#   MFW-ChainFlow Assistant is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty
#   of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See
#   the GNU General Public License for more details.

#   You should have received a copy of the GNU General Public License
#   along with MFW-ChainFlow Assistant. If not, see <https://www.gnu.org/licenses/>.

#   Contact: err.overflow@gmail.com
#   Copyright (C) 2024-2025  MFW-ChainFlow Assistant. All rights reserved.

"""
MFW-ChainFlow Assistant
MFW-ChainFlow Assistant 打包脚本
作者:overflow65537
"""
import PyInstaller.__main__
import os
import site
import shutil
import sys
from pathlib import Path

# === 确保从项目根目录运行 ===
# 获取脚本所在目录
script_dir = Path(__file__).parent.absolute()
# 项目根目录应该是脚本目录的父目录（因为脚本在 tools/ 目录下）
project_root = script_dir.parent

# 检查是否在正确的目录（通过检查 main.py 是否存在）
if not (project_root / "main.py").exists():
    # 如果从项目根目录运行，project_root 就是当前目录
    if (Path.cwd() / "main.py").exists():
        project_root = Path.cwd()
    else:
        print("[ERROR] can't find project root (can't find main.py)")
        print(f"  current working directory: {os.getcwd()}")
        print(f"  script directory: {script_dir}")
        sys.exit(1)

# 切换到项目根目录
os.chdir(project_root)
print(f"[INFO] working directory has been set to: {os.getcwd()}")

# 删除dist
if os.path.exists(os.path.join(os.getcwd(), "dist", "MFW")):
    shutil.rmtree(os.path.join(os.getcwd(), "dist", "MFW"))

# 获取参数
# === 构建参数处理 ===
print("[INFO] Received command line arguments:", sys.argv)
if len(sys.argv) != 4:  # 参数校验：平台/架构/版本号
    sys.argv = [sys.argv[0], "win", "x86_64", "v1.0.0"]

platform = sys.argv[1]
architecture = sys.argv[2]
version = sys.argv[3]

# 写入版本号
with open(os.path.join(os.getcwd(), "app", "common", "__version__.py"), "w") as f:
    f.write(f'__version__ = "{version}"')


# === 依赖包路径发现 ===
def locate_package(package_name):
    """在 site-packages 中定位指定包的安装路径"""
    for path in site.getsitepackages():
        candidate = os.path.join(path, package_name)
        if os.path.exists(candidate):
            return candidate
    raise FileNotFoundError(f"Can't find {package_name} package")


try:
    # 核心依赖包定位
    maa_path = locate_package("maa")  # MAA 框架核心库
    agent_path = locate_package("MaaAgentBinary")  # 设备连接组件
    darkdetect_path = locate_package("darkdetect")  # 系统主题检测库
    strenum = locate_package("strenum")
except FileNotFoundError as e:
    print(f"[FATAL] Dependency missing: {str(e)}")
    sys.exit(1)

# === PyInstaller 配置生成 ===
base_command = [
    "main.py",
    "--name=MFW",
    "--clean",
    "--noconfirm",  # 禁用确认提示
    # 资源包含规则（格式：源路径{分隔符}目标目录）
    f"--add-data={maa_path}{os.pathsep}maa",
    f"--add-data={agent_path}{os.pathsep}MaaAgentBinary",
    f"--add-data={darkdetect_path}{os.pathsep}darkdetect",
    # 自动收集包数据
    "--collect-data=darkdetect",
    "--collect-data=maa",
    "--collect-data=MaaAgentBinary",
    "--collect-data=certifi",  # 收集 certifi 证书文件
    # 隐式依赖声明
    "--hidden-import=darkdetect",
    "--hidden-import=maa",
    "--hidden-import=MaaAgentBinary",
    "--hidden-import=certifi",  # 确保 certifi 模块被包含
]

# === 平台特定配置 准备阶段 ===
print(f"[DEBUG] Platform: {sys.platform}")

if sys.platform == "darwin":
    if architecture == "x86_64":  # intel CPU
        base_command += ["--target-arch=x86_64"]
        print("[DEBUG] Target arch: x86_64")
    elif architecture == "aarch64":  # M1/M2 CPU
        base_command += ["--target-arch=arm64"]
        print("[DEBUG] Target arch: aarch64")
    elif architecture == "universal2":
        base_command += ["--target-arch=universal2"]
        print("[DEBUG] Target arch: universal2")
    base_command += [
        "--osx-bundle-identifier=com.overflow65537.MFW",
        "--noconsole",  # 禁用控制台窗口
    ]

elif sys.platform == "win32":
    base_command += [
        "--icon=./app/assets/icons/logo.ico",
        "--distpath",
        os.path.join("dist"),
    ]
    if "ci" not in version:
        base_command += [
            "--noconsole",  # 禁用控制台窗口
        ]

elif sys.platform == "linux":
    base_command += [
        "--noconsole",
    ]  # 禁用控制台窗口
# === 开始构建 ===
print("[INFO] Starting MFW build")
print(f"\n\n[DEBUG] base_command: {base_command}\n\n")
PyInstaller.__main__.run(base_command)

# === 二进制文件处理 ===
# 收集 MAA 的本地库文件
bin_dir = os.path.join(maa_path, "bin")
bin_files = []
for f in os.listdir(bin_dir):
    print(f"[DEBUG] Found binary file: {f}")
    print(f"[DEBUG] Adding binary file: {os.path.join(bin_dir, f)}")
    bin_files.append(f)
    base_command += [f"--add-binary={os.path.join(bin_dir, f)}{os.pathsep}."]


# === 开始构建 ===
print("[INFO] Starting MFW build")
print(f"\n\n[DEBUG] base_command: {base_command}\n\n")
PyInstaller.__main__.run(base_command)

# === 构建后处理 ===
# 复制TEM_files的内容到 dist/MFW 目录
dist_dir = os.path.join(os.getcwd(), "dist", "MFW")
internal_dir = os.path.join(dist_dir, "_internal")
temp_files_dir = os.path.join(internal_dir, "TEM_files")
if os.path.isdir(temp_files_dir):
    shutil.copytree(temp_files_dir, dist_dir, dirs_exist_ok=True)
    shutil.rmtree(temp_files_dir)
else:
    print(f"[WARN] Temporary files directory not found: {temp_files_dir}")


for i in bin_files:
    src_binary = os.path.join(dist_dir, "_internal", i)
    dst_binary = os.path.join(dist_dir, i)
    if os.path.exists(src_binary):
        shutil.copy(src_binary, dst_binary)
        os.remove(src_binary)
    else:
        print(f"[WARN] Expected binary missing: {src_binary}")

maa_bin_internal = os.path.join(internal_dir, "maa", "bin")
if os.path.isdir(maa_bin_internal):
    shutil.rmtree(maa_bin_internal)

# 复制README和许可证
shutil.copy(
    os.path.join(os.getcwd(), "README.md"),
    os.path.join(os.getcwd(), "dist", "MFW", "MFW_README.md"),
)
shutil.copy(
    os.path.join(os.getcwd(), "README-en.md"),
    os.path.join(os.getcwd(), "dist", "MFW", "MFW_README-en.md"),
)
shutil.copy(
    os.path.join(os.getcwd(), "LICENSE"),
    os.path.join(os.getcwd(), "dist", "MFW", "MFW_LICENSE"),
)

os.makedirs(os.path.join(os.getcwd(), "dist", "MFW", "app", "i18n"), exist_ok=True)
# 复制i18n文件
for qm_file in ["i18n.zh_CN.qm", "i18n.zh_HK.qm"]:
    shutil.copy(
        os.path.join(os.getcwd(), "app", "i18n", qm_file),
        os.path.join(os.getcwd(), "dist", "MFW", "app", "i18n", qm_file),
    )

# === 构建updater ===
updater_command = [
    "updater.py",
    "--name=MFWUpdater",
    "--onefile",
    "--clean",
    "--noconfirm",  # 禁用确认提示
    "--distpath",
    os.path.join("dist", "MFW"),
]
PyInstaller.__main__.run(updater_command)


def generate_file_list(input_dir, output_file=None):
    """
    生成文件夹内所有文件的列表

    Args:
        input_dir (str): 输入文件夹路径
        output_file (str, optional): 输出文件路径，如果不提供则使用默认名称
    """
    # 转换为Path对象

    input_path = Path(input_dir)

    # 检查输入路径是否存在
    if not input_path.exists():
        print(f"Error: '{input_dir}' not found")
        return False

    if not input_path.is_dir():
        print(f"Error: '{input_dir}' is not a directory")
        return False

    # 如果没有指定输出文件，使用默认名称
    if output_file is None:
        output_file = f"{input_path.name}_file_list.txt"

    try:
        with open(output_file, "w", encoding="utf-8") as f:
            # 遍历目录树
            for root, dirs, files in os.walk(input_path):
                # 计算相对路径
                rel_root = os.path.relpath(root, input_path.parent)

                # 写入所有文件
                for file in files:
                    # 构建以./开头的相对路径
                    if rel_root == input_path.name:
                        # 如果是根目录，直接使用./文件名
                        file_path = f"./{file}"
                    else:
                        # 否则使用./目录/文件名
                        rel_dir = os.path.relpath(root, input_path)
                        if rel_dir == ".":
                            file_path = f"./{file}"
                        else:
                            file_path = f"./{rel_dir}/{file}"

                    f.write(file_path + "\n")
            # 写入file_list.txt自身的路径，保持格式一致
            f.write("./file_list.txt" + "\n")

        print(f"File list generated: {output_file}")
        print(
            f"Processed {sum([len(files) for _, _, files in os.walk(input_path)])} files"
        )
        return True

    except Exception as e:
        print(f"Error generating file list: {e}")
        return False


# 生成包含文件列表
generate_file_list(
    os.path.join("dist", "MFW"), os.path.join("dist", "MFW", "file_list.txt")
)
