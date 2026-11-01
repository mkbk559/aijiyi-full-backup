"""识别层：把任意输入降维成"标准文本 + 元数据 + 置信度"，之后走同一条管线。

    Item(kind='image', ref='msg-7781', url='https://…')
        └─ Recognizers.recognize() ─> Envelope(text='发票金额 1280 元', confidence=0.72, status='ok')

设计约束（都是硬要求）：
- **不按类型分叉**：调用方拿到 Envelope 之后，文本/OCR/ASR/表情的处理代码是同一段。
- **全部异步**：本模块只在 worker 线程被调用，主链路不碰它。
- **超时 + 置信度门槛**：每个识别器有超时；低于门槛的结论标 `low_confidence`，**不喂给抽取层**。
- **原始文件不入库**：这里只收 `ref`/`url`/`size_bytes`，从不收字节；OCR/ASR 由外部引擎做。
- 初期只做 text / image / voice / emoji 四类正经识别：
  video 只留元数据（`metadata_only`），复杂视觉理解明确不做。

识别器是**可插拔**的：OCR / ASR 引擎和取链器都从构造函数注入，
缺省没有引擎时如实返回 `unsupported` + 原因，绝不编造文本。
"""

from __future__ import annotations

import html
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any, Callable

from .net_guard import UrlGuardError, validate_public_http_url

KINDS = ("text", "image", "voice", "emoji", "file", "link", "video")

# 表情 → 语义标签。只覆盖高频，够用来判断情绪；不认识的原样留着。
EMOJI: dict[str, str] = {
    "😀": "开心", "😃": "开心", "😄": "开心", "😁": "开心", "😊": "开心", "🙂": "开心",
    "😂": "好笑", "🤣": "好笑", "😅": "尴尬", "😉": "俏皮",
    "😍": "喜欢", "🥰": "喜欢", "😘": "亲昵", "❤️": "喜欢", "💕": "喜欢", "❤": "喜欢",
    "😭": "难过", "😢": "难过", "😞": "失望", "💔": "难过",
    "😡": "生气", "😠": "生气", "😤": "不满", "🤬": "愤怒",
    "😱": "惊讶", "😲": "惊讶", "🤯": "震惊", "😴": "困", "🥱": "困",
    "👍": "赞同", "👎": "反对", "🙏": "感谢", "👏": "赞赏", "🤝": "合作",
    "🔥": "很赞", "🎉": "庆祝", "💪": "加油", "🌹": "浪漫", "☕": "休息",
    "🤔": "犹豫", "🙄": "无语", "😏": "得意", "😎": "自信", "🤗": "拥抱",
    "😐": "平淡", "🙃": "反讽", "💤": "困", "✨": "期待", "😷": "不适",
}

_EMOJI_RE = re.compile("|".join(sorted(map(re.escape, EMOJI), key=len, reverse=True)))
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_TEXTUAL = ("text/", "application/json", "application/xml", "application/x-yaml", "text/csv")
MAX_TEXT = 4000
_RECOGNIZER_EXECUTOR = ThreadPoolExecutor(
    max_workers=4,
    thread_name_prefix="aiji-recog",
)


@dataclass(slots=True)
class Item:
    """一条待识别的输入。只有引用，没有字节。"""

    kind: str
    ref: str = ""
    text: str | None = None
    url: str | None = None
    mime: str | None = None
    size_bytes: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Envelope:
    """识别层产物：标准文本表示 + 元数据。"""

    kind: str
    ref: str
    text: str
    confidence: float
    status: str  # ok | low_confidence | unsupported | timeout | error | metadata_only
    recognizer: str
    elapsed_ms: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        """能不能进抽取管线：要有文本、状态正常。门槛由调用方再判一次。"""
        return self.status in ("ok", "metadata_only") and bool(self.text.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "ref": self.ref, "text": self.text,
            "confidence": round(self.confidence, 3), "status": self.status,
            "recognizer": self.recognizer, "elapsed_ms": round(self.elapsed_ms, 3),
            "meta": self.meta,
        }


