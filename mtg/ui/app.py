"""tkinter 桌面主界面：组装骨架、推进引擎、分发决策。

渲染细节在 ``board.py``，弹窗在 ``dialogs.py``，日志在 ``log_panel.py``，
尺寸计算在 ``layout.py``。本文件只保留窗口组装与交互分发。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import Any

from ..ai.agent import HeuristicAgent
from ..cards.carddb import CardDB
from ..cards.decks import auto_build, load_deck
from ..engine.game import Action, Game
from ..engine.player import Player
from ..engine.types import Phase
from . import board, dialogs, layout
from .card_widget import set_art_enabled
from .images import PIL_AVAILABLE, get_cache
from .log_panel import LogPanel
from .theme import (
    AMBER,
    BG,
    BTN_BG,
    BTN_DANGER,
    BTN_FG,
    FONT_BOLD,
    FONT_NORMAL,
    FONT_SMALL,
    FONT_TITLE,
    OURS,
    PANEL,
    PANEL_DARK,
    TEXT,
    TEXT_DIM,
)

#: 双方信息条的固定高度
INFO_H = 22

#: 法术力颜色字母 → 中文（产费选色菜单用）
_MANA_CN = {"W": "白", "U": "蓝", "B": "黑", "R": "红", "G": "绿", "C": "无色"}


class MTGApp(tk.Tk):
    """主窗口。"""

    def __init__(self) -> None:
        super().__init__()
        self.title("MTGO — 万智牌标准赛制（本地对战）")
        self.configure(bg=BG)
        width, height = layout.window_size(self.winfo_screenwidth(), self.winfo_screenheight())
        self.geometry(f"{width}x{height}")
        self.minsize(*layout.WINDOW_MIN)

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

        self._resize_job: str | None = None
        self._log_user_set = False

        self._build_shell()
        self.after(100, self._bootstrap)

    # ================================================================ 骨架
    def _build_shell(self) -> None:
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # 顶部固定条：左侧回合·阶段条，右侧卡池信息与功能按钮
        self.header = tk.Frame(self, bg=PANEL_DARK, height=layout.FIXED_HEIGHTS["header"])
        self.header.grid(row=0, column=0, sticky="ew")
        self.header.pack_propagate(False)

        # 右侧功能按钮（先放，窄窗时优先保留）
        tk.Button(
            self.header, text="新对局", bg=BTN_BG, fg=BTN_FG, font=FONT_NORMAL, relief="flat",
            command=self.new_game_dialog,
        ).pack(side="right", padx=6, pady=6)
        tk.Button(
            self.header, text="牌库/坟场", bg=BTN_BG, fg=BTN_FG, font=FONT_NORMAL, relief="flat",
            command=self.show_zones,
        ).pack(side="right", padx=4, pady=6)
        tk.Button(
            self.header, text="卡图", bg=BTN_BG, fg=BTN_FG, font=FONT_NORMAL, relief="flat",
            command=self.art_menu,
        ).pack(side="right", padx=4, pady=6)
        tk.Checkbutton(
            self.header, text="自动让过", variable=self.auto_pass, bg=PANEL_DARK, fg=TEXT,
            font=FONT_NORMAL, selectcolor=PANEL,
        ).pack(side="right", padx=8)
        # 卡池 / 卡图状态（原先在左侧，现固定到顶部右）
        self.status_var = tk.StringVar(value="正在加载卡池…")
        tk.Label(self.header, textvariable=self.status_var, bg=PANEL_DARK, fg=TEXT_DIM,
                 font=FONT_SMALL).pack(side="right", padx=12)

        # 左侧：回合·阶段条（回合信息 70% / 当前阶段 30%）
        self.turnbar = tk.Frame(self.header, bg=PANEL_DARK)
        self.turnbar.pack(side="left", fill="both", expand=True, padx=12)
        self.turnbar.grid_columnconfigure(0, weight=7)
        self.turnbar.grid_columnconfigure(1, weight=3)
        self.turnbar.grid_rowconfigure(0, weight=1)
        turn_box = tk.Frame(self.turnbar, bg=PANEL_DARK)
        turn_box.grid(row=0, column=0, sticky="w")
        self.turn_label = tk.Label(turn_box, text="", bg=PANEL_DARK, fg=TEXT, font=FONT_TITLE)
        self.turn_label.pack(side="left")
        self.side_label = tk.Label(turn_box, text="", bg=PANEL_DARK, fg=OURS, font=FONT_BOLD)
        self.side_label.pack(side="left", padx=(8, 0))
        self.phase_label = tk.Label(self.turnbar, text="", bg=PANEL_DARK, fg=AMBER, font=FONT_BOLD)
        self.phase_label.grid(row=0, column=1, sticky="w")

        # 主体：左牌桌 + 右日志
        body = tk.Frame(self, bg=BG)
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)

        self.board_frame = tk.Frame(body, bg=BG)
        self.board_frame.grid(row=0, column=0, sticky="nsew")
        self.board_frame.grid_columnconfigure(0, weight=1)

        self.log = LogPanel(body, on_toggle=self._on_log_toggle)
        self.log.grid(row=0, column=1, sticky="ns")

        # 牌桌各行：对手 → 中条 → 我方 → 手牌 → 操作条
        self.opp_row = tk.Frame(self.board_frame, bg=BG)
        self.opp_row.grid(row=0, column=0, sticky="nsew")
        self.opp_row.pack_propagate(False)
        self.opp_info = tk.Frame(self.opp_row, bg=PANEL, height=INFO_H)
        self.opp_info.pack(fill="x", padx=8, pady=(6, 0))
        self.opp_info.pack_propagate(False)
        self.opp_board = tk.Frame(self.opp_row, bg=PANEL_DARK)
        self.opp_board.pack(fill="both", expand=True, padx=8, pady=(2, 4))

        self.mid_bar = tk.Frame(self.board_frame, bg=PANEL)
        self.mid_bar.grid(row=1, column=0, sticky="nsew")
        self.mid_bar.pack_propagate(False)

        self.mine_row = tk.Frame(self.board_frame, bg=BG)
        self.mine_row.grid(row=2, column=0, sticky="nsew")
        self.mine_row.pack_propagate(False)
        self.my_board = tk.Frame(self.mine_row, bg=PANEL_DARK)
        self.my_board.pack(fill="both", expand=True, padx=8, pady=(4, 2))
        self.my_info = tk.Frame(self.mine_row, bg=PANEL, height=INFO_H)
        self.my_info.pack(fill="x", padx=8, pady=(0, 2))
        self.my_info.pack_propagate(False)

        self.hand_frame = tk.Frame(self.board_frame, bg=PANEL)
        self.hand_frame.grid(row=3, column=0, sticky="nsew")
        self.hand_frame.pack_propagate(False)

        self.action_bar = tk.Frame(self.board_frame, bg=PANEL_DARK)
        self.action_bar.grid(row=4, column=0, sticky="nsew")
        self.action_bar.pack_propagate(False)
        # 操作条三段：左主提示 / 中上下文按钮 / 右快捷键提示与错误反馈
        self.action_left = tk.Frame(self.action_bar, bg=PANEL_DARK)
        self.action_left.pack(side="left", fill="y", padx=(10, 0))
        self.action_right = tk.Frame(self.action_bar, bg=PANEL_DARK)
        self.action_right.pack(side="right", fill="y", padx=(0, 10))
        self.action_center = tk.Frame(self.action_bar, bg=PANEL_DARK)
        self.action_center.pack(side="left", fill="both", expand=True)
        self._error_job: str | None = None

        self._row_frames = {
            "opp": self.opp_row,
            "mid": self.mid_bar,
            "mine": self.mine_row,
            "hand": self.hand_frame,
            "action": self.action_bar,
        }

        self.bind("<Configure>", self._on_configure)
        self._bind_shortcuts()
        self.after(60, self._apply_window_layout)

    def _on_configure(self, event: tk.Event) -> None:
        if event.widget is not self:
            return
        if self._resize_job is not None:
            try:
                self.after_cancel(self._resize_job)
            except tk.TclError:
                pass
        self._resize_job = self.after(120, self._apply_window_layout)

    def _apply_window_layout(self) -> None:
        self._resize_job = None
        height = self.winfo_height()
        if height <= 1:
            return
        heights = layout.row_heights(height)
        self.header.configure(height=heights["header"])
        for row, key in enumerate(("opp", "mid", "mine", "hand", "action")):
            self.board_frame.grid_rowconfigure(row, minsize=heights[key], weight=0)
            self._row_frames[key].configure(height=heights[key])
        self._maybe_auto_collapse()

    def _maybe_auto_collapse(self) -> None:
        """窄窗口默认折叠日志，用户手动切换后以其选择为准。"""
        if self._log_user_set:
            return
        want = self.winfo_width() < 1200
        if self.log.collapsed != want:
            self.log.set_collapsed(want)

    def _on_log_toggle(self, _collapsed: bool) -> None:
        self._log_user_set = True

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
        dialogs.new_game_dialog(self, first)

    def show_zones(self) -> None:
        dialogs.show_zones(self)

    def show_library(self) -> None:
        dialogs.show_library(self)

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
            deck_colors = dialogs.colors_of_deck(my_deck)
            deck_name = next((n for p, n in dialogs.list_preset_decks() if p == deck_path), "预构筑套牌")
        else:
            my_deck = auto_build(self.db.cards, colors or ["G"], size=60, seed=seed)
            deck_colors = list(colors or ["G"])
            deck_name = "自动构筑"

        # 电脑用一套不同的颜色，保证对局有变化
        ai_colors = dialogs.opposite_colors(deck_colors)
        ai_deck = auto_build(self.db.cards, ai_colors, size=60, seed=seed + 7777)

        self.my_colors = deck_colors
        self.ai_colors = ai_colors
        self.deck_name = deck_name

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

        self.log.clear()

        self.selected_attackers.clear()
        self.block_assignments.clear()
        self.focused_attacker = None
        self.pending = None

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

    # ================================================================ 启动式异能
    def _on_permanent_right_click(self, perm: Any) -> None:
        """右键我方永久物：弹出其启动式异能菜单（左键仍用于横置产费）。"""
        game = self.game
        if game is None or self.human is None or game.game_over:
            return
        if perm.controller is not self.human:
            return
        from ..engine.payment import _activated_abilities

        options = [(i, ab) for i, ab in enumerate(_activated_abilities(perm))
                   if not ab.is_mana_ability]
        if not options:
            self.flash_error(f"{perm.name} 没有可启动的异能")
            return
        if self.pending is None or self.pending.kind != "priority":
            self.flash_error(f"现在不是启动 {perm.name} 异能的时机")
            return
        self._show_ability_menu(perm, options)

    def _show_ability_menu(self, perm: Any, options: list[tuple[int, Any]]) -> None:
        """异能菜单：显示费用与文本，不可用的置灰并注明原因。"""
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label=perm.name, state="disabled")
        menu.add_separator()
        for idx, ability in options:
            ok, reason = self._ability_available(perm, ability, idx)
            text = ability.text or "异能"
            if len(text) > 34:
                text = text[:33] + "…"
            label = f"{ability.cost.describe()}：{text}"
            menu.add_command(
                label=label if ok else f"{label}（{reason}）",
                state="normal" if ok else "disabled",
                command=lambda i=idx: self._activate_perm_ability(perm, i),
            )
        try:
            menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())
        finally:
            menu.grab_release()

    def _ability_available(self, perm: Any, ability: Any, index: int = -1) -> tuple[bool, str]:
        """异能此刻能否启动：法术时机 + 费用是否够。"""
        game = self.game
        if game is None or self.human is None:
            return False, "不可启动"
        cost = ability.cost
        if cost.once_per_turn and index in getattr(perm, "activated_this_turn", ()):
            return False, "本回合已用过"
        if cost.sorcery_timing and (
            game.active_player is not self.human
            or game.stack
            or game.phase not in (Phase.MAIN_1, Phase.MAIN_2)
        ):
            return False, "只能于法术时机"
        if cost.tap and perm.tapped:
            return False, "已横置"
        if cost.tap and perm.is_creature and perm.is_sick:
            return False, "召唤失调"
        from ..engine.payment import could_pay

        if not could_pay(game, perm.controller, cost.mana):
            return False, "法术力不足"
        if cost.life and perm.controller.life < cost.life:
            return False, "生命不足"
        if cost.discard and len(perm.controller.hand) < cost.discard:
            return False, "手牌不足"
        if cost.remove_counters:
            kind, amount = cost.remove_counters
            if perm.counters.get(kind, 0) < amount:
                return False, "指示物不足"
        if cost.sacrifice_other:
            has = any(
                p is not perm and game._matches_target_spec(p, cost.sacrifice_other)
                for p in perm.controller.permanents
            )
            if not has:
                return False, "没有可牺牲的永久物"
        return True, ""

    def _activate_perm_ability(self, perm: Any, ability_index: int) -> None:
        game = self.game
        if game is None or self.human is None:
            return
        from ..engine.payment import _activated_abilities

        activated = _activated_abilities(perm)
        if ability_index >= len(activated):
            return
        cost = activated[ability_index].cost

        # 1) 需要目标的异能先选目标
        targets = self._choose_ability_targets(perm, ability_index)
        if targets is None:
            return  # 用户取消了目标选择

        # 2) 需要主动选择的额外费用（弃牌 / 牺牲其他）
        choices: dict[str, Any] = {}
        if cost.discard:
            picked = dialogs.ask_cards(
                self, f"{perm.name} — 弃 {cost.discard} 张牌", list(self.human.hand),
                cost.discard, human=self.human,
            )
            if picked is None:
                return  # 用户取消了弃牌选择
            choices["discard"] = picked
        if cost.sacrifice_other:
            candidates = game._sacrifice_candidates(perm.controller, perm, cost.sacrifice_other)
            if not candidates:
                self.flash_error("没有可牺牲的永久物")
                return
            picked = dialogs.ask_target(
                self, f"{perm.name} — 牺牲{cost.sacrifice_other.describe()}", candidates,
                human=self.human,
            )
            if picked is None:
                return  # 用户取消了牺牲选择
            choices["sacrifice"] = [picked]

        game.submit(Action(kind="activate", permanent=perm,
                           ability_index=ability_index, targets=targets,
                           payload={"choices": choices}))
        self.pending = None
        self.pump()

    def _choose_ability_targets(self, perm: Any, ability_index: int) -> list | None:
        """为需要目标的异能选择目标。返回 None 表示取消。"""
        from ..engine.payment import _activated_abilities

        activated = _activated_abilities(perm)
        if ability_index >= len(activated):
            return []
        specs = list(activated[ability_index].targets)
        if not specs:
            return []

        game = self.game
        if game is None or self.human is None:
            return None
        opponent = game.other_player(self.human)

        chosen_list: list[Any] = []
        for spec in specs:
            kind = getattr(spec, "kind", "any")
            if kind == "player":
                pool: list[Any] = [opponent]
            elif kind == "spell":
                pool = [game.stack.top] if game.stack.top else []
            else:
                pool = [p for player in game.players for p in player.permanents
                        if board.matches_target_kind(p, kind)]
            controller = getattr(spec, "controller", "any")
            if controller == "you":
                pool = [p for p in pool if getattr(p, "controller", None) is self.human]
            elif controller == "opponent":
                pool = [p for p in pool if getattr(p, "controller", None) is not self.human]
            if not pool:
                if getattr(spec, "optional", False):
                    continue
                self.flash_error("没有合法目标")
                return None
            chosen = dialogs.ask_target(
                self, f"{perm.name} — 选择目标（{spec.describe()}）", pool, human=self.human,
            )
            if chosen is None:
                return None
            chosen_list.append(chosen)
        return chosen_list

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
        self.log.append(message)

    # ================================================================ 反馈与快捷键
    def flash_error(self, message: str) -> None:
        """在操作条右侧显示红色提示，2 秒后恢复快捷键提示。"""
        if self._error_job is not None:
            try:
                self.after_cancel(self._error_job)
            except tk.TclError:
                pass
            self._error_job = None
        board.clear(self.action_right)
        tk.Label(self.action_right, text=message, bg=PANEL_DARK, fg=BTN_DANGER,
                 font=FONT_BOLD).pack(side="right")
        self._error_job = self.after(2000, self._clear_error)

    def _clear_error(self) -> None:
        self._error_job = None
        board.clear(self.action_right)
        board.render_action_hints(self)

    def _bind_shortcuts(self) -> None:
        """绑定全局快捷键；焦点在输入/按钮控件时不触发。"""
        self.bind("<space>", self._key_space)
        self.bind("<Return>", self._key_return)
        self.bind("<Escape>", self._key_escape)
        self.bind("<KeyPress-a>", self._key_select_all)
        self.bind("<KeyPress-A>", self._key_select_all)
        self.bind("<Control-Return>", self._key_end_phase)
        self.bind("<KeyPress-n>", self._key_skip_idle)
        self.bind("<KeyPress-N>", self._key_skip_idle)

    def _shortcuts_enabled(self) -> bool:
        """焦点不在输入型或按钮控件上时才响应快捷键。"""
        widget = self.focus_get()
        if widget is None:
            return True
        blocked = ("Entry", "Text", "TCombobox", "Spinbox", "Button",
                   "Checkbutton", "Listbox", "Scrollbar")
        return widget.winfo_class() not in blocked

    def _key_space(self, _event: tk.Event) -> str | None:
        if not self._shortcuts_enabled() or self.pending is None:
            return None
        kind = self.pending.kind
        if kind == "priority":
            self._pass()
        elif kind == "declare_attackers":
            self._submit_declarations([])  # 不攻击
        elif kind == "declare_blockers":
            self._declare_no_blockers()
        return "break"

    def _key_return(self, _event: tk.Event) -> str | None:
        if not self._shortcuts_enabled() or self.pending is None:
            return None
        kind = self.pending.kind
        if kind == "declare_attackers":
            self._confirm_attackers()
        elif kind == "declare_blockers":
            self._confirm_blockers()
        else:
            return None
        return "break"

    def _key_escape(self, _event: tk.Event) -> str | None:
        if not self._shortcuts_enabled() or self.pending is None:
            return None
        kind = self.pending.kind
        if kind == "declare_attackers" and self.selected_attackers:
            self.selected_attackers.clear()
            self.render()
        elif kind == "declare_blockers" and (self.block_assignments or self.focused_attacker):
            self._clear_blocks()
        else:
            return None
        return "break"

    def _key_select_all(self, _event: tk.Event) -> str | None:
        if not self._shortcuts_enabled() or self.pending is None:
            return None
        if self.pending.kind != "declare_attackers":
            return None
        self._select_all_attackers()
        return "break"

    def _key_end_phase(self, _event: tk.Event) -> str | None:
        if not self._shortcuts_enabled() or self.pending is None:
            return None
        if self.pending.kind != "priority":
            return None
        self._pass_phase()
        return "break"

    def _key_skip_idle(self, _event: tk.Event) -> str | None:
        if not self._shortcuts_enabled() or self.pending is None:
            return None
        if self.pending.kind != "priority":
            return None
        self._skip_idle_phases()
        return "break"

    # ================================================================ 快进
    def _has_meaningful_action(self) -> bool:
        """当前时机玩家是否有可做的操作：能施放的咒语，或主阶段能下地。"""
        game = self.game
        human = self.human
        if game is None or human is None:
            return False
        for card in human.hand:
            if card.data.is_land:
                continue
            ok, _ = game.can_cast(human, card)
            if ok:
                return True
        in_my_main = (
            game.active_player is human
            and not game.stack
            and game.phase in (Phase.MAIN_1, Phase.MAIN_2)
        )
        if in_my_main and human.can_play_land() and any(c.data.is_land for c in human.hand):
            return True
        # 主阶段里还有可启动的非产费异能，也算"有正事"
        if in_my_main:
            from ..engine.payment import _activated_abilities

            for perm in human.permanents:
                for ability in _activated_abilities(perm):
                    if ability.is_mana_ability:
                        continue
                    if self._ability_available(perm, ability)[0]:
                        return True
        return False

    def _skip_idle_phases(self) -> None:
        """一键快进：连续让过没有可操作内容的阶段，停在下一个需要你操作的时刻。

        遇到需要宣告攻击者/阻挡者的决策，或你手上有可施放的咒语（含瞬间）、
        主阶段有地可下时停下。
        """
        game = self.game
        if game is None or self.human is None:
            return
        if self.pending is None or self.pending.kind != "priority" or self.pending.player is not self.human:
            return
        guard = 0
        while guard < 200:
            guard += 1
            if game.game_over or self._has_meaningful_action():
                break
            game.submit(Action(kind="pass"))
            decision = game.advance()
            if decision is None or game.game_over:
                self.pending = None
                self.render()
                if game.game_over:
                    self._show_game_over()
                return
            if decision.player is not self.human:
                self._ai_act(decision)
                continue
            self.pending = decision
            if decision.kind != "priority":
                break  # 需要你宣告攻击/阻挡
        self.render()

    # ================================================================ 渲染
    def render(self) -> None:
        board.render(self)

    # ================================================================ 人类交互
    def _on_hand_click(self, card: Any) -> None:
        game = self.game
        if game is None or self.human is None or game.game_over:
            return
        if self.pending is None or self.pending.kind != "priority":
            self.flash_error("现在不是你可以出牌的时机")
            return
        ok, reason = self._is_playable(card)
        if not ok:
            self.flash_error(f"{card.name}：{reason}")
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
                    if board.matches_target_kind(perm, kind):
                        pool.append(perm)
            if not pool:
                continue
            chosen = dialogs.ask_target(self, f"{card.name} — 选择目标（{spec.describe()}）",
                                        pool, human=self.human)
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
                    self.flash_error(f"{perm.name}：{reason}")
                    return
                self.selected_attackers.add(key)
            self.render()
            return

        if kind == "declare_blockers":
            if perm.controller is self.human:
                # 我方生物：分配给当前选中的攻击者
                if self.focused_attacker is None:
                    self.flash_error("请先点击要阻挡的对手生物")
                    return
                blockers = self.block_assignments.setdefault(self.focused_attacker, [])
                key = id(perm)
                if key in blockers:
                    blockers.remove(key)
                else:
                    ok, reason = game.combat.can_block(perm, board.permanent_by_id(game, self.focused_attacker))
                    if not ok:
                        self.flash_error(f"{perm.name}：{reason}")
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
        """手动横置一个永久物产费；多色可选时弹出选色菜单。"""
        game = self.game
        if game is None:
            return
        if perm.tapped:
            self.flash_error(f"{perm.name} 已横置")
            return
        from ..engine.payment import _ability_colors, _activated_abilities

        options: list[tuple[str, int]] = []  # (颜色, 异能序号)
        for idx, ability in enumerate(_activated_abilities(perm)):
            if not ability.is_mana_ability:
                continue
            for color in _ability_colors(ability):
                if all(color != chosen for chosen, _ in options):
                    options.append((color, idx))
        if not options:
            self.flash_error(f"{perm.name} 没有可以产费的异能")
            return
        # 只有"多个可选异能"才需要让用户选；单一异能即使能产多色，引擎也是一次性结算
        if len({idx for _, idx in options}) == 1:
            game.activate_ability(perm, options[0][1], [])
            self.render()
            return
        self._show_mana_menu(perm, options)

    def _show_mana_menu(self, perm: Any, options: list[tuple[str, int]]) -> None:
        """在鼠标位置弹出产费选色菜单。"""
        menu = tk.Menu(self, tearoff=0)
        for color, idx in options:
            menu.add_command(
                label=f"加 {_MANA_CN.get(color, color)}色法术力",
                command=lambda i=idx: self._activate_mana(perm, i),
            )
        try:
            menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())
        finally:
            menu.grab_release()

    def _activate_mana(self, perm: Any, ability_index: int) -> None:
        if self.game is None:
            return
        self.game.activate_ability(perm, ability_index, [])
        self.render()

    def _is_playable(self, card: Any) -> tuple[bool, str]:
        """能否在此时机打出，返回 (是否可打, 简短原因)。"""
        game = self.game
        assert game is not None and self.human is not None
        if card.data.is_land:
            if self.human.can_play_land():
                return True, ""
            return False, "本回合已下地"
        return game.can_cast(self.human, card)

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
            blockers = [board.permanent_by_id(game, bid) for bid in blocker_ids]
            blockers = [b for b in blockers if b is not None]
            if blockers:
                assignments[attacker] = blockers
        self.block_assignments.clear()
        self.focused_attacker = None
        self.game.submit(Action(kind="blockers", payload={"assignments": assignments}))
        self.pending = None
        self.pump()

    def _declare_no_blockers(self) -> None:
        """宣布不阻挡：丢弃已分配，提交空阻挡声明。"""
        if self.game is None:
            return
        self.block_assignments.clear()
        self.focused_attacker = None
        self.game.submit(Action(kind="blockers", payload={"assignments": {}}))
        self.pending = None
        self.pump()

    def _show_game_over(self) -> None:
        dialogs.show_game_over(self)

    # ---- 引擎回调（占卜/侦察时由引擎询问）
    def scry_decision(self, game: Game, player: Player, cards: list) -> list:
        return self.ai_agent.scry_decision(game, player, cards)

    def surveil_decision(self, game: Game, player: Player, cards: list) -> list:
        return self.ai_agent.surveil_decision(game, player, cards)


def main() -> int:
    app = MTGApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())