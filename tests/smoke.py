"""冒烟测试：加载卡池 → 解析异能 → 检查覆盖率 → 抽查单卡解析结果。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mtg.cards.carddb import CardDB  # noqa: E402
from mtg.cards.oracle_parser import report_unparsed  # noqa: E402


def main() -> int:
    print("正在加载标准赛制卡池…")
    db = CardDB.load()
    print()
    print(db.summary())
    print()

    # 抽查几张有代表性的牌，确认解析结果合理
    samples = [
        "Llanowar Elves",
        "Shock",
        "Grizzly Bears",
        "Forest",
        "Island",
    ]
    print("—— 单卡解析抽查 ——")
    for name in samples:
        card = db.get(name)
        if card is None:
            print(f"  {name}: （卡池中没有）")
            continue
        print(f"  {card.name} [{card.mana_string}] {card.type_line} {card.power or ''}/{card.toughness or ''}")
        for ability in card.abilities:
            print(f"      · {ability.kind}: {ability.describe()}")
        if card.unparsed:
            print(f"      ! 未解析: {card.unparsed}")
        print()

    print(report_unparsed(db.cards, limit=20))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
