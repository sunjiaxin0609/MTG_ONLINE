"""卡池数据库：加载标准赛制卡池、解析异能、提供检索与套牌构建。"""
from __future__ import annotations

import json
import os
from typing import Iterable

from ..engine.card import CardData
from .oracle_parser import parse_all, parse_card, report_unparsed

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
POOL_PATH = os.path.join(DATA_DIR, "standard_cards.json")
CACHE_PATH = os.path.join(DATA_DIR, "parsed_pool.json")


class CardDB:
    """标准赛制卡池。"""

    def __init__(self) -> None:
        self.cards: list[CardData] = []
        self.by_name: dict[str, CardData] = {}
        self.by_id: dict[str, CardData] = {}
        self.stats: dict = {}

    # ---------------------------------------------------------------- 构建
    @classmethod
    def load(cls, use_cache: bool = True) -> "CardDB":
        """加载并解析卡池。"""
        db = cls()
        raw_list = db._read_raw()
        db.cards = [db._to_card_data(raw) for raw in raw_list]
        db.cards = [c for c in db.cards if c is not None]

        # 解析异能
        db.stats = parse_all(db.cards)

        for card in db.cards:
            db.by_id[card.card_id] = card
            db.by_name.setdefault(card.name, card)

        db._ensure_basic_lands()
        return db

    def _read_raw(self) -> list[dict]:
        if not os.path.exists(POOL_PATH):
            raise FileNotFoundError(
                f"卡池文件不存在：{POOL_PATH}\n请先运行: python -m mtg.cards.fetch_scryfall"
            )
        with open(POOL_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def _to_card_data(self, raw: dict) -> CardData | None:
        """把 Scryfall 精简记录转成 CardData。"""
        try:
            faces: list[CardData] = []
            for face in raw.get("card_faces", []) or []:
                faces.append(
                    CardData(
                        card_id=raw.get("id", "") + ":" + str(face.get("name", "")),
                        name=face.get("name", ""),
                        mana_cost=face.get("mana_cost", ""),
                        cmc=face.get("cmc", 0.0) or 0.0,
                        type_line=face.get("type_line", ""),
                        oracle_text=face.get("oracle_text", "") or "",
                        power=face.get("power"),
                        toughness=face.get("toughness"),
                        loyalty=face.get("loyalty"),
                        colors=face.get("colors", []) or [],
                        image_small=(face.get("image_uris") or {}).get("small"),
                        image_normal=(face.get("image_uris") or {}).get("normal"),
                    )
                )

            image = raw.get("image_uris") or {}
            return CardData(
                card_id=raw.get("id", ""),
                name=raw.get("name", ""),
                mana_cost=raw.get("mana_cost", "") or "",
                cmc=raw.get("cmc", 0.0) or 0.0,
                type_line=raw.get("type_line", "") or "",
                oracle_text=raw.get("oracle_text", "") or "",
                power=raw.get("power"),
                toughness=raw.get("toughness"),
                loyalty=raw.get("loyalty"),
                defense=raw.get("defense"),
                colors=raw.get("colors", []) or [],
                color_identity=raw.get("color_identity", []) or [],
                keywords=raw.get("keywords", []) or [],
                produced_mana=raw.get("produced_mana", []) or [],
                set_code=raw.get("set", "") or "",
                set_name=raw.get("set_name", "") or "",
                rarity=raw.get("rarity", "") or "",
                layout=raw.get("layout", "normal") or "normal",
                image_small=image.get("small"),
                image_normal=image.get("normal"),
                faces=faces,
            )
        except Exception:  # noqa: BLE001 - 单张卡异常不应中断整个卡池加载
            return None

    def _ensure_basic_lands(self) -> None:
        """确保五种基本地一定存在（卡池里某些系列可能缺）。"""
        basics = {
            "Plains": ("W", "白"),
            "Island": ("U", "蓝"),
            "Swamp": ("B", "黑"),
            "Mountain": ("R", "红"),
            "Forest": ("G", "绿"),
        }
        for name, (color, _cn) in basics.items():
            if name in self.by_name:
                continue
            data = CardData(
                card_id=f"basic:{name}",
                name=name,
                mana_cost="",
                cmc=0.0,
                type_line=f"Basic Land — {name}",
                oracle_text=f"{{T}}: Add {{{color}}}.",
                colors=[],
                set_code="BASIC",
                rarity="common",
            )
            parse_card(data)
            self.cards.append(data)
            self.by_name[name] = data
            self.by_id[data.card_id] = data

    # ---------------------------------------------------------------- 检索
    def get(self, name: str) -> CardData | None:
        return self.by_name.get(name)

    def search(self, query: str = "", filters: dict | None = None, limit: int = 60) -> list[CardData]:
        """按关键字与条件检索卡牌。"""
        filters = filters or {}
        query = (query or "").strip().lower()
        results: list[CardData] = []

        for card in self.cards:
            if query and query not in card.name.lower() and query not in card.type_line.lower():
                continue
            if filters.get("type") and filters["type"] not in card.type_line:
                continue
            if filters.get("color") and filters["color"] not in card.colors:
                continue
            if filters.get("cmc_max") is not None and card.cmc > filters["cmc_max"]:
                continue
            if filters.get("cmc_min") is not None and card.cmc < filters["cmc_min"]:
                continue
            if filters.get("rarity") and card.rarity != filters["rarity"]:
                continue
            if filters.get("supported_only") and not card.supported:
                continue
            if filters.get("creature") and not card.is_creature:
                continue
            results.append(card)
            if len(results) >= limit:
                break
        return results

    def creatures(self, supported_only: bool = True) -> list[CardData]:
        return [
            c
            for c in self.cards
            if c.is_creature and (not supported_only or c.supported) and c.layout == "normal"
        ]

    def instants_sorceries(self, supported_only: bool = True) -> list[CardData]:
        return [
            c
            for c in self.cards
            if (c.is_instant or c.is_sorcery) and (not supported_only or c.supported) and c.layout == "normal"
        ]

    def lands(self) -> list[CardData]:
        return [c for c in self.cards if c.is_land and c.layout == "normal"]

    def supported_cards(self) -> list[CardData]:
        return [c for c in self.cards if c.supported and c.layout == "normal"]

    # ---------------------------------------------------------------- 统计
    def summary(self) -> str:
        stats = self.stats
        lines = [
            f"卡池总数：{stats.get('total', 0)}",
            f"完整解析：{stats.get('fully_supported', 0)}（{stats.get('coverage', 0)}%）",
            f"至少含一个异能：{stats.get('with_abilities', 0)}",
            f"解析耗时：{stats.get('elapsed_seconds', 0)} 秒",
        ]
        return "\n".join(lines)


def build_deck(db: CardDB, card_names: list[str]) -> list[CardData]:
    """按名字列表组装牌库（找不到名字会抛错，便于尽早发现拼写问题）。"""
    out: list[CardData] = []
    missing: list[str] = []
    for name in card_names:
        data = db.get(name)
        if data is None:
            missing.append(name)
            continue
        out.append(data)
    if missing:
        raise KeyError(f"卡池中找不到这些牌：{missing}")
    return out
