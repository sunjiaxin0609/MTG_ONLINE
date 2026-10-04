"""中等强度启发式 AI 牌手。

会做的事：
  * 按法术力曲线出牌，优先铺场而不是憋大牌
  * 计算战斗交换，只在划算时攻击/阻挡
  * 识别威胁并选择合适的去除目标
  * 手上握着瞬间时会留费响应，而不是把法术力一次花光
  * 起手牌地太多/太少时调度

不会做的事：深层的多回合博弈搜索、对手手牌推理。
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..engine.effects import Effect
from ..engine.game import Action
from ..engine.mana import ManaSymbol, can_pay, parse_mana_cost
from ..engine.types import Phase, Zone
from .combat_solver import choose_attackers, choose_blockers
from .evaluate import card_value, is_counter, is_removal, threat_score

if TYPE_CHECKING:
    from ..engine.card import Card, Permanent
    from ..engine.game import Game
    from ..engine.player import Player


class HeuristicAgent:
    """启发式 AI 牌手。"""

    def __init__(self, name: str = "AI", aggression: float = 0.55) -> None:
        self.name = name
        #: 进攻性：越高越倾向全攻、越低越倾向保守
        self.aggression = aggression
        #: 本回合施放失败的牌（ID 集合），避免反复尝试打不出的牌形成死循环
        self._failed_cards: set[int] = set()
        self._failed_turn: int = -1

    # ================================================================ 主决策

    def take_action(self, game: "Game", player: "Player") -> Action:
        """拥有优先权时决定做什么。"""
        # 新回合清空失败记录；记住上一次失败的牌
        if game.turn_number != self._failed_turn:
            self._failed_turn = game.turn_number
            self._failed_cards.clear()
        failed = getattr(game, "last_failed_card", None)
        if failed is not None:
            self._failed_cards.add(id(failed))
            game.last_failed_card = None

        # 1) 出地
        land_action = self._try_play_land(game, player)
        if land_action is not None:
            return land_action

        # 2) 施放咒语
        spell_action = self._try_cast(game, player)
        if spell_action is not None:
            return spell_action

        # 3) 启动异能
        ability_action = self._try_activate(game, player)
        if ability_action is not None:
            return ability_action

        return Action(kind="pass")

    # ---------------------------------------------------------------- 出地
    def _try_play_land(self, game: "Game", player: "Player") -> Action | None:
        if not player.can_play_land():
            return None
        lands = [c for c in player.hand if c.data.is_land and id(c) not in self._failed_cards]
        if not lands:
            return None
        # 优先打出当前最缺颜色的地
        land = self._pick_land(game, player, lands)
        return Action(kind="play_land", card=land)

    def _pick_land(self, game: "Game", player: "Player", lands: list["Card"]) -> "Card":
        """挑选要打出的地：优先能补上缺口颜色的。"""
        need = self._missing_colors(player)
        best = None
        best_score = -1
        for card in lands:
            colors = card.data.basic_land_colors or self._produced_colors(card.data)
            score = len([c for c in colors if c in need])
            if card.data.enters_tapped:
                score -= 0.5
            if score > best_score:
                best_score = score
                best = card
        return best or lands[0]

    def _missing_colors(self, player: "Player") -> set[str]:
        """手牌里最需要、但场上还产不出的颜色。"""
        needed: dict[str, int] = {}
        for card in player.hand:
            if card.data.is_land:
                continue
            for symbol in re.findall(r"\{([WUBRG])\}", card.data.mana_cost or ""):
                needed[symbol] = needed.get(symbol, 0) + 1
        produced: set[str] = set()
        for permanent in player.lands:
            produced.update(self._produced_colors(permanent.data))
        return {c for c, n in needed.items() if c not in produced}

    @staticmethod
    def _produced_colors(data: Any) -> list[str]:
        """一张牌能产出哪些颜色的法术力。"""
        colors: list[str] = []
        if getattr(data, "produced_mana", None):
            colors.extend(data.produced_mana)
        if getattr(data, "basic_land_colors", None):
            colors.extend(data.basic_land_colors)
        for ability in getattr(data, "abilities", []) or []:
            if ability.kind == "activated" and ability.activated and ability.activated.is_mana_ability:
                for effect in ability.activated.effects:
                    if effect.kind == "mana":
                        if effect.param == "any":
                            return ["W", "U", "B", "R", "G"]
                        if effect.param in ("W", "U", "B", "R", "G"):
                            colors.append(effect.param)
        return list(dict.fromkeys(colors))

    # ---------------------------------------------------------------- 施放
    def _try_cast(self, game: "Game", player: "Player") -> Action | None:
        castable = []
        for card in player.hand:
            if card.data.is_land or id(card) in self._failed_cards:
                continue
            ok, _reason = game.can_cast(player, card)
            if ok:
                castable.append(card)
        if not castable:
            return None

        # 手上留着瞬间/反击时，不要把法术力花光
        hold = self._should_hold_mana(game, player)
        if hold:
            # 只在费用很低时才继续出牌
            cheap = [c for c in castable if c.data.cmc <= max(0, self._available_mana_count(game, player) - hold)]
            if cheap:
                castable = cheap
            else:
                return None

        opponent = game.other_player(player)
        threats = sorted(opponent.creatures, key=lambda c: -threat_score(c))

        best: tuple[float, "Card", list[Any]] | None = None
        for card in castable:
            score = self._cast_score(game, player, card, threats)
            if score <= 0:
                continue
            if best is None or score > best[0]:
                targets = self.choose_targets(game, player, card)
                best = (score, card, targets)

        if best is None:
            return None

        _score, card, targets = best
        return Action(kind="cast", card=card, targets=targets)

    def _cast_score(self, game: "Game", player: "Player", card: "Card", threats: list["Permanent"]) -> float:
        """给"现在施放这张牌"打分。"""
        data = card.data
        score = card_value(data)

        # 去除类：有威胁才打，没威胁就浪费
        if is_removal(data):
            if not threats:
                return 0.0
            score += threat_score(threats[0]) * 0.6
            # 别把宝贵的去除浪费在小角色上
            if threat_score(threats[0]) < 2.0:
                score -= 1.0
        # 反击：堆叠上没东西就别打
        if is_counter(data) and not game.stack:
            return 0.0
        # 战斗前铺场优先于非紧急的法术
        if data.is_creature:
            score += 0.8
        # 法术力效率高 = 更值得现在出
        score -= data.cmc * 0.12
        return score

    def _should_hold_mana(self, game: "Game", player: "Player") -> int:
        """是否要留费响应。返回需要保留的法术力数量。"""
        if game.phase not in (Phase.MAIN_1, Phase.MAIN_2):
            return 0
        opponent = game.other_player(player)
        # 对手场面有威胁，且我手上有瞬间 → 留费
        has_instant = any(
            c.data.is_instant
            or any(getattr(a, "keyword", None) and a.keyword.value == "Flash" for a in c.data.abilities)
            for c in player.hand
        )
        if not has_instant:
            return 0
        if not opponent.creatures and not game.stack:
            return 0
        # 保留能施放最便宜的那张瞬间的费用
        costs = [
            c.data.cmc
            for c in player.hand
            if c.data.is_instant or any(a.keyword and a.keyword.value == "Flash" for a in c.data.abilities)
        ]
        return int(min(costs)) if costs else 0

    def _available_mana_count(self, game: "Game", player: "Player") -> int:
        """估算当前可用法术力总量（含未横置的地）。"""
        total = player.mana_pool.total()
        for permanent in player.untapped_lands:
            if self._produced_colors(permanent.data):
                total += 1
        return total

    # ---------------------------------------------------------------- 启动异能
    def _try_activate(self, game: "Game", player: "Player") -> Action | None:
        """启动异能（主要是法术力异能，用于凑费用）。"""
        # 法术力异能只在需要付费时启动，且由施放流程按需调用
        return None

    # ---------------------------------------------------------------- 战斗
    def declare_attackers(self, game: "Game", player: "Player", legal: list["Permanent"], **_: Any) -> list[tuple]:
        attackers = choose_attackers(game, player)
        if not attackers:
            return []
        # 进攻性低的 AI 会留下一部分生物守家
        if self.aggression < 0.5 and len(attackers) > 2:
            keep = 1
            attackers = attackers[:-keep] if len(attackers) > keep else attackers
        return attackers

    def declare_blockers(
        self, game: "Game", player: "Player", attackers: list["Permanent"], legal: list["Permanent"], **_: Any
    ) -> dict:
        return choose_blockers(game, player, attackers)

    # ---------------------------------------------------------------- 目标选择
    def choose_targets(self, game: "Game", player: "Player", card: "Card") -> list[Any]:
        """为咒语选择目标。"""
        opponent = game.other_player(player)
        specs: list[Any] = []
        for ability in getattr(card.data, "abilities", []) or []:
            if ability.kind == "spell" and ability.spell:
                specs.extend(ability.spell.targets)
        if not specs:
            return []

        chosen: list[Any] = []
        for spec in specs:
            chosen.append(self._pick_target(game, player, spec, opponent))
        return [t for t in chosen if t is not None]

    def _pick_target(self, game: "Game", player: "Player", spec: Any, opponent: "Player") -> Any:
        kind = getattr(spec, "kind", "any")

        if kind == "player":
            return opponent
        if kind in ("creature", "creature_or_planeswalker", "permanent", "any", "creature_or_player"):
            # 优先消灭对手威胁最大的生物
            candidates = [c for c in opponent.permanents if self._matches_kind(c, kind)]
            if candidates:
                return max(candidates, key=threat_score)
            # 没有合适目标就打脸或打鹏洛客
            if kind in ("any", "creature_or_player"):
                if opponent.planeswalkers:
                    return opponent.planeswalkers[0]
                return opponent
            return None
        if kind == "artifact":
            artifacts = [p for p in opponent.permanents if p.is_artifact]
            return artifacts[0] if artifacts else None
        if kind == "enchantment":
            enchantments = [p for p in opponent.permanents if p.is_enchantment]
            return enchantments[0] if enchantments else None
        if kind == "spell":
            return game.stack.top
        return None

    @staticmethod
    def _matches_kind(permanent: "Permanent", kind: str) -> bool:
        if kind == "creature":
            return permanent.is_creature
        if kind == "permanent":
            return True
        if kind == "creature_or_planeswalker":
            return permanent.is_creature or permanent.is_planeswalker
        if kind in ("any", "creature_or_player"):
            return permanent.is_creature or permanent.is_planeswalker
        return True

    # ---------------------------------------------------------------- 其他决策
    def mulligan(self, game: "Game", player: "Player") -> bool:
        """起手是否调度。"""
        lands = sum(1 for c in player.hand if c.data.is_land)
        if lands < 2 or lands > 5:
            return True
        return False

    def scry_decision(self, game: "Game", player: "Player", cards: list["Card"]) -> list["Card"]:
        """占卜：把不需要的放到牌库底。"""
        lands_in_hand = sum(1 for c in player.hand if c.data.is_land)
        bottom = []
        for card in cards:
            if card.data.is_land and lands_in_hand >= 4:
                bottom.append(card)
            elif card.data.cmc >= 6 and len(player.lands) < 4:
                bottom.append(card)
        return bottom

    def surveil_decision(self, game: "Game", player: "Player", cards: list["Card"]) -> list["Card"]:
        """侦察：把不需要的放坟场。"""
        lands_in_hand = sum(1 for c in player.hand if c.data.is_land)
        to_grave = []
        for card in cards:
            if card.data.is_land and lands_in_hand >= 4:
                to_grave.append(card)
        return to_grave


# -------------------------------------------------------------------- 法术力支付

def pay_mana_cost(game: "Game", player: "Player", symbols: list[ManaSymbol]) -> bool:
    """尝试横置地来支付费用。成功返回 True。"""
    if not symbols:
        return True
    if can_pay(symbols, player.mana_pool):
        return True

    # 收集可用的法术力源
    sources: list[tuple["Permanent", list[str], int]] = []
    for permanent in player.permanents:
        if permanent.tapped or permanent.is_sick:
            continue
        for idx, ability in enumerate(_activated_abilities(permanent)):
            if not ability.is_mana_ability:
                continue
            colors = _ability_colors(ability)
            if colors:
                sources.append((permanent, colors, idx))
                break

    if not sources:
        return False

    # 需要补充的颜色
    need: dict[str, int] = {}
    generic = 0
    for symbol in symbols:
        if symbol.kind == "generic":
            generic += symbol.amount
        elif symbol.kind == "x":
            pass
        elif symbol.kind == "colored":
            need[symbol.options[0]] = need.get(symbol.options[0], 0) + 1
        elif symbol.kind == "hybrid":
            # 混血：任选其一，先用已有法术力抵扣
            if any(c in player.mana_pool.counts and player.mana_pool.counts[c] > 0 for c in symbol.options):
                continue
            need[symbol.options[0]] = need.get(symbol.options[0], 0) + 1
        elif symbol.kind == "colorless":
            generic += 1

    # 已有法术力先抵扣
    for color in list(need):
        have = player.mana_pool.counts.get(color, 0)
        if have:
            used = min(have, need[color])
            need[color] -= used
            if need[color] == 0:
                del need[color]

    # 递归挑选要横置的地
    plan = _search_taps(need, generic, sources, [])
    if plan is None:
        return False

    for permanent, color, idx in plan:
        _activate_mana_ability(game, permanent, idx, color)
    return can_pay(symbols, player.mana_pool)


def _activated_abilities(permanent: "Permanent") -> list[Any]:
    return [a.activated for a in permanent.abilities if a.kind == "activated" and a.activated is not None]


def _ability_colors(ability: Any) -> list[str]:
    colors: list[str] = []
    for effect in ability.effects:
        if effect.kind == "mana":
            if effect.param == "any":
                return ["W", "U", "B", "R", "G"]
            if effect.param in ("W", "U", "B", "R", "G"):
                colors.append(effect.param)
            elif effect.param == "C":
                colors.append("C")
    return colors


def _activate_mana_ability(game: "Game", permanent: "Permanent", ability_index: int, color: str) -> None:
    """启动一个法术力异能。找到能产出指定颜色的那个。"""
    abilities = _activated_abilities(permanent)
    for idx, ability in enumerate(abilities):
        if not ability.is_mana_ability:
            continue
        colors = _ability_colors(ability)
        if color in colors or "any" in str(colors):
            game.activate_ability(permanent, idx, [])
            return
    # 退而求其次：任意产费
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
    if depth > 12:
        return None

    if not need and generic <= 0:
        return list(chosen)

    used_permanents = {id(c[0]) for c in chosen}

    # 先满足颜色需求
    for color, count in need.items():
        if count <= 0:
            continue
        for permanent, colors, idx in sources:
            if id(permanent) in used_permanents or color not in colors:
                continue
            new_need = dict(need)
            new_need[color] -= 1
            if new_need[color] == 0:
                del new_need[color]
            result = _search_taps(new_need, generic, sources, chosen + [(permanent, color, idx)], depth + 1)
            if result is not None:
                return result
        return None

    # 再满足通用法术力
    for permanent, colors, idx in sources:
        if id(permanent) in used_permanents:
            continue
        pick = colors[0] if colors else "C"
        result = _search_taps({}, generic - 1, sources, chosen + [(permanent, pick, idx)], depth + 1)
        if result is not None:
            return result
    return None
