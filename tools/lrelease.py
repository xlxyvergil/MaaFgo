#   This file is part of MFW-ChainFlow Assistant.
#
#   MFW-ChainFlow Assistant is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published
#   by the Free Software Foundation, either version 3 of the License,
#   or (at your option) any later version.
#
#   MFW-ChainFlow Assistant is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty
#   of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See
#   the GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with MFW-ChainFlow Assistant. If not, see <https://www.gnu.org/licenses/>.
#
#   Contact: err.overflow@gmail.com
#   Copyright (C) 2024-2025  MFW-ChainFlow Assistant. All rights reserved.

"""
MFW-ChainFlow Assistant
MFW-ChainFlow Assistant i18n 翻译脚本
自动将 app/i18n 文件夹内的所有 .ts 文件转换为 .qm 文件
作者:overflow65537
"""

import os
import subprocess
import json
import glob
import shutil
from pathlib import Path

def find_pyside6_tool(tool_name):
    """查找 PySide6 工具的位置"""
    # 首先尝试在 PATH 中查找
    tool_path = shutil.which(tool_name)
    if tool_path:
        return tool_path
    
    # 尝试查找 PySide6 安装目录
    try:
        import PySide6
        pyside6_path = Path(PySide6.__file__).parent
        # Windows
        if os.name == "nt":
            tool_exe = pyside6_path / f"{tool_name}.exe"
            if tool_exe.exists():
                return str(tool_exe)
        # Linux/Mac
        else:
            tool_path = pyside6_path / tool_name
            if tool_path.exists():
                return str(tool_path)
    except ImportError:
        pass
    
    return None

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
        print("[ERROR] can't find project root (can't find main.py）")
        print(f"  current working directory: {os.getcwd()}")
        print(f"  script directory: {script_dir}")
        exit(1)

# 切换到项目根目录
os.chdir(project_root)
print(f"[INFO] working directory has been set to: {os.getcwd()}")

# 获取当前脚本所在的目录（用于查找 i18n.json）
current_dir = script_dir
# 定义 i18n.json 文件路径（在 tools/ 目录下）
i18n_json_path = os.path.join(current_dir, "i18n.json")

# 初始化 lrelease 路径，优先使用 lrelease（PySide6 中的实际工具名）
lrelease_path = find_pyside6_tool("lrelease")
if not lrelease_path:
    # 如果找不到 lrelease，尝试 pyside6-lrelease（向后兼容）
    lrelease_path = find_pyside6_tool("pyside6-lrelease") or "lrelease"

# 尝试读取 i18n.json 文件
if os.path.exists(i18n_json_path):
    try:
        with open(i18n_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if "lrelease" in data:
                lrelease_path = data["lrelease"]
                # 如果是文件夹
                if os.path.isdir(lrelease_path):
                    # Windows 使用 .exe，Linux/Mac 不使用
                    if os.name == "nt":
                        lrelease_path = os.path.join(lrelease_path, "lrelease.exe")
                    else:
                        lrelease_path = os.path.join(lrelease_path, "lrelease")
    except Exception as e:
        print(f"Error reading i18n.json file: {e}")

# 定义 i18n 文件夹路径（相对于项目根目录）
i18n_dir = os.path.join(project_root, "app", "i18n")

# 检查 i18n 文件夹是否存在
if not os.path.exists(i18n_dir):
    print(f"Error: i18n folder not found: {i18n_dir}")
    exit(1)

# 自动查找所有 .ts 文件
ts_files = glob.glob(os.path.join(i18n_dir, "*.ts"))

if not ts_files:
    print(f"No .ts files found in {i18n_dir}")
    exit(0)

print(f"Found {len(ts_files)} .ts files:")
for ts_file in ts_files:
    print(f"  - {os.path.basename(ts_file)}")

print()
print(f"Using tool: {lrelease_path}")
print("-" * 60)

# 遍历每个 .ts 文件
success_count = 0
for ts_file in ts_files:
    # 构建对应的 .qm 文件路径（同名）
    qm_file = os.path.splitext(ts_file)[0] + ".qm"
    ts_file_name = os.path.basename(ts_file)
    qm_file_name = os.path.basename(qm_file)

    try:
        # 调用 lrelease 进行转换
        result = subprocess.run(
            [lrelease_path, ts_file],
            check=True,
            capture_output=True,
            text=True
        )
        print(f"[Success] {ts_file_name} -> {qm_file_name}")
        success_count += 1
    except subprocess.CalledProcessError as e:
        print(f"[Failed] {ts_file_name}: {e}")
        if e.stderr:
            print(f"  Error message: {e.stderr}")
    except FileNotFoundError:
        print(f"[Failed] Tool not found: {lrelease_path}")
        print("  Hint: Please ensure PySide6 is installed")
        print("  Can be resolved by:")
        print("  1. Ensure PySide6 is properly installed: pip install PySide6")
        print("  2. Specify tool path in i18n.json:")
        print('     {"lrelease": "full_path\\lrelease.exe"}')
        break

print("-" * 60)
print(f"Conversion completed: {success_count}/{len(ts_files)} files successful")

