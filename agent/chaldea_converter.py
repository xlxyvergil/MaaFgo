"""
Chaldea BattleShareData → BBchannel settings JSON 转换器

此模块负责将 Chaldea 队伍分享数据转换为 BBchannel 可识别的队伍配置格式。

设计原则:
1. 防御性编程: 所有外部输入都经过验证和清理
2. 类型安全: 使用类型注解和运行时类型检查
3. 错误隔离: 每个转换步骤独立，失败不扩散
4. 向后兼容: 支持新旧格式的 Chaldea 数据
5. 可测试性: 纯函数设计，便于单元测试

作者: MaaFgo Team
版本: 2.0.0
"""

import json
import urllib.request
import urllib.parse
import ssl
import base64
import gzip
import logging
import re
import os
from typing import Optional, Dict, List, Tuple, Any, Union
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)

CHALDEA_API = "https://worker.chaldea.center/api/v4"
ATLAS_API = "https://api.atlasacademy.io"


# ============================================================
# 类型定义与常量
# ============================================================

class SupportType(Enum):
    """助战类型枚举"""
    NONE = "none"
    FRIEND = "friend"
    FIXED = "fixed"
    NPC = "npc"


@dataclass
class ServantInfo:
    """从者信息数据结构"""
    svt_id: Optional[int] = None
    name: Optional[str] = None
    lv: int = 1
    skill_lvs: List[int] = field(default_factory=lambda: [10, 10, 10])
    append_lvs: List[int] = field(default_factory=list)
    td_lv: int = 1
    atk_fou: int = 1000
    hp_fou: int = 1000
    ce_id: Optional[int] = None
    ce_limit_break: bool = False
    ce_lv: int = 0
    support_type: SupportType = SupportType.NONE
    is_on_field: bool = True
    position: int = 0  # 0-5


@dataclass
class MysticCodeInfo:
    """魔术礼装信息"""
    mystic_code_id: int = 0
    level: int = 10


@dataclass
class TeamFormation:
    """队伍编成信息"""
    on_field: List[Optional[ServantInfo]] = field(default_factory=lambda: [None, None, None])
    backup: List[Optional[ServantInfo]] = field(default_factory=lambda: [None, None, None])
    mystic_code: MysticCodeInfo = field(default_factory=MysticCodeInfo)


@dataclass
class ConvertedConfig:
    """转换后的 BBC 配置"""
    servant_names: List[Optional[str]] = field(default_factory=lambda: [None] * 6)
    assist_idx: int = 2
    assist_mode: str = "从者礼装"
    assist_equip: Union[str, List[Optional[str]]] = field(default_factory=lambda: [None, None, None])
    master_equip: int = 0
    used_servant: List[int] = field(default_factory=list)
    rounds: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


# ============================================================
# Chaldea mysticCodeId → BBC master_equip SN 映射表
# Chaldea/Atlas Academy 使用的是游戏内部 ID (1, 20, 30, ...)
# BBC 使用的是自增序号 (0, 1, 2, ...)
# ============================================================
MYSTIC_CODE_ID_TO_BBC_SN: Dict[int, int] = {
    1:   7,   # 魔术礼装·迦勒底
    20:  6,   # 魔术礼装·迦勒底战斗服 (换人服)
    30:  9,   # 魔术礼装·魔术协会制服
    40:  1,   # 魔术礼装·阿特拉斯院制服
    50:  3,   # 金色庆典
    60:  12,  # 王室品牌
    70:  11,  # 明亮夏日
    80:  14,  # 月之海的记忆
    90:  15,  # 月之背面的记忆
    100: 0,   # 2004年的碎片
    110: 8,   # 魔术礼装·极地用迦勒底制服
    120: 10,  # 热带夏日
    130: 13,  # 华美的新年
    150: 4,   # 迦勒底船长
    160: 2,   # 第五真说要素环境用迦勒底制服
    170: 5,   # 迦勒底开拓者
    190: 16,  # 万圣夜王室装
    210: 17,  # 决战用迦勒底制服
    240: 18,  # 总耶高校学生服
    260: 19,  # 新春装束
    330: 20,  # 夏日街头
    340: 21,  # 白色圣诞
    360: 22,  # 三咲高校学生服
    410: 23,  # 冬日便装
    430: 24,  # 浅葱的队服
    440: 25,  # 标准·迦勒底制服
}

