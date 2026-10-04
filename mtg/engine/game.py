"""游戏主状态机：回合结构、优先权轮转、堆叠结算、区域移动。

UI 与 AI 通过统一的"决策点"接口与引擎交互::

    decision = game.advance()      # 推进到下一个需要决策的时刻
    game.submit(action)            # 提交玩家的动作

引擎自身不阻塞、不弹窗，所有需要人类输入的地方都以
:class:`Decision` 的形式交出去。
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from .card import Card, CardData, Permanent
from .combat import CombatManager
from .effects import Context, TokenSpec, apply_effect
from .events import EventBus
from .layers import TemporaryEffectTracker, recalculate
from .mana import ManaPool, apply_payment, can_pay, parse_mana_cost
from .payment import auto_pay, could_pay
from .player import Player
from .replacement import ReplacementManager
from .sba import run_until_stable
from .stack import Stack, StackItem
from .types import EventType, Keyword, Phase, Step, Zone

#: 一个回合内的阶段/步骤序列
PHASE_SEQUENCE: list[tuple[Phase, Step | None]] = [
    (Phase.UNTAP, None),
    (Phase.UPKEEP, None),
    (Phase.DRAW, None),
    (Phase.MAIN_1, None),
    (Phase.COMBAT, Step.BEGINNING_OF_COMBAT),
    (Phase.COMBAT, Step.DECLARE_ATTACKERS),
    (Phase.COMBAT, Step.DECLARE_BLOCKERS),
    (Phase.COMBAT, Step.COMBAT_DAMAGE),
    (Phase.COMBAT, Step.END_OF_COMBAT),
    (Phase.MAIN_2, None),
    (Phase.END, None),
    (Phase.CLEANUP, None),
]

STARTING_LIFE = 20
STARTING_HAND = 7
#: 堆叠深度上限：超过就强制结算到底，避免异常卡把游戏卡死
MAX_STACK_DEPTH = 40


# -------------------------------------------------------------------- 决策

@dataclass
class Decision:
    """一个需要玩家（或 AI）做决定的时刻。"""

    kind: str
    player: Player
    prompt: str = ""
    #: kind 相关的附加数据
    payload: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Decision {self.kind} for {self.player.name}>"


@dataclass
class Action:
    """玩家提交的一个动作。"""

    kind: str  # 'play_land' | 'cast' | 'activate' | 'pass' | 'attackers' | 'blockers' | ...
    card: Card | None = None
    permanent: Permanent | None = None
    ability_index: int = 0
    targets: list[Any] = field(default_factory=list)
    x_value: int = 0
    payload: dict[str, Any] = field(default_factory=dict)

    def player_or_owner(self) -> Player:
        """取动作的行使者。"""
        if self.card is not None:
            return self.card.controller or self.card.owner
        if self.permanent is not None:
            return self.permanent.controller
        raise ValueError("动作未指定行使者")


# -------------------------------------------------------------------- 游戏

class Game:
    """一局万智牌对战。"""

    def __init__(
        self,
        players: list[Player] | None = None,
        seed: int | None = None,
        starting_life: int = STARTING_LIFE,
    ) -> None:
        self.rng = random.Random(seed)
        self.players: list[Player] = []
        self.starting_life = starting_life

        if players:
            for player in players:
                player.game = self
                self.players.append(player)

        self.turn_number: int = 0
        self.active_player: Player | None = None
        self.priority_player: Player | None = None

        self.phase: Phase = Phase.UNTAP
        self.step: Step | None = None
        self._phase_index: int = 0

        self.stack = Stack(self)
        self.combat = CombatManager(self)
        self.events = EventBus(self)
        self.replacement = ReplacementManager(self)
        self.temporary = TemporaryEffectTracker()

        self.keywords = Keyword  # 供各模块引用

        self.log_lines: list[str] = []
        self.log_listeners: list[Any] = []
        self.last_event: Any = None

        self._timestamp = 0
        self._passed: set[int] = set()  # 已让过优先权的玩家（用 id 索引）
        self.pending_decision: Decision | None = None
        self.pending_action: Action | None = None

        #: 上一次施放失败的牌（供 AI 避免重复尝试同一张打不出的牌）
        self.last_failed_card: Card | None = None

        self.turn_actions_done: set[str] = set()
        self._first_strike_done: bool = False
        self._skip_to_main2: bool = False
        self.game_over: bool = False
        self.winner: Player | None = None

        # 每个玩家对应的决策代理（AI 或 UI 桥接）
        self.agents: dict[int, Any] = {}

    # ---------------------------------------------------------------- 日志
    def log(self, message: str) -> None:
        self.log_lines.append(message)
        for listener in self.log_listeners:
            try:
                listener(message)
            except Exception:  # noqa: BLE001 - UI 监听器异常不应中断引擎
                pass

    def on_log(self, listener: Any) -> None:
        self.log_listeners.append(listener)

    def recent_log(self, count: int = 12) -> list[str]:
        return self.log_lines[-count:]

    # ---------------------------------------------------------------- 工具
    def next_timestamp(self) -> int:
        self._timestamp += 1
        return self._timestamp

    def other_player(self, player: Player) -> Player:
        for other in self.players:
            if other is not player:
                return other
        return player

    @property
    def defending_player(self) -> Player | None:
        return self.other_player(self.active_player) if self.active_player else None

    def recalculate(self) -> None:
        recalculate(self)

    def evaluate_condition(self, condition: str, source: Any) -> bool:
        """判断静态异能的条件文本（目前保守处理：无法判定时视为成立）。"""
        if not condition:
            return True
        return True

    # ---------------------------------------------------------------- 开局
    def setup(self, draw_opening: bool = True) -> None:
        """洗牌、设定生命、抓起手牌。"""
        self.log("=== 新的一局开始 ===")
        for player in self.players:
            player.life = self.starting_life
            player.shuffle(self.rng)
            if draw_opening:
                player.draw(STARTING_HAND)
                self.log(f"{player.name} 抓了 {STARTING_HAND} 张起手牌")
        self.turn_number = 0

    def start(self, first_player: Player | None = None) -> None:
        """开始第一个回合。"""
        self.active_player = first_player or self.players[0]
        self.turn_number = 1
        self._phase_index = 0
        self.phase, self.step = PHASE_SEQUENCE[0]
        self.priority_player = self.active_player
        self.log(f"\n--- 第 {self.turn_number} 回合：{self.active_player.name} ---")

    # ================================================================ 主循环

    def advance(self) -> Decision | None:
        """推进游戏直到出现一个需要决策的时刻。

        返回 ``None`` 表示游戏已结束。
        """
        guard = 0
        while not self.game_over:
            guard += 1
            if guard > 5000:
                self.log("（引擎保护：推进次数过多，强制结束）")
                self.game_over = True
                break

            # 1) 游戏结束判定
            if self._check_game_over():
                break

            # 2) 处理待提交的玩家动作
            if self.pending_action is not None:
                action = self.pending_action
                self.pending_action = None
                self._perform_action(action)
                self._after_action()
                continue

            # 3) 执行当前阶段的回合动作（重置/抓牌/清理）
            if self._needs_turn_action():
                self._do_turn_action()
                self._after_action()
                continue

            # 4) 宣告攻击者 / 宣告阻挡者
            decision = self._maybe_declare_decision()
            if decision is not None:
                self.pending_decision = decision
                return decision

            # 5) 堆叠非空且双方连续让过 → 结算顶项
            if self.stack and len(self._passed) >= len(self.players):
                if len(self.stack) > MAX_STACK_DEPTH:
                    self.log(f"（堆叠深度超过 {MAX_STACK_DEPTH}，强制结算到底以防卡死）")
                    while self.stack:
                        self._resolve_top()
                    self._after_action()
                    continue
                self._resolve_top()
                self._after_action()
                continue

            # 6) 双方连续让过且堆叠为空 → 推进阶段
            if len(self._passed) >= len(self.players):
                self._advance_phase()
                self._after_action()
                continue

            # 7) 需要某位玩家做决定（优先权）
            if self.priority_player is not None and id(self.priority_player) not in self._passed:
                return self._priority_decision()

            # 8) 优先权轮转
            self._rotate_priority()

        return None

    def submit(self, action: Action) -> None:
        """提交一个玩家动作。"""
        self.pending_action = action
        self.pending_decision = None

    def _priority_decision(self) -> Decision:
        player = self.priority_player
        assert player is not None
        return Decision(
            kind="priority",
            player=player,
            prompt=f"{self.phase.value}{'·' + self.step.value if self.step else ''} — {player.name} 拥有优先权",
            payload={"phase": self.phase, "step": self.step},
        )

    def _rotate_priority(self) -> None:
        """把优先权交给下一位还没让过的玩家。"""
        if not self.players:
            return
        idx = self.players.index(self.priority_player) if self.priority_player in self.players else 0
        for offset in range(1, len(self.players) + 1):
            nxt = self.players[(idx + offset) % len(self.players)]
            if id(nxt) not in self._passed:
                self.priority_player = nxt
                return
        self.priority_player = self.active_player

    def _after_action(self) -> None:
        """任何动作之后：重算派生属性并执行状态检查。"""
        self.recalculate()
        run_until_stable(self)

    def _push_to_stack(self, item: StackItem) -> None:
        """新物件入栈：所有玩家重新获得优先权（APNAP：主动牌手先）。"""
        self.stack.push(item)
        self._passed.clear()
        self.priority_player = self.active_player or self.players[0]

    def _check_game_over(self) -> bool:
        losers = [p for p in self.players if p.has_lost]
        if losers:
            self.game_over = True
            alive = [p for p in self.players if not p.has_lost]
            self.winner = alive[0] if len(alive) == 1 else None
            self.log(f"=== 游戏结束：{[p.name for p in losers]} 输掉 ===")
            if self.winner:
                self.log(f"胜者：{self.winner.name}")
            return True
        return False

    # ================================================================ 阶段推进

    def _needs_turn_action(self) -> bool:
        key = f"{self.turn_number}:{self.phase.value}:{self.step.value if self.step else ''}"
        if key in self.turn_actions_done:
            return False
        return self.phase in (Phase.UNTAP, Phase.DRAW, Phase.CLEANUP) or (
            self.phase == Phase.COMBAT and self.step == Step.COMBAT_DAMAGE
        )

    def _do_turn_action(self) -> None:
        key = f"{self.turn_number}:{self.phase.value}:{self.step.value if self.step else ''}"
        self.turn_actions_done.add(key)

        if self.phase == Phase.UNTAP:
            self._do_untap()
        elif self.phase == Phase.DRAW:
            self._do_draw()
        elif self.phase == Phase.COMBAT and self.step == Step.COMBAT_DAMAGE:
            self._do_combat_damage()
        elif self.phase == Phase.CLEANUP:
            self._do_cleanup()

    def _do_untap(self) -> None:
        player = self.active_player
        assert player is not None
        for permanent in player.permanents:
            # 召唤失调判定依赖 entered_turn，重置在此只是解除横置
            permanent.untap()
        player.lands_played_this_turn = 0
        player.has_drawn_this_turn = False
        self.log(f"{player.name} 重置所有永久物")

    def _do_draw(self) -> None:
        player = self.active_player
        assert player is not None
        if self.turn_number > 1 or True:  # 先手第一回合也抓牌（简化）
            drawn = player.draw(1)
            if drawn:
                self.log(f"{player.name} 抓了一张牌")
            else:
                self.on_empty_library(player)
        player.has_drawn_this_turn = True

    def _do_combat_damage(self) -> None:
        """战斗伤害步骤。有先攻/连击时分两轮。"""
        if not self._first_strike_done and self.combat.has_first_strike_creatures():
            self.log("-- 先攻/连击伤害 --")
            self.combat.assign_damage(first_strike=True)
            self._after_action()
            if self.game_over:
                return
            self._first_strike_done = True
            # 先攻伤害后还有一轮优先权（简化为直接继续普通伤害）
            self.log("-- 普通战斗伤害 --")
            self.combat.assign_damage(first_strike=False)
        else:
            self.combat.assign_damage(first_strike=False)
        self._first_strike_done = False
        run_until_stable(self)

    def _do_cleanup(self) -> None:
        player = self.active_player
        assert player is not None

        # 弃到手牌上限
        excess = len(player.hand) - player.max_hand_size
        if excess > 0:
            discarded = player.discard_random(excess, self.rng)
            self.log(f"{player.name} 弃掉 {len(discarded)} 张牌（手牌上限）")

        # 清除伤害与直到回合结束的效应
        for p in self.players:
            for permanent in p.permanents:
                permanent.clear_damage()
                permanent.damaged_this_turn = False
        self.temporary.clear()
        self.replacement.clear_expired()
        self.turn_actions_done.clear()

        # 进入下一个回合
        self._begin_next_turn()

    def _begin_next_turn(self) -> None:
        idx = self.players.index(self.active_player) if self.active_player in self.players else 0
        self.active_player = self.players[(idx + 1) % len(self.players)]
        self.turn_number += 1
        self._phase_index = 0
        self.phase, self.step = PHASE_SEQUENCE[0]
        self.priority_player = self.active_player
        self._passed.clear()
        self._first_strike_done = False
        self._skip_to_main2 = False
        self.combat.end_of_combat()
        for player in self.players:
            player.empty_mana_pool()
            for permanent in player.permanents:
                permanent._triggered_this_turn = False  # type: ignore[attr-defined]
                permanent.trigger_counts.clear()
        self.log(f"\n--- 第 {self.turn_number} 回合：{self.active_player.name} ---")

    def _advance_phase(self) -> None:
        """双方让过且堆叠为空 → 推进到下一个阶段/步骤。"""
        self._passed.clear()
        self._first_strike_done = False

        if self._skip_to_main2 and self.phase == Phase.COMBAT and self.step == Step.DECLARE_ATTACKERS:
            # 没有攻击者：跳过整个战斗阶段
            self._skip_to_main2 = False
            self._phase_index = PHASE_SEQUENCE.index((Phase.MAIN_2, None))
            self.phase, self.step = PHASE_SEQUENCE[self._phase_index]
            self.priority_player = self.active_player
            return

        # 结束阶段清空法术力池
        if self.phase == Phase.MAIN_2:
            for player in self.players:
                player.empty_mana_pool()

        self._phase_index += 1
        if self._phase_index >= len(PHASE_SEQUENCE):
            self._phase_index = len(PHASE_SEQUENCE) - 1
            return
        self.phase, self.step = PHASE_SEQUENCE[self._phase_index]
        self.priority_player = self.active_player

        if self.phase == Phase.COMBAT and self.step == Step.DECLARE_ATTACKERS:
            self.combat.state.clear()

        self.log(f"[{self.phase.value}{'·' + self.step.value if self.step else ''}]")

    # ================================================================ 宣告

    def _maybe_declare_decision(self) -> Decision | None:
        """在宣告攻击者/阻挡者步骤返回决策请求。"""
        if self.phase != Phase.COMBAT or self.step is None:
            return None
        active = self.active_player
        assert active is not None
        defender = self.other_player(active)

        if self.step == Step.DECLARE_ATTACKERS:
            key = f"{self.turn_number}:declare_attackers"
            if key in self.turn_actions_done:
                return None
            self.turn_actions_done.add(key)
            legal = [c for c in active.creatures if self.combat.can_attack(c)[0]]
            if not legal:
                self._skip_to_main2 = True
                return None
            return Decision(
                kind="declare_attackers",
                player=active,
                prompt=f"{active.name}，选择攻击的生物",
                payload={"legal": legal, "defender": defender, "planeswalkers": defender.planeswalkers},
            )

        if self.step == Step.DECLARE_BLOCKERS:
            key = f"{self.turn_number}:declare_blockers"
            if key in self.turn_actions_done:
                return None
            self.turn_actions_done.add(key)
            attackers = self.combat.attacking_creatures()
            if not attackers:
                return None
            legal = [c for c in defender.creatures if not c.tapped]
            return Decision(
                kind="declare_blockers",
                player=defender,
                prompt=f"{defender.name}，选择阻挡方式",
                payload={"attackers": attackers, "legal": legal},
            )
        return None

    # ================================================================ 动作执行

    def _perform_action(self, action: Action) -> None:
        kind = action.kind
        self.last_failed_card = None
        if kind == "pass":
            self._pass_priority()
        elif kind == "play_land":
            if not self.play_land(action.card):
                self.last_failed_card = action.card
        elif kind == "cast":
            player = action.player_or_owner()
            if not self.cast_spell(player, action.card, action.targets, action.x_value):
                self.last_failed_card = action.card
        elif kind == "activate":
            if not self.activate_ability(action.permanent, action.ability_index, action.targets):
                self.last_failed_card = action.card
        elif kind == "attackers":
            self.combat.declare_attackers(action.payload.get("declarations", []))
        elif kind == "blockers":
            self.combat.declare_blockers(action.payload.get("assignments", {}))
        else:
            self.log(f"（未知动作：{kind}）")

    def _pass_priority(self) -> None:
        player = self.priority_player
        if player is None:
            return
        self._passed.add(id(player))
        # 阶段结束时清空法术力池（规则：阶段结束法术力池清空）
        # 简化处理：让过时保留（万智牌中法术力池在阶段/步骤结束时清空）
        if len(self._passed) >= len(self.players) and not self.stack:
            for p in self.players:
                p.empty_mana_pool()
        self._rotate_priority()

    # ---------------------------------------------------------------- 出地
    def play_land(self, card: Card | None) -> bool:
        if card is None:
            return False
        player = card.controller or card.owner
        if not player.can_play_land():
            self.log(f"{player.name} 本回合已经下过地了")
            return False
        if not card.data.is_land:
            self.log(f"{card.name} 不是地")
            return False
        if card.zone != Zone.HAND:
            return False

        player.hand.remove(card)
        player.lands_played_this_turn += 1
        self.put_permanent_onto_battlefield(card, player)
        self.events.emit(EventType.LAND_PLAYED, card, player)
        self.log(f"{player.name} 使用地：{card.name}")
        return True

    # ---------------------------------------------------------------- 施放咒语
    def can_cast(self, player: Player, card: Card) -> tuple[bool, str]:
        """判断当前时机下能否施放该咒语。"""
        if card.zone != Zone.HAND:
            return False, "不在手牌中"
        if card.data.is_land:
            return False, "地不是咒语"
        if not self._timing_allows(player, card):
            return False, "当前时机不允许施放（需要法术时机）"
        symbols = parse_mana_cost(card.data.mana_cost)
        if not could_pay(self, player, symbols):
            return False, "法术力不足"
        return True, ""

    def _timing_allows(self, player: Player, card: Card) -> bool:
        """时机合法性：瞬间/闪现可随时，其余需主阶段且堆叠为空。"""
        if card.data.is_instant:
            return True
        from .ability import Ability

        flash = any(
            ab.kind == "keyword" and ab.keyword == Keyword.FLASH for ab in getattr(card.data, "abilities", [])
        )
        if flash or card.data.has_keyword(Keyword.FLASH):
            return True
        if self.stack:
            return False
        if self.phase not in (Phase.MAIN_1, Phase.MAIN_2):
            return False
        return player is self.active_player

    def cast_spell(
        self,
        player: Player,
        card: Card | None,
        targets: list[Any] | None = None,
        x_value: int = 0,
    ) -> bool:
        """施放一个咒语：移出手中 → 支付费用 → 入堆叠 → 触发。"""
        if card is None:
            return False
        ok, reason = self.can_cast(player, card)
        if not ok:
            self.log(f"无法施放 {card.name}：{reason}")
            return False

        symbols = parse_mana_cost(card.data.mana_cost)
        # 自动横置地付费（池内法术力足够时不会多横置）
        auto_pay(self, player, symbols, x_value)
        plan = can_pay(symbols, player.mana_pool, x_value)
        if plan is None:
            self.log(f"法术力不足，无法施放 {card.name}")
            return False
        apply_payment(player.mana_pool, plan)

        # 移出手中，进入堆叠
        if card in player.hand:
            player.hand.remove(card)
        card.zone = Zone.STACK
        card.controller = player

        item = StackItem(
            name=card.name,
            controller=player,
            kind="spell",
            card=card,
            effects=list(self._spell_effects(card)),
            targets=list(targets or []),
            x_value=x_value,
            text=card.data.oracle_text,
        )
        self._push_to_stack(item)
        self.events.spell_cast(card, player)
        self.log(f"{player.name} 施放 {card.name}（{card.data.mana_string}）")
        return True

    def _spell_effects(self, card: Card) -> list[Any]:
        """取出该咒语的效应列表。"""
        from .effects import Effect

        effects: list[Effect] = []
        for ability in getattr(card.data, "abilities", []):
            if ability.kind == "spell" and ability.spell:
                effects.extend(ability.spell.effects)
        return effects

    # ---------------------------------------------------------------- 启动异能
    def activate_ability(
        self, permanent: Permanent | None, ability_index: int, targets: list[Any] | None = None
    ) -> bool:
        """启动一个启动式异能。"""
        if permanent is None:
            return False
        abilities = [
            ab for ab in permanent.abilities if ab.kind == "activated" and ab.activated is not None
        ]
        if ability_index >= len(abilities):
            return False
        ability = abilities[ability_index]
        activated = ability.activated
        assert activated is not None
        player = permanent.controller

        # 费用检查（法术力部分可自动横置地支付）
        if activated.cost.mana:
            auto_pay(self, player, activated.cost.mana)
        if not self._can_pay_cost(player, permanent, activated.cost):
            self.log(f"无法支付 {permanent.name} 的异能费用")
            return False
        self._pay_cost(player, permanent, activated.cost)

        if activated.is_mana_ability:
            # 法术力异能不使用堆叠，立即结算
            ctx = Context(game=self, source=permanent, controller=player, targets=list(targets or []))
            for effect in activated.effects:
                apply_effect(effect, ctx)
            return True

        item = StackItem(
            name=f"{permanent.name}（{activated.text or '异能'}）",
            controller=player,
            kind="ability",
            source=permanent,
            effects=list(activated.effects),
            targets=list(targets or []),
            text=activated.text,
        )
        self._push_to_stack(item)
        self.log(f"{player.name} 启动 {permanent.name} 的异能")
        return True

    def _can_pay_cost(self, player: Player, permanent: Permanent, cost: Any) -> bool:
        if cost.tap and permanent.tapped:
            return False
        if cost.tap and permanent.is_sick:
            return False
        if cost.mana and not can_pay(cost.mana, player.mana_pool):
            return False
        if cost.life and player.life < cost.life:
            return False
        return True

    def _pay_cost(self, player: Player, permanent: Permanent, cost: Any) -> None:
        if cost.tap:
            permanent.tap()
        if cost.mana:
            plan = can_pay(cost.mana, player.mana_pool)
            if plan:
                apply_payment(player.mana_pool, plan)
        if cost.life:
            player.lose_life(cost.life)
        if cost.sacrifice_self:
            self.sacrifice_permanent(permanent)

    # ---------------------------------------------------------------- 堆叠结算
    def _resolve_top(self) -> None:
        item = self.stack.pop()
        if item is None:
            return
        item.resolve(self)
        self._passed.clear()
        self.priority_player = self.active_player

    def finish_spell_resolution(self, card: Card) -> None:
        """咒语结算完成后的去向：永久物进场，其余进坟场。"""
        if card.data.is_permanent_type:
            self.put_permanent_onto_battlefield(card, card.controller or card.owner)
        else:
            self.put_card_into_graveyard(card)

    # ================================================================ 区域移动

    def put_permanent_onto_battlefield(self, card: Card, controller: Player | None = None) -> Permanent:
        """把一张牌放进战场，返回对应的永久物。"""
        controller = controller or card.controller or card.owner
        card.zone = Zone.BATTLEFIELD

        permanent = Permanent(card=card, game=self, controller=controller, owner=card.owner)
        permanent.timestamp = self.next_timestamp()
        permanent.entered_turn = self.turn_number

        card.permanent = permanent
        controller.battlefield.append(permanent)

        # 鹏洛客进场时带忠诚指示物
        if permanent.is_planeswalker:
            permanent.add_counter("loyalty", permanent.data.base_loyalty)
        # 战役进场带防御指示物
        if permanent.is_battle:
            permanent.add_counter("defense", _as_int(permanent.data.defense))
        # 进场时已横置
        if permanent.data.enters_tapped:
            permanent.tapped = True

        self.recalculate()
        self.log(f"{card.name} 进入战场（{controller.name} 操控）")
        self.events.permanent_entered(permanent)
        return permanent

    def put_permanent_into_graveyard(self, permanent: Permanent, reason: str = "") -> None:
        controller = permanent.controller
        if permanent in controller.battlefield:
            controller.battlefield.remove(permanent)
        self._detach(permanent)
        permanent.card.zone = Zone.GRAVEYARD
        permanent.card.permanent = None
        card_owner = permanent.card.owner
        card_owner.graveyard.append(permanent.card)
        self.events.permanent_left(permanent)
        if reason in ("", "destroy"):
            self.events.permanent_died(permanent)

    def destroy_permanent(self, permanent: Permanent, by_damage: bool = False) -> bool:
        """消灭一个永久物。具不灭者免疫一切消灭（含伤害致死）。"""
        if permanent.has_keyword(Keyword.INDESTRUCTIBLE):
            self.log(f"{permanent.name} 具不灭，无法被消灭")
            return False
        self.put_permanent_into_graveyard(permanent, reason="destroy")
        self.log(f"{permanent.name} 被消灭")
        return True

    def exile_permanent(self, permanent: Permanent) -> None:
        controller = permanent.controller
        if permanent in controller.battlefield:
            controller.battlefield.remove(permanent)
        self._detach(permanent)
        permanent.card.zone = Zone.EXILE
        permanent.card.permanent = None
        permanent.card.owner.exile.append(permanent.card)
        self.events.permanent_left(permanent)

    def return_to_hand(self, permanent: Permanent) -> None:
        controller = permanent.controller
        if permanent in controller.battlefield:
            controller.battlefield.remove(permanent)
        self._detach(permanent)
        card = permanent.card
        card.zone = Zone.HAND
        card.permanent = None
        card.owner.hand.append(card)
        self.events.permanent_left(permanent)

    def sacrifice_permanent(self, permanent: Permanent) -> None:
        self.put_permanent_into_graveyard(permanent, reason="sacrifice")
        self.log(f"{permanent.name} 被牺牲")

    def attach(self, source: Permanent, target: Permanent) -> None:
        """把灵气/武具结附到目标上。"""
        if source.attached_to is not None:
            host = source.attached_to
            if source in host.attachments:
                host.attachments.remove(source)
        source.attached_to = target
        if source not in target.attachments:
            target.attachments.append(source)
        self.recalculate()
        self.log(f"{source.name} 结附到 {target.name}")

    def change_control(self, permanent: Permanent, new_controller: Player) -> None:
        old = permanent.controller
        if permanent in old.battlefield:
            old.battlefield.remove(permanent)
        permanent.controller = new_controller
        permanent.card.controller = new_controller
        new_controller.battlefield.append(permanent)

    def _detach(self, permanent: Permanent) -> None:
        """把该永久物与其他物件的结附关系解除。"""
        if permanent.attached_to is not None:
            host = permanent.attached_to
            if permanent in host.attachments:
                host.attachments.remove(permanent)
            permanent.attached_to = None
        for attached in list(permanent.attachments):
            # 结附物随之进坟场（由 SBA 处理）
            attached.attached_to = None
        permanent.attachments.clear()

    def put_card_into_graveyard(self, card: Card) -> None:
        if card.zone == Zone.GRAVEYARD:
            return
        for zone_name in ("hand", "library", "exile"):
            zone = getattr(card.owner, zone_name, None)
            if zone is not None and card in zone:
                zone.remove(card)
                break
        card.zone = Zone.GRAVEYARD
        card.owner.graveyard.append(card)

    # ---------------------------------------------------------------- 衍生物
    def create_tokens(self, controller: Player, spec: TokenSpec, count: int = 1, tapped: bool = False) -> list[Permanent]:
        created: list[Permanent] = []
        for _ in range(count):
            data = spec.to_card_data()
            card = Card(data=data, owner=controller, zone=Zone.BATTLEFIELD)
            permanent = Permanent(card=card, game=self, controller=controller, owner=controller)
            permanent.is_token = True
            permanent._token_data = data
            permanent.timestamp = self.next_timestamp()
            permanent.entered_turn = self.turn_number
            permanent.tapped = tapped
            card.permanent = permanent
            controller.battlefield.append(permanent)
            if permanent.is_creature:
                pass
            self.events.permanent_entered(permanent)
            created.append(permanent)
        self.recalculate()
        return created

    # ---------------------------------------------------------------- 其他
    def on_empty_library(self, player: Player) -> None:
        """抓空牌库：该牌手输掉游戏（简化处理）。"""
        if not player.has_lost:
            self.log(f"{player.name} 试图从空牌库抓牌，输掉游戏")
            player.has_lost = True

    def add_until_end_of_turn(self, permanent: Permanent, power: int, toughness: int) -> None:
        self.temporary.add_buff(permanent, power, toughness)

    def grant_keyword_until_end_of_turn(self, permanent: Permanent, keyword: Keyword) -> None:
        self.temporary.add_keyword(permanent, keyword)

    def scry_decision(self, player: Player, cards: list[Card]) -> list[Card]:
        """占卜：返回要放到牌库底的牌。默认：法术力曲线过高的放到底。"""
        agent = self.agents.get(id(player))
        if agent is not None and hasattr(agent, "scry_decision"):
            return agent.scry_decision(self, player, cards)
        return []

    def surveil_decision(self, player: Player, cards: list[Card]) -> list[Card]:
        """侦察：返回要放入坟场的牌。默认：全留下（不放坟场）。"""
        agent = self.agents.get(id(player))
        if agent is not None and hasattr(agent, "surveil_decision"):
            return agent.surveil_decision(self, player, cards)
        return []

    # ---------------------------------------------------------------- 快照
    def snapshot(self) -> dict[str, Any]:
        """给 UI 用的状态快照。"""
        return {
            "turn": self.turn_number,
            "phase": self.phase.value if self.phase else "",
            "step": self.step.value if self.step else "",
            "active": self.active_player.name if self.active_player else "",
            "priority": self.priority_player.name if self.priority_player else "",
            "stack": [item.describe() for item in reversed(self.stack.items)],
            "players": [
                {
                    "name": p.name,
                    "life": p.life,
                    "hand": len(p.hand),
                    "library": len(p.library),
                    "graveyard": len(p.graveyard),
                    "creatures": len(p.creatures),
                    "lands": len(p.lands),
                    "mana": str(p.mana_pool),
                }
                for p in self.players
            ],
        }


def _as_int(value: str | int | float | None) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    import re

    match = re.search(r"-?\d+", str(value))
    return int(match.group()) if match else 0
