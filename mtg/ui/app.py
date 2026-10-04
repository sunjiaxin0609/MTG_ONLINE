"""tkinter 桌面主界面。

布局自上而下：对手信息条 → 对手战场 → 战斗/堆叠区 → 我方战场 → 手牌 → 操作条，
右侧是滚动日志。所有交互都通过引擎的"决策点"接口，UI 只负责收集人类输入。
"""
from __future__ import annotations

import os
import re
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from ..ai.agent import HeuristicAgent
from ..cards.carddb import CardDB
from ..cards.decks import auto_build, deck_summary, load_deck, save_deck
from ..engine.game import Action, Game
from ..engine.player import Player
from ..engine.types import Phase, Step, Zone
from .card_widget import ART_ENABLED, CardWidget, card_tooltip, set_art_enabled, tooltip
from .images import PIL_AVAILABLE, get_cache
from .theme import (
    ATTACKING,
    BG,
    BLOCKING,
    BTN_ACCENT,
    BTN_BG,
    BTN_DANGER,
    BTN_FG,
    FONT_BOLD,
    FONT_LOG,
    FONT_NORMAL,
    FONT_SMALL,
    FONT_TITLE,
    HIGHLIGHT,
    PANEL,
    PANEL_DARK,
    SELECTED,
    TEXT,
    TEXT_DIM,
)

DECKS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "decks")

COLOR_CHOICES = [
    ("白 (W)", ["W"]),
    ("蓝 (U)", ["U"]),
    ("黑 (B)", ["B"]),
    ("红 (R)", ["R"]),
    ("绿 (G)", ["G"]),
    ("白绿 (GW)", ["G", "W"]),
    ("蓝黑 (UB)", ["U", "B"]),
    ("红白 (RW)", ["R", "W"]),
    ("黑绿 (BG)", ["B", "G"]),
    ("红蓝 (UR)", ["U", "R"]),
    ("红绿 (RG)", ["R", "G"]),
    ("黑白 (WB)", ["W", "B"]),
]


