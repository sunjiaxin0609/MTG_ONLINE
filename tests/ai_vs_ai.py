"""端到端测试：让两个 AI 用自动组出的套牌打完整局，验证引擎全流程。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mtg.ai.agent import HeuristicAgent  # noqa: E402
from mtg.ai.runner import GameRunner  # noqa: E402
from mtg.cards.carddb import CardDB  # noqa: E402
from mtg.cards.decks import auto_build, deck_summary  # noqa: E402
from mtg.engine.game import Game  # noqa: E402
from mtg.engine.player import Player  # noqa: E402


def build_match(colors_a=("G",), colors_b=("R",), seed: int = 42):
    db = CardDB.load()

    deck_a = auto_build(db.cards, colors_a, size=60, seed=seed)
    deck_b = auto_build(db.cards, colors_b, size=60, seed=seed + 1)
    print("—— 套牌 A ——")
    print(deck_summary(deck_a))
    print("—— 套牌 B ——")
    print(deck_summary(deck_b))
    print()

    p1 = Player(name="绿牌手")
    p2 = Player(name="红牌手")
    game = Game(players=[p1, p2], seed=seed)
    p1.load_deck(deck_a)
    p2.load_deck(deck_b)

    agents = {
        id(p1): HeuristicAgent(name="AI-绿", aggression=0.6),
        id(p2): HeuristicAgent(name="AI-红", aggression=0.6),
    }
    game.agents = agents
    return game, p1, p2


def main() -> int:
    game, p1, p2 = build_match(seed=7)
    game.setup()
    game.start(first_player=p1)

    runner = GameRunner(game, agents={}, max_turns=120)
    runner.agents = game.agents
    winner = runner.run()

    print("=== 对局结束 ===")
    print(f"回合数：{game.turn_number}")
    print(f"决策次数：{runner.decision_count}")
    print(f"{p1.name}: 生命 {p1.life}，场上生物 {len(p1.creatures)}")
    print(f"{p2.name}: 生命 {p2.life}，场上生物 {len(p2.creatures)}")
    print(f"胜者：{winner.name if winner else '无（平局或超时）'}")

    print("\n=== 最后 40 条日志 ===")
    for line in game.log_lines[-40:]:
        print(" ", line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
