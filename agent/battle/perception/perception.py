"""感知：一帧截图 -> BattleState。

集成层，可依赖 MFW（通过传入的 context），但不直接 import maa 类型——
只用 context.run_recognition(node_name, img) 和其返回的 RecognitionDetail。

各识别结果字段（已按 MaaFw 5.12.2 核对）：
  reco.hit: bool
  reco.best_result.count   ColorMatch / FeatureMatch
  reco.best_result.score   TemplateMatch / OCR
  reco.best_result.text    OCR
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from ..core.enums import CardColor, Scene
from ..core.models import (BattleState, CommandCard, Confidence, EnemyState,
                           NpCard, ServantState, SkillState)
from . import config


def build(context, img, screenshot_id: str = "") -> BattleState:
    scene, sconf = _detect_scene(context, img)

    # 只有选卡界面才需要解析卡牌；主界面/动画等无卡可读
    cards: Tuple[CommandCard, ...] = ()
    np_cards: Tuple[NpCard, ...] = ()
    servants: Tuple[ServantState, ...] = ()
    master_skills: Tuple[SkillState, ...] = ()
    unknown: List[str] = []
    if scene is Scene.COMMAND_SELECTION:
        cards = tuple(_detect_card(context, img, i) for i in range(1, 6))
        np_cards = tuple(c for c in (_detect_np(context, img, s) for s in config.FRONTLINE_SLOTS) if c)
        for c in cards:
            if not c.confidence.passes(config.MIN_CARD_CONFIDENCE):
                unknown.append(f"card[{c.ui_slot}]")
            if c.owner_slot is None:
                unknown.append(f"card[{c.ui_slot}].owner_slot")
    elif scene is Scene.MAIN_BATTLE:
        servants = _detect_servants(context, img)
        for servant in servants:
            for index, skill in enumerate(servant.skills, start=1):
                if skill.available is None:
                    unknown.append(
                        f"servant[{servant.slot}].skill[{index}].available"
                    )
        master_skills = _detect_master_skills(context, img)
        for index, skill in enumerate(master_skills, start=1):
            if skill.available is None:
                unknown.append(f"master_skill[{index}].available")
    elif scene is Scene.ORDER_CHANGE:
        # 换人界面：检测从者技能状态无意义，只识别场景即可
        pass

    enemies = _detect_enemies(context, img)

    return BattleState(
        scene=scene,
        scene_confidence=Confidence(sconf, "scene"),
        cards=cards,
        np_cards=np_cards,
        enemies=enemies,
        servants=servants,
        master_skills=master_skills,
        screenshot_id=screenshot_id,
        unknown_fields=tuple(unknown),
    )


def _reco(context, node: str, img):
    """跑一个识别节点，返回 RecognitionDetail 或 None。"""
    return context.run_recognition(node, img)


def reached_post_battle(context, img) -> bool:
    """结算点击流是否已走完（回到关卡列表/主界面）。依赖 SETTLEMENT_DONE_NODE（待标定）。"""
    r = _reco(context, config.SETTLEMENT_DONE_NODE, img)
    return bool(r and r.hit)


def detect_scene(context, img) -> Scene:
    """轻量版：只检测场景，不做卡牌/技能/敌人等完整感知。用于轮询等待。"""
    scene, _ = _detect_scene(context, img)
    return scene


def _detect_scene(context, img) -> Tuple[Scene, float]:
    # 命中哪个场景节点就是哪个；都不命中 -> UNKNOWN
    for scene_key, node in config.SCENE_NODES.items():
        r = _reco(context, node, img)
        if r and r.hit:
            score = getattr(getattr(r, "best_result", None), "score", 1.0) or 1.0
            return Scene(scene_key), float(score)
    return Scene.UNKNOWN, 0.0


# OCR 文本 -> CardColor 映射
_CARD_TEXT_MAP = {
    "力击": CardColor.BUSTER,
    "技击": CardColor.ARTS,
    "迅击": CardColor.QUICK,
}


def _detect_card(context, img, ui_slot: int) -> CommandCard:
    # OCR 识别卡牌文字（力击/迅击/技击）
    node = config.CARD_NODE.format(ui_slot=ui_slot)
    r = _reco(context, node, img)
    if not r or not r.hit or not r.best_result:
        return CommandCard(ui_slot, CardColor.BUSTER, None, Confidence(0.0, "ocr"))
    text = getattr(r.best_result, "text", "") or ""
    score = getattr(r.best_result, "score", 1.0) or 1.0
    color = _CARD_TEXT_MAP.get(text.strip())
    if color is None:
        return CommandCard(ui_slot, CardColor.BUSTER, None, Confidence(0.0, "ocr"))
    return CommandCard(ui_slot, color, owner_slot=None, confidence=Confidence(float(score), "ocr"))


def _detect_np(context, img, servant_slot: int) -> Optional[NpCard]:
    node = config.NP_CARD_NODE.format(servant_slot=servant_slot)
    r = _reco(context, node, img)
    if not (r and r.hit):
        return None

    best = getattr(r, "best_result", None)
    text = getattr(best, "text", "") or ""
    score = float(getattr(best, "score", 0.0) or 0.0)
    percent = _parse_np_percent(text)

    # 只有高置信度、明确读到 100%~300% 才认为宝具卡可用。
    # 低于 100% 或超过游戏上限 300% 的结果都不应进入 np_cards；
    # 低置信度命中也必须 fail-closed。
    if percent is None or not (100 <= percent <= 300) or score < config.MIN_NP_CONFIDENCE:
        return None

    return NpCard(
        servant_slot,
        Confidence(score, "ocr"),
        percent=percent,
    )


def _parse_np_percent(text: str) -> Optional[int]:
    """把 OCR 文本解析成 0..300 的 NP 百分比。

    OCR 可能把 ``100%`` 读成 ``1.0.0%``，因此允许数字之间出现分隔符，
    但最终必须得到一个合法的 0..300 数值；异常值一律丢弃。
    """
    import re

    normalized = (text or "").replace("％", "%")
    match = re.search(r"([0-9][0-9.\s]{0,8}[0-9]|[0-9])\s*%?", normalized)
    if not match:
        return None

    digits = re.sub(r"\D", "", match.group(1))
    if not digits:
        return None

    value = int(digits)

    # 如果值超过游戏上限 300，可能是 OCR 把 % 误读为数字（如 "100%" → "1009"），
    # 尝试去掉最后一位再判断。
    if value > 300 and len(digits) > 1:
        trimmed = int(digits[:-1])
        if 0 <= trimmed <= 300:
            return trimmed

    return value if 0 <= value <= 300 else None


def _detect_enemies(context, img) -> Tuple[EnemyState, ...]:
    out: List[EnemyState] = []
    for slot in config.ENEMY_SLOTS:
        alive_r = _reco(context, config.ENEMY_NODE.format(slot=slot), img)
        alive = bool(alive_r and alive_r.hit)
        if not alive:
            continue
        target_r = _reco(context, config.ENEMY_TARGET_NODE.format(slot=slot), img)
        targeted = bool(target_r and target_r.hit)
        score = getattr(getattr(alive_r, "best_result", None), "score", 1.0) or 1.0
        out.append(EnemyState(slot, True, targeted, Confidence(float(score), "ocr")))
    return tuple(out)


def _count(reco) -> int:
    if not (reco and reco.hit):
        return 0
    best = getattr(reco, "best_result", None)
    return int(getattr(best, "count", 0) or 0)


def _detect_servants(context, img) -> Tuple[ServantState, ...]:
    """检测前排从者的技能可用性。

    识别逻辑：TemplateMatch 在技能按钮 ROI 内滑动窗口匹配 digits 目录的
    0-9 数字模板（任一命中），命中数字（剩余回合数）则技能在冷却、不可用；
    否则（没有 CD 数字）技能可用。FGO 里可用技能的按钮上没有 CD 数字，
    所以"未命中 CD"就是可用的直接信号，不应再标记为未知而被
    safety gate 跳过。
    """
    out: List[ServantState] = []
    for slot in config.FRONTLINE_SLOTS:
        skills: List[SkillState] = []
        for idx in range(1, 4):
            cd_node = config.SERVANT_SKILL_CD_NODE.format(servant_slot=slot, skill_index=idx)
            cd_result = _reco(context, cd_node, img)
            if cd_result and cd_result.hit:
                # 命中 CD 数字模板 → 技能在冷却，不可用。
                score = getattr(getattr(cd_result, "best_result", None), "score", 1.0) or 1.0
                skills.append(SkillState(False, Confidence(float(score), "template:cd")))
                continue
            # 未命中任何 CD 数字 → 技能可用。
            skills.append(SkillState(True, Confidence(1.0, "template:no_cd")))
        out.append(ServantState(slot, tuple(skills), Confidence(1.0, "composite")))
    return tuple(out)


def _detect_master_skills(context, img) -> Tuple[SkillState, ...]:
    """检测御主技能 1..3 的可用性。

    识别逻辑与从者技能一致：TemplateMatch 命中 CD 数字模板 → 不可用；
    否则可用。御主技能菜单默认收起，CD 数字在展开前不可见；感知层
    仅在 MAIN_BATTLE 场景下检测，此时菜单可能收起，CD 节点 ROI 区域
    无数字 → 不命中 → 判定为可用。这是保守行为：若技能实际在冷却但
    菜单收起，会误判为可用，由执行层/安全门兜底。
    """
    out: List[SkillState] = []
    for idx in range(1, 4):
        cd_node = config.MASTER_SKILL_CD_NODE.format(skill_index=idx)
        cd_result = _reco(context, cd_node, img)
        if cd_result and cd_result.hit:
            # 命中 CD 数字模板 → 技能在冷却，不可用。
            score = getattr(getattr(cd_result, "best_result", None), "score", 1.0) or 1.0
            out.append(SkillState(False, Confidence(float(score), "template:cd")))
            continue
        # 未命中任何 CD 数字 → 技能可用。
        out.append(SkillState(True, Confidence(1.0, "template:no_cd")))
    return tuple(out)