class CardStrip(tk.Frame):
    """卡片容器：按宽度自动换行，内容超出高度时可用滚轮查看。

    战场上一旦刷出十几个衍生物，固定横排会被裁掉；这里保证每一张都看得到。
    """

    def __init__(self, master: tk.Misc, bg: str) -> None:
        super().__init__(master, bg=bg)
        # canvas 的请求尺寸压到最小，避免反过来把父容器撑大（高度由父容器说了算）
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0, width=1, height=1)
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self._on_scroll_set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bar.pack(side="right", fill="y")
        self._items: list[Any] = []
        self._columns = 0
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)

    def add(self, widget: Any) -> None:
        widget.wheel_handler = self._scroll
        self._items.append(widget)
        self._relayout()

    def _on_scroll_set(self, first: str, last: str) -> None:
        """内容没超出高度时自动藏起滚动条。"""
        need = not (float(first) <= 0.0 and float(last) >= 1.0)
        if need and not self.bar.winfo_ismapped():
            self.bar.pack(side="right", fill="y")
        elif not need and self.bar.winfo_ismapped():
            self.bar.pack_forget()
        self.bar.set(first, last)

    def _on_inner(self, _event: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)
        # 用略宽松的间距估算列数，这样滚动条出现/消失时列数不会跳变
        columns = max(1, event.width // (CardWidget.WIDTH + 4))
        if columns != self._columns:
            self._columns = columns
            self._relayout()

    def _relayout(self) -> None:
        columns = max(1, self._columns)
        for index, widget in enumerate(self._items):
            widget.grid(row=index // columns, column=index % columns, padx=2, pady=2)

    def _scroll(self, event: tk.Event) -> None:
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


class MTGApp(tk.Tk):
    """主窗口。"""

    def __init__(self) -> None:
        super().__init__()
        self.title("MTGO — 万智牌标准赛制（本地对战）")
        self.configure(bg=BG)
        self.geometry("1400x900")
        self.minsize(1180, 780)

        self.db: CardDB | None = None
        self.game: Game | None = None
        self.human: Player | None = None
        self.ai: Player | None = None
        self.ai_agent = HeuristicAgent(name="电脑", aggression=0.6)

        self.pending: Any = None  # 当前等待人类输入的 Decision
        self.selected_attackers: set[int] = set()
        self.block_assignments: dict[int, list[int]] = {}  # attacker id -> blocker ids
        self.focused_attacker: int | None = None
        self.auto_pass = tk.BooleanVar(value=False)
        self.art_var = tk.BooleanVar(value=True)

        self._widget_refs: list[Any] = []
        self._log_index = 0

        self._build_shell()
        self.after(100, self._bootstrap)

    # ================================================================ 骨架
    def _build_shell(self) -> None:
        # 顶部：标题 + 新对局按钮
        header = tk.Frame(self, bg=PANEL_DARK, height=38)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        tk.Label(header, text="MTGO", bg=PANEL_DARK, fg=TEXT, font=FONT_TITLE).pack(side="left", padx=12)
        self.status_var = tk.StringVar(value="正在加载卡池…")
        tk.Label(header, textvariable=self.status_var, bg=PANEL_DARK, fg=TEXT_DIM, font=FONT_NORMAL).pack(
            side="left", padx=16
        )
        tk.Button(
            header, text="新对局", bg=BTN_BG, fg=BTN_FG, font=FONT_NORMAL, relief="flat",
            command=self.new_game_dialog,
        ).pack(side="right", padx=6, pady=6)
        tk.Button(
            header, text="牌库/坟场", bg=BTN_BG, fg=BTN_FG, font=FONT_NORMAL, relief="flat",
            command=self.show_zones,
        ).pack(side="right", padx=4, pady=6)
        tk.Button(
            header, text="卡图", bg=BTN_BG, fg=BTN_FG, font=FONT_NORMAL, relief="flat",
            command=self.art_menu,
        ).pack(side="right", padx=4, pady=6)
        tk.Checkbutton(
            header, text="自动让过", variable=self.auto_pass, bg=PANEL_DARK, fg=TEXT,
            font=FONT_NORMAL, selectcolor=PANEL,
        ).pack(side="right", padx=8)

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True)

        # 左：牌桌；右：日志
        self.board_frame = tk.Frame(body, bg=BG)
        self.board_frame.pack(side="left", fill="both", expand=True)

        right = tk.Frame(body, bg=PANEL, width=300)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)

        tk.Label(right, text="对局日志", bg=PANEL, fg=TEXT, font=FONT_BOLD).pack(anchor="w", padx=8, pady=(8, 2))
        self.log_text = tk.Text(
            right, wrap="word", font=FONT_LOG, bg="#fdfcf8", fg=TEXT, relief="flat", height=10
        )
        self.log_text.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.log_text.configure(state="disabled")

        scroll = ttk.Scrollbar(self.log_text, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)

        # 牌桌各行
        self.opp_info = tk.Frame(self.board_frame, bg=PANEL, height=26)
        self.opp_info.pack(fill="x", padx=8, pady=(8, 2))
        self.opp_info.pack_propagate(False)

        self.opp_board = tk.Frame(self.board_frame, bg=PANEL_DARK, height=200)
        self.opp_board.pack(fill="x", padx=8, pady=2)
        self.opp_board.pack_propagate(False)

        self.mid_bar = tk.Frame(self.board_frame, bg=PANEL, height=76)
        self.mid_bar.pack(fill="x", padx=8, pady=4)
        self.mid_bar.pack_propagate(False)

        self.my_board = tk.Frame(self.board_frame, bg=PANEL_DARK, height=200)
        self.my_board.pack(fill="x", padx=8, pady=2)
        self.my_board.pack_propagate(False)

        self.my_info = tk.Frame(self.board_frame, bg=PANEL, height=26)
        self.my_info.pack(fill="x", padx=8, pady=2)
        self.my_info.pack_propagate(False)

        self.hand_frame = tk.Frame(self.board_frame, bg=PANEL, height=192)
        self.hand_frame.pack(fill="x", padx=8, pady=4)
        self.hand_frame.pack_propagate(False)

        self.action_bar = tk.Frame(self.board_frame, bg=PANEL_DARK, height=46)
        self.action_bar.pack(fill="x", padx=8, pady=(2, 8))
        self.action_bar.pack_propagate(False)

    def _bootstrap(self) -> None:
        """启动后加载卡池并开一局。"""
        try:
            self.db = CardDB.load()
        except FileNotFoundError as exc:
            messagebox.showerror("缺少卡池", f"{exc}\n\n请先运行：python -m mtg.cards.fetch_scryfall")
            self.destroy()
            return
        self.status_var.set(f"卡池 {self.db.stats.get('total')} 张 · {self.db.stats.get('coverage')}% 完整解析")
        if not PIL_AVAILABLE:
            self._append_log("提示：未安装 Pillow，卡图显示已关闭（pip install pillow 后可开启）")
            self.art_var.set(False)
        self.new_game_dialog(first=True)

    # ================================================================ 新对局
    def new_game_dialog(self, first: bool = False) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("开始新对局")
        dialog.configure(bg=BG)
        dialog.geometry("460x430")
        dialog.transient(self)
        dialog.grab_set()

        tk.Label(dialog, text="选择套牌", bg=BG, fg=TEXT, font=FONT_TITLE).pack(pady=(12, 4))

        # 预构筑套牌
        preset_files = _list_preset_decks()
        tk.Label(dialog, text="预构筑套牌（推荐）", bg=BG, fg=TEXT, font=FONT_BOLD).pack(anchor="w", padx=20)
        deck_var = tk.StringVar()
        if preset_files:
            names = [name for _path, name in preset_files]
            combo = ttk.Combobox(dialog, values=names, textvariable=deck_var, state="readonly", width=22,
                                 font=FONT_NORMAL)
            combo.pack(padx=20, pady=(2, 8))
            combo.current(0)
        else:
            tk.Label(dialog, text="（未找到，请先运行 python -m mtg.cards.build_decks）",
                     bg=BG, fg=TEXT_DIM, font=FONT_SMALL).pack(padx=20)

        tk.Label(dialog, text="或按颜色自动组一套", bg=BG, fg=TEXT, font=FONT_BOLD).pack(anchor="w", padx=20)
        color_var = tk.StringVar(value="绿 (G)")
        row = tk.Frame(dialog, bg=BG)
        row.pack(pady=4, padx=20, anchor="w")
        for idx, (label, colors) in enumerate(COLOR_CHOICES):
            tk.Radiobutton(
                row, text=label, variable=color_var, value=label, bg=BG, fg=TEXT,
                font=FONT_NORMAL, selectcolor=PANEL, anchor="w", width=12,
            ).grid(row=idx // 4, column=idx % 4, sticky="w", padx=2, pady=1)

        agg_var = tk.DoubleVar(value=0.6)
        tk.Label(dialog, text="电脑进攻性", bg=BG, fg=TEXT, font=FONT_NORMAL).pack(pady=(10, 0))
        tk.Scale(dialog, from_=0.2, to=1.0, resolution=0.1, orient="horizontal",
                 variable=agg_var, bg=BG, fg=TEXT, length=300).pack()

        def start() -> None:
            dialog.destroy()
            chosen = deck_var.get()
            path = next((p for p, _n in preset_files if _n == chosen), None) if chosen else None
            if path is None and preset_files:
                path = preset_files[0][0]
            if path is not None:
                self.start_game(colors=None, deck_path=path, aggression=agg_var.get())
                return
            label = color_var.get()
            colors = next(c for lbl, c in COLOR_CHOICES if lbl == label)
            self.start_game(colors, aggression=agg_var.get())

        def cancel() -> None:
            dialog.destroy()
            if first:
                self.destroy()

        btn_row = tk.Frame(dialog, bg=BG)
        btn_row.pack(pady=14)
        tk.Button(btn_row, text="开始", bg=BTN_ACCENT, fg=BTN_FG, font=FONT_BOLD, width=10,
                  relief="flat", command=start).pack(side="left", padx=6)
        tk.Button(btn_row, text="取消", bg=BTN_BG, fg=BTN_FG, font=FONT_NORMAL, width=10,
                  relief="flat", command=cancel).pack(side="left", padx=6)

    def start_game(
        self,
        colors: list[str] | None = None,
        aggression: float = 0.6,
        seed: int | None = None,
        deck_path: str | None = None,
    ) -> None:
        assert self.db is not None
        import random

        if seed is None:
            seed = random.randrange(1, 10**6)

        if deck_path:
            my_deck = load_deck(deck_path, self.db)
            deck_colors = _colors_of_deck(my_deck)
        else:
            my_deck = auto_build(self.db.cards, colors or ["G"], size=60, seed=seed)
            deck_colors = list(colors or ["G"])

        # 电脑用一套不同的颜色，保证对局有变化
        ai_colors = _opposite_colors(deck_colors)
        ai_deck = auto_build(self.db.cards, ai_colors, size=60, seed=seed + 7777)

        self.human = Player(name="你")
        self.ai = Player(name="电脑")
        self.game = Game(players=[self.human, self.ai], seed=seed)
        self.human.load_deck(my_deck)
        self.ai.load_deck(ai_deck)

        self.ai_agent = HeuristicAgent(name="电脑", aggression=aggression)
        self.game.agents = {id(self.ai): self.ai_agent, id(self.human): self}

        self.game.on_log(self._on_engine_log)
        self.game.setup()
        self.game.start(first_player=self.human)

        self._log_index = 0
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

        self.selected_attackers.clear()
        self.block_assignments.clear()
        self.focused_attacker = None
        self.pending = None

        self.status_var.set(f"你的套牌 {'/'.join(deck_colors)} vs 电脑 {'/'.join(ai_colors)}")
        self.pump()
        self._prefetch_art()

    # ================================================================ 卡图
    def _prefetch_art(self) -> None:
        """开局后后台下载卡图：手牌优先，再补牌库（每张约 25KB，只下一次）。"""
        if self.game is None or self.human is None:
            return
        cache = get_cache()
        mine = [card.data for card in self.human.hand]
        rest: list[Any] = []
        for player in self.game.players:
            rest.extend(card.data for card in player.library)
            if player is not self.human:
                rest.extend(card.data for card in player.hand)

        first = cache.prefetch(mine, root=self)
        if first:
            self._append_log(f"卡图：先下你手上的 {first} 张…")
        self.after(600, lambda: self._prefetch_rest(rest, first))

    def _prefetch_rest(self, rest: list[Any], already: int) -> None:
        """手牌下完后，再排队补牌库里的卡图。"""
        if self.game is None:
            return
        cache = get_cache()
        queued = cache.prefetch(rest, root=self)
        total = already + queued
        if queued:
            self._append_log(f"卡图：继续下载牌库 {queued} 张（约 {queued * 25 // 1024 + 1} MB，之后不再联网）")
        if total:
            self._watch_prefetch()
        elif cache.cached_count():
            self.status_var.set(f"卡图就绪 · 本地缓存 {cache.cached_count()} 张")

    def _watch_prefetch(self) -> None:
        """下载期间在状态栏显示进度，完成后自动刷新牌面。"""
        cache = get_cache()
        if self.game is None:
            return
        if cache.pending_count:
            self.status_var.set(f"卡图下载中… 还剩 {cache.pending_count} 张（已缓存 {cache.cached_count()}）")
            self.after(400, self._watch_prefetch)
        else:
            self.status_var.set(f"卡图就绪 · 本地缓存 {cache.cached_count()} 张")
            self.render()

    def art_menu(self) -> None:
        """卡图相关设置。"""
        menu = tk.Menu(self, tearoff=0)
        menu.add_checkbutton(
            label="显示真实卡图",
            variable=self.art_var,
            command=lambda: (set_art_enabled(self.art_var.get()), self.render()),
        )
        menu.add_command(label="下载本局双方套牌的卡图", command=self._prefetch_art)
        menu.add_separator()
        menu.add_command(
            label=f"本地已缓存 {get_cache().cached_count()} 张",
            command=lambda: messagebox.showinfo(
                "卡图缓存",
                f"本地缓存目录：\n{get_cache().cache_dir}\n\n已缓存 {get_cache().cached_count()} 张卡图。\n"
                "删掉该目录即可清空缓存。",
            ),
        )
        x = self.winfo_pointerx()
        y = self.winfo_pointery()
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    # ================================================================ 引擎推进
    def pump(self) -> None:
        """推进引擎直到需要人类做决定（或游戏结束）。"""
        if self.game is None:
            return
        guard = 0
        while not self.game.game_over:
            guard += 1
            if guard > 400:
                self._append_log("（界面保护：单次推进过多，已暂停）")
                break
            decision = self.game.advance()
            if decision is None:
                break
            if decision.player is not self.human:
                self._ai_act(decision)
                continue
            # 轮到人类
            if self.auto_pass.get() and decision.kind == "priority":
                self.game.submit(Action(kind="pass"))
                continue
            self.pending = decision
            self.render()
            return
        self.pending = None
        self.render()
        if self.game.game_over:
            self._show_game_over()

    def _ai_act(self, decision: Any) -> None:
        agent = self.ai_agent
        game = self.game
        assert game is not None
        kind = decision.kind
        if kind == "priority":
            game.submit(agent.take_action(game, decision.player))
        elif kind == "declare_attackers":
            declarations = agent.declare_attackers(
                game, decision.player,
                legal=decision.payload.get("legal", []),
                defender=decision.payload.get("defender"),
            )
            game.submit(Action(kind="attackers", payload={"declarations": declarations}))
        elif kind == "declare_blockers":
            assignments = agent.declare_blockers(
                game, decision.player,
                attackers=decision.payload.get("attackers", []),
                legal=decision.payload.get("legal", []),
            )
            game.submit(Action(kind="blockers", payload={"assignments": assignments}))
        else:
            game.submit(Action(kind="pass"))

    def _on_engine_log(self, message: str) -> None:
        self._append_log(message)

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # ================================================================ 渲染
    def render(self) -> None:
        if self.game is None:
            return
        self._render_info(self.opp_info, self.ai, is_opponent=True)
        self._render_info(self.my_info, self.human, is_opponent=False)
        self._render_board(self.opp_board, self.ai, is_mine=False)
        self._render_board(self.my_board, self.human, is_mine=True)
        self._render_mid()
        self._render_hand()
        self._render_actions()

    def _clear(self, frame: tk.Frame) -> None:
        for child in frame.winfo_children():
            child.destroy()

    def _render_info(self, frame: tk.Frame, player: Player | None, is_opponent: bool) -> None:
        self._clear(frame)
        if player is None:
            return
        bits = [
            f"{player.name}",
            f"生命 {player.life}",
            f"手牌 {len(player.hand)}",
            f"牌库 {len(player.library)}",
            f"坟场 {len(player.graveyard)}",
            f"法术力 {player.mana_pool}",
        ]
        for text in bits:
            color = TEXT if "生命" not in text else (BTN_DANGER if player.life <= 5 else TEXT)
            tk.Label(frame, text=text, bg=PANEL, fg=color, font=FONT_BOLD).pack(side="left", padx=8)
        if is_opponent:
            side = "对手"
        else:
            side = "我方"
        tk.Label(frame, text=f"（{side}）", bg=PANEL, fg=TEXT_DIM, font=FONT_SMALL).pack(side="left")

    def _render_board(self, frame: tk.Frame, player: Player | None, is_mine: bool) -> None:
        self._clear(frame)
        if player is None:
            return

        lands = [p for p in player.permanents if p.is_land]
        creatures = [p for p in player.permanents if p.is_creature and not p.is_land]
        others = [p for p in player.permanents if not p.is_land and not p.is_creature]

        # 三块等宽（uniform），否则内容少的那块会被挤窄、能放的列数也跟着变
        for column, (title, items, fg) in enumerate(
            (("地", lands, TEXT_DIM), ("生物", creatures, TEXT), ("其他", others, TEXT_DIM))
        ):
            frame.columnconfigure(column, weight=1, uniform="board")
            frame.rowconfigure(0, weight=1)
            section = tk.Frame(frame, bg=PANEL_DARK)
            section.grid(row=0, column=column, sticky="nsew", padx=4, pady=4)
            tk.Label(section, text=f"{title} ({len(items)})", bg=PANEL_DARK, fg=fg,
                     font=FONT_BOLD).pack(anchor="w")
            strip = CardStrip(section, bg=PANEL_DARK)
            strip.pack(fill="both", expand=True)
            for perm in items:
                widget = self._render_permanent(strip, perm, is_mine)
                if widget is not None:
                    strip.add(widget)

    def _render_permanent(self, parent: tk.Misc, perm: Any, is_mine: bool) -> Any:
        data = perm.data
        # 徽章显示"当前"数值（含所有加成），不是牌面印刷值
        badge = None
        if perm.is_creature:
            badge = f"{perm.power()}/{perm.toughness()}"
        elif perm.is_planeswalker:
            badge = f"{perm.loyalty}"

        state_bits = []
        if perm.tapped:
            state_bits.append("横置")
        if perm.is_sick and perm.is_creature:
            state_bits.append("召唤失调")
        if perm.damage_marked:
            state_bits.append(f"伤害 {perm.damage_marked}")
        for kind, count in perm.counters.items():
            state_bits.append(f"{kind}×{count}")
        subtitle = " ".join(state_bits)

        container = parent.inner if isinstance(parent, CardStrip) else parent
        widget = CardWidget(container, data, subtitle=subtitle, compact=True, badge=badge,
                            on_click=lambda: self._on_permanent_click(perm))
        widget.set_dimmed(perm.tapped)

        # 战斗标记
        if is_mine and id(perm) in self.selected_attackers:
            widget.set_selected(True)
            widget.inner.configure(highlightbackground=ATTACKING)
        if not is_mine and self.focused_attacker is not None and id(perm) == self.focused_attacker:
            widget.inner.configure(highlightbackground=HIGHLIGHT)
        if not is_mine and any(id(perm) in v for v in self.block_assignments.values()):
            widget.inner.configure(highlightbackground=BLOCKING)

        text = _permanent_description(perm)
        card_tooltip(widget, data, text)
        return widget

    def _render_mid(self) -> None:
        self._clear(self.mid_bar)
        game = self.game
        assert game is not None

        # 阶段指示
        left = tk.Frame(self.mid_bar, bg=PANEL)
        left.pack(side="left", fill="y", padx=6)
        phase_text = game.phase.value
        if game.step:
            phase_text += f" · {game.step.value}"
        tk.Label(left, text=f"第 {game.turn_number} 回合", bg=PANEL, fg=TEXT, font=FONT_BOLD).pack(anchor="w")
        tk.Label(left, text=phase_text, bg=PANEL, fg=TEXT_DIM, font=FONT_NORMAL).pack(anchor="w")
        active = game.active_player
        tk.Label(left, text=f"主动：{active.name if active else '-'}", bg=PANEL, fg=TEXT_DIM,
                 font=FONT_SMALL).pack(anchor="w")

        # 堆叠
        stack_frame = tk.Frame(self.mid_bar, bg=PANEL)
        stack_frame.pack(side="left", fill="both", expand=True, padx=10)
        tk.Label(stack_frame, text="堆叠", bg=PANEL, fg=TEXT, font=FONT_BOLD).pack(anchor="w")
        if game.stack:
            lines = []
            for item in list(reversed(game.stack.items))[:4]:
                lines.append("▶ " + item.describe())
            tk.Label(stack_frame, text="\n".join(lines), bg=PANEL, fg=TEXT, font=FONT_SMALL,
                     justify="left").pack(anchor="w")
        else:
            tk.Label(stack_frame, text="（空）", bg=PANEL, fg=TEXT_DIM, font=FONT_SMALL).pack(anchor="w")

        # 攻击提示
        if game.combat.state.attackers:
            names = ", ".join(d.attacker.name for d in game.combat.state.attackers)
            tk.Label(self.mid_bar, text=f"攻击中：{names}", bg=PANEL, fg=ATTACKING, font=FONT_BOLD,
                     wraplength=380, justify="left").pack(side="right", padx=8)

    def _render_hand(self) -> None:
        self._clear(self.hand_frame)
        if self.game is None or self.human is None:
            return
        tk.Label(self.hand_frame, text=f"手牌（{len(self.human.hand)}）", bg=PANEL, fg=TEXT,
                 font=FONT_BOLD).pack(anchor="w", padx=6)

        strip = CardStrip(self.hand_frame, bg=PANEL)
        strip.pack(fill="both", expand=True, padx=6)

        for card in self.human.hand:
            playable = self._is_playable(card)
            widget = CardWidget(strip.inner, card.data, on_click=lambda c=card: self._on_hand_click(c))
            if not playable:
                widget.set_dimmed(True)
            card_tooltip(widget, card.data, _card_description(card.data))
            strip.add(widget)

    def _is_playable(self, card: Any) -> bool:
        game = self.game
        assert game is not None and self.human is not None
        if card.data.is_land:
            return self.human.can_play_land()
        ok, _reason = game.can_cast(self.human, card)
        return ok

    def _render_actions(self) -> None:
        self._clear(self.action_bar)
        if self.game is None:
            return

        if self.game.game_over:
            winner = self.game.winner
            tk.Label(self.action_bar, text=f"对局结束 · 胜者：{winner.name if winner else '无'}",
                     bg=PANEL_DARK, fg=TEXT, font=FONT_TITLE).pack(side="left", padx=12)
            tk.Button(self.action_bar, text="再来一局", bg=BTN_ACCENT, fg=BTN_FG, font=FONT_BOLD,
                      relief="flat", command=lambda: self.new_game_dialog()).pack(side="left", padx=6)
            return

        decision = self.pending
        if decision is None:
            tk.Label(self.action_bar, text="电脑思考中…", bg=PANEL_DARK, fg=TEXT_DIM,
                     font=FONT_NORMAL).pack(side="left", padx=12)
            return

        kind = decision.kind
        if kind == "priority":
            tk.Label(self.action_bar, text=decision.prompt, bg=PANEL_DARK, fg=TEXT,
                     font=FONT_NORMAL).pack(side="left", padx=10)
            tk.Button(self.action_bar, text="让过", bg=BTN_BG, fg=BTN_FG, font=FONT_BOLD, width=8,
                     relief="flat", command=self._pass).pack(side="left", padx=4)
            tk.Button(self.action_bar, text="让过本阶段", bg=BTN_BG, fg=BTN_FG, font=FONT_NORMAL, width=11,
                     relief="flat", command=self._pass_phase).pack(side="left", padx=4)
        elif kind == "declare_attackers":
            tk.Label(self.action_bar, text="点击自己的生物选择攻击，然后确认",
                     bg=PANEL_DARK, fg=TEXT, font=FONT_NORMAL).pack(side="left", padx=10)
            tk.Button(self.action_bar, text="全选", bg=BTN_BG, fg=BTN_FG, font=FONT_NORMAL, width=7,
                     relief="flat", command=self._select_all_attackers).pack(side="left", padx=4)
            tk.Button(self.action_bar, text="确认攻击", bg=BTN_ACCENT, fg=BTN_FG, font=FONT_BOLD, width=10,
                     relief="flat", command=self._confirm_attackers).pack(side="left", padx=4)
            tk.Button(self.action_bar, text="不攻击", bg=BTN_BG, fg=BTN_FG, font=FONT_NORMAL, width=8,
                     relief="flat", command=lambda: self._submit_declarations([])).pack(side="left", padx=4)
        elif kind == "declare_blockers":
            tk.Label(self.action_bar,
                     text="先点对手的攻击者，再点自己的生物进行阻挡",
                     bg=PANEL_DARK, fg=TEXT, font=FONT_NORMAL).pack(side="left", padx=10)
            tk.Button(self.action_bar, text="确认阻挡", bg=BTN_ACCENT, fg=BTN_FG, font=FONT_BOLD, width=10,
                     relief="flat", command=self._confirm_blockers).pack(side="left", padx=4)
            tk.Button(self.action_bar, text="清除", bg=BTN_BG, fg=BTN_FG, font=FONT_NORMAL, width=7,
                     relief="flat", command=self._clear_blocks).pack(side="left", padx=4)
            tk.Button(self.action_bar, text="不阻挡", bg=BTN_BG, fg=BTN_FG, font=FONT_NORMAL, width=8,
                     relief="flat", command=self._confirm_blockers).pack(side="left", padx=4)

    # ================================================================ 人类交互
    def _on_hand_click(self, card: Any) -> None:
        game = self.game
        if game is None or self.human is None:
            return
        if self.pending is None or self.pending.kind != "priority":
            messagebox.showinfo("提示", "现在不是你可以出牌的时机")
            return
        if card.data.is_land:
            self.game.submit(Action(kind="play_land", card=card))
        else:
            targets = self._choose_targets_for(card)
            if targets is None:
                return  # 用户取消了目标选择
            self.game.submit(Action(kind="cast", card=card, targets=targets))
        self.pending = None
        self.pump()

    def _choose_targets_for(self, card: Any) -> list | None:
        """为需要目标的咒语弹出目标选择框。返回 None 表示取消。"""
        specs: list[Any] = []
        for ability in getattr(card.data, "abilities", []) or []:
            if ability.kind == "spell" and ability.spell:
                specs.extend(ability.spell.targets)
        if not specs:
            return []

        game = self.game
        assert game is not None and self.human is not None
        opponent = game.other_player(self.human)

        candidates: list[Any] = []
        for spec in specs:
            kind = getattr(spec, "kind", "any")
            if kind == "player":
                candidates.append(opponent)
                continue
            if kind == "spell":
                if game.stack.top:
                    candidates.append(game.stack.top)
                continue
            pool: list[Any] = []
            for player in game.players:
                for perm in player.permanents:
                    if _matches_target_kind(perm, kind):
                        pool.append(perm)
            if not pool:
                continue
            chosen = _ask_target(self, f"{card.name} — 选择目标（{spec.describe()}）", pool)
            if chosen is None:
                return None
            candidates.append(chosen)
        return candidates

    def _on_permanent_click(self, perm: Any) -> None:
        game = self.game
        if game is None or self.pending is None:
            return
        kind = self.pending.kind

        if kind == "declare_attackers":
            if perm.controller is not self.human:
                return
            key = id(perm)
            if key in self.selected_attackers:
                self.selected_attackers.discard(key)
            else:
                ok, reason = game.combat.can_attack(perm)
                if not ok:
                    messagebox.showinfo("无法攻击", f"{perm.name}：{reason}")
                    return
                self.selected_attackers.add(key)
            self.render()
            return

        if kind == "declare_blockers":
            if perm.controller is self.human:
                # 我方生物：分配给当前选中的攻击者
                if self.focused_attacker is None:
                    messagebox.showinfo("提示", "请先点击要阻挡的对手生物")
                    return
                blockers = self.block_assignments.setdefault(self.focused_attacker, [])
                key = id(perm)
                if key in blockers:
                    blockers.remove(key)
                else:
                    ok, reason = game.combat.can_block(perm, _permanent_by_id(game, self.focused_attacker))
                    if not ok:
                        messagebox.showinfo("无法阻挡", f"{perm.name}：{reason}")
                        return
                    blockers.append(key)
            else:
                # 对手生物：设为当前阻挡目标
                if perm in self.pending.payload.get("attackers", []):
                    self.focused_attacker = id(perm)
                else:
                    return
            self.render()
            return

        # 优先权：点击地/产费永久物则横置产费
        if kind == "priority" and perm.controller is self.human:
            self._tap_for_mana(perm)

    def _tap_for_mana(self, perm: Any) -> None:
        """手动横置一个永久物产费。"""
        from ..engine.payment import _ability_colors, _activated_abilities

        for idx, ability in enumerate(_activated_abilities(perm)):
            if ability.is_mana_ability:
                self.game.activate_ability(perm, idx, [])
                self.render()
                return
        messagebox.showinfo("提示", f"{perm.name} 没有可以产费的异能")

    def _pass(self) -> None:
        self.game.submit(Action(kind="pass"))
        self.pending = None
        self.pump()

    def _pass_phase(self) -> None:
        """连续让过直到阶段变化。"""
        if self.game is None:
            return
        start_phase = (self.game.phase, self.game.step)
        guard = 0
        while guard < 60:
            guard += 1
            self.game.submit(Action(kind="pass"))
            decision = self.game.advance()
            if decision is None or self.game.game_over:
                self.pending = None
                self.render()
                if self.game.game_over:
                    self._show_game_over()
                return
            if decision.player is not self.human:
                self._ai_act(decision)
                continue
            if (self.game.phase, self.game.step) != start_phase:
                self.pending = decision
                self.render()
                return
        self.pump()

    def _select_all_attackers(self) -> None:
        game = self.game
        assert game is not None and self.human is not None
        for perm in self.human.creatures:
            ok, _ = game.combat.can_attack(perm)
            if ok:
                self.selected_attackers.add(id(perm))
        self.render()

    def _confirm_attackers(self) -> None:
        game = self.game
        assert game is not None and self.human is not None
        opponent = game.other_player(self.human)
        declarations = []
        for perm in self.human.creatures:
            if id(perm) in self.selected_attackers:
                declarations.append((perm, opponent))
        self._submit_declarations(declarations)

    def _submit_declarations(self, declarations: list) -> None:
        self.selected_attackers.clear()
        self.game.submit(Action(kind="attackers", payload={"declarations": declarations}))
        self.pending = None
        self.pump()

    def _clear_blocks(self) -> None:
        self.block_assignments.clear()
        self.focused_attacker = None
        self.render()

    def _confirm_blockers(self) -> None:
        game = self.game
        assert game is not None
        assignments: dict[Any, list[Any]] = {}
        attackers = self.pending.payload.get("attackers", []) if self.pending else []
        index = {id(a): a for a in attackers}
        for attacker_id, blocker_ids in self.block_assignments.items():
            attacker = index.get(attacker_id)
            if attacker is None:
                continue
            blockers = [_permanent_by_id(game, bid) for bid in blocker_ids]
            blockers = [b for b in blockers if b is not None]
            if blockers:
                assignments[attacker] = blockers
        self.block_assignments.clear()
        self.focused_attacker = None
        self.game.submit(Action(kind="blockers", payload={"assignments": assignments}))
        self.pending = None
        self.pump()

    # ================================================================ 其他窗口
    def show_zones(self) -> None:
        """查看双方坟场（卡图）与牌库概况。"""
        if self.game is None:
            return
        window = tk.Toplevel(self)
        window.title("区域 · 坟场")
        window.configure(bg=BG)
        window.geometry("760x620")

        for player in self.game.players:
            frame = tk.Frame(window, bg=PANEL)
            frame.pack(fill="x", padx=10, pady=6)
            tk.Label(frame, text=f"{player.name} — 坟场 {len(player.graveyard)} 张", bg=PANEL,
                     fg=TEXT, font=FONT_BOLD).pack(anchor="w", padx=6)
            if player.graveyard:
                grid = card_grid(frame, player.graveyard, columns=6)
                grid.pack(fill="x", padx=6, pady=4)
            else:
                tk.Label(frame, text="（空）", bg=PANEL, fg=TEXT_DIM, font=FONT_SMALL).pack(anchor="w", padx=6)
            tk.Label(frame, text=f"牌库剩余 {len(player.library)} 张", bg=PANEL, fg=TEXT_DIM,
                     font=FONT_SMALL).pack(anchor="w", padx=6)

        # 我的套牌概况
        if self.human is not None:
            allcards = list(self.human.library) + list(self.human.hand) + list(self.human.graveyard)
            datas = [c.data for c in allcards]
            tk.Label(window, text="套牌概况", bg=BG, fg=TEXT, font=FONT_BOLD).pack(anchor="w", padx=12, pady=(10, 2))
            tk.Label(window, text=deck_summary(datas), bg=BG, fg=TEXT_DIM, font=FONT_SMALL,
                     justify="left").pack(anchor="w", padx=12)
            tk.Button(
                window, text="查看我的牌库剩余卡种（卡图）", bg=BTN_BG, fg=BTN_FG, font=FONT_NORMAL,
                relief="flat", command=self.show_library,
            ).pack(anchor="w", padx=12, pady=8)

    def show_library(self) -> None:
        """用卡图列出我牌库里还剩哪些卡种（不泄露抽取顺序）。"""
        if self.human is None:
            return
        grouped: dict[str, list[Any]] = {}
        for card in self.human.library:
            grouped.setdefault(card.name, []).append(card)
        if not grouped:
            messagebox.showinfo("牌库", "牌库已空")
            return

        window = tk.Toplevel(self)
        window.title(f"我的牌库 · 剩余 {len(self.human.library)} 张 / {len(grouped)} 种")
        window.configure(bg=BG)
        window.geometry("780x600")
        tk.Label(window, text="同一种只列一张，括号里是剩余张数", bg=BG, fg=TEXT_DIM,
                 font=FONT_SMALL).pack(anchor="w", padx=12, pady=(8, 0))

        unique = [cards[0] for cards in grouped.values()]
        for card in unique:
            card.subtitle_hint = f"×{len(grouped[card.name])}"
        grid = card_grid(window, unique, columns=6, show_count=True)
        grid.pack(fill="both", expand=True, padx=10, pady=8)

        # 顺手把这些卡图也补上
        get_cache().prefetch([c.data for c in unique], root=self)

    def _show_game_over(self) -> None:
        if self.game is None:
            return
        winner = self.game.winner
        text = f"胜者：{winner.name}" if winner else "平局"
        self._append_log(f"=== 对局结束 · {text} ===")
        messagebox.showinfo("对局结束", f"{text}\n共进行了 {self.game.turn_number} 个回合")

    # ---- 引擎回调（占卜/侦察时由引擎询问）
    def scry_decision(self, game: Game, player: Player, cards: list) -> list:
        return self.ai_agent.scry_decision(game, player, cards)

    def surveil_decision(self, game: Game, player: Player, cards: list) -> list:
        return self.ai_agent.surveil_decision(game, player, cards)


# -------------------------------------------------------------------- 辅助函数

def card_grid(
    master: tk.Misc,
    cards: list[Any],
    columns: int = 6,
    show_count: bool = False,
    height: int = 320,
) -> tk.Frame:
    """带滚动条的卡图网格。cards 为 Card 对象列表（取 .data 渲染）。"""
    outer = tk.Frame(master, bg=PANEL)
    canvas = tk.Canvas(outer, bg=PANEL, highlightthickness=0, height=height)
    scroll = tk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    inner = tk.Frame(canvas, bg=PANEL)
    canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=scroll.set)
    canvas.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")

    def on_configure(_event: tk.Event) -> None:
        canvas.configure(scrollregion=canvas.bbox("all"))

    def on_wheel(event: tk.Event) -> None:
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    inner.bind("<Configure>", on_configure)
    canvas.bind("<MouseWheel>", on_wheel)

    for index, card in enumerate(cards):
        sub = getattr(card, "subtitle_hint", "") if show_count else ""
        widget = CardWidget(inner, card.data, compact=True, subtitle=sub)
        widget.grid(row=index // columns, column=index % columns, padx=3, pady=3)
        card_tooltip(widget, card.data, _card_description(card.data))
        widget.bind("<MouseWheel>", on_wheel)
        for child in widget.winfo_children():
            child.bind("<MouseWheel>", on_wheel)

    return outer


def _permanent_by_id(game: Game, perm_id: int) -> Any | None:
    for player in game.players:
        for perm in player.permanents:
            if id(perm) == perm_id:
                return perm
    return None


def _matches_target_kind(perm: Any, kind: str) -> bool:
    if kind in ("creature",):
        return perm.is_creature
    if kind == "permanent":
        return True
    if kind == "creature_or_planeswalker":
        return perm.is_creature or perm.is_planeswalker
    if kind in ("any", "creature_or_player"):
        return perm.is_creature or perm.is_planeswalker
    if kind == "artifact":
        return perm.is_artifact
    if kind == "enchantment":
        return perm.is_enchantment
    if kind == "land":
        return perm.is_land
    return True


def _ask_target(master: tk.Misc, title: str, options: list) -> Any | None:
    """弹窗让人类选一个目标。"""
    result: list[Any] = [None]
    dialog = tk.Toplevel(master)
    dialog.title(title)
    dialog.configure(bg=BG)
    dialog.geometry("440x380")
    dialog.transient(master)
    dialog.grab_set()

    tk.Label(dialog, text=title, bg=BG, fg=TEXT, font=FONT_BOLD, wraplength=400).pack(pady=(10, 6))

    listbox = tk.Listbox(dialog, font=FONT_NORMAL, height=12)
    listbox.pack(fill="both", expand=True, padx=12)
    for perm in options:
        owner = "我方" if perm.controller is getattr(master, "human", None) else "对手"
        extra = f"{perm.power()}/{perm.toughness()}" if perm.is_creature else ""
        listbox.insert("end", f"[{owner}] {perm.name} {extra}".strip())
    if options:
        listbox.selection_set(0)

    def confirm() -> None:
        sel = listbox.curselection()
        if sel:
            result[0] = options[sel[0]]
        dialog.destroy()

    def cancel() -> None:
        result[0] = None
        dialog.destroy()

    row = tk.Frame(dialog, bg=BG)
    row.pack(pady=8)
    tk.Button(row, text="确定", bg=BTN_ACCENT, fg=BTN_FG, font=FONT_BOLD, width=8, relief="flat",
              command=confirm).pack(side="left", padx=6)
    tk.Button(row, text="取消", bg=BTN_BG, fg=BTN_FG, font=FONT_NORMAL, width=8, relief="flat",
              command=cancel).pack(side="left", padx=6)

    dialog.wait_window()
    return result[0]


def _permanent_description(perm: Any) -> str:
    lines = [perm.name, perm.data.type_line_cn]
    if perm.is_creature:
        lines.append(f"攻防 {perm.power()}/{perm.toughness()}")
    if perm.is_planeswalker:
        lines.append(f"忠诚 {perm.loyalty}")
    if perm.keywords:
        lines.append("异能：" + "、".join(sorted(str(k.value) for k in perm.keywords)))
    if perm.abilities:
        texts = [a.describe() for a in perm.abilities if a.kind != "keyword"]
        if texts:
            lines.append("规则：" + " | ".join(t[:70] for t in texts))
    if perm.data.unparsed:
        lines.append("未实现：" + " | ".join(u[:60] for u in perm.data.unparsed))
    return "\n".join(lines)


def _card_description(data: Any) -> str:
    lines = [data.name, f"{data.mana_string}  （法术力值 {int(data.cmc)}）", data.type_line_cn]
    if data.is_creature:
        lines.append(f"攻防 {data.base_power}/{data.base_toughness}")
    if data.oracle_text:
        lines.append("")
        lines.append(data.oracle_text)
    if data.unparsed:
        lines.append("")
        lines.append("（部分异能本引擎未实现：" + "；".join(data.unparsed) + "）")
    return "\n".join(lines)


def _list_preset_decks() -> list[tuple[str, str]]:
    """列出 decks/ 下的预构筑套牌（路径, 显示名）。"""
    if not os.path.isdir(DECKS_DIR):
        return []
    import json

    out: list[tuple[str, str]] = []
    for filename in sorted(os.listdir(DECKS_DIR)):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(DECKS_DIR, filename)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            out.append((path, payload.get("name", filename[:-5])))
        except Exception:  # noqa: BLE001
            continue
    return out


def _colors_of_deck(deck: list) -> list[str]:
    """从套牌推断主色。"""
    tally: dict[str, int] = {}
    for data in deck:
        if data.is_land:
            continue
        for symbol in re.findall(r"\{([WUBRG])\}", data.mana_cost or ""):
            tally[symbol] = tally.get(symbol, 0) + 1
    ordered = sorted(tally.items(), key=lambda kv: -kv[1])
    return [c for c, _n in ordered[:2]] or ["G"]


def _opposite_colors(colors: list[str]) -> list[str]:
    """给电脑挑一套不同的颜色，增加对局变化。"""
    pairs = {
        ("W",): ["B", "R"],
        ("U",): ["R", "G"],
        ("B",): ["W", "G"],
        ("R",): ["U", "W"],
        ("G",): ["B", "U"],
    }
    key = tuple(sorted(colors))
    if key in pairs:
        return pairs[key]
    all_colors = ["W", "U", "B", "R", "G"]
    remaining = [c for c in all_colors if c not in colors]
    return remaining[:2] if remaining else ["R"]


def main() -> int:
    app = MTGApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
