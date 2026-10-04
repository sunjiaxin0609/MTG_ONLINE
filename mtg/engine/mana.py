"""法术力系统：费用文本解析、法术力池、费用支付求解。

支持:
  * 有色法术力  {W}{U}{B}{R}{G}
  * 无色/通用   {1}{2}{10}{X}{C}
  * 混血法术力  {W/U}  {2/W}  {B/G}
  * 非瑞克西亚  {W/P}（以 2 点生命替代）
  * 雪境        {S}
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .types import Color

COLORS = ("W", "U", "B", "R", "G")
LETTER_TO_COLOR: dict[str, Color] = {
    "W": Color.WHITE,
    "U": Color.BLUE,
    "B": Color.BLACK,
    "R": Color.RED,
    "G": Color.GREEN,
}

_SYMBOL_RE = re.compile(r"\{([^{}]+)\}")


# ------------------------------------------------------------------ 法术力符号

@dataclass(frozen=True)
class ManaSymbol:
    """一个法术力符号。

    kind 取值:
      ``colored``  — 单一颜色, ``options=('W',)``
      ``generic``  — 通用法术力, ``amount`` 为数量
      ``colorless`` — 严格无色 {C}
      ``snow``     — {S}
      ``hybrid``   — 混血, ``options`` 给出可选颜色
      ``phyrexian`` — {W/P}, ``options=('W',)`` 且可用 2 生命替代
      ``x``        — {X}
      ``variable`` — {Y}{Z} 等
    """

    kind: str
    options: tuple[str, ...] = ()
    amount: int = 1

    @property
    def is_colored(self) -> bool:
        return self.kind in ("colored", "hybrid", "phyrexian")

    @property
    def cmc(self) -> int:
        """该符号对总法术力值的贡献。"""
        if self.kind in ("generic", "colorless", "snow"):
            return self.amount
        if self.kind == "x":
            return 0
        return 1

    def __str__(self) -> str:
        if self.kind == "generic":
            return f"{{{self.amount}}}"
        if self.kind == "x":
            return "{X}"
        if self.kind == "colorless":
            return "{C}"
        if self.kind == "snow":
            return "{S}"
        if self.kind == "phyrexian":
            return f"{{{self.options[0]}/P}}"
        return "{" + "/".join(self.options) + "}"


def parse_mana_cost(text: str | None) -> list[ManaSymbol]:
    """把 ``"{1}{W}{U}"`` 解析为符号列表。"""
    if not text:
        return []
    symbols: list[ManaSymbol] = []
    for raw in _SYMBOL_RE.findall(text):
        symbols.append(parse_symbol(raw))
    return symbols


def parse_symbol(inner: str) -> ManaSymbol:
    """解析单个花括号内部的内容。"""
    token = inner.strip()

    if "/" in token:
        parts = [p.strip() for p in token.split("/")]
        # 混血 / 非瑞克西亚
        if parts[-1] == "P":
            return ManaSymbol("phyrexian", (parts[0],))
        # {2/W} 这类"两点通用或一点指定色"
        if parts[0].isdigit():
            return ManaSymbol("hybrid", tuple(p for p in parts[1:] if p in COLORS), amount=int(parts[0]))
        return ManaSymbol("hybrid", tuple(p for p in parts if p in COLORS))

    if token.isdigit():
        return ManaSymbol("generic", amount=int(token))
    if token == "X":
        return ManaSymbol("x")
    if token == "Y":
        return ManaSymbol("variable", ("Y",))
    if token == "Z":
        return ManaSymbol("variable", ("Z",))
    if token == "C":
        return ManaSymbol("colorless")
    if token == "S":
        return ManaSymbol("snow")
    if token in COLORS:
        return ManaSymbol("colored", (token,))
    # 半法术力 {1/2} 等罕见情况，按 0 处理
    return ManaSymbol("generic", amount=0)


def mana_cost_to_string(symbols: Sequence[ManaSymbol]) -> str:
    return "".join(str(s) for s in symbols)


# ------------------------------------------------------------------ 法术力池

@dataclass
class ManaPool:
    """牌手的法术力池。``counts['W']`` 等为各色数量，``generic`` 为无色/通用。"""

    counts: dict[str, int] = field(default_factory=lambda: {c: 0 for c in COLORS})
    colorless: int = 0
    snow: int = 0

    def __post_init__(self) -> None:
        for color in COLORS:
            self.counts.setdefault(color, 0)

    # ---- 基本操作
    def add(self, color: str, amount: int = 1) -> None:
        if color == "C":
            self.colorless += amount
        elif color == "S":
            self.snow += amount
            self.colorless += amount
        elif color in COLORS:
            self.counts[color] += amount
        elif color == "*":  # 任意色（如某些宝藏指示物），暂时记作无色
            self.colorless += amount

    def total(self) -> int:
        return sum(self.counts.values()) + self.colorless

    def empty(self) -> None:
        for color in COLORS:
            self.counts[color] = 0
        self.colorless = 0
        self.snow = 0

    def copy(self) -> "ManaPool":
        return ManaPool(counts=dict(self.counts), colorless=self.colorless, snow=self.snow)

    def __str__(self) -> str:
        if self.total() == 0:
            return "空"
        parts = [f"{c}:{n}" for c, n in self.counts.items() if n]
        if self.colorless:
            parts.append(f"无色:{self.colorless}")
        return " ".join(parts)


# ------------------------------------------------------------------ 费用支付

@dataclass
class PaymentPlan:
    """一次费用支付的具体方案。"""

    colored: dict[str, int] = field(default_factory=dict)  # 各色使用量
    generic: int = 0  # 用掉的通用法术力
    generic_from: dict[str, int] = field(default_factory=dict)  # 通用法术力取自哪些颜色
    colorless_used: int = 0
    life_instead: int = 0  # 非瑞克西亚法术力改用生命支付的点数
    x_value: int = 0

    def describe(self) -> str:
        bits = []
        for color, amount in sorted(self.colored.items()):
            if amount:
                bits.append(f"{color * amount}")
        if self.generic:
            bits.append(f"通用 {self.generic}")
        if self.life_instead:
            bits.append(f"支付 {self.life_instead} 点生命")
        return " + ".join(bits) if bits else "无需费用"


def _can_pay_with_pool(symbols: Sequence[ManaSymbol], pool: ManaPool, x_value: int = 0) -> PaymentPlan | None:
    """在给定法术力池中为费用求解支付方案（回溯搜索）。"""
    colored_needs: dict[str, int] = {c: 0 for c in COLORS}
    generic_need = 0
    colorless_need = 0
    snow_need = 0
    flexible: list[tuple[str, ...]] = []  # 混血/非瑞克西亚的可选项

    for sym in symbols:
        if sym.kind == "generic":
            generic_need += sym.amount
        elif sym.kind == "x":
            generic_need += x_value
        elif sym.kind == "colorless":
            colorless_need += 1
        elif sym.kind == "snow":
            snow_need += 1
        elif sym.kind == "colored":
            colored_needs[sym.options[0]] += 1
        elif sym.kind == "hybrid":
            flexible.append(sym.options)
        elif sym.kind == "phyrexian":
            flexible.append(("P", sym.options[0]))  # P 表示用生命替代

    # ---- 回溯为 flexible 符号挑选颜色（或生命）
    best: PaymentPlan | None = None
    choices: list[str] = []

    def search(idx: int) -> bool:
        nonlocal best
        if idx == len(flexible):
            demand = dict(colored_needs)
            life_used = 0
            for choice in choices:
                if choice == "P":
                    life_used += 2
                else:
                    demand[choice] = demand.get(choice, 0) + 1
            plan = _allocate(demand, generic_need, colorless_need, snow_need, pool)
            if plan is not None:
                plan.life_instead = life_used
                plan.x_value = x_value
                best = plan
                return True
            return False

        for option in flexible[idx]:
            choices.append(option)
            if search(idx + 1):
                return True
            choices.pop()
        return False

    search(0)
    return best


def _allocate(
    demand: dict[str, int],
    generic_need: int,
    colorless_need: int,
    snow_need: int,
    pool: ManaPool,
) -> PaymentPlan | None:
    """在硬性颜色需求确定后，检查池内法术力是否足够并计算通用法术力来源。"""
    # 严格无色需求（{C}）只能由无色法术力支付
    available_colorless = pool.colorless
    if snow_need:
        if pool.snow < snow_need:
            return None
        available_colorless -= snow_need
    if colorless_need:
        if available_colorless < colorless_need:
            return None
        available_colorless -= colorless_need

    remaining = {c: pool.counts.get(c, 0) for c in COLORS}
    for color, need in demand.items():
        if remaining.get(color, 0) < need:
            return None
        remaining[color] -= need

    # 通用法术力：优先用无色，再用剩余有色
    plan = PaymentPlan(colored={c: n for c, n in demand.items() if n}, colorless_used=colorless_need + snow_need)
    still_need = generic_need
    take_from_colorless = min(available_colorless, still_need)
    plan.generic_from["C"] = take_from_colorless
    plan.generic += take_from_colorless
    still_need -= take_from_colorless

    for color in COLORS:
        if still_need <= 0:
            break
        take = min(remaining.get(color, 0), still_need)
        if take:
            plan.generic_from[color] = take
            plan.generic += take
            still_need -= take

    if still_need > 0:
        return None
    return plan


def can_pay(symbols: Sequence[ManaSymbol], pool: ManaPool, x_value: int = 0) -> PaymentPlan | None:
    """公开入口：判断是否能支付该费用，能则返回方案。"""
    return _can_pay_with_pool(symbols, pool, x_value)


def apply_payment(pool: ManaPool, plan: PaymentPlan) -> None:
    """按方案从池中扣除法术力。"""
    for color, amount in plan.colored.items():
        pool.counts[color] -= amount
    for color, amount in plan.generic_from.items():
        if color == "C":
            pool.colorless -= amount
        else:
            pool.counts[color] -= amount


def convert_to_mana(colors: Iterable[str] | None, produced: Iterable[str] | None) -> list[str]:
    """把一张牌的产费信息统一成颜色字母列表。"""
    out: list[str] = []
    if produced:
        out.extend(str(c) for c in produced)
    elif colors:
        out.extend(str(c) for c in colors)
    return out
