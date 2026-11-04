"""统一助战英灵选择的显示：职介简称(英文)、图鉴编号倒序。

仅更新已有候选的 label 和顺序，不增删从者、不修改内部 name、默认值、
图标、等级/技能选项或 pipeline_override。数据更新后可再次运行；--check 只检查。
"""
import argparse
import copy
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLASS_KEYS = {
    "剑士": "saber", "弓兵": "archer", "枪兵": "lancer", "骑兵": "rider",
    "魔术师": "caster", "暗杀者": "assassin", "狂战士": "berserker",
    "裁定者": "ruler", "复仇者": "avenger", "月之癌": "moonCancer",
    "他人格": "alterEgo", "降临者": "foreigner", "伪装者": "pretender",
    "盾兵": "shielder", "兽": "beast",
}


def build_class_options(document, class_cn):
    updated = copy.deepcopy(document)
    for case in updated["option"]["英灵选择"]["cases"]:
        # “不要求英灵”保持第一项；兽类沿用原来合并的游戏职介，不拆运行时键。
        if case["name"] == "不要求英灵":
            continue
        case["label"] = class_cn[CLASS_KEYS[case["name"]]]
    return updated


def build_servant_options(document, option_key, servants):
    updated = copy.deepcopy(document)
    choices = updated["option"][option_key]["cases"]
    numbers = {}
    for case in choices:
        sid = case["name"]
        record = servants.get(sid)
        if record is None:
            raise ValueError(f"{option_key}: 从者 {sid} 缺少目录记录")
        number = record.get("collection_no")
        if number is not None and (type(number) is not int or number <= 0):
            raise ValueError(f"{option_key}: 从者 {sid} 缺少有效图鉴编号")
        # 旧 NPC/特殊条目没有图鉴号：不编造编号、不删除已有候选，明确显示内部 ID 并置底。
        numbers[sid] = number if number is not None else -1
        # 保留原选项名称的译名（可能与目录译名不同），并允许目录修正图鉴编号。
        prefix = f"{number}. " if number is not None else f"ID {sid}. "
        name = re.sub(r"^(?:ID )?\d+\. ", "", case["label"])
        case["label"] = prefix + name
    choices.sort(key=lambda case: numbers[case["name"]], reverse=True)
    return updated


def build_documents(root=ROOT):
    catalog = json.loads((root / "agent/custom/servant_list.json").read_text(encoding="utf-8"))
    servants = {str(item["id"]): item for item in catalog["servants"]}
    directory = root / "assets/options/support"
    class_path = directory / "英灵选择.json"
    result = {class_path: build_class_options(
        json.loads(class_path.read_text(encoding="utf-8")), catalog["class_cn"])}
    for path in sorted((directory / "英灵选择").glob("*.json")):
        result[path] = build_servant_options(
            json.loads(path.read_text(encoding="utf-8")), path.stem, servants)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只检查显示名称及顺序，不写入文件")
    args = parser.parse_args()
    stale = []
    # 先完成所有文件的目录检查，避免数据缺失时只更新一半文件。
    for path, expected in build_documents().items():
        original = json.loads(path.read_text(encoding="utf-8"))
        if original == expected:
            continue
        stale.append(path.relative_to(ROOT).as_posix())
        if not args.check:
            path.write_text(json.dumps(expected, ensure_ascii=False, indent=4) + "\n",
                            encoding="utf-8", newline="\n")
    if args.check and stale:
        raise SystemExit("请运行 tools/generate_support_servant_options.py 更新: " + ", ".join(stale))
    print(f"{'待更新' if args.check else '已更新'} {len(stale)} 个助战选项文件")


if __name__ == "__main__":
    main()
