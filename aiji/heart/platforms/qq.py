"""QQ 适配器：私聊 / 群聊 / 建群 / 名片赞 / 历史消息（OneBot 11 over NapCat）。

约定：`call(action, payload)` 是唯一出口（`OneBotClient.call` 的签名），
测试里塞一个假 call 就能把整条路径跑一遍，不需要真 QQ。

真实边界（2026-11-05 实测）：
- `send_private_msg` / `send_group_msg` / `get_friend_list` / `get_group_list` 都可用；
- **建群没有 OneBot 标准动作**：NapCat 侧不一定有 `create_group`，这里如实报
  `unsupported`，不假装成功；
- 火花是 QQ 自己算的，但**续火花靠的是"今天有没有互动"**——账本在 `streak.py`，
  这里只提供"发一条真消息 + 点个赞"这两个能计数的动作。
"""

from __future__ import annotations

from typing import Any, Callable

from aiji.heart.platforms.base import (
    EXPERIMENTAL,
    NEEDS_LOGIN,
    OFFLINE,
    OK,
    UNSUPPORTED,
    Adapter,
    Guard,
    PlatformError,
    Result,
)

# OneBot/NapCat 的失败说法 → 机器判据
_TRANSPORT_HINTS = ("refused", "actively refused", "timed out", "timeout",
                    "urlopen error", "connection", "tunnel", "10061")


def _classify(exc: Exception) -> PlatformError:
    if isinstance(exc, PlatformError):
        return exc
    text = str(exc)
    low = text.lower()
    if any(h in low for h in _TRANSPORT_HINTS):
        return PlatformError("transport_down", text[:300], status=503,
                             hint="NapCat 的 OneBot HTTP(3000) 没起来；群里能用说明还在 websocket 上")
    if "not found" in low or "不支持的" in text or "unsupported" in low:
        return PlatformError("unsupported", text[:300])
    return PlatformError("onebot_error", text[:300])


