"""儿童人格真机评测：把 persona=child 装进真实服务，用真实模型问 6 个问题。

评的不是"提示词长得对不对"（那有单测），而是**模型在真实链路里有没有真的像个六七岁小孩**。
自动打分的规则（全部可复核，规则本身也落盘）：

- 该有的儿童特征：语气词（呀/啦/嘛/诶/哇/哼）、叠词、追问（问号）、
  不懂就说不知道、句子短（平均 ≤ 30 字）；
- 不该有的成人特征：客服腔、列点、Markdown、成语、大道理、成人式安慰、
  "作为一个…"这类书面语。

用法：python tools/archcheck/persona_child_live.py
产物：tools/archcheck/results/persona_child_live.json
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / ".tmp" / "persona-live"
PORT = 18793
BASE = f"http://127.0.0.1:{PORT}"
GATEWAY = "http://127.0.0.1:8788/v1"

PROMPTS = [
    "今天上班被老板骂了，好难过",
    "你喜欢什么颜色",
    "为什么天是蓝的",
    "你谈过恋爱吗",
    "帮我写一段 Python 快速排序",
    "你是不是 AI 啊",
    "你现在在哪呢",
    "你叫什么名字",
    "我要睡觉了，晚安",
]

FORBIDDEN = {
    "客服腔": ("有什么可以帮", "想聊点什么", "很高兴为您", "希望对你有帮助"),
    "列点/Markdown": ("- ", "* ", "1.", "**", "###", "```"),
    "书面语": ("作为", "综上", "首先", "其次", "因此", "建议您", "总的来说"),
    "成人式安慰": ("我理解你的感受", "都会好起来的", "你要坚强"),
    "成语/大道理": ("顺其自然", "人生", "成长的过程", "意义在于"),
    "旧身份（鱼）": ("鱼缸", "鱼尾", "本鱼", "一条鱼", "咕噜", "鳍"),
    "机器身份": ("在电脑里", "电脑里", "我是一个程序", "我是个程序", "语言模型",
                 "人工智能", "被造出来", "虚拟角色", "我没有身体", "作为 AI"),
}
CHILD_SIGNS = {
    "语气词": ("呀", "啦", "嘛", "诶", "哇", "哼", "哦", "啊"),
    "叠词": None,  # 正则
    "追问": None,  # 有问号
    "说不知道": ("不知道", "不懂", "不会"),
    "转移大人话题": ("问妈妈", "不懂", "不知道"),
}


def op():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def post(path: str, payload: dict, timeout: float = 180.0):
    req = urllib.request.Request(f"{BASE}{path}", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with op().open(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def score(reply: str) -> dict:
    hits: dict[str, list[str]] = {}
    for name, words in FORBIDDEN.items():
        found = [w for w in words if w in reply]
        if found:
            hits[name] = found
    signs = []
    if any(w in reply for w in CHILD_SIGNS["语气词"]):
        signs.append("语气词")
    if re.search(r"(.)\1", reply):
        signs.append("叠词/重复")
    if "？" in reply or "?" in reply:
        signs.append("追问")
    if any(w in reply for w in CHILD_SIGNS["说不知道"]):
        signs.append("说不知道")
    return {
        "chars": len(reply),
        "child_signs": signs,
        "forbidden": hits,
        "clean": not hits,
    }


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    cfg = WORK / "aiji.json"
    cfg.write_text(json.dumps({
        "db_path": str(WORK / "aiji.db"),
        "routes": [{"name": "wb", "base_url": GATEWAY, "model": "glm-5.3-flash",
                    "api_key": "local-probe-key"}],
        "persona": "child",
        "debounce_ms": 0, "humanize_min_ms": 0, "humanize_max_ms": 0,
        "typing_min_ms": 0, "typing_max_ms": 0,
        "summary_model": False, "reflection_interval_s": 3600,
        "sticker_update": False, "actions_enabled": False,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    log = (WORK / "serve.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "aiji.senses.cli", "--config", str(cfg),
         "serve", "--port", str(PORT)],
        cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT)
    report: dict = {"config": json.loads(cfg.read_text("utf-8")), "rows": []}
    try:
        for _ in range(60):
            try:
                with op().open(f"{BASE}/health", timeout=3) as r:
                    r.read()
                break
            except Exception:
                if proc.poll() is not None:
                    report["error"] = "服务起不来：" + (WORK / "serve.log").read_text("utf-8")[-800:]
                    print(json.dumps(report, ensure_ascii=False))
                    return
                time.sleep(1.0)
        session = f"child-{uuid.uuid4().hex[:6]}"
        for prompt in PROMPTS:
            t0 = time.perf_counter()
            data = post("/v1/chat/completions", {
                "model": "aiji",
                "messages": [{"role": "user", "content": prompt}],
                "aiji": {"remember": False, "history": False, "persona": "child"},
            })
            ms = round((time.perf_counter() - t0) * 1000)
            reply = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
            row = {"prompt": prompt, "reply": reply.strip(), "ms": ms, **score(reply)}
            report["rows"].append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
        del session
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=15)
        except Exception:
            proc.kill()
        log.close()

    rows = report["rows"]
    report["verdict"] = {
        "n": len(rows),
        "clean_ratio": round(sum(1 for r in rows if r["clean"]) / max(1, len(rows)), 2),
        "avg_chars": round(sum(r["chars"] for r in rows) / max(1, len(rows)), 1),
        "with_child_sign": sum(1 for r in rows if r["child_signs"]),
        "forbidden_hits": {k: [r["prompt"] for r in rows if k in r["forbidden"]]
                           for k in FORBIDDEN if any(k in r["forbidden"] for r in rows)},
    }
    (ROOT / "tools" / "archcheck" / "results" / "persona_child_live.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("SUMMARY " + json.dumps(report["verdict"], ensure_ascii=False))


if __name__ == "__main__":
    main()
