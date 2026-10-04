"""事件总线与触发式异能调度。

引擎在游戏各处发出事件（"某生物进场"、"某牌手抓牌"……），
事件总线负责找出战场上所有匹配的触发式异能，按 APNAP 顺序
（主动牌手先入栈，因而后结算）把它们放进堆叠。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .stack import StackItem
from .types import EventType, Zone

if TYPE_CHECKING:
    from .card import Card, Permanent
    from .game import Game
    from .player import Player


#: 同一个触发式异能在同一回合内的最大触发次数（防止无限循环）
MAX_TRIGGERS_PER_TURN = 15


@dataclass
class GameEvent:
    """一次游戏事件。"""

    type: EventType
    #: 事件主体（进场/离场的永久物、造成 damage 的来源等）
    subject: Any = None
    #: 参与者：谁触发的（通常是操控者）
    controller: "Player | None" = None
    #: 附加信息
    data: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Event {self.type.value}>"


class EventBus:
    """监听并派发触发式异能。"""

    def __init__(self, game: "Game") -> None:
        self.game = game
        self._pending: list[tuple[Any, Any, GameEvent]] = []

    # ---------------------------------------------------------------- 发出事件
    def emit(self, event_type: EventType, subject: Any = None, controller: "Player | None" = None, **data: Any) -> None:
        event = GameEvent(type=event_type, subject=subject, controller=controller, data=data)
        self.game.last_event = event
        self._collect_triggers(event)

    # ---------------------------------------------------------------- 便捷方法
    def permanent_entered(self, permanent: "Permanent") -> None:
        self.emit(EventType.ENTERS_BATTLEFIELD, permanent, permanent.controller)

    def permanent_left(self, permanent: "Permanent") -> None:
        self.emit(EventType.LEAVES_BATTLEFIELD, permanent, permanent.controller)

    def permanent_died(self, permanent: "Permanent") -> None:
        self.emit(EventType.DIES, permanent, permanent.controller)
        if permanent.is_creature:
            self.emit(EventType.CREATURE_DIED, permanent, permanent.controller)

    def attacked(self, attacker: "Permanent", defender: "Player") -> None:
        self.emit(EventType.ATTACKS, attacker, attacker.controller, defender=defender)

    def blocked(self, blocker: "Permanent", attacker: "Permanent") -> None:
        self.emit(EventType.BLOCKS, blocker, blocker.controller, attacker=attacker)

    def card_drawn(self, player: "Player", card: "Card") -> None:
        self.emit(EventType.DRAW, card, player)

    def card_discarded(self, player: "Player", card: "Card") -> None:
        self.emit(EventType.DISCARD, card, player)

    def life_gained(self, player: "Player", amount: int) -> None:
        if amount > 0:
            self.emit(EventType.GAIN_LIFE, player, player, amount=amount)

    def life_lost(self, player: "Player", amount: int) -> None:
        if amount > 0:
            self.emit(EventType.LOSE_LIFE, player, player, amount=amount)

    def player_damaged(self, player: "Player", amount: int, source: Any, combat: bool) -> None:
        self.emit(EventType.DEALS_DAMAGE, source, player, amount=amount, target=player, combat=combat)
        if combat:
            self.emit(EventType.DEALS_COMBAT_DAMAGE_PLAYER, source, source.controller if source else None,
                      amount=amount, target=player)

    def spell_cast(self, card: "Card", caster: "Player") -> None:
        self.emit(EventType.SPELL_CAST, card, caster)
        if card.data.is_creature:
            self.emit(EventType.CREATURE_SPELL_CAST, card, caster)
        if card.data.is_instant or card.data.is_sorcery:
            self.emit(EventType.INSTANT_SORCERY_CAST, card, caster)

    def untap_or_tap(self, permanent: "Permanent", tapped: bool) -> None:
        if tapped:
            self.emit(EventType.BECOMES_TAPPED, permanent, permanent.controller)
        else:
            self.emit(EventType.TURNS, permanent, permanent.controller)

    def creature_damaged(self, permanent: "Permanent", amount: int, source: Any, combat: bool) -> None:
        if combat:
            self.emit(
                EventType.DEALS_COMBAT_DAMAGE_CREATURE,
                source,
                source.controller if hasattr(source, "controller") else None,
                amount=amount,
                target=permanent,
            )

    # ---------------------------------------------------------------- 触发收集
    def _collect_triggers(self, event: GameEvent) -> None:
        """找出所有匹配该事件的触发式异能并入栈。"""
        game = self.game
        triggers: list[tuple["Player", Any, Any]] = []  # (controller, permanent, ability)

        for player in game.players:
            for permanent in list(player.battlefield):
                if permanent.card.zone != Zone.BATTLEFIELD:
                    continue
                for ability in permanent.abilities:
                    if ability.kind != "triggered" or ability.triggered is None:
                        continue
                    trig = ability.triggered
                    if trig.trigger != event.type:
                        continue
                    if not self._condition_matches(trig.condition, event, permanent):
                        continue
                    if trig.once_per_turn and getattr(permanent, "_triggered_this_turn", False):
                        continue
                    # 掐断无限循环：同一异能每回合最多触发 MAX_TRIGGERS_PER_TURN 次
                    key = trig.text or str(trig.trigger.value)
                    fired = permanent.trigger_counts.get(key, 0)
                    if fired >= MAX_TRIGGERS_PER_TURN:
                        continue
                    permanent.trigger_counts[key] = fired + 1
                    triggers.append((permanent.controller, permanent, trig))

        # APNAP：主动牌手的触发先入栈（后结算）
        active = game.active_player
        triggers.sort(key=lambda item: 0 if item[0] is active else 1)

        for controller, permanent, trig in triggers:
            permanent._triggered_this_turn = True  # type: ignore[attr-defined]
            item = StackItem(
                name=f"{permanent.name}（{trig.text or trig.trigger.value}）",
                controller=controller,
                kind="triggered",
                source=permanent,
                effects=list(trig.effects),
                targets=[],
                text=trig.text,
            )
            game.stack.push(item)
            game.log(f"触发：{permanent.name} — {trig.text or trig.trigger.value}")

    # ---------------------------------------------------------------- 条件判断
    def _condition_matches(self, condition: str, event: GameEvent, source: Any) -> bool:
        """判断触发条件附加文本是否匹配。目前支持少量常见限定。"""
        if not condition:
            return True
        lowered = condition.lower()

        # "whenever ~ attacks" 类已由 trigger 类型区分，这里处理更细的限定
        if "another" in lowered or "other" in lowered:
            # 触发主体不能是来源自身
            if event.subject is source:
                return False
        if "you control" in lowered:
            subject = event.subject
            controller = getattr(subject, "controller", None)
            if controller is not None and controller is not source.controller:
                return False
        if "opponent" in lowered:
            subject = event.subject
            controller = getattr(subject, "controller", None)
            if controller is not None and controller is source.controller:
                return False
        return True