def _clean(text: str) -> tuple[str, bool]:
    t = (text or "").replace("\x00", " ").strip()
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    if len(t) > MAX_TEXT:
        return t[:MAX_TEXT], True
    return t, False


def _timed(fn: Callable[[], Any], timeout_s: float) -> Any:
    """识别器一律带超时。超时抛 FutureTimeout，由调用方转成 status='timeout'。"""
    future = _RECOGNIZER_EXECUTOR.submit(fn)
    try:
        return future.result(timeout=max(0.05, timeout_s))
    except FutureTimeout:
        future.cancel()
        raise


def _default_fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "aiji/0.2 (+recognizer)"})
    with urllib.request.urlopen(req, timeout=2.0) as r:
        raw = r.read(200_000)
    charset = "utf-8"
    ctype = r.headers.get("Content-Type", "")
    m = re.search(r"charset=([\w-]+)", ctype)
    if m:
        charset = m.group(1)
    try:
        return raw.decode(charset, "replace")
    except LookupError:
        return raw.decode("utf-8", "replace")


class Recognizers:
    """按 kind 分派到具体识别器；统一计时、截断、门槛判定。"""

    def __init__(
        self,
        *,
        ocr: Callable[[Item], Any] | None = None,
        asr: Callable[[Item], Any] | None = None,
        fetcher: Callable[[str], str] | None = None,
        timeout_s: float = 2.0,
        min_confidence: float = 0.5,
    ):
        self.ocr = ocr
        self.asr = asr
        self.fetcher = fetcher or _default_fetch
        self.timeout_s = timeout_s
        self.min_confidence = min_confidence
        self.calls = 0
        self.by_status: dict[str, int] = {}

    # ---- 对外唯一入口 ----

    def recognize(self, item: Item) -> Envelope:
        t0 = time.perf_counter()
        self.calls += 1
        fn = {
            "text": self._text,
            "emoji": self._emoji,
            "image": self._image,
            "voice": self._voice,
            "file": self._file,
            "link": self._link,
            "video": self._video,
        }.get(item.kind)
        if fn is None:
            env = Envelope(item.kind, item.ref, "", 0.0, "unsupported", "none",
                           meta={"reason": f"unknown_kind:{item.kind}"})
        else:
            try:
                env = fn(item)
            except FutureTimeout:
                env = Envelope(item.kind, item.ref, "", 0.0, "timeout", item.kind,
                               meta={"reason": "recognizer_timeout", "timeout_s": self.timeout_s})
            except Exception as exc:  # noqa: BLE001 - 识别失败不能炸 worker
                env = Envelope(item.kind, item.ref, "", 0.0, "error", item.kind,
                               meta={"reason": f"{type(exc).__name__}: {exc}"[:200]})
        text, truncated = _clean(env.text)
        env.text = text
        if truncated:
            env.meta["truncated"] = True
        if env.status == "ok" and env.confidence < self.min_confidence:
            env.status = "low_confidence"
        env.elapsed_ms = (time.perf_counter() - t0) * 1000
        self.by_status[env.status] = self.by_status.get(env.status, 0) + 1
        return env

    # ---- 具体识别器 ----

    def _text(self, item: Item) -> Envelope:
        if not (item.text or "").strip():
            return Envelope("text", item.ref, "", 0.0, "unsupported", "text",
                            meta={"reason": "empty_text"})
        return Envelope("text", item.ref, item.text or "", 1.0, "ok", "text")

    def _emoji(self, item: Item) -> Envelope:
        raw = item.text or ""
        if not raw.strip():
            return Envelope("emoji", item.ref, "", 0.0, "unsupported", "emoji-map",
                            meta={"reason": "empty_text"})
        hits = _EMOJI_RE.findall(raw)
        tags = [EMOJI[e] for e in hits]
        if not tags:
            return Envelope("emoji", item.ref, raw, 0.9, "ok", "emoji-map",
                            meta={"emojis": [], "labels": []})
        plain = _EMOJI_RE.sub("", raw).strip()
        label = "、".join(dict.fromkeys(tags))
        text = f"{plain}（表情：{label}）" if plain else f"用户发了表情：{label}"
        return Envelope("emoji", item.ref, text, 0.9, "ok", "emoji-map",
                        meta={"emojis": hits[:8], "labels": tags[:8]})

    def _image(self, item: Item) -> Envelope:
        if self.ocr is None:
            return Envelope("image", item.ref, "", 0.0, "unsupported", "ocr",
                            meta={"reason": "no_ocr_engine", "url": item.url})
        out = _timed(lambda: self.ocr(item), self.timeout_s)
        text, conf = out if isinstance(out, tuple) else (out, 0.7)
        return Envelope("image", item.ref, str(text or ""), float(conf), "ok", "ocr",
                        meta={"url": item.url, "mime": item.mime})

    def _voice(self, item: Item) -> Envelope:
        if self.asr is None:
            return Envelope("voice", item.ref, "", 0.0, "unsupported", "asr",
                            meta={"reason": "no_asr_engine", "url": item.url})
        out = _timed(lambda: self.asr(item), self.timeout_s)
        text, conf = out if isinstance(out, tuple) else (out, 0.7)
        return Envelope("voice", item.ref, str(text or ""), float(conf), "ok", "asr",
                        meta={"url": item.url, "mime": item.mime,
                              "duration_s": item.extra.get("duration_s")})

    def _file(self, item: Item) -> Envelope:
        if (item.text or "").strip():  # 调用方已经提好了文本
            return Envelope("file", item.ref, item.text or "", 0.9, "ok", "caller-text",
                            meta={"mime": item.mime, "size_bytes": item.size_bytes})
        mime = (item.mime or "").lower()
        if any(mime.startswith(p) for p in _TEXTUAL):
            return Envelope("file", item.ref, "", 0.0, "unsupported", "file",
                            meta={"reason": "need_text_from_caller", "mime": mime})
        return Envelope("file", item.ref, "", 0.0, "unsupported", "file",
                        meta={"reason": "no_extractor_for_mime", "mime": mime}) 

    def _link(self, item: Item) -> Envelope:
        url = item.url or item.text or ""
        if not url.startswith(("http://", "https://")):
            return Envelope("link", item.ref, "", 0.0, "unsupported", "link",
                            meta={"reason": "not_a_url"})
        try:
            validate_public_http_url(url)
        except UrlGuardError as exc:
            return Envelope("link", item.ref, "", 0.0, "error", "link",
                            meta={"reason": exc.code, "url": url})
        page = _timed(lambda: self.fetcher(url), self.timeout_s)
        m = _TITLE_RE.search(page or "")
        title = html.unescape(re.sub(r"\s+", " ", m.group(1)).strip()) if m else ""
        if not title:
            return Envelope("link", item.ref, "", 0.0, "error", "link",
                            meta={"reason": "no_title", "url": url})
        return Envelope("link", item.ref, title, 0.6, "ok", "link",
                        meta={"url": url, "title": title})

    def _video(self, item: Item) -> Envelope:
        # 只存元数据，不做理解。text 留空 => 不进抽取管线。
        return Envelope("video", item.ref, "", 0.0, "metadata_only", "video-meta",
                        meta={"url": item.url, "mime": item.mime,
                              "size_bytes": item.size_bytes,
                              "duration_s": item.extra.get("duration_s"),
                              "note": "视频只存元数据，不做视觉理解"})

    def stats(self) -> dict[str, Any]:
        return {"calls": self.calls, "by_status": dict(self.by_status),
                "min_confidence": self.min_confidence, "timeout_s": self.timeout_s,
                "ocr": self.ocr is not None, "asr": self.asr is not None}


__all__ = ["EMOJI", "Envelope", "Item", "KINDS", "Recognizers"]
