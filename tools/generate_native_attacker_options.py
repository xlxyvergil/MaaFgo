"""Generate native battle servant pickers from the local servant catalog."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = "assets/options/原生自动战斗主输出从者.json"
CLASS_OPTION = "原生自动战斗主输出职阶"
CONFIG = "原生自动战斗-高级策略配置"
ATTACKER = "native_battle_preferred_attacker"
OPENING_OUTPUT = "assets/options/原生自动战斗开局等牌目标从者.json"
OPENING_CLASS_OPTION = "原生自动战斗开局等牌目标职阶"
OPENING_TARGET = "native_battle_opening_hand_selected_servant"
FACE_DIR = Path("resource/base/image/servant_face")

# Load the standalone alias rules without initializing Chaldea's network client.
sys.path.insert(0, str(ROOT / "agent/chaldea"))
from servant_aliases import canonical_servants


def build_document(root=ROOT, *, opening=False):
    catalog = json.loads((root / "agent/custom/servant_list.json").read_text(encoding="utf-8"))
    groups = defaultdict(list)
    for servant in canonical_servants(catalog["servants"]):
        groups[servant["class"]].append(servant)

    class_option = OPENING_CLASS_OPTION if opening else CLASS_OPTION
    option_prefix = "原生自动战斗开局等牌目标从者" if opening else "原生自动战斗主输出从者"
    target_key = OPENING_TARGET if opening else ATTACKER
    empty_label = "未指定从者（使用前排位置或微调）" if opening else "未选择"

    def selection_case(name, label, selected=""):
        return {
            "name": name,
            "label": label,
            "pipeline_override": {CONFIG: {"attach": {target_key: selected}}},
        }

    classes = [selection_case("none", empty_label)]
    options = {class_option: {"type": "select", "label": "目标从者职阶" if opening else "职阶", "default": "none", "cases": classes}}
    class_labels = {**catalog["class_cn"], "loreGrandCaster": "冠位术"}
    for job_class in (*class_labels, *sorted(groups.keys() - class_labels.keys())):
        servants = groups.get(job_class)
        if not servants:
            continue
        key = f"{option_prefix}-{job_class}"
        classes.append({"name": job_class, "label": class_labels.get(job_class, job_class), "option": [key]})
        cases = [selection_case("none", empty_label)]
        ordered_servants = sorted(
            servants, key=lambda entry: (-(entry.get("collection_no") or 0), int(entry["id"]))
        )
        for servant in ordered_servants:
            sid = str(servant["id"])
            number = servant.get("collection_no")
            prefix = str(number) if number else f"ID {sid}"
            case = selection_case(sid, f"{prefix}. {servant['name']}", sid)
            for image in servant["images"]:
                icon = FACE_DIR / image
                if (root / "assets" / icon).is_file():
                    case["icon"] = icon.as_posix()
                    break
            cases.append(case)
        options[key] = {"type": "select", "label": "目标从者" if opening else "主输出从者", "default": "none", "cases": cases}
    return {"option": options}


def build_documents(root=ROOT):
    return {OUTPUT: build_document(root), OPENING_OUTPUT: build_document(root, opening=True)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只检查从者选项是否需要更新")
    args = parser.parse_args()
    changed = []
    for relative, document in build_documents().items():
        path = ROOT / relative
        expected = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
        if not path.is_file() or path.read_text(encoding="utf-8") != expected:
            changed.append(relative)
            if not args.check:
                path.write_text(expected, encoding="utf-8", newline="\n")
    if args.check and changed:
        raise SystemExit("从者选项需要更新，请运行 python tools/generate_native_attacker_options.py：" + "、".join(changed))
    print("已更新从者选项：" + "、".join(changed) if changed else "主输出与开局等牌从者选项已是最新")


if __name__ == "__main__":
    main()
