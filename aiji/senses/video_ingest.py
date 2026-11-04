"""aiji.senses.video_ingest —— 视频学习入口（B 站/抖音/任意 yt-dlp 支持的站点）。

为什么是"元数据 + 字幕"而不是"看画面"：
- 视频的**知识含量几乎都在标题/简介/字幕/弹幕**里，画面本身没有现成的本地理解引擎；
- yt-dlp 能拿到的就是这四样（探针 2026-11-04：B 站 BV1GJ411x7h7 只有 danmaku，
  知识类视频通常有 zh-Hans CC）；没有字幕的视频给出"转音频 ASR"降级计划。
- 弹幕**不进记忆**：信噪比太低（同一视频 7000+ 条"你被骗了"），只留 stats 里的条数。

网络边界（探针 …\\083635-video-net-probe-34e6a7）：
- 本机 HTTP_PROXY=127.0.0.1:7897 常年是**死代理**；yt-dlp 还会读 Windows 系统代理。
  一律 `--proxy ""` 强制直连——B 站直连 200，走代理直接 ConnectionRefused。
- 抖音需要新鲜 cookies（哪怕不登录）：yt-dlp 报 Fresh cookies are needed。
  把 `--cookies-from-browser edge/chrome` 暴露成参数，登录态由使用者提供。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_TIMEOUT_FLOOR = 5.0
_MAX_DESC_CHARS = 600
_MAX_TEXT_CHARS = 4000

# 这些站的直链常见且值得点名，别的站走 yt-dlp 的通用 extractor
KNOWN_HOSTS = ("bilibili.com", "b23.tv", "douyin.com", "v.douyin.com")


class VideoIngestError(RuntimeError):
    """拉取失败。message 里带"下一步该怎么办"，调用方直接展示给用户。"""

    def __init__(self, message: str, *, code: str = "video_fetch_failed") -> None:
        super().__init__(message)
        self.code = code


@dataclass(slots=True)
class VideoInfo:
    """yt-dlp 元数据的归一化视图。字段都是"学习文本"要用的那几个。"""

    webpage_url: str = ""
    video_id: str = ""
    title: str = ""
    uploader: str = ""
    duration_s: float = 0.0
    description: str = ""
    view_count: int = 0
    tags: list[str] = field(default_factory=list)
    subtitle_langs: list[str] = field(default_factory=list)
    extractor: str = ""
    has_subs: bool = False
    is_live: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "webpage_url": self.webpage_url, "video_id": self.video_id,
            "title": self.title, "uploader": self.uploader,
            "duration_s": round(self.duration_s, 1), "description": self.description,
            "view_count": self.view_count, "tags": self.tags[:12],
            "subtitle_langs": self.subtitle_langs, "extractor": self.extractor,
            "has_subs": self.has_subs, "is_live": self.is_live,
        }


def _run_ytdlp(args: list[str], timeout_s: float) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-X", "utf8", "-m", "yt_dlp", "--proxy", "", *args]
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        raise VideoIngestError(
            f"yt-dlp 超时（{timeout_s:.0f}s）：网络太慢或站点风控，稍后再试",
            code="video_timeout",
        ) from exc
    except FileNotFoundError as exc:
        raise VideoIngestError(
            "yt-dlp 不可用：需要 `pip install yt-dlp`", code="yt_dlp_missing",
        ) from exc


def _lang_keys(info: dict[str, Any]) -> list[str]:
    """合并手动/自动字幕的语言键（yt-dlp 里 key 可能是 'zh-Hans' 或 'danmaku'）。"""
    langs: list[str] = []
    for section in ("subtitles", "automatic_captions"):
        for key in (info.get(section) or {}):
            if key not in langs:
                langs.append(str(key))
    return langs


def fetch_video_info(
    url: str,
    *,
    timeout_s: float = 60.0,
    cookies_from_browser: str = "",
) -> VideoInfo:
    """不下载任何媒体，只要元数据。失败要带"能看懂的下一步"。"""
    url = (url or "").strip()
    if not re.match(r"^https?://", url):
        raise VideoIngestError("需要一个 http(s) 视频链接", code="invalid_url")
    args = ["--skip-download", "--dump-json", "--no-playlist", "--no-warnings"]
    if cookies_from_browser:
        args += ["--cookies-from-browser", cookies_from_browser]
    proc = _run_ytdlp([*args, url], max(_TIMEOUT_FLOOR, timeout_s))
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        hint = ""
        err = (proc.stderr or proc.stdout or "")
        if "Fresh cookies" in err or "cookies" in err.lower():
            hint = "（这个站点要登录态：加 --cookies-from-browser edge 或 chrome）"
        elif "Sign in" in err or "login" in err.lower():
            hint = "（内容需要登录/会员才能看）"
        raise VideoIngestError(f"抓不到视频信息{hint}：{err.strip().splitlines()[-1][:200]}")
    try:
        raw = json.loads(proc.stdout.strip().splitlines()[0])
    except (ValueError, IndexError) as exc:
        raise VideoIngestError(f"yt-dlp 输出解析失败：{exc}") from exc
    if raw.get("_type") == "playlist":
        raise VideoIngestError(
            "这是个合集/播放列表，请给单集链接", code="playlist_needs_single",
        )
    info = VideoInfo(
        webpage_url=str(raw.get("webpage_url") or url),
        video_id=str(raw.get("id") or ""),
        title=str(raw.get("title") or "").strip(),
        uploader=str(raw.get("uploader") or "").strip(),
        duration_s=float(raw.get("duration") or 0.0),
        description=str(raw.get("description") or "").strip(),
        view_count=int(raw.get("view_count") or 0),
        tags=[str(t) for t in (raw.get("tags") or []) if str(t).strip()][:12],
        subtitle_langs=_lang_keys(raw),
        extractor=str(raw.get("extractor_key") or raw.get("extractor") or ""),
        is_live=bool(raw.get("is_live")),
    )
    info.has_subs = any(
        lang not in ("danmaku",) and "zh" in lang.lower() or lang.lower() == "en"
        for lang in info.subtitle_langs
    )
    if not info.title:
        raise VideoIngestError("拿到了元数据但标题为空，可能是风控页", code="empty_title")
    return info


def build_learn_text(info: VideoInfo) -> str:
    """VideoInfo → 记忆管线吃的文本。截断在 4000 字符内（MAX_TEXT 同一量级）。"""
    minutes = int(info.duration_s // 60)
    seconds = int(info.duration_s % 60)
    parts = [
        f"【视频】{info.title}",
        f"UP主/作者：{info.uploader}" if info.uploader else "",
        f"时长：{minutes}分{seconds:02d}秒" if info.duration_s else "",
        f"播放量：{info.view_count}" if info.view_count else "",
        f"标签：{'、'.join(info.tags[:8])}" if info.tags else "",
    ]
    desc = info.description.replace("\r", "").strip()
    if len(desc) > _MAX_DESC_CHARS:
        desc = desc[:_MAX_DESC_CHARS] + "…"
    if desc:
        parts.append(f"简介：{desc}")
    parts.append(f"链接：{info.webpage_url}")
    return "\n".join(p for p in parts if p)[:_MAX_TEXT_CHARS]


def learning_plan(info: VideoInfo) -> dict[str, Any]:
    """告诉用户"这个视频我能学到什么、学不到什么"。不做任何下载。"""
    if info.is_live:
        return {"mode": "metadata_only", "reason": "直播没有可用的成片字幕",
                "text": build_learn_text(info)}
    if info.has_subs:
        return {"mode": "subtitles", "reason": "有字幕，字幕和元数据都会进记忆",
                "text": build_learn_text(info)}
    if info.duration_s and info.duration_s <= 1800:
        return {"mode": "audio_asr", "reason": "没有字幕但视频不长，可以转音频用 ASR 听",
                "text": build_learn_text(info)}
    return {"mode": "metadata_only", "reason": "没有字幕且视频较长，只学标题/简介/标签",
            "text": build_learn_text(info)}


def listen_video(
    url: str,
    *,
    seconds: float = 300.0,
    timeout_s: float = 300.0,
    cookies_from_browser: str = "",
) -> tuple[str, float]:
    """听视频：拉音轨 → 16k wav → FunASR 转写，返回 (文本, 置信度)。

    全程在临时目录里完成，函数返回时临时文件已清理——wav 是中间产物，
    不跨函数存活（2026-11-04 教训：finally 清理把调用方还要读的文件删了）。

    为什么不用 --download-sections：B 站对"边下边切"的 HTTP range 请求会 429
    （探针 100127 实测）；整段音频通常只有几 MB，整段下载更稳。
    seconds 只限制**转写长度**（ffmpeg -t），下载仍是整段。
    """
    import shutil
    import tempfile

    workdir = Path(tempfile.mkdtemp(prefix="aiji-video-"))
    try:
        args = ["-f", "bestaudio", "--no-playlist", "--no-warnings"]
        if cookies_from_browser:
            args += ["--cookies-from-browser", cookies_from_browser]
        proc = _run_ytdlp([*args, "-o", str(workdir / "audio.%(ext)s"), url],
                          timeout_s)
        if proc.returncode != 0:
            raise VideoIngestError(
                f"音轨下载失败：{(proc.stderr or '').strip().splitlines()[-1][:160]}",
                code="audio_download_failed",
            )
        audio = next((p for p in workdir.iterdir()
                      if p.suffix in (".m4a", ".opus", ".webm", ".aac",
                                      ".m4s", ".mp3")),
                     None)
        if audio is None:
            raise VideoIngestError("下载完成但没找到音频文件", code="audio_missing")
        wav = workdir / "clip.wav"
        try:
            conv = subprocess.run(
                ["ffmpeg", "-y", "-i", str(audio), "-t", str(max(1.0, seconds)),
                 "-ar", "16000", "-ac", "1", str(wav)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=max(60.0, seconds),
            )
        except FileNotFoundError as exc:
            raise VideoIngestError(
                "ffmpeg 不可用：需要安装 ffmpeg 并加入 PATH", code="ffmpeg_missing",
            ) from exc
        if conv.returncode != 0 or not wav.exists():
            tail = (conv.stderr or "").strip().splitlines()[-1][:160] if conv.stderr else "no output"
            raise VideoIngestError(f"音频转码失败：{tail}", code="ffmpeg_failed")
        from aiji.hippocampus.real_engines import transcribe_wav_bytes

        text, conf = transcribe_wav_bytes(wav.read_bytes())
        return (text or "").strip()[:_MAX_TEXT_CHARS], conf
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def look_video(
    url: str,
    *,
    frames: int = 6,
    timeout_s: float = 300.0,
    cookies_from_browser: str = "",
) -> tuple[str, float]:
    """看视频：拉低清画面 → 均匀抽帧 → RapidOCR 读画面文字。

    返回 (去重后的文本行拼接, 平均置信度)。抽帧数固定上限 12，
    每帧缩到 640 宽——OCR 只关心标题板/PPT 大字，分辨率高没收益。
    全程临时目录，返回时清理（同 listen_video 的教训）。
    """
    import shutil
    import tempfile

    n = max(1, min(int(frames), 12))
    workdir = Path(tempfile.mkdtemp(prefix="aiji-video-"))
    try:
        args = ["-f", "30016+30216/30032+30216/worst", "--no-playlist",
                "--no-warnings"]
        if cookies_from_browser:
            args += ["--cookies-from-browser", cookies_from_browser]
        proc = _run_ytdlp([*args, "-o", str(workdir / "video.%(ext)s"), url],
                          timeout_s)
        if proc.returncode != 0:
            raise VideoIngestError(
                f"画面下载失败：{(proc.stderr or '').strip().splitlines()[-1][:160]}",
                code="video_download_failed",
            )
        video = next((p for p in workdir.iterdir()
                      if p.suffix in (".mp4", ".webm", ".flv", ".mkv")), None)
        if video is None:
            raise VideoIngestError("下载完成但没找到视频文件", code="video_missing")
        # 均匀抽 n 帧：先用 fps 高频抽再按序取，比 -ss 逐次 seek 快且稳
        import random

        grab = workdir / "grab_%03d.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(video), "-vf",
             "fps=1,scale=640:-2", str(grab)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180)
        grabbed = sorted(workdir.glob("grab_*.jpg"))
        if not grabbed:
            raise VideoIngestError("抽帧失败（视频可能没有画面流）",
                                   code="frames_missing")
        pick = grabbed if len(grabbed) <= n else sorted(
            random.sample(grabbed, n))
        from aiji.hippocampus.real_engines import ocr_images

        text, conf = ocr_images([p.read_bytes() for p in pick])
        seen: list[str] = []
        for line in (text or "").splitlines():
            line = line.strip()
            if line and line not in seen:
                seen.append(line)
        return "\\n".join(seen)[:_MAX_TEXT_CHARS], conf
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


__all__ = [
    "KNOWN_HOSTS", "VideoInfo", "VideoIngestError",
    "build_learn_text", "fetch_video_info", "learning_plan",
    "listen_video", "look_video",
]
