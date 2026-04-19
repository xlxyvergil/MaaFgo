"""
Chaldea 本地名称数据库更新工具

从 Atlas Academy API 下载国服从者和礼装的名称数据，
保存为 agent/data/ 下的本地 JSON 文件。

运行一次即可建立/更新本地数据库，之后转换器无需网络即可查询名称。

用法:
    python tools/update_chaldea_data.py
    python tools/update_chaldea_data.py --region JP   # 切换服务器（默认 CN）
"""

import json
import ssl
import sys
import os
import argparse
import urllib.request

# 强制 stdout 使用 UTF-8，避免 Windows GBK 终端 UnicodeEncodeError
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ATLAS_API = "https://api.atlasacademy.io"

# 输出目录：agent/data/
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_TOOLS_DIR, "..", "agent", "data")


def fetch_json(url: str, timeout: int = 60) -> list | dict:
    """下载 JSON，禁用 SSL 验证（兼容国内网络环境）"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": "MaaFgo/1.0"})
    print(f"  正在请求: {url}")
    resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
    return json.loads(resp.read().decode("utf-8"))


def update_servant_names(region: str = "CN") -> int:
    """
    下载从者名称并保存到 servant_names_{region}.json

    格式: { "svtId(int字符串)": "中文名" }
    键为字符串便于 JSON 标准兼容，读取时转 int。
    """
    url = f"{ATLAS_API}/export/{region}/nice_servant.json"
    data = fetch_json(url)

    result: dict[str, str] = {}
    for svt in data:
        svt_id = svt.get("id")
        name = svt.get("name")
        if svt_id is not None and name:
            result[str(svt_id)] = name

    out_path = os.path.join(DATA_DIR, f"servant_names_{region}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, sort_keys=True)

    print(f"  [OK] 从者名称: {len(result)} 条 -> {out_path}")
    return len(result)


def update_equip_names(region: str = "CN") -> int:
    """
    下载礼装（概念礼装/助战礼装）名称并保存到 equip_names_{region}.json

    格式: { "ceId(int字符串)": "礼装名称" }
    """
    url = f"{ATLAS_API}/export/{region}/nice_equip.json"
    data = fetch_json(url)

    result: dict[str, str] = {}
    for eq in data:
        eq_id = eq.get("id")
        name = eq.get("name")
        if eq_id is not None and name:
            result[str(eq_id)] = name

    out_path = os.path.join(DATA_DIR, f"equip_names_{region}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, sort_keys=True)

    print(f"  [OK] 礼装名称: {len(result)} 条 -> {out_path}")
    return len(result)


def main():
    parser = argparse.ArgumentParser(
        description="从 Atlas Academy API 更新本地 Chaldea 名称数据库"
    )
    parser.add_argument(
        "--region",
        default="CN",
        choices=["CN", "JP", "NA", "TW", "KR"],
        help="服务器区域（默认 CN）",
    )
    args = parser.parse_args()
    region = args.region.upper()

    os.makedirs(DATA_DIR, exist_ok=True)
    print(f"\n[Chaldea 数据库更新] 区域: {region}\n")

    errors = []

    try:
        update_servant_names(region)
    except Exception as e:
        print(f"  [FAIL] 从者名称下载失败: {e}")
        errors.append("servant")

    try:
        update_equip_names(region)
    except Exception as e:
        print(f"  [FAIL] 礼装名称下载失败: {e}")
        errors.append("equip")

    if errors:
        print(f"\n[WARN] 部分数据更新失败: {errors}")
        sys.exit(1)
    else:
        print(f"\n[OK] 本地数据库已更新完成（{DATA_DIR}）")


if __name__ == "__main__":
    main()
