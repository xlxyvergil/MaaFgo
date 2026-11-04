"""Chaldea replay choices and the supported in-game selection menus (no Maa dependency)."""
from dataclasses import dataclass


class SpecialSkillChoiceError(ValueError):
    """A configured replay selection cannot be executed faithfully."""


@dataclass(frozen=True)
class SpecialSkillChoices:
    td_type_changes: tuple[int, ...] = ()
    skill_act_selections: tuple[int, ...] = ()

    @property
    def configured(self) -> bool:
        return bool(self.td_type_changes or self.skill_act_selections)


def parse_special_skill_choices(delegate) -> SpecialSkillChoices:
    if not isinstance(delegate, dict):
        return SpecialSkillChoices()
    result = []
    for key in ("tdTypeChanges", "skillActSelectSelections"):
        values = delegate.get(key)
        if values is None:
            values = []
        if not isinstance(values, list):
            raise SpecialSkillChoiceError(f"delegate.{key} 必须是数组")
        parsed = []
        for index, value in enumerate(values):
            if key == "tdTypeChanges" and isinstance(value, str):
                value = {"arts": 1, "buster": 2, "quick": 3}.get(value)
            allowed = (1, 2, 3) if key == "tdTypeChanges" else (0, 1, 2)
            if type(value) is not int or value not in allowed:
                raise SpecialSkillChoiceError(
                    f"delegate.{key}[{index}]={values[index]!r} 无法对应游戏选项；"
                    f"支持 {allowed}，不支持模拟器的空选择"
                )
            parsed.append(value)
        result.append(tuple(parsed))
    return SpecialSkillChoices(*result)


@dataclass(frozen=True)
class SpecialSkillGroup:
    root: str
    servant_ids: tuple[str, ...]
    skills: tuple[int, ...]
    field: str
    options: tuple[str, ...]
    default: int
    # tdTypeChanges stores CardType values 1/2/3, not button positions Q/A/B.
    first_value: int = 0


SELECT = "skillActSelectSelections"
TD = "tdTypeChanges"


def _select(root, ids, skills, options, default=0):
    return SpecialSkillGroup(root, ids, skills, SELECT, tuple(options), default)


SPECIAL_SKILL_GROUPS = (
    SpecialSkillGroup("战斗_仇凛2技能_色卡选择", ("1100900",), (2,), TD,
                      ("战斗_仇凛2技能_选A", "战斗_仇凛2技能_选B", "战斗_仇凛2技能_选Q"), 1, 1),
    _select("战斗_库库尔坎_暴击星消耗", ("2501100",), (1, 2, 3),
            ("战斗_库库尔坎_不消耗", "战斗_库库尔坎_消耗10_可点击"), 1),
    _select("战斗_马嘶1技能_无敌选择", ("203300",), (1,),
            ("战斗_马嘶1技能_无敌选择", "战斗_马嘶1技能_毅力选择")),
    _select("战斗_梵高矿工1技能_Arts选择", ("305600",), (1,),
            ("战斗_梵高矿工1技能_Quick选择", "战斗_梵高矿工1技能_Arts选择", "战斗_梵高矿工1技能_Buster选择"), 1),
    _select("战斗_莉莉丝3技能_不赋予选择", ("405900",), (3,),
            ("战斗_莉莉丝3技能_不赋予选择", "战斗_莉莉丝3技能_赋予选择")),
    _select("战斗_普雷拉蒂1技能_不换形态选择", ("505600", "505700"), (1,),
            ("战斗_普雷拉蒂1技能_不换形态选择", "战斗_普雷拉蒂1技能_换形态选择")),
    _select("战斗_夏洛特3技能_Arts选择", ("603800",), (3,),
            ("战斗_夏洛特3技能_Arts选择", "战斗_夏洛特3技能_暴击威力选择", "战斗_夏洛特3技能_宝具威力选择")),
    _select("战斗_芙萝拉3技能_通常状态选择", ("1002000", "1002100"), (3,),
            ("战斗_芙萝拉3技能_通常状态选择", "战斗_芙萝拉3技能_冥界之花园状态选择")),
    _select("战斗_安哥拉曼纽3技能_50%毅力选择", ("1100100",), (3,),
            ("战斗_安哥拉曼纽3技能_无毅力选择", "战斗_安哥拉曼纽3技能_50%毅力选择", "战斗_安哥拉曼纽3技能_1HP毅力选择"), 1),
    _select("战斗_但丁2技能_天国Arts选择", ("2801000",), (2,),
            ("战斗_但丁2技能_天国Arts选择", "战斗_但丁2技能_地狱Buster选择")),
    _select("战斗_玉兔2技能_宝具类型选择", ("2301100",), (2,),
            ("战斗_玉兔2技能_宝具类型选择", "战斗_玉兔2技能_单体宝具选择")),
    _select("战斗_岸波白野3技能_天之力选择", ("2300700", "2300800"), (3,),
            ("战斗_岸波白野3技能_天之力选择", "战斗_岸波白野3技能_地之力选择", "战斗_岸波白野3技能_人之力选择")),
    _select("战斗_静希草十郎3技能_红卡选择", ("704900",), (3,),
            ("战斗_静希草十郎3技能_绿卡选择", "战斗_静希草十郎3技能_蓝卡选择", "战斗_静希草十郎3技能_红卡选择"), 2),
    _select("战斗_巴格斯特3技能_全体宝具选择", ("204900",), (3,),
            ("战斗_巴格斯特3技能_全体宝具选择", "战斗_巴格斯特3技能_单体宝具选择")),
)
