import json
import urllib.request
import urllib.parse
import ssl
import base64
import gzip
import logging
import re
import os
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)

CHALDEA_API = "https://worker.chaldea.center/api/v4"
ATLAS_API = "https://api.atlasacademy.io"

# ============================================================
# Chaldea mysticCodeId → BBC master_equip SN 映射表
# Chaldea/Atlas Academy 使用的是游戏内部 ID (1, 20, 30, ...)
# BBC 使用的是自增序号 (0, 1, 2, ...)
# ============================================================
MYSTIC_CODE_ID_TO_BBC_SN = {
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

# 换人服的 mysticCodeId 列表 (BBC 中 SN=6 和 SN=17 和 SN=25 都有换人技能)
ORDER_CHANGE_MYSTIC_CODE_IDS = {20, 210, 440}

# ============================================================
# 缓存：从者/礼装名称映射 (svtId/ceId → 中文名)
# ============================================================
_servant_name_cache: Dict[int, str] = {}
_equip_name_cache: Dict[int, str] = {}
_cache_loaded = False


def _load_atlas_caches():
    """从 Atlas Academy API 加载从者和礼装名称缓存"""
    global _cache_loaded
    if _cache_loaded:
        return

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    # 加载从者名称
    try:
        url = f"{ATLAS_API}/export/CN/nice_servant.json"
        logger.info(f"[Chaldea] 加载从者名称缓存: {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "MaaFgo/1.0"})
        resp = urllib.request.urlopen(req, timeout=30, context=ctx)
        data = json.loads(resp.read().decode("utf-8"))
        for svt in data:
            _servant_name_cache[svt["id"]] = svt["name"]
        logger.info(f"[Chaldea] 从者名称缓存加载完成: {len(_servant_name_cache)} 条")
    except Exception as e:
        logger.warning(f"[Chaldea] 从者名称缓存加载失败: {e}")

    # 加载礼装名称
    try:
        url = f"{ATLAS_API}/export/CN/nice_equip.json"
        logger.info(f"[Chaldea] 加载礼装名称缓存: {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "MaaFgo/1.0"})
        resp = urllib.request.urlopen(req, timeout=30, context=ctx)
        data = json.loads(resp.read().decode("utf-8"))
        for eq in data:
            _equip_name_cache[eq["id"]] = eq["name"]
        logger.info(f"[Chaldea] 礼装名称缓存加载完成: {len(_equip_name_cache)} 条")
    except Exception as e:
        logger.warning(f"[Chaldea] 礼装名称缓存加载失败: {e}")

    _cache_loaded = True


def _load_atlas_caches_from_bbc(bbc_path: str = None):
    """从 BBC 本地文件加载从者名称缓存 (备选方案，无需网络)"""
    global _cache_loaded
    if _cache_loaded:
        return

    if bbc_path is None:
        # 尝试自动查找
        candidates = [
            os.path.join(os.path.dirname(__file__), "..", "BBchannel"),
            os.path.join(os.path.dirname(__file__), "BBchannel"),
        ]
        for c in candidates:
            info_path = os.path.join(c, "servant_info_CH.json")
            if os.path.exists(info_path):
                bbc_path = c
                break

    if bbc_path is None:
        logger.warning("[Chaldea] 未找到 BBC 本地文件，跳过从者名称缓存")
        _cache_loaded = True
        return

    # 从 BBC servant_info_CH.json 加载 (SN → 名称)
    info_path = os.path.join(bbc_path, "servant_info_CH.json")
    if os.path.exists(info_path):
        try:
            with open(info_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for name, info in data.items():
                sn = info.get("SN")
                if sn is not None:
                    _servant_name_cache[int(sn)] = name
            logger.info(f"[Chaldea] 从 BBC 本地文件加载从者名称: {len(_servant_name_cache)} 条")
        except Exception as e:
            logger.warning(f"[Chaldea] BBC 本地从者名称加载失败: {e}")

    _cache_loaded = True


def get_servant_name(svt_id: int) -> str:
    """获取从者中文名称，优先缓存，fallback 为 '从者_{svtId}'"""
    if not _cache_loaded:
        _load_atlas_caches()
    return _servant_name_cache.get(svt_id, f"从者_{svt_id}")


def get_equip_name(ce_id: int) -> Optional[str]:
    """获取礼装中文名称，优先缓存，fallback 为 None"""
    if not _cache_loaded:
        _load_atlas_caches()
    return _equip_name_cache.get(ce_id)


def get_master_equip_sn(mystic_code_id: int) -> int:
    """将 Chaldea mysticCodeId 转换为 BBC master_equip SN"""
    return MYSTIC_CODE_ID_TO_BBC_SN.get(mystic_code_id, 0)

# 默认常规指令卡优先级策略 (从 BBC 配置文件中提取的经典保底策略)
DEFAULT_STRATEGY = [
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

def fetch_teams_by_quest(quest_id: int, phase: int = 3, limit: int = 5) -> list:
    url = f"{CHALDEA_API}/quest/{quest_id}/team?phase={phase}&page=1&limit={limit}&free=true"
    logger.info(f"[Chaldea] 请求关卡队伍排行榜: {url}")
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={"User-Agent": "MaaFgo/1.0"})
        resp = urllib.request.urlopen(req, timeout=15, context=ctx)
        data = json.loads(resp.read().decode("utf-8"))
        return data.get("data", [])
    except Exception as e:
        logger.error(f"[Chaldea] 关卡API请求失败: {e}")
        return []

def fetch_team_by_id(team_id: int) -> Optional[dict]:
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

def select_best_team(teams: list) -> Optional[dict]:
    if not teams:
        return None
    return max(
        teams,
        key=lambda t: t.get("votes", {}).get("up", 0) - t.get("votes", {}).get("down", 0)
    )

def decode_content(content: str) -> Optional[dict]:
    try:
        if content.startswith("G"):
            b64_data = content[1:]
        elif content.startswith("H4s"):
            b64_data = content
        else:
            logger.error(f"[Chaldea] 未知 content 格式: {content[:10]}")
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

def convert_actions_to_bbc_rounds(actions: list, delegate: dict = None, mystic_code_id: int = 0) -> dict:
    """将 Chaldea actions 转换为 BBC 回合配置
    
    BBC 技能格式:
    - 单个数字 n: 使用技能 n (不需要选目标, skill_type=0)
    - [n, t] (n <= 12): 使用技能 n 并选择己方目标 t (skill_type=1, 需选目标)
    - [13, t]: 选择第 t 个敌方目标 (用于宝具目标选择等)
    - [-2, backup_idx]: 换人 (与后备 backup_idx 交换)
    
    技能编号:
    - 1-3: 从者0的技能1-3
    - 4-6: 从者1的技能1-3
    - 7-9: 从者2的技能1-3
    - 10-12: 御主技能1-3
    """
    rounds_config = {}
    current_skills = []
    current_nps = []
    round_idx = 1
    turn_idx = 0

    replace_members = delegate.get("replaceMemberIndexes", []) if delegate else []
    replace_ptr = 0
    
    # 换人服判断：mysticCodeId 在换人服列表中
    is_order_change = (mystic_code_id in ORDER_CHANGE_MYSTIC_CODE_IDS)

    for action in actions:
        action_type = action.get("type", "")

        if action_type == "skill":
            svt_idx = action.get("svt")       # None 为御主技能
            skill_idx = action.get("skill", 0)
            options = action.get("options", {})

            if svt_idx is None:
                # 御主技能
                bbc_skill_idx = 10 + skill_idx
                if bbc_skill_idx == 12 and is_order_change:
                    # 换人服3技能：从 delegate 取出换人信息
                    if replace_ptr < len(replace_members):
                        field_idx = replace_members[replace_ptr][0] + 1
                        backup_idx = replace_members[replace_ptr][1] + 1
                        current_skills.append([-2, backup_idx])
                        replace_ptr += 1
                    else:
                        current_skills.append([-2, 1]) # fallback
                    continue
            else:
                # 从者技能 (0~2) * 3 + (0~2) + 1 = 1~9
                bbc_skill_idx = svt_idx * 3 + skill_idx + 1
            
            # 敌方目标选择 (enemyTarget: 0-based → BBC [13, 1-based])
            # BBC 中 [13, target] 是独立操作，应在技能之前执行
            enemy_target = options.get("enemyTarget")
            if enemy_target is not None and enemy_target >= 0:
                current_skills.append([13, enemy_target + 1])
            
            # 己方目标选择 (playerTarget: 0-based → BBC 1-based)
            player_target = options.get("playerTarget")
            if player_target is not None and player_target >= 0:
                current_skills.append([bbc_skill_idx, player_target + 1])
            else:
                current_skills.append(bbc_skill_idx)

        elif action_type == "attack":
            attacks = action.get("attacks", [])
            for atk in attacks:
                if atk.get("isTD", False):
                    svt_pos = atk.get("svt", 0) + 1
                    if svt_pos not in current_nps:
                        current_nps.append(svt_pos)

            # attack action 中也可能有 enemyTarget (宝具目标选择)
            options = action.get("options", {})
            enemy_target = options.get("enemyTarget")
            if enemy_target is not None and enemy_target >= 0:
                current_skills.append([13, enemy_target + 1])

            # 一个 attack 即一个 Round/Turn 结束（BB频道以Round作为分割回合结构）
            rounds_config[f"round{round_idx}_turns"] = 1
            rounds_config[f"round{round_idx}_extraSkill"] = []
            rounds_config[f"round{round_idx}_extraStrategy"] = None
            rounds_config[f"round{round_idx}_turn{turn_idx}_skill"] = current_skills.copy()
            rounds_config[f"round{round_idx}_turn{turn_idx}_np"] = current_nps.copy()
            rounds_config[f"round{round_idx}_turn{turn_idx}_strategy"] = None
            rounds_config[f"round{round_idx}_turn{turn_idx}_condition"] = None
            
            current_skills = []
            current_nps = []
            round_idx += 1

    return rounds_config

def chaldea_to_bbc(share_data: dict) -> dict:
    team = share_data.get("team", {})
    actions = share_data.get("actions", [])
    delegate = share_data.get("delegate", {})
    options = share_data.get("options", {})
    
    on_field_svts = list(team.get("onFieldSvts", []))
    backup_svts = list(team.get("backupSvts", []))
    all_svts = on_field_svts + backup_svts
    mystic_code = team.get("mysticCode", {})
    mystic_code_id = mystic_code.get("mysticCodeId", 0)
    
    # ---- 判断助战信息 ----
    # Chaldea supportType: "friend" / "none" / "fixed" 等
    # BBC assistMode: "从者礼装" / "概念礼装" 等
    # BBC assistIdx: 助战从者在场上位置 (0/1/2)
    # BBC usedServant: 非助战从者的位置列表 (0-based)
    # BBC assistEquip: 助战礼装名称(字符串) 或 [null,null,null](无礼装信息)
    assist_idx = None
    assist_mode = "从者礼装"
    used_servant = []
    assist_equip = [None, None, None]
    
    for i, svt_info in enumerate(on_field_svts):
        if svt_info is None:
            continue
        support_type = svt_info.get("supportType", "none")
        if support_type in ("friend", "fixed"):
            assist_idx = i
            # 助战礼装
            ce_id = _get_svt_ce_id(svt_info)
            if ce_id:
                ce_name = get_equip_name(ce_id)
                if ce_name:
                    assist_equip = ce_name  # 字符串格式
                    assist_mode = "从者礼装"
        else:
            # 非助战从者
            used_servant.append(i)
    
    # 如果没有助战，默认 assistIdx=2 (BBC 默认行为)
    if assist_idx is None:
        assist_idx = 2
        used_servant = [0, 1]
    
    # ---- 构建基础模板 ----
    result = {
        "_source": "chaldea",
        "_questId": (share_data.get("quest") or {}).get("id"),
        "_appBuild": share_data.get("appBuild"),
    }
    
    # ---- 从者名称映射 ----
    for i in range(6):
        svt_info = all_svts[i] if i < len(all_svts) else None
        if svt_info is not None:
            svt_id = svt_info.get("svtId")
            result[f"servant_{i}_name"] = get_servant_name(svt_id) if svt_id else None
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
    
    # ---- 结合回合战斗逻辑操作序列 ----
    bbc_actions = convert_actions_to_bbc_rounds(actions, delegate, mystic_code_id)
    result.update(bbc_actions)
    
    return result


def _get_svt_ce_id(svt_info: dict) -> Optional[int]:
    """从从者信息中提取概念礼装 ID"""
    # 优先使用 equip1.id (Chaldea 新版格式)
    equip1 = svt_info.get("equip1")
    if equip1 and equip1.get("id"):
        return equip1["id"]
    # 兼容旧格式 ceId
    ce_id = svt_info.get("ceId")
    if ce_id:
        return ce_id
    return None

def parse_import_source(source: str):
    """
    智能解析用户的输入。
    返回 tuple: (quest_id, team_id, direct_data)
      - direct_data 如果有值，直接走本地免网络解析。
    """
    source = source.strip()
    
    # 纯数字判断
    if source.isdigit():
        num = int(source)
        if len(source) <= 6:
            return None, num, None # team_id
        else:
            return num, None, None # quest_id

    # 包含长串压缩数据 data=GH4...
    match_data = re.search(r'data=([A-Za-z0-9\-\_]+)', source)
    if match_data:
        return None, None, match_data.group(1)
        
    # 包含短链接 ID id=...
    match_id = re.search(r'id=(\d+)', source)
    if match_id:
        return None, int(match_id.group(1)), None

    return None, None, None

def fetch_and_convert(source: str, output_dir: Optional[str] = None) -> Optional[str]:
    """主入口编排：通过 source 获取数据并生成 BBC 字典配置"""
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
            
    if not share_data:
        logger.error("[Chaldea] 数据结构提取失败。")
        return None

    bbc_config = chaldea_to_bbc(share_data)
    
    filename = f"chaldea_{quest_id}_{team_id}.json"
    filepath = os.path.join(output_dir or ".", filename)

    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(bbc_config, f, ensure_ascii=False, indent=4)
        
    logger.info(f"[Chaldea] 已保存队伍 JSON 到 {filepath}")
    return filename

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=str, required=True)
    parser.add_argument("--outd", type=str, default=".")
    args = parser.parse_args()
    fetch_and_convert(args.source, args.outd)