# 换人服的 mysticCodeId 列表
ORDER_CHANGE_MYSTIC_CODE_IDS: set = {20, 210, 440}

# ============================================================
# 缓存：从者/礼装名称映射
# ============================================================
_servant_name_cache: Dict[int, str] = {}
_equip_name_cache: Dict[int, str] = {}
_bbc_servant_sn_cache: Dict[int, str] = {}  # svtId -> SN 映射
_cache_loaded: bool = False
_cache_load_attempted: bool = False


def _ensure_cache_loaded() -> None:
    """
    确保名称缓存已加载，采用两级策略:

    1. 本地 JSON 数据库 (agent/data/servant_names_CN.json)
       由 tools/update_chaldea_data.py 生成，完全离线，无需网络。
       运行一次更新脚本后永久有效。

    2. Atlas Academy API (网络备用，30秒超时)
       在本地数据库不存在或为空时触发。

    3. 全部失败 → 使用 fallback 名称 "从者_{svtId}"（BBC 无法识别助战）
    """
    global _cache_loaded, _cache_load_attempted
    if _cache_loaded or _cache_load_attempted:
        return
    _cache_load_attempted = True

    # ---- 第一级: 本地 JSON 数据库 ----
    _agent_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(_agent_dir, "data")
    servant_path = os.path.join(data_dir, "servant_names_CN.json")
    equip_path = os.path.join(data_dir, "equip_names_CN.json")

    local_servant_ok = False
    if os.path.exists(servant_path):
        try:
            with open(servant_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for key, name in raw.items():
                if key.startswith("_"):
                    continue  # 跳过 _readme 等元数据键
                try:
                    _servant_name_cache[int(key)] = name
                except ValueError:
                    pass
            if _servant_name_cache:
                logger.info(f"[Chaldea] 从本地数据库加载从者名称: {len(_servant_name_cache)} 条")
                local_servant_ok = True
            else:
                logger.warning("[Chaldea] 本地从者数据库为空，请运行 tools/update_chaldea_data.py")
        except Exception as e:
            logger.warning(f"[Chaldea] 本地从者数据库加载失败: {e}")
    else:
        logger.warning(f"[Chaldea] 本地从者数据库不存在: {servant_path}，请运行 tools/update_chaldea_data.py")

    if os.path.exists(equip_path):
        try:
            with open(equip_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for key, name in raw.items():
                if key.startswith("_"):
                    continue
                try:
                    _equip_name_cache[int(key)] = name
                except ValueError:
                    pass
            if _equip_name_cache:
                logger.info(f"[Chaldea] 从本地数据库加载礼装名称: {len(_equip_name_cache)} 条")
        except Exception as e:
            logger.warning(f"[Chaldea] 本地礼装数据库加载失败: {e}")

    if local_servant_ok:
        _cache_loaded = True
        return

    # ---- 第二级: Atlas Academy API (需要网络) ----
    logger.info("[Chaldea] 本地数据库不可用，尝试从 Atlas Academy API 联网获取...")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    atlas_ok = False
    try:
        url = f"{ATLAS_API}/export/CN/nice_servant.json"
        logger.info(f"[Chaldea] 加载从者名称缓存: {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "MaaFgo/1.0"})
        resp = urllib.request.urlopen(req, timeout=30, context=ctx)
        data = json.loads(resp.read().decode("utf-8"))
        for svt in data:
            svt_id = svt.get("id")
            name = svt.get("name")
            if svt_id is not None and name:
                _servant_name_cache[svt_id] = name
                collection_no = svt.get("collectionNo")
                if collection_no:
                    _bbc_servant_sn_cache[svt_id] = str(collection_no).zfill(3) + "00"
        logger.info(f"[Chaldea] 从者名称缓存加载完成: {len(_servant_name_cache)} 条")
        atlas_ok = True
    except Exception as e:
        logger.warning(f"[Chaldea] Atlas API 从者名称加载失败: {e}")

    try:
        url = f"{ATLAS_API}/export/CN/nice_equip.json"
        req = urllib.request.Request(url, headers={"User-Agent": "MaaFgo/1.0"})
        resp = urllib.request.urlopen(req, timeout=30, context=ctx)
        data = json.loads(resp.read().decode("utf-8"))
        for eq in data:
            eq_id = eq.get("id")
            eq_name = eq.get("name")
            if eq_id is not None and eq_name:
                _equip_name_cache[eq_id] = eq_name
        logger.info(f"[Chaldea] 礼装名称缓存加载完成: {len(_equip_name_cache)} 条")
    except Exception as e:
        logger.warning(f"[Chaldea] Atlas API 礼装名称加载失败: {e}")

    if atlas_ok:
        _cache_loaded = True
        return

    # ---- 第三级: 全部失败 ----
    logger.warning("[Chaldea] 所有名称数据源均失败，将使用 fallback 名称（从者_{svtId}）")
    _cache_loaded = True


def get_servant_name(svt_id: Optional[int]) -> str:
    """
    获取从者中文名称
    
    参数:
        svt_id: Atlas Academy 从者 ID (如 504500)
    
    返回:
        中文名称，缓存未命中时返回 '从者_{svtId}'
    """
    if svt_id is None:
        return "从者_未知"
    _ensure_cache_loaded()
    return _servant_name_cache.get(svt_id, f"从者_{svt_id}")


def get_equip_name(ce_id: Optional[int]) -> Optional[str]:
    """
    获取礼装中文名称
    
    参数:
        ce_id: Atlas Academy 礼装 ID
    
    返回:
        中文名称，缓存未命中时返回 None
    """
    if ce_id is None:
        return None
    _ensure_cache_loaded()
    return _equip_name_cache.get(ce_id)


def get_master_equip_sn(mystic_code_id: int) -> int:
    """
    将 Chaldea mysticCodeId 转换为 BBC master_equip SN
    
    参数:
        mystic_code_id: Chaldea 魔术礼装 ID (如 20)
    
    返回:
        BBC SN 编号，未知 ID 返回 0 (默认)
    """
    if not isinstance(mystic_code_id, int) or mystic_code_id < 0:
        logger.warning(f"[Chaldea] 无效的 mysticCodeId: {mystic_code_id}，使用默认值 0")
        return 0
    sn = MYSTIC_CODE_ID_TO_BBC_SN.get(mystic_code_id, 0)
    if mystic_code_id not in MYSTIC_CODE_ID_TO_BBC_SN:
        logger.warning(f"[Chaldea] 未知的 mysticCodeId: {mystic_code_id}，使用默认 SN=0")
    return sn

# 默认常规指令卡优先级策略 (从 BBC 配置文件中提取的经典保底策略)
DEFAULT_STRATEGY: List[Dict[str, Any]] = [
    {
        "card1": {
            "type": 0,
            "cards": [1],
            "criticalStar": 0,
            "more_or_less": True
        },
        "card2": {
            "type": 1,
            "cards": ["1A", "1B", "1Q", "2B", "3B", "2A", "3A", "2Q", "3Q"],
            "criticalStar": 0,
            "more_or_less": True
        },
        "card3": {
            "type": 1,
            "cards": ["1A", "1B", "1Q", "2B", "3B", "2A", "3A", "2Q", "3Q"],
            "criticalStar": 0,
            "more_or_less": True
        },
        "breakpoint": [False, False],
        "colorFirst": True
    }
]

# ============================================================
# API 层：获取 Chaldea 队伍数据
# ============================================================

def fetch_teams_by_quest(quest_id: int, phase: int = 3, limit: int = 5) -> List[dict]:
    """
    从 Chaldea API 按关卡搜索队伍
    
    参数:
        quest_id: Atlas Academy 关卡 ID (如 94086601)
        phase: 关卡阶段 (通常为 3)
        limit: 返回数量上限
    
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
    """
    获取单个队伍详情
    
    参数:
        team_id: Chaldea 队伍 ID
    
    返回:
        UserBattleData 或 None
    """
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
    """
    从队伍列表中选择投票最高的队伍
    
    参数:
        teams: fetch_teams_by_quest 返回的列表
    
    返回:
        投票净值最高的 UserBattleData，或 None
    """
    if not teams:
        return None
    
    def vote_score(t: dict) -> int:
        votes = t.get("votes", {})
        return votes.get("up", 0) - votes.get("down", 0)
    
    return max(teams, key=vote_score)

# ============================================================
# 解码层：解析 Chaldea 数据格式
# ============================================================

def decode_content(content: str) -> Optional[dict]:
    """
    解码 UserBattleData.content 字段
    
    编码方式 (ver=2): JSON → gzip → base64url → 加 'G' 前缀
    编码方式 (ver=1): JSON → gzip → base64 (以 'H4s' 开头)
    
    参数:
        content: 编码后的字符串 (以 'G' 或 'H4s' 开头)
    
    返回:
        BattleShareData (dict)，失败返回 None
    """
    if not isinstance(content, str) or not content:
        logger.error("[Chaldea] content 为空或类型错误")
        return None
    
    try:
        if content.startswith("G"):
            # V2 格式: 'G' + base64url(gzip(JSON))
            b64_data = content[1:]
        elif content.startswith("H4s"):
            # V1 格式: base64(gzip(JSON))
            b64_data = content
        else:
            logger.error(f"[Chaldea] 未知 content 格式: {content[:20]}...")
            return None

        # 补齐 base64 padding
        padding = 4 - len(b64_data) % 4
        if padding != 4:
            b64_data += "=" * padding

        raw = base64.urlsafe_b64decode(b64_data)
        decompressed = gzip.decompress(raw)
        return json.loads(decompressed.decode("utf-8"))
    except Exception as e:
        logger.error(f"[Chaldea] content 解码失败: {e}")
        return None

# ============================================================
# 转换层：Chaldea → BBchannel
# ============================================================

def _parse_support_type(support_type_str: Optional[str]) -> SupportType:
    """解析助战类型字符串"""
    if not support_type_str:
        return SupportType.NONE
    try:
        return SupportType(support_type_str.lower())
    except ValueError:
        return SupportType.NONE


def _extract_ce_id(svt_info: dict) -> Optional[int]:
    """
    从从者信息中提取概念礼装 ID
    
    支持多种格式:
    - 新版: equip1.id
    - 旧版: ceId
    """
    if not isinstance(svt_info, dict):
        return None
    
    # 优先使用 equip1.id (Chaldea 新版格式)
    equip1 = svt_info.get("equip1")
    if isinstance(equip1, dict) and equip1.get("id"):
        return equip1["id"]
    
    # 兼容旧格式 ceId
    ce_id = svt_info.get("ceId")
    if ce_id is not None:
        return ce_id
    
    return None


def _convert_svt_info(svt_data: Optional[dict], position: int, is_on_field: bool) -> Optional[ServantInfo]:
    """
    将 Chaldea 从者数据转换为内部 ServantInfo
    
    参数:
        svt_data: Chaldea 从者数据
        position: 位置 (0-5)
        is_on_field: 是否在场
    
    返回:
        ServantInfo 或 None (空位)
    """
    if svt_data is None:
        return None
    if not isinstance(svt_data, dict):
        logger.warning(f"[Chaldea] 无效的从者数据类型: {type(svt_data)}")
        return None
    
    # 提取技能等级，确保长度为3
    skill_lvs = svt_data.get("skillLvs", [])
    if not isinstance(skill_lvs, list) or len(skill_lvs) < 3:
        skill_lvs = [10, 10, 10]  # 默认满级
    
    # 提取追加技能等级
    append_lvs = svt_data.get("appendLvs", [])
    if not isinstance(append_lvs, list):
        append_lvs = []
    
    return ServantInfo(
        svt_id=svt_data.get("svtId"),
        name=None,  # 稍后通过缓存获取
        lv=svt_data.get("lv", 1),
        skill_lvs=skill_lvs[:3],
        append_lvs=append_lvs,
        td_lv=svt_data.get("tdLv", 1),
        atk_fou=svt_data.get("atkFou", 1000),
        hp_fou=svt_data.get("hpFou", 1000),
        ce_id=_extract_ce_id(svt_data),
        ce_limit_break=svt_data.get("ceLimitBreak", False),
        ce_lv=svt_data.get("ceLv", 0),
        support_type=_parse_support_type(svt_data.get("supportType")),
        is_on_field=is_on_field,
        position=position
    )


def _convert_formation(team_data: dict) -> TeamFormation:
    """
    转换队伍编成数据
    
    参数:
        team_data: Chaldea team 字段
    
    返回:
        TeamFormation
    """
    formation = TeamFormation()
    
    # 处理场上从者
    on_field_svts = team_data.get("onFieldSvts", [])
    if isinstance(on_field_svts, list):
        for i in range(3):
            svt_data = on_field_svts[i] if i < len(on_field_svts) else None
            formation.on_field[i] = _convert_svt_info(svt_data, i, True)
    
    # 处理后备从者
    backup_svts = team_data.get("backupSvts", [])
    if isinstance(backup_svts, list):
        for i in range(3):
            svt_data = backup_svts[i] if i < len(backup_svts) else None
            formation.backup[i] = _convert_svt_info(svt_data, i + 3, False)
    
    # 处理魔术礼装
    mystic_code = team_data.get("mysticCode", {})
    if isinstance(mystic_code, dict):
        formation.mystic_code = MysticCodeInfo(
            mystic_code_id=mystic_code.get("mysticCodeId", 0),
            level=mystic_code.get("level", 10)
        )
    
    return formation


def _determine_assist_info(formation: TeamFormation) -> Tuple[int, str, Union[str, List[Optional[str]]], List[int]]:
    """
    确定助战相关信息
    
    参数:
        formation: 队伍编成
    
    返回:
        (assist_idx, assist_mode, assist_equip, used_servant)
    """
    assist_idx = None
    assist_mode = "从者礼装"
    assist_equip: Union[str, List[Optional[str]]] = [None, None, None]
    used_servant: List[int] = []
    
    # 查找助战从者
    for i, svt in enumerate(formation.on_field):
        if svt is None:
            continue
        if svt.support_type in (SupportType.FRIEND, SupportType.FIXED):
            assist_idx = i
            # 获取助战礼装名称
            if svt.ce_id is not None:
                ce_name = get_equip_name(svt.ce_id)
                if ce_name:
                    # 根据 BBC 格式，可以是字符串或数组
                    assist_equip = ce_name
                    assist_mode = "从者礼装"
        else:
            used_servant.append(i)
    
    # 如果没有找到助战，使用默认配置
    if assist_idx is None:
        assist_idx = 2  # BBC 默认助战位置
        # 重新计算 used_servant
        used_servant = [i for i in range(3) if formation.on_field[i] is not None and i != assist_idx]
        if not used_servant:
            used_servant = [0, 1]  # 默认使用位置 0 和 1
    
    return assist_idx, assist_mode, assist_equip, used_servant


def convert_actions_to_bbc_rounds(
    actions: List[dict],
    delegate: Optional[dict] = None,
    mystic_code_id: int = 0
) -> Dict[str, Any]:
    """
    将 Chaldea actions 转换为 BBC 回合配置
    
    BBC 技能格式:
    - 单个数字 n: 使用技能 n (不需要选目标)
    - [n, t]: 使用技能 n 并选择己方目标 t (1-based)
    - [13, t]: 选择第 t 个敌方目标
    - [-2, backup_idx]: 换人 (与后备交换)
    
    技能编号映射:
    - 1-3: 从者0的技能1-3
    - 4-6: 从者1的技能1-3
    - 7-9: 从者2的技能1-3
    - 10-12: 御主技能1-3
    
    参数:
        actions: Chaldea actions 列表
        delegate: 包含换人信息的 delegate 字段
        mystic_code_id: 魔术礼装 ID (用于判断是否为换人服)
    
    返回:
        BBC 回合配置字典
    """
    rounds_config: Dict[str, Any] = {}
    current_skills: List[Union[int, List]] = []
    current_nps: List[int] = []
    round_idx = 1
    turn_idx = 0

    # 获取换人信息
    replace_members: List[List[int]] = []
    if isinstance(delegate, dict):
        replace_members = delegate.get("replaceMemberIndexes", [])
    replace_ptr = 0
    
    # 判断是否为换人服
    is_order_change = (mystic_code_id in ORDER_CHANGE_MYSTIC_CODE_IDS)

    for action in actions:
        if not isinstance(action, dict):
            continue
            
        action_type = action.get("type", "")

        if action_type == "skill":
            svt_idx = action.get("svt")  # None 表示御主技能
            skill_idx = action.get("skill", 0)
            options = action.get("options", {}) or {}

            if svt_idx is None:
                # 御主技能
                bbc_skill_idx = 10 + skill_idx
                
                # 处理换人服技能
                if bbc_skill_idx == 12 and is_order_change:
                    if replace_ptr < len(replace_members):
                        # replace_members 格式: [[field_idx, backup_idx], ...]
                        # BBC 格式: [-2, backup_idx] (1-based)
                        backup_idx = replace_members[replace_ptr][1] + 1
                        current_skills.append([-2, backup_idx])
                        replace_ptr += 1
                    else:
                        logger.warning("[Chaldea] 换人技能数量超过 delegate 中定义的换人信息")
                        current_skills.append([-2, 1])  # fallback
                    continue
            else:
                # 从者技能: (0~2) * 3 + (0~2) + 1 = 1~9
                if not isinstance(svt_idx, int) or not (0 <= svt_idx <= 5):
                    logger.warning(f"[Chaldea] 无效的从者索引: {svt_idx}")
                    continue
                bbc_skill_idx = svt_idx * 3 + skill_idx + 1
            
            # 处理己方目标选择
            player_target = options.get("playerTarget")
            if player_target is not None and isinstance(player_target, int) and player_target >= 0:
                # BBC 使用 1-based 索引
                current_skills.append([bbc_skill_idx, player_target + 1])
            else:
                current_skills.append(bbc_skill_idx)

        elif action_type == "attack":
            attacks = action.get("attacks", [])
            if isinstance(attacks, list):
                for atk in attacks:
                    if not isinstance(atk, dict):
                        continue
                    if atk.get("isTD", False):
                        svt_pos = atk.get("svt", 0)
                        if isinstance(svt_pos, int) and 0 <= svt_pos <= 5:
                            np_pos = svt_pos + 1  # 转换为 1-based
                            if np_pos not in current_nps:
                                current_nps.append(np_pos)

            # 保存当前回合配置
            rounds_config[f"round{round_idx}_turns"] = 1
            rounds_config[f"round{round_idx}_extraSkill"] = []
            rounds_config[f"round{round_idx}_extraStrategy"] = None
            rounds_config[f"round{round_idx}_turn{turn_idx}_skill"] = current_skills.copy()
            rounds_config[f"round{round_idx}_turn{turn_idx}_np"] = current_nps.copy()
            rounds_config[f"round{round_idx}_turn{turn_idx}_strategy"] = None
            rounds_config[f"round{round_idx}_turn{turn_idx}_condition"] = None
            
            # 重置当前回合数据
            current_skills = []
            current_nps = []
            round_idx += 1

    return rounds_config

def chaldea_to_bbc(share_data: dict) -> dict:
    """
    主转换函数: Chaldea BattleShareData → BBchannel 配置
    
    参数:
        share_data: 解码后的 BattleShareData
    
    返回:
        BBchannel 配置 dict，可直接 json.dump 保存为 settings/*.json
    """
    if not isinstance(share_data, dict):
        logger.error("[Chaldea] share_data 类型错误，期望 dict")
        return {}
    
    team_data = share_data.get("team", {})
    actions = share_data.get("actions", [])
    delegate = share_data.get("delegate") or {}
    options = share_data.get("options", {})
    
    if not isinstance(team_data, dict):
        logger.error("[Chaldea] team 字段类型错误")
        return {}
    
    # ---- 解析队伍编成 ----
    formation = _convert_formation(team_data)
    mystic_code_id = formation.mystic_code.mystic_code_id
    
    # ---- 确定助战信息 ----
    assist_idx, assist_mode, assist_equip, used_servant = _determine_assist_info(formation)
    
    # ---- 构建结果字典 ----
    result: Dict[str, Any] = {
        # 元数据 (BBC 应忽略未知字段，仅供调试)
        "_source": "chaldea",
        "_questId": (share_data.get("quest") or {}).get("id"),
        "_appBuild": share_data.get("appBuild"),
    }
    
    # ---- 从者名称映射 ----
    all_svts = list(formation.on_field) + list(formation.backup)
    for i in range(6):
        svt = all_svts[i] if i < len(all_svts) else None
        if svt is not None and svt.svt_id is not None:
            result[f"servant_{i}_name"] = get_servant_name(svt.svt_id)
        else:
            result[f"servant_{i}_name"] = None
    
    # ---- 助战相关字段 ----
    result["assistMode"] = assist_mode
    result["assistIdx"] = assist_idx
    result["assistEquip"] = assist_equip
    
    # ---- 魔术礼装映射 ----
    result["master_equip"] = get_master_equip_sn(mystic_code_id)
    
    # ---- 使用助战的从者位置 ----
    result["usedServant"] = used_servant
    
    # ---- 默认设备配置 ----
    result["connectMode"] = "ADB方式"
    result["snapshotDevice"] = ["normal", "127.0.0.1:7555"]
    result["operateDevice"] = ["normal", "127.0.0.1:7555"]
    result["specialKeys"] = []
    
    # ---- 回合战斗逻辑操作序列 ----
    if isinstance(actions, list):
        bbc_actions = convert_actions_to_bbc_rounds(actions, delegate, mystic_code_id)
        result.update(bbc_actions)
    else:
        logger.warning("[Chaldea] actions 字段不是列表，跳过战斗逻辑转换")
    
    return result


def parse_import_source(source: str) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    """
    智能解析用户的输入来源
    
    支持格式:
    - 纯数字 (<=6位): team_id
    - 纯数字 (>6位): quest_id
    - URL 含 data= 参数: 离线压缩数据
    - URL 含 id= 参数: team_id
    
    参数:
        source: 用户输入的字符串
    
    返回:
        (quest_id, team_id, direct_data)
        - direct_data 有值时，直接走本地免网络解析
    """
    if not isinstance(source, str) or not source.strip():
        return None, None, None
    
    source = source.strip()
    
    # 纯数字判断
    if source.isdigit():
        num = int(source)
        if len(source) <= 6:
            return None, num, None  # team_id
        else:
            return num, None, None  # quest_id

    # 包含长串压缩数据 data=GH4...
    match_data = re.search(r'data=([A-Za-z0-9\-_]+)', source)
    if match_data:
        return None, None, match_data.group(1)
        
    # 包含短链接 ID id=...
    match_id = re.search(r'id=(\d+)', source)
    if match_id:
        return None, int(match_id.group(1)), None

    return None, None, None


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


# ============================================================
# 验证层：校验转换结果
# ============================================================

def validate_bbc_config(config: dict) -> List[str]:
    """
    校验 BBC 配置的完整性和正确性
    
    参数:
        config: chaldea_to_bbc 的输出
    
    返回:
        错误信息列表，空列表表示校验通过
    """
    errors: List[str] = []
    
    if not isinstance(config, dict):
        errors.append("配置不是有效的字典")
        return errors
    
    # 检查必需字段
    required_fields = ["master_equip", "assistIdx", "assistMode", "usedServant"]
    for field in required_fields:
        if field not in config:
            errors.append(f"缺少必需字段: {field}")
    
    # 检查从者名称
    servant_count = 0
    for i in range(6):
        name = config.get(f"servant_{i}_name")
        if name is not None:
            servant_count += 1
    if servant_count == 0:
        errors.append("没有有效的从者信息")
    
    # 检查助战索引
    assist_idx = config.get("assistIdx")
    if assist_idx is not None and not (0 <= assist_idx <= 2):
        errors.append(f"助战索引超出范围: {assist_idx} (应为 0-2)")
    
    # 检查魔术礼装
    master_equip = config.get("master_equip")
    if master_equip is not None and not isinstance(master_equip, int):
        errors.append(f"魔术礼装 SN 类型错误: {type(master_equip)}")
    
    # 检查回合配置
    round_count = sum(1 for k in config.keys() if k.endswith("_turns"))
    if round_count == 0:
        errors.append("没有回合配置数据")
    
    for i in range(1, round_count + 1):
        skill_key = f"round{i}_turn0_skill"
        np_key = f"round{i}_turn0_np"
        if skill_key not in config:
            errors.append(f"第 {i} 回合缺少技能配置: {skill_key}")
        if np_key not in config:
            errors.append(f"第 {i} 回合缺少宝具配置: {np_key}")
    
    return errors

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Chaldea → BBchannel 队伍配置转换器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python chaldea_converter.py --source 94061640
  python chaldea_converter.py --source 17300
  python chaldea_converter.py --source "https://chaldea.center/team?id=17300"
  python chaldea_converter.py --source "data=GH4sI..." --outd ./settings
        """
    )
    parser.add_argument("--source", type=str, required=True, help="关卡ID/队伍ID/URL/压缩数据")
    parser.add_argument("--outd", type=str, default=".", help="输出目录")
    parser.add_argument("--validate", action="store_true", help="转换后验证配置")
    args = parser.parse_args()
    
    result = fetch_and_convert(args.source, args.outd)
    
    if result:
        print(f"\n✓ 转换成功: {result}")
        
        if args.validate:
            filepath = os.path.join(args.outd, result)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    config = json.load(f)
                errors = validate_bbc_config(config)
                if errors:
                    print("\n⚠ 配置验证警告:")
                    for err in errors:
                        print(f"  - {err}")
                else:
                    print("\n✓ 配置验证通过")
            except Exception as e:
                print(f"\n✗ 验证失败: {e}")
    else:
        print("\n✗ 转换失败")
        exit(1)
