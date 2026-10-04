"""卡牌控件：优先显示真实卡图，图片未就绪时先给文字骨架。"""
from __future__ import annotations

import tkinter as tk
from typing import TYPE_CHECKING, Callable

from .images import PIL_AVAILABLE, get_cache, show_card_image
from .layout import card_size
from .theme import (
    ACCENT,
    AMBER,
    CARD_BG,
    CARD_BORDER,
    COLOR_HEX,
    DIM_MASK,
    FONT_BOLD,
    FONT_SMALL,
    PANEL_DARK,
    SELECTED,
    TAPPED,
    TEXT,
    TEXT_DIM,
    color_key,
)

if TYPE_CHECKING:
    from ..engine.card import CardData, Permanent

#: 全局开关：False 时退回纯文字卡片（首次卡图没下载完也能先玩）
ART_ENABLED = [PIL_AVAILABLE]

#: 模块级默认尺寸（M 档），供旧引用与 card_grid 兼容
WIDTH, HEIGHT, PIC_W, PIC_H = card_size("M")


def set_art_enabled(enabled: bool) -> None:
    ART_ENABLED[0] = bool(enabled)


class CardWidget(tk.Frame):
    """一张牌。有卡图时铺图，没有时显示文字骨架，图到位后自动替换。"""

    WIDTH = WIDTH
    HEIGHT = HEIGHT

    def __init__(
        self,
        master: tk.Misc,
        data: "CardData",
        on_click: Callable[[], None] | None = None,
        subtitle: str = "",
        compact: bool = False,
        badge: str | None = None,
        badge_fg: str | None = None,
        size: str = "M",
        dimmed: bool = False,
        on_right_click: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master, bg=CARD_BORDER, bd=1, relief="solid")
        self.data = data
        self.on_click = on_click
        self.on_right_click = on_right_click
        self.subtitle = subtitle
        self.compact = compact
        #: 尺寸档：S / M / L / XL，由 layout 决定具体像素
        self.size = size
        self.card_w, self.card_h, self.pic_w, self.pic_h = card_size(size)
        #: 右下角徽章：战场上的生物传当前攻防（含加成），手牌传不可出原因
        self.badge = badge
        #: 徽章文字色（默认金色；不可出牌原因用红色）
        self.badge_fg = badge_fg
        self.selected = False
        #: 初始即降亮（横置 / 不可出牌），避免图到位后变亮
        self.dimmed = bool(dimmed)
        self._photo = None
        self._art_label: tk.Label | None = None
        self._badge: tk.Label | None = None
        self._destroyed = False
        #: 滚轮转发目标（由外层可滚动容器设置）
        self.wheel_handler: Callable[[tk.Event], None] | None = None

        self.key = color_key(data.colors)
        self.bg = COLOR_HEX.get(self.key, CARD_BG)

        self.configure(width=self.card_w, height=self.card_h)
        self.pack_propagate(False)
        self.grid_propagate(False)

        self.inner = tk.Frame(
            self, bg=self.bg, highlightthickness=2,
            highlightbackground=ACCENT.get(self.key, CARD_BORDER),
        )
        self.inner.pack(fill="both", expand=True, padx=1, pady=1)

        self._build_skeleton()
        self._load_art()
        if self.dimmed and self._art_label is None:
            # 无卡图（未装 Pillow / 图未就绪）时用底色降亮，保证不可出牌一样看得出灰化
            self._apply_bg_dim()

        if on_click is not None:
            self._bind_click(self)
        self._bind_double(self)
        self._bind_wheel(self)

    # ---------------------------------------------------------------- 滚轮
    def _on_wheel(self, event: tk.Event) -> str:
        if self.wheel_handler is not None:
            self.wheel_handler(event)
        return "break"

    def _bind_wheel(self, widget: tk.Misc) -> None:
        widget.bind("<MouseWheel>", self._on_wheel)
        for child in widget.winfo_children():
            self._bind_wheel(child)

    # ---------------------------------------------------------------- 骨架
    def _build_skeleton(self) -> None:
        """图片没到位时的占位：名字 + 费用 + 类别 + 攻防。"""
        pad = 3
        wrap = self.card_w - 8
        name_text = self.data.name if len(self.data.name) <= 16 else self.data.name[:15] + "…"
        tk.Label(
            self.inner, text=name_text, bg=self.bg, fg=TEXT, font=FONT_BOLD,
            wraplength=wrap, justify="left",
        ).pack(anchor="w", padx=pad, pady=(2, 0))

        if self.data.mana_cost:
            tk.Label(self.inner, text=self.data.mana_cost, bg=self.bg, fg=AMBER,
                     font=FONT_SMALL).pack(anchor="w", padx=pad)

        if not self.compact:
            type_text = self.data.type_line_cn
            if len(type_text) > 22:
                type_text = type_text[:21] + "…"
            tk.Label(self.inner, text=type_text, bg=self.bg, fg=TEXT_DIM, font=FONT_SMALL,
                     wraplength=wrap, justify="left").pack(anchor="w", padx=pad, pady=(2, 0))

        bottom = tk.Frame(self.inner, bg=self.bg)
        bottom.pack(side="bottom", fill="x", padx=pad, pady=2)
        # 有原因角标时右下角让给角标，不再画印刷攻防
        if not self.badge_fg:
            if self.data.is_creature:
                tk.Label(bottom, text=f"{self.data.base_power}/{self.data.base_toughness}",
                         bg=self.bg, fg=TEXT, font=FONT_BOLD).pack(side="right")
            elif self.data.loyalty:
                tk.Label(bottom, text=f"忠诚 {self.data.base_loyalty}", bg=self.bg, fg=TEXT,
                         font=FONT_SMALL).pack(side="right")
        if self.subtitle:
            tk.Label(bottom, text=self.subtitle, bg=self.bg, fg=TEXT_DIM,
                     font=FONT_SMALL).pack(side="left")
        # 无卡图时也把原因角标画出来（_show_art 会用卡图重画一遍）
        if self.badge and self.badge_fg:
            self._place_badge(self.badge)

    # ---------------------------------------------------------------- 角标
    def _place_badge(self, text: str) -> None:
        """在右下角叠一枚徽章：战场传当前攻防，手牌传不可出原因。"""
        fg = self.badge_fg or AMBER
        # 中文角标（不可出原因）用雅黑，数值角标（攻防）用等宽更整齐
        badge_font = FONT_BOLD if any(ord(ch) > 127 for ch in text) else ("Consolas", 9, "bold")
        self._badge = tk.Label(
            self.inner, text=text, bg=DIM_MASK, fg=fg, font=badge_font, bd=0,
            highlightthickness=1, highlightbackground=fg, padx=2,
        )
        self._badge.place(relx=1.0, rely=1.0, anchor="se", x=-3, y=-3)

    # ---------------------------------------------------------------- 卡图
    def _load_art(self) -> None:
        if not ART_ENABLED[0]:
            return
        cache = get_cache()

        def apply(photo) -> None:
            if photo is None or self._destroyed:
                return
            try:
                if not self.winfo_exists():
                    return
            except tk.TclError:
                return
            self._show_art(photo)

        ready = cache.peek(self.data, self.pic_w, self.pic_h, self.dimmed)
        if ready is not None:
            self._show_art(ready)
            return
        # 官方没有卡图（衍生物、双面卡背面等）→ 程序画一张占位卡
        if not cache._url_for(self.data, "small"):
            placeholder = cache.placeholder(self.data, self.pic_w, self.pic_h, self.dimmed)
            if placeholder is not None:
                self._show_art(placeholder)
            return
        cache.request(self.data, self.pic_w, self.pic_h, apply, root=self.winfo_toplevel(),
                      dim=self.dimmed)

    def _show_art(self, photo) -> None:
        """用卡图替换骨架，并在右下角叠一枚攻防/忠诚徽章。"""
        self._photo = photo
        for child in list(self.inner.winfo_children()):
            child.destroy()
        self._badge = None

        self._art_label = tk.Label(self.inner, image=photo, bg=self.bg, bd=0)
        self._art_label.pack(fill="both", expand=True)

        badge_text = self.badge or ""
        if not badge_text:
            if self.data.is_creature:
                badge_text = f"{self.data.base_power}/{self.data.base_toughness}"
            elif self.data.loyalty:
                badge_text = f"{self.data.base_loyalty}"
        if badge_text:
            self._place_badge(badge_text)

        if self.subtitle and self.subtitle.strip():
            tip = tk.Label(self.inner, text=self.subtitle, bg=PANEL_DARK, fg=TEXT_DIM,
                           font=FONT_SMALL, bd=0, highlightthickness=1,
                           highlightbackground=CARD_BORDER, padx=2)
            tip.place(relx=0.0, rely=1.0, anchor="sw", x=3, y=-3)

        if self.on_click is not None:
            self._bind_click(self)
        if self.on_right_click is not None:
            self._bind_right_click(self)
        self._bind_double(self)
        self._bind_wheel(self)

    # ---------------------------------------------------------------- 事件
    def _bind_click(self, widget: tk.Misc) -> None:
        widget.bind("<Button-1>", lambda _e: self.on_click())
        for child in widget.winfo_children():
            self._bind_click(child)

    def _bind_right_click(self, widget: tk.Misc) -> None:
        widget.bind("<Button-3>", lambda _e: self.on_right_click())
        for child in widget.winfo_children():
            self._bind_right_click(child)

    def _bind_double(self, widget: tk.Misc) -> None:
        widget.bind("<Double-Button-1>", lambda _e: show_card_image(widget, self.data))
        for child in widget.winfo_children():
            self._bind_double(child)

    def destroy(self) -> None:  # type: ignore[override]
        self._destroyed = True
        super().destroy()

    # ---------------------------------------------------------------- 状态
    def set_selected(self, selected: bool) -> None:
        self.selected = selected
        self._refresh_border()

    def set_border(self, color: str) -> None:
        """外部直接指定边框色（攻击/阻挡/高亮）。"""
        self._custom_border = color
        self.inner.configure(highlightbackground=color)

    def _refresh_border(self) -> None:
        if getattr(self, "_custom_border", None):
            self.inner.configure(highlightbackground=self._custom_border)
        elif self.selected:
            self.inner.configure(highlightbackground=SELECTED)
        else:
            self.inner.configure(highlightbackground=ACCENT.get(self.key, CARD_BORDER))

    def _apply_bg_dim(self) -> None:
        """没有卡图可降亮时，用底色表示降状态（横置 / 不可出）。"""
        base = TAPPED if self.dimmed else self.bg

        def paint(widget: tk.Misc) -> None:
            if widget is self._badge:  # 角标自带深底，不跟着一起变色
                return
            try:
                widget.configure(bg=base)
            except tk.TclError:
                pass
            for child in widget.winfo_children():
                paint(child)

        paint(self.inner)

    def set_dimmed(self, dimmed: bool) -> None:
        if dimmed == self.dimmed:
            return
        self.dimmed = dimmed
        if self._art_label is not None and ART_ENABLED[0]:
            cache = get_cache()
            photo = cache.peek(self.data, self.pic_w, self.pic_h, dimmed)
            if photo is None and not cache._url_for(self.data, "small"):
                photo = cache.placeholder(self.data, self.pic_w, self.pic_h, dimmed)
            if photo is not None:
                self._photo = photo
                self._art_label.configure(image=photo)
                return
            # 图还没缓存好就先整卡变暗
        self._apply_bg_dim()


