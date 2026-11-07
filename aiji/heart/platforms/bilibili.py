"""B 站适配器：刷排行榜学习 / 评论 / 私信 / 点赞。

复用 `aiji.senses.bili_client`（登录态、评论、投稿都在那儿），这里只做
"平台动作 → 统一结果"的翻译，外加闸门与留痕。

真实边界（2026-11-05 口径）：
- **评论真发已验证**（2026-11-04 真账号 BV1GJ411x7h7 rpid 322895379680）；
- **排行榜/视频详情读路径已验证**（不需要登录态，也不需要 wbi 签名）；
- **私信与点赞写路径只在离线测试里验过**，真账号还没发过 → 状态如实标 `experimental`；
- B 站没有"群聊"web 接口，粉丝群只有 App 内 → `group` 如实 unsupported。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from aiji.heart.platforms.base import (
    EXPERIMENTAL,
    NEEDS_LOGIN,
    OK,
    UNSUPPORTED,
    Adapter,
    Guard,
    PlatformError,
    Result,
)
from aiji.senses import bili_client
from aiji.senses.bili_client import BiliCredentials, BiliError
from aiji.skeleton.paths import DATA

DEFAULT_JAR = DATA / "bili_cookies.jar"


def _wrap(exc: Exception) -> PlatformError:
    if isinstance(exc, PlatformError):
        return exc
    if isinstance(exc, BiliError):
        code = exc.code
        if code in ("not_logged_in", "unknown_self"):
            return PlatformError(NEEDS_LOGIN, str(exc), status=401,
                                 hint="aiji bili-login --cookies-from-browser edge")
        if code in ("rate_limited",):
            return PlatformError("rate_limited", str(exc), status=429)
        return PlatformError(code, str(exc))
    return PlatformError("bili_error", str(exc)[:300])


class BiliAdapter(Adapter):
    name = "bilibili"

    def __init__(self, cred: BiliCredentials | None = None, *,
                 cred_loader: Callable[[], BiliCredentials | None] | None = None,
                 guard: Guard | None = None, dry_run: bool = True,
                 state_dir: str | Path | None = None) -> None:
        self._cred = cred
        self._loader = cred_loader or _load_default
        self.guard = guard
        self.dry_run = dry_run
        self.state_dir = Path(state_dir) if state_dir else Path("data")
        self._checked = False

    # ---- 登录态 -----------------------------------------------------------
    @property
    def cred(self) -> BiliCredentials | None:
        if self._cred is None:
            try:
                self._cred = self._loader()
            except Exception:  # noqa: BLE001 - 没登录态不是异常，是状态
                self._cred = None
        return self._cred

    def login_state(self) -> dict[str, Any]:
        cred = self.cred
        if cred is None or not cred.usable:
            return {"is_login": False, "reason": "没有 cookie"}
        try:
            info = bili_client.check_login(cred)
        except Exception as exc:  # noqa: BLE001
            return {"is_login": False, "reason": f"{type(exc).__name__}: {exc}"[:200]}
        self._checked = bool(info.get("is_login"))
        return info

    # ---- 能力 -------------------------------------------------------------
    def capabilities(self) -> dict[str, str]:
        return {
            "dm": EXPERIMENTAL,     # 写路径离线验过，真账号未发
            "group": UNSUPPORTED,
            "comment": OK,          # 2026-11-04 真机发过
            "feed": OK,             # 排行榜读路径已验证
            "streak": UNSUPPORTED,  # B 站没有火花机制
            "group_create": UNSUPPORTED,
            "like": OK,
        }

    def notes(self) -> dict[str, str]:
        return {
            "dm": "web_im/send_msg 已接；真账号发信未验证",
            "group": "B 站没有群聊 web 接口（粉丝群只在 App 内）",
            "comment": "2026-11-04 真账号真发通过（rpid 已回查）",
            "feed": "用全站排行榜（免 wbi 签名）；推荐流需要 wbi，未接",
            "streak": "B 站没有连续互动计数；对应动作是每天点赞/观看",
            "like": "2026-11-05 真机点赞成功（has/like 回查 liked=1）；取消点赞被 -400 拒（见 excluded#193）",
        }

    # ---- 读 ---------------------------------------------------------------
    def feed(self, limit: int = 10) -> list[dict[str, Any]]:
        self.require("feed")
        cred = self.cred
        cookie = cred.cookie_header() if (cred is not None and cred.usable) else ""
        try:
            return bili_client.ranking(limit, cookie=cookie)
        except Exception as exc:  # noqa: BLE001
            raise _wrap(exc) from exc

    def video(self, bvid: str) -> dict[str, Any]:
        try:
            return bili_client.video_detail(bvid)
        except Exception as exc:  # noqa: BLE001
            raise _wrap(exc) from exc

    # ---- 写 ---------------------------------------------------------------
    def comment(self, url: str, text: str, *, dry_run: bool | None = None,
                restyled: str = "") -> Result:
        text = str(text or restyled or "").strip()
        if not text:
            raise PlatformError("empty_text", "评论内容为空")
        dry = self.dry_run if dry_run is None else bool(dry_run)
        aid = bili_client.resolve_aid(url)
        if dry:
            return Result(True, self.name, "comment",
                          {"aid": aid, "url": url, "text": text}, dry_run=True,
                          code="dry_run")
        self._check("comment", str(aid))
        try:
            res = bili_client.post_comment(aid, text, self.cred, confirm=True,
                                           state_dir=self.state_dir)
        except Exception as exc:  # noqa: BLE001
            err = _wrap(exc)
            self._log("comment", str(aid), ok=False, code=err.code,
                      detail={"text": text})
            raise err from exc
        self._log("comment", str(aid), ok=True, detail={"text": text,
                                                        "rpid": res.rpid})
        return Result(True, self.name, "comment",
                      {"aid": aid, "url": url, "text": text, "rpid": res.rpid})

    def dm(self, mid: str | int, text: str, *, dry_run: bool | None = None) -> Result:
        text = str(text or "").strip()
        if not text:
            raise PlatformError("empty_text", "私信内容为空")
        dry = self.dry_run if dry_run is None else bool(dry_run)
        if dry:
            return Result(True, self.name, "dm",
                          {"peer": str(mid), "text": text}, dry_run=True,
                          code="dry_run")
        self._check("dm", str(mid))
        try:
            res = bili_client.send_dm(mid, text, self.cred, confirm=True,
                                      state_dir=self.state_dir)
        except Exception as exc:  # noqa: BLE001
            err = _wrap(exc)
            self._log("dm", str(mid), ok=False, code=err.code, detail={"text": text})
            raise err from exc
        self._log("dm", str(mid), ok=True, detail={"text": text})
        return Result(True, self.name, "dm", {"peer": str(mid), "text": text})

    def like(self, aid: int, *, dry_run: bool | None = None,
             unlike: bool = False) -> Result:
        if unlike:
            # 2026-11-05 真机：like=0 一律 -400 请求错误（like=1 正常，重复点赞是 65006）。
            # 宁可报 unsupported，也不让调用方以为"取消成功了"。
            raise PlatformError(
                "unsupported", "B 站取消点赞接口不可用（like=0 返回 -400）")
        dry = self.dry_run if dry_run is None else bool(dry_run)
        if dry:
            return Result(True, self.name, "like", {"aid": aid, "unlike": unlike},
                          dry_run=True, code="dry_run")
        self._check("like", str(aid))
        try:
            bili_client.like(aid, self.cred, confirm=True,
                             state_dir=self.state_dir, unlike=unlike)
        except Exception as exc:  # noqa: BLE001
            err = _wrap(exc)
            self._log("like", str(aid), ok=False, code=err.code, detail={})
            raise err from exc
        self._log("like", str(aid), ok=True, detail={"unlike": unlike})
        return Result(True, self.name, "like", {"aid": aid, "unlike": unlike})

    # ---- 内部 -------------------------------------------------------------
    def _check(self, action: str, key: str) -> None:
        if self.cred is None or not self.cred.usable:
            raise PlatformError(NEEDS_LOGIN, "没有 B 站登录态", status=401,
                                hint="aiji bili-login --cookies-from-browser edge")
        if self.guard is not None:
            self.guard.check(self.name, action, key)

    def _log(self, action: str, key: str, *, ok: bool, code: str = "",
             detail: dict[str, Any] | None = None) -> None:
        if self.guard is None:
            return
        self.guard.log({"platform": self.name, "action": action, "key": key,
                        "ok": ok, "code": code, "detail": detail or {}})


def _load_default() -> BiliCredentials | None:
    jar = DEFAULT_JAR
    if not jar.exists():
        return None
    return bili_client.parse_cookie_jar(jar)
