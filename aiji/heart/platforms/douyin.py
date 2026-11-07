"""抖音适配器：刷视频学习 / 发评论 / 私信（真实浏览器 + 真实登录态）。

为什么是浏览器而不是接口：抖音网页的每个读接口都带 `a_bogus`/`X-Bogus` 签名，
签名算法随版本变；写接口（评论/私信）还要设备指纹。**跟着签名跑是一次性的，
跟着 DOM 跑只要页面结构不大改就还能用**——所以这里走 Playwright + 用户自己的登录态。

三道红线（照 bili_client 的约定）：
- 写动作默认 dry-run，`--yes` 才真发；每次尝试都进 `platform_actions.jsonl`；
- 动作之间限速（默认 90s），日上限默认 10 次（抖音风控比 B 站狠）；
- 选择器找不到时抛 `selector_missing` 并**列出试过的选择器**，不静默成功。

真机状态（2026-11-05）：
- headless 打开 douyin.com 会掉进"验证中间页"（`title` 含"验证"）→ 读内容要 `headless=False`；
- 本仓库**还没有登录过的抖音账号**，所以 `feed`/`comment` 的 DOM 路径只做过离线（假页面）测试。
"""

from __future__ import annotations

import base64
import re
import time
from pathlib import Path
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
from aiji.left_hand.browser import TEMP_ROOT, stamp

HOME = "https://www.douyin.com/"
HOT = "https://www.douyin.com/hot"        # 热点榜：**无需登录**就有视频卡片（2026-11-05 实测 20 个）
SEARCH = "https://www.douyin.com/search/{kw}?type=video"
CHAT = "https://www.douyin.com/chat"

# 登录态 cookie（有 sessionid 基本就算登录了）
LOGIN_COOKIES = ("sessionid", "sessionid_ss", "sid_tt")

# /chat 会话列表的每一项，以及打开会话后的输入框（draft.js/editor-kit）
CONV_ITEM = ".conversationConversationItemwrapper"
# 真机 class 全名是 `zone-container editor-kit-container messageEditorinputArea n`
# 套在 `messageEditorimChatEditorContainer` 里；之前写 `.messageEditori` 是被截断的名字。
CHAT_EDITOR = ('[class*="messageEditorinputArea"], '
               '.messageEditorimChatEditorContainer [contenteditable="true"]')

# 采集用 JS：一次 evaluate 拿回所有卡片，避免逐个 query 的时序问题
_COLLECT_JS = """
() => {
  const out = [];
  const seen = new Set();
  for (const a of document.querySelectorAll('a[href*="/video/"]')) {
    const href = a.href.split('?')[0];
    const m = href.match(/\\/video\\/(\\d+)/);
    if (!m || seen.has(m[1])) continue;
    seen.add(m[1]);
    const card = a.closest('li,div');
    const text = (card ? card.innerText : a.innerText || '').trim().slice(0, 300);
    out.push({video_id: m[1], url: href, text});
  }
  return out;
}
"""

# 评论区打开方式：先点"留下你的精彩评论吧"占位块（draft.js 编辑器会随之出现），
# 再退回到评论图标（2026-11-05 实测顺序）
COMMENT_OPEN = (
    '.comment-input-inner-container',
    '[class*="comment-input-inner"]',
)

# draft.js 编辑器的正文（点击后才有）
COMMENT_INPUT = (
    '.public-DraftEditor-content',
    'div[role="textbox"]',
    '[data-e2e="comment-input"]',
    'div.comment-input-inner',
    'div[contenteditable="true"]',
    'textarea[placeholder*="评论"]',
)

# 点赞：读当前赞数 / 点它
_LIKE_STATE_JS = """
() => {
  const el = document.querySelector('[data-e2e="video-player-digg"]');
  return el ? (el.innerText || '').replace(/\\s+/g, '').trim() : '';
}
"""

_CLICK_LIKE_JS = """
() => {
  const el = document.querySelector('[data-e2e="video-player-digg"]');
  if (!el) return false;
  (el.querySelector('div,span,svg') || el).click();
  return true;
}
"""

# 读回 draft.js 里当前输入的文字（判断键盘事件有没有真的进去）
_DRAFT_TEXT_JS = """
() => {
  const ed = document.querySelector('.public-DraftEditor-content');
  return ed ? (ed.innerText || '') : '';
}
"""

# 点"发送"：draft.js 版评论框的发送按钮常是纯文字节点，没有 data-e2e
_CLICK_SEND_JS = """
() => {
  const nodes = Array.from(document.querySelectorAll('span,div,button,a'));
  for (const n of nodes) {
    const t = (n.innerText || '').trim();
    if (t === '发送' && (n.offsetWidth || n.offsetHeight)) { n.click(); return t; }
  }
  return '';
}
"""

