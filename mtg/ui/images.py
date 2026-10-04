"""卡图下载、缓存与缩放。

设计要点：
- 磁盘缓存 ``data/images/<card_id>[_normal].jpg``（small 约 25KB/张，只下一次）
- 内存缓存已打开的 PIL 图 + 已缩放的 ``ImageTk.PhotoImage``，按 (卡, 规格, 尺寸, 明暗) 复用
- 下载在线程池里做；**PIL 解码与 PhotoImage 创建只能在主线程**（Tk 硬性要求），
  所以工作线程只往队列里放结果，由主线程定时器轮询取走。
  早先在线程里调 ``root.after`` 会静默失败（异常被线程池吞掉），界面永远收不到通知。
"""
from __future__ import annotations

import os
import queue
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import tkinter as tk

try:  # Pillow 可选：没有它游戏照样能玩，只是显示文字卡面
    from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageTk

    PIL_AVAILABLE = True
except ImportError:  # pragma: no cover - 取决于运行环境
    Image = ImageDraw = ImageEnhance = ImageFont = ImageTk = None  # type: ignore[assignment]
    PIL_AVAILABLE = False

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
CACHE_DIR = os.path.join(DATA_DIR, "images")

USER_AGENT = "MTGO-Python/1.0 (local desktop project)"
MAX_WORKERS = 6
REQUEST_GAP = 0.05  # 秒，Scryfall 要求请求之间留 50~100ms

# 缩放质量：小图用 LANCZOS，缩小时足够锐利
RESAMPLE = Image.Resampling.LANCZOS

SMALL = "small"
NORMAL = "normal"

#: 已缩放 PhotoImage 的内存上限（尺寸档增多后按 LRU 淘汰，避免内存膨胀）
MAX_PHOTO_CACHE = 300

_FONTS: dict[int, ImageFont.FreeTypeFont] = {}


def _font(size: int):
    """优先用微软雅黑，找不到就退回 PIL 内置位图字体。"""
    if size not in _FONTS:
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont = ImageFont.load_default()
        for name in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "arial.ttf"):
            try:
                font = ImageFont.truetype(name, size)
                break
            except OSError:
                continue
        _FONTS[size] = font
    return _FONTS[size]


def _wrap(draw, text: str, font, max_width: float) -> list[str]:
    """按可用宽度折行（中英混排都按字符贪心，够用）。"""
    lines: list[str] = []
    current = ""
    for char in str(text):
        trial = current + char
        if draw.textlength(trial, font=font) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = char
    if current:
        lines.append(current)
    return lines


def _safe_id(card_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(card_id))


