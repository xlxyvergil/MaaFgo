import subprocess
import sys
import os

def main():
    # 定义需要下载的包
    packages = [
        "maafw",
        "maaagentbinary",
        "opencv-python",
        "numpy"
    ]
    
    # 定义下载目标目录
    target_dir = os.path.join(os.path.dirname(__file__), "..", "deps", "python_packages")
    os.makedirs(target_dir, exist_ok=True)
    
    print(f"Downloading dependencies to {target_dir}...")
    
    # 构建 pip download 命令
    # --only-binary=:all: 确保只下载预编译包，不下载源码
    cmd = [
        sys.executable, "-m", "pip", "download",
        *packages,
        "-d", target_dir,
        "--only-binary=:all:",
        "--platform", "win_amd64", # 默认下载 x86_64，CI 中会根据矩阵覆盖
        "--python-version", "312",
        "--abi", "cp312"
    ]
    
    # 如果 CI 传入了特定的平台参数，则覆盖默认值
    if len(sys.argv) > 1:
        platform = sys.argv[1]
        arch = sys.argv[2] if len(sys.argv) > 2 else "x86_64"
        
        if platform == "win":
            plat_tag = "win_amd64" if arch == "x86_64" else "win_arm64"
            cmd = [
                sys.executable, "-m", "pip", "download",
                *packages,
                "-d", target_dir,
                "--only-binary=:all:",
                "--platform", plat_tag,
                "--python-version", "312",
                "--abi", "cp312"
            ]

    try:
        subprocess.check_call(cmd)
        print("Dependencies downloaded successfully.")
    except subprocess.CalledProcessError as e:
        print(f"Platform specific download failed: {e}")
        
        # 如果是 ARM64 且包含 opencv-python，尝试剔除它再试一次
        if "aarch64" in sys.argv and "opencv-python" in packages:
            print("Detected ARM64 architecture. Removing opencv-python from download list as it lacks pre-built wheels.")
            packages.remove("opencv-python")
            
            fallback_cmd = [
                sys.executable, "-m", "pip", "download",
                *packages,
                "-d", target_dir,
                "--only-binary=:all:"
            ]
            
            try:
                subprocess.check_call(fallback_cmd)
                print("Dependencies downloaded successfully (without opencv-python).")
                return
            except subprocess.CalledProcessError as e2:
                print(f"Fallback also failed: {e2}")
                sys.exit(1)
        else:
            print(f"Failed to download dependencies: {e}")
            sys.exit(1)

if __name__ == "__main__":
    main()
