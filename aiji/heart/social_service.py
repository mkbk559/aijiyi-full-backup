"""QQ 空间与 QQ 分享动作。

The service delegates QQ-side side effects to NapCat's extended OneBot actions.
It never fakes a successful publish when NapCat is not configured.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable


class SocialError(RuntimeError):
    def __init__(self, code: str, message: str = "", *, status: int = 400) -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.status = int(status)


_VISIBILITY = {
    "public": 1,
    "all": 1,
    "friends": 4,
    "private": 64,
    "self": 64,
    "include": 16,
    "exclude": 128,
}
_IMAGE_SUFFIXES = frozenset((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"))


class SocialService:
    def __init__(
        self,
        call: Callable[[str, dict[str, Any]], Any] | None,
        *,
        media_root: str | Path,
        sticker_dir: str | Path | None = None,
        song_lookup: Callable[[str], dict[str, Any]] | None = None,
        sticker_status: Callable[[], dict[str, Any]] | None = None,
        enabled: bool = True,
    ) -> None:
        self._call = call
        self.media_root = Path(media_root).expanduser().resolve()
        self.sticker_dir = (
            Path(sticker_dir).expanduser().resolve()
            if sticker_dir is not None else None
        )
        self.song_lookup = song_lookup
        self.sticker_status = sticker_status
        self.enabled = bool(enabled and call is not None)
        self.media_root.mkdir(parents=True, exist_ok=True)

    def snapshot(self) -> dict[str, Any]:
        return {
            "configured": self.enabled,
            "transport": "napcat_onebot_http",
            "media_root": str(self.media_root),
            "stickers": self._sticker_snapshot(),
        }

    def publish(
        self,
        content: str,
        *,
        images: list[str] | None = None,
        visibility: str = "public",
        target_uins: list[str] | None = None,
    ) -> dict[str, Any]:
        text = self._text(content, "content", required=not images)
        validated = [self._media_path(item) for item in (images or [])]
        rights = self._visibility(visibility)
        targets = self._qq_numbers(target_uins or [])
        if rights in (16, 128) and not targets:
            raise SocialError(
                "target_uins_required",
                "target_uins is required for include/exclude visibility",
            )
        payload = {
            "content": text,
            "images": validated,
            "ugc_right": rights,
            "target_uins": targets,
        }
        data = self._invoke("send_qzone_msg", payload)
        return {
            "ok": True,
            "action": "qzone.publish",
            "tid": str((data or {}).get("tid") or ""),
            "time": (data or {}).get("time") or 0,
            "visibility": visibility,
            "images": len(validated),
        }

    def comment(
        self,
        tid: str,
        content: str,
        *,
        target_uin: str = "",
        images: list[str] | None = None,
    ) -> dict[str, Any]:
        post_id = str(tid or "").strip()
        if not post_id:
            raise SocialError("tid_required", "tid is required")
        text = self._text(content, "content", required=not images)
        payload: dict[str, Any] = {
            "tid": post_id,
            "content": text,
            "images": [self._media_path(item) for item in (images or [])],
        }
        if target_uin:
            payload["target_uin"] = self._qq_numbers([target_uin])[0]
        data = self._invoke("comment_qzone", payload)
        return {
            "ok": True,
            "action": "qzone.comment",
            "tid": post_id,
            "comment_id": str((data or {}).get("comment_id") or ""),
        }

    def auto_comment(
        self,
        target_uin: str,
        content: str,
        *,
        post_index: int = 0,
        images: list[str] | None = None,
    ) -> dict[str, Any]:
        owner = self._qq_numbers([target_uin])[0]
        index = int(post_index)
        if index < 0 or index >= 100:
            raise SocialError("invalid_post_index", "post_index must be between 0 and 99")
        data = self._invoke(
            "get_qzone_msg_list",
            {"target_uin": owner, "pos": 0, "num": index + 1},
        )
        posts = (data or {}).get("msglist") if isinstance(data, dict) else None
        if not isinstance(posts, list) or index >= len(posts):
            raise SocialError(
                "qzone_post_not_found",
                "the requested Qzone post was not found",
                status=404,
            )
        tid = str((posts[index] or {}).get("tid") or "").strip()
        if not tid:
            raise SocialError(
                "qzone_post_not_found",
                "Qzone did not return a usable tid",
                status=404,
            )
        result = self.comment(
            tid,
            content,
            target_uin=owner,
            images=images,
        )
        result["action"] = "qzone.auto_comment"
        result["post_index"] = index
        return result

    def set_avatar(self, file: str) -> dict[str, Any]:
        image = self._media_path(file)
        self._invoke("set_qq_avatar", {"file": image})
        return {"ok": True, "action": "qq.avatar", "file": image}

    def set_signature(self, signature: str) -> dict[str, Any]:
        text = self._text(signature, "signature")
        if len(text) > 200:
            raise SocialError("signature_too_long", "signature exceeds 200 characters")
        self._invoke("set_self_longnick", {"longNick": text})
        return {"ok": True, "action": "qq.signature", "signature": text}

    def share_music(
        self,
        query: str,
        *,
        caption: str = "",
        visibility: str = "public",
    ) -> dict[str, Any]:
        wanted = self._text(query, "query")
        song = dict(self.song_lookup(wanted) or {}) if self.song_lookup else {}
        song_id = str(song.get("id") or "").strip()
        title = str(song.get("title") or "").strip()
        url = str(song.get("url") or "").strip()
        if not song_id and re.fullmatch(r"\d{3,20}", wanted):
            song_id = wanted
            title = f"网易云 {song_id}"
            url = f"https://music.163.com/song?id={song_id}"
        if not title or not url:
            raise SocialError("song_not_found", f"could not resolve song: {wanted}")
        lead = self._text(caption, "caption", required=False)
        content = "\n".join(part for part in (lead, title, url) if part)
        published = self.publish(content, visibility=visibility)
        return {
            **published,
            "action": "share.music",
            "song_id": song_id,
            "title": title,
            "url": url,
        }

    def share_bilibili(
        self,
        value: str,
        *,
        title: str = "",
        caption: str = "",
        visibility: str = "public",
    ) -> dict[str, Any]:
        raw = self._text(value, "url")
        bvid = ""
        match = re.search(r"(BV[0-9A-Za-z]{10})", raw)
        if match:
            bvid = match.group(1)
            url = f"https://www.bilibili.com/video/{bvid}"
        elif re.fullmatch(r"av\d+", raw, re.IGNORECASE):
            url = f"https://www.bilibili.com/video/{raw.lower()}"
        else:
            parsed = re.match(
                r"https?://(?:www\.|m\.)?bilibili\.com/video/([^/?#]+)",
                raw,
                re.IGNORECASE,
            )
            if not parsed:
                raise SocialError("invalid_bilibili_url", "a Bilibili video URL, BV or av id is required")
            identity = parsed.group(1)
            bvid = identity if identity.lower().startswith("bv") else ""
            url = f"https://www.bilibili.com/video/{identity}"
        heading = self._text(title, "title", required=False) or "B站视频"
        lead = self._text(caption, "caption", required=False)
        content = "\n".join(part for part in (
            lead,
            f"【B站】{heading}",
            url,
        ) if part)
        published = self.publish(content, visibility=visibility)
        return {
            **published,
            "action": "share.bilibili",
            "bvid": bvid,
            "title": heading,
            "url": url,
        }

    def _invoke(self, action: str, payload: dict[str, Any]) -> Any:
        if not self.enabled or self._call is None:
            raise SocialError(
                "social_not_configured",
                "set AIJI_ONEBOT_API to a NapCat HTTP API endpoint",
                status=503,
            )
        try:
            return self._call(action, payload)
        except SocialError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SocialError(
                "onebot_call_failed",
                f"{type(exc).__name__}: {exc}",
                status=502,
            ) from exc

    @staticmethod
    def _text(value: Any, field: str, *, required: bool = True) -> str:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[ \t\f\v]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if required and not text:
            raise SocialError(f"{field}_required", f"{field} is required")
        if len(text) > 2000:
            raise SocialError(f"{field}_too_long", f"{field} exceeds 2000 characters")
        return text

    def _media_path(self, value: str) -> str:
        raw = str(value or "").strip()
        if re.match(r"https?://", raw, re.IGNORECASE):
            return raw
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = self.media_root / path
        path = path.resolve()
        roots = [self.media_root]
        if self.sticker_dir is not None:
            roots.append(self.sticker_dir)
        if not any(path == root or root in path.parents for root in roots):
            raise SocialError("media_path_forbidden", f"image path is outside allowed roots: {path}")
        if path.suffix.lower() not in _IMAGE_SUFFIXES:
            raise SocialError("unsupported_image", f"unsupported image type: {path.suffix}")
        if not path.is_file():
            raise SocialError("media_missing", f"image not found: {path}")
        return str(path)

    def _sticker_snapshot(self) -> dict[str, Any]:
        if self.sticker_status is not None:
            try:
                return dict(self.sticker_status())
            except Exception:  # noqa: BLE001
                pass
        summary: dict[str, Any] = {
            "dir": str(self.sticker_dir) if self.sticker_dir else "",
            "source": "local_manifest",
            "remote_fetch": False,
            "count": 0,
            "name": "",
            "moods": [],
        }
        if self.sticker_dir is None:
            return summary
        manifest = self.sticker_dir / "manifest.json"
        try:
            payload = json.loads(manifest.read_text("utf-8"))
        except (OSError, ValueError, TypeError):
            return summary
        if not isinstance(payload, dict):
            return summary
        stickers = payload.get("stickers")
        if not isinstance(stickers, list):
            return summary
        valid = []
        for item in stickers:
            if not isinstance(item, dict):
                continue
            raw = str(item.get("file") or "").strip()
            path = (self.sticker_dir / raw).resolve()
            if (
                raw
                and self.sticker_dir in path.parents
                and path.suffix.lower() in _IMAGE_SUFFIXES
                and path.is_file()
            ):
                valid.append({
                    "file": path.name,
                    "mood": str(item.get("mood") or ""),
                })
        summary["name"] = str(payload.get("name") or "")
        summary["count"] = len(valid)
        summary["moods"] = sorted({
            item["mood"] for item in valid if item["mood"]
        })
        return summary

    @staticmethod
    def _visibility(value: str) -> int:
        text = str(value or "public").strip().lower()
        try:
            rights = int(text)
        except ValueError:
            rights = _VISIBILITY.get(text, 0)
        if rights not in (1, 4, 16, 64, 128):
            raise SocialError("invalid_visibility", f"unsupported visibility: {value}")
        return rights

    @staticmethod
    def _qq_numbers(values: list[Any]) -> list[str]:
        out = []
        for value in values:
            qq = str(value or "").strip()
            if not re.fullmatch(r"[1-9]\d{0,19}", qq):
                raise SocialError("invalid_qq", f"invalid QQ number: {value}")
            out.append(qq)
        return out


__all__ = ["SocialError", "SocialService"]
