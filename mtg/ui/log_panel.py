"""对局日志面板：分类着色、自动滚动、可折叠。"""
from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk

from .theme import (
    AMBER,
    BTN_BG,
    BTN_DANGER,
    BTN_FG,
    FONT_BOLD,
    FONT_LOG,
    OURS,
    PANEL,
    PANEL_DARK,
    TEXT,
    TEXT_DIM,
)

#: 触发类（紫）
TRIGGER = "#a583d8"

#: 行首/关键词 → 标签。顺序即优先级，先匹配到的先用。
_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("head", re.compile(r"^\s*(?:-+\s*第\s*\d+\s*回合|=== )")),
    ("warn", re.compile(r"(无法|不足|输掉|被反击|未实现|未知动作|保护：)")),
    ("phase", re.compile(r"^\s*\[.+\]\s*$")),
    ("combat", re.compile(r"(攻击|阻挡|战斗伤害|斗殴|威慑)")),
    ("trigger", re.compile(r"^\s*触发：")),
    ("cast", re.compile(r"(施放 |加入堆叠：|使用地：|启动 )")),
    ("resolve", re.compile(r"^\s*结算：")),
]

_TAG_COLORS = {
    "head": AMBER,
    "phase": TEXT_DIM,
    "cast": OURS,
    "resolve": TEXT,
    "trigger": TRIGGER,
    "combat": BTN_DANGER,
    "warn": BTN_DANGER,
}


def classify(message: str) -> str | None:
    """按规格 §5.3 的行首关键词给日志行分类，返回标签名。"""
    for tag, pattern in _RULES:
        if pattern.search(message):
            return tag
    return None


class LogPanel(tk.Frame):
    """右侧日志栏。窄窗口可折叠成一条竖边。"""

    def __init__(self, master: tk.Misc, on_toggle=None) -> None:
        super().__init__(master, bg=PANEL, width=300)
        self.pack_propagate(False)
        self.on_toggle = on_toggle
        self.collapsed = False

        head = tk.Frame(self, bg=PANEL)
        head.pack(fill="x")
        tk.Label(head, text="对局日志", bg=PANEL, fg=TEXT, font=FONT_BOLD).pack(
            side="left", padx=8, pady=(8, 2)
        )
        self.toggle_btn = tk.Button(
            head, text="折叠", bg=BTN_BG, fg=BTN_FG, font=FONT_BOLD, relief="flat",
            command=self.toggle,
        )
        self.toggle_btn.pack(side="right", padx=6, pady=(6, 2))

        self.body = tk.Frame(self, bg=PANEL)
        self.body.pack(fill="both", expand=True)

        self.text = tk.Text(
            self.body, wrap="word", font=FONT_LOG, bg=PANEL_DARK, fg=TEXT,
            relief="flat", height=10, width=24,
        )
        self.scroll = ttk.Scrollbar(self.body, command=self.text.yview)
        self.text.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side="right", fill="y", pady=(0, 8))
        self.text.pack(fill="both", expand=True, padx=(8, 0), pady=(0, 8))
        self.text.configure(state="disabled")

        for tag, color in _TAG_COLORS.items():
            self.text.tag_configure(tag, foreground=color)

    # ---------------------------------------------------------------- 内容
    def append(self, message: str) -> None:
        self.text.configure(state="normal")
        tag = classify(message)
        if tag:
            self.text.insert("end", message + "\n", tag)
        else:
            self.text.insert("end", message + "\n")
        self.text.see("end")
        self.text.configure(state="disabled")

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    # ---------------------------------------------------------------- 折叠
    def set_collapsed(self, collapsed: bool) -> None:
        self.collapsed = bool(collapsed)
        if self.collapsed:
            self.body.pack_forget()
            self.toggle_btn.configure(text="展开")
            self.configure(width=112)
        else:
            self.body.pack(fill="both", expand=True)
            self.toggle_btn.configure(text="折叠")
            self.configure(width=300)

    def toggle(self) -> None:
        self.set_collapsed(not self.collapsed)
        if self.on_toggle is not None:
            self.on_toggle(self.collapsed)