class QQAdapter(Adapter):
    name = "qq"

    def __init__(self, call: Callable[[str, dict[str, Any]], Any], *,
                 guard: Guard | None = None, dry_run: bool = True,
                 cluster: str = "") -> None:
        self.call = call
        self.guard = guard
        self.dry_run = dry_run
        self.cluster = cluster or "agent"

    # ---- 能力 -------------------------------------------------------------
    def capabilities(self) -> dict[str, str]:
        return {
            "dm": OK,
            "group": OK,
            "comment": UNSUPPORTED,      # QQ 空间评论要走 qzone 接口，不在这条链上
            "feed": UNSUPPORTED,         # QQ 没有公开"推荐流"
            "streak": OK,                # 账本 + 发送都在本仓库
            "group_create": EXPERIMENTAL,  # NapCat 是否实现 create_group 未证
            "like": OK,                  # send_like：名片赞，真实存在
        }

    def notes(self) -> dict[str, str]:
        return {
            "comment": "QQ 空间评论不在 OneBot 链路上（social_service 里是另一条）",
            "feed": "QQ 没有可刷的公开内容流",
            "group_create": "标准 OneBot 无建群动作；未在 NapCat 上验证存在",
            "streak": "火花本身由 QQ 计算，这里管的是'今天有没有互动'",
        }

    # ---- 查询 -------------------------------------------------------------
    def probe(self) -> dict[str, Any]:
        """登录态与传输是否真的通。失败不抛，返回结构化状态。"""
        try:
            data = self.call("get_login_info", {})
        except Exception as exc:  # noqa: BLE001
            err = _classify(exc)
            return {"online": False, "code": err.code, "error": err.message}
        data = data or {}
        return {"online": True, "self_id": str(data.get("user_id", "")),
                "nickname": str(data.get("nickname", ""))}

    def _state(self) -> str:
        return OK if self.probe().get("online") else OFFLINE

    def login_state(self) -> dict[str, Any]:
        """统一入口：QQ 的"登录态"就是 OneBot 传输 + 账号信息。"""
        state = self.probe()
        state["is_login"] = bool(state.get("online"))
        return state

    def friends(self, limit: int = 100) -> list[dict[str, Any]]:
        try:
            rows = self.call("get_friend_list", {}) or []
        except Exception as exc:  # noqa: BLE001
            raise _classify(exc) from exc
        out = []
        for row in rows[:limit]:
            out.append({
                "peer": str(row.get("user_id", "")),
                "name": str(row.get("nickname") or row.get("remark") or ""),
                "remark": str(row.get("remark") or ""),
            })
        return out

    def groups(self, limit: int = 200) -> list[dict[str, Any]]:
        try:
            rows = self.call("get_group_list", {}) or []
        except Exception as exc:  # noqa: BLE001
            raise _classify(exc) from exc
        out = []
        for row in rows[:limit]:
            out.append({
                "peer": str(row.get("group_id", "")),
                "name": str(row.get("group_name", "")),
                "member_count": int(row.get("member_count") or 0),
            })
        return out

    def history(self, kind: str, peer: str | int, *, count: int = 20) -> list[dict[str, Any]]:
        action = "get_friend_msg_history" if kind == "friend" else "get_group_msg_history"
        payload = {"user_id": int(peer), "count": int(count)} if kind == "friend" \
            else {"group_id": int(peer), "count": int(count)}
        try:
            data = self.call(action, payload) or {}
        except Exception as exc:  # noqa: BLE001
            raise _classify(exc) from exc
        rows = data.get("messages") if isinstance(data, dict) else data
        out = []
        for row in rows or []:
            out.append({
                "ts": row.get("time"),
                "user": str((row.get("sender") or {}).get("user_id", "")),
                "text": _plain(row.get("message")),
            })
        return out

    # ---- 写 ---------------------------------------------------------------
    def dm(self, user_id: str | int, text: str, *, dry_run: bool | None = None) -> Result:
        return self._send("dm", "send_private_msg",
                          {"user_id": int(user_id)}, str(user_id), text, dry_run)

    def group(self, group_id: str | int, text: str, *, dry_run: bool | None = None) -> Result:
        return self._send("group", "send_group_msg",
                          {"group_id": int(group_id)}, str(group_id), text, dry_run)

    def voice(self, peer: str | int, path: str, *, scope: str = "dm",
              dry_run: bool | None = None) -> Result:
        """发语音条（NapCat 走 CQ 码 record，file 用绝对路径的 file:// URI）。

        2026-11-06 先做成"能发但不默认发"：是否该发由 `aiji/heart/voice_plan.py` 决定，
        这里只负责把手里的音频送出去。
        """
        from pathlib import Path as _Path

        audio = _Path(path)
        if not audio.exists():
            return Result(False, self.name, "voice", {"error": f"文件不存在：{path}"},
                          dry_run=True, code="missing_file")
        cq = f"[CQ:record,file={audio.resolve().as_uri()}]"
        action = "send_private_msg" if scope == "dm" else "send_group_msg"
        key = "user_id" if scope == "dm" else "group_id"
        return self._send("voice", action, {key: int(peer)}, str(peer), cq, dry_run)

    def like(self, user_id: str | int, times: int = 1, *, dry_run: bool | None = None) -> Result:
        dry = self.dry_run if dry_run is None else bool(dry_run)
        payload = {"user_id": int(user_id), "times": max(1, min(10, int(times)))}
        if dry:
            return Result(True, self.name, "like", {"peer": str(user_id),
                                                    "would_send": payload}, dry_run=True)
        self._check("like", str(user_id))
        try:
            self.call("send_like", payload)
        except Exception as exc:  # noqa: BLE001
            err = _classify(exc)
            self._log("like", str(user_id), ok=False, code=err.code, detail=payload)
            raise err from exc
        self._log("like", str(user_id), ok=True, detail=payload)
        return Result(True, self.name, "like", {"peer": str(user_id), "times": payload["times"]})

    def group_create(self, name: str, members: list[str], *,
                     dry_run: bool | None = None) -> Result:
        dry = self.dry_run if dry_run is None else bool(dry_run)
        payload = {"group_name": name, "user_ids": [int(m) for m in members if str(m).isdigit()]}
        if dry:
            return Result(True, self.name, "group_create",
                          {"would_send": payload}, dry_run=True, code="dry_run")
        self._check("group_create", name)
        try:
            data = self.call("create_group", payload)
        except Exception as exc:  # noqa: BLE001
            err = _classify(exc)
            self._log("group_create", name, ok=False, code=err.code, detail=payload)
            if err.code in ("unsupported", "onebot_error"):
                raise PlatformError(
                    "unsupported",
                    f"NapCat 没有可用的建群动作（{err.message}）",
                    hint="建群是账号侧动作：QQ 手机端建群后，用 --peer 记进 account 即可",
                ) from exc
            raise err from exc
        self._log("group_create", name, ok=True, detail=payload)
        return Result(True, self.name, "group_create",
                      {"group_id": str((data or {}).get("group_id", "")) if isinstance(data, dict) else "",
                       "name": name, "members": payload["user_ids"]})

    # ---- 内部 -------------------------------------------------------------
    def _send(self, action: str, ob_action: str, target: dict[str, Any], key: str,
              text: str, dry_run: bool | None) -> Result:
        text = str(text or "").strip()
        if not text:
            raise PlatformError("empty_text", "发空消息没有意义")
        dry = self.dry_run if dry_run is None else bool(dry_run)
        payload = dict(target)
        payload["message"] = [{"type": "text", "data": {"text": text}}]
        if dry:
            return Result(True, self.name, action,
                          {"peer": key, "text": text, "would_send": ob_action},
                          dry_run=True, code="dry_run")
        self._check(action, key)
        try:
            data = self.call(ob_action, payload)
        except Exception as exc:  # noqa: BLE001
            err = _classify(exc)
            self._log(action, key, ok=False, code=err.code, detail={"text": text})
            raise err from exc
        message_id = ""
        if isinstance(data, dict):
            message_id = str(data.get("message_id", ""))
        self._log(action, key, ok=True, detail={"text": text, "message_id": message_id})
        return Result(True, self.name, action,
                      {"peer": key, "text": text, "message_id": message_id})

    def _check(self, action: str, key: str) -> None:
        if self.guard is not None:
            self.guard.check(self.name, action, key)

    def _log(self, action: str, key: str, *, ok: bool, code: str = "",
             detail: dict[str, Any] | None = None) -> None:
        if self.guard is None:
            return
        self.guard.log({"platform": self.name, "action": action, "key": key,
                        "ok": ok, "code": code, "detail": detail or {}})


def _plain(message: Any) -> str:
    """把 OneBot 的 message（数组或 CQ 串）压成纯文本，给账本/火花判断用。"""
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        parts = []
        for seg in message:
            if not isinstance(seg, dict):
                continue
            if seg.get("type") == "text":
                parts.append(str((seg.get("data") or {}).get("text", "")))
            elif seg.get("type") == "at":
                parts.append("@")
        return "".join(parts)
    return ""
