"""局面评估与单卡价值打分。

AI 的所有决策最终都归结为"这么做之后局面分变化了多少"，
因此评估函数的质量直接决定 AI 的强弱。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..engine.types import Keyword

if TYPE_CHECKING:
    from ..engine.card import CardData, Permanent
    from ..engine.game import Game
    from ..engine.player import Player


# -------------------------------------------------------------------- 关键字权重

KEYWORD_SCORE: dict[Keyword, float] = {
    Keyword.FLYING: 1.6,
    Keyword.FIRST_STRIKE: 1.2,
    Keyword.DOUBLE_STRIKE: 2.6,
    Keyword.DEATHTOUCH: 1.8,
    Keyword.LIFELINK: 1.3,
    Keyword.TRAMPLE: 1.2,
    Keyword.VIGILANCE: 1.0,
    Keyword.HASTE: 0.8,
    Keyword.MENACE: 0.9,
    Keyword.HEXPROOF: 1.5,
    Keyword.INDESTRUCTIBLE: 2.2,
    Keyword.REACH: 0.5,
    Keyword.PROWESS: 0.6,
    Keyword.FLASH: 0.5,
    Keyword.DEFENDER: -0.8,
}


def creature_value(permanent: "Permanent") -> float:
    """一个生物值多少分（大致等价于"值几张牌"）。"""
    power = permanent.power()
    toughness = permanent.toughness()
    if toughness <= 0:
        return 0.0

    # 攻防本身：力量和防御力权重不同，防御力更能"站住"
    score = power * 0.9 + toughness * 0.7

    for keyword, weight in KEYWORD_SCORE.items():
        if permanent.has_keyword(keyword):
            score += weight

    # 异能越多越强
    score += 0.35 * len([a for a in permanent.abilities if a.kind in ("triggered", "activated")])

    # 已横置的生物价值略降
    if permanent.tapped:
        score -= 0.2

    return score


def card_value(data: "CardData") -> float:
    """一张手牌的潜在价值（用于决定先出哪张）。"""
    if data.is_land:
        return 0.1
    if data.is_creature:
        power = data.base_power
        toughness = data.base_toughness
        score = power * 0.9 + toughness * 0.7
        for raw in data.keywords:
            lowered = raw.lower()
            for keyword, weight in KEYWORD_SCORE.items():
                if keyword.value.lower() in lowered:
                    score += weight
        score += 0.35 * len([a for a in data.abilities if a.kind in ("triggered", "activated")])
        # 法术力效率：同样费用里更强的更好
        return score / max(data.cmc, 0.5) * 1.4
    # 瞬间/法术/结界/神器：按费用粗估，去除类额外加分
    base = 1.2 + data.cmc * 0.5
    text = (data.oracle_text or "").lower()
    if "destroy target" in text or "exile target" in text:
        base += 1.5
    if "counter target" in text:
        base += 1.4
    if "draw" in text:
        base += 0.9
    if "deals" in text and "damage" in text:
        base += 0.6
    return base


def board_score(player: "Player") -> float:
    """一位牌手的场面分。"""
    score = 0.0
    for permanent in player.permanents:
        if permanent.is_creature:
            score += creature_value(permanent)
        elif permanent.is_planeswalker:
            score += permanent.loyalty * 0.8
        elif permanent.is_land:
            score += 0.15
        else:
            score += 0.5
    # 手牌是资源
    score += len(player.hand) * 1.1
    return score


def position_score(game: "Game", player: "Player") -> float:
    """从 player 视角评估局面分差（越大越好）。"""
    opponent = game.other_player(player)
    my_score = board_score(player) + player.life * 0.35
    opp_score = board_score(opponent) + opponent.life * 0.35
    return my_score - opp_score


# -------------------------------------------------------------------- 威胁评估

def threat_score(permanent: "Permanent") -> float:
    """一个生物有多"烦人"（用于决定去除目标）。"""
    if not permanent.is_creature:
        return 0.0
    score = creature_value(permanent)
    # 能一击造成大量伤害的更该先解掉
    score += permanent.power() * 0.4
    # 具系命/死触的更难缠
    if permanent.has_keyword(Keyword.LIFELINK):
        score += 0.8
    if permanent.has_keyword(Keyword.DEATHTOUCH):
        score += 0.8
    return score


def is_removal(data: "CardData") -> bool:
    text = (data.oracle_text or "").lower()
    return (
        "destroy target" in text
        or "exile target" in text
        or ("deals" in text and "damage" in text and "target" in text)
    )


def is_counter(data: "CardData") -> bool:
    return "counter target" in (data.oracle_text or "").lower()