# 发完之后的提示条（"评论成功"/"请先登录"/"操作过于频繁"）
_TOAST_JS = """
() => {
  const t = document.querySelector('[class*="toast"],[class*="Toast"],[role="alert"]');
  return t ? (t.innerText || '').replace(/\\s+/g, ' ').slice(0, 80) : '';
}
"""
COMMENT_SEND = (
    '[data-e2e="comment-publish"]',
    'div[data-e2e="comment-publish"]',
    'button:has-text("发送")',
    'span:has-text("发送")',
)

# 评论侧栏默认是折叠的，点这些把面板打开（2026-11-05 实测）
COMMENT_TOGGLE = (
    '[data-e2e="feed-comment-icon"]',
    '[data-e2e="comment-icon"]',
    '[data-e2e="video-comment-icon"]',
    'span[data-e2e="comment-count"]',
)


# 点开登录弹窗：优先按"登录"字样点，其次按常见的 data-e2e 兜底
_OPEN_LOGIN_JS = """
() => {
  const cands = Array.from(document.querySelectorAll(
    '[data-e2e="login-button"], #douyin-header-menuCt button, button, div, span, a'));
  for (const n of cands) {
    const t = (n.innerText || '').trim();
    if (t === '登录' || t === '立即登录' || t === '登录/注册') {
      try { n.click(); return t; } catch (e) {}
    }
  }
  return '';
}
"""

# 页面上"可见的登录按钮"个数：0 才是真登录（cookie 在但按钮在 = 前端不认）
_LOGIN_BUTTON_JS = """
() => Array.from(document.querySelectorAll('button,div,span,a'))
  .filter(e => (e.innerText || '').trim() === '登录'
               && (e.offsetWidth || e.offsetHeight) > 0).length
"""

# 从登录弹窗里抠二维码：先找 data:image 的 img，再找弹窗内的 canvas
_GRAB_QR_JS = """
() => {
  const imgs = Array.from(document.querySelectorAll('img'));
  for (const im of imgs) {
    const src = im.getAttribute('src') || '';
    const w = im.naturalWidth || im.width || 0;
    const h = im.naturalHeight || im.height || 0;
    if (src.startsWith('data:image') && w >= 100 && w <= 500 && h >= 100) {
      return {data_url: src, w, h};
    }
  }
  for (const im of imgs) {
    const src = im.getAttribute('src') || '';
    const w = im.naturalWidth || im.width || 0;
    if (/qrcode|qr/i.test(src) && w >= 80) return {data_url: src, w};
  }
  const canvases = Array.from(document.querySelectorAll('canvas'));
  for (const c of canvases) {
    const w = c.width || 0;
    if (w >= 100 && w <= 500) {
      try { return {canvas: c.toDataURL('image/png'), w}; } catch (e) {}
    }
  }
  return {};
}
"""


def _looks_like_qr(raw: bytes) -> bool:
    """粗校验：这张图是不是"可能像二维码"（挡掉纯黑/纯白 canvas 和空白图）。

    用 Pillow 是可选依赖，没有就退回"文件大小 > 800 字节"这种弱判断。
    """
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(raw)) as im:
            im = im.convert("L")
            w, h = im.size
            if w < 120 or h < 120 or abs(w - h) > max(w, h) * 0.35:
                return False
            colors = im.getcolors(65536) or []
            if len(colors) < 2:          # 纯黑/纯白 canvas → 假
                return False
            lo, hi = im.getextrema()
            return hi - lo > 64
    except Exception:  # noqa: BLE001 - 没装 Pillow 或解不开就走弱判断
        return len(raw) > 800


def _is_verify(session: Any) -> bool:
    title = ""
    try:
        title = session.title()
    except Exception:  # noqa: BLE001
        pass
    url = ""
    try:
        url = session.url()
    except Exception:  # noqa: BLE001
        pass
    return ("验证" in title) or ("verify" in url.lower()) or ("captcha" in url.lower())


# 2026-11-05：**在页面上下文里发一条 fetch** 才是抖音评论的正确发法。
# 三种做法都试过：①点 UI → 会弹短信验证；②Python 自己签名 POST → 403；
# ③页面里 fetch → 服务端返回带 cid 的 JSON（真机 cid=7704571082514711306）。
# 前提是**账号已验证**：人工在有头窗口过一次验证 + 亲手发一条之后，这条路立刻打通。
_PUBLISH_COMMENT_JS = """
async ([aweme, text]) => {
  const COMMON = {
    device_platform:'webapp', aid:'6383', channel:'channel_pc_web',
    pc_client_type:'1', version_code:'190500', version_name:'19.5.0',
    cookie_enabled:'true', screen_width:'1920', screen_height:'1080',
    browser_language:'zh-CN', browser_platform:'Win32',
    browser_name:'Chrome', browser_version:'141.0.0.0',
    engine_name:'Blink', engine_version:'141.0.0.0',
    os_name:'Windows', os_version:'10', platform:'PC'
  };
  const qs = (d) => Object.keys(d).map(k => encodeURIComponent(k)+'='+encodeURIComponent(d[k])).join('&');
  const body = qs({ aweme_id: aweme, text, text_extra: '[]',
                    one_level_comment_rank: '-1', paste_edit_method: 'non_paste' });
  const r = await fetch('/aweme/v1/web/comment/publish?' + qs(COMMON), {
    method: 'POST', credentials: 'include',
    headers: { 'content-type': 'application/x-www-form-urlencoded' },
    body,
  });
  const t = await r.text();
  return { status: r.status, len: t.length, text: t.slice(0, 200000) };
}
"""

