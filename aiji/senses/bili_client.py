"""B 站账号客户端：登录检查 + 评论（护栏默认开）。

账号来源：yt-dlp 能读的 cookie（--cookies-from-browser 或 Netscape jar 文件）。
用户给玛卡巴卡开了 B 站账号后，在 Edge/Chrome 里登录一次即可。

护栏（自我管理的红线，2026-11-04 与用户约定）：
- 一切外发动作默认 dry-run：先打印将要发生什么，``--yes`` 才真发；
- 限速：两次真发评论至少隔 60s（防风控也防口无遮拦）；
- 留痕：每次真发都写 data/bili_actions.jsonl（时间/动作/目标/内容/结果）；
- 评论内容必须先过 persona（玛卡巴卡，6 岁半）——由调用方（brain）负责措辞，
  本模块只管"安全地发出去"。

API 事实（2026-11-04 探针 104110 验证读取路径；写路径待真 cookie 后实测）：
- 登录态：GET /x/web-interface/nav（带 SESSDATA）→ data.isLogin / data.uname
- 评论：POST /x/v2/reply/add，表单 oid=<aid>&type=1&message=<文本>&csrf=<bili_jct>
- aid 解析：yt-dlp --dump-json 的 id 字段就是 av 号
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
MIN_INTERVAL_S = 60.0


class BiliError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class BiliCredentials:
    sessdata: str = ""
    bili_jct: str = ""
    dedeuserid: str = ""

    def cookie_header(self) -> str:
        parts = []
        if self.sessdata:
            parts.append(f"SESSDATA={self.sessdata}")
        if self.dedeuserid:
            parts.append(f"DedeUserID={self.dedeuserid}")
        if self.bili_jct:
            parts.append(f"bili_jct={self.bili_jct}")
        return "; ".join(parts)

    @property
    def usable(self) -> bool:
        return bool(self.sessdata and self.bili_jct)


def parse_cookie_jar(path: str | Path) -> BiliCredentials:
    """解析 Netscape cookie jar（yt-dlp --cookies 导出的格式）。"""
    p = Path(path)
    if not p.exists():
        raise BiliError("cookie_file_missing", f"cookie 文件不存在：{p}")
    cred = BiliCredentials()
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        name, value = parts[5], parts[6]
        if name == "SESSDATA":
            cred.sessdata = value
        elif name == "bili_jct":
            cred.bili_jct = value
        elif name == "DedeUserID":
            cred.dedeuserid = value
    if not cred.usable:
        raise BiliError("cookie_not_logged_in",
                        "cookie 文件里没有 SESSDATA/bili_jct——先在浏览器里登录 B 站"
                        "再用 yt-dlp 导出（见 learn-video --cookies-from-browser）")
    return cred


def credentials_from_browser(browser: str, video_url: str,
                             timeout_s: float = 90.0) -> BiliCredentials:
    """用 yt-dlp 从浏览器导 cookie（借用它的 DPAPI 解密），返回凭据。"""
    import subprocess
    import sys
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        jar = Path(f.name)
    try:
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "yt_dlp", "--proxy", "",
             "--cookies-from-browser", browser, "--cookies", str(jar),
             "--skip-download", "--no-warnings", video_url],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout_s)
        if not jar.exists():
            raise BiliError(
                "cookie_export_failed",
                (proc.stderr or "").strip().splitlines()[-1][:160] if proc.stderr else "no jar")
        return parse_cookie_jar(jar)
    finally:
        try:
            jar.unlink()
        except OSError:
            pass


def _get_json(url: str, cookie: str = "", timeout: float = 10.0) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Referer": "https://www.bilibili.com/",
        **({"Cookie": cookie} if cookie else {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def check_login(cred: BiliCredentials) -> dict:
    """查登录态（无副作用）。返回 {is_login, uname, mid, level}。"""
    if not cred.sessdata:
        return {"is_login": False, "reason": "没有 SESSDATA"}
    try:
        data = _get_json("https://api.bilibili.com/x/web-interface/nav",
                         cookie=cred.cookie_header())
    except urllib.error.HTTPError as exc:
        raise BiliError("nav_http_error", f"nav 接口 HTTP {exc.code}") from exc
    d = data.get("data") or {}
    return {"is_login": bool(d.get("isLogin")), "uname": d.get("uname") or "",
            "mid": d.get("mid"), "level": d.get("level_info", {}).get("current_level")}


def bv_to_av(bvid: str) -> int:
    """BV 号 → av 号（B 站公开的确定性算法，离线可算）。

    算法来源：bilibili-API-collect（XOR_CODE/MASK_CODE + 自定义 base58 表）。
    与线上互相转换的行为一致（2026-11-04 用 BV1GJ411x7h7 → av97630895 验证）。
    """
    xor_code = 23442827791579
    mask_code = 2251799813685247
    alphabet = "FcwAPNKTMug3GV5Lj7EJnHpWsx4tb8haYeviqBz6rkCy12mUSDQX9RdoZf"
    encode_map = (8, 7, 0, 5, 1, 3, 2, 4, 6)
    decode_map = tuple(reversed(encode_map))
    base = len(alphabet)
    prefix = "BV1"
    if not bvid.startswith(prefix):
        raise BiliError("bad_bvid", f"不是 BV 号：{bvid!r}")
    body = bvid[len(prefix):]
    tmp = 0
    for i in range(len(encode_map)):
        ch = body[decode_map[i]]
        idx = alphabet.find(ch)
        if idx < 0:
            raise BiliError("bad_bvid", f"BV 号里有非法字符 {ch!r}：{bvid}")
        tmp = tmp * base + idx
    return (tmp & mask_code) ^ xor_code


def resolve_aid(video_url: str, timeout_s: float = 60.0) -> int:
    """视频链接 → av 号。BV 号走确定性算法，纯数字直接用。"""
    from aiji.senses.video_ingest import fetch_video_info

    vid = fetch_video_info(video_url, timeout_s=timeout_s).video_id or ""
    vid = str(vid).strip()
    if vid.upper().startswith("BV"):
        return bv_to_av(vid)
    try:
        return int(vid)
    except (TypeError, ValueError) as exc:
        raise BiliError("bad_video_id", f"无法解析 av 号：{vid!r}") from exc


@dataclass
class CommentResult:
    ok: bool
    dry_run: bool
    message: str
    rpid: int | None = None
    raw: dict = field(default_factory=dict)


def post_comment(
    aid: int,
    message: str,
    cred: BiliCredentials,
    *,
    confirm: bool = False,
    state_dir: Path | None = None,
    now: float | None = None,
) -> CommentResult:
    """发一条评论。confirm=False 时只做 dry-run（返回将要发什么）。

    限速与留痕都在真发路径；dry-run 不写留痕、不占限速窗口。
    """
    if not cred.usable:
        raise BiliError("not_logged_in", "没有可用登录态（SESSDATA/bili_jct 缺失）")
    message = message.strip()
    if not message:
        raise BiliError("empty_message", "评论内容为空")
    if len(message) > 1000:
        raise BiliError("message_too_long", f"评论 {len(message)} 字，B 站上限约 1000")
    if not confirm:
        return CommentResult(ok=False, dry_run=True,
                             message=f"[dry-run] 将在 av{aid} 下评论：{message}")
    # 限速：读留痕文件里最后一次真发时间
    t = time.time() if now is None else now
    log = (state_dir or Path("data")) / "bili_actions.jsonl"
    if log.exists():
        try:
            lines = log.read_text(encoding="utf-8").strip().splitlines()
            if lines:
                last = json.loads(lines[-1])
                if last.get("action") == "comment" and last.get("ok"):
                    wait = MIN_INTERVAL_S - (t - float(last.get("ts", 0)))
                    if wait > 0:
                        raise BiliError(
                            "rate_limited",
                            f"距上次评论 {int(t - float(last['ts']))}s，"
                            f"限速 {int(MIN_INTERVAL_S)}s，再等 {int(wait)}s")
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            pass  # 留痕坏了不拦动作，限速本来就是软护栏
    form = urllib.parse.urlencode({
        "oid": aid, "type": "1", "message": message,
        "csrf": cred.bili_jct, "plat": "1", "web_location": "1315875",
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.bilibili.com/x/v2/reply/add", data=form, headers={
            "User-Agent": UA, "Referer": f"https://www.bilibili.com/video/av{aid}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": cred.cookie_header()})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            payload = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raise BiliError("comment_http_error", f"评论接口 HTTP {exc.code}") from exc
    code = payload.get("code")
    ok = code == 0
    rpid = None
    try:
        rpid = int((((payload.get("data") or {}).get("reply")) or {}).get("rpid"))
    except (TypeError, ValueError):
        pass
    _log_action(state_dir or Path("data"), {
        "ts": t, "action": "comment", "aid": aid, "message": message,
        "ok": ok, "code": code, "rpid": rpid,
        "message_brief": payload.get("message", ""),
    })
    if not ok:
        raise BiliError("comment_rejected",
                        f"评论被拒 code={code} message={payload.get('message')}")
    return CommentResult(ok=True, dry_run=False,
                         message=f"已在 av{aid} 下发出评论", rpid=rpid, raw=payload)


def _log_action(state_dir: Path, entry: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    log = state_dir / "bili_actions.jsonl"
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


__all__ = [
    "BiliCredentials", "BiliError", "CommentResult",
    "bv_to_av", "check_login", "credentials_from_browser", "parse_cookie_jar",
    "post_comment", "resolve_aid",
]
