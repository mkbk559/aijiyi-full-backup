"""一天的作息：严格时间表 + 当天日记 + 发布。

为什么要有这个：`玛卡巴卡` 之前是"被动接话"的——谁说话它才动，没人说话它就静止。
但一个人是有作息的：几点起、几点上学、几点写作业、晚上写日记。这套东西决定了
"它现在正在干嘛"，也决定了它跟人聊天时的状态（上课时只能短回、晚上有空多聊）。

设计原则：
- **确定性优先**：时间表是纯函数（`plan(date)`），不依赖模型，任何一次调用都一样；
- 阶段跟着成长引擎走（小学/初中/高中…作息不同），不写死；
- 日记是"当天真实发生过什么"的总结（turn + fact），不是编故事；
- 发布走平台已有能力：B 站有 `bili_client.upload_video`，抖音视频投稿**没有**（真机只有评论/私信/点赞），
  所以抖音那条路如实报 unsupported，不假装。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DATA = Path(__file__).resolve().parents[2] / "data"
DIARY_DIR = DATA / "diary"


@dataclass(frozen=True)
class Block:
    """一个时间块：几点到几点，干什么。"""

    start: str          # "06:40"
    end: str            # "07:10"
    kind: str           # wake / meal / school / study / rest / social / diary / sleep
    what: str           # 人话描述
    talk: str = "short"  # 这段时间能怎么聊天：none / short / normal

    def to_dict(self) -> dict[str, str]:
        return {"start": self.start, "end": self.end, "kind": self.kind,
                "what": self.what, "talk": self.talk}


# 初中作息（默认）。小学/高中只是时间差一点，够用就先这样，别过早抽象。
MIDDLE_SCHOOL: tuple[Block, ...] = (
    Block("06:40", "07:10", "wake", "起床、刷牙洗脸、穿校服", "short"),
    Block("07:10", "07:40", "meal", "吃早饭（一般是我妈做的粥）", "short"),
    Block("07:40", "08:00", "school", "走去学校", "none"),
    Block("08:00", "11:50", "school", "上课（上午四节，数学最费脑子）", "none"),
    Block("11:50", "12:40", "meal", "午饭 + 在教室趴一会儿", "short"),
    Block("12:40", "17:20", "school", "下午的课（有体育就开心）", "none"),
    Block("17:20", "18:10", "rest", "回家、吃点东西、刷会儿视频", "normal"),
    Block("18:10", "19:00", "meal", "晚饭", "short"),
    Block("19:00", "21:00", "study", "写作业（数学卷子最磨人）", "none"),
    Block("21:00", "22:00", "social", "自由时间：聊天、看视频、听歌", "normal"),
    Block("22:00", "22:30", "diary", "写今天的日记", "short"),
    Block("22:30", "06:40", "sleep", "睡觉", "none"),
)


def _stage_blocks(stage_id: str) -> tuple[Block, ...]:
    """按成长阶段取作息。没有专门表的阶段先用初中表（不编不存在的作息）。"""
    return MIDDLE_SCHOOL


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def plan(when: float | None = None, *, stage_id: str = "middle") -> list[dict[str, str]]:
    """某一天的作息表（纯函数，不碰世界）。"""
    return [b.to_dict() for b in _stage_blocks(stage_id)]


def current_block(when: float | None = None, *, stage_id: str = "middle") -> dict[str, Any]:
    """现在这个点，应该在干什么。跨零点（睡觉那段）也要对。"""
    ts = time.localtime(when if when is not None else time.time())
    now = ts.tm_hour * 60 + ts.tm_min
    for b in _stage_blocks(stage_id):
        start, end = _minutes(b.start), _minutes(b.end)
        inside = (start <= now < end) if start < end else (now >= start or now < end)
        if inside:
            left = (end - now) % (24 * 60)
            return {**b.to_dict(), "left_min": left}
    return {**_stage_blocks(stage_id)[0].to_dict(), "left_min": 0}


def _day_bounds(when: float | None = None) -> tuple[float, float]:
    """本机时间的"今天 00:00"到"现在"。"""
    ts = time.localtime(when if when is not None else time.time())
    start = time.mktime((ts.tm_year, ts.tm_mon, ts.tm_mday, 0, 0, 0, 0, 0, -1))
    return start, time.time()


_NOISE_PAT = (
    r"^@?[:：]?\(\(",      # @:((3634784769) 这种 @ 提及残留
    r"^\[图片\]",          # 图片 OCR 转写
    r"^\[动画表情\]",
    r"^\[表情\]",
    r"^(hello|hi|在吗|你好)[。！？!?]*$",
)


def _is_noise(text: str) -> bool:
    """这句话是不是"没有信息量"的噪声（@残留、图片转写、纯招呼）。"""
    import re as _re

    t = (text or "").strip()
    if len(t) < 4:
        return True
    return any(_re.search(pat, t, _re.I) for pat in _NOISE_PAT)


def _spread(items: list[str], n: int) -> list[str]:
    """均匀取样：取首尾和中间的，不要"当天最早那几句"。"""
    if len(items) <= n:
        return items
    step = len(items) / float(n)
    return [items[min(len(items) - 1, int(i * step))] for i in range(n)]


def day_facts(memory: Any, *, ident: Any = None, limit: int = 40,
              when: float | None = None) -> dict[str, Any]:
    """把"今天真的发生过什么"从库里捞出来（不编）。"""
    start, end = _day_bounds(when)
    out: dict[str, Any] = {"turns": 0, "said": [], "facts": []}
    db = getattr(memory, "db", None)
    _conn = getattr(db, "conn", None)
    conn = _conn() if callable(_conn) else _conn   # DB.conn 是方法，不是属性
    if conn is None:
        return out
    try:
        rows = conn.execute(
            "SELECT role, content FROM turn WHERE ts >= ? ORDER BY ts",
            (start,)).fetchall()
    except Exception:  # noqa: BLE001
        rows = []
    for role, text in rows or []:
        if str(role) != "user":
            continue
        out["turns"] += 1
        text = str(text or "").strip()
        if not text or _is_noise(text):
            continue
        if text not in out["said"]:
            out["said"].append(text[:120])
    try:
        rows = conn.execute(
            "SELECT text FROM fact WHERE created_at >= ? ORDER BY created_at LIMIT ?",
            (start, limit)).fetchall()
        facts = []
        for (t,) in rows or []:
            f = str(t or "").strip()
            if f and not _is_noise(f) and f not in facts:
                facts.append(f[:100])
        out["facts"] = facts
    except Exception:  # noqa: BLE001
        pass
    return out

def diary_text(material: dict[str, Any], *, stage_label: str = "初中",
               day: str = "") -> str:
    """不靠模型也能写出一篇像样的日记（有素材就写素材，没素材就写"今天挺平常"）。"""
    said = [s for s in material.get("said") or [] if s]
    facts = [f for f in material.get("facts") or [] if f]
    lines = [f"# {day or time.strftime('%Y-%m-%d')} 日记", ""]
    lines.append(f"今天跟人聊了 {material.get('turns', 0)} 句。" if material.get("turns")
                 else "今天没人来找我说话，安静得有点奇怪。")
    if said:
        lines.append("")
        lines.append("说过的话里，我记得这几句：")
        for s in _spread(said, 6):
            lines.append(f"- 「{s}」")
    if facts:
        lines.append("")
        lines.append("今天学到/记住的事：")
        for f in _spread(facts, 8):
            lines.append(f"- {f}")
    lines.append("")
    lines.append(f"（现在的我是{stage_label}，一天就这么过去了，明天还有数学。）")
    return "\n".join(lines)


def llm_diary(material: dict[str, Any], *, stage_label: str = "初中",
              day: str = "", ask: Any = None) -> str:
    """让模型用第一人称写今天的日记；失败/没素材就退回模板（模板永远可用）。"""
    said = _spread([s for s in material.get("said") or [] if s], 10)
    facts = _spread([f for f in material.get("facts") or [] if f], 12)
    if not said and not facts:
        return diary_text(material, stage_label=stage_label, day=day)
    if ask is None:
        return diary_text(material, stage_label=stage_label, day=day)
    prompt = (
        f"你是{stage_label}生，今天（{day}）跟人聊了 {material.get('turns', 0)} 句。\n"
        f"今天说过/听过的话：\n" + "\n".join(f"- {x}" for x in said) +
        "\n\n今天记住的事：\n" + "\n".join(f"- {x}" for x in facts) +
        "\n\n用第一人称写一篇今天的日记（150–250 字），像真的中学生写的。\n"
        "**硬规矩：只写上面素材里真实出现过的事。**不许编造吃了什么、去哪玩、谁陪你，"
        "素材里没有的细节一律不写；拿不准就少写，写\"今天挺平常\"也行。"
        "可以写的：聊了什么、记住了什么、对哪件事有情绪、作息上的事（上课/写作业/睡觉）。"
        "只写日记正文，不要标题、不要分点；提到对方就说昵称或\"他\"，"
        "不要出现\"用户\"\"模型\"\"AI\"这种词。"
    )
    try:
        text = str(ask(prompt) or "").strip()
    except Exception:  # noqa: BLE001
        text = ""
    if len(text) < 40:
        return diary_text(material, stage_label=stage_label, day=day)
    return f"# {day or time.strftime('%Y-%m-%d')} 日记\n\n{text}\n"


def write_diary(text: str, *, day: str = "", root: Path | str = DIARY_DIR) -> Path:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{day or time.strftime('%Y-%m-%d')}.md"
    path.write_text(text, encoding="utf-8")
    return path


def list_diaries(root: Path | str = DIARY_DIR, limit: int = 30) -> list[str]:
    root = Path(root)
    if not root.exists():
        return []
    return sorted((p.stem for p in root.glob("*.md")), reverse=True)[:limit]


def audit_sends(actions_path: Path | str = DATA / "platform_actions.jsonl",
                *, when: float | None = None) -> dict[str, Any]:
    """体检当天发出的消息：重复、身份矛盾、节奏、失败原因。

    只读不写库；结果给 `data/audit/<日期>.json` 和命令行看。
    """
    import collections
    import datetime as _dt
    import re as _re

    path = Path(actions_path)
    day = _dt.datetime.fromtimestamp(when if when is not None else time.time()).date()
    sent: list[dict[str, Any]] = []
    fails: list[dict[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                ts = float(row.get("ts") or 0)
                stamp = _dt.datetime.fromtimestamp(ts)
            except Exception:  # noqa: BLE001
                continue
            if stamp.date() != day:
                continue
            detail = row.get("detail") or {}
            text = str(detail.get("text") or "")
            if row.get("ok") and text:
                sent.append({"t": stamp, "platform": row.get("platform"),
                             "action": row.get("action"),
                             "peer": detail.get("peer") or detail.get("url") or "",
                             "text": text})
            elif not row.get("ok"):
                fails.append({"t": stamp, "platform": row.get("platform"),
                              "action": row.get("action"), "code": row.get("code")})

    counts = collections.Counter(s["text"] for s in sent)
    dups = {t: n for t, n in counts.items() if n > 1}
    identity = [s for s in sent if _re.search(r"(小肥鱼|六岁|小学一年)", s["text"])]
    per_hour = collections.Counter(s["t"].strftime("%H") for s in sent)
    busiest = max(per_hour.values()) if per_hour else 0
    fail_codes = collections.Counter(f"{f['platform']}:{f['action']}:{f['code']}"
                                     for f in fails)
    problems: list[str] = []
    if dups:
        problems.append(f"{len(dups)} 句话重复发过（最多 {max(dups.values())} 次）")
    if identity:
        problems.append(f"{len(identity)} 条消息里有旧身份/错年龄")
    if busiest > 6:
        problems.append(f"某一小时发了 {busiest} 条，节奏偏机械")
    if len(fails) > 10:
        problems.append(f"失败 {len(fails)} 次，最多的是 {fail_codes.most_common(1)[0][0]}")
    return {
        "day": day.isoformat(),
        "sent": len(sent),
        "fails": len(fails),
        "duplicates": dups,
        "identity_conflicts": [s["text"][:40] for s in identity],
        "per_hour": dict(sorted(per_hour.items())),
        "busiest_hour": busiest,
        "fail_codes": dict(fail_codes.most_common(5)),
        "problems": problems,
        "healthy": not problems,
    }


def publish_targets() -> dict[str, str]:
    """各平台能不能发视频（如实写，不假装）。"""
    return {
        "bilibili": "ok",          # bili_client.upload_video 已实现（真机需 cookie）
        "douyin": "unsupported",   # 抖音网页端没有视频投稿入口，只有评论/私信/点赞
    }
