"""
Chaldea 队伍数据转换包

将 chaldea.center 的队伍分享数据转换为 BBchannel 可识别的战斗配置 JSON。
"""

import json
import os
import re
import logging
from pathlib import Path
from typing import Optional

import mfaalog

from .chaldea_client import (
    fetch_teams_by_quest, fetch_team_by_id, select_best_team,
    decode_content, parse_import_source,
)
from .game_data import get_servant_name
from .bbc_formatter import chaldea_to_bbc
from .config_checker import validate_bbc_config

logger = logging.getLogger(__name__)

# 本地队伍缓存目录（相对 agent 运行的工作目录，即资源包根的 config/Battle）
CACHE_DIR = os.path.join("config", "Battle")

# 仅为正式交付的试炼示例提供离线回退，不扩大其他文件名的搜索范围。
_BUNDLED_BATTLE_PRESETS = frozenset({
    "地狱的试炼_202610_阿兹赖尔.json",
    "地狱的试炼_202610_补贯通.json",
})


def _build_cache_name(share_data: dict, quest_id, team_id) -> str:
    """生成可读的缓存文件名: 从者名首字_队伍ID_关卡ID

    例: 诺奥阿_77210_94149047 (每个从者取名字的第一个字)
    从者名查不到时用 svtId 兜底; 文件名非法字符替换为下划线。
    """
    chars = []
    for svt in (share_data.get("team") or {}).get("onFieldSvts") or []:
        if not isinstance(svt, dict):
            continue
        svt_id = svt.get("svtId")
        if svt_id is None:
            continue
        name = get_servant_name(svt_id)
        # 取名字的首字; 从者_4000100 兜底名取数字部分
        first = name[:1] if name else ""
        if not first or name.startswith("从者_"):
            first = str(svt_id)[-3:]
        chars.append(first)
    name_part = "".join(chars) if chars else "unknown"
    # Windows 文件名非法字符过滤
    for ch in r'\/:*?"<>|':
        name_part = name_part.replace(ch, "_")
    # 限制总长度, 防止路径过长
    if len(name_part) > 40:
        name_part = name_part[:40]
    return f"{name_part}_{team_id}_{quest_id}"


def _find_cache_file(quest_id, team_id) -> Optional[str]:
    """在缓存目录中查找已存在的队伍缓存文件, 找不到返回 None

    兼容两类命名:
    - 新命名: 从者名首字_队伍ID_关卡ID (按 team_id 通配匹配: ^.+_{team_id}_数字)
    - 旧纯ID名: chaldea_team_{team_id}.json / chaldea_quest_{quest_id}_*.json
    以及历史命名 94149047_77210_太岁星君... ({quest_id}_{team_id}_ 前缀)。
    """
    try:
        if not os.path.isdir(CACHE_DIR):
            return None
        import re
        patterns = []
        if team_id is not None:
            # 旧格式精确名
            patterns.append(("exact", f"chaldea_team_{team_id}"))
            # 历史命名: {quest_id}_{team_id}_xxx
            if quest_id is not None:
                patterns.append(("prefix", f"{quest_id}_{team_id}_"))
            # 新命名: 从者首字_{team_id}_{quest_id}, 从者部分任意
            patterns.append(("regex", rf"^.+_{team_id}_\d+$"))
        if quest_id is not None:
            quest_key = re.escape(str(quest_id))
            patterns.append(("regex", rf"^chaldea_quest_{quest_key}(?:_.+)?$"))
            # 新缓存名也用于关卡搜索；队伍编号部分可为数字或 top 等旧标记。
            patterns.append(("regex", rf"^.+_[^_]+_{quest_key}$"))
        if not patterns:
            return None

        for fname in os.listdir(CACHE_DIR):
            if not fname.endswith(".json"):
                continue
            stem = fname[:-5]
            for mode, pat in patterns:
                if (mode == "exact" and stem == pat) or (
                    mode == "prefix" and stem.startswith(pat)
                ) or (mode == "regex" and re.match(pat, stem)):
                    path = os.path.join(CACHE_DIR, fname)
                    if quest_id is not None:
                        # 文件名只用于筛选候选。缓存被误命名/覆盖时不能加载
                        # 其他关卡的队伍，也不能让损坏的首个文件挡住有效缓存。
                        data = _load_cache_file(path)
                        quest = data.get("quest") if isinstance(data, dict) else None
                        if not isinstance(quest, dict) or str(quest.get("id")) != str(quest_id):
                            continue
                    return path
    except Exception as e:
        logger.warning(f"[Chaldea] 扫描缓存目录失败: {e}")
    return None


