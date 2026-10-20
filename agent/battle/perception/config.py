"""感知配置：识别节点名与阈值。

约定：真正的 ROI/阈值写在 MFW resource 的识别节点里（1280x720 下标定），
本文件只保存"节点名模板"和门控阈值。

识别节点定义在 assets/resource/base/pipeline/自动战斗_感知.json：
  - 战斗_选卡场景        TemplateMatch（选卡界面特征）
  - 战斗_主界面          TemplateMatch（主界面攻击钮）
  - 战斗_胜利 / 战斗_失败   OCR / TemplateMatch
  - 战斗_卡{1..5}        OCR（识别「力击/迅击/技击」）
  - 战斗_宝具卡{1..3}_卡面 / _封印  实际卡面与限制状态
  - 战斗_敌人{1..3} / 战斗_敌人{1..3}_选中
战斗 HUD 头像参数定义在 自动战斗_前排身份.json 的 attach 中。
"""
from __future__ import annotations

# 场景检测节点（按序尝试，先命中者定场景）
SCENE_NODES = {
    "command_selection": "战斗_选卡场景",   # TemplateMatch：选卡界面特征（如卡区/返回钮）
    "order_change": "战斗_换人界面",          # OCR："请从首发成员和候补成员中"
    "main_battle": "战斗_主界面",            # TemplateMatch：主界面攻击钮
    "victory": "战斗_胜利",
    "defeat": "战斗_失败",
}

# 技能特殊覆盖层（弹窗/目标选择/专属技能流程）的识别已下沉到
# assets/resource/base/pipeline/自动战斗_特殊技能.json，由 runtime 按需 run_task 驱动，
# 感知层不再维护对应的 Python 侧注册表。

# 参数化节点名模板
CARD_NODE = "战斗_卡{ui_slot}"               # OCR，识别 "力击"/"迅击"/"技击"
NP_VISUAL_NODE = "战斗_宝具卡{servant_slot}_卡面" # 上排宝具名称，仅作辅助证据
NP_FRAME_NODE = "战斗_宝具卡{servant_slot}_边框" # 未知编队时的通用卡片证据
NP_BLOCKED_NODE = "战斗_宝具卡{servant_slot}_封印"
FACE_BLOCKED_NODE = "战斗_卡{ui_slot}_封印"
ENEMY_NODE = "战斗_敌人{slot}"                     # 存活/位置
ENEMY_TARGET_NODE = "战斗_敌人{slot}_选中"         # 当前目标

# 从者技能 CD/可用识别节点名模板 (servant_slot 1..3, skill_index 1..3)
SERVANT_SKILL_NODE = "战斗_从者{servant_slot}技能{skill_index}"
SERVANT_SKILL_CD_NODE = "战斗_从者{servant_slot}技能{skill_index}_CD"

# 御主技能可用识别节点名模板 (skill_index 1..3)
MASTER_SKILL_NODE = "战斗_御主技能{skill_index}"
MASTER_SKILL_CD_NODE = "战斗_御主技能{skill_index}_CD"

# 技能处于 CD 时点击后弹出的提示窗。

# 门控阈值
MIN_SCENE_CONFIDENCE = 0.95
MIN_CARD_CONFIDENCE = 0.90
MIN_NP_CONFIDENCE = 0.80

# V1 前排/敌方槽位数（先按最多算，实际存活由识别决定）
FRONTLINE_SLOTS = (1, 2, 3)
ENEMY_SLOTS = (1, 2, 3)
