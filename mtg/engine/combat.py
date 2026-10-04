"""战斗系统：宣告攻击者/阻挡者、伤害分配、先攻与连击、践踏与死触。

流程遵循规则 506-510：
    战斗开始 → 宣告攻击者 → 宣告阻挡者 →（先攻伤害）→ 战斗伤害 → 战斗结束
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .effects import Context
from .types import Keyword, Zone

if TYPE_CHECKING:
    from .card import Permanent
    from .game import Game
    from .player import Player


@dataclass
class AttackDeclaration:
    """一次攻击宣告。"""

    attacker: "Permanent"
    #: 攻击目标：牌手、鹏洛客或战役
    target: Any


@dataclass
class CombatState:
    """本回合战斗的完整状态。"""

    attackers: list[AttackDeclaration] = field(default_factory=list)
    #: attacker -> 阻挡它的生物列表
    blocks: dict[int, list["Permanent"]] = field(default_factory=dict)
    #: 已被阻挡的攻击者集合（id）
    blocked: set[int] = field(default_factory=set)
    #: 攻击方指定的伤害分配顺序：attacker id -> blocker id 列表
    assignment_order: dict[int, list[int]] = field(default_factory=dict)
    #: 本回合造成过战斗伤害的攻击者（用于触发）
    damaged_players: set[int] = field(default_factory=set)

    def clear(self) -> None:
        self.attackers.clear()
        self.blocks.clear()
        self.blocked.clear()
        self.assignment_order.clear()
        self.damaged_players.clear()

    def blockers_of(self, attacker: "Permanent") -> list["Permanent"]:
        return self.blocks.get(id(attacker), [])

    def is_blocked(self, attacker: "Permanent") -> bool:
        return id(attacker) in self.blocked


class CombatManager:
    """管理战斗流程与伤害计算。"""

    def __init__(self, game: "Game") -> None:
        self.game = game
        self.state = CombatState()

    # ---------------------------------------------------------------- 合法性
    def can_attack(self, permanent: "Permanent", target: Any = None) -> tuple[bool, str]:
        """判断一个生物能否攻击，返回 (是否可行, 原因)。"""
        if not permanent.is_creature:
            return False, "不是生物"
        if permanent.tapped:
            return False, "已横置"
        if permanent.is_sick:
            return False, "召唤失调"
        if permanent.has_keyword(Keyword.DEFENDER):
            return False, "具守备异能，无法攻击"
        if permanent.card.zone != Zone.BATTLEFIELD:
            return False, "不在战场上"
        return True, ""

    def can_block(self, blocker: "Permanent", attacker: "Permanent") -> tuple[bool, str]:
        """判断一个生物能否阻挡某个攻击者。"""
        if not blocker.is_creature:
            return False, "不是生物"
        if blocker.tapped:
            return False, "已横置"
        if blocker.card.zone != Zone.BATTLEFIELD:
            return False, "不在战场上"

        # 飞行：只有飞行或延势能阻挡
        if attacker.has_keyword(Keyword.FLYING):
            if not (blocker.has_keyword(Keyword.FLYING) or blocker.has_keyword(Keyword.REACH)):
                return False, "攻击者具飞行异能"
        return True, ""

    def minimum_blockers(self, attacker: "Permanent") -> int:
        """阻挡该攻击者所需的最少生物数（威慑 = 2）。"""
        return 2 if attacker.has_keyword(Keyword.MENACE) else 1

    # ---------------------------------------------------------------- 宣告
    def declare_attackers(self, declarations: list[tuple["Permanent", Any]]) -> None:
        """宣告攻击者。生物会横置（警戒除外）。"""
        self.state.attackers.clear()
        for attacker, target in declarations:
            ok, reason = self.can_attack(attacker, target)
            if not ok:
                self.game.log(f"{attacker.name} 无法攻击：{reason}")
                continue
            if not attacker.has_keyword(Keyword.VIGILANCE):
                attacker.tap()
            attacker.attacked_this_turn = True
            self.state.attackers.append(AttackDeclaration(attacker=attacker, target=target))
            self.game.events.attacked(attacker, target if hasattr(target, "life") else attacker.controller.opponent)
            self.game.log(f"{attacker.name} 攻击 {getattr(target, 'name', '对手')}")

    def declare_blockers(self, assignments: dict["Permanent", list["Permanent"]]) -> None:
        """宣告阻挡者。"""
        self.state.blocks.clear()
        self.state.blocked.clear()
        for attacker, blockers in assignments.items():
            if not blockers:
                continue
            legal = [b for b in blockers if self.can_block(b, attacker)[0]]
            if len(legal) < self.minimum_blockers(attacker) and len(blockers) >= self.minimum_blockers(attacker):
                self.game.log(f"{attacker.name} 具威慑，需要至少两个生物阻挡")
                continue
            self.state.blocks[id(attacker)] = legal
            self.state.blocked.add(id(attacker))
            for blocker in legal:
                blocker.blocked_count += 1
                self.game.events.blocked(blocker, attacker)
                self.game.log(f"{blocker.name} 阻挡 {attacker.name}")
            # 默认伤害分配顺序：按阻挡者防御力从小到大（先打死脆的）
            self.state.assignment_order[id(attacker)] = [
                id(b) for b in sorted(legal, key=lambda x: (x.toughness(), x.power()))
            ]

    # ---------------------------------------------------------------- 伤害
    def assign_damage(self, first_strike: bool) -> None:
        """执行一次战斗伤害分配。``first_strike=True`` 时为先攻/连击伤害步骤。"""
        game = self.game
        log: list[str] = []

        for declaration in list(self.state.attackers):
            attacker = declaration.attacker
            if attacker.card.zone != Zone.BATTLEFIELD:
                continue

            has_first = attacker.has_keyword(Keyword.FIRST_STRIKE) or attacker.has_keyword(Keyword.DOUBLE_STRIKE)
            if first_strike != has_first:
                # 普通伤害步骤中，连击生物再次造成伤害
                if not first_strike and not attacker.has_keyword(Keyword.DOUBLE_STRIKE):
                    continue
                if first_strike:
                    continue

            power = attacker.power()
            if power <= 0:
                continue

            blockers = [b for b in self.state.blockers_of(attacker) if b.card.zone == Zone.BATTLEFIELD]

            if not blockers:
                # 未被阻挡：直接对原目标造成伤害
                self._damage_unblocked(attacker, declaration.target, power, log)
                continue

            # 被阻挡：按指定顺序分配伤害
            order_ids = self.state.assignment_order.get(id(attacker), [id(b) for b in blockers])
            ordered = [b for b in sorted(blockers, key=lambda x: order_ids.index(id(x)) if id(x) in order_ids else 999)]
            self._damage_blocked(attacker, ordered, declaration.target, power, log)

        # 阻挡者对攻击者造成伤害
        for declaration in list(self.state.attackers):
            attacker = declaration.attacker
            if attacker.card.zone != Zone.BATTLEFIELD:
                continue
            for blocker in [b for b in self.state.blockers_of(attacker) if b.card.zone == Zone.BATTLEFIELD]:
                blocker_has_first = blocker.has_keyword(Keyword.FIRST_STRIKE) or blocker.has_keyword(
                    Keyword.DOUBLE_STRIKE
                )
                if first_strike != blocker_has_first:
                    if not first_strike and not blocker.has_keyword(Keyword.DOUBLE_STRIKE):
                        continue
                    if first_strike:
                        continue
                self._blocker_damage(blocker, attacker, log)

        for line in log:
            game.log(line)

    def _damage_unblocked(self, attacker: "Permanent", target: Any, power: int, log: list[str]) -> None:
        """未被阻挡的攻击者对牌手/鹏洛客造成伤害。"""
        if hasattr(target, "damage"):
            target.damage(power, attacker, combat=True)
            log.append(f"{attacker.name} 对 {target.name} 造成 {power} 点战斗伤害")
            self.state.damaged_players.add(id(target))
        elif hasattr(target, "remove_counter") and target.is_battle:
            # 战役：移除防御指示物
            target.remove_counter("defense", power)
            log.append(f"{attacker.name} 对战役 {target.name} 造成 {power} 点伤害")
        elif hasattr(target, "remove_counter") and target.is_planeswalker:
            target.remove_counter("loyalty", power)
            log.append(f"{attacker.name} 对 {target.name} 造成 {power} 点伤害（忠诚下降）")

        if attacker.has_keyword(Keyword.LIFELINK):
            attacker.controller.gain_life(power)

    def _damage_blocked(
        self, attacker: "Permanent", blockers: list["Permanent"], target: Any, power: int, log: list[str]
    ) -> None:
        """被阻挡时的伤害分配（含践踏）。"""
        remaining = power
        trample = attacker.has_keyword(Keyword.TRAMPLE)
        lifelink_total = 0

        for blocker in blockers:
            if remaining <= 0:
                break
            # 需要分配的"致命伤害"数量
            lethal = blocker.toughness() - blocker.damage_marked
            if attacker.has_keyword(Keyword.DEATHTOUCH):
                lethal = 1  # 死触：1 点即致命
            lethal = max(lethal, 0)

            if trample:
                assign = min(remaining, lethal)
            else:
                # 非践踏：攻击方可以全部分配给第一个，默认分配致命伤害后溢出给下一个
                assign = min(remaining, lethal)
                if assign == 0 and remaining > 0:
                    assign = remaining  # 前面的阻挡者已死，全部给下一个

            if assign <= 0:
                continue

            blocker.mark_damage(assign, attacker, combat=True)
            self.game.events.creature_damaged(blocker, assign, attacker, combat=True)
            log.append(f"{attacker.name} 对 {blocker.name} 造成 {assign} 点战斗伤害")
            lifelink_total += assign
            remaining -= assign

        # 践踏的溢出伤害打给原目标
        if remaining > 0 and trample:
            if hasattr(target, "damage"):
                target.damage(remaining, attacker, combat=True)
                log.append(f"{attacker.name} 践踏对 {target.name} 造成 {remaining} 点战斗伤害")
                lifelink_total += remaining

        if attacker.has_keyword(Keyword.LIFELINK) and lifelink_total:
            attacker.controller.gain_life(lifelink_total)

    def _blocker_damage(self, blocker: "Permanent", attacker: "Permanent", log: list[str]) -> None:
        """阻挡者对攻击者造成伤害。"""
        power = blocker.power()
        if power <= 0:
            return
        attacker.mark_damage(power, blocker, combat=True)
        self.game.events.creature_damaged(attacker, power, blocker, combat=True)
        log.append(f"{blocker.name} 对 {attacker.name} 造成 {power} 点战斗伤害")
        if blocker.has_keyword(Keyword.LIFELINK):
            blocker.controller.gain_life(power)

    # ---------------------------------------------------------------- 步骤推进
    def has_first_strike_creatures(self) -> bool:
        """本回合战斗中是否存在先攻/连击生物。"""
        for declaration in self.state.attackers:
            attacker = declaration.attacker
            if attacker.has_keyword(Keyword.FIRST_STRIKE) or attacker.has_keyword(Keyword.DOUBLE_STRIKE):
                return True
        for blockers in self.state.blocks.values():
            for blocker in blockers:
                if blocker.has_keyword(Keyword.FIRST_STRIKE) or blocker.has_keyword(Keyword.DOUBLE_STRIKE):
                    return True
        return False

    def end_of_combat(self) -> None:
        """战斗结束：清理临时状态。"""
        for declaration in self.state.attackers:
            declaration.attacker.attacked_this_turn = False
        for blockers in self.state.blocks.values():
            for blocker in blockers:
                blocker.blocked_count = 0
        self.state.clear()

    # ---------------------------------------------------------------- 供 AI 使用
    def attacking_creatures(self) -> list["Permanent"]:
        return [d.attacker for d in self.state.attackers if d.attacker.card.zone == Zone.BATTLEFIELD]

    def total_attack_power(self) -> int:
        return sum(a.power() for a in self.attacking_creatures())
