"""牌桌渲染：卡片流容器（自动选档/换行/滚动）与各区域渲染函数。

渲染函数显式接收 ``app``（MTGApp 实例）作为上下文，避免隐式 self 依赖。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

from .card_widget import CardWidget, card_tooltip
from .dialogs import card_description
from .layout import columns_for, pick_tier
from .theme import (
    AMBER,
    ATTACKING,
    BLOCKING,
    BTN_ACCENT,
    BTN_BG,
    BTN_DANGER,
    BTN_FG,
    FONT_BOLD,
    FONT_NORMAL,
    FONT_PROMPT,
    FONT_SMALL,
    FONT_TITLE,
    HIGHLIGHT,
    OURS,
    PANEL,
    PANEL_DARK,
    TEXT,
    TEXT_DIM,
    THEIRS,
)

#: 颜色字母 → 中文，用于中条套牌信息
_COLOR_CN = {"W": "白", "U": "蓝", "B": "黑", "R": "红", "G": "绿"}

#: 不可出牌原因 → 角标短标签（按关键词匹配）
_REASON_SHORT = (
    ("法术力不足", "法术力不足"),
    ("时机", "需法术时机"),
    ("不在手牌", "不在手牌"),
    ("已下地", "已下地"),
)


def short_reason(reason: str) -> str:
    """把 ``can_cast`` 的长原因压成适合角标的短标签。"""
    for key, short in _REASON_SHORT:
        if key in reason:
            return short
    return reason[:6] if reason else "不可用"


class CardStrip(tk.Frame):
    """卡片容器：按可用宽度自动选尺寸档与列数，内容超高时可滚轮查看。

    - 列数变化：只重新排布已有卡片（开销小）。
    - 档位变化（S/M/L/XL）：按 ``builder`` 以新尺寸重建卡片。
    """

    def __init__(self, master: tk.Misc, bg: str, min_columns: int = 4) -> None:
        super().__init__(master, bg=bg)
        self.min_columns = min_columns
        # canvas 的请求尺寸压到最小，避免反过来把父容器撑大（高度由父容器说了算）
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0, width=1, height=1)
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self._on_scroll_set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bar.pack(side="right", fill="y")
        self._items: list[Any] = []
        self._builder: Callable[[tk.Misc, Any, str], Any] | None = None
        self._widgets: list[Any] = []
        self._columns = 0
        self._tier = ""
        self.bar_visible = True
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)

    # ---------------------------------------------------------------- 内容
    def set_items(self, items: list[Any], builder: Callable[[tk.Misc, Any, str], Any]) -> None:
        """全量替换内容。builder(容器, item, 尺寸档) 负责造出一张卡。"""
        self._items = list(items)
        self._builder = builder
        avail = self.canvas.winfo_width()
        if avail <= 1:
            avail = self._fallback_width()
        self._tier = ""  # 强制重建
        self._relayout(avail)

    def _fallback_width(self) -> int:
        try:
            return max(self.winfo_width(), self.master.winfo_width())
        except tk.TclError:
            return 0

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
        self._relayout(event.width)

    def _relayout(self, avail_w: int) -> None:
        tier = pick_tier(avail_w, self.min_columns)
        columns = columns_for(avail_w, tier)
        changed_tier = tier != self._tier
        if changed_tier:
            self._tier = tier
        self._columns = columns
        if changed_tier or not self._widgets:
            self._rebuild()
        else:
            self._grid()

    def _rebuild(self) -> None:
        for widget in self._widgets:
            widget.destroy()
        self._widgets = []
        if self._builder is None:
            return
        for item in self._items:
            widget = self._builder(self.inner, item, self._tier)
            if widget is not None:
                self._widgets.append(widget)
        self._grid()

    def _grid(self) -> None:
        columns = max(1, self._columns)
        for index, widget in enumerate(self._widgets):
            widget.grid(row=index // columns, column=index % columns, padx=2, pady=2)

    def _scroll(self, event: tk.Event) -> None:
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


# ==================================================================== 渲染入口
def render(app: Any) -> None:
    """重绘整张牌桌。"""
    if app.game is None:
        return
    render_header(app)
    render_info(app, app.opp_info, app.ai, is_opponent=True)
    render_info(app, app.my_info, app.human, is_opponent=False)
    render_board(app, app.opp_board, app.ai, is_mine=False)
    render_board(app, app.my_board, app.human, is_mine=True)
    render_mid(app)
    render_hand(app)
    render_actions(app)


def clear(frame: tk.Frame) -> None:
    for child in frame.winfo_children():
        child.destroy()


# -------------------------------------------------------------------- 回合·阶段条
def render_header(app: Any) -> None:
    """顶部条：左侧回合归属（我方蓝 / 对手红），右侧当前阶段（金色高亮）。"""
    game = app.game
    if game is None:
        app.turn_label.configure(text="")
        app.side_label.configure(text="", fg=TEXT_DIM)
        app.phase_label.configure(text="")
        return
    active = game.active_player
    is_mine = active is app.human
    app.turn_label.configure(text=f"第 {game.turn_number} 回合")
    app.side_label.configure(text="· 我方" if is_mine else "· 对手",
                             fg=OURS if is_mine else THEIRS)
    phase_text = game.phase.value
    if game.step:
        phase_text += f" · {game.step.value}"
    app.phase_label.configure(text=phase_text, fg=AMBER)


# -------------------------------------------------------------------- 信息条
def render_info(app: Any, frame: tk.Frame, player: Any, is_opponent: bool) -> None:
    clear(frame)
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
    side = "对手" if is_opponent else "我方"
    tk.Label(frame, text=f"（{side}）", bg=PANEL, fg=TEXT_DIM, font=FONT_SMALL).pack(side="left")


# -------------------------------------------------------------------- 战场
def render_board(app: Any, frame: tk.Frame, player: Any, is_mine: bool) -> None:
    clear(frame)
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
        strip = CardStrip(section, bg=PANEL_DARK, min_columns=4)
        strip.pack(fill="both", expand=True)
        strip.set_items(items, lambda cont, perm, size: permanent_widget(app, cont, perm, is_mine, size))


def permanent_widget(app: Any, parent: tk.Misc, perm: Any, is_mine: bool, size: str) -> Any:
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

    widget = CardWidget(parent, data, subtitle=subtitle, compact=True, badge=badge,
                        size=size, dimmed=perm.tapped,
                        on_click=lambda: app._on_permanent_click(perm),
                        on_right_click=(lambda: app._on_permanent_right_click(perm)) if is_mine else None)

    # 战斗标记
    if is_mine and id(perm) in app.selected_attackers:
        widget.set_selected(True)
        widget.inner.configure(highlightbackground=ATTACKING)
    if not is_mine and app.focused_attacker is not None and id(perm) == app.focused_attacker:
        widget.inner.configure(highlightbackground=HIGHLIGHT)
    if not is_mine and any(id(perm) in v for v in app.block_assignments.values()):
        widget.inner.configure(highlightbackground=BLOCKING)

    desc = permanent_description(perm)
    if is_mine and has_nonmana_ability(perm):
        desc = f"{desc}\n右键：启动异能"
    card_tooltip(widget, data, desc)
    return widget


def has_nonmana_ability(perm: Any) -> bool:
    """该永久物是否有可由玩家主动启动的非产费异能。"""
    for ability in getattr(perm, "abilities", []) or []:
        if ability.kind == "activated" and ability.activated and not ability.activated.is_mana_ability:
            return True
    return False


def _colors_cn(colors: Any) -> str:
    if not colors:
        return "-"
    return "".join(_COLOR_CN.get(c, c) for c in colors)


# -------------------------------------------------------------------- 中条
def render_mid(app: Any) -> None:
    clear(app.mid_bar)
    game = app.game
    assert game is not None

    # 左：套牌信息（回合与阶段已在顶部条显示，这里不重复）
    left = tk.Frame(app.mid_bar, bg=PANEL)
    left.pack(side="left", fill="y", padx=8)
    my_colors = _colors_cn(getattr(app, "my_colors", None))
    ai_colors = _colors_cn(getattr(app, "ai_colors", None))
    deck_name = getattr(app, "deck_name", "") or "自动构筑"
    tk.Label(left, text=f"套牌 {deck_name}（{my_colors}）", bg=PANEL, fg=TEXT,
             font=FONT_BOLD).pack(anchor="w")
    if app.human is not None:
        tk.Label(left, text=f"牌库 {len(app.human.library)}", bg=PANEL, fg=TEXT_DIM,
                 font=FONT_SMALL).pack(anchor="w")
    if app.ai is not None:
        tk.Label(left, text=f"电脑套牌（{ai_colors}）· 牌库 {len(app.ai.library)}", bg=PANEL,
                 fg=TEXT_DIM, font=FONT_SMALL).pack(anchor="w")

    # 堆叠
    stack_frame = tk.Frame(app.mid_bar, bg=PANEL)
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
        tk.Label(app.mid_bar, text=f"攻击中：{names}", bg=PANEL, fg=ATTACKING, font=FONT_BOLD,
                 wraplength=380, justify="left").pack(side="right", padx=8)


# -------------------------------------------------------------------- 手牌
def render_hand(app: Any) -> None:
    clear(app.hand_frame)
    if app.game is None or app.human is None:
        return
    tk.Label(app.hand_frame, text=f"手牌（{len(app.human.hand)}）", bg=PANEL, fg=TEXT,
             font=FONT_BOLD).pack(anchor="w", padx=6)

    strip = CardStrip(app.hand_frame, bg=PANEL, min_columns=5)
    strip.pack(fill="both", expand=True, padx=6)
    strip.set_items(list(app.human.hand), lambda cont, card, size: hand_widget(app, cont, card, size))


def hand_widget(app: Any, parent: tk.Misc, card: Any, size: str) -> Any:
    ok, reason = app._is_playable(card)
    badge = None if ok else short_reason(reason)
    widget = CardWidget(parent, card.data, size=size, badge=badge,
                        badge_fg=BTN_DANGER, dimmed=not ok,
                        on_click=lambda c=card: app._on_hand_click(c))
    tip = card_description(card.data)
    if not ok and reason:
        tip = f"无法出牌：{reason}\n\n{tip}"
    card_tooltip(widget, card.data, tip)
    return widget


# -------------------------------------------------------------------- 操作条
#: 各决策下可用的快捷键提示（灰色小字，显示在操作条右侧）
_SHORTCUT_HINTS = {
    "priority": "空格 让过 · N 快进 · Ctrl+Enter 结束阶段",
    "declare_attackers": "A 全选 · Enter 确认 · Esc 清除 · 空格 不攻击",
    "declare_blockers": "Enter 确认 · Esc 清除 · 空格 不阻挡",
}


def render_action_hints(app: Any) -> None:
    """操作条右侧的快捷键提示。"""
    clear(app.action_right)
    decision = app.pending
    if decision is None:
        return
    hint = _SHORTCUT_HINTS.get(decision.kind)
    if hint:
        tk.Label(app.action_right, text=hint, bg=PANEL_DARK, fg=TEXT_DIM,
                 font=FONT_SMALL).pack(side="right")


def render_actions(app: Any) -> None:
    """操作条：左主提示 / 中上下文按钮 / 右快捷键提示。"""
    clear(app.action_left)
    clear(app.action_center)
    clear(app.action_right)
    if app.game is None:
        return

    if app.game.game_over:
        winner = app.game.winner
        tk.Label(app.action_left, text=f"对局结束 · 胜者：{winner.name if winner else '无'}",
                 bg=PANEL_DARK, fg=TEXT, font=FONT_TITLE).pack(side="left")
        tk.Button(app.action_center, text="再来一局", bg=BTN_ACCENT, fg=BTN_FG, font=FONT_BOLD,
                  relief="flat", command=lambda: app.new_game_dialog()).pack(side="left", padx=6)
        return

    decision = app.pending
    if decision is None:
        tk.Label(app.action_left, text="电脑思考中…", bg=PANEL_DARK, fg=TEXT_DIM,
                 font=FONT_NORMAL).pack(side="left")
        return

    kind = decision.kind
    if kind == "priority":
        tk.Label(app.action_left, text=decision.prompt, bg=PANEL_DARK, fg=TEXT,
                 font=FONT_PROMPT).pack(side="left")
        buttons = [
            ("让过", BTN_BG, FONT_BOLD, app._pass),
            ("跳过无事阶段", BTN_BG, FONT_NORMAL, app._skip_idle_phases),
            ("结束阶段", BTN_BG, FONT_NORMAL, app._pass_phase),
        ]
    elif kind == "declare_attackers":
        tk.Label(app.action_left, text="点击自己的生物选择攻击，然后确认",
                 bg=PANEL_DARK, fg=TEXT, font=FONT_PROMPT).pack(side="left")
        buttons = [
            ("全选", BTN_BG, FONT_NORMAL, app._select_all_attackers),
            ("确认攻击", BTN_ACCENT, FONT_BOLD, app._confirm_attackers),
            ("不攻击", BTN_BG, FONT_NORMAL, lambda: app._submit_declarations([])),
        ]
    elif kind == "declare_blockers":
        tk.Label(app.action_left, text="先点对手的攻击者，再点自己的生物进行阻挡",
                 bg=PANEL_DARK, fg=TEXT, font=FONT_PROMPT).pack(side="left")
        buttons = [
            ("确认阻挡", BTN_ACCENT, FONT_BOLD, app._confirm_blockers),
            ("清除", BTN_BG, FONT_NORMAL, app._clear_blocks),
            ("不阻挡", BTN_BG, FONT_NORMAL, app._declare_no_blockers),
        ]
    else:
        buttons = []

    for text, bg, font, command in buttons:
        tk.Button(app.action_center, text=text, bg=bg, fg=BTN_FG, font=font,
                  padx=6, relief="flat", command=command).pack(side="left", padx=3)

    render_action_hints(app)


# ==================================================================== 辅助
def permanent_by_id(game: Any, perm_id: int) -> Any | None:
    for player in game.players:
        for perm in player.permanents:
            if id(perm) == perm_id:
                return perm
    return None


def matches_target_kind(perm: Any, kind: str) -> bool:
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


def permanent_description(perm: Any) -> str:
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