"""布局计算：纯函数，无 tkinter 依赖，便于单独测试。

窗口尺寸、区域高度权重、卡牌尺寸档与列数都集中在这里，
`app.py` 只负责把结果应用到 tkinter 控件上。
"""
from __future__ import annotations

# ---- 卡牌尺寸档：(卡宽, 卡高, 图宽, 图高)
CARD_TIERS: dict[str, tuple[int, int, int, int]] = {
    "S": (84, 118, 80, 112),
    "M": (100, 140, 96, 134),
    "L": (116, 162, 112, 156),
    "XL": (132, 184, 128, 178),
}

#: 从大到小试探顺序
TIER_ORDER = ["XL", "L", "M", "S"]

#: 卡牌之间的水平间距
CARD_GAP = 4

# ---- 窗口
WINDOW_MAX = (1400, 900)
WINDOW_MARGIN = 80
WINDOW_MIN = (1024, 700)

# ---- 垂直布局权重与边界
ROW_WEIGHTS: dict[str, float] = {"opp": 3.0, "mid": 1.6, "mine": 3.0, "hand": 2.4}
FIXED_HEIGHTS: dict[str, int] = {"header": 40, "action": 44}
REGION_BOUNDS: dict[str, tuple[int, int]] = {
    "opp": (140, 320),
    "mid": (72, 120),
    "mine": (140, 320),
    "hand": (150, 260),
}

#: 牌桌自上而下的行顺序
ROW_ORDER = ["header", "opp", "mid", "mine", "hand", "action"]


def pick_tier(avail_w: int, min_columns: int) -> str:
    """从最大档向下试探，返回第一个能满足最小列数的档；都不满足则退回 S。"""
    for tier in TIER_ORDER:
        width = CARD_TIERS[tier][0]
        if avail_w // (width + CARD_GAP) >= min_columns:
            return tier
    return "S"


def columns_for(avail_w: int, tier: str) -> int:
    """按档位宽度算出可用列数（至少 1）。"""
    width = CARD_TIERS.get(tier, CARD_TIERS["M"])[0]
    return max(1, avail_w // (width + CARD_GAP))


def card_size(tier: str) -> tuple[int, int, int, int]:
    """返回档位对应的 (卡宽, 卡高, 图宽, 图高)。"""
    return CARD_TIERS.get(tier, CARD_TIERS["M"])


def window_size(screen_w: int, screen_h: int) -> tuple[int, int]:
    """启动尺寸：不超过屏幕，且不小于最小可用尺寸。"""
    w = min(WINDOW_MAX[0], screen_w - WINDOW_MARGIN)
    h = min(WINDOW_MAX[1], screen_h - WINDOW_MARGIN)
    return max(WINDOW_MIN[0], w), max(WINDOW_MIN[1], h)


def row_heights(avail_h: int) -> dict[str, int]:
    """按权重把可用高度分配到各区域，并夹在各自 min/max 之间。

    固定条（header / action）原样返回；机动区域从各自 min 起步，把富余高度
    按权重分配（不超过 max），从而保证总和恰好等于可用高度、不会溢出。
    若连所有 min 都放不下，则按比例压缩，至少留 1px。
    """
    header = FIXED_HEIGHTS["header"]
    action = FIXED_HEIGHTS["action"]
    flexible = max(0, avail_h - header - action)
    keys = list(ROW_WEIGHTS)
    mins = {k: REGION_BOUNDS[k][0] for k in keys}
    maxs = {k: REGION_BOUNDS[k][1] for k in keys}

    if flexible < sum(mins.values()):  # 极端矮窗：按比例压缩
        total = sum(mins.values()) or 1
        vals = {k: max(1, mins[k] * flexible // total) for k in keys}
        return {"header": header, "action": action, **vals}

    vals = dict(mins)
    extra = flexible - sum(mins.values())
    active = [k for k in keys if maxs[k] > vals[k]]
    while extra > 0 and active:
        weight_total = sum(ROW_WEIGHTS[k] for k in active) or 1.0
        progressed = 0
        for k in active:
            add = min(int(extra * ROW_WEIGHTS[k] / weight_total), maxs[k] - vals[k])
            vals[k] += add
            progressed += add
        if progressed == 0:  # 权重太小分不动时，逐个补 1px
            for k in active:
                if vals[k] < maxs[k]:
                    vals[k] += 1
                    progressed = 1
                    break
        extra -= progressed
        active = [k for k in active if vals[k] < maxs[k]]

    return {"header": header, "action": action, **vals}