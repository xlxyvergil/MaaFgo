"""搓丸子的配置、额度与材料规则；不包含截图、识别或设备操作。"""
from dataclasses import dataclass
import re
import unicodedata


class ExpballConfigError(ValueError):
    pass


def integer(value, name, low, high):
    if isinstance(value, bool):
        raise ExpballConfigError(f"{name}必须为整数")
    try:
        result = int(str(value).strip())
    except (ValueError, TypeError):
        raise ExpballConfigError(f"{name}必须为整数") from None
    if not low <= result <= high:
        raise ExpballConfigError(f"{name}须为{low}至{high}")
    return result


def boolean(value, name):
    if value is True or value == "true":
        return True
    if value is False or value == "false":
        return False
    raise ExpballConfigError(f"{name}必须为开关值")


def normalize_name(value):
    return re.sub(r"[\s·・•\[\]【】〔〕()（）]", "", unicodedata.normalize("NFKC", str(value)))


def read_number(text):
    """数字 OCR 必须完整可解析；缺失/乱码不能当作零。"""
    value = unicodedata.normalize("NFKC", str(text)).strip()
    if not re.fullmatch(r"\d{1,3}(?:[,， ]\d{3})+|\d+", value):
        return None
    return int(re.sub(r"[,， ]", "", value))


def read_level(text):
    value = unicodedata.normalize("NFKC", str(text))
    match = re.search(r"(?:Lv\.?\s*)?(\d{1,3})\s*/\s*(\d{1,3})", value, re.I)
    if not match:
        return None
    current, maximum = map(int, match.groups())
    return (current, maximum) if 1 <= current <= maximum <= 120 else None


@dataclass
class TargetQuota:
    kind: str
    identity: str
    count: int | None
    confirmed: int = 0

    @property
    def remaining(self):
        return None if self.count is None else max(0, self.count - self.confirmed)

    @property
    def needed(self):
        return self.remaining is None or self.remaining > 0

    def record(self):
        if not self.needed:
            return False
        self.confirmed += 1
        return True


def parse_targets(attach):
    merged = {}
    for index in range(1, 6):
        prefix = f"target_{index}_"
        kind = str(attach.get(prefix + "kind", "none"))
        if kind == "none":
            continue
        if kind not in ("servant", "equip"):
            raise ExpballConfigError("保留对象类型无效")
        identity = str(attach.get(prefix + "id", "")).strip()
        if not identity.isdigit() or int(identity) <= 0:
            raise ExpballConfigError(f"保留目标{index}未选择有效对象")
        mode = attach.get(prefix + "mode", "count")
        if mode not in ("count", "all"):
            raise ExpballConfigError("保留方式无效")
        count = None if mode == "all" else integer(attach.get(prefix + "count", 1), "保留份数", 1, 9999)
        key = kind, identity
        if key not in merged:
            merged[key] = TargetQuota(kind, identity, count)
        else:
            old = merged[key]
            old.count = None if old.count is None or count is None else old.count + count
    return list(merged.values())


@dataclass(frozen=True)
class ExpballConfig:
    mode: str
    make_ball: bool
    ball_star: int
    eat3: bool
    eat4: bool
    draw_fp: bool
    draw_batch: int
    direction: str
    max_draw_batches: int
    friend_points_reserve: int
    max_cycles: int
    stop_targets: bool
    store4: bool
    store5: bool
    store_balls: bool
    sell_servants: bool
    sell4: bool
    sell5: bool
    sell_wz: bool
    aqf: bool
    levelup: bool
    level_servant_id: str
    target_level: int

    @classmethod
    def parse(cls, attach):
        defaults = dict(mode="normal", make_ball=True, ball_star=1, eat3=False, eat4=False,
                        draw_fp=False, draw_batch=10, direction="left", max_draw_batches=50,
                        friend_points_reserve=0, max_cycles=200, stop_targets=False,
                        store4=False, store5=False, store_balls=False, sell_servants=False,
                        sell4=False, sell5=False, sell_wz=False, aqf=False, levelup=False,
                        level_servant_id="", target_level=0)
        values = {key: attach.get(key, value) for key, value in defaults.items()}
        for key, default in defaults.items():
            if isinstance(default, bool):
                values[key] = boolean(values[key], key)
        for key, low, high in (("ball_star", 1, 2), ("draw_batch", 10, 100),
                               ("max_draw_batches", 1, 9999), ("friend_points_reserve", 0, 999999999),
                               ("max_cycles", 1, 9999), ("target_level", 0, 120)):
            values[key] = integer(values[key], key, low, high)
        if values["draw_batch"] not in (10, 100):
            raise ExpballConfigError("每批抽取数量只能为10或100")
        if values["mode"] not in ("normal", "test_servant") or values["direction"] not in ("left", "right"):
            raise ExpballConfigError("运行方式或寻找方向无效")
        values["level_servant_id"] = str(values["level_servant_id"]).strip()
        targets = parse_targets(attach)
        if values["stop_targets"] and (not targets or any(t.count is None for t in targets)):
            raise ExpballConfigError("全部满足后停止须配置有限份数目标，不能包含全部保留")
        if targets and values["draw_fp"] and values["draw_batch"] == 100:
            raise ExpballConfigError("100抽的保留目标遍历尚未验收，请改用10抽")
        if targets and not values["draw_fp"]:
            raise ExpballConfigError("保留目标须开启自动抽友情")
        if values["store_balls"] and not values["make_ball"]:
            raise ExpballConfigError("成品入库仅处理本轮制作的丸子，请同时启用自动搓丸子")
        if values["levelup"] or values["mode"] == "test_servant":
            if not values["level_servant_id"].isdigit() or not 1 <= values["target_level"] <= 120:
                raise ExpballConfigError("请选择升级从者并设置1至120的目标等级")
        if not any(values[k] for k in ("make_ball", "draw_fp", "store4", "store5", "store_balls",
                                      "sell_servants", "sell4", "sell5", "sell_wz", "levelup")) and values["mode"] == "normal":
            raise ExpballConfigError("未启用任何操作")
        return cls(**values)


@dataclass(frozen=True)
class CardObservation:
    identity: str
    rarity: int
    current: int
    maximum: int
    locked: bool
    marked: bool

    @property
    def complete(self):
        return self.rarity in (1, 2) and self.current == self.maximum == {1: 50, 2: 55}[self.rarity]


def material_allowed(card, base, config, protected_ids=()):
    allowed = {1, 2} | ({3} if config.eat3 else set()) | ({4} if config.eat4 else set())
    if card.rarity not in allowed or card.locked or card.marked or card.identity in protected_ids:
        return False
    # 已有经验的个体不能作为普通素材被批量吞掉；半成品只作为底卡继续。
    if card.current != 1 or card.maximum < card.current:
        return False
    # 尚未达到最终等级上限时先用同名卡突破，避免将经验浪费在低上限。
    goal = {1: 50, 2: 55}[base.rarity]
    return base.maximum >= goal or card.identity == base.identity
