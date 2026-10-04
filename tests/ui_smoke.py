"""界面冒烟测试：建窗口 → 开局 → 渲染 → 推进若干步，确认不抛异常。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk  # noqa: E402

from mtg.cards.carddb import CardDB  # noqa: E402
from mtg.engine.game import Action  # noqa: E402
from mtg.ui.app import MTGApp  # noqa: E402


def main() -> int:
    app = MTGApp()
    app.withdraw()  # 不实际显示窗口
    try:
        print("加载卡池…")
        app.db = CardDB.load()
        print(app.db.summary())

        print("开局…")
        app.start_game(["G"], aggression=0.6, seed=1)
        app.update()
        print(f"  我方手牌 {len(app.human.hand)}，生命 {app.human.life}")
        print(f"  电脑手牌 {len(app.ai.hand)}，生命 {app.ai.life}")

        # 模拟人类连续让过若干次，检查渲染与推进不报错
        steps = 0
        for _ in range(60):
            if app.game.game_over:
                print("  对局已结束")
                break
            if app.pending is None:
                app.pump()
                continue
            if app.pending.kind == "priority":
                app.game.submit(Action(kind="pass"))
                app.pending = None
            elif app.pending.kind == "declare_attackers":
                app._select_all_attackers()
                app._confirm_attackers()
            elif app.pending.kind == "declare_blockers":
                app._confirm_blockers()
            else:
                app.game.submit(Action(kind="pass"))
                app.pending = None
            app.pump()
            app.update()
            steps += 1

        print(f"  推进 {steps} 步后：回合 {app.game.turn_number}")
        print(f"  我方生命 {app.human.life}，电脑生命 {app.ai.life}")
        print(f"  日志行数 {len(app.game.log_lines)}")

        # 渲染各面板
        app.render()
        app.update()
        app.show_zones()
        app.update()
        print("界面渲染正常")
        return 0
    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print(f"界面测试失败：{type(exc).__name__}: {exc}")
        return 1
    finally:
        app.destroy()


if __name__ == "__main__":
    raise SystemExit(main())
