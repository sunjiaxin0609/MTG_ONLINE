"""界面配色与字体。浅色牌桌风格，长时间对局不刺眼。"""
from __future__ import annotations

# ---- 背景与面板
BG = "#f2efe6"           # 牌桌底色（米白）
PANEL = "#e6e1d3"        # 面板
PANEL_DARK = "#d8d2c2"   # 面板强调
BORDER = "#b9b2a0"

# ---- 文字
TEXT = "#2b2b2b"
TEXT_DIM = "#6b6558"
TEXT_LIGHT = "#ffffff"

# ---- 卡牌底色（按颜色）
CARD_BG = "#fbfaf6"
CARD_BORDER = "#8d8778"

COLOR_HEX = {
    "W": "#f5f2e3",
    "U": "#d6e6f2",
    "B": "#ded9dd",
    "R": "#f2d9d2",
    "G": "#d9ead2",
    "C": "#e8e6e0",
    "M": "#e8ddc8",  # 多色
}

# 边框高亮色（按颜色身份）
ACCENT = {
    "W": "#c9b458",
    "U": "#3f7fb5",
    "B": "#4a4453",
    "R": "#c0503a",
    "G": "#3f7a4a",
    "C": "#7a7a7a",
}

# ---- 状态
SELECTED = "#2f6f4f"
ATTACKING = "#c0503a"
BLOCKING = "#3f7fb5"
TAPPED = "#9a9484"
HIGHLIGHT = "#f0c419"

# ---- 按钮
BTN_BG = "#455a64"
BTN_FG = "#ffffff"
BTN_HOVER = "#37474f"
BTN_ACCENT = "#2f6f4f"
BTN_DANGER = "#a8443a"

FONT_FAMILY = "Microsoft YaHei"
FONT_MONO = "Consolas"

FONT_SMALL = (FONT_FAMILY, 8)
FONT_NORMAL = (FONT_FAMILY, 9)
FONT_BOLD = (FONT_FAMILY, 9, "bold")
FONT_TITLE = (FONT_FAMILY, 11, "bold")
FONT_LOG = (FONT_MONO, 8)


def color_key(colors: list[str]) -> str:
    """根据颜色身份返回配色键。"""
    if not colors:
        return "C"
    if len(colors) == 1:
        return colors[0]
    return "M"