# 验证页判定：验证码 iframe **常驻 DOM**，只有可见且没有 video 才算被拦（照 dsh-douyin-watch 的口径）
_VERIFY_JS = """
() => {
  const vf = document.querySelector('iframe[src*="rc-verifycenter"], iframe[src*="verifycenter"], iframe[src*="/captcha"]');
  const vis = !!(vf && vf.offsetWidth > 2 && vf.offsetHeight > 2
                 && getComputedStyle(vf).visibility !== 'hidden');
  const hasVideo = !!document.querySelector('video');
  const t = String(document.title || '');
  return { verify: /验证码|安全验证/.test(t) || (vis && !hasVideo),
           title: t.slice(0, 60), has_video: hasVideo, iframe_visible: vis };
}
"""


def _aweme_id(url: str) -> str:
    """从视频链接里抠 aweme_id：支持 /video/<id>、?modal_id=<id>、纯 id。"""
    m = re.search(r"/video/(\d{6,})", url or "")
    if m:
        return m.group(1)
    m = re.search(r"modal_id=(\d{6,})", url or "")
    if m:
        return m.group(1)
    m = re.fullmatch(r"\s*(\d{6,})\s*", url or "")
    return m.group(1) if m else ""


# 2026-11-05：抖音/微信这类聊天，长句要么被吞、要么不像人说话。拆成短的逐条发。
_SENT_END = "。！？!?；;…~～\n"


def _split_message(text: str, max_chars: int = 0) -> list[str]:
    """按句末标点把长回复拆成多条；单条上限 max_chars（0 = 用环境变量/默认 24）。

    规则很朴素，但足够：先按句末标点切，再把过长的片段按逗号切，最后按字数硬切。
    """
    import os as _os

    raw = str(text or "").strip()
    if not raw:
        return []
    limit = int(max_chars or _os.environ.get("AIJI_DOUYIN_CHUNK_CHARS", "24") or 24)
    limit = max(8, limit)
    if len(raw) <= limit:
        return [raw]
    pieces: list[str] = []
    buf = ""
    for ch in raw:
        buf += ch
        if ch in _SENT_END and len(buf.strip()) >= 2:
            pieces.append(buf.strip())
            buf = ""
    if buf.strip():
        pieces.append(buf.strip())
    # 过长的片段再按逗号拆，最后硬切
    out: list[str] = []
    for piece in pieces:
        while len(piece) > limit:
            cut = max(piece.rfind("，", 0, limit), piece.rfind(",", 0, limit),
                      piece.rfind("、", 0, limit))
            if cut < limit // 2:
                cut = limit - 1
            out.append(piece[:cut + 1].strip())
            piece = piece[cut + 1:].strip()
        if piece:
            out.append(piece)
    return out


# 2026-11-05：真机发出去的 120 条里翻出 3 处身份/年龄自相矛盾
# （"我是小肥鱼" ×2、"六岁在小学一年级" ×2）。人格已经改由成长阶段决定，
# 但模型偶尔还会把旧设定说出口——发之前拦一道，宁可这一条不发。
_IDENTITY_CONFLICT = (
    ("小肥鱼", "旧昵称，现在叫玛卡巴卡"),
    ("六岁", "年龄由成长阶段决定，小学阶段已过"),
    ("小学一年级", "年级由成长阶段决定"),
    ("小学二年级", "年级由成长阶段决定"),
)


def check_identity(text: str, *, stage_label: str = "") -> None:
    """发之前自检：别把旧人格/错年龄说出去。小学阶段放行。"""
    if stage_label and "小学" in stage_label:
        return
    for bad, why in _IDENTITY_CONFLICT:
        if bad in str(text or ""):
            raise PlatformError("identity_conflict",
                                f"这句里有「{bad}」：{why}（当前阶段 {stage_label or '未知'}），已拦下不发",
                                hint="改掉这句或者重生成；确实要说旧设定就手动放行")


