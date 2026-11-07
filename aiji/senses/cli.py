"""命令行入口：serve / remember / recall / media / accounts / stats / doctor / export / bench。"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sqlite3
import sys
import time
from pathlib import Path

from aiji import __version__
from aiji.right_hand.config import Settings
from aiji.hippocampus.memory import Memory
from aiji.brain.quota import sends_last_24h
from aiji.hippocampus.store import Ident
from aiji.skeleton.paths import BENCH
from aiji.senses.video_ingest import (
    VideoIngestError,
    build_learn_text,
    fetch_video_info,
    learning_plan,
    listen_video,
    look_video,
)


def _memory(args: argparse.Namespace) -> Memory:
    return Memory.open(args.db, getattr(args, "config", None))


def _ident(args: argparse.Namespace) -> Ident:
    return Ident(
        getattr(args, "platform", None) or os.environ.get("AIJI_PLATFORM") or "local",
        getattr(args, "user", None) or "default",
        getattr(args, "scope", None) or "default",
    )


def _add_identity(p: argparse.ArgumentParser) -> None:
    p.add_argument("--platform", default=None, help="平台，如 wechat / douyin / cli（默认 local）")
    p.add_argument("--user", dest="user", default=None, help="平台内用户 id（默认 default）")
    p.add_argument("--scope", default=None, help="记忆域，如 personal / work（默认 default）")


def cmd_learn_video(args: argparse.Namespace) -> int:
    """看视频学习：抓元数据（+字幕计划）→ 走同一条记忆抽取管线。

    --listen N：把视频音轨的前 N 分钟转写出来（FunASR）一起进记忆——
    B 站绝大多数视频没有 CC 字幕（探针 100056/100018 实测），"听"是通用路线。
    """
    m = _memory(args)
    try:
        try:
            info = fetch_video_info(
                " ".join(args.url),
                timeout_s=args.timeout,
                cookies_from_browser=args.cookies_from_browser,
            )
        except VideoIngestError as exc:
            print(json.dumps({"ok": False, "code": exc.code, "error": str(exc)},
                             ensure_ascii=False), file=sys.stderr)
            return 2
        plan = learning_plan(info)
        text = build_learn_text(info)
        extra = {
            "video_info": info.to_dict(),
            "plan_mode": plan["mode"],
        }
        heard = None
        saw = None
        if args.look > 0:
            try:
                saw_text, saw_conf = look_video(
                    " ".join(args.url), frames=args.look,
                    timeout_s=args.timeout * 4,
                    cookies_from_browser=args.cookies_from_browser,
                )
            except VideoIngestError as exc:
                saw = {"error": exc.code, "detail": str(exc)}
            except Exception as exc:  # noqa: BLE001 - OCR 引擎缺依赖时降级
                saw = {"error": "ocr_failed", "detail": str(exc)[:200]}
            else:
                saw = {"frames": args.look, "confidence": round(saw_conf, 2),
                       "chars": len(saw_text)}
                if saw_text:
                    text = text + "\n\n画面里看到的字：\n" + saw_text
                    extra["saw"] = saw
        if args.listen > 0:
            try:
                heard_text, conf = listen_video(
                    " ".join(args.url), seconds=args.listen * 60.0,
                    timeout_s=args.timeout * 4,
                    cookies_from_browser=args.cookies_from_browser,
                )
            except VideoIngestError as exc:
                print(json.dumps({"ok": False, "code": exc.code,
                                  "error": f"音频路线失败：{exc}"},
                                 ensure_ascii=False), file=sys.stderr)
                # 元数据已经拿到了：音频失败降级为只学元数据，不算整条失败
                heard = {"error": exc.code, "detail": str(exc)}
            except Exception as exc:  # noqa: BLE001 - ASR 引擎缺依赖时降级
                heard = {"error": "asr_failed", "detail": str(exc)[:200]}
            else:
                heard = {"seconds_planned": args.listen * 60,
                         "confidence": round(conf, 2),
                         "chars": len(heard_text)}
                if heard_text:
                    text = text + "\n\n听到的内容：\n" + heard_text
                    extra["heard"] = heard
        ident = _ident(args)
        m.ingest(ident, kind="video", ref=info.video_id or info.webpage_url,
                 text=text[:4000], url=info.webpage_url,
                 session=args.session, extra=extra)
        ok = m.flush(args.flush_timeout)
        print(json.dumps({
            "ok": True, "flushed": ok,
            "video": info.to_dict(),
            "plan": plan,
            "heard": heard,
            "saw": saw,
            "text_chars": len(text),
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        m.close()


def _bili_cred(args):
    """按 --cookie-file > --cookies-from-browser 的顺序拿凭据。"""
    from aiji.senses import bili_client
    if getattr(args, "cookie_file", ""):
        return bili_client.parse_cookie_jar(args.cookie_file)
    if getattr(args, "cookies_from_browser", ""):
        return bili_client.credentials_from_browser(
            args.cookies_from_browser,
            " ".join(getattr(args, "url", ["https://www.bilibili.com"])))
    raise bili_client.BiliError(
        "no_cookie_source",
        "需要 --cookie-file <jar> 或 --cookies-from-browser edge/chrome "
        "（先在浏览器里登录 B 站）")


def cmd_bili_login(args: argparse.Namespace) -> int:
    """查 B 站登录态（玛卡巴卡的账号是否可用）。无副作用。"""
    from aiji.senses import bili_client
    try:
        cred = _bili_cred(args)
        info = bili_client.check_login(cred)
    except bili_client.BiliError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "error": str(exc)},
                         ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({"ok": True, **info}, ensure_ascii=False, indent=2))
    return 0 if info.get("is_login") else 3


def cmd_bili_comment(args: argparse.Namespace) -> int:
    """B 站评论。默认 dry-run；--yes 才真发（限速 60s + 留痕）。

    --persona：先按玛卡巴卡（六七岁小孩）口吻改写再发；dry-run 会展示改写结果。
    """
    from aiji.senses import bili_client
    url = " ".join(args.url)
    message = args.message
    restyled = None
    if getattr(args, "persona", False):
        # 措辞改写优先走运行中的服务（它有配置好的路由），不需要 B 站登录态。
        try:
            message, source = bili_client.restyle_comment(
                message, timeout_s=getattr(args, "style_timeout", 60.0))
            restyled = {"source": source, "text": message}
        except bili_client.BiliError:
            raise
    cred = None
    try:
        if args.yes or getattr(args, "cookie_file", "") or getattr(args, "cookies_from_browser", ""):
            cred = _bili_cred(args)
        else:
            # dry-run 且没给 cookie 源：预览改写结果不需要登录态
            cred = bili_client.BiliCredentials()
        aid = bili_client.resolve_aid(url)
        result = bili_client.post_comment(
            aid, message, cred, confirm=bool(args.yes),
            state_dir=pathlib.Path(getattr(args, "state_dir", "data")))
    except bili_client.BiliError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "error": str(exc),
                          "restyled": restyled},
                         ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({
        "ok": result.ok, "dry_run": result.dry_run, "aid": aid,
        "rpid": result.rpid, "message": result.message,
        "restyled": restyled,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_bili_upload(args: argparse.Namespace) -> int:
    """B 站投稿（发视频）。默认 dry-run（本地预检，无需登录态）；--yes 才真传。"""
    from aiji.senses import bili_client
    cred = None
    if args.yes:
        try:
            cred = _bili_cred(args)
        except bili_client.BiliError as exc:
            print(json.dumps({"ok": False, "code": exc.code, "error": str(exc)},
                             ensure_ascii=False, indent=2))
            return 2
    elif not (getattr(args, "cookie_file", "") or getattr(args, "cookies_from_browser", "")):
        # dry-run 且没给 cookie 源：直接本地预检（登录态只在真传时需要）
        cred = bili_client.BiliCredentials()  # 不可用占位；upload 对 dry-run 不查
    else:
        try:
            cred = _bili_cred(args)
        except bili_client.BiliError:
            cred = bili_client.BiliCredentials()
    try:
        result = bili_client.upload_video(
            args.file, args.title, desc=args.desc, cred=cred,
            confirm=bool(args.yes),
            state_dir=pathlib.Path(getattr(args, "state_dir", "data")),
            timeout_s=args.timeout,
        )
    except bili_client.BiliError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "error": str(exc)},
                         ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({
        "ok": result.ok, "dry_run": result.dry_run, "aid": result.aid,
        "message": result.message,
    }, ensure_ascii=False, indent=2))
    return 0


def _heal_dead_proxy() -> None:
    """本机常年挂着 Clash 一类代理（HTTP(S)_PROXY=127.0.0.1:7897）。

    代理进程死了但环境变量还挂着时，urllib 会把**所有**非 localhost 请求交给一个
    拒绝连接的端口——模型池的公网路由（zhipu/siliconflow/openrouter/pollinations）
    全灭，只剩本机两个反代，表现就是"玛卡巴卡老回兜底话"（2026-11-04 实测）。
    启动时探一次代理端口：拒绝就清掉本进程四件套（子进程也干净）；活着就尊重。
    """
    raw = (os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or "").strip()
    if not raw:
        return
    import socket
    import urllib.parse

    url = raw if "://" in raw else f"http://{raw}"
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 7897
    except ValueError:
        return
    try:
        with socket.create_connection((host, port), timeout=0.8):
            return  # 代理活着：尊重用户环境
    except OSError:
        pass
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ.pop(key, None)
    print(f"proxy {raw} unreachable; cleared HTTP(S)_PROXY for this process",
          file=sys.stderr)


def cmd_serve(args: argparse.Namespace) -> int:
    from aiji.endocrine.presence import PresenceServer
    from aiji.throat.server import serve

    _heal_dead_proxy()
    m = _memory(args)
    presence = None
    try:
        if not args.no_presence:
            try:
                presence = PresenceServer(
                    m.presence, args.host, args.presence_port,
                ).start()
                print(f"presence ws://{args.host}:{presence.port}")
            except OSError as exc:
                print(f"presence disabled: {exc}", file=sys.stderr)
        serve(m, args.host, args.port)
    finally:
        if presence is not None:
            presence.stop()
        m.close()
    return 0


def cmd_remember(args: argparse.Namespace) -> int:
    m = _memory(args)
    try:
        depth = m.remember(" ".join(args.text), _ident(args), session=args.session, extra=args.extra)
        ok = m.flush(args.timeout)
        print(json.dumps({"queued": True, "queue_depth": depth, "flushed": ok,
                          "facts": m.stats()["store"]["facts"]}, ensure_ascii=False))
    finally:
        m.close()
    return 0


def cmd_recall(args: argparse.Namespace) -> int:
    m = _memory(args)
    try:
        res = m.recall(" ".join(args.text), _ident(args), session=args.session,
                       top_k=args.top_k, budget_ms=args.budget_ms)
        print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
    finally:
        m.close()
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    m = _memory(args)
    try:
        depth = m.ingest(
            _ident(args), kind=args.kind, ref=args.ref or "", text=args.text,
            url=args.url, mime=args.mime, size_bytes=args.size,
            session=args.session, extra=args.extra,
        )
        ok = m.flush(args.timeout)
        media = m.media(_ident(args), args.session, 3)
        print(json.dumps({"queued": True, "queue_depth": depth, "flushed": ok,
                          "latest": media[0] if media else None},
                         ensure_ascii=False, indent=2))
    finally:
        m.close()
    return 0


def cmd_inject(args: argparse.Namespace) -> int:
    m = _memory(args)
    try:
        inj = m.inject(" ".join(args.query), _ident(args), session=args.session,
                       max_chars=args.max_chars, max_items=args.max_items)
        print(json.dumps(inj.to_dict(), ensure_ascii=False, indent=2))
    finally:
        m.close()
    return 0


def cmd_feedback(args: argparse.Namespace) -> int:
    m = _memory(args)
    try:
        n = m.feedback(_ident(args), args.injection_id, args.used,
                       helpful=not args.not_helpful, note=args.note or "")
        print(json.dumps({"updated": n}, ensure_ascii=False))
    finally:
        m.close()
    return 0


def cmd_style(args: argparse.Namespace) -> int:
    m = _memory(args)
    try:
        print(json.dumps({"style": m.style(_ident(args)),
                          "summary": m.summary(_ident(args), args.session),
                          "profile": m.profile(_ident(args))},
                         ensure_ascii=False, indent=2))
    finally:
        m.close()
    return 0


def cmd_media(args: argparse.Namespace) -> int:
    m = _memory(args)
    try:
        ident = _ident(args)
        if args.ref:
            mid = m.add_media(ident, kind=args.kind, ref=args.ref, url=args.url,
                              session=args.session, mime=args.mime)
            print(json.dumps({"id": mid, "stored": "reference_only"}, ensure_ascii=False))
        else:
            print(json.dumps(m.media(ident, args.session, args.limit), ensure_ascii=False, indent=2))
    finally:
        m.close()
    return 0


def cmd_media_gc(args: argparse.Namespace) -> int:
    """清掉"没有任何文字结论"的失败媒体行。

    只删 `status in (unsupported/error/timeout/empty)` **且** `text` 为空的：
    这些行本来就喂不进抽取层，留着只是行数（2026-11-03 体检：7 668 行里
    5 690 行非 ok，其中 unsupported/error/timeout 共 2 007 行基本都没文字）。
    `low_confidence` 有文字、只是不可信，一律保留。
    """
    m = _memory(args)
    try:
        db_path = m.settings.db_path
        db = sqlite3.connect(str(db_path))
        try:
            rows = db.execute(
                "SELECT status, COUNT(*) FROM media GROUP BY status ORDER BY 2 DESC"
            ).fetchall()
            before = {str(k): int(v) for k, v in rows}
            dead = ("unsupported", "error", "timeout", "empty")
            marks = ",".join("?" * len(dead))
            n = db.execute(
                f"SELECT COUNT(*) FROM media WHERE status IN ({marks})"
                " AND (text IS NULL OR TRIM(text) = '')", dead).fetchone()[0]
            report = {"db": str(db_path), "before": before, "deletable": int(n),
                      "applied": False}
            if args.apply and n:
                db.execute(
                    f"DELETE FROM media WHERE status IN ({marks})"
                    " AND (text IS NULL OR TRIM(text) = '')", dead)
                db.commit()
                db.execute("VACUUM")
                after = db.execute(
                    "SELECT status, COUNT(*) FROM media GROUP BY status ORDER BY 2 DESC"
                ).fetchall()
                report["applied"] = True
                report["deleted"] = int(n)
                report["after"] = {str(k): int(v) for k, v in after}
            else:
                report["hint"] = "加 --apply 才真的删"
            print(json.dumps(report, ensure_ascii=False, indent=2))
        finally:
            db.close()
    finally:
        m.close()
    return 0


def cmd_accounts(args: argparse.Namespace) -> int:
    m = _memory(args)
    try:
        print(json.dumps(m.accounts(), ensure_ascii=False, indent=2))
    finally:
        m.close()
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    m = _memory(args)
    try:
        print(json.dumps(m.stats(), ensure_ascii=False, indent=2))
    finally:
        m.close()
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    s = Settings.load(args.config)
    if args.db:
        s.db_path = Path(args.db)
    m = Memory.open(s.db_path, args.config)
    try:
        st = m.stats()
        ready = [r["name"] for r in st["pool"]]
        print(json.dumps(
            {
                "version": __version__,
                "python": sys.version.split()[0],
                "settings": s.describe(),
                "db": st["db"],
                "store": st["store"],
                "routes_ready": ready,
                "hint": None if ready else (
                    "没有可用模型路由：抽取会走本地规则兜底（仍能存事实/画像）。"
                    "要启用免费模型池，设置 ZHIPU_API_KEY / SILICONFLOW_API_KEY / OPENROUTER_API_KEY "
                    "等环境变量，或用 --config 指定带 api_key 的路由文件。"
                ),
                "sqlite": sqlite3.sqlite_version,
            },
            ensure_ascii=False,
            indent=2,
        ))
    finally:
        m.close()
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    m = _memory(args)
    try:
        ident = _ident(args)
        w, wargs = ident.where()
        rows = [dict(r) for r in m.db.read(f"SELECT * FROM fact WHERE {w} AND status='active'", tuple(wargs))]
        text = json.dumps(rows, ensure_ascii=False, indent=2)
        if args.out:
            Path(args.out).write_text(text, "utf-8")
            print(f"wrote {len(rows)} facts -> {args.out}")
        else:
            print(text)
    finally:
        m.close()
    return 0


def cmd_forget(args: argparse.Namespace) -> int:
    """显式遗忘：--id 12 或 --like 旧猜测。不可见 + 留审计（不改物理行）。"""
    m = _memory(args)
    try:
        ident = _ident(args)
        res = m.forget(ident, fact_id=args.id, like=args.like or "")
        print(json.dumps(res, ensure_ascii=False))
    finally:
        m.close()
    return 0


def cmd_archive(args: argparse.Namespace) -> int:
    """回查被压缩淘汰的原文（退役不删，默认留 90 天）。"""
    m = _memory(args)
    try:
        rows = m.archived_turns(_ident(args), args.session, limit=args.limit)
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    finally:
        m.close()
    return 0


def _purge_tmp_workdirs(older_than_s: float = 3600.0) -> dict[str, float]:
    """删掉测试留下的 `.tmp/test_*` 工作目录。

    2026-11-05 实测：跑一遍全量测试会留下 ~750MB 的 `.tmp/test_*`（最狠的
    `test_property` 一个就 235MB），堆几次就把盘吃满。测试自己不清理，
    就由 janitor 兜底，只删超过 1 小时的（不打断正在跑的测试）。
    """
    import shutil
    import time as _t
    from pathlib import Path as _P

    root = _P(".tmp")
    freed = 0.0
    killed = 0
    if not root.exists():
        return {"files": 0, "mb": 0.0}
    cutoff = _t.time() - older_than_s
    for d in root.glob("test_*"):
        if not d.is_dir():
            continue
        try:
            if d.stat().st_mtime > cutoff:
                continue
            size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            shutil.rmtree(d, ignore_errors=True)
            freed += size / (1024 * 1024)
            killed += 1
        except Exception:  # noqa: BLE001
            pass
    return {"dirs": killed, "mb": round(freed, 1)}


def cmd_janitor(args: argparse.Namespace) -> int:
    """手动跑一轮代谢清理（冻结/去重/边封顶/归档清除/WAL 截断）。

    服务端后台线程跑的是同一套（AIJI_JANITOR 门）；这个命令用于
    运维验证与证据采集——输出 run() 的统计 dict。
    """
    from aiji.hippocampus.janitor import Janitor
    m = _memory(args)
    try:
        j = Janitor(m.db)
        summary = j.run()
        summary["tmp_workdirs"] = _purge_tmp_workdirs()
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        m.close()
    return 0


def cmd_self_memory(args: argparse.Namespace) -> int:
    """自我记忆（她自己的事）：不带参数=列出；带 --add=写一条。"""
    m = _memory(args)
    try:
        if args.add:
            sid = m.remember_self(args.add, kind=args.kind, scope=args.scope)
            print(json.dumps({"id": sid, "text": args.add}, ensure_ascii=False))
        else:
            rows = m.self_memory(scope=args.scope or None, limit=args.limit)
            print(json.dumps(rows, ensure_ascii=False, indent=2))
    finally:
        m.close()
    return 0


def cmd_quota(args: argparse.Namespace) -> int:
    """主动配额体检：剩下多少积分、单条多贵、今天还能主动几次、已经用了几次。"""
    m = _memory(args)
    try:
        if args.set_credits is not None:
            path = Path(m.settings.db_path).parent / "credits.json"
            path.write_text(
                json.dumps({"credits": float(args.set_credits), "at": time.time()}),
                encoding="utf-8",
            )
            m.credits._remaining_cache = None  # 立刻生效，别等缓存过期
        ident = Ident.coerce(
            None, platform=args.platform, platform_user_id=args.user, scope=args.scope)
        st = m.quota_state(daily_cap=args.daily_cap)
        used = sends_last_24h(m.heart, ident, time.time())
        out = dict(st.to_dict())
        out["identity"] = f"{ident.platform}/{ident.platform_user_id}/{ident.scope or '-'}"
        out["used_24h"] = used
        out["left_now"] = max(0, st.allowed_24h - used)
        out["ok"] = m.proactive_quota_ok(ident)
        print(json.dumps(out, ensure_ascii=False, indent=2))
    finally:
        m.close()
    return 0


def cmd_grow(args: argparse.Namespace) -> int:
    """成长状态：学到哪、什么阶段、离下一级还差多少学分。

    成长只看学习量（学分），**不看真实时间**；`--set-stage` 是演示/验收用的钉住。
    """
    from aiji.brain import growth

    g = growth.Growth()
    if args.set_stage:
        try:
            g.set_stage(args.set_stage)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    if args.scale is not None:
        if args.scale <= 0:
            raise SystemExit("scale 必须大于 0")
        data = g.state()
        data["scale"] = float(args.scale)
        g.save(data)
    if args.sync or True:          # 看状态顺手结算一次，免得显示落后于库
        try:
            from aiji.right_hand.config import Settings

            settings = Settings.load(getattr(args, "config", None))
            db_path = getattr(args, "db", None) or settings.db_path
            g.sync(str(db_path), force=True)   # 只读计数，不启整套 Memory
        except Exception:  # noqa: BLE001 - 结算失败也照样能看
            pass
    out = g.describe()
    out["path"] = str(g.path)
    out["weights"] = dict(growth.WEIGHTS)
    out["stages"] = [
        {"id": s.id, "label": s.label, "school": s.school, "xp_min": s.xp_min,
         "range": [s.age_min, s.age_max], "sentence": s.sentence, "topics": s.topics}
        for s in growth.STAGES
    ]
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(BENCH))
    from bench_recall import run  # type: ignore[import-not-found]

    return run(args)


# ---- 三平台社交（2026-11-05） ------------------------------------------------

def _social_adapters(args: argparse.Namespace):
    from aiji.heart.platforms import build

    only = None
    if getattr(args, "platform", ""):
        only = (args.platform,)
    return build(dry_run=not bool(getattr(args, "yes", False)), only=only)


def _social_adapter(args: argparse.Namespace):
    from aiji.heart.platforms import PlatformError

    name = (getattr(args, "platform", "") or "").strip().lower()
    adapters = _social_adapters(args)
    if name not in adapters:
        raise PlatformError("bad_platform", f"不认识的平台：{name!r}",
                            hint="qq / bilibili / douyin")
    return adapters[name]


def _emit(payload: dict, *, as_json: bool = True) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _opener(peer: str, row: dict, args: argparse.Namespace) -> str:
    """续火花说什么。优先让运行中的服务按人设生成，失败才用兜底短句。"""
    if getattr(args, "text", ""):
        return args.text
    name = str(getattr(args, "peer_name", "") or "").strip()
    prompt = (f"你想找{name or '对方'}说句话，随便聊一句，续上你们的连续聊天记录。"
              f"你们已经连着聊了 {row.get('days', 1)} 天。只输出这一句话。")
    try:
        import urllib.request

        body = json.dumps({
            "model": "default",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 60, "session": "streak",
            "aiji": {"remember": False, "tools": False},
        }, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:8787/v1/chat/completions", data=body,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=45) as r:
            payload = json.loads(r.read().decode("utf-8", "replace"))
        text = str(((payload.get("choices") or [{}])[0].get("message") or {})
                   .get("content") or "").strip().strip('"')
        if text and len(text) <= 80:
            return text
    except Exception:  # noqa: BLE001 - 服务不在就兜底
        pass
    return "在干嘛呀？"


def cmd_social(args: argparse.Namespace) -> int:
    """统一入口：`aiji social [platform] [action]`。"""
    from aiji.heart.platforms import matrix, summary_lines
    from aiji.heart.platforms.base import PlatformError

    platform = (getattr(args, "platform", "") or "").strip().lower()
    action = (getattr(args, "action", "") or "").strip().lower()

    # 0) 三平台社交自检：目标里的六件事逐项判定 + 数真机成功次数
    if platform == "selfcheck" or action == "selfcheck":
        from aiji.heart.platforms import selfcheck

        report = selfcheck.build_report(live=bool(getattr(args, "live", False)))
        path = selfcheck.save(report)
        if getattr(args, "as_json", False):
            _emit({"ok": True, "path": str(path), "report": report})
        else:
            _emit({"ok": True, "path": str(path)})
            print(selfcheck.render(report))
        return 0

    # 1) 能力矩阵（不连网也能看）
    if not platform or action in ("", "matrix"):
        data = matrix(_social_adapters(args), live=bool(getattr(args, "live", False)))
        if getattr(args, "as_json", False):
            _emit(data)
        else:
            _emit({"platforms": list(data["platforms"]),
                   "actions": data["actions"]})
            print("\n".join(summary_lines(data)))
        return 0

    # 2) 账本（跨平台，不需要适配器）
    if action in ("streak", "streak-refresh") and platform != "douyin":
        try:
            m = _memory(args)
        except Exception as exc:  # noqa: BLE001
            raise PlatformError("db_unavailable", f"打不开库：{exc}") from exc
        from aiji.heart.platforms import StreakBook, observe_turns
        from aiji.heart.platforms.streak import day_of

        book = StreakBook(m.db)
        try:
            if action == "streak-refresh":
                out = observe_turns(book, m.db, platform=platform, days=3)
                out["summary"] = book.summary()
                _emit(out)
                return 0
            summary = book.summary()
            summary["due"] = book.due(platform)
            if not getattr(args, "touch", False):
                _emit(summary)
                return 0
            keys = [str(r["peer"]) for r in summary["due"]]
            if getattr(args, "peer", ""):
                keys = [str(args.peer)]
            adapter = _social_adapter(args)
            from aiji.heart.platforms import streak_sweep

            out = streak_sweep(
                adapter, book, platform=platform, peers=keys,
                message_for=lambda peer, row: _opener(peer, row, args),
                limit=int(getattr(args, "limit", 5) or 5),
                dry_run=not bool(getattr(args, "yes", False)))
            out["day"] = day_of()
            _emit(out)
            return 0 if out["ok"] else 2
        finally:
            m.close()

    # 3) 平台动作
    try:
        adapter = _social_adapter(args)
    except PlatformError as exc:
        _emit(exc.to_dict())
        return 2

    peer = str(getattr(args, "peer", "") or "")
    text = str(getattr(args, "text", "") or "")
    limit = int(getattr(args, "limit", 8) or 8)
    try:
        if action == "login":
            state = adapter.login_state()
            wait_s = float(getattr(args, "wait", 0) or 0)
            if (not state.get("is_login")) and wait_s > 0 and hasattr(adapter, "begin_login"):
                print(json.dumps({"stage": "waiting_for_scan", "qr": "data/temp/douyin-login.png",
                                  "wait_s": wait_s}, ensure_ascii=False), file=sys.stderr)
                state = adapter.begin_login(wait_s=wait_s)
            _emit(state)
            return 0
        if action == "friends":
            _emit({"ok": True, "friends": adapter.friends(limit=limit)})
            return 0
        if action == "groups":
            _emit({"ok": True, "groups": adapter.groups(limit=limit)})
            return 0
        if action == "history":
            kind = "group" if getattr(args, "group", False) else "friend"
            _emit({"ok": True, "messages": adapter.history(kind, peer, count=limit)})
            return 0
        if action == "dm":
            _emit(adapter.dm(peer, text).to_dict())
            return 0
        if action == "group":
            _emit(adapter.group(peer, text).to_dict())
            return 0
        if action == "like":
            _emit(adapter.like(peer or args.aid, dry_run=None,
                               unlike=bool(getattr(args, "unlike", False))).to_dict())
            return 0
        if action == "create-group":
            members = [x for x in (getattr(args, "members", "") or "").split(",") if x]
            _emit(adapter.group_create(text or "玛卡巴卡的小群", members).to_dict())
            return 0
        if action == "comment":
            _emit(adapter.comment(str(getattr(args, "url", "") or ""), text).to_dict())
            return 0
        if action == "streak":
            # 抖音火花：每天一条私信（web 端消息计入火花，见 excluded#217）
            _emit(adapter.streak(peer, text,
                                 dry_run=not bool(getattr(args, "yes", False))).to_dict())
            return 0
        if action == "watch":
            # 抖音群聊主动聊天：读群 → 让 aiji 想一句（和 QQ 同一套人格/记忆）→ 发
            from aiji.heart.platforms import douyin_chat

            group = peer or str(getattr(args, "peer_name", "") or "")
            if getattr(args, "all", False):
                # --all：扫所有会话（群 + 私聊），谁有新话回谁；每个会话独立限流
                out = douyin_chat.run_all(
                    adapter,
                    ask=lambda t, s: douyin_chat.http_ask(
                        t, session_id="douyin:peer:" + (s or "unknown"), sender=s),
                    self_name="玛卡巴卡的小推车",
                    dry_run=not bool(getattr(args, "yes", False)),
                )
                _emit({"ok": True, "platform": adapter.name, "action": "watch_all",
                       **out})
                return 0
            if not group:
                _emit({"ok": False, "code": "empty_peer",
                       "error": "watch 需要 --peer <群名>（或 --all 扫全部会话）"})
                return 2
            session_id = f"douyin:group:{group}"
            rounds = 1 if getattr(args, "once", False) else int(
                getattr(args, "rounds", 1) or 1)
            results = []
            for i in range(rounds):
                if i:
                    time.sleep(float(getattr(args, "interval", 60) or 60))
                results.append(douyin_chat.run_once(
                    adapter, group,
                    ask=lambda t, s: douyin_chat.http_ask(t, session_id=session_id,
                                                          sender=s),
                    dry_run=not bool(getattr(args, "yes", False))))
            _emit({"ok": True, "platform": adapter.name, "action": "watch",
                   "rounds": results})
            return 0
        if action in ("feed", "learn"):
            rows = adapter.feed(limit)
            if action == "feed" and not getattr(args, "learn", False):
                _emit({"ok": True, "platform": adapter.name, "count": len(rows),
                       "items": rows})
                return 0
            m = _memory(args)
            try:
                from aiji.heart.platforms import learn_feed

                base = _ident(args)
                ident = type(base)(adapter.name, base.platform_user_id, base.scope)
                out = learn_feed(adapter, m, ident, limit=limit,
                                 topic=str(getattr(args, "topic", "") or ""),
                                 keyword=str(getattr(args, "topic", "") or ""))
                _emit(out)
                return 0
            finally:
                m.close()
        raise PlatformError("bad_action", f"{adapter.name} 不支持动作 {action!r}",
                            hint="matrix / friends / groups / dm / group / comment / "
                                 "feed / learn / like / streak / login")
    except PlatformError as exc:
        _emit(exc.to_dict())
        return 2
    except AttributeError as exc:
        _emit({"ok": False, "code": "unsupported",
               "error": f"{adapter.name} 没有 {action} 这个动作（{exc}）"})
        return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser("aiji", description="分层记忆：SQLite 事实/画像 + 会话缓冲 + 可选向量索引")
    p.add_argument("--db", default=None, help="SQLite 路径（默认 data/aiji.db）")
    p.add_argument("--config", default=None, help="配置文件 JSON")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("serve", help="起本地 HTTP 服务")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8787)
    sp.add_argument("--presence-port", type=int, default=8789)
    sp.add_argument("--no-presence", action="store_true")
    sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser("remember", help="写一条记忆（异步抽取，等待落库后打印统计）")
    sp.add_argument("text", nargs="+")
    sp.add_argument("--session", default="default")
    sp.add_argument("--timeout", type=float, default=30.0)
    sp.add_argument("--extra", default=None, help="平台特有字段 JSON")
    _add_identity(sp)
    sp.set_defaults(func=cmd_remember)

    sp = sub.add_parser("recall", help="快召回")
    sp.add_argument("text", nargs="+")
    sp.add_argument("--session", default=None)
    sp.add_argument("--top-k", type=int, default=None)
    sp.add_argument("--budget-ms", type=int, default=None)
    _add_identity(sp)
    sp.set_defaults(func=cmd_recall)

    sp = sub.add_parser("ingest", help="任意输入（图片/语音/表情/链接/文件/视频）走识别管线")
    sp.add_argument("--kind", default="image", help="text|image|voice|emoji|file|link|video")
    sp.add_argument("--ref", default=None, help="平台侧句柄")
    sp.add_argument("--text", default=None)
    sp.add_argument("--url", default=None)
    sp.add_argument("--mime", default=None)
    sp.add_argument("--size", type=int, default=None)
    sp.add_argument("--session", default="default")
    sp.add_argument("--timeout", type=float, default=15.0)
    sp.add_argument("--extra", default=None)
    _add_identity(sp)
    sp.set_defaults(func=cmd_ingest)

    sp = sub.add_parser("learn-video", help="看视频学习：抓 B 站/抖音等视频的元数据与字幕，走同一条记忆管线")
    sp.add_argument("url", nargs="+", help="视频链接（B 站 BV/av/合集单集、抖音分享链接）")
    sp.add_argument("--timeout", type=float, default=60.0, help="yt-dlp 拉取超时（秒）")
    sp.add_argument("--cookies-from-browser", default="",
                    help="需要登录态的站点从浏览器拿 cookie：edge / chrome / firefox")
    sp.add_argument("--listen", type=float, default=0.0,
                    help="把音轨前 N 分钟转写进记忆（FunASR 听）；0=只学元数据")
    sp.add_argument("--look", type=int, default=0,
                    help="抽 N 帧用 OCR 读画面文字进记忆（标题板/PPT）；0=不看画面")
    sp.add_argument("--session", default="default")
    sp.add_argument("--flush-timeout", type=float, default=20.0)
    _add_identity(sp)
    sp.set_defaults(func=cmd_learn_video)

    sp = sub.add_parser("bili-login", help="检查 B 站登录态（玛卡巴卡的账号）")
    sp.add_argument("--cookie-file", default="", help="Netscape cookie jar（yt-dlp 导出）")
    sp.add_argument("--cookies-from-browser", default="",
                    help="从浏览器拿 cookie：edge / chrome / firefox")
    sp.set_defaults(func=cmd_bili_login)

    sp = sub.add_parser("bili-comment", help="B 站评论：默认 dry-run，--yes 才真发")
    sp.add_argument("url", nargs="+", help="视频链接")
    sp.add_argument("message", help="评论内容（建议先过 persona 措辞）")
    sp.add_argument("--cookie-file", default="")
    sp.add_argument("--cookies-from-browser", default="")
    sp.add_argument("--yes", action="store_true", help="确认真发（默认只 dry-run）")
    sp.add_argument("--persona", action="store_true",
                    help="先按玛卡巴卡口吻改写（走运行中服务）；dry-run 会展示改写结果")
    sp.add_argument("--style-timeout", type=float, default=60.0)
    sp.add_argument("--state-dir", default="data", help="留痕目录（bili_actions.jsonl）")
    sp.add_argument("--session", default="default")
    _add_identity(sp)
    sp.set_defaults(func=cmd_bili_comment)

    sp = sub.add_parser("bili-upload", help="B 站投稿（发视频）：默认 dry-run，--yes 才真传")
    sp.add_argument("file", help="视频文件路径（mp4 等）")
    sp.add_argument("title", help="投稿标题（≤80 字）")
    sp.add_argument("--desc", default="", help="投稿简介")
    sp.add_argument("--cookie-file", default="")
    sp.add_argument("--cookies-from-browser", default="")
    sp.add_argument("--yes", action="store_true", help="确认真传（默认只本地预检）")
    sp.add_argument("--timeout", type=float, default=300.0, help="分片上传超时")
    sp.add_argument("--state-dir", default="data")
    sp.set_defaults(func=cmd_bili_upload)

    sp = sub.add_parser("inject", help="生成给主模型的软引导块（不生成回复）")
    sp.add_argument("query", nargs="+")
    sp.add_argument("--session", default=None)
    sp.add_argument("--max-chars", type=int, default=None)
    sp.add_argument("--max-items", type=int, default=None)
    _add_identity(sp)
    sp.set_defaults(func=cmd_inject)

    sp = sub.add_parser("feedback", help="回报主模型实际用了哪些记忆")
    sp.add_argument("--injection-id", type=int, default=None)
    sp.add_argument("--used", type=int, nargs="*", default=[])
    sp.add_argument("--not-helpful", action="store_true")
    sp.add_argument("--note", default=None)
    _add_identity(sp)
    sp.set_defaults(func=cmd_feedback)

    sp = sub.add_parser("style", help="查看风格/摘要/画像")
    sp.add_argument("--session", default=None)
    _add_identity(sp)
    sp.set_defaults(func=cmd_style)

    sp = sub.add_parser("media", help="登记/列出媒体引用（不存文件）")
    sp.add_argument("--kind", default="image")
    sp.add_argument("--ref", default=None, help="平台侧句柄；给了就登记")
    sp.add_argument("--url", default=None)
    sp.add_argument("--mime", default=None)
    sp.add_argument("--session", default=None)
    sp.add_argument("--limit", type=int, default=20)
    _add_identity(sp)
    sp.set_defaults(func=cmd_media)

    sp = sub.add_parser("media-gc", help="清理没有文字结论的失败媒体行（默认只看不删）")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_media_gc)

    sp = sub.add_parser("accounts", help="列出已知身份")
    sp.set_defaults(func=cmd_accounts)

    sp = sub.add_parser("stats", help="存储/队列/模型池统计")
    sp.set_defaults(func=cmd_stats)

    sp = sub.add_parser("doctor", help="环境与配置自检")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("export", help="导出某身份的活跃事实为 JSON")
    sp.add_argument("--out", default=None)
    _add_identity(sp)
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser("forget", help="显式遗忘一条事实（立刻不可见 + 留审计）")
    sp.add_argument("--id", type=int, default=None)
    sp.add_argument("--like", default="", help="按文本片段匹配（慎用）")
    _add_identity(sp)
    sp.set_defaults(func=cmd_forget)

    sp = sub.add_parser("archive", help="回查被压缩淘汰的原文（退役不删）")
    sp.add_argument("--session", default=None)
    sp.add_argument("--limit", type=int, default=50)
    _add_identity(sp)
    sp.set_defaults(func=cmd_archive)

    sp = sub.add_parser("janitor", help="手动跑一轮代谢清理（输出统计）")
    sp.set_defaults(func=cmd_janitor)

    sp = sub.add_parser("day", help="作息 / 日记 / 发布：plan | now | diary | publish")
    sp.add_argument("what", choices=["plan", "now", "diary", "publish", "audit"])
    sp.add_argument("--date", default="", help="日记日期 YYYY-MM-DD（默认今天）")
    sp.add_argument("--file", default="", help="publish：视频文件路径")
    sp.add_argument("--title", default="", help="publish：投稿标题")
    sp.add_argument("--desc", default="", help="publish：简介")
    sp.add_argument("--yes", action="store_true", help="publish：确认真传")
    sp.add_argument("--json", dest="as_json", action="store_true")
    sp.set_defaults(func=cmd_day)

    sp = sub.add_parser("self-memory", help="自我记忆：列表 / --add 写一条")
    sp.add_argument("--add", default="")
    sp.add_argument("--kind", default="pref")
    sp.add_argument("--scope", default="default")
    sp.add_argument("--limit", type=int, default=50)
    sp.set_defaults(func=cmd_self_memory)

    sp = sub.add_parser("quota", help="主动配额：现在还能主动几次、依据是什么（反代积分）")
    _add_identity(sp)
    sp.add_argument("--daily-cap", type=int, default=None)
    sp.add_argument("--set", dest="set_credits", type=float, default=None,
                    help="把反代当前积分写进 data/credits.json（按单价折算成每日次数）")
    sp.set_defaults(func=cmd_quota)

    sp = sub.add_parser(
        "grow",
        help="成长状态：学分/阶段/离下一级差多少；可 --set-stage 钉住、--scale 调节门槛")
    sp.add_argument("--set-stage", default=None,
                    choices=["primary", "middle", "high", "university", "work"],
                    help="把阶段钉到这一级（演示/验收用；平时靠学习量自己长）")
    sp.add_argument("--scale", type=float, default=None,
                    help="门槛倍数：>1 长得更慢，<1 长得更快（默认 1.0）")
    sp.add_argument("--sync", action="store_true",
                    help="先结算一次（默认也会结算，写下来只是让意图明显）")
    sp.set_defaults(func=cmd_grow)

    sp = sub.add_parser(
        "social",
        help="三平台社交：能力矩阵 / 私聊群聊 / 发评论 / 刷视频学习 / 续火花（默认 dry-run）")
    sp.add_argument("platform", nargs="?", default="",
                    help="qq / bilibili / douyin（留空 = 打印能力矩阵）")
    sp.add_argument("action", nargs="?", default="",
                    help="matrix|login|friends|groups|history|dm|group|comment|"
                         "feed|learn|like|create-group|streak|streak-refresh|watch")
    sp.add_argument("--peer", default="", help="对方 user_id / group_id / mid / 昵称")
    sp.add_argument("--text", default="", help="要发的话；续火花留空则由人设现生成")
    sp.add_argument("--url", default="", help="评论目标（视频链接）")
    sp.add_argument("--topic", default="", help="刷内容的关键词（抖音=搜索词）")
    sp.add_argument("--limit", type=int, default=8)
    sp.add_argument("--members", default="", help="建群成员，逗号分隔 QQ 号")
    sp.add_argument("--aid", type=int, default=0, help="B 站点赞用")
    sp.add_argument("--unlike", action="store_true", help="B 站取消点赞（like 的反向，用来做真机往返）")
    sp.add_argument("--group", action="store_true", help="history：按群聊查")
    sp.add_argument("--learn", action="store_true", help="feed 抓到内容后顺手写进记忆")
    sp.add_argument("--live", action="store_true", help="矩阵里顺带查登录/传输状态")
    sp.add_argument("--touch", action="store_true", help="streak：真的去续（默认只看）")
    sp.add_argument("--all", action="store_true",
                    help="watch：扫所有会话（群+私聊），谁有新话回谁")
    sp.add_argument("--once", action="store_true",
                    help="watch：只跑一轮（不写这个就按 --rounds 跑）")
    sp.add_argument("--rounds", type=int, default=1, help="watch：跑几轮")
    sp.add_argument("--interval", type=float, default=60.0, help="watch：轮次间隔（秒）")
    sp.add_argument("--peer-name", dest="peer_name", default="")
    sp.add_argument("--wait", type=float, default=0.0,
                    help="login：等扫码的秒数（>0 才开有头窗口）")
    sp.add_argument("--yes", action="store_true", help="确认真发（默认 dry-run）")
    sp.add_argument("--json", dest="as_json", action="store_true")
    sp.add_argument("--user", dest="user", default=None)
    sp.add_argument("--scope", default=None)
    sp.set_defaults(func=cmd_social)

    sp = sub.add_parser("bench", help="召回延迟压测")
    sp.add_argument("--facts", type=int, default=5000)
    sp.add_argument("--queries", type=int, default=200)
    sp.add_argument("--budget-ms", type=int, default=100)
    sp.add_argument("--workers", type=int, default=4)
    sp.add_argument("--platforms", type=int, default=1, help="压测时分散到几个平台")
    sp.add_argument("--json", default=None)
    sp.set_defaults(func=cmd_bench)
    return p


def _diary_ask(prompt: str) -> str:
    """问本机的 aiji 服务写日记（走同一套人格/文风）；服务没起就返回空，回退模板。"""
    import urllib.request

    body = {"model": "aiji", "user": "diary", "stream": False,
            "messages": [{"role": "user", "content": prompt}],
            "aiji": {"tools": False, "history": False, "proactive": False}}
    req = urllib.request.Request(
        "http://127.0.0.1:8787/v1/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return str(data["choices"][0]["message"]["content"] or "")
    except Exception:  # noqa: BLE001
        return ""


def cmd_day(args: Any) -> int:
    """作息 / 当前在干嘛 / 今天的日记 / 发布。"""
    from aiji.brain import day as day_mod
    from aiji.brain import growth as growth_mod

    stage = growth_mod.current().stage()
    what = args.what
    if what == "plan":
        _emit({"ok": True, "stage": stage.id, "day": time.strftime("%Y-%m-%d"),
               "blocks": day_mod.plan(stage_id=stage.id)})
        return 0
    if what == "now":
        block = day_mod.current_block(stage_id=stage.id)
        _emit({"ok": True, "stage": stage.id, "now": time.strftime("%H:%M"),
               "doing": block["what"], "kind": block["kind"], "until": block["end"],
               "left_min": block["left_min"], "talk": block["talk"]})
        return 0
    if what == "diary":
        day_str = args.date or time.strftime("%Y-%m-%d")
        m = _memory(args)
        try:
            material = day_mod.day_facts(m)
            text = day_mod.llm_diary(material, stage_label=stage.label, day=day_str,
                                     ask=_diary_ask)
            path = day_mod.write_diary(text, day=day_str)
            # B4：日记里的"明天想……"落进自我记忆，life_loop 次日看得见
            plan = day_mod.extract_tomorrow_plan(text)
            plan_id = 0
            if plan:
                plan_id = m.remember_self(plan, kind="plan", scope="default",
                                         importance=0.7)
            _emit({"ok": True, "day": day_str, "path": str(path), "chars": len(text),
                   "turns": material.get("turns", 0),
                   "facts": len(material.get("facts") or []), "preview": text[:160],
                   "tomorrow_plan": plan, "self_memory_id": plan_id})
        finally:
            m.close()
        return 0
    if what == "audit":
        import json as _json
        from pathlib import Path as _P

        report = day_mod.audit_sends()
        out_dir = _P("data/audit")
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{report['day']}.json"
        path.write_text(_json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        _emit({"ok": True, **report, "path": str(path)})
        return 0 if report.get("healthy") else 0
    if what == "publish":
        targets = day_mod.publish_targets()
        if not args.file:
            _emit({"ok": False, "code": "no_file", "targets": targets,
                   "error": "publish 需要 --file <视频路径>（B 站能投稿，抖音没有投稿入口）"})
            return 2
        from aiji.throat import bili_client

        out = bili_client.upload_video(
            Path(args.file), args.title or f"{time.strftime('%Y-%m-%d')} 日记",
            args.desc, dry_run=not bool(args.yes))
        _emit({"ok": bool(getattr(out, "ok", True)), "platform": "bilibili",
               "targets": targets, "detail": getattr(out, "detail", {})})
        return 0
    _emit({"ok": False, "code": "bad_what"})
    return 2


def main(argv: list[str] | None = None) -> int:
    # 中文 Windows 的 stdout 默认 GBK：打印 B 站标题（含 \xa0/emoji）会 UnicodeEncodeError
    # 把整条命令打成 traceback。CLI 输出统一走 UTF-8（管道/文件也一致）。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
