"""界面配色与字体。深色牌桌风格（MTGO / Arena 观感）。"""
from __future__ import annotations

# ---- 背景与面板
BG = "#0e1116"           # 牌桌底色
PANEL = "#161a21"        # 面板
PANEL_DARK = "#1d222b"   # 面板强调
BORDER = "#2b323d"       # 分隔线

# ---- 文字
TEXT = "#e8ebf0"
TEXT_DIM = "#8d97a5"
TEXT_LIGHT = "#ffffff"

# ---- 强调与阵营
AMBER = "#d8a84e"        # 强调（金）
OURS = "#4d9fe0"         # 我方（蓝）
THEIRS = "#e0604a"       # 对手（红）
DIM_MASK = "#0e1116"     # 不可用蒙版底色

# ---- 卡牌底色（深底按颜色微调）
CARD_BG = "#1a1f27"
CARD_BORDER = "#39414d"

COLOR_HEX = {
    "W": "#33301f",
    "U": "#16222f",
    "B": "#241d2e",
    "R": "#2f1e1a",
    "G": "#1b2c20",
    "C": "#20242b",
    "M": "#2e2a1c",  # 多色
}

# 边框高亮色（按颜色身份，深底可读）
ACCENT = {
    "W": "#e3cf8a",
    "U": "#4d9fe0",
    "B": "#a583d8",
    "R": "#e0604a",
    "G": "#57b06e",
    "C": "#8d97a5",
    "M": "#d8a84e",
}

# ---- 状态
SELECTED = "#d8a84e"
ATTACKING = "#e0604a"
BLOCKING = "#4d9fe0"
TAPPED = "#2a2f38"
HIGHLIGHT = "#d8a84e"

# ---- 按钮
BTN_BG = "#2b323d"
BTN_FG = "#e8ebf0"
BTN_HOVER = "#3a424f"
BTN_ACCENT = "#2f6f4f"
BTN_DANGER = "#b04a3c"

FONT_FAMILY = "Microsoft YaHei"
FONT_MONO = "Consolas"

FONT_SMALL = (FONT_FAMILY, 8)
FONT_NORMAL = (FONT_FAMILY, 9)
FONT_BOLD = (FONT_FAMILY, 9, "bold")
FONT_TITLE = (FONT_FAMILY, 11, "bold")
FONT_LOG = (FONT_FAMILY, 9)
FONT_PROMPT = (FONT_FAMILY, 12, "bold")


def color_key(colors: list[str]) -> str:
    """根据颜色身份返回配色键。"""
    if not colors:
        return "C"
    if len(colors) == 1:
        return colors[0]
    return "M"