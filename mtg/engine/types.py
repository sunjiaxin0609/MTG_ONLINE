"""基础枚举与常量：颜色、区域、阶段、类别、关键字。"""
from __future__ import annotations

from enum import Enum, Flag, auto


class Color(Flag):
    """五色的位标志，便于做颜色集合运算。"""

    NONE = 0
    WHITE = auto()
    BLUE = auto()
    RED = auto()
    BLACK = auto()
    GREEN = auto()

    @property
    def letters(self) -> list[str]:
        return [c.letter for c in (Color.WHITE, Color.BLUE, Color.BLACK, Color.RED, Color.GREEN) if c in self]

    @classmethod
    def parse(cls, symbols: object) -> "Color":
        """从 Scryfall 的 colors 数组（如 ["W","U"]）解析。"""
        result = cls.NONE
        if not symbols:
            return result
        for sym in symbols:
            result |= COLOR_BY_LETTER.get(str(sym).upper(), cls.NONE)
        return result


COLOR_BY_LETTER: dict[str, Color] = {
    "W": Color.WHITE,
    "U": Color.BLUE,
    "B": Color.BLACK,
    "R": Color.RED,
    "G": Color.GREEN,
}

COLOR_NAMES: dict[Color, str] = {
    Color.WHITE: "白",
    Color.BLUE: "蓝",
    Color.BLACK: "黑",
    Color.RED: "红",
    Color.GREEN: "绿",
}

LETTER_TO_COLOR_NAME: dict[str, str] = {
    "W": "白",
    "U": "蓝",
    "B": "黑",
    "R": "红",
    "G": "绿",
    "C": "无色",
}


class Zone(str, Enum):
    """游戏区域。"""

    LIBRARY = "牌库"
    HAND = "手牌"
    BATTLEFIELD = "战场"
    GRAVEYARD = "坟场"
    STACK = "堆叠"
    EXILE = "放逐区"
    COMMAND = "统帅区"
    OUTSIDE = "游戏外"


class Phase(str, Enum):
    """回合阶段。"""

    UNTAP = "重置"
    UPKEEP = "维持"
    DRAW = "抓牌"
    MAIN_1 = "第一主阶段"
    COMBAT = "战斗"
    MAIN_2 = "第二主阶段"
    END = "结束"
    CLEANUP = "清理"


class Step(str, Enum):
    """战斗阶段内的步骤（其余阶段视为单一同名步骤）。"""

    BEGINNING_OF_COMBAT = "战斗开始"
    DECLARE_ATTACKERS = "宣告攻击者"
    DECLARE_BLOCKERS = "宣告阻挡者"
    COMBAT_DAMAGE = "战斗伤害"
    END_OF_COMBAT = "战斗结束"


#: 阶段的先后次序，用于推进回合
PHASE_ORDER: list[Phase] = [
    Phase.UNTAP,
    Phase.UPKEEP,
    Phase.DRAW,
    Phase.MAIN_1,
    Phase.COMBAT,
    Phase.MAIN_2,
    Phase.END,
    Phase.CLEANUP,
]

#: 战斗阶段内部步骤次序
COMBAT_STEPS: list[Step] = [
    Step.BEGINNING_OF_COMBAT,
    Step.DECLARE_ATTACKERS,
    Step.DECLARE_BLOCKERS,
    Step.COMBAT_DAMAGE,
    Step.END_OF_COMBAT,
]

#: 法术时机受限的阶段（只能施放瞬间与具闪现的牌）
SORCERY_RESTRICTED_PHASES = {
    Phase.UNTAP,
    Phase.DRAW,
    Phase.CLEANUP,
    Phase.COMBAT,
}


# ---------------------------------------------------------------- 牌张类别

SUPERTYPES = frozenset({"Legendary", "Basic", "Snow", "World", "Ongoing"})

#: 万智牌的"牌张类别"
CARD_TYPES = frozenset(
    {
        "Creature",
        "Instant",
        "Sorcery",
        "Enchantment",
        "Artifact",
        "Land",
        "Planeswalker",
        "Battle",
        "Kindred",
        "Tribal",
    }
)

PERMANENT_TYPES = frozenset(
    {"Creature", "Enchantment", "Artifact", "Land", "Planeswalker", "Battle", "Kindred", "Tribal"}
)

#: 地的基本类别 → 产出法术力颜色
BASIC_LAND_MANA: dict[str, str] = {
    "Plains": "W",
    "Island": "U",
    "Swamp": "B",
    "Mountain": "R",
    "Forest": "G",
}


# ---------------------------------------------------------------- 关键字异能

