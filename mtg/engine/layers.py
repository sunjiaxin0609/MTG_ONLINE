"""层系统：持续效应的计算顺序（规则 613）。

引擎不缓存派生属性（攻防、颜色、类别、关键字），
而是在每次状态查询前调用 :func:`recalculate` 重算一遍。
层数较小的先应用，同层内按时间戳顺序。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .types import Keyword, Layer, Zone

if TYPE_CHECKING:
    from .card import Permanent
    from .game import Game


def recalculate(game: "Game") -> None:
    """重算战场上所有永久物的派生属性。"""
    # 1) 清空所有临时修正
    for player in game.players:
        for permanent in player.battlefield:
            permanent.reset_layer_state()

    # 2) 收集所有静态异能产生的修正
    effects: list[tuple[int, int, "Permanent", object]] = []  # (layer, timestamp, source, static)
    for player in game.players:
        for permanent in player.battlefield:
            if permanent.card.zone != Zone.BATTLEFIELD or permanent.phased_out:
                continue
            for ability in permanent.abilities:
                if ability.kind != "static" or ability.static is None:
                    continue
                static = ability.static
                if static.condition and not game.evaluate_condition(static.condition, permanent):
                    continue
                effects.append((static.layer, permanent.timestamp, permanent, static))

    # 3) 按层、时间戳排序后逐个应用
    effects.sort(key=lambda item: (item[0], item[1]))
    for _layer, _ts, source, static in effects:
        _apply_static(game, source, static)  # type: ignore[arg-type]


def _apply_static(game: "Game", source: "Permanent", static) -> None:  # noqa: ANN001
    """把一个静态异能应用到其作用范围内的永久物。"""
    targets = _scope_targets(game, source, static.scope)

    for target in targets:
        if target is source and static.scope in ("other_creatures", "other_you_control"):
            continue
        # 修正型异能通常只作用于生物
        applies_to_creature = target.is_creature
        if static.power or static.toughness:
            if applies_to_creature:
                target._pt_modify += static.power
                target._pt_modify_t += static.toughness
        for keyword in static.grant_keywords:
            target._extra_keywords.add(keyword)


def _scope_targets(game: "Game", source: "Permanent", scope: str) -> list["Permanent"]:
    """解析静态异能的作用范围。"""
    me = source.controller
    opponent = game.other_player(me)

    if scope == "self":
        return [source]
    if scope == "your_creatures":
        return list(me.creatures)
    if scope == "other_creatures":
        return [c for c in me.creatures if c is not source]
    if scope == "other_you_control":
        return [p for p in me.permanents if p is not source]
    if scope == "all_creatures":
        return [c for player in game.players for c in player.creatures]
    if scope == "opponent_creatures":
        return list(opponent.creatures)
    if scope == "your_lands":
        return list(me.lands)
    if scope == "all_lands":
        return [land for player in game.players for land in player.lands]
    if scope == "your_artifacts":
        return list(me.artifacts)
    if scope == "your_enchantments":
        return list(me.enchantments)
    if scope == "your_permanents":
        return list(me.permanents)
    if scope in ("equipped_creature", "enchanted_creature"):
        host = source.attached_to
        if host is not None and host.card.zone == Zone.BATTLEFIELD:
            return [host]
        return []
    return []


# -------------------------------------------------------------------- 临时性增益

@dataclass
class TemporaryBuff:
    """直到回合结束的增益（记录在场，清理步骤时移除）。"""

    permanent: "Permanent"
    power: int
    toughness: int
    keywords: list[Keyword] = field(default_factory=list)


class TemporaryEffectTracker:
    """管理"直到回合结束"类效应。"""

    def __init__(self) -> None:
        self.buffs: list[TemporaryBuff] = []
        self.keywords: list[tuple["Permanent", Keyword]] = []

    def add_buff(self, permanent: "Permanent", power: int, toughness: int) -> None:
        self.buffs.append(TemporaryBuff(permanent, power, toughness))
        permanent._pt_modify += power
        permanent._pt_modify_t += toughness

    def add_keyword(self, permanent: "Permanent", keyword: Keyword) -> None:
        self.keywords.append((permanent, keyword))
        permanent._extra_keywords.add(keyword)

    def clear(self) -> None:
        """清理步骤：移除所有直到回合结束的效应。"""
        for buff in self.buffs:
            buff.permanent._pt_modify -= buff.power
            buff.permanent._pt_modify_t -= buff.toughness
        self.buffs.clear()
        for permanent, keyword in self.keywords:
            permanent._extra_keywords.discard(keyword)
        self.keywords.clear()