class ImageCache:
    """卡图缓存：磁盘 + 内存双缓存，线程池下载，主线程解码。"""

    def __init__(self, cache_dir: str = CACHE_DIR) -> None:
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

        self._pil: dict[str, Any] = {}                 # token -> 已打开的原始图
        self._photos: dict[tuple[str, int, int, bool], Any] = {}
        self._order: "OrderedDict[Any, None]" = OrderedDict()  # _photos 的 LRU 顺序
        self._pending: set[str] = set()                        # token
        self._callbacks: dict[str, list[tuple[int, int, bool, Callable]]] = {}
        self._failed: set[str] = set()
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="cardimg")
        self._queue: queue.Queue = queue.Queue()
        self._root: tk.Misc | None = None
        self._last_request = 0.0
        self._rate_lock = threading.Lock()
        self._downloaded = 0
        self._pump_running = False

    # ---------------------------------------------------------------- 路径
    @staticmethod
    def _token(card_id: str, kind: str) -> str:
        return f"{card_id}:{kind}"

    def _path_for(self, card_id: str, kind: str = SMALL) -> str:
        suffix = "" if kind == SMALL else f"_{kind}"
        return os.path.join(self.cache_dir, f"{_safe_id(card_id)}{suffix}.jpg")

    @staticmethod
    def _url_for(data: Any, kind: str) -> str:
        if kind == NORMAL:
            return getattr(data, "image_normal", None) or getattr(data, "image_small", None) or ""
        return getattr(data, "image_small", None) or getattr(data, "image_normal", None) or ""

    # ---------------------------------------------------------------- 主线程取
    def _load_pil(self, token: str, card_id: str, kind: str):
        if not PIL_AVAILABLE:
            return None
        if token in self._pil:
            return self._pil[token]
        path = self._path_for(card_id, kind)
        try:
            if not os.path.exists(path) or os.path.getsize(path) == 0:
                return None
            image = Image.open(path)
            image.load()
        except (OSError, ValueError):
            return None
        self._pil[token] = image
        return image

    def peek(self, data: Any, width: int, height: int, dim: bool = False, kind: str = SMALL):
        """只在已就绪时返回缩放好的图，绝不联网。"""
        card_id = data.card_id
        token = self._token(card_id, kind)
        with self._lock:
            if token in self._failed:
                return None
        key = (token, width, height, dim)
        if key in self._photos:
            self._order.move_to_end(key)
            return self._photos[key]
        image = self._load_pil(token, card_id, kind)
        if image is None:
            return None
        return self._make_photo(token, image, width, height, dim)

    def _store(self, key: Any, photo: Any) -> Any:
        """写入 PhotoImage 并按 LRU 淘汰最旧条目。"""
        self._photos[key] = photo
        self._order[key] = None
        self._order.move_to_end(key)
        while len(self._order) > MAX_PHOTO_CACHE:
            old_key, _ = self._order.popitem(last=False)
            self._photos.pop(old_key, None)
        return photo

    def placeholder(self, data: Any, width: int, height: int, dim: bool = False):
        """没有官方卡图时的程序占位卡（衍生物、双面卡背面等）。"""
        if not PIL_AVAILABLE:
            return None
        key = ("__placeholder__", data.card_id, width, height, dim)
        if key in self._photos:
            self._order.move_to_end(key)
            return self._photos[key]

        image = Image.new("RGB", (width, height), (233, 229, 218))
        draw = ImageDraw.Draw(image)
        draw.rectangle([0, 0, width - 1, height - 1], outline=(146, 139, 124), width=2)
        draw.rectangle([3, 3, width - 4, height - 4], outline=(205, 199, 185), width=1)

        name_font = _font(11)
        type_font = _font(9)

        lines = _wrap(draw, getattr(data, "name", "") or "", name_font, width - 14)[:3]
        y = 9
        for line in lines:
            text_width = draw.textlength(line, font=name_font)
            draw.text(((width - text_width) / 2, y), line, fill=(28, 28, 28), font=name_font)
            y += 14

        type_text = getattr(data, "type_line_cn", "") or getattr(data, "type_line", "") or ""
        type_lines = _wrap(draw, type_text, type_font, width - 14)[:2]
        y = height - 20 - 12 * (len(type_lines) - 1)
        for line in type_lines:
            text_width = draw.textlength(line, font=type_font)
            draw.text(((width - text_width) / 2, y), line, fill=(92, 87, 78), font=type_font)
            y += 12

        if dim:
            image = ImageEnhance.Brightness(image).enhance(0.6)

        photo = ImageTk.PhotoImage(image)
        return self._store(key, photo)

    def _make_photo(self, token: str, image, width: int, height: int, dim: bool = False):
        source = image
        if dim:
            source = ImageEnhance.Brightness(image).enhance(0.55)
            source = ImageEnhance.Color(source).enhance(0.55)
        photo = ImageTk.PhotoImage(source.resize((width, height), RESAMPLE))
        return self._store((token, width, height, dim), photo)

    # ---------------------------------------------------------------- 异步取
    def request(
        self,
        data: Any,
        width: int,
        height: int,
        on_ready: Callable[[Any], None],
        root: tk.Misc | None = None,
        dim: bool = False,
        kind: str = SMALL,
    ) -> None:
        """异步取图：已就绪立刻回调，否则下载完成后回调。"""
        if not PIL_AVAILABLE:
            on_ready(None)
            return
        if root is not None:
            self._root = root
        card_id = data.card_id
        token = self._token(card_id, kind)

        ready = self.peek(data, width, height, dim, kind)
        if ready is not None:
            on_ready(ready)
            return

        with self._lock:
            if token in self._failed:
                on_ready(None)
                return
            first = token not in self._pending
            if first:
                self._pending.add(token)
            self._callbacks.setdefault(token, []).append((width, height, dim, on_ready))

        if first:
            url = self._url_for(data, kind)
            if not url:
                self._mark_failed(token)
                self._ensure_pump()
                return
            self._executor.submit(self._download, token, url, self._path_for(card_id, kind))
        self._ensure_pump()

    def prefetch(self, datas: list[Any], root: tk.Misc | None = None, kind: str = SMALL) -> int:
        """批量后台下载（不回调显示）。按卡种去重，返回本次真正排队的张数。"""
        if not PIL_AVAILABLE:
            return 0
        if root is not None:
            self._root = root
        queued = 0
        seen: set[str] = set()
        for data in datas:
            if data is None:
                continue
            card_id = data.card_id
            if card_id in seen:
                continue
            seen.add(card_id)
            token = self._token(card_id, kind)
            with self._lock:
                if token in self._failed or token in self._pending:
                    continue
                path = self._path_for(card_id, kind)
                if os.path.exists(path) and os.path.getsize(path) > 0:
                    continue
                self._pending.add(token)
            url = self._url_for(data, kind)
            if not url:
                self._mark_failed(token)
                continue
            self._executor.submit(self._download, token, url, path)
            queued += 1
        if queued:
            self._ensure_pump()
        return queued

    # ---------------------------------------------------------------- 后台线程
    def _download(self, token: str, url: str, path: str) -> None:
        ok = False
        try:
            with self._rate_lock:
                gap = time.time() - self._last_request
                if gap < REQUEST_GAP:
                    time.sleep(REQUEST_GAP - gap)
                self._last_request = time.time()
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = resp.read()
            if payload:
                tmp = path + ".part"
                with open(tmp, "wb") as fh:
                    fh.write(payload)
                os.replace(tmp, path)
                ok = True
                self._downloaded += 1
        except (urllib.error.URLError, OSError, TimeoutError, ValueError):
            ok = False
        self._queue.put((token, ok))

    def _mark_failed(self, token: str) -> None:
        with self._lock:
            self._failed.add(token)

    # ---------------------------------------------------------------- 主线程泵
    def _ensure_pump(self) -> None:
        """用主线程定时器轮询下载结果（工作线程绝不能碰 Tk）。"""
        if self._pump_running or self._root is None:
            return
        self._pump_running = True
        try:
            self._root.after(100, self._tick)
        except tk.TclError:
            self._pump_running = False

    def _tick(self) -> None:
        self._drain()
        if self._root is None:
            self._pump_running = False
            return
        with self._lock:
            busy = bool(self._pending) or not self._queue.empty()
        if not busy:
            self._pump_running = False
            return
        try:
            self._root.after(100, self._tick)
        except tk.TclError:
            self._pump_running = False

    def _drain(self) -> None:
        """主线程：解码 + 缩放 + 回调。每轮限量，避免卡界面。"""
        processed = 0
        while processed < 10:
            try:
                token, ok = self._queue.get_nowait()
            except queue.Empty:
                return
            processed += 1
            with self._lock:
                self._pending.discard(token)
                callbacks = self._callbacks.pop(token, [])
                if not ok:
                    self._failed.add(token)
            if not ok:
                for _w, _h, _d, callback in callbacks:
                    try:
                        callback(None)
                    except tk.TclError:
                        pass
                continue
            card_id, _, kind = token.rpartition(":")
            image = self._load_pil(token, card_id, kind)
            for width, height, dim, callback in callbacks:
                if image is None:
                    try:
                        callback(None)
                    except tk.TclError:
                        pass
                    continue
                try:
                    callback(self._make_photo(token, image, width, height, dim))
                except tk.TclError:
                    pass

    # ---------------------------------------------------------------- 统计
    @property
    def downloaded(self) -> int:
        return self._downloaded

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def cached_count(self) -> int:
        try:
            return len([n for n in os.listdir(self.cache_dir) if n.endswith(".jpg")])
        except OSError:
            return 0


