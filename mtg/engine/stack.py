"""堆叠：咒语与异能的 LIFO 结算区。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .effects import Context, Effect, apply_effect
from .types import Zone

if TYPE_CHECKING:
    from .card import Card, Permanent
    from .game import Game
    from .player import Player


@dataclass
class StackItem:
    """堆叠上的一项：一个咒语或一个异能。"""

    name: str
    controller: "Player"
    kind: str  # 'spell' | 'ability' | 'triggered'

    #: 咒语：对应的牌张；异能：来源永久物
    card: "Card | None" = None
    source: "Permanent | None" = None

    effects: list[Effect] = field(default_factory=list)
    targets: list[Any] = field(default_factory=list)
    x_value: int = 0

    countered: bool = False
    #: 该咒语/异能是否已被反击（目标全失效时也会自动被规则反击）
    fizzled: bool = False

    #: 法术力异能不使用堆叠
    is_mana_ability: bool = False

    timestamp: int = 0
    #: 触发来源文本
    text: str = ""

    def __repr__(self) -> str:  # pragma: no cover
        return f"<StackItem {self.name}>"

    # ---------------------------------------------------------------- 目标校验
    @property
    def still_has_legal_targets(self) -> bool:
        """所有目标是否已全部失效。"""
        if not self.targets:
            return True
        return any(not _target_is_illegal(t) for t in self.targets)

    @property
    def legal_targets(self) -> list[Any]:
        return [t for t in self.targets if not _target_is_illegal(t)]

    # ---------------------------------------------------------------- 结算
    def resolve(self, game: "Game") -> None:
        """结算这一项。"""
        if self.countered:
            game.log(f"{self.name} 被反击，未结算")
            if self.card and self.card.zone == Zone.STACK:
                game.put_card_into_graveyard(self.card)
            return

        if not self.still_has_legal_targets:
            game.log(f"{self.name} 的所有目标已失效，被规则反击")
            if self.card and self.card.zone == Zone.STACK:
                game.put_card_into_graveyard(self.card)
            return

        ctx = Context(
            game=game,
            source=self.source if self.source is not None else self.card,
            controller=self.controller,
            targets=self.legal_targets,
            x_value=self.x_value,
        )

        game.log(f"结算：{self.name}")
        for effect in self.effects:
            apply_effect(effect, ctx)

        # 咒语结算后进坟场（或进场）
        if self.kind == "spell" and self.card is not None:
            game.finish_spell_resolution(self.card)

    def describe(self) -> str:
        bits = [self.name]
        if self.targets:
            names = ", ".join(getattr(t, "name", str(t)) for t in self.targets)
            bits.append(f"→ {names}")
        if self.countered:
            bits.append("（已反击）")
        return " ".join(bits)


def _target_is_illegal(target: Any) -> bool:
    """目标是否已经失效（离场、区域改变、受保护等）。"""
    if target is None:
        return True
    # 永久物目标：离场即失效
    if hasattr(target, "card") and hasattr(target, "damage_marked"):
        perm: Permanent = target  # type: ignore[assignment]
        return perm.card.zone != Zone.BATTLEFIELD
    # 牌张目标：仍在堆叠上才合法
    if isinstance(getattr(target, "zone", None), Zone):
        return target.zone == Zone.EXILE or target.zone == Zone.GRAVEYARD
    return False


class Stack:
    """堆叠本体。"""

    def __init__(self, game: "Game") -> None:
        self.game = game
        self.items: list[StackItem] = []

    def push(self, item: StackItem) -> None:
        item.timestamp = self.game.next_timestamp()
        self.items.append(item)
        self.game.log(f"加入堆叠：{item.describe()}")

    def pop(self) -> StackItem | None:
        return self.items.pop() if self.items else None

    @property
    def top(self) -> StackItem | None:
        return self.items[-1] if self.items else None

    def __len__(self) -> int:
        return len(self.items)

    def __bool__(self) -> bool:
        return bool(self.items)

    def is_empty(self) -> bool:
        return not self.items

    def clear(self) -> None:
        self.items.clear()

    def describe(self) -> str:
        if not self.items:
            return "（堆叠为空）"
        lines = []
        for idx, item in enumerate(reversed(self.items)):
            mark = "▶ " if idx == 0 else "  "
            lines.append(f"{mark}{item.describe()}")
        return "\n".join(lines)
