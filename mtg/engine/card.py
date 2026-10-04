"""卡牌与永久物模型。

三层结构::

    CardData     静态定义（来自卡池，全局共享、只读）
      └─ Card    一张具体的牌张实例（有拥有者、所在区域）
           └─ Permanent  当牌张进入战场时的战场对象状态

``Permanent`` 的攻防、颜色、类别、异能都通过层系统实时计算，
因此引擎里不缓存"当前攻防"这类派生值。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .mana import ManaSymbol, mana_cost_to_string, parse_mana_cost
from .types import (
    BASIC_LAND_MANA,
    CARD_TYPES,
    Color,
    Keyword,
    SUPERTYPES,
    Zone,
    normalize_keyword,
)

if TYPE_CHECKING:
    from .game import Game
    from .player import Player


# -------------------------------------------------------------------- 静态数据

@dataclass
class CardData:
    """一张牌的静态定义（名字、费用、类别、规则文本、已解析异能）。"""

    card_id: str
    name: str
    mana_cost: str = ""
    cmc: float = 0.0
    type_line: str = ""
    oracle_text: str = ""
    power: str | None = None
    toughness: str | None = None
    loyalty: str | None = None
    defense: str | None = None
    colors: list[str] = field(default_factory=list)
    color_identity: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    produced_mana: list[str] = field(default_factory=list)
    set_code: str = ""
    set_name: str = ""
    rarity: str = ""
    layout: str = "normal"
    image_small: str | None = None
    image_normal: str | None = None
    faces: list["CardData"] = field(default_factory=list)

    #: 由 oracle 解析器填充的执行期异能
    abilities: list[Any] = field(default_factory=list)
    #: 未能解析的规则文本片段（用于 UI 提示）
    unparsed: list[str] = field(default_factory=list)
    #: 整张牌是否被引擎完整支持
    supported: bool = True
    #: 进场时是否已横置
    enters_tapped: bool = False
    #: 灵气的结附目标（"Enchant creature" 等）
    enchant_target: str = ""

    # ---------------------------------------------------------------- 类别
    @property
    def supertypes(self) -> list[str]:
        return [w for w in self._type_words() if w in SUPERTYPES]

    @property
    def card_types(self) -> list[str]:
        return [w for w in self._type_words() if w in CARD_TYPES]

    @property
    def subtypes(self) -> list[str]:
        words = self._type_words()
        return [w for w in words if w not in SUPERTYPES and w not in CARD_TYPES]

    def _type_words(self) -> list[str]:
        """拆出类别行的全部词：传奇/基本等超类别、牌张类别、以及破折号后的副类别。"""
        if not self.type_line:
            return []
        # 只按破折号切分（em dash / en dash），避免误伤连字符
        parts = re.split(r"\s*[—–]\s*", self.type_line)
        words: list[str] = []
        for part in parts:
            words.extend(w.strip() for w in part.split() if w.strip())
        # 副类别里可能带连字符（如 "Forest Island" 不会，但 "Tolkien-Elf" 会），保留原形
        return words

    def is_type(self, *names: str) -> bool:
        types = set(self.card_types)
        return any(n in types for n in names)

    @property
    def is_creature(self) -> bool:
        return "Creature" in self.card_types

    @property
    def is_land(self) -> bool:
        return "Land" in self.card_types

    @property
    def is_instant(self) -> bool:
        return "Instant" in self.card_types

    @property
    def is_sorcery(self) -> bool:
        return "Sorcery" in self.card_types

    @property
    def is_enchantment(self) -> bool:
        return "Enchantment" in self.card_types

    @property
    def is_artifact(self) -> bool:
        return "Artifact" in self.card_types

    @property
    def is_planeswalker(self) -> bool:
        return "Planeswalker" in self.card_types

    @property
    def is_battle(self) -> bool:
        return "Battle" in self.card_types

    @property
    def is_permanent_type(self) -> bool:
        return bool(
            {"Creature", "Artifact", "Enchantment", "Land", "Planeswalker", "Battle", "Kindred", "Tribal"}
            & set(self.card_types)
        )

    @property
    def is_legendary(self) -> bool:
        return "Legendary" in self.supertypes

    @property
    def is_basic_land(self) -> bool:
        return self.is_land and "Basic" in self.supertypes

    @property
    def basic_land_colors(self) -> list[str]:
        out = []
        for sub in self.subtypes:
            if sub in BASIC_LAND_MANA:
                out.append(BASIC_LAND_MANA[sub])
        return out

    # ---------------------------------------------------------------- 法术力
    @property
    def mana_symbols(self) -> list[ManaSymbol]:
        return parse_mana_cost(self.mana_cost)

    @property
    def mana_string(self) -> str:
        return mana_cost_to_string(self.mana_symbols) if self.mana_cost else ""

    # ---------------------------------------------------------------- 攻防
    @property
    def base_power(self) -> int:
        return _as_int(self.power)

    @property
    def base_toughness(self) -> int:
        return _as_int(self.toughness)

    @property
    def base_loyalty(self) -> int:
        return _as_int(self.loyalty)

    # ---------------------------------------------------------------- 颜色
    @property
    def color_set(self) -> Color:
        result = Color.NONE
        for letter in self.colors:
            result |= {
                "W": Color.WHITE,
                "U": Color.BLUE,
                "B": Color.BLACK,
                "R": Color.RED,
                "G": Color.GREEN,
            }.get(letter, Color.NONE)
        return result

    @property
    def is_colorless(self) -> bool:
        return self.color_set == Color.NONE

    # ---------------------------------------------------------------- 关键字
    @property
    def keyword_set(self) -> set[Keyword]:
        out: set[Keyword] = set()
        for raw in self.keywords:
            kw = normalize_keyword(raw)
            if kw:
                out.add(kw)
        return out

    def has_keyword(self, keyword: Keyword) -> bool:
        return keyword in self.keyword_set

    # ---------------------------------------------------------------- 其他
    @property
    def type_line_cn(self) -> str:
        """简单汉化的类别行，便于中文界面显示。"""
        mapping = {
            "Creature": "生物",
            "Instant": "瞬间",
            "Sorcery": "法术",
            "Enchantment": "结界",
            "Artifact": "神器",
            "Land": "地",
            "Planeswalker": "鹏洛客",
            "Battle": "战役",
            "Kindred": "族类",
            "Tribal": "部族",
            "Legendary": "传奇",
            "Basic": "基本",
            "Snow": "雪境",
            "World": "世界",
        }
        head, _, tail = self.type_line.partition("—")
        head_cn = " ".join(mapping.get(w, w) for w in head.split())
        tail_cn = " ".join(mapping.get(w.strip(), w.strip()) for w in tail.split("—")) if tail else ""
        return f"{head_cn} — {tail_cn}" if tail_cn else head_cn

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<CardData {self.name} {self.mana_string}>"


def _as_int(value: str | int | float | None) -> int:
    """把 "*" / "1+*" 之类的攻防文本转成整数（无法转换记 0）。"""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    match = re.search(r"-?\d+", str(value))
    return int(match.group()) if match else 0


# -------------------------------------------------------------------- 牌张实例

@dataclass(eq=False)
class Card:
    """一张物理牌张实例：拥有某个牌手、处在某个区域。

    用身份语义（而非值比较）：每张牌都是唯一对象，需要能作为字典键。
    """

    data: CardData
    owner: "Player"
    zone: Zone = Zone.LIBRARY
    controller: "Player | None" = None

    #: 战场实例（仅当 zone == BATTLEFIELD 时有意义）
    permanent: "Permanent | None" = None

    #: 该牌张进入当前区域的顺序号，用于"最近进入"判断
    timestamp: int = 0

    def __post_init__(self) -> None:
        if self.controller is None:
            self.controller = self.owner

    @property
    def name(self) -> str:
        return self.data.name

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Card {self.name} @ {self.zone.value}>"


# -------------------------------------------------------------------- 永久物

@dataclass(eq=False)
class Permanent:
    """战场上的一个对象。攻防/颜色/类别/异能均实时计算。

    同样采用身份语义，以便直接用作阻挡关系等映射的键。
    """

    card: Card
    game: "Game"

    controller: "Player" = None  # type: ignore[assignment]
    owner: "Player" = None  # type: ignore[assignment]

    tapped: bool = False
    face_down: bool = False
    phased_out: bool = False
    damage_marked: int = 0

    #: 指示物，如 {"+1/+1": 2, "loyalty": 3}
    counters: dict[str, int] = field(default_factory=dict)

    #: 灵气/武具结附目标
    attached_to: "Permanent | None" = None
    #: 结附在本永久物上的对象
    attachments: list["Permanent"] = field(default_factory=list)

    #: 进入战场的回合数（用于召唤失调）
    entered_turn: int = 0
    #: 本回合是否已攻击
    attacked_this_turn: bool = False
    #: 本回合阻挡了几个生物
    blocked_count: int = 0
    #: 本回合受到过伤害（用于某些触发）
    damaged_this_turn: bool = False

    #: 本回合各触发式异能已触发的次数，用于掐断无限循环（如"进场生成衍生物"套娃）
    trigger_counts: dict[str, int] = field(default_factory=dict)

    #: 层系统产生的临时修正（每次重算时清空）
    _pt_modify: int = 0
    _pt_modify_t: int = 0
    _pt_set: tuple[int, int] | None = None
    _extra_keywords: set[Keyword] = field(default_factory=set)
    _lost_keywords: set[Keyword] = field(default_factory=set)
    _lost_abilities: bool = False
    _pt_switch: bool = False
    _type_override: list[str] | None = None
    _color_override: Color | None = None

    timestamp: int = 0
    #: 派生出的指示物/衍生物标记
    is_token: bool = False
    _token_data: CardData | None = None

    def __post_init__(self) -> None:
        if self.controller is None:
            self.controller = self.card.owner
        if self.owner is None:
            self.owner = self.card.owner

    # ------------------------------------------------------------ 基础数据
    @property
    def data(self) -> CardData:
        if self.is_token and self._token_data is not None:
            return self._token_data
        return self.card.data

    @property
    def name(self) -> str:
        return self.data.name

    # ------------------------------------------------------------ 类别
    @property
    def types(self) -> list[str]:
        if self._type_override is not None:
            return self._type_override
        return self.data.card_types

    @property
    def subtypes(self) -> list[str]:
        return self.data.subtypes

    @property
    def supertypes(self) -> list[str]:
        return self.data.supertypes

    def is_type(self, *names: str) -> bool:
        return any(t in self.types for t in names)

    @property
    def is_creature(self) -> bool:
        return "Creature" in self.types

    @property
    def is_land(self) -> bool:
        return "Land" in self.types

    @property
    def is_artifact(self) -> bool:
        return "Artifact" in self.types

    @property
    def is_enchantment(self) -> bool:
        return "Enchantment" in self.types

    @property
    def is_planeswalker(self) -> bool:
        return "Planeswalker" in self.types

    @property
    def is_battle(self) -> bool:
        return "Battle" in self.types

    @property
    def is_legendary(self) -> bool:
        return "Legendary" in self.supertypes

    # ------------------------------------------------------------ 颜色
    @property
    def colors(self) -> Color:
        if self._color_override is not None:
            return self._color_override
        return self.data.color_set

    # ------------------------------------------------------------ 关键字
    @property
    def keywords(self) -> set[Keyword]:
        base = self.data.keyword_set
        return (base | self._extra_keywords) - self._lost_keywords

    def has_keyword(self, keyword: Keyword) -> bool:
        return keyword in self.keywords

    def has_ability(self, name: str) -> bool:
        """检查是否具备某异能（用于"失去所有异能"类效应）。"""
        if self._lost_abilities:
            return False
        return any(getattr(ability, "keyword", None) == name for ability in self.abilities)

    # ------------------------------------------------------------ 攻防
    @property
    def base_power(self) -> int:
        if self.is_creature:
            return self.data.base_power
        return 0

    @property
    def base_toughness(self) -> int:
        if self.is_creature:
            return self.data.base_toughness
        return 0

    def power(self) -> int:
        """经层系统计算后的力量。"""
        if not self.is_creature:
            return 0
        power, toughness = self._computed_pt()
        return power

    def toughness(self) -> int:
        """经层系统计算后的防御力。"""
        if not self.is_creature:
            return 0
        power, toughness = self._computed_pt()
        return toughness

    def _computed_pt(self) -> tuple[int, int]:
        base_p = self.base_power + self.counters.get("+1/+1", 0) - self.counters.get("-1/-1", 0)
        base_t = self.base_toughness + self.counters.get("+1/+1", 0) - self.counters.get("-1/-1", 0)

        if self._pt_set is not None:
            base_p, base_t = self._pt_set

        power = base_p + self._pt_modify
        toughness = base_t + self._pt_modify_t

        if self._pt_switch:
            power, toughness = toughness, power

        return max(power, 0), max(toughness, 0)

    @property
    def loyalty(self) -> int:
        if not self.is_planeswalker:
            return 0
        return self.data.base_loyalty + self.counters.get("loyalty", 0)

    # ------------------------------------------------------------ 状态
    @property
    def is_sick(self) -> bool:
        """召唤失调：本回合进场的生物无法攻击或使用含横置费用的异能。"""
        if not self.is_creature:
            return False
        if self.has_keyword(Keyword.HASTE):
            return False
        return self.entered_turn >= self.game.turn_number

    @property
    def is_tapped(self) -> bool:
        return self.tapped

    def tap(self) -> None:
        if not self.tapped:
            self.tapped = True
            self.game.events.untap_or_tap(self, tapped=True)

    def untap(self) -> None:
        if self.tapped:
            self.tapped = False
            self.game.events.untap_or_tap(self, tapped=False)

    # ------------------------------------------------------------ 伤害
    def mark_damage(self, amount: int, source: "Permanent | None" = None, combat: bool = False) -> int:
        """标记伤害，返回实际造成的伤害量。"""
        if amount <= 0:
            return 0
        self.damage_marked += amount
        self.damaged_this_turn = True
        return amount

    def clear_damage(self) -> None:
        self.damage_marked = 0

    @property
    def lethal_damage(self) -> bool:
        """是否已受到致命伤害。"""
        if not self.is_creature:
            return False
        return self.damage_marked >= self.toughness()

    @property
    def is_dead(self) -> bool:
        """是否应当被消灭（防御力 <= 0 或致命伤害且非不灭）。"""
        if not self.is_creature:
            return False
        if self.toughness() <= 0:
            return True
        if self.damage_marked >= self.toughness() and not self.has_keyword(Keyword.INDESTRUCTIBLE):
            return True
        return False

    # ------------------------------------------------------------ 异能
    @property
    def abilities(self) -> list[Any]:
        """当前生效的异能（考虑"失去所有异能"）。"""
        if self._lost_abilities:
            return []
        return list(self.data.abilities)

    # ------------------------------------------------------------ 指示物
    def add_counter(self, kind: str, amount: int = 1) -> None:
        self.counters[kind] = self.counters.get(kind, 0) + amount

    def remove_counter(self, kind: str, amount: int = 1) -> int:
        have = self.counters.get(kind, 0)
        removed = min(have, amount)
        if removed:
            self.counters[kind] = have - removed
            if self.counters[kind] == 0:
                del self.counters[kind]
        return removed

    # ------------------------------------------------------------ 重置层修正
    def reset_layer_state(self) -> None:
        """每次重算前清空层系统写入的临时值。"""
        self._pt_modify = 0
        self._pt_modify_t = 0
        self._pt_set = None
        self._pt_switch = False
        self._extra_keywords.clear()
        self._lost_keywords.clear()
        self._lost_abilities = False
        self._type_override = None
        self._color_override = None

    def __repr__(self) -> str:  # pragma: no cover
        if self.is_creature:
            return f"<{self.name} {self.power()}/{self.toughness()}>"
        return f"<{self.name}>"
