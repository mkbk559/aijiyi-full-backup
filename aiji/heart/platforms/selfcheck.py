"""三平台社交能力的**一键自检**：把"目标里的六件事"逐项判成机器可读的状态。

为什么要有它：能力散在三个适配器里，人肉汇总每次都要翻日志。这里统一做三件事——
1. 问每个适配器要 `capabilities()`（ok / experimental / unsupported / needs_login / offline）
2. （可选）真机探一次 `login_state()`
3. 从 `data/platform_actions.jsonl` 里数"真发出去过几次"（只算 ok=True 且 verified≠False 的）

输出的 JSON 可以直接贴进交接文档，也能当回归基线：跑完对比两次的 `items` 就知道有没有退化。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

ITEMS: dict[str, str] = {
    "dm": "私聊",
    "group": "群聊 / 主动聊天",
    "feed": "刷视频学习",
    "streak": "续火花",
    "comment": "发评论",
}

# 用户 2026-11-05 定调：**不做自建群**；火花只有抖音有，其他平台没有就不接。
# 这些项在报告里显示成「—（该平台没有）」，不算缺口。
NOT_APPLICABLE: dict[str, set[str]] = {
    "qq": {"feed"},
    "douyin": {"group_create"},
    "bilibili": {"streak", "group_create"},
}
DROPPED = {"group_create": "用户明确说不需要（2026-11-05）"}

# 真机证据在哪些文件里（报告里直接给出路径，省得下次翻）
EVIDENCE = {
    "qq": ["data/platform_actions.jsonl", "data/logs/astrbot.log"],
    "douyin": ["data/logs/douyin_chat.log", "data/platform_actions.jsonl"],
    "bilibili": ["data/platform_actions.jsonl", "data/bili_cookies.jar"],
}

STATUS_LABEL = {
    "ok": "✅ ok",
    "experimental": "⚠ experimental",
    "unsupported": "❌ 不支持",
    "needs_login": "🔑 需登录",
    "offline": "⚪ 传输断开",
    "": "—",
}


def _read_actions(path: Path, *, limit_lines: int = 20000) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return rows
    for line in text.strip().splitlines()[-limit_lines:]:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:  # noqa: BLE001
            continue
    return rows


def _evidence_counts(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """每个 (platform, action) 的"成功次数 + 最近一次时间"。

    只把 **显式回读通过**（`verified is True`，或在 `detail.verified` 里）算成真发出去；
    老日志里没有 verified 字段的成功行单列成 `unverified`——2026-11-05 抖音评论就是
    "适配器说发成功、评论区其实没有"（excluded#210），不能混进真机证据里。
    """
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not row.get("ok"):
            continue
        key = f"{row.get('platform','?')}.{row.get('action','?')}"
        slot = out.setdefault(key, {"ok": 0, "unverified": 0, "last_ts": 0})
        verified = row.get("verified")
        if verified is None:
            verified = (row.get("detail") or {}).get("verified")
        if verified is True:
            slot["ok"] += 1
            ts = row.get("ts") or 0
            if isinstance(ts, (int, float)) and ts > slot["last_ts"]:
                slot["last_ts"] = ts
        else:
            slot["unverified"] += 1
    return out


def build_report(*, live: bool = False, platforms: Any = None,
                 action_log: str | Path = "data/platform_actions.jsonl",
                 data_dir: str | Path = "data") -> dict[str, Any]:
    """跑一次自检，返回结构化报告（不写盘；写盘在 `save`）。"""
    if platforms is None:
        from aiji.heart.platforms import registry

        platforms = registry.build(runtime={}, dry_run=True)
    counts = _evidence_counts(_read_actions(Path(action_log)))

    report: dict[str, Any] = {
        "ts": time.time(),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "live": bool(live),
        "platforms": {},
    }
    for name, adapter in platforms.items():
        caps = adapter.capabilities()
        unknown = sorted(set(caps) - set(ITEMS))
        entry: dict[str, Any] = {
            "capabilities": dict(caps),
            "notes": adapter.notes(),
            "items": {},
            "evidence": EVIDENCE.get(name, []),
            "extra_capabilities": unknown,
        }
        for key, label in ITEMS.items():
            status = caps.get(key, "")
            ev = counts.get(f"{name}.{key}", {})
            na = key in NOT_APPLICABLE.get(name, set())
            entry["items"][key] = {
                "label": label,
                "status": status,
                "not_applicable": na,
                "status_label": ("—（该平台没有）" if na
                                 else STATUS_LABEL.get(status, status or "—")),
                "real_sends": ev.get("ok", 0),
                "unverified_sends": ev.get("unverified", 0),
                "last_ts": ev.get("last_ts", 0),
                "last_at": (time.strftime("%Y-%m-%d %H:%M:%S",
                                          time.localtime(ev["last_ts"]))
                            if ev.get("last_ts") else ""),
            }
        if live:
            try:
                entry["login"] = adapter.login_state()
            except Exception as exc:  # noqa: BLE001 - 探活失败不算致命
                entry["login"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        report["platforms"][name] = entry
    report["data_dir"] = str(data_dir)
    return report


def save(report: dict[str, Any], path: str | Path = "data/social_selfcheck.json") -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def render(report: dict[str, Any]) -> str:
    """给人看的表格：一眼看出哪件事在哪个平台是真通了的。"""
    names = list(report.get("platforms", {}))
    head = "| 能力 | " + " | ".join(names) + " |"
    sep = "|---" * (len(names) + 1) + "|"
    lines = [head, sep]
    for key, label in ITEMS.items():
        cells = []
        for name in names:
            item = report["platforms"][name]["items"].get(key, {})
            mark = item.get("status_label", "—")
            n = item.get("real_sends", 0)
            u = item.get("unverified_sends", 0)
            if n:
                mark += f" ×{n}"
            if u:
                mark += f"（另有 {u} 次未验）"
            cells.append(mark)
        lines.append(f"| {label} (`{key}`) | " + " | ".join(cells) + " |")
    if report.get("live"):
        lines.append("")
        lines.append("真机登录态：")
        for name in names:
            login = report["platforms"][name].get("login") or {}
            ok = login.get("ok", login.get("is_login", False))
            lines.append(f"- {name}: {'✅' if ok else '❌'} {json.dumps(login, ensure_ascii=False)[:160]}")
    return "\n".join(lines)
