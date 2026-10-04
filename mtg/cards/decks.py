"""套牌构建与序列化。

既能从卡池自动拼一套曲线合理的套牌（用于快速开局/测试），
也能读写 JSON 套牌文件（用于保存你组好的牌）。
"""
from __future__ import annotations

import json
import os
from typing import Iterable

from ..engine.card import CardData

DECKS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "decks")


# -------------------------------------------------------------------- 自动组牌

def auto_build(
    cards: list[CardData],
    colors: Iterable[str] = (),
    size: int = 60,
    land_count: int = 24,
    seed: int | None = None,
) -> list[CardData]:
    """从卡池里自动拼一套曲线合理的套牌。

    策略：按颜色身份筛选 → 按法术力曲线分配名额 → 优先挑引擎完整支持的卡。
    """
    import random

    rng = random.Random(seed)
    wanted = {c.upper() for c in colors}

    def color_ok(card: CardData) -> bool:
        if not wanted:
            return True
        identity = {c for c in card.color_identity if c in "WUBRG"}
        return bool(identity) and identity <= wanted

    # 1) 非地牌池
    pool = [
        c
        for c in cards
        if not c.is_land
        and color_ok(c)
        and c.supported
        and c.layout == "normal"
        and not c.is_battle
    ]
    # 2) 地牌池
    land_pool = [c for c in cards if c.is_land and color_ok(c) and c.layout == "normal"]

    # 曲线名额：低费铺场、中费主力、高费收尾
    slots = {1: 8, 2: 10, 3: 9, 4: 6, 5: 3}
    deck: list[CardData] = []

    for cmc, count in sorted(slots.items()):
        bucket = [c for c in pool if int(c.cmc) == cmc]
        rng.shuffle(bucket)
        # 生物优先，保证能打
        bucket.sort(key=lambda c: (not c.is_creature, -_simple_score(c)))
        picked = bucket[:count]
        deck.extend(picked)
        for card in picked:
            pool.remove(card)

    # 3) 补地：优先基本地，避开传奇地，多种地轮流取用
    if wanted:
        per_color = max(land_count // len(wanted), 1)
        for color in sorted(wanted):
            candidates = [
                c
                for c in land_pool
                if color in (c.basic_land_colors or c.produced_mana or []) and not c.is_legendary
            ]
            basics = [c for c in candidates if c.is_basic_land]
            source = basics or candidates
            if not source:
                continue
            for i in range(per_color):
                deck.append(source[i % len(source)])
    else:
        deck.extend(land_pool[:land_count])

    # 4) 补齐张数
    while len(deck) < size and pool:
        deck.append(pool.pop(0))
    while len(deck) < size and land_pool:
        deck.append(land_pool[0])

    # 5) 同一种牌最多 4 张（构筑赛制限制）
    return enforce_max_four(deck[:size])


def enforce_max_four(deck: list[CardData], limit: int = 4) -> list[CardData]:
    """把同名牌压到最多 limit 张，缺的用基本地补齐。"""
    counts: dict[str, int] = {}
    out: list[CardData] = []
    overflow: list[CardData] = []

    for card in deck:
        if card.is_basic_land:
            out.append(card)
            continue
        seen = counts.get(card.name, 0)
        if seen < limit:
            counts[card.name] = seen + 1
            out.append(card)
        else:
            overflow.append(card)

    # 多出来的名额用基本地补
    basics = [c for c in deck if c.is_basic_land]
    for card in overflow:
        if basics:
            out.append(basics[len(out) % len(basics)])
        else:
            out.append(card)
    return out


def _simple_score(card: CardData) -> float:
    if card.is_creature:
        return card.base_power * 0.9 + card.base_toughness * 0.7 + len(card.keywords) * 0.5
    text = (card.oracle_text or "").lower()
    score = 1.0 + card.cmc * 0.3
    if "destroy target" in text or "exile target" in text:
        score += 1.2
    if "draw" in text:
        score += 0.8
    return score


# -------------------------------------------------------------------- 套牌文件

def save_deck(path: str, name: str, cards: Iterable[CardData]) -> str:
    """把套牌存成 JSON（按名字计数）。"""
    counts: dict[str, int] = {}
    for card in cards:
        counts[card.name] = counts.get(card.name, 0) + 1
    payload = {
        "name": name,
        "format": "standard",
        "cards": [{"name": n, "count": c} for n, c in sorted(counts.items())],
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return path


def load_deck(path: str, db) -> list[CardData]:  # noqa: ANN001
    """从 JSON 套牌文件载入。找不到的牌会被跳过并提示。"""
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)

    out: list[CardData] = []
    missing: list[str] = []
    for entry in payload.get("cards", []):
        name = entry["name"] if isinstance(entry, dict) else entry
        count = entry.get("count", 1) if isinstance(entry, dict) else 1
        data = db.get(name)
        if data is None:
            missing.append(name)
            continue
        out.extend([data] * count)
    if missing:
        print(f"警告：套牌中有 {len(missing)} 张牌不在标准卡池里，已跳过：{missing[:8]}")
    return out


def deck_summary(cards: Iterable[CardData]) -> str:
    """输出套牌概况（曲线分布与构成）。"""
    cards = list(cards)
    counts: dict[str, int] = {}
    for card in cards:
        counts[card.name] = counts.get(card.name, 0) + 1

    curve: dict[int, int] = {}
    lands = 0
    creatures = 0
    others = 0
    for card in cards:
        if card.is_land:
            lands += 1
            continue
        bucket = min(int(card.cmc), 7)
        curve[bucket] = curve.get(bucket, 0) + 1
        if card.is_creature:
            creatures += 1
        else:
            others += 1

    lines = [
        f"总张数：{len(cards)}（地 {lands} / 生物 {creatures} / 其他 {others}）",
        "法术力曲线：",
    ]
    for cmc in sorted(curve):
        lines.append(f"  {cmc} 费: {'█' * curve[cmc]} ({curve[cmc]})")
    return "\n".join(lines)
