import os
import sys
import urllib.request
import zipfile
from pathlib import Path

def setup_embed_python(target_dir, arch="amd64", version="3.12.10"):
    """
    下载并配置嵌入式 Python 环境
    :param target_dir: 目标安装目录 (如 build/python 或 install/python)
    :param arch: 架构 (amd64 或 arm64)
    :param version: Python 版本
    """
    target_path = Path(target_dir)
    target_path.mkdir(parents=True, exist_ok=True)
    
    print(f"Setting up embedded Python {version} ({arch}) in {target_path}...")
    
    # 1. 下载
    zip_name = f"python-{version}-embed-{arch}.zip"
    url = f"https://www.python.org/ftp/python/{version}/{zip_name}"
    zip_path = target_path / zip_name
    
    if not zip_path.exists():
        print(f"Downloading from {url}...")
        urllib.request.urlretrieve(url, zip_path)
    
    # 2. 解压
    print("Extracting...")
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(target_path)
    zip_path.unlink() # 删除压缩包
    
    # 3. 配置 python3x._pth
    pth_files = list(target_path.glob("python*._pth"))
    if pth_files:
        pth_file = pth_files[0]
        content = pth_file.read_text(encoding="utf-8")
        
        # 启用 site-packages
        content = content.replace("#import site", "import site")
        
        # 确保包含必要路径
        required_paths = [".", "Lib", "Lib\\site-packages"]
        for p in required_paths:
            if p not in content.splitlines():
                content += f"\n{p}"
                
        pth_file.write_text(content, encoding="utf-8")
        print(f"Configured {pth_file.name}")
    
    # 4. 安装 pip
    py_exe = target_path / "python.exe"
    get_pip_script = target_path / "get-pip.py"
    
    if not get_pip_script.exists():
        print("Downloading get-pip.py...")
        urllib.request.urlretrieve("https://bootstrap.pypa.io/get-pip.py", get_pip_script)
    
    print("Installing pip...")
    os.system(f'"{py_exe}" "{get_pip_script}" --no-warn-script-location')
    get_pip_script.unlink()
    
    print("Embedded Python setup complete.")

if __name__ == "__main__":
    # 默认参数，CI 调用时可以传参
    # usage: python setup_embed_python.py <target_dir> <arch>
    target = sys.argv[1] if len(sys.argv) > 1 else "install/python"
    arch = sys.argv[2] if len(sys.argv) > 2 else "amd64"
    setup_embed_python(target, arch)
