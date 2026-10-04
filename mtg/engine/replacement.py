"""替代性效应与防止伤害（规则 614-615）。

实现的是一个精简框架：
  * 防止伤害护盾（"防止接下来将造成的 N 点伤害"）
  * 进战场方式改写（"若此生物将进场，改为……" 目前仅记录）
  * 抓牌改写（"若你将抓一张牌，改为抓两张"）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .card import Permanent
    from .game import Game
    from .player import Player


@dataclass
class PreventionShield:
    """一个防止伤害的护盾。"""

    target: Any
    remaining: int


class ReplacementManager:
    """管理替代性效应与防止伤害。"""

    def __init__(self, game: "Game") -> None:
        self.game = game
        self.shields: list[PreventionShield] = []

    # ---------------------------------------------------------------- 防止伤害
    def add_prevention(self, target: Any, amount: int) -> None:
        self.shields.append(PreventionShield(target=target, remaining=amount))

    def modify_damage_to_player(self, player: "Player", amount: int, source: Any) -> int:
        """牌手受到的伤害，扣除护盾。"""
        return self._apply_shields(player, amount)

    def modify_damage_to_creature(self, permanent: "Permanent", amount: int, source: Any) -> int:
        """生物受到的伤害，扣除护盾。"""
        return self._apply_shields(permanent, amount)

    def _apply_shields(self, target: Any, amount: int) -> int:
        remaining = amount
        for shield in list(self.shields):
            if shield.target is target and shield.remaining > 0:
                absorbed = min(shield.remaining, remaining)
                shield.remaining -= absorbed
                remaining -= absorbed
                if shield.remaining <= 0:
                    self.shields.remove(shield)
            if remaining <= 0:
                break
        return max(remaining, 0)

    def has_shield(self, target: Any) -> int:
        return sum(s.remaining for s in self.shields if s.target is target)

    def clear_expired(self) -> None:
        """清理步骤：移除过期护盾。"""
        self.shields.clear()

    # ---------------------------------------------------------------- 抓牌改写
    def modify_draw(self, player: "Player", amount: int) -> int:
        """返回实际应抓的牌数（可被"改为抓更多/更少"的效应改写）。"""
        return amount

    # ---------------------------------------------------------------- 进战场改写
    def modify_enters_battlefield(self, card: Any, tapped: bool) -> tuple[Any, bool, bool]:
        """返回 (card, tapped, 是否取消进场)。"""
        return card, tapped, False