class Keyword(str, Enum):
    """常青关键字异能（异能词）。"""

    DEATHTOUCH = "Deathtouch"
    DEFENDER = "Defender"
    DOUBLE_STRIKE = "Double strike"
    FIRST_STRIKE = "First strike"
    FLASH = "Flash"
    FLYING = "Flying"
    HASTE = "Haste"
    HEXPROOF = "Hexproof"
    INDESTRUCTIBLE = "Indestructible"
    LIFELINK = "Lifelink"
    MENACE = "Menace"
    PROWESS = "Prowess"
    REACH = "Reach"
    TRAMPLE = "Trample"
    VIGILANCE = "Vigilance"
    WARD = "Ward"
    SHROUD = "Shroud"
    PROTECTION = "Protection"
    FLYING_CRAWLER = "Flying"  # 兼容别名


#: Scryfall 关键字名 → 引擎关键字枚举
KEYWORD_ALIASES: dict[str, Keyword] = {
    "Deathtouch": Keyword.DEATHTOUCH,
    "Defender": Keyword.DEFENDER,
    "Double Strike": Keyword.DOUBLE_STRIKE,
    "First Strike": Keyword.FIRST_STRIKE,
    "Flash": Keyword.FLASH,
    "Flying": Keyword.FLYING,
    "Haste": Keyword.HASTE,
    "Hexproof": Keyword.HEXPROOF,
    "Indestructible": Keyword.INDESTRUCTIBLE,
    "Lifelink": Keyword.LIFELINK,
    "Menace": Keyword.MENACE,
    "Prowess": Keyword.PROWESS,
    "Reach": Keyword.REACH,
    "Trample": Keyword.TRAMPLE,
    "Vigilance": Keyword.VIGILANCE,
    "Ward": Keyword.WARD,
    "Shroud": Keyword.SHROUD,
    "Protection": Keyword.PROTECTION,
}


def normalize_keyword(name: str) -> Keyword | None:
    """把 Scryfall 的 keywords 字符串映射到引擎关键字。"""
    if not name:
        return None
    cleaned = " ".join(name.split())
    if cleaned in KEYWORD_ALIASES:
        return KEYWORD_ALIASES[cleaned]
    # "Ward {2}" / "Protection from white" 这类带参数的写法
    head = cleaned.split("{")[0].split(" from ")[0].strip()
    return KEYWORD_ALIASES.get(head)


# ---------------------------------------------------------------- 效果/事件类型

class EventType(str, Enum):
    """游戏中可被触发式异能监听的事件。"""

    ENTERS_BATTLEFIELD = "enters_battlefield"
    LEAVES_BATTLEFIELD = "leaves_battlefield"
    DIES = "dies"
    CREATURE_DIED = "creature_died"
    ATTACKS = "attacks"
    ATTACKS_ALONE = "attacks_alone"
    BLOCKS = "blocks"
    BECOMES_BLOCKED = "becomes_blocked"
    BECOMES_BLOCKED_BY = "becomes_blocked_by"
    DEALS_COMBAT_DAMAGE_PLAYER = "combat_damage_to_player"
    DEALS_COMBAT_DAMAGE_CREATURE = "combat_damage_to_creature"
    DEALS_DAMAGE = "deals_damage"
    SPELL_CAST = "spell_cast"
    CREATURE_SPELL_CAST = "creature_spell_cast"
    INSTANT_SORCERY_CAST = "instant_sorcery_cast"
    LAND_PLAYED = "land_played"
    UPKEEP = "upkeep"
    END_STEP = "end_step"
    DRAW = "draw"
    DISCARD = "discard"
    GAIN_LIFE = "gain_life"
    LOSE_LIFE = "lose_life"
    TURNS = "turns"
    UNTAPS = "untaps"
    BECOMES_TAPPED = "becomes_tapped"
    SACRIFICE = "sacrifice"
    TARGETED = "targeted"
    COUNTERED = "countered"
    ATTACKER_DECLARED = "attacker_declared"
    EQUIP = "equip"
    LOYALTY_CHANGE = "loyalty_change"


class Layer(int, Enum):
    """持续效应层系统（规则 613）。数值小的先应用。"""

    COPY = 1  # 复制效应
    CONTROL = 2  # 控制权变更
    TEXT = 3  # 文本变更
    TYPE = 4  # 类别变更
    COLOR = 5  # 颜色变更
    ABILITY = 6  # 异能增减
    PT_SET = 7  # P/T 设定（如 "成为 3/3"）
    PT_MODIFY = 8  # P/T 加减
    PT_SWITCH = 9  # 攻防互换
