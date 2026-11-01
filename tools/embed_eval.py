# -*- coding: utf-8 -*-
"""HybridIndex（hashing+LLM 软嵌入）vs BruteForceIndex（纯 hashing）检索质量对比。

评测核心：**改写检索**——query 与 fact 字面不同但语义相同，
hashing（字符 n-gram）在这上面是已知弱项（vectors.py 注释自认"弱语义"）。
"""
import io
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from tools.load_keys import bridge  # noqa: E402
bridge()
import os  # noqa: E402

from aiji.config import Route  # noqa: E402
from aiji.pool import ModelPool  # noqa: E402
from aiji.vectors import BruteForceIndex, HybridIndex, llm_dim_embed  # noqa: E402

# 语料：fact 库（10 条）+ 5 个改写 query（正确答案 id + 干扰项）
FACTS = [
    (1, "用户叫陈明，住在深圳南山区"),
    (2, "在后端开发组工作五年了"),
    (3, "养了一只橘猫叫橘子，三岁"),
    (4, "周末喜欢钓鱼和摄影"),
    (5, "最近在学自由泳"),
    (6, "不吃香菜"),
    (7, "写代码用 vim"),
    (8, "上周见了大学同学张伟"),
    (9, "张伟在杭州做产品经理"),
    (10, "下周日和同事去北高峰爬山"),
]
# (query, 正确 fact id)——query 全部是改写，不含 fact 原词的核心字面
QUERIES = [
    ("他在哪个城市居住啊", 1),          # 住→居住，深圳南山区零字面重叠（城市二字在 fact 无）
    ("他做开发多少年了", 2),            # 后端→开发
    ("他家宠物几岁了", 3),              # 猫→宠物
    ("平时有什么消遣", 4),              # 钓鱼摄影→消遣
    ("约了谁去登山", 10),               # 爬山→登山
]

routes = []
for name, base, model, key_env in (
    ("workbuddy", "http://127.0.0.1:8788/v1", "glm-5.3-flash", "WORKBUDDY_HUB_KEY"),
    ("agent2api", "http://127.0.0.1:3065/v1", "glm-5.3-flash", "AGENT2API_KEY"),
):
    k = os.environ.get(key_env, "")
    if k:
        routes.append(Route(name=name, base_url=base, model=model, api_key=k, timeout_s=30))
pool = ModelPool(tuple(routes))


def chat(messages):
    return pool.chat(messages, timeout_s=15.0).text


t0 = time.time()
h_idx = BruteForceIndex()
for fid, text in FACTS:
    h_idx.upsert(fid, text, {"text": text})
x_idx = HybridIndex(chat=chat)
for fid, text in FACTS:
    x_idx.upsert(fid, text, {"text": text})
build_s = round(time.time() - t0, 1)

h_hits = x_hits = 0
print(f"== 检索对比（索引构建 {build_s}s，LLM 维度 {len(FACTS)} 条）==")
for q, want in QUERIES:
    h_top = h_idx.search(q, k=3)
    x_top = x_idx.search(q, k=3)
    h_ok = h_top and h_top[0][0] == want
    x_ok = x_top and x_top[0][0] == want
    h_hits += h_ok
    x_hits += x_ok
    h_ids = [(i, round(s, 2)) for i, s, _ in h_top]
    x_ids = [(i, round(s, 2)) for i, s, _ in x_top]
    print(f"\nQ: {q}  (want #{want})")
    print(f"  hashing top3: {h_ids} {'✓' if h_ok else '✗'}")
    print(f"  hybrid  top3: {x_ids} {'✓' if x_ok else '✗'}")

print(f"\n== 汇总：hashing {h_hits}/{len(QUERIES)}  vs  hybrid {x_hits}/{len(QUERIES)} ==")
