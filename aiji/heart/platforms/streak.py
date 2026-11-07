"""续火花（社交连续性账本）。

QQ 的"火花"是"连续 N 天有消息往来"。平台自己算，但机器人要能**主动续**：
每天挑出"昨天还连着、今天还没互动"的 peer，发一句真实的由头（不是打卡文案），
再把这一天的互动记进账本。

账本口径（写死在测试里）：
- 同一天再记一次**不涨**（幂等，避免同一晚来回十条消息刷出十天花）；
- 隔一天记一次 +1；
- 断一天以上归零重来，`best` 保留历史最长。
- 日期用**本地日**（`%Y-%m-%d`）：火花是给人看的，跟着人所在时区走。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any, Iterable

from aiji.heart.platforms.base import PlatformError
from aiji.hippocampus.db import DB

DAY_FMT = "%Y-%m-%d"


def day_of(ts: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if ts is None else ts).strftime(DAY_FMT)


def _shift(day: str, delta: int) -> str:
    base = datetime.strptime(day, DAY_FMT)
    return (base + timedelta(days=delta)).strftime(DAY_FMT)


class StreakBook:
    """连续互动账本。所有写都走 `DB.tx()`（全局写锁），读走 `DB.read()`。"""

    def __init__(self, db: DB, *, clock=time.time) -> None:
        self.db = db
        self.clock = clock

    # ---- 写 ---------------------------------------------------------------
    def touch(self, platform: str, peer: str | int, *, kind: str = "friend",
              day: str | None = None) -> dict[str, Any]:
        """记一次互动。返回 {days, best, extended, day, previous}。"""
        peer = str(peer or "").strip()
        if not peer:
            raise PlatformError("invalid_peer", "peer 不能为空")
        day = day or day_of(self.clock())
        now = float(self.clock())
        with self.db.tx() as c:
            row = c.execute(
                "SELECT days, best, last_day FROM streak"
                " WHERE platform=? AND peer=? AND kind=?",
                (platform, peer, kind),
            ).fetchone()
            if row is None:
                days, best, previous = 1, 1, ""
                c.execute(
                    "INSERT INTO streak(platform, peer, kind, last_day, days, best, updated_at)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (platform, peer, kind, day, days, best, now),
                )
                extended = True
            else:
                previous = row["last_day"]
                if previous == day:
                    # 今天已经记过：幂等，不涨也不写
                    return {
                        "platform": platform, "peer": peer, "kind": kind,
                        "day": day, "days": int(row["days"]), "best": int(row["best"]),
                        "extended": False, "previous": previous,
                    }
                if previous == _shift(day, -1):
                    days = int(row["days"]) + 1
                    extended = True
                else:
                    days = 1          # 断了，从今天重新算
                    extended = False
                best = max(int(row["best"]), days)
                c.execute(
                    "UPDATE streak SET last_day=?, days=?, best=?, updated_at=?"
                    " WHERE platform=? AND peer=? AND kind=?",
                    (day, days, best, now, platform, peer, kind),
                )
        return {
            "platform": platform, "peer": peer, "kind": kind, "day": day,
            "days": days, "best": best, "extended": extended, "previous": previous,
        }

    def reset(self, platform: str, peer: str | int, *, kind: str = "friend") -> int:
        with self.db.tx() as c:
            cur = c.execute(
                "DELETE FROM streak WHERE platform=? AND peer=? AND kind=?",
                (platform, str(peer), kind),
            )
        return int(cur.rowcount or 0)

    # ---- 读 ---------------------------------------------------------------
    def status(self, platform: str | None = None, *, kind: str | None = None,
               day: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        day = day or day_of(self.clock())
        sql = "SELECT platform, peer, kind, last_day, days, best FROM streak"
        args: list[Any] = []
        where = []
        if platform:
            where.append("platform=?")
            args.append(platform)
        if kind:
            where.append("kind=?")
            args.append(kind)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY days DESC, last_day DESC LIMIT ?"
        args.append(int(limit))
        rows = self.db.read(sql, tuple(args))
        out = []
        for r in rows:
            alive = r["last_day"] >= _shift(day, -1)
            out.append({
                "platform": r["platform"], "peer": r["peer"], "kind": r["kind"],
                "last_day": r["last_day"], "days": int(r["days"]),
                "best": int(r["best"]), "alive": alive,
                "effective_days": int(r["days"]) if alive else 0,
                "touched_today": r["last_day"] == day,
            })
        return out

    def due(self, platform: str | None = None, *, day: str | None = None,
            limit: int = 50) -> list[dict[str, Any]]:
        """昨天还连着、今天还没互动的 peer —— 这些才是"要续"的。"""
        day = day or day_of(self.clock())
        return [
            row for row in self.status(platform, day=day, limit=limit)
            if row["alive"] and not row["touched_today"]
        ]

    def summary(self, *, day: str | None = None) -> dict[str, Any]:
        day = day or day_of(self.clock())
        rows = self.status(day=day)
        due = [r for r in rows if r["alive"] and not r["touched_today"]]
        return {
            "day": day,
            "tracked": len(rows),
            "alive": sum(1 for r in rows if r["alive"]),
            "due": len(due),
            "best": max([r["best"] for r in rows], default=0),
            "top": rows[:5],
        }


def observe_turns(book: StreakBook, db: DB, *, platform: str = "qq",
                  days: int = 2, now: float | None = None) -> dict[str, Any]:
    """从真实消息记录里补账：把最近 `days` 天每个 peer 的互动日都记进去。

    为什么不依赖内存里的实时事件：重启、断线、插件重装都会漏事件，
    而 `turn` 表是**已经落库的事实**——用它回填，账本才有"可信的历史"。
    """
    now = time.time() if now is None else now
    since = now - days * 86400.0
    rows = db.read(
        "SELECT platform_user_id AS peer, ts FROM turn"
        " WHERE platform=? AND ts>=? ORDER BY ts ASC",
        (platform, since),
    )
    seen: dict[str, set[str]] = {}
    for r in rows:
        seen.setdefault(str(r["peer"]), set()).add(day_of(float(r["ts"])))
    touched = 0
    for peer, dayset in seen.items():
        for day in sorted(dayset):
            book.touch(platform, peer, day=day)
            touched += 1
    return {"peers": len(seen), "days_recorded": touched, "since_days": days}


def plan_touches(book: StreakBook, platform: str, peers: Iterable[str],
                 *, day: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
    """从候选 peer 里挑出今天要续的（已在账本里的优先，其次新 peer）。"""
    day = day or day_of(book.clock())
    due = {str(r["peer"]): r for r in book.due(platform, day=day, limit=200)}
    out = []
    for peer in peers:
        peer = str(peer)
        row = due.get(peer)
        if row is not None:
            out.append(row)
        if len(out) >= limit:
            break
    return out
