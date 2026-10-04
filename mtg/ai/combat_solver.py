"""战斗求解：AI 的攻击与阻挡决策。

核心是"交换评估"——把一次战斗翻译成"我损失多少分、对手损失多少分"，
只在收益为正时动手。同时保留两条硬规则：

    1. 能一击致死的攻击，无条件全攻；
    2. 会被打死的阻挡，无条件执行。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..engine.types import Keyword
from .evaluate import creature_value

if TYPE_CHECKING:
    from ..engine.card import Permanent
    from ..engine.game import Game
    from ..engine.player import Player


LIFE_WEIGHT = 0.4  # 1 点生命相当于多少场面分


def _has_first_strike(permanent: "Permanent") -> bool:
    return permanent.has_keyword(Keyword.FIRST_STRIKE) or permanent.has_keyword(Keyword.DOUBLE_STRIKE)


@dataclass
class BlockOutcome:
    """一次阻挡的结果预测。"""

    attacker_dies: bool
    blockers_die: list[bool]
    #: 践踏/漏过去的伤害
    damage_through: int
    #: 我方（防守方）净收益
    net: float


def simulate_block(attacker: "Permanent", blockers: list["Permanent"]) -> BlockOutcome:
    """模拟一个攻击者对一组阻挡者的战斗结果（不修改游戏状态）。"""
    if not blockers:
        return BlockOutcome(False, [], attacker.power(), 0.0)

    attacker_power = attacker.power()
    attacker_toughness = attacker.toughness()
    attacker_first = _has_first_strike(attacker)

    remaining = attacker_power
    blockers_die = [False] * len(blockers)
    total_damage_to_attacker = 0
    damage_through = 0

    # 按防御力升序分配伤害（与引擎默认顺序一致）
    order = sorted(range(len(blockers)), key=lambda i: (blockers[i].toughness(), blockers[i].power()))

    for idx in order:
        blocker = blockers[idx]
        if remaining <= 0:
            break
        if attacker_first and attacker_power >= blocker.toughness() and not _has_first_strike(blocker):
            # 先攻先杀死阻挡者，它打不出伤害
            blockers_die[idx] = True
            lethal = 1 if attacker.has_keyword(Keyword.DEATHTOUCH) else blocker.toughness()
            remaining -= min(remaining, max(lethal, 1))
            continue
        lethal = 1 if attacker.has_keyword(Keyword.DEATHTOUCH) else max(blocker.toughness() - 0, 1)
        assign = min(remaining, lethal)
        remaining -= assign
        if assign >= blocker.toughness() or attacker.has_keyword(Keyword.DEATHTOUCH):
            blockers_die[idx] = True
        else:
            total_damage_to_attacker += blocker.power()

    # 没被先攻杀死的阻挡者会反击
    for idx, blocker in enumerate(blockers):
        if blockers_die[idx]:
            continue
        total_damage_to_attacker += blocker.power()

    attacker_dies = (
        total_damage_to_attacker >= attacker_toughness
        or any(b.has_keyword(Keyword.DEATHTOUCH) for b in blockers if not blockers_die[blockers.index(b)])
    )
    if attacker.has_keyword(Keyword.TRAMPLE):
        damage_through = remaining
    elif not any(blockers_die):
        damage_through = 0

    # 净收益：对手损失的生物价值 − 我方损失的生物价值 − 漏过去的伤害
    opp_loss = creature_value(attacker) if attacker_dies else 0.0
    my_loss = sum(creature_value(b) for b, dead in zip(blockers, blockers_die) if dead)
    net = opp_loss - my_loss - damage_through * LIFE_WEIGHT
    return BlockOutcome(attacker_dies, blockers_die, damage_through, net)


# -------------------------------------------------------------------- 攻击

def choose_attackers(game: "Game", player: "Player") -> list[tuple["Permanent", object]]:
    """决定本回合攻击哪些生物。"""
    opponent = game.other_player(player)
    legal = [c for c in player.creatures if game.combat.can_attack(c)[0]]

    if not legal:
        return []

    # 能一击致死 → 全攻
    total_power = sum(c.power() for c in legal)
    blockers_can_stop = [c for c in opponent.creatures if not c.tapped]
    if total_power >= opponent.life and not blockers_can_stop:
        return [(c, opponent) for c in legal]

    # 对手完全挡不住（都没有能阻挡的）→ 全攻
    def can_be_blocked(attacker: "Permanent") -> bool:
        return any(game.combat.can_block(b, attacker)[0] for b in blockers_can_stop)

    unblockable = [c for c in legal if not can_be_blocked(c)]
    if len(unblockable) == len(legal):
        return [(c, opponent) for c in legal]

    attackers: list[tuple["Permanent", object]] = []

    for creature in sorted(legal, key=lambda c: -creature_value(c)):
        if not can_be_blocked(creature):
            # 挡不住就尽管打
            attackers.append((creature, opponent))
            continue

        # 对手会用最优的一组阻挡者来应对，取对我最不利的情况
        candidates = [b for b in blockers_can_stop if game.combat.can_block(b, creature)[0]]
        worst = _worst_case_block(creature, candidates, game.combat.minimum_blockers(creature))

        # 打过去的收益
        if worst.damage_through > 0:
            gain = worst.damage_through * LIFE_WEIGHT
        else:
            gain = creature.power() * LIFE_WEIGHT * 0.35  # 逼对手横置阻挡者的隐性收益

        cost = sum(
            creature_value(b)
            for b, dead in zip(_best_blockers_for(creature, candidates, game.combat.minimum_blockers(creature)),
                               worst.blockers_die)
            if dead
        )
        attacker_loss = creature_value(creature) if worst.attacker_dies else 0.0
        opponent_loss = creature_value(creature) if worst.attacker_dies else 0.0

        # 我方净收益 = 对手损失 + 造成的伤害 − 我方损失
        net = opponent_loss + gain - attacker_loss * 0.0 - cost * 0.0 + (worst.net)

        if net > -0.15:
            attackers.append((creature, opponent))

    if not attackers and unblockable:
        attackers = [(c, opponent) for c in unblockable]

    return attackers


def _best_blockers_for(attacker: "Permanent", candidates: list["Permanent"], minimum: int) -> list["Permanent"]:
    """对手会用哪几个生物来阻挡（取对手视角最优）。"""
    if not candidates:
        return []
    if minimum >= 2 and len(candidates) >= 2:
        # 威慑：挑两个最能打的
        ordered = sorted(candidates, key=lambda b: (-b.power(), b.toughness()))
        return ordered[:2]
    # 单个：优先能杀死攻击者且自己不死；其次能同归于尽；最后随便挡
    for blocker in sorted(candidates, key=lambda b: -creature_value(b)):
        outcome = simulate_block(attacker, [blocker])
        if outcome.attacker_dies and not outcome.blockers_die[0]:
            return [blocker]
    for blocker in sorted(candidates, key=lambda b: creature_value(b)):
        outcome = simulate_block(attacker, [blocker])
        if outcome.attacker_dies:
            return [blocker]
    return [min(candidates, key=lambda b: creature_value(b))]


def _worst_case_block(attacker: "Permanent", candidates: list["Permanent"], minimum: int) -> BlockOutcome:
    """从攻击方视角看，最不利的阻挡结果。"""
    chosen = _best_blockers_for(attacker, candidates, minimum)
    if not chosen:
        return BlockOutcome(False, [], attacker.power(), float(attacker.power()) * LIFE_WEIGHT)
    return simulate_block(attacker, chosen)


# -------------------------------------------------------------------- 阻挡

def choose_blockers(game: "Game", player: "Player", attackers: list["Permanent"]) -> dict["Permanent", list["Permanent"]]:
    """决定如何阻挡。"""
    available = [c for c in player.creatures if not c.tapped]
    assignments: dict["Permanent", list["Permanent"]] = {}

    if not attackers or not available:
        return assignments

    # 先算：如果完全不挡，会掉多少血
    incoming = sum(a.power() for a in attackers)
    lethal_if_not_blocked = incoming >= player.life

    # 按威胁从大到小处理
    ordered_attackers = sorted(attackers, key=lambda a: -(a.power() * 1.0 + creature_value(a) * 0.5))

    for attacker in ordered_attackers:
        candidates = [b for b in available if game.combat.can_block(b, attacker)[0]]
        if not candidates:
            continue

        minimum = game.combat.minimum_blockers(attacker)
        if len(candidates) < minimum:
            continue

        best: list["Permanent"] | None = None
        best_score = -999.0

        # 单阻挡者
        for blocker in candidates:
            outcome = simulate_block(attacker, [blocker])
            score = outcome.net
            # 快死了就一定要挡
            if lethal_if_not_blocked:
                score += 3.0
            if score > best_score:
                best_score = score
                best = [blocker]

        # 双阻挡者（能打死大生物时值得）
        if len(candidates) >= 2:
            for i in range(len(candidates)):
                for j in range(i + 1, len(candidates)):
                    pair = [candidates[i], candidates[j]]
                    outcome = simulate_block(attacker, pair)
                    score = outcome.net - 0.4  # 多搭一个生物有额外代价
                    if lethal_if_not_blocked:
                        score += 3.0
                    if score > best_score:
                        best_score = score
                        best = pair

        # 只有当收益为正、或者不挡就会死时才真的挡
        if best is not None and (best_score > 0.05 or lethal_if_not_blocked):
            assignments[attacker] = best
            for blocker in best:
                if blocker in available:
                    available.remove(blocker)

    return assignments