def _cache_path(name: str) -> str:
    return os.path.join(CACHE_DIR, f"{name}.json")


def _load_cache_file(path: str) -> Optional[dict]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        mfaalog.info(f"[Chaldea] 命中本地缓存: {path}")
        return data
    except Exception as e:
        logger.warning(f"[Chaldea] 读取缓存失败({path}): {e}")
        return None


def _save_cache(name: str, data: dict) -> None:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        path = _cache_path(name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        mfaalog.info(f"[Chaldea] 已保存队伍到本地缓存: {path}")
    except Exception as e:
        logger.warning(f"[Chaldea] 保存缓存失败({name}): {e}")


def _is_battle_document(data: object) -> bool:
    """Recognize explicit battle trees, including actions/strategy-only files."""
    return isinstance(data, dict) and (
        isinstance(data.get("team"), dict)
        or isinstance(data.get("actions"), list)
        or isinstance(data.get("maaAdvancedStrategy"), dict)
    )


def _load_local_file(source: str) -> Optional[dict]:
    """尝试把输入当作本地文件路径/文件名加载 BattleShareData。

    支持:
    - 绝对/相对路径: G:/xxx/team.json / ./team.json
    - 纯文件名: 优先在 CACHE_DIR (config/Battle) 中查找；本期内置示例
      在本地同名文件不存在时再查 resource/base/battle_presets
    - .json 后缀可省略
    返回加载成功的 dict, 不是文件输入返回 None。
    """
    s = source.strip()
    if not s or re.match(r'^https?://', s) or s.isdigit():
        return None

    candidates = []
    is_explicit_path = (
        os.path.isabs(s)
        or os.sep in s
        or (os.altsep and os.altsep in s)
        or s.startswith(".")
    )
    if is_explicit_path:
        # 显式路径形式
        candidates.append(s)
        if not s.lower().endswith(".json"):
            candidates.append(s + ".json")
    else:
        # 纯文件名（可带 .json）: 在缓存目录中查找
        names = (s,) if s.lower().endswith(".json") else (s, s + ".json")
        for name in names:
            candidates.append(os.path.join(CACHE_DIR, name))

    local_exists = False
    for path in candidates:
        if os.path.isfile(path):
            local_exists = True
            data = _load_cache_file(path)
            if _is_battle_document(data):
                return data

    # 用户的同名文件即使损坏也不能被示例静默替代；显式路径不做回退。
    if is_explicit_path or local_exists:
        return None
    project_root = Path(__file__).resolve().parents[2]
    for resource_root in (project_root / "assets/resource/base", project_root / "resource/base"):
        for name in names:
            if name not in _BUNDLED_BATTLE_PRESETS:
                continue
            preset = resource_root / "battle_presets" / name
            if preset.is_file():
                data = _load_cache_file(str(preset))
                if _is_battle_document(data):
                    mfaalog.info(f"[Chaldea] 使用内置试炼示例（可另存为本地方案）: {preset}")
                    return data
    return None


def fetch_share_data(source: str):
    """下载 + 解码 Chaldea BattleShareData（BBC 与 auto-battle 共用入口）。

    解析 source（team_id / quest_id / URL / data= 压缩串），按需从 Chaldea
    API 下载（team_id / quest_id）或离线解码（data= 内联数据），返回解码后的
    BattleShareData dict。

    Returns:
        (share_data, quest_id, team_id)：
        - share_data: 解码后的 dict，失败为 None
        - quest_id / team_id: 解析出的展示用标识（str），供命名等使用
    """
    # 优先尝试本地文件输入（路径 / config/Battle 下的文件名），避免文件路径
    # 先经过链接/ID 解析器并产生误导性的“无法解析”日志。
    local = _load_local_file(source)
    if local:
        mfaalog.info(f"[Chaldea] 输入识别为本地文件, 直接加载")
        quest_id = (local.get("quest") or {}).get("id", "0")
        team_id = "local"
        return local, quest_id, team_id

    quest_id, team_id, direct_data = parse_import_source(source)
    mfaalog.info(f"[Chaldea] parse_import_source: quest_id={quest_id} team_id={team_id} direct_data={'有' if direct_data else '无'}")
    share_data = None

    if direct_data:
        logger.info("[Chaldea] 匹配到长链接数据特征，开启离线解码...")
        mfaalog.info("[Chaldea] 离线解码 direct_data...")
        share_data = decode_content(direct_data)
        team_id = "offline"
        quest_id = (share_data.get("quest") or {}).get("id", "0") if share_data else "0"
    elif team_id:
        cache_file = _find_cache_file(None, team_id)
        share_data = None
        if cache_file:
            share_data = _load_cache_file(cache_file)
        if share_data:
            team_id = str(team_id)
            quest_id = (share_data.get("quest") or {}).get("id", "0")
            mfaalog.info(f"[Chaldea] 使用缓存队伍, 跳过下载: team_id={team_id}")
        else:
            mfaalog.info(f"[Chaldea] 按 team_id={team_id} 下载队伍...")
            team_resp = fetch_team_by_id(team_id)
            if team_resp and "content" in team_resp:
                mfaalog.info(f"[Chaldea] 队伍下载成功, 开始解码 content")
                share_data = decode_content(team_resp["content"])
                quest_id = team_resp.get("questId", "0")
                if share_data:
                    # 下载成功后落盘缓存, 下次不再重复下载
                    _save_cache(_build_cache_name(share_data, quest_id, team_id), share_data)
            else:
                logger.error("[Chaldea] 队伍接口无匹配数据。")
                mfaalog.error(f"[Chaldea] 队伍接口无匹配数据: team_id={team_id}")
    elif quest_id:
        cache_file = _find_cache_file(quest_id, None)
        share_data = None
        if cache_file:
            share_data = _load_cache_file(cache_file)
        if share_data:
            mfaalog.info(f"[Chaldea] 使用缓存队伍, 跳过搜索: quest_id={quest_id}")
            team_id = "cached"
        else:
            mfaalog.info(f"[Chaldea] 按 quest_id={quest_id} 搜索队伍...")
            teams = fetch_teams_by_quest(quest_id, phase=None, limit=10)
            mfaalog.info(f"[Chaldea] 获取到 {len(teams)} 个队伍")
            best = select_best_team(teams)
            if best and "content" in best:
                mfaalog.info(f"[Chaldea] 选择最佳队伍 id={best.get('id')}, 开始解码 content")
                share_data = decode_content(best["content"])
                team_id = best.get("id", "top")
                if share_data:
                    _save_cache(_build_cache_name(share_data, quest_id, team_id), share_data)
            else:
                logger.error("[Chaldea] 该关卡无可用队伍数据。")
                mfaalog.error(f"[Chaldea] 该关卡无可用队伍数据: quest_id={quest_id}")
    else:
        logger.error("[Chaldea] 无法解析输入来源。")
        mfaalog.error(f"[Chaldea] 无法解析输入来源: {source[:100]}")

    mfaalog.info(f"[Chaldea] fetch_share_data 完成: share_data={'有' if share_data else '无'} quest_id={quest_id} team_id={team_id}")

    return share_data, quest_id, team_id


def fetch_and_convert(source: str, output_dir: Optional[str] = None) -> Optional[str]:
    """
    主入口编排: 通过 source 获取数据并生成 BBC 配置文件

    参数:
        source: 用户输入 (quest_id / team_id / URL / 压缩数据)
        output_dir: 输出目录

    返回:
        保存的文件名，失败返回 None
    """
    share_data, quest_id, team_id = fetch_share_data(source)
    if not share_data:
        logger.error("[Chaldea] 数据结构提取失败。")
        return None

    bbc_config = chaldea_to_bbc(share_data)

    if not bbc_config:
        logger.error("[Chaldea] 转换结果为空。")
        return None

    filename = f"chaldea_{quest_id}_{team_id}.json"
    filepath = os.path.join(output_dir or ".", filename)

    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(bbc_config, f, ensure_ascii=False, indent=4)

    logger.info(f"[Chaldea] 已保存队伍 JSON 到 {filepath}")
    return filename