HOVER_W = 200
HOVER_H = 280


def card_tooltip(widget: tk.Widget, data: "CardData", text: str = "") -> None:
    """悬停时弹出放大的卡面（尽量用高清图）和说明文字。

    牌桌上的卡缩小到 100px 后规则文字看不清，这里让你不用点开就能读。
    """
    from .images import NORMAL, get_cache

    state: dict[str, object] = {"win": None, "timer": None, "shown": False}

    def build() -> None:
        state["timer"] = None
        state["shown"] = True
        try:
            if state["win"] is not None or not widget.winfo_exists():
                return
        except tk.TclError:
            return

        window = tk.Toplevel(widget)
        window.wm_overrideredirect(True)
        try:
            window.attributes("-topmost", True)  # 别被牌桌窗口盖住
        except tk.TclError:
            pass
        window.configure(bg="#1b1f24")
        frame = tk.Frame(window, bg="#1b1f24", bd=1, relief="solid")
        frame.pack()
        holder = tk.Label(frame, bg="#1b1f24", fg="#aaaaaa", text="载入卡图…",
                          font=("Microsoft YaHei", 8), width=24, height=9)
        holder.pack()
        if text:
            tk.Label(frame, text=text, bg="#1b1f24", fg="#e8e8e8", justify="left",
                     wraplength=HOVER_W + 60, font=("Microsoft YaHei", 8)).pack(padx=6, pady=(0, 6))

        x = widget.winfo_rootx() + widget.winfo_width() + 8
        y = widget.winfo_rooty()
        if x + HOVER_W + 24 > widget.winfo_screenwidth():
            x = max(0, widget.winfo_rootx() - HOVER_W - 12)
        # 底部空间不够就往上挪，保证卡面 + 说明都在屏幕里
        if y + HOVER_H + 190 > widget.winfo_screenheight():
            y = max(0, widget.winfo_screenheight() - HOVER_H - 200)
        window.wm_geometry(f"+{x}+{y}")
        state["win"] = window

        def paint(photo) -> None:
            try:
                if not window.winfo_exists():
                    return
            except tk.TclError:
                return
            if photo is None:
                holder.configure(text="（暂无卡图）")
                return
            window.image = photo  # 保持引用
            holder.configure(image=photo, text="", width=0, height=0)

        cache = get_cache()
        if not cache._url_for(data, "small"):
            paint(cache.placeholder(data, HOVER_W, HOVER_H))
            return
        # 先顶上已经缓存的小图，再异步换高清
        cached = cache.peek(data, HOVER_W, HOVER_H)
        if cached is not None:
            paint(cached)
        cache.request(data, HOVER_W, HOVER_H, paint, root=widget.winfo_toplevel(), kind=NORMAL)

    def enter(_event: tk.Event) -> None:
        if state["timer"] is None and state["win"] is None:
            try:
                state["timer"] = widget.after(280, build)
            except tk.TclError:
                pass

    def leave(_event: tk.Event) -> None:
        timer = state["timer"]
        if timer is not None:
            try:
                widget.after_cancel(timer)
            except tk.TclError:
                pass
            state["timer"] = None
        window = state["win"]
        if window is not None:
            try:
                window.destroy()
            except tk.TclError:
                pass
            state["win"] = None

    widget.bind("<Enter>", enter, add="+")
    widget.bind("<Leave>", leave, add="+")
    widget.bind("<Destroy>", lambda _e: leave(None), add="+")


def tooltip(widget: tk.Widget, text: str) -> None:
    """给控件挂一个简单悬浮提示。"""
    tip: list[tk.Toplevel | None] = [None]

    def show(_event: tk.Event) -> None:
        if tip[0] is not None or not text:
            return
        x = widget.winfo_rootx() + 20
        y = widget.winfo_rooty() + 20
        tip[0] = tk.Toplevel(widget)
        tip[0].wm_overrideredirect(True)
        tip[0].wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            tip[0],
            text=text,
            justify="left",
            background="#ffffe0",
            relief="solid",
            borderwidth=1,
            font=("Microsoft YaHei", 9),
            wraplength=320,
        )
        label.pack(ipadx=4, ipady=2)

    def hide(_event: tk.Event) -> None:
        if tip[0] is not None:
            tip[0].destroy()
            tip[0] = None

    widget.bind("<Enter>", show)
    widget.bind("<Leave>", hide)
