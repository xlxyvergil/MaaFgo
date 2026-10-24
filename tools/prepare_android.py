"""生成 MaaFwApp 资源副本，保留桌面资源和版本字段不变。"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "Android" / "assets"
ANDROID_OPTION_TYPES = {"select", "switch", "checkbox", "input"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
REMOVED_TASKS = {"启动bbc", "关闭bbc", "BBC 配置转 Chaldea 本地队伍"}
BILIBILI_ANDROID_LAUNCHERS = {
    "com.bilibili.fatego",
    "com.bilibili.fatego/.EmptyClass",
    "com.bilibili.fatego/com.bilibili.fatego.EmptyClass",
}
BILIBILI_ANDROID_GAME_ACTIVITY = "com.bilibili.fatego/.UnityPlayerNativeActivity"
NATIVE_NEXT = [
    "[JumpBack]执行回主界面", "[JumpBack]执行队伍选择", "[JumpBack]执行章节导航",
    "[JumpBack]原生自动战斗_多次入口", "战斗完成信息",
]

# 仅调整确实等待后继界面的节点；timeout=1 的失败出口和可选弹窗探测保持原样。
ANDROID_TRANSITION_TIMEOUTS = {
    "base/pipeline/冠位戴冠战.json": {
        "冠位戴冠战-职介": 10000,
        "冠位戴冠战列表": 10000,
    },
    "base/pipeline/自动战斗_特殊技能.json": {
        "战斗_仇凛2技能_色卡选择": 15000,
        "战斗_库库尔坎_暴击星消耗": 15000,
        "战斗_技能目标子屏": 15000,
        "战斗_特殊技能处理": 15000,
    },
    "base/pipeline/自动编队.json": {"自动编队-卸下当前礼装": 15000},
    "base/pipeline/羁绊补齐自动编队.json": {"羁绊补齐-卸下当前礼装": 15000},
    "base/pipeline/原生自动战斗调度.json": {
        "结算推进_点击右上角": 20000,
        "结算推进_点击右上角_继续": 20000,
    },
    "base/pipeline/进本流程.json": {
        "进本-开始后弹窗检查": 20000,
        "进本-开始按钮已消失": 15000,
    },
    "base/pipeline/跳过剧情.json": {
        "战斗-出卡后剧情处理": 15000,
        "跳过剧情-点击跳过": 15000,
        "跳过剧情-直接点击跳过": 15000,
    },
    "jp/pipeline/run_FGO.json": {
        "自动登录": 60000,
        "自动登录2": 60000,
        "如果要SKIP": 15000,
        "点击是": 15000,
    },
}


def adapt_transition_timeouts(pipeline, relative_path):
    """在生成的安卓副本中给切页留余量，不更改 next、重试数或固定延时。"""
    for name, minimum in ANDROID_TRANSITION_TIMEOUTS.get(relative_path, {}).items():
        node = pipeline.get(name)
        if isinstance(node, dict):
            previous = node.get("timeout", minimum)
            node["timeout"] = max(previous, minimum)
            if node["timeout"] > previous:
                node["doc"] = (node.get("doc", "") + " 安卓采集和 OCR 较慢，放宽后继界面查找上限；命中即继续，保留原有失败出口。").strip()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def adapt_android_start_apps(value):
    """B服默认启动壳会将后台游戏带回主屏；安卓副本直接启动导出的游戏 Activity。"""
    if isinstance(value, dict):
        action = value.get("action")
        if isinstance(action, dict) and action.get("type") == "StartApp":
            params = action.get("param")
            if isinstance(params, dict) and params.get("package") in BILIBILI_ANDROID_LAUNCHERS:
                params["package"] = BILIBILI_ANDROID_GAME_ACTIVITY
        for child in value.values():
            adapt_android_start_apps(child)
    elif isinstance(value, list):
        for child in value:
            adapt_android_start_apps(child)


def adapt_wait_freezes(value):
    """DirectHit 没有识别矩形；新版框架需显式指定等待区域。"""
    if isinstance(value, dict):
        recognition = value.get("recognition", "DirectHit")
        kind = recognition.get("type", "DirectHit") if isinstance(recognition, dict) else recognition
        if kind == "DirectHit":
            for key in ("pre_wait_freezes", "post_wait_freezes", "repeat_wait_freezes"):
                wait = value.get(key)
                if isinstance(wait, (int, float)) and wait > 0:
                    value[key] = {"time": wait, "target": [0, 0, 1280, 720]}
                elif isinstance(wait, dict) and wait.get("target", True) is True:
                    wait["target"] = [0, 0, 1280, 720]
        for child in value.values():
            adapt_wait_freezes(child)
    elif isinstance(value, list):
        for child in value:
            adapt_wait_freezes(child)


def adapt_screencap_focus(value):
    """安卓外壳将截图保存在全局日志目录 log/screencap 下。"""
    if isinstance(value, dict):
        focus = value.get("focus")
        if isinstance(focus, str):
            value["focus"] = focus.replace("debug/screencap/", "log/screencap/")
        elif isinstance(focus, dict):
            for event, message in focus.items():
                if isinstance(message, str):
                    focus[event] = message.replace("debug/screencap/", "log/screencap/")
        for child in value.values():
            adapt_screencap_focus(child)
    elif isinstance(value, list):
        for child in value:
            adapt_screencap_focus(child)


def copy_agent(source: Path, target: Path):
    def ignore(folder, names):
        patterns = ["__pycache__", "*.pyc"]
        if Path(folder) == source / "custom":
            patterns += ["bbc*.py", "chaldea_import_action.py"]
        return shutil.ignore_patterns(*patterns)(folder, names)
    # chaldea/bbc_formatter.py 是公共包的纯 Python 依赖，不能按文件名前缀全局剔除。
    shutil.copytree(source, target, ignore=ignore)


def load_interface(assets: Path) -> dict:
    """展开 PI imports：列表按声明顺序追加，映射后声明覆盖前声明。"""
    result = {}
    active = set()

    def merge(path):
        path = path.resolve()
        if not path.is_relative_to(assets.resolve()):
            raise ValueError(f"import 越出资源目录: {path}")
        if path in active:
            raise ValueError(f"循环 import: {path}")
        active.add(path)
        data = read_json(path)
        for key, value in data.items():
            if key == "import":
                continue
            if key in ("task", "preset", "group"):
                result.setdefault(key, []).extend(value)
            elif key == "option":
                result.setdefault(key, {}).update(value)
            else:
                result[key] = value
        for imported in data.get("import", []):
            merge(assets / imported)
        active.remove(path)

    merge(assets / "interface.json")
    return result


def option_refs(value):
    if isinstance(value, dict):
        refs = value.get("option", [])
        if isinstance(refs, list):
            yield from (ref for ref in refs if isinstance(ref, str))
        for key, child in value.items():
            # pipeline 的 attach / option 字段不属于 PI 选项依赖。
            if key not in ("pipeline_override", "option"):
                yield from option_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from option_refs(child)


def adapt_scan_select(name: str, option: dict, assets: Path):
    folder = (assets / option.pop("scan_dir")).resolve()
    if not folder.is_relative_to(assets.resolve()):
        raise ValueError(f"scan_dir 越出资源目录: {folder}")
    pattern = option.pop("scan_filter", "*.png")
    files = sorted((p for p in folder.glob(pattern) if p.is_file()),
                   key=lambda p: (p.name != "0_空.png", p.name))
    if not files:
        raise ValueError(f"选项 {name} 没有可打包的文件: {folder}")
    override = option.pop("pipeline_override", {})

    def assign(value, filename):
        if isinstance(value, dict):
            copied = {key: assign(child, filename) for key, child in value.items()}
            # MXU 按 attach 键名赋值，即使原值不是 {option_name} 也会覆盖。
            if isinstance(copied.get("attach"), dict) and name in copied["attach"]:
                copied["attach"][name] = filename
            return copied
        if isinstance(value, list):
            return [assign(child, filename) for child in value]
        if isinstance(value, str):
            return value.replace("{" + name + "}", filename)
        return value

    cases = []
    for path in files:
        if not path.resolve().is_relative_to(assets.resolve()):
            raise ValueError(f"扫描文件越出资源目录: {path}")
        case = {"name": path.name, "label": "不限" if path.name == "0_空.png" else path.stem,
                "pipeline_override": assign(override, path.name)}
        if path.suffix.lower() in IMAGE_SUFFIXES:
            case["icon"] = path.relative_to(assets.resolve()).as_posix()
        cases.append(case)
    default = option.pop("default_case", None) or option.pop("default", None) or files[0].name
    if default not in {case["name"] for case in cases}:
        raise ValueError(f"选项 {name} 的默认文件不存在: {default}")
    option.pop("default", None)
    option.update(type="select", cases=cases, default_case=default)
    option["description"] = "从当前版本内置的文件列表中选择。"
    if any(path.name == "0_空.png" for path in files):
        option["description"] += "「不限」表示不校验此格礼装；满破和非满破分别选择。"


def compatibility_report(source: dict, converted: dict) -> dict:
    """审计实际保留的选项，便于发现后续新增的 MXU 扩展。"""
    options = converted["option"]
    return {
        "option_types": sorted({option.get("type", "select") for option in options.values()}),
        "file_lists": [{"option": name, "type": option["type"], "files": len(option["cases"]),
                        "default_case": option["default_case"]}
                       for name, option in options.items() if source["option"][name].get("type") == "scan_select"],
        "file_inputs_as_text": [{"option": name, "input": field["name"]}
                                for name in options for field in source["option"][name].get("inputs", [])
                                if field.get("input_type") == "file"],
        "task_docs_in_description": [{"task": task["name"], "doc": task["doc"]}
                                     for task in source["task"] if task.get("doc")
                                     and any(t["name"] == task["name"] for t in converted["task"])],
    }


def adapt_interface(data: dict, assets: Path) -> dict:
    data = copy.deepcopy(data)
    data["description"] = "FGO 安卓自动化助手；使用 Shizuku 或 Root，后台画面固定为 1280×720。"
    # APK 的更新来源由 profile.yaml 显式配置，不继承桌面更新字段。
    data.pop("mirrorchyan_rid", None)
    data.pop("mirrorchyan_multiplatform", None)
    data["agent"] = {"child_exec": "python3", "child_args": ["-u", "agent/main.py"]}
    data["task"] = [t for t in data["task"] if t["name"] not in REMOVED_TASKS]
    for task in data["task"]:
        if task["name"] == "直接bbc战斗":
            # 保留内部任务名以兼容既有 pipeline / preset，仅用户文案不再提 BBC。
            task["description"] = "从助战、编队或战斗界面开始原生自动战斗。"
        elif "description" in task:
            for text in ("BBC 战斗或原生自动战斗", "BBC战斗或原生自动战斗", "BBC 或原生自动战斗"):
                task["description"] = task["description"].replace(text, "原生自动战斗")
        # 独立 doc 按钮也是 MXU 扩展；标准安卓任务说明可直接显示 Markdown。
        if task.get("doc"):
            doc = (assets.parent / task.pop("doc")).resolve()
            if not doc.is_relative_to(assets.parent.resolve()):
                raise ValueError(f"任务说明越出工程目录: {doc}")
            task["description"] = task.get("description", "") + "\n\n" + doc.read_text(encoding="utf-8-sig")
        if task["name"] == "截图" and isinstance(task.get("description"), str):
            task["description"] = task["description"].replace("debug/screencap/", "log/screencap/")
    for preset in data.get("preset", []):
        preset["task"] = [t for t in preset["task"] if t["name"] not in REMOVED_TASKS]

    options = data["option"]
    for name in ("battle_mode", "主线-战斗方式"):
        option = options[name]
        option["cases"] = [case for case in option["cases"] if case["name"] == "native"]
        option["default"] = "native"
        option["description"] = "安卓版使用原生自动战斗，可导入 Chaldea 队伍。"

    # 只保留可从任务、资源和全局选项到达的选项，去掉 BBC / 模拟器路径配置。
    pending = list(option_refs({key: value for key, value in data.items() if key != "option"}))
    pending.extend(data.get("global_option", []))
    used = set()
    while pending:
        name = pending.pop()
        if name in used:
            continue
        if name not in options:
            raise ValueError(f"缺少选项定义: {name}")
        used.add(name)
        pending.extend(option_refs(options[name]))
    data["option"] = {name: opt for name, opt in options.items() if name in used}

    # MXU 分支独有 scan_select：把已打包图片枚举为标准 PI select。
    for name, option in data["option"].items():
        if option.get("type") == "scan_select":
            adapt_scan_select(name, option, assets)
        if option.get("type", "select") not in ANDROID_OPTION_TYPES:
            raise ValueError(f"安卓外壳不支持选项 {name} 的类型: {option['type']}")
        for field in option.get("inputs", []):
            if field.get("input_type") == "file":
                field.pop("input_type")
                field.setdefault("pipeline_type", "string")
                field["description"] = "可粘贴分享链接、队伍/关卡 ID，或填写手机上可读取的 JSON 路径、已缓存的队伍文件名。"
                option["description"] = "直接输入或粘贴导入来源；本地文件使用手机路径，缓存文件可只填文件名。"
    # MXU 兼容字段 default 不被 MaaFwApp 的 PI 解析器读取。
    for option in data["option"].values():
        if option.get("type", "select") in ("select", "switch", "checkbox") and "default" in option:
            option.setdefault("default_case", option.pop("default"))
    return data


def prepare(root: Path = ROOT, output: Path = OUTPUT) -> dict:
    assets = root / "assets"
    interface_source = load_interface(assets)
    interface = adapt_interface(interface_source, assets)
    adapt_android_start_apps(interface)
    adapt_wait_freezes(interface)
    adapt_screencap_focus(interface)
    output = output.resolve()
    # 此目录只放生成文件；不接受任意用户目录，避免清理误删其他工程文件。
    if output != (root / "Android" / "assets").resolve():
        raise ValueError("输出目录必须是工程内的 Android/assets")
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    for folder in ("resource", "i18n"):
        shutil.copytree(assets / folder, output / folder, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))
    copy_agent(root / "agent", output / "agent")
    for name, source in (("Announcement.md", root / "Android/Announcement.md"), ("LICENSE", root / "LICENSE")):
        shutil.copy2(source, output / name)
    write_json(output / "interface.json", interface)
    write_json(output.parent / "out/interface-compatibility.json", compatibility_report(interface_source, interface))

    # 即便某入口未带 battle_mode 选项，也不能落回桌面默认的 BBC 调度。
    daily = output / "resource/base/pipeline/日常战斗.json"
    pipeline = read_json(daily)
    pipeline["通用战斗调度"]["next"] = NATIVE_NEXT
    pipeline["执行bbc战斗"] = {"enabled": False}
    pipeline["执行战斗结束"] = {"enabled": False}
    write_json(daily, pipeline)
    for server in ("cn", "jp"):
        close = output / "resource" / server / "pipeline/关闭游戏.json"
        pipeline = read_json(close)
        pipeline.pop("关闭bbc", None)
        pipeline["关闭游戏"].pop("next", None)
        write_json(close, pipeline)
    for name in ("bbc战斗.json", "BBC配置转Chaldea本地队伍.json", "启动bbc.json", "更新bbc配置文件.json"):
        (output / "resource/base/pipeline" / name).unlink(missing_ok=True)

    for path in (output / "resource").glob("*/pipeline/**/*.json"):
        pipeline = read_json(path)
        original = copy.deepcopy(pipeline)
        adapt_android_start_apps(pipeline)
        adapt_wait_freezes(pipeline)
        adapt_screencap_focus(pipeline)
        adapt_transition_timeouts(pipeline, path.relative_to(output / "resource").as_posix())
        if pipeline != original:
            write_json(path, pipeline)

    # OCR 模型既支持仓库现有文件，也支持干净 checkout 的 MaaCommonAssets。
    ocr = output / "resource/model/ocr"
    if not all((ocr / file).is_file() for file in ("rec.onnx", "det.onnx", "keys.txt")):
        common = assets / "MaaCommonAssets/OCR/ppocr_v6/medium"
        if not common.is_dir():
            raise ValueError("OCR 模型缺失，请运行 git submodule update --init assets/MaaCommonAssets")
        shutil.copytree(common, ocr, dirs_exist_ok=True)
    # 对生成包记录哈希；不触碰既有桌面打包目录。
    hashes = {path.relative_to(output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted(output.rglob("*")) if path.is_file()}
    write_json(output.parent / "out/resource-sha256.json", hashes)
    return interface


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    result = prepare()
    print(f"Android 资源已生成: {OUTPUT}，{len(result['task'])} 个任务，{len(result['option'])} 个选项")
