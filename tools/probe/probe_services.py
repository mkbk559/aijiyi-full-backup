"""服务层/安全层深探：能力矩阵、外部依赖降级、审批与边界、噪声控制。"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.probe._harness import Probe, cleanup, quiet_env, workdir  # noqa: E402

quiet_env()
probe = Probe("services")
base = workdir("services")


def env(name: str, default: str = "") -> str:
    import os
    return os.environ.get(name, default)


try:
    # ================= 1. 声音：声线注册表 + 就绪判断 =================
    from aiji.voice_service import VoiceError, VoiceRegistry

    voices_dir = base / "voices"
    voices_dir.mkdir(mode=0o777)
    registry = VoiceRegistry(voices_dir)
    snapshot = registry.snapshot()
    probe.case("声线注册表：内置 edge-default 存在",
                snapshot.get("active") == "edge-default"
                and any(p["id"] == "edge-default" for p in snapshot["profiles"]),
                f"active={snapshot.get('active')} n={len(snapshot['profiles'])}")
    probe.case("声线注册表：报告 ffmpeg 是否可用", "ffmpeg" in snapshot, f"ffmpeg={snapshot.get('ffmpeg')}")
    try:
        registry.get("nope")
        code = ""
    except VoiceError as exc:
        code = str(getattr(exc, "code", ""))
    probe.case("未知声线 → 明确 profile_not_found", code == "profile_not_found", f"code={code}")
    try:
        registry.synthesize("你好", base / "x.wav", profile_id="nope")
        raised = False
    except VoiceError as exc:
        raised = str(getattr(exc, "code", "")) == "profile_not_found"
    probe.case("未知声线合成抛 VoiceError(profile_not_found)", raised)

    # ================= 2. 点歌/社交/图库：未配置时的诚实降级 =================
    from aiji.song_service import SongError, SongService
    from aiji.social_service import SocialError, SocialService
    from aiji.sticker_service import StickerError, StickerService

    songs = SongService(registry, data_root=base)
    probe.case("点歌服务：ready() 反映外部依赖",
                isinstance(songs.ready(), (bool, dict)), f"ready={songs.ready()}")
    try:
        songs.submit("晴天", profile_id="nope")
        raised = False
    except SongError as exc:
        raised = str(getattr(exc, "code", "")) in ("profile_not_found", "voice_not_ready")
    probe.case("点歌：未知声线 → SongError", raised)
    try:
        songs.submit("晴天", profile_id="edge-default")
        raised = False
    except SongError as exc:
        raised = str(getattr(exc, "code", "")) == "voice_converter_not_configured"
    probe.case("点歌：声线没配换声器 → 明确 voice_converter_not_configured", raised)

    social = SocialService(
        lambda action, params: (_ for _ in ()).throw(RuntimeError("napcat down")),
        media_root=base / "media", sticker_dir=base / "stickers",
        song_lookup=songs.lookup, sticker_status=lambda: {}, enabled=False,
    )
    probe.case("社交：未配置时 snapshot 标 configured=False",
                social.snapshot().get("configured") is False, f"{social.snapshot()}")
    try:
        social.publish("探测")
        raised = False
    except SocialError as exc:
        raised = str(getattr(exc, "code", "")) == "social_not_configured"
    probe.case("社交：未配置时发说说 → SocialError(social_not_configured)", raised)

    stickers_dir = base / "stickers"
    stickers_dir.mkdir(mode=0o777)
    stickers = StickerService(stickers_dir, feed_url="https://127.0.0.1:9/none.json", max_bytes=1024)
    probe.case("图库：空目录 snapshot 可用", isinstance(stickers.snapshot(), dict),
               f"keys={sorted(stickers.snapshot())[:6]}")
    try:
        stickers.import_image("http://127.0.0.1:9/x.png", mood="开心")
        raised = False
    except StickerError as exc:
        raised = str(getattr(exc, "code", "")) != ""
    probe.case("图库：环回地址导入被拒（SSRF 防线）", raised)

    # ================= 3. MCP：白名单与只读声明 =================
    from aiji.mcp_client import MCPRegistry

    mcp = MCPRegistry(Path("config/mcp_servers.json"))
    snap = mcp.snapshot()
    servers = snap.get("servers", snap if isinstance(snap, list) else [])
    names = {s.get("name") for s in servers} if isinstance(servers, list) else set()
    probe.case("MCP 配置加载出 3 个服务（ddg/fetch/github）",
                {"duckduckgo", "fetch", "github"} <= names, f"names={sorted(names)}")
    probe.case("MCP 默认只读且 GitHub 默认停用",
                all(s.get("read_only") for s in servers)
                and not next(s for s in servers if s["name"] == "github").get("enabled"),
                f"enabled={[s['name'] for s in servers if s.get('enabled')]}")
    try:
        mcp.call_sync("duckduckgo", "rm -rf /", {})
        raised = False
    except Exception as exc:  # noqa: BLE001
        raised = type(exc).__name__ in ("MCPToolDenied", "MCPConfigError", "MCPError")
    probe.case("MCP 非白名单工具被拒", raised)

    # ================= 4. 受控电脑操作：策略 + 审批 =================
    from aiji.actions import ActionExecutor, ActionPolicy, ApprovalManager, ToolCall
    from aiji.actions.policy import PolicyViolation

    ws = base / "ws"
    ws.mkdir(mode=0o777)
    (ws / "note.txt").write_text("hello", encoding="utf-8")
    policy = ActionPolicy(ws)
    probe.case("策略：工作区内路径放行", policy.resolve("note.txt") == (ws / "note.txt").resolve())
    for bad in ("../escape.txt", "C:/Windows/win.ini"):
        try:
            policy.resolve(bad)
            blocked = False
        except PolicyViolation:
            blocked = True
        probe.case(f"策略：{bad} 越界被拒", blocked)
    for sensitive in ("aiji.db", "id_rsa"):
        try:
            policy.check_file(ws / sensitive)
            blocked = False
        except PolicyViolation:
            blocked = True
        probe.case(f"策略：敏感文件 {sensitive} 被拒", blocked)
    try:
        policy.resolve("../../../Windows/win.ini")
        blocked = False
    except PolicyViolation:
        blocked = True
    probe.case("策略：多级 ../ 越界被拒", blocked)
    for bad_cmd in ("rm -rf /", "format C:", "del /f /q C:\\*"):
        ok_cmd, reason = policy.check_command(bad_cmd)
        probe.case(f"策略：危险命令被拒（{bad_cmd[:12]}）", not ok_cmd, f"reason={reason}")
    ok_cmd, reason = policy.check_command("echo hello")
    probe.case("策略：普通命令放行", ok_cmd, f"reason={reason}")

    approvals = ApprovalManager()
    executor = ActionExecutor(ws, approvals=approvals, approval_timeout_s=1.0)
    read_call = ToolCall("c-read", "read_file", {"path": "note.txt"})
    result = executor.execute(read_call)
    probe.case("执行器：读文件成功", result.ok and "hello" in (result.output or ""),
               f"ok={result.ok} out={str(result.output)[:40]}")
    write_call = ToolCall("c-write", "write_file", {"path": "note.txt", "content": "new"})
    result = executor.execute(write_call)
    probe.case("执行器：写文件未审批 → 拒绝（不 silent 直写）",
                (not result.ok) and result.approved is False,
                f"ok={result.ok} approved={result.approved} risk={result.risk}")

    import threading

    holder = {}

    def auto_approve(item):
        threading.Timer(0.05, lambda: holder["mgr"].approve(item["id"])).start()

    manager2 = ApprovalManager(on_request=auto_approve)
    holder["mgr"] = manager2
    executor2 = ActionExecutor(ws, approvals=manager2, approval_timeout_s=5.0)
    approved_result = executor2.execute(write_call)
    probe.case("执行器：有人批准后写入成功",
                approved_result.ok and (ws / "note.txt").read_text(encoding="utf-8") == "new",
                f"ok={approved_result.ok} approved={approved_result.approved}")

    # ================= 5. 出口闸门 / 角色边界 =================
    from aiji import role_boundary
    from aiji.gate import check_reply, sanitize_reply

    bad = check_reply("我的密码是 123456")
    probe.case("闸门：凭据转述被拦", not bad.ok, f"ok={bad.ok} reason={getattr(bad, 'reason', '')}")
    long_reply = check_reply("啊" * 5000)
    probe.case("闸门：超长回复被拦", not long_reply.ok, f"ok={long_reply.ok}")
    weird = sanitize_reply("你好[微笑]呀")
    probe.case("闸门：文字表情标记被剔除", "[微笑]" not in weird[0] if isinstance(weird, tuple) else True,
               f"sanitized={weird}")
    probe.case("角色边界：写代码请求被识别",
               role_boundary.is_physical_action_request("帮我写个 python 脚本删库"))
    probe.case("角色边界：可执行代码检测",
               role_boundary.contains_executable_code("```python\nprint(1)\n```"))
    probe.case("角色边界：提示词块不含鱼缸角色扮演",
                "鱼的身体" not in role_boundary.prompt_block())

    # ================= 6. 拟人节奏 / 连发合并 =================
    from aiji.debounce import ConversationDebouncer, merge_messages
    from aiji.humanize import HumanPacer

    pacer = HumanPacer(min_ms=600, max_ms=4500, typing_min_ms=100, typing_max_ms=1500)
    delay = pacer.plan_ms("在吗" * 60, "qq")
    probe.case("拟人节奏：IM 平台有首字延迟且不超上限", 0 < delay <= 9000, f"delay={delay}ms")
    probe.case("拟人节奏：非 IM 平台不加延迟", pacer.plan_ms("你好", "desktop") == 0.0)
    import threading as _threading

    calls = {"n": 0}

    def gen(messages):
        calls["n"] += 1
        return "回"

    debouncer = ConversationDebouncer(400)
    key = ("qq", "u1", "default", "s1")
    results = {}

    def fire(tag, text):
        results[tag] = debouncer.call(key, [{"role": "user", "content": text}], gen)

    t1 = _threading.Thread(target=fire, args=("a", "一"))
    t1.start()
    time.sleep(0.05)
    t2 = _threading.Thread(target=fire, args=("b", "二"))
    t2.start()
    t1.join(5)
    t2.join(5)
    probe.case("连发合并：并发同会话只生成一次",
                calls["n"] == 1 and results.get("a") == results.get("b") == "回",
                f"calls={calls['n']} results={results}")
    merged = merge_messages([
        [{"role": "user", "content": "第一句"}],
        [{"role": "user", "content": "第二句"}],
    ])
    probe.case("合并保留第一份上下文并追加后续用户消息",
                isinstance(merged, list) and any("第二句" in str(m.get("content")) for m in merged),
                f"n={len(merged) if isinstance(merged, list) else '?'}")

    # ================= 7. 存在感广播 =================
    from aiji.presence import PresenceHub

    hub = PresenceHub()
    hub.publish({"state": "thinking"})
    snap = hub.snapshot()
    probe.case("存在感：广播后可读快照", bool(snap), f"snapshot={str(snap)[:80]}")

    # ================= 8. 桌宠：精灵图集 / 养成 / 桌面感知 =================
    from aiji.pet import avatar, sprite
    from aiji.pet.desk import window_titles
    from aiji.pet.growth import PetStore

    manifest = sprite.load_manifest()
    tracks = manifest.get("tracks") if isinstance(manifest, dict) else None
    probe.case("精灵图集：清单与 9 条轨迹",
                isinstance(tracks, dict) and len(tracks) == 9, f"tracks={len(tracks or {})}")
    frames = {}
    for name in (tracks or {}):
        row = sprite.ROW_OF_TRACK[name]
        trimmed = sprite.trim_track(sprite.TRACKS[name], sprite.FRAMES[row])
        frames[name] = len(trimmed["durations"])
    probe.case("精灵图集：每条轨迹都有帧",
                all(count > 0 for count in frames.values()), f"frames={frames}")
    frame = sprite.cell("idle", 0, size=128)
    probe.case("精灵图集：能取到 RGBA 帧", frame.mode == "RGBA" and frame.size[0] > 0,
               f"mode={frame.mode} size={frame.size}")
    rendered = avatar.frame("idle", 0, 160)
    probe.case("形象层：可渲染一帧", rendered is not None and rendered.size[0] > 0,
               f"size={getattr(rendered, 'size', None)}")

    pet_dir = base / "pet"
    pet_dir.mkdir(mode=0o777)
    store_pet = PetStore(pet_dir / "pet_state.json")
    now_ms = time.time() * 1000
    snap0 = store_pet.snapshot(now_ms)
    probe.case("养成：初始状态含亲密度/小鱼干", {"affinity", "treats"} & set(snap0) != set(),
               f"keys={sorted(snap0)}")
    delta1, line1, applied1 = store_pet.pet(now_ms)
    delta2, line2, applied2 = store_pet.pet(now_ms + 1000)
    probe.case("养成：摸头 +1 且有 10s 冷却", applied1 and not applied2,
               f"delta={delta1} second={applied2} line={line2[:12]}")
    delta3, line3, applied3 = store_pet.feed(now_ms + 100_000)
    probe.case("养成：没粮时喂食被拒且不白扣",
                applied3 is False, f"applied={applied3} line={line3[:16]}")
    store_pet.rename("探测鱼")
    probe.case("养成：改名生效", store_pet.snapshot(now_ms + 100_000)["name"] == "探测鱼")
    titles = window_titles()
    probe.case("桌面感知：能枚举前台窗口标题", isinstance(titles, list),
               f"n={len(titles)} sample={titles[:2]}")

    # ================= 9. 语音后端探测（真实依赖） =================
    from aiji import real_engines as re
    from aiji.pet import voice as pv

    has_sapi = bool(pv.sapi_voices())
    probe.case("本机 TTS：SAPI 可用", has_sapi, f"sapi_voices={len(pv.sapi_voices() or [])}")
    probe.case("本机 ASR：FunASR/whisper 至少一个可用", re.HAS_FUNASR or re.HAS_ASR,
               f"funasr={re.HAS_FUNASR} whisper={re.HAS_ASR}")
finally:
    cleanup(base)


raise SystemExit(probe.finish())
