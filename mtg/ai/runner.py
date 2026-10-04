"""对局驱动器：把引擎的决策点接到各个牌手的 AI（或人类）代理上。"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..engine.game import Action, Game

if TYPE_CHECKING:
    from ..engine.player import Player


class GameRunner:
    """驱动一局游戏跑到底。"""

    def __init__(self, game: Game, agents: dict[int, Any], max_turns: int = 300) -> None:
        self.game = game
        self.agents = agents
        self.max_turns = max_turns
        self.decision_count = 0

    def agent_for(self, player: "Player") -> Any:
        return self.agents.get(id(player))

    def step(self) -> bool:
        """处理一个决策点。返回 False 表示游戏已结束。"""
        game = self.game
        if game.game_over or game.turn_number > self.max_turns:
            return False

        decision = game.advance()
        if decision is None:
            return False

        self.decision_count += 1
        agent = self.agent_for(decision.player)
        if agent is None:
            # 没有代理就一律让过
            game.submit(Action(kind="pass"))
            return True

        kind = decision.kind
        if kind == "priority":
            action = agent.take_action(game, decision.player)
            game.submit(action)
        elif kind == "declare_attackers":
            declarations = agent.declare_attackers(
                game,
                decision.player,
                legal=decision.payload.get("legal", []),
                defender=decision.payload.get("defender"),
            )
            game.submit(Action(kind="attackers", payload={"declarations": declarations}))
        elif kind == "declare_blockers":
            assignments = agent.declare_blockers(
                game,
                decision.player,
                attackers=decision.payload.get("attackers", []),
                legal=decision.payload.get("legal", []),
            )
            game.submit(Action(kind="blockers", payload={"assignments": assignments}))
        elif kind == "choose_targets":
            targets = agent.choose_targets(game, decision.player, decision.payload.get("card"))
            game.submit(Action(kind="targets", payload={"targets": targets}))
        else:
            game.submit(Action(kind="pass"))
        return True

    def run(self, verbose: bool = False) -> "Player | None":
        """跑完整局，返回胜者。"""
        guard = 0
        while self.step():
            guard += 1
            if guard > 20000:
                self.game.log("（驱动器保护：决策次数过多，强制结束）")
                break
        if verbose:
            for line in self.game.log_lines:
                print(line)
        return self.game.winner


def play_match(
    game: Game,
    agents: dict[int, Any],
    max_turns: int = 300,
    verbose: bool = False,
) -> "Player | None":
    """便捷入口：跑一局并返回胜者。"""
    return GameRunner(game, agents, max_turns=max_turns).run(verbose=verbose)
