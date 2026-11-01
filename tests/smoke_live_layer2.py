# -*- coding: utf-8 -*-
"""LLM 第二层真实验证：完整闭环走本机网关（workbuddy/agent2api）。

这是 AIJiYi 第一次在真实模型池上验证 writer 的全链路：
  turn → pool.chat（模型抽取）→ apply_extraction → fact 落库
  → summary_model 路径（模型摘要，非 heuristic）
  → recall/inject → 反馈回写

不是断言测试（依赖网关在线），失败就如实打印。
"""
import io
import json
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WORK = ROOT / ".tmp"
WORK.mkdir(mode=0o777, exist_ok=True)
db = WORK / "live_layer2.db"
if db.exists():
    db.unlink()

from aiji import Memory  # noqa: E402

out = {"steps": []}


def step(name: str, ok: bool, detail: str = "") -> None:
    out["steps"].append({"name": name, "ok": ok, "detail": detail})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))


t0 = time.time()
m = Memory.open(path=str(db))
routes = [r.name for r in m.settings.routes]
print(f"== LLM 第二层真实验验（routes={routes}） ==")

# ① 模型抽取链路：一句含多条事实+一条关系
text1 = "我叫陈明，住在深圳，在南山区做后端开发，我养了只猫叫橘子。"
m.turn(("wechat", "u_live"), "s1", "user", text1)
ok1 = m.flush(60)
rows = m.db.read(
    "SELECT predicate, object, source_kind, kind FROM fact WHERE platform_user_id='u_live'"
    " AND status='active'")
preds = {r["predicate"] for r in rows}
src_kinds = {r["source_kind"] for r in rows}
step("模型抽取落库（≥3 条 fact）", len(rows) >= 3, f"rows={len(rows)} preds={preds}")
step("source_kind=model（真实模型路径，非规则兜底）", "model" in src_kinds, f"kinds={src_kinds}")

# ② 模型关系抽取（猫叫橘子 → relation/知识）
step("关系/宠物类信息被抽到", any(p in preds for p in ("宠物", "关系", "备注")) or
     any(r["kind"] in ("relation", "knowledge") for r in rows), f"rows={[(r['predicate'], r['kind']) for r in rows]}")

# ③ 模型摘要路径：推过 summary_every（临时调小）
m.settings.summary_every = 3
for i in range(4):
    m.turn(("wechat", "u_live"), "s1", "user", f"今天聊了第{i}个话题，我说了我喜欢钓鱼和摄影。")
    m.flush(30)
    time.sleep(0.2)
sm = m.db.read(
    "SELECT text, extra FROM summary WHERE platform_user_id='u_live' ORDER BY id DESC LIMIT 1")
if sm:
    extra = json.loads(sm[0]["extra"])
    is_model = extra.get("source_kind") == "model"
    step("模型摘要产出（source_kind=model）", is_model, f"extra={extra}")
    step("摘要内容非空且像摘要", len(sm[0]["text"]) > 10, f"text={sm[0]['text'][:80]}")
    out["summary_sample"] = sm[0]["text"][:200]
else:
    step("摘要产出", False, "无 summary 行")

# ④ 召回 + 注入（读路径不受模型影响，但验证 fact 来自模型抽取）
res = m.recall("他住在哪", ("wechat", "u_live"))
hit = any("深圳" in h.text for h in res.hits)
step("召回命中模型抽取的事实（深圳）", hit,
     f"hits={[h.text[:30] for h in res.hits]}")
inj = m.inject("他是谁", ("wechat", "u_live"), session="s1")
step("注入块含事实", any("深圳" in i.text or "陈明" in i.text for i in inj.items),
     f"items={[i.text[:30] for i in inj.items]}")

# ⑤ 反馈回写
if inj.items:
    m.feedback(("wechat", "u_live"), inj.injection_id, [i.id for i in inj.items if i.id][:3])
    fb = m.db.read("SELECT COUNT(*) c FROM injection WHERE feedback IS NOT NULL")[0]["c"]
    step("反馈回写留痕", fb >= 1, f"with_fb={fb}")

out["stats"] = {
    "model_ok": m.stats()["writer"]["model_ok"],
    "heuristic": m.stats()["writer"]["heuristic"],
    "summaries": m.stats()["writer"]["summaries"],
    "summary_model": m.stats()["writer"]["summary_model"],
    "pool": m.stats()["pool"],
}
out["total_s"] = round(time.time() - t0, 1)
n_ok = sum(1 for s in out["steps"] if s["ok"])
print(f"\n== 结果：{n_ok}/{len(out['steps'])}；model_ok={out['stats']['model_ok']} "
      f"summary_model={out['stats']['summary_model']}；耗时 {out['total_s']}s")
(WORK / "live_layer2_report.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"报告：{WORK / 'live_layer2_report.json'}")
sys.exit(0 if n_ok == len(out["steps"]) else 1)
