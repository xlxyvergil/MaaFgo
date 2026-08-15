"""
Chaldea 队伍数据转换包

将 chaldea.center 的队伍分享数据转换为 BBchannel 可识别的战斗配置 JSON。
"""

import json
import os
import logging
from typing import Optional

from .chaldea_client import (
    fetch_teams_by_quest, fetch_team_by_id, select_best_team,
    decode_content, parse_import_source,
)
from .bbc_formatter import chaldea_to_bbc
from .config_checker import validate_bbc_config

logger = logging.getLogger(__name__)


def fetch_and_convert(source: str, output_dir: Optional[str] = None) -> Optional[str]:
    """
    主入口编排: 通过 source 获取数据并生成 BBC 配置文件

    参数:
        source: 用户输入 (quest_id / team_id / URL / 压缩数据)
        output_dir: 输出目录

    返回:
        保存的文件名，失败返回 None
    """
    quest_id, team_id, direct_data = parse_import_source(source)
    share_data = None

    if direct_data:
        logger.info("[Chaldea] 匹配到长链接数据特征，开启离线解码...")
        share_data = decode_content(direct_data)
        team_id = "offline"
        quest_id = (share_data.get("quest") or {}).get("id", "0") if share_data else "0"
    elif team_id:
        team_resp = fetch_team_by_id(team_id)
        if team_resp and "content" in team_resp:
            share_data = decode_content(team_resp["content"])
            quest_id = team_resp.get("questId", "0")
        else:
            logger.error("[Chaldea] 队伍接口无匹配数据。")
            return None
    elif quest_id:
        teams = fetch_teams_by_quest(quest_id, 3, 10)
        best = select_best_team(teams)
        if best and "content" in best:
            share_data = decode_content(best["content"])
            team_id = best.get("id", "top")
        else:
            logger.error("[Chaldea] 该关卡无可用队伍数据。")
            return None
    else:
        logger.error("[Chaldea] 无法解析输入来源。")
        return None

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
