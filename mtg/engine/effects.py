"""效果原语：引擎中"具体做什么"的原子操作。

每个 Effect 是一个惰性描述（由 oracle 解析器生成），在结算时由
:func:`apply_effect` 结合上下文执行。这样异能可以被序列化、检查，
也能在 AI 里做代价评估而不必真的执行。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .card import CardData, Permanent
from .types import Color, Keyword, Zone

if TYPE_CHECKING:
    from .game import Game
    from .player import Player
    from .stack import StackItem


# -------------------------------------------------------------------- 目标选择器

@dataclass
class Selector:
    """描述"作用于谁"。解析器生成，执行时解析成具体对象列表。"""

    #: 'self' | 'controller' | 'opponent' | 'each_opponent' | 'each_player'
    #: 'target' | 'targets'（取自已选目标）
    #: 'all_creatures' | 'all_opponent_creatures' | 'your_creatures'
    #: 'all_permanents' | 'all_lands' ...
    who: str
    #: 额外的过滤条件
    filter: str = ""
    #: 目标索引（当异能有多个目标时）
    index: int = 0

    def __str__(self) -> str:
        return self.who


# -------------------------------------------------------------------- 衍生物规格

@dataclass
class TokenSpec:
    """一个衍生物的定义。"""

    name: str
    power: int = 0
    toughness: int = 0
    colors: list[str] = field(default_factory=list)
    types: list[str] = field(default_factory=lambda: ["Creature"])
    subtypes: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    count: int = 1
    tapped: bool = False

    def to_card_data(self) -> CardData:
        type_line = " ".join(self.types)
        if self.subtypes:
            type_line += " — " + " ".join(self.subtypes)
        return CardData(
            card_id=f"token:{self.name}",
            name=self.name,
            mana_cost="",
            cmc=0.0,
            type_line=type_line,
            oracle_text=("、".join(self.keywords) if self.keywords else ""),
            power=str(self.power) if self.power else None,
            toughness=str(self.toughness) if self.toughness else None,
            colors=list(self.colors),
            keywords=list(self.keywords),
        )


#: 常见衍生物预设
PRESET_TOKENS: dict[str, TokenSpec] = {
    "Treasure": TokenSpec("Treasure", types=["Artifact"], subtypes=["Treasure"]),
    "Food": TokenSpec("Food", types=["Artifact"], subtypes=["Food"]),
    "Clue": TokenSpec("Clue", types=["Artifact"], subtypes=["Clue"]),
    "Blood": TokenSpec("Blood", types=["Artifact"], subtypes=["Blood"]),
    "Powerstone": TokenSpec("Powerstone", types=["Artifact"], subtypes=["Powerstone"]),
    "Map": TokenSpec("Map", types=["Artifact"], subtypes=["Map"]),
    "Incubator": TokenSpec("Incubator", types=["Artifact"], subtypes=["Incubator"]),
}


# -------------------------------------------------------------------- 效果

@dataclass
class Effect:
    """一个原子效果。``kind`` 决定语义，其余字段为参数。"""

    kind: str
    amount: int = 0
    amount2: int = 0
    target: Selector | None = None
    who: Selector | None = None

    #: 文本类参数：颜色、异能名、指示物种类、衍生物规格等
    param: str = ""
    param2: str = ""
    token: TokenSpec | None = None
    keywords: list[str] = field(default_factory=list)

    #: 持续时间: 'instant' | 'end_of_turn' | 'permanent'
    duration: str = "instant"
    #: 条件文本（未能解析的条件会保留在此）
    condition: str = ""
    #: 原始规则文本，便于回溯与 UI 展示
    text: str = ""

    def describe(self, cn: bool = True) -> str:
        """生成一句人类可读（中文）的效果描述。"""
        return describe_effect(self)


def describe_effect(effect: Effect) -> str:
    """把效果翻成中文短句，供日志与卡面提示使用。"""
    who_cn = {
        "controller": "你",
        "opponent": "目标对手",
        "each_opponent": "每位对手",
        "each_player": "每位牌手",
        "self": "它",
        "target": "目标",
        "all_creatures": "所有生物",
        "your_creatures": "你操控的生物",
        "opponent_creatures": "对手操控的生物",
        "all_opponent_creatures": "对手操控的所有生物",
        "all_permanents": "所有永久物",
    }

    def tgt(e: Effect) -> str:
        sel = e.target or e.who
        return who_cn.get(sel.who, sel.who) if sel else ""

    kind = effect.kind
    n = effect.amount
    if kind == "draw":
        return f"抓 {n} 张牌"
    if kind == "gain_life":
        return f"{tgt(effect)}获得 {n} 点生命"
    if kind == "lose_life":
        return f"{tgt(effect)}失去 {n} 点生命"
    if kind == "damage":
        return f"对{tgt(effect)}造成 {n} 点伤害"
    if kind == "destroy":
        return f"消灭{tgt(effect)}"
    if kind == "exile":
        return f"放逐{tgt(effect)}"
    if kind == "bounce":
        return f"将{tgt(effect)}移回其拥有者手上"
    if kind == "tap":
        return f"横置{tgt(effect)}"
    if kind == "untap":
        return f"重置{tgt(effect)}"
    if kind == "counter":
        return "反击目标咒语"
    if kind == "mana":
        return f"加 {n} 点{effect.param or '法术力'}"
    if kind == "token":
        spec = effect.token
        name = spec.name if spec else "衍生物"
        return f"生成 {n if n else 1} 个{name}衍生物"
    if kind == "buff":
        sign_p = f"+{n}" if n >= 0 else str(n)
        sign_t = f"+{effect.amount2}" if effect.amount2 >= 0 else str(effect.amount2)
        until = "直到回合结束" if effect.duration == "end_of_turn" else ""
        return f"{tgt(effect)}得到 {sign_p}/{sign_t} {until}".strip()
    if kind == "add_counter":
        return f"在{tgt(effect)}上放置 {n} 个{effect.param}指示物"
    if kind == "sacrifice":
        return f"牺牲{tgt(effect)}"
    if kind == "discard":
        return f"{tgt(effect)}弃 {n} 张牌"
    if kind == "mill":
        return f"{tgt(effect)}磨 {n} 张牌"
    if kind == "scry":
        return f"占卜 {n}"
    if kind == "surveil":
        return f"侦察 {n}"
    if kind == "fight":
        return "令目标生物互相斗殴"
    if kind == "grant_keyword":
        kws = "、".join(effect.keywords)
        until = "直到回合结束" if effect.duration == "end_of_turn" else ""
        return f"{tgt(effect)}获得{kws} {until}".strip()
    if kind == "gain_control":
        return f"获得{tgt(effect)}的操控权"
    if kind == "prevent_damage":
        return f"防止接下来对{tgt(effect)}的 {n} 点伤害"
    if kind == "unparsed":
        return f"（未实现：{effect.text}）"
    return f"{kind} {n}"


# -------------------------------------------------------------------- 执行上下文

@dataclass
class Context:
    """效果结算上下文。"""

    game: "Game"
    source: Any  # Permanent 或 Card（咒语）
    controller: "Player"
    targets: list[Any] = field(default_factory=list)
    x_value: int = 0

    @property
    def source_permanent(self) -> Permanent | None:
        return self.source if isinstance(self.source, Permanent) else None


# -------------------------------------------------------------------- 解析选择器

def resolve_selector(selector: Selector | None, ctx: Context) -> list[Any]:
    """把选择器解析为具体对象列表。"""
    if selector is None:
        return []
    game = ctx.game
    me = ctx.controller
    who = selector.who

    if who == "self":
        return [ctx.source] if ctx.source is not None else []
    if who == "controller":
        return [me]
    if who == "opponent":
        return [game.other_player(me)]
    if who == "each_opponent":
        return [game.other_player(me)]
    if who == "each_player":
        return [me, game.other_player(me)]
    if who == "target" or who == "targets":
        if selector.index < len(ctx.targets):
            return [ctx.targets[selector.index]]
        return list(ctx.targets)
    if who == "all_creatures":
        return [p for player in game.players for p in player.creatures]
    if who == "your_creatures":
        return list(me.creatures)
    if who in ("opponent_creatures", "all_opponent_creatures"):
        return list(game.other_player(me).creatures)
    if who == "all_permanents":
        return [p for player in game.players for p in player.permanents]
    if who == "all_lands":
        return [p for player in game.players for p in player.lands]
    if who == "your_lands":
        return list(me.lands)
    if who == "all_artifacts":
        return [p for player in game.players for p in player.permanents if p.is_artifact]
    if who == "all_enchantments":
        return [p for player in game.players for p in player.permanents if p.is_enchantment]
    if who == "your_library_top":
        return [me.library[0]] if me.library else []
    if who == "your_graveyard":
        return list(me.graveyard)
    return []


# -------------------------------------------------------------------- 效果执行

def apply_effect(effect: Effect, ctx: Context) -> bool:
    """执行一个效果，返回是否成功执行。"""
    handler = _HANDLERS.get(effect.kind)
    if handler is None:
        ctx.game.log(f"（未实现的效果：{effect.kind} / {effect.text}）")
        return False
    return handler(effect, ctx)


def _amount(effect: Effect, ctx: Context) -> int:
    """处理 {X} 费用——若效果是 X 相关，用 x_value 替换。"""
    if effect.param == "X":
        return ctx.x_value
    return effect.amount


# ---- 具体处理器 -------------------------------------------------------------

def _h_draw(effect: Effect, ctx: Context) -> bool:
    players = resolve_selector(effect.who or effect.target, ctx)
    for player in players:
        drawn = player.draw(_amount(effect, ctx))
        if drawn:
            ctx.game.log(f"{player.name} 抓 {len(drawn)} 张牌")
    return bool(players)


def _h_gain_life(effect: Effect, ctx: Context) -> bool:
    for player in resolve_selector(effect.who or effect.target, ctx):
        player.gain_life(_amount(effect, ctx))
        ctx.game.log(f"{player.name} 获得 {_amount(effect, ctx)} 点生命")
    return True


def _h_lose_life(effect: Effect, ctx: Context) -> bool:
    for player in resolve_selector(effect.who or effect.target, ctx):
        player.lose_life(_amount(effect, ctx))
        ctx.game.log(f"{player.name} 失去 {_amount(effect, ctx)} 点生命")
    return True


def _h_damage(effect: Effect, ctx: Context) -> bool:
    amount = _amount(effect, ctx)
    source = ctx.source_permanent
    hit = False
    for obj in resolve_selector(effect.target or effect.who, ctx):
        if isinstance(obj, Permanent):
            obj.mark_damage(amount, source)
            ctx.game.log(f"对 {obj.name} 造成 {amount} 点伤害")
            hit = True
        elif hasattr(obj, "damage"):
            obj.damage(amount, source)
            ctx.game.log(f"对 {obj.name} 造成 {amount} 点伤害")
            hit = True
    return hit


def _h_destroy(effect: Effect, ctx: Context) -> bool:
    hit = False
    for obj in resolve_selector(effect.target or effect.who, ctx):
        if isinstance(obj, Permanent):
            ctx.game.destroy_permanent(obj)
            ctx.game.log(f"消灭 {obj.name}")
            hit = True
    return hit


def _h_exile(effect: Effect, ctx: Context) -> bool:
    hit = False
    for obj in resolve_selector(effect.target or effect.who, ctx):
        if isinstance(obj, Permanent):
            ctx.game.exile_permanent(obj)
            ctx.game.log(f"放逐 {obj.name}")
            hit = True
    return hit


def _h_bounce(effect: Effect, ctx: Context) -> bool:
    hit = False
    for obj in resolve_selector(effect.target or effect.who, ctx):
        if isinstance(obj, Permanent):
            ctx.game.return_to_hand(obj)
            ctx.game.log(f"{obj.name} 回到其拥有者手上")
            hit = True
    return hit


def _h_tap(effect: Effect, ctx: Context) -> bool:
    for obj in resolve_selector(effect.target or effect.who, ctx):
        if isinstance(obj, Permanent):
            obj.tap()
            ctx.game.log(f"横置 {obj.name}")
    return True


def _h_untap(effect: Effect, ctx: Context) -> bool:
    for obj in resolve_selector(effect.target or effect.who, ctx):
        if isinstance(obj, Permanent):
            obj.untap()
            ctx.game.log(f"重置 {obj.name}")
    return True


def _h_counter(effect: Effect, ctx: Context) -> bool:
    for obj in resolve_selector(effect.target or effect.who, ctx):
        if hasattr(obj, "countered"):
            obj.countered = True
            ctx.game.log(f"反击了 {getattr(obj, 'name', '咒语')}")
            return True
    return False


def _h_mana(effect: Effect, ctx: Context) -> bool:
    color = effect.param or "C"
    amount = _amount(effect, ctx) or 1
    ctx.controller.add_mana(color, amount)
    ctx.game.log(f"{ctx.controller.name} 加 {amount} 点{color}法术力")
    return True


def _h_token(effect: Effect, ctx: Context) -> bool:
    spec = effect.token
    if spec is None:
        return False
    count = effect.amount or spec.count or 1
    ctx.game.create_tokens(ctx.controller, spec, count, tapped=spec.tapped)
    ctx.game.log(f"{ctx.controller.name} 生成 {count} 个{spec.name}衍生物")
    return True


def _h_buff(effect: Effect, ctx: Context) -> bool:
    power = _amount(effect, ctx)
    toughness = effect.amount2
    for obj in resolve_selector(effect.target or effect.who, ctx):
        if isinstance(obj, Permanent):
            if effect.duration == "end_of_turn":
                ctx.game.add_until_end_of_turn(obj, power, toughness)
            else:
                obj._pt_modify += power
                obj._pt_modify_t += toughness
            ctx.game.log(f"{obj.name} 得到 {power:+d}/{toughness:+d}")
    return True


def _h_add_counter(effect: Effect, ctx: Context) -> bool:
    kind = effect.param or "+1/+1"
    amount = _amount(effect, ctx) or 1
    for obj in resolve_selector(effect.target or effect.who, ctx):
        if isinstance(obj, Permanent):
            obj.add_counter(kind, amount)
            ctx.game.log(f"{obj.name} 获得 {amount} 个{kind}指示物")
    return True


def _h_sacrifice(effect: Effect, ctx: Context) -> bool:
    for obj in resolve_selector(effect.target or effect.who, ctx):
        if isinstance(obj, Permanent):
            ctx.game.sacrifice_permanent(obj)
    return True


def _h_discard(effect: Effect, ctx: Context) -> bool:
    for player in resolve_selector(effect.who or effect.target, ctx):
        player.discard_random(_amount(effect, ctx))
    return True


def _h_mill(effect: Effect, ctx: Context) -> bool:
    for player in resolve_selector(effect.who or effect.target, ctx):
        milled = player.mill(_amount(effect, ctx))
        ctx.game.log(f"{player.name} 磨 {len(milled)} 张牌")
    return True


def _h_scry(effect: Effect, ctx: Context) -> bool:
    """占卜：简化为"留顶或置底"，AI 自动决策，人类玩家由 UI 弹出选择。"""
    player = ctx.controller
    count = _amount(effect, ctx)
    cards = player.library[:count]
    if not cards:
        return False
    bottom = ctx.game.scry_decision(player, cards)
    for card in bottom:
        player.library.remove(card)
        player.library.append(card)
    ctx.game.log(f"{player.name} 占卜 {len(cards)}")
    return True


def _h_surveil(effect: Effect, ctx: Context) -> bool:
    player = ctx.controller
    count = _amount(effect, ctx)
    cards = player.library[:count]
    if not cards:
        return False
    to_grave = ctx.game.surveil_decision(player, cards)
    for card in to_grave:
        player.library.remove(card)
        player.move_to_graveyard(card)
    ctx.game.log(f"{player.name} 侦察 {count}")
    return True


def _h_fight(effect: Effect, ctx: Context) -> bool:
    targets = resolve_selector(effect.target or effect.who, ctx)
    creatures = [t for t in targets if isinstance(t, Permanent) and t.is_creature]
    if len(creatures) < 2:
        return False
    a, b = creatures[0], creatures[1]
    a_dmg = a.power()
    b_dmg = b.power()
    a.mark_damage(b_dmg, b)
    b.mark_damage(a_dmg, a)
    ctx.game.log(f"{a.name} 与 {b.name} 斗殴")
    return True


def _h_grant_keyword(effect: Effect, ctx: Context) -> bool:
    for obj in resolve_selector(effect.target or effect.who, ctx):
        if not isinstance(obj, Permanent):
            continue
        for raw in effect.keywords:
            keyword = _keyword_from_name(raw)
            if keyword is None:
                continue
            if effect.duration == "end_of_turn":
                ctx.game.grant_keyword_until_end_of_turn(obj, keyword)
            else:
                obj._extra_keywords.add(keyword)
    return True


def _h_gain_control(effect: Effect, ctx: Context) -> bool:
    for obj in resolve_selector(effect.target or effect.who, ctx):
        if isinstance(obj, Permanent):
            ctx.game.change_control(obj, ctx.controller)
            ctx.game.log(f"{ctx.controller.name} 获得 {obj.name} 的操控权")
    return True


def _h_equip(effect: Effect, ctx: Context) -> bool:
    """佩戴：把来源武具结附到目标生物上。"""
    source = ctx.source_permanent
    if source is None:
        return False
    for obj in resolve_selector(effect.target or effect.who, ctx):
        if isinstance(obj, Permanent) and obj.is_creature:
            ctx.game.attach(source, obj)
            ctx.game.log(f"{source.name} 佩戴到 {obj.name}")
            return True
    return False


def _h_prevent_damage(effect: Effect, ctx: Context) -> bool:
    for obj in resolve_selector(effect.target or effect.who, ctx):
        ctx.game.replacement.add_prevention(obj, _amount(effect, ctx))
    return True


def _h_unparsed(effect: Effect, ctx: Context) -> bool:
    ctx.game.log(f"（部分异能未实现：{effect.text}）")
    return False


def _keyword_from_name(name: str) -> Keyword | None:
    lowered = name.strip().lower()
    table = {
        "flying": Keyword.FLYING,
        "first strike": Keyword.FIRST_STRIKE,
        "double strike": Keyword.DOUBLE_STRIKE,
        "deathtouch": Keyword.DEATHTOUCH,
        "haste": Keyword.HASTE,
        "hexproof": Keyword.HEXPROOF,
        "indestructible": Keyword.INDESTRUCTIBLE,
        "lifelink": Keyword.LIFELINK,
        "menace": Keyword.MENACE,
        "reach": Keyword.REACH,
        "trample": Keyword.TRAMPLE,
        "vigilance": Keyword.VIGILANCE,
        "defender": Keyword.DEFENDER,
    }
    return table.get(lowered)


_HANDLERS: dict[str, Any] = {
    "draw": _h_draw,
    "gain_life": _h_gain_life,
    "lose_life": _h_lose_life,
    "damage": _h_damage,
    "destroy": _h_destroy,
    "exile": _h_exile,
    "bounce": _h_bounce,
    "tap": _h_tap,
    "untap": _h_untap,
    "counter": _h_counter,
    "mana": _h_mana,
    "token": _h_token,
    "buff": _h_buff,
    "add_counter": _h_add_counter,
    "sacrifice": _h_sacrifice,
    "discard": _h_discard,
    "mill": _h_mill,
    "scry": _h_scry,
    "surveil": _h_surveil,
    "fight": _h_fight,
    "grant_keyword": _h_grant_keyword,
    "gain_control": _h_gain_control,
    "prevent_damage": _h_prevent_damage,
    "equip": _h_equip,
    "unparsed": _h_unparsed,
}
