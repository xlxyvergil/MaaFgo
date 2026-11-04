"""Generate MaaFGO's MXU expball options from the existing identity catalogs.

Only writes the expball task and its own option namespace. It does not modify
the inventory catalogs, valuable-equipment flags, interface version or images.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACTION = "执行搓丸子"
SLOTS = 5
POSITIVE = r"^[1-9][0-9]{0,5}$"
NONNEGATIVE = r"^(0|[1-9][0-9]{0,9})$"


def override(**fields):
    return {ACTION: {"attach": fields}}


def choice(value, label, fields=None, children=(), description=None):
    result = {"name": str(value), "label": label}
    if fields:
        result["pipeline_override"] = override(**fields)
    if children:
        result["option"] = list(children)
    if description:
        result["description"] = description
    return result


def select(label, cases, default=None, description=None):
    result = {
        "type": "select", "label": label,
        "default": str(default if default is not None else cases[0]["name"]),
        "cases": cases,
    }
    if description:
        result["description"] = description
    return result


def toggle(label, field, *, default=False, children=(), description=None):
    return select(label, [
        choice("off", "不启用", {field: False}),
        choice("on", "启用", {field: True}, children),
    ], "on" if default else "off", description)


def integer(label, field, default, verify=POSITIVE, description=None):
    result = {
        "type": "input", "label": label,
        "inputs": [{
            "name": field, "label": label, "default": str(default),
            "pipeline_type": "int", "verify": verify,
        }],
        "pipeline_override": override(**{field: "{" + field + "}"}),
    }
    if description:
        result["description"] = description
    return result


def image_path(root, kind, row):
    folders = ("servant_face", "NarrowFigures") if kind == "servant" else (
        "EquipFaces/team", "EquipFaces/list"
    )
    for folder in folders:
        for filename in row.get("images", []):
            if (root / "assets/resource/base/image" / folder / filename).is_file():
                return f"resource/base/image/{folder}/{filename}"
    return None


def identity_select(root, label, kind, rows, field, class_names):
    cases = [choice("none", "请选择对象", {field: ""})]
    for row in sorted(rows, key=lambda r: (int(r.get("collection_no") or 0), str(r["id"]))):
        identity = str(row["id"])
        number = row.get("collection_no", "")
        suffix = f"{row.get('rarity', '?')}星"
        if kind == "servant":
            suffix += "·" + class_names.get(row.get("class"), row.get("class", ""))
        display = f"{number}. {row['name']}（{suffix}）[{identity}]"
        case = choice(identity, display, {field: identity})
        path = image_path(root, kind, row)
        if path:
            case["icon"] = path
            case["description"] = (
                f"{row['name']} · ID {identity}\n\n"
                f'<img src="{path}" width="79" alt="{row["name"]}" />'
            )
        cases.append(case)
    return select(label, cases, "none", "可搜索名称或 ID。目录复用工程已有资料；缺少可用识别图的保留目标会在启动前说明并停止。")


def target_options(root, slot, servants, equips, class_names):
    name = f"搓丸子-保留目标{slot}"
    field = f"target_{slot}_"
    objects = {
        name: select(f"保留目标 {slot}", [
            choice("none", "不启用", {field + "kind": "none", field + "id": ""}),
            choice("servant", "从者", {field + "kind": "servant"},
                   [name + "-从者", name + "-保留方式"]),
            choice("equip", "礼装", {field + "kind": "equip"},
                   [name + "-礼装", name + "-保留方式"]),
        ], "none"),
        name + "-从者": identity_select(
            root, "保留从者", "servant", servants, field + "id", class_names
        ),
        name + "-礼装": identity_select(
            root, "保留礼装", "equip", equips, field + "id", class_names
        ),
        name + "-保留方式": select("保留方式", [
            choice("count", "指定份数", {field + "mode": "count"}, [name + "-份数"]),
            choice("all", "全部保留", {field + "mode": "all"}),
        ], "count", "指定份数按本次新增确认锁定计数，不是账号总持有量。"),
        name + "-份数": integer("本次保留份数", field + "count", 1),
    }
    return objects


def load_catalogs(root):
    """Join the existing full editor metadata with recognition assets that exist.

    The high-value equipment catalog deliberately excludes many low-star cards;
    joining is read-only and never changes its filtering flags.
    """
    servant_data = json.loads((root / "agent/custom/servant_list.json").read_text(encoding="utf-8"))
    servants = [r for r in servant_data["servants"] if int(r.get("collection_no") or 0) > 0]
    known_equips = json.loads((root / "agent/custom/equip_list.json").read_text(encoding="utf-8"))["equips"]
    editor = json.loads((root / "agent/battle_editor/data/catalog.json").read_text(encoding="utf-8"))
    known_by_id = {str(r["id"]): r for r in known_equips}
    equips = []
    for row in editor["equips"]:
        old = known_by_id.get(str(row["id"]), {})
        filenames = list(dict.fromkeys([*old.get("images", []), *([row["image"]] if row.get("image") else [])]))
        equips.append({
            "id": str(row["id"]), "collection_no": row.get("collectionNo", 0),
            "name": row.get("name") or row.get("nameJp") or str(row["id"]),
            "name_jp": row.get("nameJp", ""), "rarity": int(row.get("rarity", 0)),
            "images": filenames,
        })
    # Preserve existing runtime identities even if a future editor refresh is partial.
    present = {r["id"] for r in equips}
    equips.extend(dict(row) for row in known_equips if str(row["id"]) not in present)
    catalog = {"schema": 1, "equips": [], "servants": []}
    for kind, rows, folders in (
        ("equips", equips, ("EquipFaces/list", "EquipFaces/team")),
        ("servants", servants, ("servant_face", "NarrowFigures")),
    ):
        for row in rows:
            paths = [
                f"{folder}/{filename}"
                for folder in folders for filename in row.get("images", [])
                if (root / "assets/resource/base/image" / folder / filename).is_file()
            ]
            catalog[kind].append({
                "id": str(row["id"]), "name": row["name"],
                "name_jp": row.get("name_jp", ""), "rarity": int(row.get("rarity", 0)),
                "images": list(dict.fromkeys(paths)),
            })
    return servants, equips, servant_data.get("class_cn", {}), catalog


def build(root=ROOT):
    servants, equips, classes, catalog = load_catalogs(root)
    all_operations_off = {
        "make_ball": False, "draw_fp": False, "store4": False, "store5": False,
        "store_balls": False, "sell_servants": False, "sell4": False,
        "sell5": False, "sell_wz": False, "levelup": False,
    }
    normal_options = [
        "搓丸子-自动搓丸子", "搓丸子-自动抽友情", "搓丸子-存储经验值卡",
        "搓丸子-自动贩卖", "搓丸子-自动喂从者", "搓丸子-QP满处理", "搓丸子-循环上限",
    ]
    options = {
        "搓丸子-运行方式": select("运行方式", [
            choice("normal", "正常运行", {"mode": "normal"}, normal_options),
            choice("test_servant", "仅测试从者选择", {"mode": "test_servant", **all_operations_off},
                   ["搓丸子-升级从者", "搓丸子-目标等级"]),
        ], "normal", "从者选择测试只查找配置的从者，不抽取、贩卖或强化。"),
        "搓丸子-自动搓丸子": toggle(
            "自动搓丸子", "make_ball", default=True,
            children=["搓丸子-丸子星级", "搓丸子-额外材料", "搓丸子-成品入库"],
        ),
        "搓丸子-丸子星级": select("丸子星级", [
            choice("1", "1星丸子", {"ball_star": 1}),
            choice("2", "2星丸子", {"ball_star": 2}),
        ], "1", "决定最终强化的概念礼装星级。"),
        "搓丸子-额外材料": select("额外允许消耗的礼装星级", [
            choice("none", "仅1、2星", {"eat3": False, "eat4": False}),
            choice("3", "额外允许3星", {"eat3": True, "eat4": False}),
            choice("4", "额外允许4星", {"eat3": False, "eat4": True}),
            choice("3_4", "额外允许3星和4星", {"eat3": True, "eat4": True}),
        ], "none", "作为强化素材的礼装会被消耗；3星和4星互不连带。锁定、标记及保留目标不作为素材。"),
        "搓丸子-成品入库": toggle("满级丸子存入保管室", "store_balls"),
        "搓丸子-自动抽友情": toggle(
            "自动抽友情", "draw_fp",
            children=["搓丸子-每批抽数", "搓丸子-寻找方向", "搓丸子-抽取批次上限",
                      "搓丸子-友情点保留量", "搓丸子-保留对象设置"],
            description="使用友情点持续召唤，不影响原每日免费友情任务。请确认游戏自动变还设定不会处理保留对象或所需材料。",
        ),
        "搓丸子-每批抽数": select("每批抽取数量", [
            choice("10", "10抽", {"draw_batch": 10}),
            choice("100", "100抽", {"draw_batch": 100}),
        ], "10", "100抽尚未完成整批保留对象遍历，当前不允许与保留目标同时开启；流程尚待实机校准。"),
        "搓丸子-寻找方向": select("寻找友情池方向", [
            choice("left", "向左", {"direction": "left"}),
            choice("right", "向右", {"direction": "right"}),
        ], "left"),
        "搓丸子-抽取批次上限": integer("最多抽取批次", "max_draw_batches", 50),
        "搓丸子-友情点保留量": integer(
            "保留友情点", "friend_points_reserve", 0, NONNEGATIVE,
            "保留量读取不可靠时停止，不能把识别失败当作余额为零。",
        ),
        "搓丸子-保留对象设置": select("保留对象设置", [
            choice("off", "不设置保留对象", {
                **{f"target_{i}_kind": "none" for i in range(1, SLOTS + 1)},
                "stop_targets": False,
            }),
            choice("on", "选择保留对象", children=[
                *[f"搓丸子-保留目标{i}" for i in range(1, SLOTS + 1)], "搓丸子-目标全部满足停止",
            ]),
        ], "off", "从者和礼装均从已有目录选择，份数按本次新增锁定计数。"),
        "搓丸子-目标全部满足停止": toggle(
            "保留对象全部满足后停止", "stop_targets",
            description="只适用于有效的指定份数目标；与全部保留目标同时配置时会在启动前说明冲突。",
        ),
        "搓丸子-存储经验值卡": select("优先保存经验值卡", [
            choice("off", "不存入", {"store4": False, "store5": False}),
            choice("on", "存入保管室", children=["搓丸子-存四星", "搓丸子-存五星"]),
        ], "off"),
        "搓丸子-存四星": toggle("存入4星经验值卡", "store4"),
        "搓丸子-存五星": toggle("存入5星经验值卡", "store5"),
        "搓丸子-自动贩卖": select("自动贩卖", [
            choice("off", "不贩卖", {"sell_servants": False, "sell4": False, "sell5": False, "sell_wz": False}),
            choice("on", "按所选类别贩卖", children=[
                "搓丸子-卖从者", "搓丸子-卖四星", "搓丸子-卖五星", "搓丸子-卖纹章",
            ]),
        ], "off", "会处理运行前已有库存。仅处理明确允许且未锁定的对象，不自动扩大类别。"),
        "搓丸子-卖从者": toggle(
            "贩卖友情从者", "sell_servants",
            description="功能配置保留；本轮尚未开放从者贩卖的逐卡核验，启用后会在任何游戏输入前停止并说明。",
        ),
        "搓丸子-卖四星": toggle("贩卖4星经验值卡", "sell4"),
        "搓丸子-卖五星": toggle("贩卖5星经验值卡", "sell5"),
        "搓丸子-卖纹章": toggle(
            "贩卖低星纹章", "sell_wz",
            description="功能配置保留；本轮尚未开放纹章贩卖的逐卡核验，启用后会在任何游戏输入前停止并说明。",
        ),
        "搓丸子-自动喂从者": toggle(
            "自动喂从者", "levelup", children=["搓丸子-升级从者", "搓丸子-目标等级"],
            description="复用现有从者强化能力。此处是从者经验值卡，与丸子所用概念礼装无关。",
        ),
        "搓丸子-QP满处理": select("贩卖时QP已满", [
            choice("stop", "停止", {"aqf": False}),
            choice("continue", "继续贩卖", {"aqf": True}),
        ], "stop"),
        "搓丸子-循环上限": integer("最多处理轮数", "max_cycles", 200),
    }
    level_options = {
        "搓丸子-升级从者": identity_select(
            root, "升级或测试的从者", "servant", servants, "level_servant_id", classes
        ),
        "搓丸子-目标等级": integer("目标等级（1—120）", "target_level", 120,
                                  r"^([1-9][0-9]?|1[01][0-9]|120)$"),
    }
    task = {"task": [{
        "name": "搓丸子", "label": "🍡 搓丸子（待实机校准）", "entry": "搓丸子",
        "description": (
            "将允许消耗的概念礼装强化为1星或2星经验丸子，可按配置联动友情召唤、保留对象、存储、贩卖和从者升级。\n\n"
            "默认只搓现有礼装，不抽友情、不贩卖。丸子星级决定成品，额外材料星级决定哪些礼装会被消耗。"
            "重要对象请预先锁定；请核对游戏友情点自动变还设置。\n\n"
            "首版仅从未锁定、未标记的Lv1底卡开始，本轮半成品会锁定保护；跨次自动续接历史半成品尚未开放。"
            "保留对象提供5组从者/礼装目录选择；指定份数按本次新增锁定计数。100抽与保留目标暂不能组合。"
            "友情从者与纹章贩卖暂未开放，启用后会在输入前说明并停止。"
            "礼装实际强化及完整联动流程仍待双服实机验收；无法可靠识别时停止并报告原因。"
        ),
        "option": ["搓丸子-运行方式"], "group": ["日常行为"],
    }]}
    documents = {
        "agent/custom/expball_catalog.json": catalog,
        "assets/tasks/搓丸子.json": task,
        "assets/options/搓丸子.json": {"option": options},
        "assets/options/搓丸子/从者升级.json": {"option": level_options},
    }
    for i in range(1, SLOTS + 1):
        documents[f"assets/options/搓丸子/保留目标{i}.json"] = {
            "option": target_options(root, i, servants, equips, classes)
        }
    return documents


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify generated files without writing")
    args = parser.parse_args()
    changed = []
    for relative, document in build().items():
        path = ROOT / relative
        encoded = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != encoded:
                changed.append(relative)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(encoded, encoding="utf-8", newline="\n")
            print(relative)
    if changed:
        raise SystemExit("Generated options differ: " + ", ".join(changed))


if __name__ == "__main__":
    main()
