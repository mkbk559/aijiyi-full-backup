"""命令行入口：serve / remember / recall / media / accounts / stats / doctor / export / bench。"""

from __future__ import annotations

import argparse
import json
import os
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


def cmd_serve(args: argparse.Namespace) -> int:
    from aiji.endocrine.presence import PresenceServer
    from aiji.throat.server import serve

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

    sp = sub.add_parser("bench", help="召回延迟压测")
    sp.add_argument("--facts", type=int, default=5000)
    sp.add_argument("--queries", type=int, default=200)
    sp.add_argument("--budget-ms", type=int, default=100)
    sp.add_argument("--workers", type=int, default=4)
    sp.add_argument("--platforms", type=int, default=1, help="压测时分散到几个平台")
    sp.add_argument("--json", default=None)
    sp.set_defaults(func=cmd_bench)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
