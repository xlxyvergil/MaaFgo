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

from dataclasses import replace
from typing import List, Optional, Sequence, Tuple

from ..core.enums import CardColor, Scene
from ..core.models import (BattleState, CommandCard, Confidence, EnemyState,
                           NpCard, ServantState, SkillState)
from . import config
from .card_owner import CommandCardOwnerRecognizer
from .frontline import FrontlineServantRecognizer


def build(
    context,
    img,
    screenshot_id: str = "",
    *,
    frontline_servant_ids: Sequence[str | int | None] | None = None,
    owner_recognizer: CommandCardOwnerRecognizer | None = None,
    candidate_servant_ids: Sequence[str | int | None] | None = None,
    servant_recognizer: FrontlineServantRecognizer | None = None,
) -> BattleState:
    scene, sconf = _detect_scene(context, img)

    # 只有选卡界面才需要解析卡牌；主界面/动画等无卡可读
    cards: Tuple[CommandCard, ...] = ()
    np_cards: Tuple[NpCard, ...] = ()
    servants: Tuple[ServantState, ...] = ()
    master_skills: Tuple[SkillState, ...] = ()
    unknown: List[str] = []
    if scene is Scene.COMMAND_SELECTION:
        card_states = []
        for i in range(1, 6):
            blocked = _reco(context, config.FACE_BLOCKED_NODE.format(ui_slot=i), img)
            is_blocked = bool(blocked and blocked.hit)
            card_states.append(replace(
                _detect_card(context, img, i), can_attack=not is_blocked,
                blocked_reason="sealed_or_cannot_act" if is_blocked else "",
            ))
        cards = tuple(card_states)
        np_owners = {}
        if frontline_servant_ids is not None:
            recognizer = owner_recognizer or CommandCardOwnerRecognizer.default()
            owners = {item.ui_slot: item for item in recognizer.detect(img, frontline_servant_ids)}
            np_owners = {item.ui_slot: item for item in recognizer.detect_np(img, frontline_servant_ids)}
            cards = tuple(
                replace(
                    card,
                    owner_slot=owners[card.ui_slot].owner_slot,
                    owner_confidence=owners[card.ui_slot].confidence,
                    owner_servant_id=owners[card.ui_slot].servant_id,
                    owner_margin=owners[card.ui_slot].margin,
                    owner_template=owners[card.ui_slot].matched_template,
                    owner_reason=owners[card.ui_slot].reason,
                )
                for card in cards
            )
        np_cards = tuple(c for c in (_detect_np(context, img, s, np_owners.get(s))
                                    for s in config.FRONTLINE_SLOTS) if c)
        for c in cards:
            if not c.confidence.passes(config.MIN_CARD_CONFIDENCE):
                unknown.append(f"card[{c.ui_slot}]")
            if c.owner_slot is None:
                unknown.append(f"card[{c.ui_slot}].owner_slot")
    elif scene is Scene.MAIN_BATTLE:
        servants = _detect_servants(
            context, img, candidate_servant_ids or frontline_servant_ids or (),
            recognizer=servant_recognizer,
        )
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


def _detect_np(context, img, servant_slot: int, owner=None) -> Optional[NpCard]:
    visual = _reco(context, config.NP_VISUAL_NODE.format(servant_slot=servant_slot), img)
    title = getattr(getattr(visual, "best_result", None), "text", "") or ""
    title = title.strip()
    title_present = bool(visual and visual.hit and len(title) >= 2
                         and float(getattr(visual.best_result, "score", 0.0) or 0.0) >= config.MIN_NP_CONFIDENCE
                         and title.casefold() not in {"目标", "目標", "target"})
    owner_matches = bool(owner and owner.owner_slot == servant_slot
                         and owner.best_score >= config.MIN_NP_CONFIDENCE)
    blocked = _reco(context, config.NP_BLOCKED_NODE.format(servant_slot=servant_slot), img)
    is_blocked = bool(blocked and blocked.hit)
    if not (owner_matches or is_blocked):
        # Scenery text is not a card. When identity evidence is unavailable,
        # require the shared NP frame as well as its visible title.
        frame = _reco(context, config.NP_FRAME_NODE.format(servant_slot=servant_slot), img) if title_present else None
        if not (frame and frame.hit):
            return None

    # Availability comes only from the visible card and its restriction overlay.
    score = max(
        float(getattr(getattr(visual, "best_result", None), "score", 0.0) or 0.0) if title_present else 0.0,
        owner.best_score if owner_matches else 0.0,
        1.0 if is_blocked else 0.0,
    )
    return NpCard(
        servant_slot,
        Confidence(score, "np_card_visual"),
        available=not is_blocked,
        blocked_reason="sealed_or_cannot_act" if is_blocked else "",
        owner_servant_id=owner.servant_id if owner and owner.owner_slot == servant_slot else None,
        owner_confidence=owner.confidence if owner and owner.owner_slot == servant_slot else Confidence(0.0),
        owner_template=owner.matched_template if owner and owner.owner_slot == servant_slot else "",
    )


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


def _detect_servants(context, img, candidate_servant_ids=(), *, recognizer=None) -> Tuple[ServantState, ...]:
    """先识别各位置的战斗头像，再检测已确认从者的技能 CD。

    头像未匹配时按空位处理，不识别或使用该位置的技能。
    存在从者时，技能按钮命中 CD 数字模板则不可用，否则可用。
    搜索范围为已确认编队（含候补），身份与位置取自当前截图。
    """
    out: List[ServantState] = []
    if not any(candidate_servant_ids):
        return ()
    recognizer = recognizer or FrontlineServantRecognizer.from_context(context)
    for identity in recognizer.detect(img, candidate_servant_ids):
        if identity.servant_id is None:
            continue
        slot = identity.slot
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
        out.append(ServantState(slot, tuple(skills), identity.confidence, identity.servant_id))
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
