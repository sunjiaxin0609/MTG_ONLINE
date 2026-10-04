"""自动法术力支付：按需横置地来凑齐费用。

AI 与人类玩家共用——点一张牌就能打出去，不用手动点地。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .mana import ManaSymbol, can_pay

if TYPE_CHECKING:
    from .card import Permanent
    from .game import Game
    from .player import Player


def _activated_abilities(permanent: "Permanent") -> list[Any]:
    return [a.activated for a in permanent.abilities if a.kind == "activated" and a.activated is not None]


def _ability_colors(ability: Any) -> list[str]:
    """一个法术力异能能产出哪些颜色。"""
    colors: list[str] = []
    for effect in ability.effects:
        if effect.kind != "mana":
            continue
        if effect.param == "any":
            return ["W", "U", "B", "R", "G"]
        if effect.param in ("W", "U", "B", "R", "G"):
            colors.append(effect.param)
        elif effect.param == "C":
            colors.append("C")
    return colors


def mana_sources(player: "Player") -> list[tuple["Permanent", list[str], int]]:
    """列出所有可立即产费的法术力源（ permanent, 可产颜色, 异能序号 ）。"""
    sources: list[tuple["Permanent", list[str], int]] = []
    for permanent in player.permanents:
        if permanent.tapped or permanent.is_sick or permanent.phased_out:
            continue
        for idx, ability in enumerate(_activated_abilities(permanent)):
            if not ability.is_mana_ability:
                continue
            colors = _ability_colors(ability)
            if colors:
                sources.append((permanent, colors, idx))
                break
    return sources


def _activate_mana_ability(game: "Game", permanent: "Permanent", ability_index: int, color: str) -> None:
    abilities = _activated_abilities(permanent)
    for idx, ability in enumerate(abilities):
        if not ability.is_mana_ability:
            continue
        if color in _ability_colors(ability):
            game.activate_ability(permanent, idx, [])
            return
    for idx, ability in enumerate(abilities):
        if ability.is_mana_ability:
            game.activate_ability(permanent, idx, [])
            return


def _search_taps(
    need: dict[str, int],
    generic: int,
    sources: list[tuple["Permanent", list[str], int]],
    chosen: list[tuple["Permanent", str, int]],
    depth: int = 0,
) -> list[tuple["Permanent", str, int]] | None:
    """递归挑选要横置的法术力源。"""
    if depth > 14:
        return None
    if not need and generic <= 0:
        return list(chosen)

    used = {id(c[0]) for c in chosen}

    # 先满足颜色需求
    for color, count in need.items():
        if count <= 0:
            continue
        for permanent, colors, idx in sources:
            if id(permanent) in used or color not in colors:
                continue
            new_need = dict(need)
            new_need[color] -= 1
            if new_need[color] == 0:
                del new_need[color]
            result = _search_taps(new_need, generic, sources, chosen + [(permanent, color, idx)], depth + 1)
            if result is not None:
                return result
        return None

    # 再凑通用法术力：任何来源都能付，无需回溯，直接取用剩余的即可
    if generic > 0:
        remaining = [s for s in sources if id(s[0]) not in used]
        if len(remaining) < generic:
            return None
        for permanent, colors, idx in remaining[:generic]:
            chosen.append((permanent, colors[0] if colors else "C", idx))
    return chosen


def _compute_shortfall(
    player: "Player", symbols: list[ManaSymbol], x_value: int = 0
) -> tuple[dict[str, int], int]:
    """计算在动用未横置的法术力源之前，还缺多少颜色和通用法术力。"""
    need: dict[str, int] = {}
    generic = 0
    for symbol in symbols:
        if symbol.kind == "generic":
            generic += symbol.amount
        elif symbol.kind == "x":
            generic += x_value
        elif symbol.kind in ("colorless", "snow"):
            generic += 1
        elif symbol.kind == "colored":
            need[symbol.options[0]] = need.get(symbol.options[0], 0) + 1
        elif symbol.kind == "hybrid":
            if any(player.mana_pool.counts.get(c, 0) > 0 for c in symbol.options):
                continue
            need[symbol.options[0]] = need.get(symbol.options[0], 0) + 1

    # 池内已有法术力先抵扣
    for color in list(need):
        have = player.mana_pool.counts.get(color, 0)
        if have:
            need[color] -= min(have, need[color])
            if need[color] <= 0:
                del need[color]
    generic = max(generic - player.mana_pool.total(), 0)
    return need, generic


def could_pay(game: "Game", player: "Player", symbols: list[ManaSymbol], x_value: int = 0) -> bool:
    """判断"有没有可能"支付这笔费用（考虑未横置的地，但不真的横置）。"""
    if not symbols:
        return True
    if can_pay(symbols, player.mana_pool, x_value):
        return True
    need, generic = _compute_shortfall(player, symbols, x_value)
    if not need and generic <= 0:
        return True
    sources = mana_sources(player)
    if not sources:
        return False
    return _search_taps(need, generic, sources, []) is not None


def auto_pay(game: "Game", player: "Player", symbols: list[ManaSymbol], x_value: int = 0) -> bool:
    """自动横置法术力源来支付费用。池中已有足够法术力时不会多横置。"""
    if not symbols:
        return True
    if can_pay(symbols, player.mana_pool, x_value):
        return True

    need, generic = _compute_shortfall(player, symbols, x_value)
    sources = mana_sources(player)
    if not sources:
        return False

    plan = _search_taps(need, generic, sources, [])
    if plan is None:
        return False

    for permanent, color, _idx in plan:
        _activate_mana_ability(game, permanent, 0, color)
    return can_pay(symbols, player.mana_pool, x_value)
