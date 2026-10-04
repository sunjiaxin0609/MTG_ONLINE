"""状态检查（State-Based Actions，规则 704）。

每次玩家将获得优先权时、以及每次结算后自动执行，直到状态稳定。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .types import Zone

if TYPE_CHECKING:
    from .card import Permanent
    from .game import Game


def check_state_based_actions(game: "Game") -> bool:
    """执行一轮状态检查。返回是否发生了变化（需要重来一轮）。"""
    changed = False

    # 1) 生命值 <= 0 的牌手输掉游戏
    for player in game.players:
        if player.life <= 0 and not player.has_lost:
            game.log(f"{player.name} 生命值降至 {player.life}，输掉游戏")
            player.has_lost = True
            changed = True
        if player.poison_counters >= 10 and not player.has_lost:
            game.log(f"{player.name} 中毒指示物达到 10，输掉游戏")
            player.has_lost = True
            changed = True

    # 2) 传奇规则：同名传奇永久物只保留一个
    for player in game.players:
        seen: dict[str, "Permanent"] = {}
        for permanent in list(player.battlefield):
            if not permanent.is_legendary or permanent.phased_out:
                continue
            # 以"完整名称"判断（双面卡两面都算同名）
            key = permanent.name
            if key in seen:
                game.log(f"传奇规则：{permanent.name} 被置入坟场")
                game.put_permanent_into_graveyard(permanent, reason="legend rule")
                changed = True
            else:
                seen[key] = permanent

    # 3) 防御力 <= 0 的生物进坟场
    for player in game.players:
        for permanent in list(player.battlefield):
            if permanent.is_creature and permanent.toughness() <= 0 and permanent.card.zone == Zone.BATTLEFIELD:
                game.log(f"{permanent.name} 防御力为 {permanent.toughness()}，被置入坟场")
                game.put_permanent_into_graveyard(permanent, reason="toughness zero")
                changed = True

    # 4) 受到致命伤害的生物被消灭
    for player in game.players:
        for permanent in list(player.battlefield):
            if permanent.card.zone != Zone.BATTLEFIELD or not permanent.is_creature:
                continue
            if permanent.damage_marked >= permanent.toughness() > 0:
                if permanent.has_keyword(game.keywords.INDESTRUCTIBLE):
                    continue
                game.log(f"{permanent.name} 受到 {permanent.damage_marked} 点伤害，被消灭")
                game.destroy_permanent(permanent, by_damage=True)
                changed = True

    # 5) 鹏洛客忠诚为 0 进坟场
    for player in game.players:
        for permanent in list(player.battlefield):
            if permanent.is_planeswalker and permanent.loyalty <= 0 and permanent.card.zone == Zone.BATTLEFIELD:
                game.log(f"{permanent.name} 忠诚降至 0，被置入坟场")
                game.put_permanent_into_graveyard(permanent, reason="loyalty zero")
                changed = True

    # 6) 灵气/武具失去结附对象则进坟场
    for player in game.players:
        for permanent in list(player.battlefield):
            if permanent.card.zone != Zone.BATTLEFIELD:
                continue
            needs_host = _needs_host(permanent)
            if needs_host and permanent.attached_to is None:
                game.log(f"{permanent.name} 失去结附对象，被置入坟场")
                game.put_permanent_into_graveyard(permanent, reason="unattached")
                changed = True
            elif (
                needs_host
                and permanent.attached_to is not None
                and permanent.attached_to.card.zone != Zone.BATTLEFIELD
            ):
                game.log(f"{permanent.name} 失去结附对象，被置入坟场")
                game.put_permanent_into_graveyard(permanent, reason="unattached")
                changed = True

    # 7) 战役的防御指示物耗尽 → 转化（简化：置入坟场或由拥有者施放）
    for player in game.players:
        for permanent in list(player.battlefield):
            if permanent.is_battle and permanent.counters.get("defense", 0) <= 0:
                if permanent.card.zone == Zone.BATTLEFIELD:
                    game.log(f"{permanent.name} 防御指示物耗尽")
                    game.put_permanent_into_graveyard(permanent, reason="battle defeated")
                    changed = True

    if changed:
        game.recalculate()
    return changed


def _needs_host(permanent: "Permanent") -> bool:
    """该永久物是否需要结附在其他东西上。"""
    if "Aura" in permanent.subtypes:
        return True
    if "Equipment" in permanent.subtypes:
        return permanent.attached_to is not None  # 武具未佩戴时留在战场
    if "Fortification" in permanent.subtypes:
        return permanent.attached_to is not None
    return False


def run_until_stable(game: "Game", max_iterations: int = 20) -> None:
    """反复执行状态检查直到稳定。"""
    for _ in range(max_iterations):
        if not check_state_based_actions(game):
            return
