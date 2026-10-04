"""批量对局测试：检验引擎稳定性与先后手平衡。"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mtg.ai.agent import HeuristicAgent  # noqa: E402
from mtg.ai.runner import GameRunner  # noqa: E402
from mtg.cards.carddb import CardDB  # noqa: E402
from mtg.cards.decks import auto_build  # noqa: E402
from mtg.engine.game import Game  # noqa: E402
from mtg.engine.player import Player  # noqa: E402


def run_one(db, colors_a, colors_b, seed, first_is_a: bool, max_turns: int = 150):
    deck_a = auto_build(db.cards, colors_a, size=60, seed=seed)
    deck_b = auto_build(db.cards, colors_b, size=60, seed=seed + 1000)

    p1 = Player(name="A")
    p2 = Player(name="B")
    game = Game(players=[p1, p2], seed=seed)
    p1.load_deck(deck_a)
    p2.load_deck(deck_b)

    agents = {
        id(p1): HeuristicAgent(aggression=0.6),
        id(p2): HeuristicAgent(aggression=0.6),
    }
    game.agents = agents
    game.setup()
    game.start(first_player=p1 if first_is_a else p2)

    runner = GameRunner(game, agents=agents, max_turns=max_turns)
    winner = runner.run()
    return winner, game.turn_number, runner.decision_count


def main() -> int:
    db = CardDB.load()
    print(f"卡池加载完成：{db.stats.get('total')} 张\n")

    n = 20
    stats = {"A": 0, "B": 0, "draw": 0}
    turns_total = 0
    start = time.time()

    for i in range(n):
        first_is_a = i % 2 == 0
        try:
            winner, turns, decisions = run_one(db, ("G",), ("R",), seed=i * 13 + 1, first_is_a=first_is_a)
        except Exception as exc:  # noqa: BLE001
            print(f"  第 {i + 1} 局出错: {type(exc).__name__}: {exc}")
            import traceback

            traceback.print_exc()
            continue
        name = winner.name if winner else "draw"
        stats[name] = stats.get(name, 0) + 1
        turns_total += turns
        print(f"  第 {i + 1:>2} 局: 先手={'A' if first_is_a else 'B'} 胜者={name} 回合={turns} 决策={decisions}")

    elapsed = time.time() - start
    print()
    print(f"共 {n} 局，用时 {elapsed:.1f}s，平均每局 {elapsed / n:.2f}s")
    print(f"胜场: A={stats.get('A', 0)} B={stats.get('B', 0)} 平局/超时={stats.get('draw', 0)}")
    print(f"平均回合数: {turns_total / n:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
