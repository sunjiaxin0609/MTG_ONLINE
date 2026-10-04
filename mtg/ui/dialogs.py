"""所有弹窗：新对局、区域/牌库列表、目标选择、对局结束。"""
from __future__ import annotations

import json
import os
import re
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from ..cards.decks import deck_summary
from .card_widget import CardWidget, card_tooltip
from .images import get_cache
from .theme import (
    AMBER,
    BG,
    BTN_ACCENT,
    BTN_BG,
    BTN_FG,
    FONT_BOLD,
    FONT_NORMAL,
    FONT_SMALL,
    FONT_TITLE,
    PANEL,
    PANEL_DARK,
    TEXT,
    TEXT_DIM,
)

DECKS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "decks"
)

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


# -------------------------------------------------------------------- 新对局
def new_game_dialog(app: Any, first: bool = False) -> None:
    dialog = tk.Toplevel(app)
    dialog.title("开始新对局")
    dialog.configure(bg=BG)
    dialog.geometry("460x430")
    dialog.transient(app)
    dialog.grab_set()

    tk.Label(dialog, text="选择套牌", bg=BG, fg=TEXT, font=FONT_TITLE).pack(pady=(12, 4))

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
            app.start_game(colors=None, deck_path=path, aggression=agg_var.get())
            return
        label = color_var.get()
        colors = next(c for lbl, c in COLOR_CHOICES if lbl == label)
        app.start_game(colors, aggression=agg_var.get())

    def cancel() -> None:
        dialog.destroy()
        if first:
            app.destroy()

    btn_row = tk.Frame(dialog, bg=BG)
    btn_row.pack(pady=14)
    tk.Button(btn_row, text="开始", bg=BTN_ACCENT, fg=BTN_FG, font=FONT_BOLD, width=10,
              relief="flat", command=start).pack(side="left", padx=6)
    tk.Button(btn_row, text="取消", bg=BTN_BG, fg=BTN_FG, font=FONT_NORMAL, width=10,
              relief="flat", command=cancel).pack(side="left", padx=6)


# -------------------------------------------------------------------- 区域 / 牌库
def show_zones(app: Any) -> None:
    """查看双方坟场（卡图）与牌库概况。"""
    if app.game is None:
        return
    window = tk.Toplevel(app)
    window.title("区域 · 坟场")
    window.configure(bg=BG)
    window.geometry("760x620")

    for player in app.game.players:
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

    if app.human is not None:
        allcards = list(app.human.library) + list(app.human.hand) + list(app.human.graveyard)
        datas = [c.data for c in allcards]
        tk.Label(window, text="套牌概况", bg=BG, fg=TEXT, font=FONT_BOLD).pack(anchor="w", padx=12, pady=(10, 2))
        tk.Label(window, text=deck_summary(datas), bg=BG, fg=TEXT_DIM, font=FONT_SMALL,
                 justify="left").pack(anchor="w", padx=12)
        tk.Button(
            window, text="查看我的牌库剩余卡种（卡图）", bg=BTN_BG, fg=BTN_FG, font=FONT_NORMAL,
            relief="flat", command=lambda: show_library(app),
        ).pack(anchor="w", padx=12, pady=8)


def show_library(app: Any) -> None:
    """用卡图列出我牌库里还剩哪些卡种（不泄露抽取顺序）。"""
    if app.human is None:
        return
    grouped: dict[str, list[Any]] = {}
    for card in app.human.library:
        grouped.setdefault(card.name, []).append(card)
    if not grouped:
        messagebox.showinfo("牌库", "牌库已空")
        return

    window = tk.Toplevel(app)
    window.title(f"我的牌库 · 剩余 {len(app.human.library)} 张 / {len(grouped)} 种")
    window.configure(bg=BG)
    window.geometry("780x600")
    tk.Label(window, text="同一种只列一张，括号里是剩余张数", bg=BG, fg=TEXT_DIM,
             font=FONT_SMALL).pack(anchor="w", padx=12, pady=(8, 0))

    unique = [cards[0] for cards in grouped.values()]
    for card in unique:
        card.subtitle_hint = f"×{len(grouped[card.name])}"
    grid = card_grid(window, unique, columns=6, show_count=True)
    grid.pack(fill="both", expand=True, padx=10, pady=8)

    get_cache().prefetch([c.data for c in unique], root=app)


