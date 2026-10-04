"""异能模型：触发式、启动式、静态、法术力异能、咒语效果、关键字。

由 :mod:`mtg.cards.oracle_parser` 从真实规则文本解析生成，
被引擎在结算、堆叠、层系统中消费。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .effects import Effect
from .mana import ManaSymbol
from .types import EventType, Keyword


@dataclass
class TargetSpec:
    """一个目标的规格说明，用于施放/启动时校验与选择。"""

    #: 'creature' | 'player' | 'any' | 'permanent' | 'artifact' | 'enchantment'
    #: 'creature_or_player' | 'spell' | 'land' | 'planeswalker' | 'battlefield_creature'
    kind: str = "any"
    #: 'any' | 'opponent' | 'you' | 'other'
    controller: str = "any"
    #: 可选目标（"up to one"）
    optional: bool = False
    #: 数量（如 "target two creatures"）
    count: int = 1
    #: 附加过滤文本（未解析部分）
    filter_text: str = ""

    def describe(self) -> str:
        names = {
            "creature": "生物",
            "player": "牌手",
            "any": "任意目标",
            "permanent": "永久物",
            "artifact": "神器",
            "enchantment": "结界",
            "creature_or_player": "生物或牌手",
            "spell": "咒语",
            "land": "地",
            "planeswalker": "鹏洛客",
            "creature_or_planeswalker": "生物或鹏洛客",
        }
        return names.get(self.kind, self.kind)


@dataclass
class Cost:
    """启动式异能的费用。"""

    mana: list[ManaSymbol] = field(default_factory=list)
    tap: bool = False
    untap: bool = False
    sacrifice_self: bool = False
    sacrifice_other: TargetSpec | None = None
    discard: int = 0
    life: int = 0
    remove_counters: tuple[str, int] | None = None
    #: 每回合只能启动一次（"Activate only once each turn"）
    once_per_turn: bool = False
    #: 只能于法术时机启动
    sorcery_timing: bool = False
    text: str = ""

    def describe(self) -> str:
        parts = []
        if self.mana:
            parts.append("".join(str(s) for s in self.mana))
        if self.tap:
            parts.append("横置")
        if self.sacrifice_self:
            parts.append("牺牲此永久物")
        if self.sacrifice_other:
            parts.append(f"牺牲{self.sacrifice_other.describe()}")
        if self.life:
            parts.append(f"支付 {self.life} 点生命")
        if self.discard:
            parts.append(f"弃 {self.discard} 张牌")
        if self.remove_counters:
            kind, amount = self.remove_counters
            parts.append(f"移去 {amount} 个{kind}指示物")
        return "，".join(parts) if parts else "免费"


@dataclass
class StaticAbility:
    """静态（持续）异能：作用于战场的持续修正。"""

    #: 作用范围: 'your_creatures' | 'all_creatures' | 'other_creatures' | 'self' | 'opponent_creatures'
    scope: str
    power: int = 0
    toughness: int = 0
    keywords: list[Keyword] = field(default_factory=list)
    grant_keywords: list[Keyword] = field(default_factory=list)
    #: "获得所有生物类别" 之类的文本改写
    text: str = ""
    #: 条件（如"只要你操控X"），未解析时保留文本
    condition: str = ""
    layer: int = 8


@dataclass
class TriggeredAbility:
    """触发式异能。"""

    trigger: EventType
    effects: list[Effect] = field(default_factory=list)
    targets: list[TargetSpec] = field(default_factory=list)
    #: 触发条件限定，如 "whenever you cast an instant or sorcery spell"
    condition: str = ""
    #: 是否为"可以"（may）
    optional: bool = False
    text: str = ""
    #: 每回合只触发一次
    once_per_turn: bool = False


@dataclass
class ActivatedAbility:
    """启动式异能（含法术力异能）。"""

    cost: Cost
    effects: list[Effect] = field(default_factory=list)
    targets: list[TargetSpec] = field(default_factory=list)
    is_mana_ability: bool = False
    text: str = ""


@dataclass
class SpellAbility:
    """咒语（施放该牌时）的效果。"""

    effects: list[Effect] = field(default_factory=list)
    targets: list[TargetSpec] = field(default_factory=list)
    text: str = ""
    #: 需要选择模式（如 "Choose one —"）
    modes: list["SpellAbility"] = field(default_factory=list)


@dataclass
class KeywordAbility:
    """单纯的关键字异能（无声效果，由引擎直接实现）。"""

    keyword: Keyword
    param: str = ""
    text: str = ""


@dataclass
class Ability:
    """一个统一的异能容器。``kind`` 决定其余字段的含义。"""

    kind: str  # 'keyword' | 'triggered' | 'activated' | 'static' | 'spell'
    keyword: Keyword | None = None
    triggered: TriggeredAbility | None = None
    activated: ActivatedAbility | None = None
    static: StaticAbility | None = None
    spell: SpellAbility | None = None
    text: str = ""

    @property
    def is_mana_ability(self) -> bool:
        return bool(self.activated and self.activated.is_mana_ability)

    def describe(self) -> str:
        if self.kind == "keyword" and self.keyword:
            base = str(self.keyword.value)
            return f"{base} {self.text}".strip() if self.text and self.text != base else base
        if self.text:
            return self.text
        return self.kind


def ability_text(ability: Ability) -> str:
    """给 UI 用的异能文本。"""
    if ability.kind == "triggered" and ability.triggered:
        return ability.triggered.text or ability.text
    if ability.kind == "activated" and ability.activated:
        return ability.activated.text or ability.text
    if ability.kind == "static" and ability.static:
        return ability.static.text or ability.text
    return ability.text
