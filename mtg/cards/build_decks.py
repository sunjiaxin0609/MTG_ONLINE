"""生成预构筑标准套牌，存到 decks/ 供界面直接选用。

    python -m mtg.cards.build_decks
"""
from __future__ import annotations

import os
import sys

from ..engine.card import CardData
from .carddb import CardDB
from .decks import DECKS_DIR, auto_build, deck_summary, save_deck

#: 预置套牌：(文件名, 显示名, 颜色)
PRESETS: list[tuple[str, str, list[str]]] = [
    ("mono_white", "纯白快攻", ["W"]),
    ("mono_blue", "纯蓝控制", ["U"]),
    ("mono_black", "纯黑中速", ["B"]),
    ("mono_red", "纯红烧", ["R"]),
    ("mono_green", "纯绿猛袭", ["G"]),
    ("selesnya", "白绿铺场", ["G", "W"]),
    ("dimir", "蓝黑控制", ["U", "B"]),
    ("boros", "红白快攻", ["R", "W"]),
    ("golgari", "黑绿中速", ["B", "G"]),
    ("izzet", "红蓝法术", ["U", "R"]),
    ("gruul", "红绿猛袭", ["R", "G"]),
    ("orzhov", "黑白中速", ["W", "B"]),
]


def build_all(seed: int = 20240) -> dict[str, list[CardData]]:
    """为每种配色生成一套牌。"""
    db = CardDB.load()
    results: dict[str, list[CardData]] = {}
    os.makedirs(DECKS_DIR, exist_ok=True)

    for idx, (filename, display, colors) in enumerate(PRESETS):
        deck = auto_build(db.cards, colors, size=60, seed=seed + idx * 137)
        path = os.path.join(DECKS_DIR, f"{filename}.json")
        save_deck(path, display, deck)
        results[filename] = deck
        unsupported = [c.name for c in deck if not c.supported]
        print(f"✓ {display:<8} {'-'.join(colors):<5} {len(deck)} 张  -> {os.path.basename(path)}")
        if unsupported:
            print(f"    含 {len(unsupported)} 张引擎未完全支持的牌：{unsupported[:4]}")
    return results


def main() -> int:
    print("生成预构筑标准套牌…\n")
    build_all()
    print(f"\n完成，保存在 {DECKS_DIR}")
    print("提示：这些套牌是从真实标准卡池自动组出的，可以直接改 decks/*.json 调整。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