# -------------------------------------------------------------------- 对局结束
def show_game_over(app: Any) -> None:
    if app.game is None:
        return
    winner = app.game.winner
    text = f"胜者：{winner.name}" if winner else "平局"
    app._append_log(f"=== 对局结束 · {text} ===")
    messagebox.showinfo("对局结束", f"{text}\n共进行了 {app.game.turn_number} 个回合")


# -------------------------------------------------------------------- 目标选择
def ask_target(master: tk.Misc, title: str, options: list, human: Any = None) -> Any | None:
    """弹窗让人类选一个目标。返回 None 表示取消。"""
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
    owner_of = human if human is not None else getattr(master, "human", None)
    for perm in options:
        owner = "我方" if perm.controller is owner_of else "对手"
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


# -------------------------------------------------------------------- 弃牌选择
def ask_cards(
    master: tk.Misc, title: str, cards: list, count: int, human: Any = None
) -> list | None:
    """弹窗让人类从手牌中选出 ``count`` 张。返回 None 表示取消。"""
    if count <= 0:
        return []
    result: list[Any] = [None]
    dialog = tk.Toplevel(master)
    dialog.title(title)
    dialog.configure(bg=BG)
    dialog.geometry("460x420")
    dialog.transient(master)
    dialog.grab_set()

    tk.Label(
        dialog, text=f"{title}（需选 {count} 张）", bg=BG, fg=TEXT, font=FONT_BOLD, wraplength=420,
    ).pack(pady=(10, 6))

    listbox = tk.Listbox(
        dialog, font=FONT_NORMAL, height=14, selectmode="extended", exportselection=False,
    )
    listbox.pack(fill="both", expand=True, padx=12)
    for card in cards:
        listbox.insert("end", card.name)

    status = tk.Label(dialog, text=f"已选 0/{count}", bg=BG, fg=TEXT_DIM, font=FONT_SMALL)
    status.pack(pady=(4, 0))

    def refresh(_event: tk.Event | None = None) -> None:
        chosen = len(listbox.curselection())
        status.config(text=f"已选 {chosen}/{count}", fg=AMBER if chosen != count else TEXT_DIM)

    listbox.bind("<<ListboxSelect>>", refresh)

    def confirm() -> None:
        sel = list(listbox.curselection())
        if len(sel) != count:
            status.config(text=f"请选择 {count} 张（当前 {len(sel)}）", fg=AMBER)
            return
        result[0] = [cards[i] for i in sel]
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


# -------------------------------------------------------------------- 卡图网格
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
        card_tooltip(widget, card.data, card_description(card.data))
        widget.bind("<MouseWheel>", on_wheel)
        for child in widget.winfo_children():
            child.bind("<MouseWheel>", on_wheel)

    return outer


# -------------------------------------------------------------------- 文字描述
def card_description(data: Any) -> str:
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


# -------------------------------------------------------------------- 套牌辅助
def list_preset_decks() -> list[tuple[str, str]]:
    """列出 decks/ 下的预构筑套牌（路径, 显示名）。"""
    if not os.path.isdir(DECKS_DIR):
        return []

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


#: 兼容旧命名
_list_preset_decks = list_preset_decks


def colors_of_deck(deck: list) -> list[str]:
    """从套牌推断主色。"""
    tally: dict[str, int] = {}
    for data in deck:
        if data.is_land:
            continue
        for symbol in re.findall(r"\{([WUBRG])\}", data.mana_cost or ""):
            tally[symbol] = tally.get(symbol, 0) + 1
    ordered = sorted(tally.items(), key=lambda kv: -kv[1])
    return [c for c, _n in ordered[:2]] or ["G"]


def opposite_colors(colors: list[str]) -> list[str]:
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