_CACHE: ImageCache | None = None


def get_cache() -> ImageCache:
    global _CACHE
    if _CACHE is None:
        _CACHE = ImageCache()
    return _CACHE


# -------------------------------------------------------------------- 大图预览
BIG_WIDTH = 420
BIG_HEIGHT = 585  # 标准卡比例 488:680


def show_card_image(master: tk.Misc, data: Any) -> tk.Toplevel:
    """弹出窗口显示该卡的大图：先顶上已缓存的小图，再补 normal 高清图。"""
    if not PIL_AVAILABLE:
        window = tk.Toplevel(master)
        window.title(data.name)
        window.configure(bg="#20242a")
        window.geometry("420x260")
        tk.Label(window, text=f"{data.name}\n\n未安装 Pillow，无法显示卡图。\n运行 pip install pillow 后重试。",
                 bg="#20242a", fg="#dddddd", font=("Microsoft YaHei", 10), justify="center").pack(expand=True)
        return window
    cache = get_cache()

    window = tk.Toplevel(master)
    window.title(data.name)
    window.configure(bg="#20242a")
    window.transient(master)

    label = tk.Label(window, text="正在加载卡图…", bg="#20242a", fg="#cccccc", font=("Microsoft YaHei", 10))
    label.pack(padx=10, pady=20)

    info = tk.Label(
        window,
        text=f"{data.name}   {getattr(data, 'mana_string', '')}",
        bg="#20242a", fg="#ffffff", font=("Microsoft YaHei", 10, "bold"), wraplength=460,
    )
    info.pack(pady=(0, 8))

    def paint(photo) -> None:
        try:
            if not window.winfo_exists():
                return
        except tk.TclError:
            return
        window.image = photo  # 保持引用，避免被回收
        label.configure(image=photo, text="")
        window.geometry(f"{photo.width() + 24}x{photo.height() + 96}")

    # 已缓存的小图先放大顶上，避免窗口空白
    small = cache.peek(data, BIG_WIDTH, BIG_HEIGHT)
    if small is not None:
        paint(small)

    def on_big(photo) -> None:
        if photo is not None:
            paint(photo)
            return
        # normal 没拿到，退回收到的 small（哪怕糊）
        fallback = cache.peek(data, BIG_WIDTH, BIG_HEIGHT)
        if fallback is not None:
            paint(fallback)
            return
        try:
            if window.winfo_exists():
                label.configure(text=f"无法加载卡图\n{data.name}", fg="#999999")
        except tk.TclError:
            pass

    cache.request(data, BIG_WIDTH, BIG_HEIGHT, on_big, root=master, kind=NORMAL)
    return window
