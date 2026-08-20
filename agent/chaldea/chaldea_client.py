"""
Chaldea API 客户端 + 内容解码 + 用户输入解析
"""

import json
import ssl
import re
import base64
import gzip
import urllib.request
import logging
from typing import Optional, List, Tuple

import mfaalog

logger = logging.getLogger(__name__)

CHALDEA_API = "https://worker.chaldea.center/api/v4"


def fetch_teams_by_quest(quest_id: int, phase: int = 3, limit: int = 5) -> List[dict]:
    """
    从 Chaldea API 按关卡搜索队伍

    返回:
        UserBattleData 列表，失败返回空列表
    """
    if not isinstance(quest_id, int) or quest_id <= 0:
        logger.error(f"[Chaldea] 无效的 quest_id: {quest_id}")
        return []

    url = f"{CHALDEA_API}/quest/{quest_id}/team?phase={phase}&page=1&limit={limit}&free=true"
    logger.info(f"[Chaldea] 请求关卡队伍排行榜: {url}")

    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={"User-Agent": "MaaFgo/1.0"})
        resp = urllib.request.urlopen(req, timeout=15, context=ctx)
        data = json.loads(resp.read().decode("utf-8"))
        teams = data.get("data", [])
        logger.info(f"[Chaldea] 获取到 {len(teams)} 个队伍")
        return teams
    except Exception as e:
        logger.error(f"[Chaldea] 关卡API请求失败: {e}")
        return []


def fetch_team_by_id(team_id: int) -> Optional[dict]:
    """获取单个队伍详情"""
    if not isinstance(team_id, int) or team_id <= 0:
        logger.error(f"[Chaldea] 无效的 team_id: {team_id}")
        return None

    url = f"{CHALDEA_API}/team/{team_id}"
    logger.info(f"[Chaldea] 请求单独队伍配置: {url}")

    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={"User-Agent": "MaaFgo/1.0"})
        resp = urllib.request.urlopen(req, timeout=15, context=ctx)
        return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.error(f"[Chaldea] 队伍API请求失败: {e}")
        return None


def select_best_team(teams: List[dict]) -> Optional[dict]:
    """从队伍列表中选择投票最高的队伍"""
    if not teams:
        return None

    def vote_score(t: dict) -> int:
        votes = t.get("votes", {})
        return votes.get("up", 0) - votes.get("down", 0)

    return max(teams, key=vote_score)


def decode_content(content: str) -> Optional[dict]:
    """
    解码 UserBattleData.content 字段

    编码方式 (ver=2): JSON → gzip → base64url → 加 'G' 前缀
    编码方式 (ver=1): JSON → gzip → base64 (以 'H4s' 开头)
    """
    if not isinstance(content, str) or not content:
        logger.error("[Chaldea] content 为空或类型错误")
        mfaalog.error("[Chaldea] decode_content: content 为空或类型错误")
        return None

    try:
        if content.startswith("G"):
            b64_data = content[1:]
            mfaalog.info(f"[Chaldea] decode_content: V2格式, 原始长度={len(content)}")
        elif content.startswith("H4s"):
            b64_data = content
            mfaalog.info(f"[Chaldea] decode_content: V1格式, 原始长度={len(content)}")
        else:
            logger.error(f"[Chaldea] 未知 content 格式: {content[:20]}...")
            mfaalog.error(f"[Chaldea] decode_content: 未知格式, 前20字符={content[:20]}")
            return None

        # 补齐 base64 padding
        padding = 4 - len(b64_data) % 4
        if padding != 4:
            b64_data += "=" * padding

        raw = base64.urlsafe_b64decode(b64_data)
        decompressed = gzip.decompress(raw)
        result = json.loads(decompressed.decode("utf-8"))
        mfaalog.info(f"[Chaldea] decode_content: 解码成功, 数据keys={list(result.keys())}")
        return result
    except Exception as e:
        logger.error(f"[Chaldea] content 解码失败: {e}")
        mfaalog.error(f"[Chaldea] decode_content: 解码失败: {e}")
        return None


def parse_import_source(source: str) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    """
    智能解析用户的输入来源

    支持格式:
    - 纯数字 (<=6位): team_id
    - 纯数字 (>6位): quest_id
    - URL 含 data= 参数: 离线压缩数据
    - URL 含 id= 参数: team_id

    返回:
        (quest_id, team_id, direct_data)
    """
    if not isinstance(source, str) or not source.strip():
        mfaalog.warning("[Chaldea] parse_import_source: source 为空")
        return None, None, None

    source = source.strip()
    mfaalog.info(f"[Chaldea] parse_import_source: 原始输入={source[:120]}")

    if source.isdigit():
        num = int(source)
        if len(source) <= 6:
            mfaalog.info(f"[Chaldea] 解析为 team_id={num}")
            return None, num, None  # team_id
        else:
            mfaalog.info(f"[Chaldea] 解析为 quest_id={num}")
            return num, None, None  # quest_id

    match_data = re.search(r'data=([A-Za-z0-9\-_]+)', source)
    if match_data:
        data_val = match_data.group(1)
        mfaalog.info(f"[Chaldea] 解析为 data= 离线数据, 长度={len(data_val)}")
        return None, None, data_val

    match_id = re.search(r'id=(\d+)', source)
    if match_id:
        id_val = int(match_id.group(1))
        mfaalog.info(f"[Chaldea] 解析URL中 id={id_val}")
        return None, id_val, None

    mfaalog.warning(f"[Chaldea] 无法解析输入来源: {source[:120]}")
    return None, None, None