class DouyinAdapter(Adapter):
    name = "douyin"

    def __init__(self, session_factory: Callable[..., Any] | None = None, *,
                 guard: Guard | None = None, dry_run: bool = True,
                 profile: str = "douyin", headless: bool = True,
                 timeout_ms: int = 30_000, offscreen: bool = False) -> None:
        self._factory = session_factory or _default_session
        self.guard = guard
        self.dry_run = dry_run
        self.profile = profile
        self.headless = headless
        self.offscreen = offscreen
        self.timeout_ms = timeout_ms
        self._login_cache: dict[str, Any] | None = None

    # ---- 能力 -------------------------------------------------------------
    def capabilities(self) -> dict[str, str]:
        return {
            # 2026-11-07 用户拍板：抖音私聊/群聊/火花全链路删除。原来的 OK 状态与
            # 真机验证记录见 git 历史（08168ca 之前）与 excluded。
            "dm": UNSUPPORTED,
            "group": UNSUPPORTED,
            "streak": UNSUPPORTED,
            "comment": OK,             # 2026-11-05 真机：页面上下文 fetch，服务端回 cid
            "feed": OK,                # 2026-11-05 真机：/hot 热点榜无需登录就有真卡片
            "group_create": UNSUPPORTED,
            "like": EXPERIMENTAL,
        }

    def notes(self) -> dict[str, str]:
        return {
            "dm": "2026-11-07 下线（用户拍板删除私聊/群聊）",
            "group": "2026-11-07 下线（用户拍板删除私聊/群聊）",
            "streak": "2026-11-07 下线（火花依赖私信，一并删除）",
            "comment": "走**页面上下文 fetch**：在视频页里 fetch /aweme/v1/web/comment/publish，服务端返回 {comment:{cid:...}} 即算真发出（2026-11-05 真机 cid=7704571082514711306、7704571348132463396，评论区回读 has_text=true）。前提：账号已验证——人工在有头窗口过一次验证并亲手发一条之后才通；没过验证时服务端回 200+0 字节（静默丢弃），本适配器如实报 unverified，见 excluded#224",
            # 2026-11-05 实测：headless 能开首页（拿到 __ac_signature），但 feed 卡片为 0 ——
            # 是**登录墙**，不是验证页。据此 feed 现在遇 0 卡片抛 empty_feed，不静默 count=0。
            "feed": "走 /hot 热点榜（无需登录，真机 5 条已验）；首页推荐流需要登录态",
            "group_create": "抖音不支持自建群（网页）",
            "like": "点数字会变（787→788）但**刷新后回落**、'我的-喜欢'里也没有 → "
                    "PC 网页点赞不落地，见 excluded#211",
        }

    # ---- 登录 -------------------------------------------------------------
    def login_state(self, *, headless: bool | None = None) -> dict[str, Any]:
        if self._login_cache is not None:
            return self._login_cache
        want_headless = self.headless if headless is None else bool(headless)
        try:
            state = self._with_session(
                lambda s: self._login_from(s), headless=want_headless)
        except PlatformError as exc:
            return {"is_login": False, "reason": exc.message, "code": exc.code}
        self._login_cache = state
        return state

    def _login_from(self, session: Any) -> dict[str, Any]:
        session.goto(HOME)
        session.wait(2500)
        names: set[str] = set()
        try:
            names = set(session.cookie_names())
        except Exception:  # noqa: BLE001
            names = set()
        has_cookie = any(c in names for c in LOGIN_COOKIES)
        # 光看 cookie 会撒谎：2026-11-05 扫码后 sessionid/sid_tt/uid_tt 全在，
        # 但页面右上角仍然渲染出 6 个"登录"按钮，评论区也是 comment-input-**un-login**
        # 容器 —— 抖音前端并不认这个会话。所以再要一个功能信号。
        ui_login = self._login_buttons(session)
        return {"is_login": bool(has_cookie and ui_login == 0),
                "cookies_seen": len(names), "cookie_ok": has_cookie,
                "login_buttons": ui_login,
                "verify_page": _is_verify(session), "url": session.url()}

    @staticmethod
    def _login_buttons(session: Any) -> int:
        """页面上"可见的登录按钮"个数（0 = 前端认为已登录）。拿不到就按 1 处理。"""
        try:
            return int(session.page.evaluate(_LOGIN_BUTTON_JS) or 0)
        except Exception:  # noqa: BLE001
            return 1

    def begin_login(self, *, wait_s: float = 180.0, headless: bool = False) -> dict[str, Any]:
        """打开有头窗口等用户扫码，并把**二维码本身**落到 `data/temp/douyin-qr.png`。

        为什么要人能参与：抖音登录只有扫码/短信，没有可编程口令；而**登录态是
        刷内容/发评论/私信的前置**。扫码一次，之后 profile 目录里就有 cookie 了。

        2026-11-05 加：光开窗口不够——用户看到的是首页，得自己找到"登录"再等弹窗。
        现在主动点开登录弹窗，并把弹窗里的二维码抠成图片返回，人可以直接扫图。
        """
        qr = ""
        shot = ""
        with self._factory(self.profile, headless=headless,
                           timeout_ms=self.timeout_ms) as session:
            session.goto(HOME)
            session.wait(2500)
            try:
                qr = self._grab_login_qr(session)
            except Exception:  # noqa: BLE001 - 抠不到二维码不算致命，还能人工点
                qr = ""
            try:
                shot = session.screenshot(TEMP_ROOT / "douyin-login.png")
            except Exception:  # noqa: BLE001
                shot = ""
            deadline = time.time() + max(15.0, float(wait_s))
            while time.time() < deadline:
                names = set(session.cookie_names())
                if any(c in names for c in LOGIN_COOKIES) \
                        and self._login_buttons(session) == 0:
                    self._login_cache = {"is_login": True, "cookies_seen": len(names)}
                    return {"is_login": True, "qr": qr, "shot": shot, "url": session.url()}
                session.wait(3000)
            names = set(session.cookie_names())
            buttons = self._login_buttons(session)
        return {"is_login": False, "qr": qr, "shot": shot,
                "reason": f"等待超时：cookie {len(names)} 个，页面仍有 {buttons} 个登录按钮",
                "cookies_seen": len(names), "login_buttons": buttons}

    def _grab_login_qr(self, session: Any) -> str:
        """点开"登录"弹窗，把二维码 <img>/<canvas> 抠成 PNG；抠不到返回 ""。

        注意：2026-11-05 实测 headless 下拿到过一个 300x150 **纯黑 canvas**，
        存下来会变成"看似有图其实扫不了"。所以落盘前先校验：至少要像二维码
        （不是纯色、宽高比接近 1:1、尺寸 ≥120）。
        """
        session.page.evaluate(_OPEN_LOGIN_JS)
        session.wait(4000)
        for round_no in range(3):
            data = session.page.evaluate(_GRAB_QR_JS) or {}
            src = data.get("data_url") or data.get("canvas") or ""
            if src.startswith("data:image"):
                try:
                    raw = base64.b64decode(src.split(",", 1)[1])
                except Exception:  # noqa: BLE001
                    raw = b""
                if raw and _looks_like_qr(raw):
                    path = TEMP_ROOT / "douyin-qr.png"
                    path.write_bytes(raw)
                    return str(path)
            session.wait(2500 + round_no * 1500)
        return ""

    # ---- 刷内容 -----------------------------------------------------------
    def feed(self, limit: int = 8, *, keyword: str = "", headless: bool | None = None) -> list[dict[str, Any]]:
        """刷内容，回 [{video_id, url, text}]。

        入口顺序有讲究（2026-11-05 实测）：
        - `/hot` 热点榜 **不需要登录**，DOM 里就有 20 个 `/video/` 链接；
        - 首页 `/`（会跳到 /jingxuan）在**未登录时是空的**（0 卡片，登录墙）。
        所以先试热点榜，再试首页；搜索走搜索页。
        """
        self.require("feed")
        want_headless = self.headless if headless is None else bool(headless)
        urls = [SEARCH.format(kw=keyword)] if keyword else [HOT, HOME]
        errors: list[str] = []
        for url in urls:
            try:
                rows = self._with_session(
                    lambda s, u=url: self._collect(s, u, limit), headless=want_headless)
            except PlatformError as exc:
                errors.append(f"{url} -> {exc.code}")
                if exc.code == "verify_page":
                    raise
                continue
            if rows:
                return rows
        raise PlatformError("empty_feed", "所有入口都没拿到卡片：" + "；".join(errors),
                            hint="可能是风控/登录墙/选择器变了")

    def _collect(self, session: Any, url: str, limit: int) -> list[dict[str, Any]]:
        session.goto(url)
        session.wait(3000)
        if _is_verify(session):
            raise PlatformError(
                "verify_page", "抖音返回了验证中间页（headless 或风控）",
                status=403, hint="用 headless=False 跑，或者先在窗口里人工过验证")
        rows: list[dict[str, Any]] = []
        for round_no in range(3):
            batch = session.page.evaluate(_COLLECT_JS) or []
            have = {r["video_id"] for r in rows}
            for row in batch:
                if row.get("video_id") and row["video_id"] not in have:
                    rows.append(row)
                    have.add(row["video_id"])
            if len(rows) >= limit:
                break
            session.page.evaluate("() => window.scrollBy(0, window.innerHeight * 2)")
            session.wait(1800 + round_no * 400)
        if not rows:
            # 空结果不许静默"成功"：留一张截图，把标题/URL 一起报出去
            shot = ""
            try:
                shot = session.screenshot(TEMP_ROOT / f"douyin-feed-{stamp()}.png")
            except Exception:  # noqa: BLE001 - 截图失败不影响报错
                shot = ""
            raise PlatformError(
                "empty_feed",
                f"页面里没有视频卡片（title={session.title()[:40]!r} url={session.url()[:80]!r}）",
                hint=f"截图：{shot or '无'}；可能是登录墙/验证页/选择器变了",
            )
        return rows[:max(1, int(limit))]

    # ---- 写 ---------------------------------------------------------------
    def like(self, target: Any, *, dry_run: bool | None = None,
             unlike: bool = False, headless: bool | None = None) -> Result:
        """点赞 / 取消点赞。`target` = 视频 id 或完整 URL。

        2026-11-05 实测：视频页有 `[data-e2e="video-player-digg"]`，文字就是赞数；
        点一下数字会变（点赞 +1，再点回 -1），所以**用数字做回查**，不信回执。
        """
        raw = str(target or "").strip()
        if not raw:
            raise PlatformError("empty_target", "点赞需要视频 id 或 URL")
        url = raw if raw.startswith("http") else f"https://www.douyin.com/video/{raw}"
        dry = self.dry_run if dry_run is None else bool(dry_run)
        if dry:
            return Result(True, self.name, "like", {"url": url, "unlike": unlike},
                          dry_run=True, code="dry_run")
        self._check("like", url)
        want_headless = self.headless if headless is None else bool(headless)
        try:
            out = self._with_session(
                lambda s: self._toggle_like(s, url), headless=want_headless)
        except PlatformError as exc:
            self._log("like", url, ok=False, code=exc.code, detail={})
            raise
        self._log("like", url, ok=True, detail={"before": out["before"],
                                               "after": out["after"]})
        return Result(True, self.name, "like", out)

    @staticmethod
    def _read_like(session: Any) -> tuple[str, int]:
        """返回 (原始文字, 数字)。拿不到就给 ('', -1)。"""
        txt = session.page.evaluate(_LIKE_STATE_JS) or ""
        digits = "".join(ch for ch in str(txt) if ch.isdigit())
        if str(txt).endswith("万") and digits:
            return txt, int(float(digits) * 10_000)
        return txt, (int(digits) if digits else -1)

    def _toggle_like(self, session: Any, url: str) -> dict[str, Any]:
        session.goto(url)
        session.wait(5000)
        if _is_verify(session):
            raise PlatformError("verify_page", "抖音返回验证页", status=403)
        before_txt, before = self._read_like(session)
        if before < 0:
            raise PlatformError("selector_missing", "找不到点赞按钮",
                                hint='试过：[data-e2e="video-player-digg"]')
        # 先 JS 点（React 合成事件能吃），数字没变再上真鼠标
        session.page.evaluate(_CLICK_LIKE_JS)
        session.wait(2000)
        after_txt, after = self._read_like(session)
        route = "js"
        if after == before:
            try:
                box = _first(session, ('[data-e2e="video-player-digg"]',))
                if box is not None:
                    box.click(force=True)
                    session.wait(2000)
                    after_txt, after = self._read_like(session)
                    route = "mouse"
            except Exception:  # noqa: BLE001 - 点不动就保持 js 的结果
                pass
        if after == before:
            raise PlatformError(
                "like_failed", f"点赞数没变（{before_txt!r}），可能没生效或被风控",
                hint="JS 点击与真鼠标点击都试过")
        # 立刻变数字 ≠ 落地：2026-11-05 实测点完 787→788，**刷新后又回到 787**，
        # "我的-喜欢"列表里也没有这条。所以必须重载再读一次才算数（excluded#211）。
        session.goto(url)
        session.wait(4000)
        persisted_txt, persisted = self._read_like(session)
        if persisted == before:
            raise PlatformError(
                "like_not_persisted",
                f"点赞只改了界面数字（{before_txt}→{after_txt}），刷新后回到 "
                f"{persisted_txt}，没落地",
                hint="抖音 PC 网页点赞不持久；当前账号/环境只能算 UI 假成功")
        return {"url": url, "before": before, "after": after,
                "persisted": persisted, "delta": persisted - before,
                "route": route, "unliked": persisted < before}

    def comment(self, url: str, text: str, *, dry_run: bool | None = None,
                headless: bool | None = None) -> Result:
        text = _unquote(text)
        if not text:
            raise PlatformError("empty_text", "评论内容为空")
        dry = self.dry_run if dry_run is None else bool(dry_run)
        if dry:
            return Result(True, self.name, "comment", {"url": url, "text": text},
                          dry_run=True, code="dry_run")
        self.require("comment")
        self._check("comment", url)
        want_headless = self.headless if headless is None else bool(headless)
        try:
            out = self._with_session(
                lambda s: self._post_comment(s, url, text), headless=want_headless)
        except PlatformError as exc:
            self._log("comment", url, ok=False, code=exc.code, detail={"text": text})
            raise
        self._log("comment", url, ok=True, detail={"text": text, **out})
        return Result(True, self.name, "comment", {"url": url, "text": text, **out})

    def _post_comment(self, session: Any, url: str, text: str) -> dict[str, Any]:
        """发评论：**页面上下文 fetch**，不看 UI、不点按钮。

        2026-11-05 真机结论（有 cid 回执）：
          - 点 UI 那条路会触发短信验证，而且回读不可靠；
          - 页面里 fetch 同一接口，服务端直接给 `{comment: {cid: ...}}`；
          - 但它有前提：账号已通过验证（人工在有头窗口过一次 + 亲手发一条）。
            没过验证时服务端回 `200 + 0 字节`（静默丢弃），本函数会如实报 `unverified`。
        """
        aweme = _aweme_id(url)
        if not aweme:
            raise PlatformError("bad_url", f"从链接里找不到 aweme_id：{url[:60]}")
        session.goto(f"https://www.douyin.com/video/{aweme}")
        session.wait(3000)
        check = session.page.evaluate(_VERIFY_JS) or {}
        if check.get("verify"):
            raise PlatformError(
                "verify_required",
                "抖音要求人工过一次验证（滑块/短信），自动发评论会被静默丢弃",
                status=403,
                hint="有头窗口打开该视频页，人工过一次验证并亲手发一条评论，之后自动路径即可用")
        out = session.page.evaluate(_PUBLISH_COMMENT_JS, [aweme, text]) or {}
        raw = str(out.get("text") or "")
        info: dict[str, Any] = {"status": out.get("status"), "len": out.get("len")}
        if not raw.strip():
            raise PlatformError(
                "unverified",
                f"publish 接口 HTTP {info['status']}，响应体为空（{info['len']} 字节）——"
                f"账号该端未通过验证时抖音就是这样静默吞掉评论的",
                hint="有头窗口人工过一次验证并亲手发一条，再重试；见 excluded#224")
        cstatus = None
        try:
            payload = json.loads(raw)
            comment = (payload or {}).get("comment") or {}
            cid = str(comment.get("cid") or "")
            cstatus = comment.get("status")
        except Exception:  # noqa: BLE001
            # 响应体可能过长被截断：服务端给了 cid 就算真发出去了，用正则兜底
            m = re.search(r'"cid"\s*:\s*"(\d+)"', raw)
            cid = m.group(1) if m else ""
            if not cid:
                raise PlatformError("unverified", f"响应不是 JSON：{raw[:120]}") from None
        if not cid:
            status_code = (payload or {}).get("status_code")
            raise PlatformError(
                "unverified",
                f"接口没给 cid（status_code={status_code}）：{raw[:160]}",
                hint="见 excluded#224")
        # 服务端给了 cid 才算真发出去了；「有没有在评论区显示」是另一件事（可能审核中）
        visible = self._comment_visible(session, text)
        return {"sent": True, "cid": cid, "chars": len(text),
                "visible": visible, "verified": True,
                "status": cstatus}

    @staticmethod
    def _comment_visible(session: Any, text: str, *, tries: int = 3) -> bool:
        """**评论区列表**里能不能找到这句话。

        注意只能看 `[data-e2e="comment-list"]` 里的文字：2026-11-05 第一版查的是
        `document.body.innerText`，结果把自己刚打进草稿框的字也算进去了，
        于是"没发出去"依然判成功（`verified: true`）。草稿框里的字不算数。
        """
        needle = str(text or "").strip().strip('"')[:14]
        if not needle:
            return False
        js = ("(n) => { const l = document.querySelector('[data-e2e=\"comment-list\"]');"
              " return l ? (l.innerText || '').includes(n) : false; }")
        for _ in range(tries):
            try:
                if session.page.evaluate(js, needle):
                    return True
            except Exception:  # noqa: BLE001
                return False
            session.wait(1500)
        return False

    def dm(self, peer: str, text: str, *, dry_run: bool | None = None,
           headless: bool | None = None) -> Result:
        """私信。2026-11-07 用户拍板：私聊/群聊全链路删除。"""
        raise PlatformError(
            "removed", "抖音私聊已于 2026-11-07 下线（用户拍板删除）")

    def group(self, group_id: str | int, text: str, *, dry_run: bool | None = None,
              headless: bool | None = None) -> Result:
        """群聊。2026-11-07 用户拍板：私聊/群聊全链路删除。"""
        raise PlatformError(
            "removed", "抖音群聊已于 2026-11-07 下线（用户拍板删除）")

    # ---- 续火花（2026-11-07 随私聊一并删除） -------------------------------
    STREAK_LEDGER = Path("data/douyin_streak.json")

    def streak(self, peer: str, text: str = "", *, dry_run: bool | None = None,
               headless: bool | None = None) -> Result:
        raise PlatformError(
            "removed", "抖音火花依赖私信，已于 2026-11-07 一并下线（用户拍板删除）")

    # ---- 内部 -------------------------------------------------------------
    def chat_session(self, fn: Callable[[Any], Any]) -> Any:
        """在**有头**浏览器里跑一段会话内逻辑。2026-11-07：聊天链路已删，保留给 login。"""
        return self._with_session(fn, headless=False)

    def _with_session(self, fn: Callable[[Any], Any], *, headless: bool) -> Any:
        try:
            kwargs: dict[str, Any] = {"headless": headless,
                                      "timeout_ms": self.timeout_ms}
            if self.offscreen:
                # "背后跑"：窗口挪到屏幕外，人看不见但渲染等同有头（/chat 在 headless 会崩）
                kwargs["offscreen"] = True
            with self._factory(self.profile, **kwargs) as session:
                return fn(session)
        except PlatformError:
            raise
        except Exception as exc:  # noqa: BLE001
            code = getattr(exc, "code", "") or "browser_failed"
            if code in ("playwright_missing", "browser_launch_failed"):
                raise PlatformError(OFFLINE, str(getattr(exc, "message", exc))[:300],
                                    status=503) from exc
            raise PlatformError(code, str(exc)[:300]) from exc

    def _check(self, action: str, key: str) -> None:
        if self.guard is not None:
            self.guard.check(self.name, action, key)

    def _log(self, action: str, key: str, *, ok: bool, code: str = "",
             detail: dict[str, Any] | None = None) -> None:
        if self.guard is None:
            return
        self.guard.log({"platform": self.name, "action": action, "key": key,
                        "ok": ok, "code": code, "detail": detail or {}})


