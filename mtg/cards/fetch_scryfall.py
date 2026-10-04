"""从 Scryfall 抓取标准赛制全部卡牌，精简后落盘为本地卡池数据库。

用法:
    python -m mtg.cards.fetch_scryfall            # 抓取（默认）
    python -m mtg.cards.fetch_scryfall --refresh  # 强制重新抓取

仅使用标准库（urllib / json / time），无需 pip 安装任何依赖。
Scryfall API 要求礼貌访问：请求间隔 >= 100ms，并携带 User-Agent。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
OUT_PATH = os.path.join(DATA_DIR, "standard_cards.json")
META_PATH = os.path.join(DATA_DIR, "cardpool_meta.json")

API_ROOT = "https://api.scryfall.com"
USER_AGENT = "MTGO-Python/1.0 (local desktop project; contact: local user)"
REQUEST_DELAY = 0.12  # Scryfall 礼貌间隔

# 不可作为正常游戏牌张使用的布局
SKIP_LAYOUTS = {
    "token",
    "double_faced_token",
    "emblem",
    "art_series",
    "planar",
    "scheme",
    "vanguard",
}

# 我们保留的字段（其余丢弃以控制体积）
KEEP_KEYS = (
    "id",
    "name",
    "mana_cost",
    "cmc",
    "type_line",
    "oracle_text",
    "power",
    "toughness",
    "loyalty",
    "defense",
    "colors",
    "color_identity",
    "keywords",
    "produced_mana",
    "set",
    "set_name",
    "rarity",
    "layout",
    "card_faces",
    "image_uris",
    "legalities",
    "promo_types",
    "digital",
    "flavor_text",
    "artist",
)


def _get(url: str, retries: int = 4) -> dict[str, Any] | None:
    """带退避重试的 GET。"""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=45) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:  # noqa: PERF203
            if exc.code in (429, 503):
                wait = 2.0 * (attempt + 1)
                print(f"  [限流 {exc.code}] 等待 {wait:.0f}s 后重试…", flush=True)
                time.sleep(wait)
                continue
            print(f"  [HTTP {exc.code}] {url}", flush=True)
            return None
        except Exception as exc:  # noqa: BLE001
            print(f"  [网络错误] {exc} -> 重试 {attempt + 1}/{retries}", flush=True)
            time.sleep(1.5 * (attempt + 1))
    return None


def _slim(card: dict[str, Any]) -> dict[str, Any] | None:
    """精简单张卡牌数据。"""
    layout = card.get("layout", "normal")
    if layout in SKIP_LAYOUTS:
        return None

    out: dict[str, Any] = {}
    for key in KEEP_KEYS:
        val = card.get(key)
        if val is None:
            continue
        # 双面卡的正反两面各自裁剪，避免塞进整坨无用字段
        if key == "card_faces":
            faces = []
            for face in val:
                if not isinstance(face, dict):
                    continue
                faces.append(
                    {
                        k: face.get(k)
                        for k in (
                            "name",
                            "mana_cost",
                            "cmc",
                            "type_line",
                            "oracle_text",
                            "power",
                            "toughness",
                            "loyalty",
                            "defense",
                            "colors",
                            "produced_mana",
                            "image_uris",
                        )
                        if face.get(k) is not None
                    }
                )
            out["card_faces"] = faces
            continue
        if key == "image_uris":
            out["image_uris"] = {
                "small": val.get("small"),
                "normal": val.get("normal"),
                "art_crop": val.get("art_crop"),
            }
            continue
        if key == "legalities":
            out["standard_legal"] = val.get("standard")
            continue
        out[key] = val

    # 双面卡的顶层信息常常为空，用正面补齐
    if out.get("card_faces"):
        front = out["card_faces"][0]
        for key in ("mana_cost", "cmc", "type_line", "oracle_text", "power", "toughness", "loyalty", "colors"):
            if not out.get(key) and front.get(key) is not None:
                out[key] = front[key]
        if not out.get("image_uris"):
            out["image_uris"] = front.get("image_uris")

    # 统一关键字段形态，方便引擎消费
    out.setdefault("oracle_text", "")
    out.setdefault("keywords", [])
    out.setdefault("colors", [])
    out.setdefault("color_identity", [])
    out.setdefault("type_line", "")
    out.setdefault("cmc", 0.0)
    return out


def fetch_standard(force: bool = False) -> list[dict[str, Any]]:
    """抓取全部标准赛制合法卡牌。"""
    if not force and os.path.exists(OUT_PATH):
        with open(OUT_PATH, "r", encoding="utf-8") as fh:
            cached = json.load(fh)
        if cached:
            print(f"使用本地缓存卡池: {len(cached)} 张 ({OUT_PATH})")
            print("如需更新，请加 --refresh")
            return cached

    print("正在从 Scryfall 抓取标准赛制卡池…", flush=True)
    query = "legal:standard"
    url = f"{API_ROOT}/cards/search?{urllib.parse.urlencode({'q': query, 'unique': 'cards', 'order': 'name', 'page': 1})}"
    cards: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    page = 0

    while url:
        page += 1
        payload = _get(url)
        if payload is None:
            print(f"第 {page} 页获取失败，停止。", flush=True)
            break
        if payload.get("object") == "error":
            print(f"API 返回错误: {payload.get('details')}", flush=True)
            break

        batch = payload.get("data", [])
        for raw in batch:
            slim = _slim(raw)
            if slim is None or slim["id"] in seen_ids:
                continue
            seen_ids.add(slim["id"])
            cards.append(slim)

        has_more = bool(payload.get("has_more"))
        print(f"  第 {page:>3} 页: 累计 {len(cards)} 张", flush=True)
        if not has_more:
            break
        url = payload.get("next_page")
        time.sleep(REQUEST_DELAY)

    if not cards:
        print("抓取失败：没有拿到任何卡牌。", file=sys.stderr)
        return []

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(cards, fh, ensure_ascii=False, separators=(",", ":"))

    meta = {
        "source": "Scryfall /cards/search?q=legal:standard",
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(cards),
        "layouts": {},
    }
    for card in cards:
        meta["layouts"][card.get("layout", "normal")] = meta["layouts"].get(card.get("layout", "normal"), 0) + 1
    with open(META_PATH, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)

    size_mb = os.path.getsize(OUT_PATH) / 1024 / 1024
    print(f"\n完成: {len(cards)} 张标准赛制卡牌 -> {OUT_PATH} ({size_mb:.1f} MB)")
    return cards


if __name__ == "__main__":
    force = "--refresh" in sys.argv
    result = fetch_standard(force=force)
    sys.exit(0 if result else 1)
