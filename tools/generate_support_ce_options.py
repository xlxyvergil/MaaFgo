"""从正式礼装模板生成各助战槽位的单选/多选选项，保留旧单选键。"""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SLOTS = ("普通助战-礼装", "冠位助战-1号礼装", "冠位助战-2号礼装")
STATUSES = ("满破", "非满破")


def build_options(slot, assets):
    path = assets / "options/support" / f"{slot}.json"
    original = json.loads(path.read_text(encoding="utf-8"))["option"]
    options = {}
    for status in STATUSES:
        key = f"{slot}-{status}"
        # 保留 scan_select 类型和键名，已有任务不会被客户端重置。
        options[key] = original[key]
        mode_key = f"{key}-选择方式"
        candidates_key = f"{key}-候选"
        label = original[key]["label"]
        options[mode_key] = {
            "type": "select", "label": f"{label}选择方式", "default_case": "单选",
            "cases": [{
                "name": mode, "label": mode,
                "option": [key if mode == "单选" else candidates_key],
                "pipeline_override": {"助战action": {"attach": {f"{key}-多选": mode == "多选"}}},
            } for mode in ("单选", "多选")],
        }
        folder = assets / "resource/base/image/lizhuang" / status
        files = sorted((p for p in folder.glob("*.png") if p.is_file() and p.name not in ("0_空.png", "空.png")),
                       key=lambda p: p.name)
        if not files:
            raise ValueError(f"没有礼装模板: {folder}")
        options[candidates_key] = {
            "type": "checkbox", "label": f"{label}候选（可多选）",
            "description": "同一槽位任意一个候选命中即通过；不勾选表示不限此格礼装。冠位助战两个槽位分别匹配，各自均需通过。",
            "default_case": [],
            "cases": [{
                "name": p.name, "label": p.stem,
                "icon": p.relative_to(assets).as_posix(),
                # attach 的数组/嵌套对象可能被后一个 case 整体替换，使用独立键累积。
                "pipeline_override": {"助战action": {"attach": {f"{key}-候选:{p.name}": True}}},
            } for p in files],
        }
    return {"option": options}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只检查生成结果是否与模板一致")
    args = parser.parse_args()
    assets = ROOT / "assets"
    stale = []
    for slot in SLOTS:
        path = assets / "options/support" / f"{slot}.json"
        expected = build_options(slot, assets)
        if args.check:
            if json.loads(path.read_text(encoding="utf-8")) != expected:
                stale.append(str(path.relative_to(ROOT)))
        else:
            path.write_text(json.dumps(expected, ensure_ascii=False, indent=4) + "\n", encoding="utf-8", newline="\n")
    if stale:
        raise SystemExit("请运行 tools/generate_support_ce_options.py 更新: " + ", ".join(stale))


if __name__ == "__main__":
    main()