# 风控提示词。借鉴 Xiaowu-0916/douyin-spark：它在续火花时一旦看到"操作频繁/安全验证"
# 就整轮中止，避免在被限流的状态下继续发（继续发只会让账号更凉）。
BLOCK_HINTS = ("操作频繁", "安全验证", "访问频繁", "请稍后再试", "操作过于频繁")


def _raise_if_blocked(session: Any) -> None:
    """页面上出现风控提示就抛 `rate_limited_security`，让调用方立刻停手。"""
    try:
        body = session.page.evaluate(
            "() => (document.body.innerText || '').slice(0, 4000)") or ""
    except Exception:  # noqa: BLE001 - 页面没了就别在这儿报错，交给上层
        return
    for hint in BLOCK_HINTS:
        if hint in body:
            raise PlatformError("rate_limited_security",
                                f"页面出现「{hint}」，本轮停止以免加重风控",
                                status=429,
                                hint="隔几小时再试；或先在 App 里手动发一条确认账号正常")


def _first(session: Any, selectors: tuple[str, ...]) -> Any:
    """按顺序试选择器，返回第一个命中的元素（找不到返回 None，不抛）。"""
    for sel in selectors:
        try:
            el = session.page.query_selector(sel)
        except Exception:  # noqa: BLE001 - 非法选择器就换下一个
            el = None
        if el is not None:
            return el
    return None


