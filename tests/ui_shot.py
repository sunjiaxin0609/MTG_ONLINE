"""界面截图工具：开局 → AI 代打若干回合 → 截图，用于目视检查卡图渲染。

    python tests/ui_shot.py [回合数] [输出文件]
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 必须在建窗口之前：让进程与屏幕统一为物理像素，
# 否则 Windows 显示缩放会让 Tk 坐标和截图坐标错位。
try:
    import ctypes

    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import tkinter as tk
from PIL import ImageGrab

from mtg.ai.agent import HeuristicAgent
from mtg.cards.carddb import CardDB
from mtg.engine.game import Action
from mtg.ui.app import MTGApp


def screen_scale(app: tk.Tk) -> float:
    """物理分辨率 / Tk 逻辑分辨率。进程已声明 DPI 感知时返回 1.0。"""
    try:
        import ctypes

        physical = ctypes.windll.user32.GetSystemMetrics(0)
        logical = app.winfo_screenwidth()
        if logical:
            return physical / logical
    except Exception:
        pass
    return 1.0


def drive(game, agent, max_turns: int) -> None:
    """用给定 agent 驱动双方，快速推进到指定回合。"""
    for _ in range(4000):
        decision = game.advance()
        if decision is None or game.game_over:
            return
        if decision.kind == "priority":
            game.submit(agent.take_action(game, decision.player))
        elif decision.kind == "declare_attackers":
            game.submit(Action(kind="attackers", payload={"declarations": agent.declare_attackers(
                game, decision.player,
                legal=decision.payload.get("legal", []),
                defender=decision.payload.get("defender"))}))
        elif decision.kind == "declare_blockers":
            game.submit(Action(kind="blockers", payload={"assignments": agent.declare_blockers(
                game, decision.player,
                attackers=decision.payload.get("attackers", []),
                legal=decision.payload.get("legal", []))}))
        else:
            game.submit(Action(kind="pass"))
        if game.turn_number >= max_turns:
            return


def main() -> int:
    turns = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    out = sys.argv[2] if len(sys.argv) > 2 else "data/_shot.png"

    app = MTGApp()
    app.db = CardDB.load()
    app.start_game(colors=["G"], aggression=0.6, seed=11)
    app.update()
    for window in [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)]:
        window.destroy()

    auto = HeuristicAgent(name="自动")
    original = app.game.agents[id(app.human)]
    app.game.agents[id(app.human)] = auto
    drive(app.game, auto, turns)
    app.game.agents[id(app.human)] = original

    app.render()
    app.update()
    deadline = time.time() + 60
    while time.time() < deadline and not app.status_var.get().startswith("卡图就绪"):
        app.update()
        time.sleep(0.03)

    app.geometry("1420x920+20+20")
    app.attributes("-topmost", True)
    app.deiconify()
    app.lift()
    for _ in range(40):
        app.update()
        time.sleep(0.05)

    # Windows 开了显示缩放时，Tk 报逻辑像素而截图是物理像素，必须换算
    scale = screen_scale(app)
    x, y = app.winfo_rootx(), app.winfo_rooty()
    bbox = (
        int(x * scale), int(y * scale),
        int((x + app.winfo_width()) * scale), int((y + app.winfo_height()) * scale),
    )
    image = ImageGrab.grab(bbox=bbox)
    image.save(out)
    print(f"回合 {app.game.turn_number} | 我方永久物 {len(app.human.permanents)} "
          f"| 对手 {len(app.ai.permanents)} | 手牌 {len(app.human.hand)}")
    print(f"截图 -> {out} {image.size}")
    app.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
