"""牌手模型：生命、区域、法术力池、抓牌/弃牌等基本动作。"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .card import Card, CardData, Permanent
from .mana import ManaPool
from .types import Zone

if TYPE_CHECKING:
    from .game import Game


@dataclass
class Player:
    """一位牌手。"""

    name: str
    game: "Game" = None  # type: ignore[assignment]

    life: int = 20
    max_hand_size: int = 7
    poison_counters: int = 0
    energy_counters: int = 0

    library: list[Card] = field(default_factory=list)
    hand: list[Card] = field(default_factory=list)
    graveyard: list[Card] = field(default_factory=list)
    exile: list[Card] = field(default_factory=list)
    battlefield: list[Permanent] = field(default_factory=list)

    mana_pool: ManaPool = field(default_factory=ManaPool)

    lands_played_this_turn: int = 0
    lands_per_turn: int = 1
    has_drawn_this_turn: bool = False
    lost_this_turn: bool = False

    #: AI 控制标记
    is_ai: bool = False
    #: 该牌手是否已"输掉"
    has_lost: bool = False
    #: 是否已调度过
    mulligan_count: int = 0

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Player {self.name} life={self.life}>"

    # ---------------------------------------------------------------- 区域访问
    def zone_list(self, zone: Zone) -> list:
        return {
            Zone.LIBRARY: self.library,
            Zone.HAND: self.hand,
            Zone.BATTLEFIELD: self.battlefield,
            Zone.GRAVEYARD: self.graveyard,
            Zone.EXILE: self.exile,
        }.get(zone, [])

    # ---------------------------------------------------------------- 牌库操作
    def shuffle(self, rng: random.Random | None = None) -> None:
        rng = rng or self.game.rng
        rng.shuffle(self.library)

    def draw(self, count: int = 1) -> list[Card]:
        """抓牌。牌库为空则触发"抓空牌库"。"""
        drawn: list[Card] = []
        for _ in range(count):
            if not self.library:
                self.game.on_empty_library(self)
                break
            card = self.library.pop(0)
            self._move_to_hand(card)
            drawn.append(card)
            self.game.events.card_drawn(self, card)
        return drawn

    def _move_to_hand(self, card: Card) -> None:
        card.zone = Zone.HAND
        card.controller = self
        self.hand.append(card)

    def mill(self, count: int) -> list[Card]:
        """磨牌：把牌库顶的牌置入坟场。"""
        milled: list[Card] = []
        for _ in range(count):
            if not self.library:
                break
            card = self.library.pop(0)
            self.move_to_graveyard(card)
            milled.append(card)
        return milled

    def discard(self, card: Card) -> None:
        if card in self.hand:
            self.hand.remove(card)
            self.move_to_graveyard(card)
            self.game.events.card_discarded(self, card)

    def discard_random(self, count: int = 1, rng: random.Random | None = None) -> list[Card]:
        rng = rng or self.game.rng
        out = []
        for _ in range(count):
            if not self.hand:
                break
            card = rng.choice(self.hand)
            self.discard(card)
            out.append(card)
        return out

    def move_to_graveyard(self, card: Card) -> None:
        card.zone = Zone.GRAVEYARD
        self.graveyard.append(card)
        card.permanent = None

    def move_to_exile(self, card: Card) -> None:
        card.zone = Zone.EXILE
        self.exile.append(card)
        card.permanent = None

    def put_on_top(self, card: Card) -> None:
        card.zone = Zone.LIBRARY
        self.library.insert(0, card)

    def put_on_bottom(self, card: Card) -> None:
        card.zone = Zone.LIBRARY
        self.library.append(card)

    # ---------------------------------------------------------------- 生命
    def gain_life(self, amount: int) -> int:
        if amount <= 0:
            return 0
        self.life += amount
        self.game.events.life_gained(self, amount)
        return amount

    def lose_life(self, amount: int) -> int:
        if amount <= 0:
            return 0
        self.life -= amount
        self.game.events.life_lost(self, amount)
        return amount

    def damage(self, amount: int, source: Permanent | None = None, combat: bool = False) -> int:
        """受到伤害（会被"防止伤害"等替代性效应改写）。"""
        actual = self.game.replacement.modify_damage_to_player(self, amount, source)
        if actual <= 0:
            return 0
        self.life -= actual
        self.game.events.player_damaged(self, actual, source, combat)
        if self.life <= 0:
            self.lost_this_turn = True
        return actual

    # ---------------------------------------------------------------- 战场
    @property
    def creatures(self) -> list[Permanent]:
        return [p for p in self.battlefield if p.is_creature and not p.phased_out]

    @property
    def untapped_creatures(self) -> list[Permanent]:
        return [c for c in self.creatures if not c.tapped]

    @property
    def lands(self) -> list[Permanent]:
        return [p for p in self.battlefield if p.is_land and not p.phased_out]

    @property
    def untapped_lands(self) -> list[Permanent]:
        return [p for p in self.lands if not p.tapped]

    @property
    def permanents(self) -> list[Permanent]:
        return [p for p in self.battlefield if not p.phased_out]

    @property
    def artifacts(self) -> list[Permanent]:
        return [p for p in self.permanents if p.is_artifact]

    @property
    def enchantments(self) -> list[Permanent]:
        return [p for p in self.permanents if p.is_enchantment]

    @property
    def planeswalkers(self) -> list[Permanent]:
        return [p for p in self.permanents if p.is_planeswalker]

    def controls_legendary(self, name: str) -> Permanent | None:
        for perm in self.battlefield:
            if perm.is_legendary and perm.name == name:
                return perm
        return None

    # ---------------------------------------------------------------- 法术力
    def add_mana(self, color: str, amount: int = 1) -> None:
        self.mana_pool.add(color, amount)

    def empty_mana_pool(self) -> None:
        self.mana_pool.empty()

    def can_play_land(self) -> bool:
        return self.lands_played_this_turn < self.lands_per_turn

    # ---------------------------------------------------------------- 对手
    @property
    def opponent(self) -> "Player":
        return self.game.other_player(self)

    def is_opponent(self, other: "Player") -> bool:
        return other is not self

    # ---------------------------------------------------------------- 套牌
    def load_deck(self, card_data_list: list[CardData]) -> None:
        """用一组 CardData 构建牌库。"""
        self.library = []
        for data in card_data_list:
            card = Card(data=data, owner=self, zone=Zone.LIBRARY)
            self.library.append(card)

    def library_count(self) -> int:
        return len(self.library)
