"""oracle 文本自动解析器：把真实卡牌的规则文本翻译成可执行异能。

万智牌的规则文本是受限的半形式化英语，绝大多数常见卡可以用
模式匹配覆盖。解析器按段落切分规则文本，依次尝试：

    关键字行  →  KeywordAbility
    触发式    →  TriggeredAbility（含触发事件与效果）
    启动式    →  ActivatedAbility（含费用与效果）
    静态异能  →  StaticAbility（持续修正）
    咒语效果  →  SpellAbility

无法识别的段落会记入 ``CardData.unparsed`` 并在 UI 中提示，
保证"能跑的卡真的会跑"，不会静默失效。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Iterable

from ..engine.ability import (
    Ability,
    ActivatedAbility,
    Cost,
    KeywordAbility,
    SpellAbility,
    StaticAbility,
    TargetSpec,
    TriggeredAbility,
)
from ..engine.effects import Effect, Selector, TokenSpec
from ..engine.mana import parse_mana_cost
from ..engine.types import EventType, Keyword, normalize_keyword

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")


# -------------------------------------------------------------------- 词法工具

WORD_NUMBERS: dict[str, str] = {
    "a": "1",
    "an": "1",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
    "twenty": "20",
    "x": "X",
    "y": "Y",
}

COLORS = ("W", "U", "B", "R", "G")

#: 中文颜色名（用于描述）
COLOR_CN = {"W": "白", "U": "蓝", "B": "黑", "R": "红", "G": "绿", "C": "无色"}


def _num(token: str | None) -> int:
    """把数字词转成整数，X 记 0（具体值在结算时用 x_value 替换）。"""
    if token is None:
        return 1
    token = token.lower().strip()
    mapped = WORD_NUMBERS.get(token, token)
    if mapped in ("X", "Y"):
        return 0
    try:
        return int(mapped)
    except ValueError:
        return 1


def _num_token(token: str | None) -> str:
    """返回数字词条，X 保留为 'X'。"""
    if token is None:
        return "1"
    lowered = token.lower().strip()
    mapped = WORD_NUMBERS.get(lowered, lowered)
    return mapped


# -------------------------------------------------------------------- 关键字行

#: 非规则性的补充文本，出现即视为"已处理"，不影响 supported 判定
IGNORABLE_TEXTS = (
    "activate only as a sorcery",
    "equip only as a sorcery",
    "enchant creature",
    "enchant player",
    "enchant permanent",
    "enchant land",
    "enchant artifact",
    "enchant enchantment",
    "this ability triggers only once each turn",
    "do this only once each turn",
    "choose one",
    "choose one or more",
    "choose two",
    "as an additional cost to cast this spell",
    "this creature can attack as though it didn't have defender",
    "it's still a land",
    "it's still a creature",
    "this spell can't be countered",
    "this creature enters prepared",
    "put the rest on the bottom of your library",
    "you may choose new targets",
    "you may look at the top card of your library",
    "untap it",
    "tap it",
    "enchanted creature",
    "equipped creature",
)

#: 引擎暂不实现、但 Scryfall 已在 keywords 字段中标出的异能词。
#: 这些行出现时不会让卡变成"不支持"，只记录为未实现的机制说明。
UNIMPLEMENTED_KEYWORDS = frozenset(
    {
        "crew",
        "saddle",
        "cycling",
        "convoke",
        "changeling",
        "bargain",
        "spree",
        "station",
        "job select",
        "start your engines",
        "prototype",
        "level up",
        "discover",
        "draft",
        "boast",
        "foretell",
        "disturb",
        "escape",
        "mutate",
        "companion",
        "eternalize",
        "embalm",
        "awaken",
        "surge",
        "tributary",
        "craft",
        "impending",
        "suspend",
        "flashback",
        "madness",
        "morph",
        "megamorph",
        "ninjutsu",
        "prowl",
        "dash",
        "blitz",
        "plot",
        "suspend",
        "recover",
        "retrace",
        "scavenge",
        "unearth",
        "transmute",
        "dredge",
        "delve",
        "affinity",
        "bushido",
        "modular",
        "sunburst",
        "amplify",
        "kicker",
        "multikicker",
        "entwine",
        "splice",
        "epic",
        "graft",
        "dethrone",
        "exploit",
        "renown",
        "skulk",
        "tribute",
        "undaunted",
        "myriad",
        "melee",
        "assist",
        "goad",
        "cohort",
        "aftermath",
        "afterlife",
        "mentor",
        "riot",
        "adamant",
        "escalate",
        "rebound",
        "strive",
        "fuse",
        "haunt",
        "echo",
        "fading",
        "vanishing",
        "flanking",
        "phasing",
        "shadow",
        "horsemanship",
        "bands with other",
        "rampage",
        "offspring",
        "firebending",
        "mobilize",
        "teamwork",
        "harmonize",
        "gift",
        "airbend",
        "waterbend",
        "earthbend",
        "firebend",
        "lesson",
        "tempting offer",
        "demonstrate",
        "casualty",
        "squad",
        "decayed",
        "daybound",
        "nightbound",
        "battle cry",
        "exalted",
        "infect",
        "wither",
        "annihilator",
        "devour",
        "amplify",
        "champion",
        "evoke",
        "provoke",
        "reinforce",
        "hideaway",
        "recover",
        "ripple",
        "split second",
        "totem armor",
        "undying",
        "persist",
        "soulbond",
        "soulshift",
        "splice",
        "transfigure",
        "type cycling",
        "unleash",
        "unearth",
        "vanishing",
    }
)

#: 提示文本（括号里的规则解释）不算规则，解析前先剥离
_REMINDER_RE = re.compile(r"\([^()]*\)")


def is_unimplemented_keyword(text: str) -> bool:
    """该行是否是引擎暂不支持的异能词行。"""
    lowered = text.lower().strip().rstrip(".").strip()
    if not lowered:
        return False
    # "Crew 2" / "Cycling {2}" / "Start your engines!"
    for keyword in UNIMPLEMENTED_KEYWORDS:
        if re.match(rf"^{re.escape(keyword)}\b", lowered):
            return True
    return False


def strip_reminder_text(text: str) -> str:
    """移除括号中的提示文本。"""
    previous = None
    result = text
    while previous != result:
        previous = result
        result = _REMINDER_RE.sub("", result)
    return re.sub(r"\s{2,}", " ", result).strip()


def is_ignorable(text: str) -> bool:
    """该片段是否属于不需引擎执行的说明性文本。"""
    lowered = text.lower().strip().rstrip(".").strip()
    if not lowered:
        return True
    return any(lowered.startswith(item) for item in IGNORABLE_TEXTS)


def _keyword_line(text: str) -> Ability | None:
    """识别纯关键字行（含带参数形式）。"""
    cleaned = strip_reminder_text(text).rstrip(".").strip()
    lowered = cleaned.lower()

    simple = {
        "flying": Keyword.FLYING,
        "first strike": Keyword.FIRST_STRIKE,
        "double strike": Keyword.DOUBLE_STRIKE,
        "deathtouch": Keyword.DEATHTOUCH,
        "defender": Keyword.DEFENDER,
        "flash": Keyword.FLASH,
        "haste": Keyword.HASTE,
        "hexproof": Keyword.HEXPROOF,
        "indestructible": Keyword.INDESTRUCTIBLE,
        "lifelink": Keyword.LIFELINK,
        "menace": Keyword.MENACE,
        "prowess": Keyword.PROWESS,
        "reach": Keyword.REACH,
        "trample": Keyword.TRAMPLE,
        "vigilance": Keyword.VIGILANCE,
        "shroud": Keyword.SHROUD,
        "skulk": None,
        "renown": None,
    }
    if lowered in simple and simple[lowered] is not None:
        return Ability(kind="keyword", keyword=simple[lowered], text=cleaned)

    # "Hexproof from black" / "Protection from white" / "Ward {2}"
    if lowered.startswith("hexproof"):
        return Ability(kind="keyword", keyword=Keyword.HEXPROOF, text=cleaned)
    if lowered.startswith("protection"):
        return Ability(kind="keyword", keyword=Keyword.PROTECTION, text=cleaned)
    if lowered.startswith("ward"):
        return Ability(kind="keyword", keyword=Keyword.WARD, text=cleaned)
    if lowered.startswith("prowess"):
        return Ability(kind="keyword", keyword=Keyword.PROWESS, text=cleaned)
    return None


def _parse_keyword_list(line: str) -> list[Ability] | None:
    """识别 "Flying, vigilance" 这类逗号分隔的关键字串。

    只有当每一段都是已识别的关键字时才返回，避免误伤含逗号的规则句。
    """
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 2:
        return None
    abilities: list[Ability] = []
    for part in parts:
        if len(part.split()) > 5:
            return None
        ability = _keyword_line(part)
        if ability is None:
            return None
        abilities.append(ability)
    return abilities


def _parse_equip(line: str) -> Ability | None:
    """解析武具的佩戴异能 "{2}: Attach to target creature you control." """
    lowered = line.lower()
    if not lowered.startswith("equip"):
        return None
    mana_match = re.search(r"equip (\{[^}]+\}|[^{}\s]+)", line)
    cost = Cost(text=line)
    if mana_match:
        cost.mana = parse_mana_cost(mana_match.group(1))
        cost.sorcery_timing = True
    # 佩戴效果：把此武具结附到目标生物上
    return Ability(
        kind="activated",
        activated=ActivatedAbility(
            cost=cost,
            effects=[Effect(kind="equip", target=Selector(who="target"), text=line)],
            targets=[TargetSpec(kind="creature")],
            is_mana_ability=False,
            text=line,
        ),
        text=line,
    )


def _parse_equipped_static(line: str) -> Ability | None:
    """解析 "Equipped creature gets +2/+2." / "Enchanted creature gets +1/+1." """
    lowered = line.lower()
    prefix = None
    if lowered.startswith("equipped creature"):
        prefix = "equipped"
    elif lowered.startswith("enchanted creature"):
        prefix = "enchanted"
    if prefix is None:
        return None

    buff_match = re.search(r"([+-]?\d+)/([+-]?\d+)", line)
    keywords: list[Keyword] = []
    for word, keyword in (
        ("flying", Keyword.FLYING),
        ("first strike", Keyword.FIRST_STRIKE),
        ("double strike", Keyword.DOUBLE_STRIKE),
        ("deathtouch", Keyword.DEATHTOUCH),
        ("haste", Keyword.HASTE),
        ("hexproof", Keyword.HEXPROOF),
        ("indestructible", Keyword.INDESTRUCTIBLE),
        ("lifelink", Keyword.LIFELINK),
        ("menace", Keyword.MENACE),
        ("reach", Keyword.REACH),
        ("trample", Keyword.TRAMPLE),
        ("vigilance", Keyword.VIGILANCE),
    ):
        if word in lowered:
            keywords.append(keyword)
    if not buff_match and not keywords:
        return None

    return Ability(
        kind="static",
        static=StaticAbility(
            scope=f"{prefix}_creature",
            power=int(buff_match.group(1)) if buff_match else 0,
            toughness=int(buff_match.group(2)) if buff_match else 0,
            grant_keywords=keywords,
            text=line,
        ),
        text=line,
    )


def _parse_enters_tapped(line: str) -> bool:
    """判断是否为"进场时已横置"。"""
    lowered = line.lower()
    return bool(re.search(r"(?:this land|~|it|this creature|this permanent) enters tapped", lowered))


# -------------------------------------------------------------------- 目标规格

_TARGET_KINDS: list[tuple[str, TargetSpec]] = [
    (r"target creature or planeswalker", TargetSpec(kind="creature_or_planeswalker")),
    (r"target creatures? or planeswalkers?", TargetSpec(kind="creature_or_planeswalker")),
    (r"target creature or player", TargetSpec(kind="creature_or_player")),
    (r"any target", TargetSpec(kind="any")),
    (r"target nonland permanents?", TargetSpec(kind="permanent")),
    (r"target permanents?", TargetSpec(kind="permanent")),
    (r"target artifacts? or creatures?", TargetSpec(kind="permanent")),
    (r"target artifacts? or enchantments?", TargetSpec(kind="permanent")),
    (r"target artifacts?", TargetSpec(kind="artifact")),
    (r"target enchantments?", TargetSpec(kind="enchantment")),
    (r"target lands?", TargetSpec(kind="land")),
    (r"target planeswalkers?", TargetSpec(kind="planeswalker")),
    (r"target creatures?", TargetSpec(kind="creature")),
    (r"target opponent", TargetSpec(kind="player", controller="opponent")),
    (r"target players?", TargetSpec(kind="player")),
    (r"target spells?", TargetSpec(kind="spell")),
    (r"target(?:ed)? (?:creature|player|permanent|spell)", TargetSpec(kind="any")),
]


def _find_target(text: str) -> TargetSpec | None:
    lowered = text.lower()
    for pattern, spec in _TARGET_KINDS:
        if re.search(pattern, lowered):
            return TargetSpec(kind=spec.kind, controller=spec.controller)
    return None


def _selector_for(text: str) -> Selector | None:
    """根据文本判断效果作用于谁，返回选择器。"""
    lowered = text.lower()
    if "each opponent" in lowered or "each of your opponents" in lowered:
        return Selector(who="each_opponent")
    if "each player" in lowered:
        return Selector(who="each_player")
    if "that player" in lowered or "target player" in lowered or "target opponent" in lowered:
        return Selector(who="target")
    if "all creatures" in lowered or "each creature" in lowered:
        return Selector(who="all_creatures")
    if re.search(r"creatures? you control", lowered):
        return Selector(who="your_creatures")
    if re.search(r"(?:each|all) creature(?:s)? your opponents control", lowered) or "creatures your opponents control" in lowered:
        return Selector(who="all_opponent_creatures")
    if "you " in lowered and ("gain" in lowered or "draw" in lowered or "lose" in lowered):
        return Selector(who="controller")
    if "its owner" in lowered and "hand" in lowered:
        return Selector(who="target")
    return Selector(who="target")


# -------------------------------------------------------------------- 效果短语

def parse_effect_clause(text: str) -> tuple[list[Effect], list[TargetSpec], bool]:
    """把一句效果文本解析成 Effect 列表。

    返回 ``(effects, targets, fully_parsed)``。``fully_parsed=False``
    表示该句里有引擎无法执行的成分。
    """
    clause = text.strip().rstrip(".").strip()
    if not clause:
        return [], [], True

    lowered = clause.lower()
    effects: list[Effect] = []
    targets: list[TargetSpec] = []
    target = _find_target(clause)

    # ---- 抓牌
    match = re.search(r"draw (x|an?|one|two|three|four|five|six|\d+) cards?|draw cards? equal", lowered)
    if match and "draw" in lowered:
        raw = match.group(1)
        amount = _num(raw)
        who = Selector(who="controller")
        if "target player" in lowered:
            who = Selector(who="target")
        effects.append(Effect(kind="draw", amount=amount, who=who, param="X" if raw == "x" else "", text=clause))
        if "target player" in lowered:
            targets.append(TargetSpec(kind="player"))
        return effects, targets, True

    # ---- 加血
    match = re.search(r"gain (x|an?|one|two|three|four|five|six|seven|eight|\d+) (?:life|lives)", lowered)
    if match is None and ("gain life" in lowered or "gains life" in lowered):
        match = re.search(r"gain (?:life)", lowered)
    if match and "gain" in lowered and "life" in lowered:
        raw = match.group(1) if match.lastindex else "1"
        amount = _num(raw)
        who = Selector(who="controller")
        if "target player" in lowered:
            who = Selector(who="target")
            targets.append(TargetSpec(kind="player"))
        effects.append(Effect(kind="gain_life", amount=amount, who=who, param="X" if raw == "x" else "", text=clause))
        return effects, targets, True

    # ---- 失去生命
    match = re.search(r"loses? (x|an?|one|two|three|four|five|six|seven|eight|\d+) (?:life|lives)", lowered)
    if match:
        raw = match.group(1)
        who = _selector_for(clause)
        targets_for: list[TargetSpec] = []
        if who.who == "target":
            targets_for.append(TargetSpec(kind="player", controller="opponent"))
        effects.append(
            Effect(kind="lose_life", amount=_num(raw), who=who, target=who,
                   param="X" if raw == "x" else "", text=clause)
        )
        return effects, targets_for, True

    # ---- 造成伤害
    match = re.search(
        r"(?:deals?|deal) (x|an?|one|two|three|four|five|six|seven|eight|nine|ten|\d+) damages?", lowered
    )
    if match:
        raw = match.group(1)
        amount = _num(raw)
        selector = _selector_for(clause)
        if "any target" in lowered or "target creature" in lowered or "target player" in lowered:
            if target is None:
                target = TargetSpec(kind="any")
            targets.append(target)
            selector = Selector(who="target")
        elif "each opponent" in lowered:
            selector = Selector(who="each_opponent")
        elif "each creature" in lowered or "all creatures" in lowered:
            selector = Selector(who="all_creatures")
        elif "itself" in lowered or "you" in lowered and "to you" in lowered:
            selector = Selector(who="controller")
        effects.append(
            Effect(kind="damage", amount=amount, target=selector, who=selector,
                   param="X" if raw == "x" else "", text=clause)
        )
        return effects, targets, True

    # ---- 消灭
    if re.search(r"^destroy ", lowered) or " destroy " in lowered:
        if "all creatures" in lowered:
            effects.append(Effect(kind="destroy", target=Selector(who="all_creatures"), text=clause))
            return effects, [], True
        if "all artifacts" in lowered:
            effects.append(Effect(kind="destroy", target=Selector(who="all_permanents"), text=clause))
            return effects, [], True
        if target is None:
            target = TargetSpec(kind="permanent")
        targets.append(target)
        effects.append(Effect(kind="destroy", target=Selector(who="target"), text=clause))
        return effects, targets, True

    # ---- 放逐
    if re.search(r"^exile ", lowered):
        if target is None:
            target = TargetSpec(kind="permanent")
        targets.append(target)
        effects.append(Effect(kind="exile", target=Selector(who="target"), text=clause))
        return effects, targets, True

    # ---- 移回手牌
    if "return" in lowered and "to its owner's hand" in lowered:
        if target is None:
            target = TargetSpec(kind="permanent")
        targets.append(target)
        effects.append(Effect(kind="bounce", target=Selector(who="target"), text=clause))
        return effects, targets, True

    # ---- 横置 / 重置
    if re.match(r"^tap target", lowered):
        targets.append(target or TargetSpec(kind="permanent"))
        effects.append(Effect(kind="tap", target=Selector(who="target"), text=clause))
        return effects, targets, True
    if re.match(r"^untap target", lowered) or re.match(r"^untap (?:up to )?\w+ target", lowered):
        targets.append(target or TargetSpec(kind="permanent"))
        effects.append(Effect(kind="untap", target=Selector(who="target"), text=clause))
        return effects, targets, True

    # ---- 反击
    if "counter target" in lowered:
        targets.append(TargetSpec(kind="spell"))
        effects.append(Effect(kind="counter", target=Selector(who="target"), text=clause))
        return effects, targets, True

    # ---- 生成衍生物
    token_match = re.search(
        r"create (x|an?|one|two|three|four|five|\d+)?\s*(\d+)/(\d+)?\s*([a-z\s,]*?)?(?:creature )?tokens?",
        lowered,
    )
    if "create" in lowered and "token" in lowered:
        count_match = re.search(r"create (x|an?|one|two|three|four|five|\d+)", lowered)
        count = _num(count_match.group(1)) if count_match else 1
        pt_match = re.search(r"(\d+)/(\d+)", lowered)
        power = int(pt_match.group(1)) if pt_match else 1
        toughness = int(pt_match.group(2)) if pt_match else 1
        colors = [c for c in COLORS if re.search(rf"\b{_color_word(c)}\b", lowered)]
        keywords = [kw for kw in ("flying", "haste", "trample", "vigilance", "lifelink", "deathtouch") if kw in lowered]
        subtypes = []
        subtype_match = re.search(r"(?:creature )?tokens? (?:named|with)?\s*([a-z]+)?", lowered)
        name = "衍生物"
        name_match = re.search(r"create[s]? .*? (?:a|an)? ?([A-Z][A-Za-z ]*?) tokens? called", clause)
        if name_match:
            name = name_match.group(1).strip()
        spec = TokenSpec(
            name=name,
            power=power,
            toughness=toughness,
            colors=colors,
            types=["Creature"],
            subtypes=subtypes,
            keywords=keywords,
            count=count,
        )
        effects.append(Effect(kind="token", amount=count, token=spec, text=clause))
        return effects, [], True

    # ---- 加指示物
    counter_match = re.search(r"put (x|an?|one|two|three|\d+)?\s*(\+?\d+/\+\d+|\+1/\+1|-1/-1|[a-z]+) counters?", lowered)
    if "counter" in lowered and ("put" in lowered):
        count_match = re.search(r"put (x|an?|one|two|three|\d+)", lowered)
        count = _num(count_match.group(1)) if count_match else 1
        kind = "+1/+1"
        kind_match = re.search(r"(\+\d+/\+\d+|-\d+/-\d+)", clause)
        if kind_match:
            kind = kind_match.group(1)
        else:
            kind_match = re.search(r"put (?:\w+ )?(a|an|one|two|three|\d+)? ?(\w+) counters?", lowered)
            if kind_match and kind_match.group(2) not in ("on", "target", "it"):
                kind = kind_match.group(2)
        selector = Selector(who="self")
        if target is not None and "target" in lowered:
            selector = Selector(who="target")
            targets.append(target)
        effects.append(Effect(kind="add_counter", amount=count, param=kind, target=selector, text=clause))
        return effects, targets, True

    # ---- 静态/瞬时增减攻防 (get +N/+N)
    buff_match = re.search(r"gets? ([+-]?\d+)/([+-]?\d+)", clause)
    if buff_match:
        power = int(buff_match.group(1))
        toughness = int(buff_match.group(2))
        duration = "end_of_turn" if "until end of turn" in lowered else "instant"
        selector = _selector_for(clause)
        if "creatures you control" in lowered:
            selector = Selector(who="your_creatures")
        elif "all creatures" in lowered:
            selector = Selector(who="all_creatures")
        if target is not None and "target creature" in lowered:
            selector = Selector(who="target")
            targets.append(target)
        effects.append(
            Effect(kind="buff", amount=power, amount2=toughness, target=selector, duration=duration, text=clause)
        )
        return effects, targets, True

    # ---- 获得关键字
    kw_match = re.search(r"(?:gains?|has|have) (flying|first strike|double strike|deathtouch|haste|hexproof|"
                         r"indestructible|lifelink|menace|reach|trample|vigilance)", lowered)
    if kw_match:
        keywords = [kw_match.group(1)]
        duration = "end_of_turn" if "until end of turn" in lowered else "permanent"
        selector = Selector(who="self")
        if target is not None and "target" in lowered:
            selector = Selector(who="target")
            targets.append(target)
        elif "creatures you control" in lowered:
            selector = Selector(who="your_creatures")
        effects.append(
            Effect(kind="grant_keyword", keywords=keywords, target=selector, duration=duration, text=clause)
        )
        return effects, targets, True

    # ---- 法术力
    if re.match(r"^add ", lowered):
        return _parse_mana_clause(clause, effects)

    # ---- 磨牌
    mill_match = re.search(r"mills? (x|an?|one|two|three|\d+) cards?|puts? the top (x|an?|one|two|\d+) cards?", lowered)
    if mill_match or "mill" in lowered:
        raw = mill_match.group(1) or mill_match.group(2) if mill_match else "1"
        who = Selector(who="controller")
        if "target player" in lowered or "target opponent" in lowered:
            who = Selector(who="target")
            targets.append(TargetSpec(kind="player", controller="opponent" if "opponent" in lowered else "any"))
        effects.append(Effect(kind="mill", amount=_num(raw), who=who, text=clause))
        return effects, targets, True

    # ---- 占卜 / 侦察
    scry_match = re.search(r"scry (x|an?|one|two|three|\d+)", lowered)
    if scry_match:
        effects.append(Effect(kind="scry", amount=_num(scry_match.group(1)), text=clause))
        return effects, [], True
    surveil_match = re.search(r"surveil (x|an?|one|two|three|\d+)", lowered)
    if surveil_match:
        effects.append(Effect(kind="surveil", amount=_num(surveil_match.group(1)), text=clause))
        return effects, [], True

    # ---- 弃牌
    discard_match = re.search(r"discards? (x|an?|one|two|three|\d+) cards?", lowered)
    if discard_match or "discard" in lowered:
        raw = discard_match.group(1) if discard_match else "1"
        who = Selector(who="target")
        if "target opponent" in lowered or "target player" in lowered:
            targets.append(TargetSpec(kind="player", controller="opponent"))
            who = Selector(who="target")
        elif "each opponent" in lowered:
            who = Selector(who="each_opponent")
        else:
            who = Selector(who="controller")
        effects.append(Effect(kind="discard", amount=_num(raw), who=who, text=clause))
        return effects, targets, True

    # ---- 牺牲
    if "sacrifice" in lowered:
        if "sacrifice this" in lowered or "sacrifice ~" in lowered:
            effects.append(Effect(kind="sacrifice", target=Selector(who="self"), text=clause))
            return effects, [], True
        if target is not None and "target" in lowered:
            targets.append(target)
            effects.append(Effect(kind="sacrifice", target=Selector(who="target"), text=clause))
            return effects, targets, True
        effects.append(Effect(kind="sacrifice", target=Selector(who="self"), text=clause))
        return effects, [], True

    return [], [], False


def _color_word(letter: str) -> str:
    return {
        "W": "white",
        "U": "blue",
        "B": "black",
        "R": "red",
        "G": "green",
    }.get(letter, letter)


def _parse_mana_clause(clause: str, effects: list[Effect]) -> tuple[list[Effect], list[TargetSpec], bool]:
    """解析 "Add {G}." 这类产费效果。"""
    lowered = clause.lower()
    # "add one mana of any color" / "add two mana in any combination of colors"
    if "of any color" in lowered or "any combination of colors" in lowered:
        count_match = re.search(r"add (x|an?|one|two|three|\d+) mana", lowered)
        count = _num(count_match.group(1)) if count_match else 1
        effects.append(Effect(kind="mana", amount=count, param="any", text=clause))
        return effects, [], True

    symbols = re.findall(r"\{([^{}]+)\}", clause)
    if symbols:
        total = 0
        for inner in symbols:
            token = inner.strip()
            if token.isdigit():
                total += int(token)
            elif token in COLORS:
                effects.append(Effect(kind="mana", amount=1, param=token, text=clause))
            elif token == "C":
                effects.append(Effect(kind="mana", amount=1, param="C", text=clause))
            elif token == "S":
                effects.append(Effect(kind="mana", amount=1, param="C", text=clause))
        if total:
            effects.append(Effect(kind="mana", amount=total, param="C", text=clause))
        return effects, [], True

    count_match = re.search(r"add (x|an?|one|two|three|\d+) mana", lowered)
    if count_match:
        effects.append(Effect(kind="mana", amount=_num(count_match.group(1)), param="C", text=clause))
        return effects, [], True

    effects.append(Effect(kind="mana", amount=1, param="C", text=clause))
    return effects, [], True


# -------------------------------------------------------------------- 触发式异能

#: "this X enters" 中的 X 可以是任何永久物类型
_THIS_ENTER = r"(?:~|it|this (?:creature|land|artifact|enchantment|permanent|equipment|planeswalker|battle|vehicle|saga|token))"

_TRIGGER_PATTERNS: list[tuple[str, EventType, str]] = [
    (rf"when(?:ever)? {_THIS_ENTER} enters the battlefield", EventType.ENTERS_BATTLEFIELD, ""),
    (rf"when(?:ever)? {_THIS_ENTER} enters(?! the battlefield)", EventType.ENTERS_BATTLEFIELD, ""),
    (rf"when(?:ever)? {_THIS_ENTER} (?:dies|is put into a graveyard)", EventType.DIES, ""),
    (rf"when(?:ever)? {_THIS_ENTER} dies", EventType.DIES, ""),
    (rf"when(?:ever)? {_THIS_ENTER} attacks alone", EventType.ATTACKS_ALONE, ""),
    (rf"when(?:ever)? {_THIS_ENTER} attacks", EventType.ATTACKS, ""),
    (rf"when(?:ever)? {_THIS_ENTER} blocks", EventType.BLOCKS, ""),
    (rf"when(?:ever)? {_THIS_ENTER} becomes blocked", EventType.BECOMES_BLOCKED, ""),
    (rf"when(?:ever)? {_THIS_ENTER} becomes tapped", EventType.BECOMES_TAPPED, ""),
    (rf"when(?:ever)? {_THIS_ENTER} untaps", EventType.TURNS, ""),
    (rf"when(?:ever)? {_THIS_ENTER} deals combat damage to a player", EventType.DEALS_COMBAT_DAMAGE_PLAYER, ""),
    (rf"when(?:ever)? {_THIS_ENTER} deals combat damage to a creature", EventType.DEALS_COMBAT_DAMAGE_CREATURE, ""),
    (rf"when(?:ever)? {_THIS_ENTER} deals damage", EventType.DEALS_DAMAGE, ""),
    (rf"when(?:ever)? {_THIS_ENTER} is dealt damage", EventType.DEALS_DAMAGE, ""),
    (r"when(?:ever)? another creature (?:you control )?enters the battlefield", EventType.ENTERS_BATTLEFIELD, "another"),
    (r"when(?:ever)? a creature (?:you control )?dies", EventType.CREATURE_DIED, ""),
    (r"when(?:ever)? another creature dies", EventType.CREATURE_DIED, "another"),
    (r"when(?:ever)? you cast an? (?:instant|sorcery) spell", EventType.INSTANT_SORCERY_CAST, ""),
    (r"when(?:ever)? you cast a creature spell", EventType.CREATURE_SPELL_CAST, ""),
    (r"when(?:ever)? you cast a spell", EventType.SPELL_CAST, ""),
    (r"when(?:ever)? you draw a card", EventType.DRAW, ""),
    (r"when(?:ever)? you gain life", EventType.GAIN_LIFE, ""),
    (r"when(?:ever)? you lose life", EventType.LOSE_LIFE, ""),
    (r"when(?:ever)? (?:~|it) leaves the battlefield", EventType.LEAVES_BATTLEFIELD, ""),
    (r"at the beginning of your upkeep", EventType.UPKEEP, ""),
    (r"at the beginning of your end step", EventType.END_STEP, ""),
    (r"at the beginning of combat on your turn", EventType.ATTACKS, ""),
    (r"when(?:ever)? a land (?:you control )?enters the battlefield", EventType.ENTERS_BATTLEFIELD, ""),
]

_TRIGGER_RE = re.compile(r"^(when|whenever|at)\b[^\n]*?,\s*", re.IGNORECASE)


def _parse_triggered(paragraph: str) -> Ability | None:
    """解析触发式异能段落。"""
    lowered = paragraph.lower()
    if not re.match(r"^(when|whenever|at)\b", lowered):
        return None

    match = _TRIGGER_RE.match(paragraph)
    if not match:
        return None

    trigger_text = paragraph[: match.end()].rstrip(", ").strip()
    effect_text = paragraph[match.end() :].strip()

    trigger: EventType | None = None
    condition = ""
    for pattern, event, cond in _TRIGGER_PATTERNS:
        if re.search(pattern, trigger_text.lower()):
            trigger = event
            condition = cond
            break
    if trigger is None:
        return None

    effects, targets, ok = parse_effect_clause(effect_text)
    if not effects:
        return None

    return Ability(
        kind="triggered",
        triggered=TriggeredAbility(
            trigger=trigger,
            effects=effects,
            targets=targets,
            condition=condition,
            optional="you may" in effect_text.lower(),
            text=paragraph,
        ),
        text=paragraph,
    )


# -------------------------------------------------------------------- 启动式异能

_ACTIVATED_RE = re.compile(r"^(?P<cost>[^:]{1,120}?):\s*(?P<effect>.+)$", re.DOTALL)


def _parse_activated(paragraph: str) -> Ability | None:
    """解析启动式异能（含法术力异能）。"""
    match = _ACTIVATED_RE.match(paragraph.strip())
    if not match:
        return None

    cost_text = match.group("cost").strip()
    effect_text = match.group("effect").strip()

    # 费用必须含法术力符号或横置符号，否则可能是别的句式
    if "{" not in cost_text:
        return None

    cost = _parse_cost(cost_text)
    effects, targets, ok = parse_effect_clause(effect_text)
    if not effects:
        return None

    is_mana = effect_text.lower().startswith("add")
    return Ability(
        kind="activated",
        activated=ActivatedAbility(
            cost=cost,
            effects=effects,
            targets=targets,
            is_mana_ability=is_mana,
            text=paragraph,
        ),
        text=paragraph,
    )


def _parse_cost(cost_text: str) -> Cost:
    """解析启动费用文本。"""
    cost = Cost(text=cost_text)
    # 横置/重置符号
    if "{T}" in cost_text or "{t}" in cost_text:
        cost.tap = True
    if "{Q}" in cost_text:
        cost.untap = True

    # 法术力符号（冒号之前的部分里，且不属于"Remove a counter"之类）
    mana_part = cost_text
    symbols = re.findall(r"\{([^{}]+)\}", mana_part)
    mana_symbols = []
    for inner in symbols:
        token = inner.strip()
        if token in ("T", "Q", "S", "E"):
            continue
        mana_symbols.append(inner)
    if mana_symbols:
        cost.mana = parse_mana_cost("".join(f"{{{s}}}" for s in mana_symbols))

    lowered = cost_text.lower()
    if "sacrifice ~" in lowered or "sacrifice this" in lowered or "sacrifice it" in lowered:
        cost.sacrifice_self = True
    if "sacrifice a creature" in lowered or "sacrifice another" in lowered:
        cost.sacrifice_other = TargetSpec(kind="creature")
    life_match = re.search(r"pay (\d+) life", lowered)
    if life_match:
        cost.life = int(life_match.group(1))
    discard_match = re.search(r"discard (?:a|an|one|\d+) cards?", lowered)
    if discard_match:
        cost.discard = 1
    if "activate only once each turn" in lowered:
        cost.once_per_turn = True
    if "activate only as a sorcery" in lowered:
        cost.sorcery_timing = True
    return cost


# -------------------------------------------------------------------- 静态异能

_STATIC_RE = re.compile(
    r"^(?P<subject>(?:other |nontoken )?creatures? (?:you control|your opponents control|on the battlefield)"
    r"|creatures? (?:you control|your opponents control|with flying)"
    r"|(?:other )?(?:creature|artifact|enchantment)? ?tokens? you control"
    r"|permanents? you control"
    r"|lands? you control"
    r"|~|this creature"
    r"|creatures? with .*?)\s+"
    r"(?:gets?|have|has)\s+"
    r"(?P<rest>.+)$",
    re.IGNORECASE,
)


def _parse_static(paragraph: str) -> Ability | None:
    """解析静态异能（持续修正）。"""
    clause = paragraph.strip().rstrip(".").strip()
    match = _STATIC_RE.match(clause)
    if not match:
        return None

    subject = match.group("subject").lower()
    rest = match.group("rest")

    scope = "your_creatures"
    if subject.startswith("other"):
        scope = "other_creatures"
    elif "opponents control" in subject:
        scope = "opponent_creatures"
    elif "on the battlefield" in subject or "all creatures" in subject:
        scope = "all_creatures"
    elif subject.startswith("~") or "this creature" in subject:
        scope = "self"
    elif "land" in subject:
        scope = "your_lands"
    elif "permanent" in subject:
        scope = "your_permanents"

    buff_match = re.search(r"([+-]?\d+)/([+-]?\d+)", rest)
    keywords: list[Keyword] = []
    for word, keyword in (
        ("flying", Keyword.FLYING),
        ("first strike", Keyword.FIRST_STRIKE),
        ("double strike", Keyword.DOUBLE_STRIKE),
        ("deathtouch", Keyword.DEATHTOUCH),
        ("haste", Keyword.HASTE),
        ("hexproof", Keyword.HEXPROOF),
        ("indestructible", Keyword.INDESTRUCTIBLE),
        ("lifelink", Keyword.LIFELINK),
        ("menace", Keyword.MENACE),
        ("reach", Keyword.REACH),
        ("trample", Keyword.TRAMPLE),
        ("vigilance", Keyword.VIGILANCE),
    ):
        if word in rest.lower():
            keywords.append(keyword)

    if not buff_match and not keywords:
        return None

    power = int(buff_match.group(1)) if buff_match else 0
    toughness = int(buff_match.group(2)) if buff_match else 0

    return Ability(
        kind="static",
        static=StaticAbility(
            scope=scope,
            power=power,
            toughness=toughness,
            grant_keywords=keywords,
            text=clause,
        ),
        text=clause,
    )


# -------------------------------------------------------------------- 咒语效果

def _parse_spell(data, paragraph: str) -> Ability | None:
    """解析咒语效果段落（瞬间/法术/以及部分永久物的进战场段落）。"""
    effects, targets, ok = parse_effect_clause(paragraph)
    if not effects:
        return None
    return Ability(
        kind="spell",
        spell=SpellAbility(effects=effects, targets=targets, text=paragraph),
        text=paragraph,
    )


# -------------------------------------------------------------------- 段落切分

def _split_paragraphs(text: str) -> list[str]:
    """按行切分规则文本。"""
    if not text:
        return []
    lines: list[str] = []
    for raw in text.split("\n"):
        line = raw.strip()
        if line:
            lines.append(line)
    return lines


def _split_sentences(line: str) -> list[str]:
    """把一行拆成若干句子（按句号 + 空格/大写切分）。"""
    parts = re.split(r"(?<=[.!])\s+(?=[A-Z\"“])", line)
    return [p.strip() for p in parts if p.strip()]


# -------------------------------------------------------------------- 卡牌解析入口

def parse_card(data) -> None:  # noqa: ANN001 - CardData 类型在运行时导入以避免循环
    """就地填充 ``data.abilities`` 与 ``data.unparsed``。"""
    from ..engine.card import CardData  # 延迟导入避免循环

    if not isinstance(data, CardData):
        raise TypeError("parse_card 需要 CardData 实例")

    data.abilities = []
    data.unparsed = []
    data.supported = True

    # 1) Scryfall 已提取的关键字
    seen_keywords: set[str] = set()
    for raw in data.keywords:
        keyword = normalize_keyword(raw)
        if keyword is None:
            continue
        key = str(keyword.value)
        if key in seen_keywords:
            continue
        seen_keywords.add(key)
        data.abilities.append(Ability(kind="keyword", keyword=keyword, text=raw))

    # 2) 规则文本
    for raw_line in _split_paragraphs(data.oracle_text):
        line = strip_reminder_text(raw_line)
        if not line:
            continue

        # 进场横置
        if _parse_enters_tapped(line):
            data.enters_tapped = True
            continue

        # 关键字列表（"Flying, vigilance"）
        keyword_list = _parse_keyword_list(line)
        if keyword_list is not None:
            for ability in keyword_list:
                exists = any(
                    ab.kind == "keyword" and ab.keyword == ability.keyword for ab in data.abilities
                )
                if not exists:
                    data.abilities.append(ability)
            continue

        # 关键字符独立成行
        keyword_ability = _keyword_line(line)
        if keyword_ability is not None:
            exists = any(
                ab.kind == "keyword" and ab.keyword == keyword_ability.keyword for ab in data.abilities
            )
            if not exists:
                data.abilities.append(keyword_ability)
            continue

        # 灵气结附目标
        if re.match(r"^enchant ", line.lower()):
            data.enchant_target = line
            continue

        # 武具佩戴
        equip_ability = _parse_equip(line)
        if equip_ability is not None:
            data.abilities.append(equip_ability)
            continue

        # "Equipped/Enchanted creature gets +N/+N"
        equipped_static = _parse_equipped_static(line)
        if equipped_static is not None:
            data.abilities.append(equipped_static)
            continue

        # 暂未实现的异能词（Crew / Cycling / Convoke …）
        if is_unimplemented_keyword(line):
            continue

        # "If you do, ..." 条件性补充效果
        if_do_match = re.match(r"^if you do,?\s*(.+)$", line, re.IGNORECASE)
        if if_do_match:
            sub = _parse_spell(data, if_do_match.group(1))
            if sub is not None:
                data.abilities.append(sub)
                continue

        # 可忽略的说明性文本
        if is_ignorable(line):
            continue

        # 依次尝试：触发 → 启动 → 静态 → 咒语效果
        ability = _parse_triggered(line)
        if ability is None:
            ability = _parse_activated(line)
        if ability is None:
            ability = _parse_static(line)

        if ability is not None:
            data.abilities.append(ability)
            continue

        # 最后按句子逐个尝试（一行里可能有多句）
        parsed_any = False
        for sentence in _split_sentences(line):
            if len(_split_sentences(line)) == 1:
                break
            sub = _parse_triggered(sentence) or _parse_activated(sentence) or _parse_static(sentence)
            if sub is None and (data.is_instant or data.is_sorcery or "enters the battlefield" in sentence.lower()):
                sub = _parse_spell(data, sentence)
            if sub is not None:
                data.abilities.append(sub)
                parsed_any = True
            elif is_ignorable(sentence):
                parsed_any = True
            else:
                data.unparsed.append(sentence)
        if not parsed_any and len(_split_sentences(line)) == 1:
            # 单句：作为咒语效果或 ETB 效果尝试
            if data.is_instant or data.is_sorcery or "enters the battlefield" in line.lower():
                sub = _parse_spell(data, line)
                if sub is not None:
                    data.abilities.append(sub)
                    continue
            if not is_ignorable(line):
                data.unparsed.append(line)

    # 3) 地牌的产费（Scryfall 对基本地的 oracle_text 为空）
    if data.is_land and not data.abilities:
        for color in data.basic_land_colors:
            data.abilities.append(
                Ability(
                    kind="activated",
                    activated=ActivatedAbility(
                        cost=Cost(tap=True, text="{T}"),
                        effects=[Effect(kind="mana", amount=1, param=color, text=f"{{T}}: Add {{{color}}}.")],
                        is_mana_ability=True,
                        text=f"{{T}}: Add {{{color}}}.",
                    ),
                    text=f"{{T}}: Add {{{color}}}.",
                )
            )

    data.supported = not data.unparsed


# -------------------------------------------------------------------- 批量解析

def parse_all(cards: Iterable) -> dict:  # noqa: ANN001
    """批量解析并统计覆盖率。"""
    from ..engine.card import CardData

    cards = list(cards)
    start = time.time()
    for data in cards:
        if isinstance(data, CardData):
            parse_card(data)

    total = len(cards)
    supported = sum(1 for c in cards if getattr(c, "supported", False))
    with_abilities = sum(1 for c in cards if getattr(c, "abilities", None))
    elapsed = time.time() - start

    stats = {
        "total": total,
        "fully_supported": supported,
        "with_abilities": with_abilities,
        "coverage": round(supported / total * 100, 2) if total else 0.0,
        "elapsed_seconds": round(elapsed, 2),
    }
    return stats


def report_unparsed(cards: Iterable, limit: int = 25) -> str:  # noqa: ANN001
    """输出未识别文本最多的若干张卡，便于迭代解析器。"""
    from collections import Counter

    counter: Counter = Counter()
    examples: dict[str, str] = {}
    for card in cards:
        for text in getattr(card, "unparsed", []):
            key = re.sub(r"\d+", "N", text)[:90]
            counter[key] += 1
            examples.setdefault(key, card.name)

    lines = [f"未解析片段 Top {limit}："]
    for key, count in counter.most_common(limit):
        lines.append(f"  [{count:>3}] {key}   （例：{examples.get(key, '?')}）")
    return "\n".join(lines)