def _default_session(profile: str, **kwargs: Any) -> Any:
    from aiji.left_hand import browser
    return browser.session(profile, **kwargs)


def video_id_of(url: str) -> str:
    m = re.search(r"/video/(\d{6,})", str(url))
    return m.group(1) if m else ""


def _unquote(text: str) -> str:
    """去掉整条被引号包住的情况。

    2026-11-05 真机：命令行 `--text "你好呀…"` 经过 cmd/pwsh 两层引号后，**引号本身进了
    消息正文**（群里真出现 `"你好呀，我是小肥鱼～"`，还把 chars 从 10 变成 12）。
    只处理"整条被同一种引号包住"的情况，不动句中的引号。
    """
    s = str(text or "").strip()
    for q in ('"', "'", "“", "”"):
        if len(s) > 2 and s.startswith(q) and s.endswith(q):
            inner = s[1:-1].strip()
            if inner and q not in inner:
                return inner
    return s


def learn_blob(rows: list[dict[str, Any]], *, topic: str = "") -> str:
    """把一批刷到的视频压成"学习文本"（走同一条记忆抽取管线）。"""
    if not rows:
        return ""
    head = f"在抖音刷到这些视频（{topic or '推荐'}）：" if topic else "在抖音刷到这些视频："
    lines = [head]
    for i, row in enumerate(rows, 1):
        text = re.sub(r"\s+", " ", str(row.get("text") or "")).strip()
        lines.append(f"{i}. {text or row.get('url', '')}")
    return "\n".join(lines)
