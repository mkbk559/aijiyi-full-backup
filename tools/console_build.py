"""从架构图生成控制台页面（单一数据源：tools/archcheck/results/nodes.json）。

控制台以前是手写的 4 个视图（概览/功能/配置/生命周期），和 `docs/architecture.html`
的九大系统对不上。现在改成"架构图怎么分，控制台就怎么分"：
左栏 9 个身体系统 → 每个系统一页 = 该系统在架构图里的节点 + 真实 API 面板 + 动作。

用法：
    python tools/console_build.py              # 生成 aiji/right_hand/web/operations.html
    python tools/console_build.py --check      # 只校验不写文件（配合 console_check.py）

nodes.json 由 tools/archcheck/dump_bands.py 从 docs/architecture.html 抽出，
架构图改了要重新生成一次。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NODES = ROOT / "tools" / "archcheck" / "results" / "nodes.json"
TARGET = ROOT / "aiji" / "right_hand" / "web" / "operations.html"

# 每个系统页：要看的真实接口 + 能做的动作（路径必须存在于 server.py 路由表）
SYSTEMS = [
    {
        "id": "entry", "nav": "① 感官",
        "title": "感官 · 五官", "sub": "外部世界怎么进来（眼耳口）",
        "panels": [["累计数据", "/stats"], ["最近对话", "/api/recent-turns"],
                   ["能力清单", "/api/capabilities"]],
        "acts": [["手动投递一条文本消息", "POST", "/ingest",
                  [["kind", "text"], ["text", "今天在北京见到老同学"]]],
                 ["按轮次写入", "POST", "/turns", [["content", "用户在杭州工作"]]]],
    },
    {
        "id": "ingress", "nav": "② 咽喉",
        "title": "咽喉 · 神经通路", "sub": "所有入口汇成一条咽喉",
        "panels": [["健康", "/health"], ["OneBot 接入", "/api/integrations/onebot"],
                   ["能力清单", "/api/capabilities"]],
        "acts": [["走一次网关入站", "POST", "/v1/gateway/messages",
                  [["content", "网关测试消息"], ["platform", "wechat"],
                   ["user", "u1"], ["scope", "personal"]]]],
    },
    {
        "id": "orch", "nav": "③ 大脑",
        "title": "大脑 · 皮层六区", "sub": "一次对话怎么被想出来",
        "panels": [["模型路由", "/api/models"], ["自我认知", "/api/self"],
                   ["功能清单", "/api/operations/features"], ["主动消息", "/api/proactive"]],
        "acts": [],
    },
    {
        "id": "memory", "nav": "④ 海马体",
        "title": "记忆 · 海马体", "sub": "写不丢 · 读有预算 · 注入不越权",
        "panels": [["记忆条目", "/api/memories"], ["记忆图谱", "/api/memory-graph"],
                   ["注入记录", "/api/injections"], ["运行统计", "/stats"]],
        "acts": [["记一条", "POST", "/remember", [["text", "用户喜欢喝美式"]]],
                 ["召回", "POST", "/recall", [["text", "用户喝什么"]]],
                 ["注入预览", "POST", "/inject", [["text", "用户喝什么"]]],
                 ["反馈一条", "POST", "/feedback", [["note", "这条注入有用"]]]],
    },
    {
        "id": "life", "nav": "⑤ 内分泌",
        "title": "内分泌 · 生命腺", "sub": "状态、体检、主动与存在感",
        "panels": [["生命状态", "/api/life"], ["主动消息", "/api/proactive"],
                   ["定时任务", "/api/scheduler"], ["体检报告", "/api/being/checkup"]],
        "acts": [["跑一次真机体检（会调模型，约 30 s）", "POST", "/api/being/checkup/live",
                  [["refresh", "true"]]]],
    },
    {
        "id": "out", "nav": "⑥ 心脏",
        "title": "心脏 · 循环泵", "sub": "从队列到真实送达",
        "panels": [["声线", "/v1/voices"], ["表情包", "/api/stickers"],
                   ["社交", "/api/social"], ["点歌任务", "/v1/songs"]],
        "acts": [["合成一条语音", "POST", "/v1/audio/speech", [["input", "这条是控制台合成的"]]],
                 ["切换声线", "POST", "/v1/voices/use", [["profile", "dafeiyu"]]],
                 ["刷新表情包", "POST", "/api/stickers/update", []],
                 ["发一条空间说说", "POST", "/api/social/qzone/publish",
                  [["content", "今天状态不错"]]]],
    },
    {
        "id": "tools", "nav": "⑦ 左手",
        "title": "左手 · 动作", "sub": "受控的动作与联网",
        "panels": [["MCP 工具", "/api/mcp"], ["待审批动作", "/api/actions/pending"],
                   ["动作统计", "/api/actions/stats"]],
        "acts": [["触发一次 MCP 研究", "POST", "/api/mcp/research", []]],
    },
    {
        "id": "ops", "nav": "⑧ 右手",
        "title": "右手 · 控制", "sub": "看得见、改得动、起停得住",
        "panels": [["服务链路", "/api/operations/services"],
                   ["功能清单", "/api/operations/features"],
                   ["当前配置", "/api/operations/config"], ["工作区", "/api/fs"]],
        "acts": [["启动整套服务", "POST", "/api/operations/services", [["action", "start"]]],
                 ["停止整套服务", "POST", "/api/operations/services", [["action", "stop"]]],
                 ["写一个工作区文件", "POST", "/api/fs/write",
                  [["path", "notes/hello.txt"], ["content", "hello"]]],
                 ["排空写入队列", "POST", "/flush", []]],
    },
    {
        "id": "data", "nav": "⑨ 骨架",
        "title": "骨架 · 双脚地基", "sub": "SQLite、素材、模型与账号",
        "panels": [["运行统计", "/stats"], ["服务链路", "/api/operations/services"],
                   ["媒体", "/media"], ["健康", "/health"]],
        "acts": [],
    },
]

EXTRA_VIEWS = [
    {"id": "settings", "nav": "⚙ 配置", "title": "功能配置",
     "sub": "改这里等于改 config；保存后重启整套服务生效",
     "panels": [["当前配置", "/api/operations/config"]],
     "acts": [["保存改动（JSON: {\"changes\":{...}}）", "POST", "/api/operations/config",
               [["changes", "{}"]]]]},
    {"id": "lifecycle", "nav": "⏻ 生命周期", "title": "从启动到结束",
     "sub": "按依赖顺序看每一项服务，并在这里启停",
     "panels": [["服务链路", "/api/operations/services"]],
     "acts": [["启动整套", "POST", "/api/operations/services", [["action", "start"]]],
              ["停止整套", "POST", "/api/operations/services", [["action", "stop"]]]]},
]

TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AIJiYi · 九大系统运行簿</title>
<style>
*{box-sizing:border-box}
body{margin:0;font:15px/1.55 "Segoe UI","Microsoft YaHei",system-ui,sans-serif;color:#12212b;background:#eef3f5}
.shell{display:grid;grid-template-columns:236px minmax(0,1fr);min-height:100vh}
.rail{background:#14293a;color:#d7e6ec;padding:22px 16px;display:flex;flex-direction:column;gap:14px;position:sticky;top:0;height:100vh;overflow:auto}
.identity{display:flex;gap:10px;align-items:center}
.logo{width:36px;height:36px;border-radius:10px;background:#2f9c95;color:#fff;display:grid;place-items:center;font-weight:700}
.identity strong{display:block;font-size:15px}
.identity small{color:#8fb0bf;font-size:11px;letter-spacing:.08em}
.ctx{display:grid;gap:6px;padding:10px;border-radius:10px;background:#1b3446}
.ctx label{font-size:11px;color:#8fb0bf}
.ctx input{width:100%;padding:5px 7px;border-radius:7px;border:1px solid #2b4a5e;background:#0f2231;color:#dff0f5;font-size:12px}
.nav{display:flex;flex-direction:column;gap:4px}
.nav button{all:unset;cursor:pointer;padding:9px 11px;border-radius:9px;font-size:13.5px;color:#c7dbe4;display:flex;justify-content:space-between;gap:8px}
.nav button:hover{background:#1d3a4d}
.nav button.active{background:#2f9c95;color:#fff;font-weight:600}
.nav .sep{margin:10px 4px 4px;font-size:10.5px;letter-spacing:.14em;color:#6d8fa0}
.rail-foot{margin-top:auto;display:grid;gap:8px}
.rail-foot button{all:unset;cursor:pointer;text-align:center;padding:9px;border-radius:9px;background:#2f9c95;color:#fff;font-size:13px}
.rail-foot .chip{font-size:11.5px;color:#9fc0cf;text-align:center}
main{padding:26px 30px 60px;min-width:0}
.page-head{display:flex;justify-content:space-between;gap:20px;align-items:flex-end;margin-bottom:18px;flex-wrap:wrap}
.eyebrow{font-size:11px;letter-spacing:.16em;color:#5b7d8c}
h1{margin:4px 0 6px;font-size:26px}
.page-head p{margin:0;color:#4d6a78;max-width:70ch}
.node-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(228px,1fr));gap:10px;margin:14px 0 22px}
.node{background:#fff;border:1px solid #dbe6ea;border-left:4px solid #2f9c95;border-radius:11px;padding:11px 13px}
.node b{display:block;font-size:14px}
.node small{color:#5b7d8c;font-size:12px;display:block;margin:2px 0 6px}
.node span{font-size:11.5px;color:#7d95a1;font-family:ui-monospace,Consolas,monospace;word-break:break-all}
.panel{background:#fff;border:1px solid #dbe6ea;border-radius:12px;padding:14px 16px;margin-bottom:12px}
.panel h3{margin:0 0 10px;font-size:14.5px;display:flex;justify-content:space-between;align-items:center;gap:8px}
.panel h3 em{font-style:normal;font-size:11.5px;color:#7d95a1;font-weight:400}
.kv{display:grid;gap:3px;font-size:12.5px;max-height:260px;overflow:auto}
.kv div{display:grid;grid-template-columns:minmax(90px,28%) 1fr;gap:8px;padding:3px 0;border-bottom:1px dashed #eef4f6}
.kv b{font-weight:600;color:#33525f;word-break:break-all}
.kv span{color:#5b7d8c;word-break:break-word;white-space:pre-wrap}
.muted{color:#8aa3ae;font-size:12.5px}
.act{background:#fff;border:1px solid #dbe6ea;border-radius:12px;padding:12px 14px;margin-bottom:10px}
.act .row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.act input{flex:1 1 140px;min-width:110px;padding:6px 8px;border:1px solid #cfdde2;border-radius:8px;font-size:12.5px}
.act button{all:unset;cursor:pointer;padding:7px 14px;border-radius:8px;background:#14293a;color:#fff;font-size:12.5px}
.act button:hover{background:#235265}
.act pre{margin:8px 0 0;padding:9px;border-radius:8px;background:#0f2231;color:#bfe3ea;font-size:11.5px;max-height:200px;overflow:auto}
.band-color-1{border-left-color:#2f9c95}.band-color-2{border-left-color:#3d7ea6}.band-color-3{border-left-color:#7a68ad}
.band-color-4{border-left-color:#c1813b}.band-color-5{border-left-color:#c05a6a}.band-color-6{border-left-color:#b3464f}
.band-color-7{border-left-color:#4f8f6a}.band-color-8{border-left-color:#4a6d8c}.band-color-9{border-left-color:#6b6f77}
#toast{position:fixed;right:22px;bottom:22px;padding:10px 16px;border-radius:10px;background:#14293a;color:#fff;font-size:13px;opacity:0;transform:translateY(8px);transition:.18s;pointer-events:none}
#toast.show{opacity:1;transform:none}
@media(max-width:820px){.shell{display:block}.rail{height:auto;position:static}main{padding:18px}}
</style>
</head>
<body>
<div class="shell">
  <aside class="rail">
    <div class="identity"><div class="logo">AI</div><div><strong>AIJiYi</strong><small>九大系统 · 运行簿</small></div></div>
    <div class="ctx">
      <label for="ctx-platform">platform</label><input id="ctx-platform" value="local">
      <label for="ctx-user">platform_user_id</label><input id="ctx-user" value="default">
      <label for="ctx-scope">scope</label><input id="ctx-scope" value="default">
      <label for="ctx-session">session</label><input id="ctx-session" value="default">
    </div>
    <nav class="nav" id="nav"></nav>
    <div class="rail-foot">
      <button id="refresh">刷新当前系统</button>
      <div class="chip" id="health-chip">—</div>
    </div>
  </aside>
  <main>
    <div class="page-head">
      <div>
        <div class="eyebrow" id="page-eyebrow">AIJIYI OPERATIONS</div>
        <h1 id="page-title">感官 · 五官</h1>
        <p id="page-subtitle"></p>
      </div>
      <div class="muted" id="page-meta"></div>
    </div>
    <div id="band-nodes"></div>
    <div id="panels"></div>
    <div id="actions"></div>
  </main>
</div>
<div id="toast"></div>
<script>
const SYSTEMS = __SYSTEMS__;
const NODES = __NODES__;
const EXTRA = __EXTRA__;
const ALL = SYSTEMS.concat(EXTRA);
const $ = id => document.getElementById(id);
const state = {view: null, key: sessionStorage.getItem('aiji-key') || ''};
const ORIGIN = location.protocol === 'file:' ? 'http://127.0.0.1:8787' : '';
const net = p => p.startsWith('http') ? p : ORIGIN + p;
const ctx = () => ({
  platform: $('ctx-platform').value.trim() || 'local',
  platform_user_id: $('ctx-user').value.trim() || 'default',
  scope: $('ctx-scope').value.trim() || 'default',
  session: $('ctx-session').value.trim() || 'default',
});
const CTX_PATHS = new Set(['/api/memories','/api/memory-graph','/api/self','/api/recent-turns',
  '/api/injections','/api/life','/style','/media','/api/being/checkup']);
function contextual(p) {
  if (!CTX_PATHS.has(p)) return p;
  return p + '?' + new URLSearchParams(ctx()).toString();
}
function toast(msg, bad) {
  const n = $('toast'); n.textContent = msg;
  n.style.background = bad ? '#a63e46' : '#14293a';
  n.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => n.classList.remove('show'), 4200);
}
async function api(path, opt = {}) {
  const headers = {'Content-Type': 'application/json'};
  if (state.key) headers.Authorization = 'Bearer ' + state.key;
  let r;
  try { r = await fetch(net(path), {...opt, headers}); }
  catch (e) { throw new Error('连不上本地服务 ' + (ORIGIN || 'http://127.0.0.1:8787')); }
  let data = null; try { data = await r.json(); } catch (e) { data = null; }
  if (r.status === 401) {
    const key = prompt('此服务需要访问密钥（AIJI_API_KEY）：');
    if (key) { state.key = key; sessionStorage.setItem('aiji-key', key); return api(path, opt); }
  }
  if (!r.ok) {
    const msg = (data && (data.error?.message || data.error || data.code)) || ('HTTP ' + r.status);
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
  }
  return data === null ? {} : data;
}
const plain = v => v === null ? '—' : typeof v === 'object' ? JSON.stringify(v, null, 2) : String(v);
function kv(data) {
  const box = document.createElement('div'); box.className = 'kv';
  const entries = Object.entries(data || {});
  if (!entries.length) { box.className = 'muted'; box.textContent = '当前没有记录'; return box; }
  for (const [k, v] of entries.slice(0, 80)) {
    const row = document.createElement('div');
    const b = document.createElement('b'); b.textContent = k;
    const s = document.createElement('span'); s.textContent = plain(v);
    row.append(b, s); box.append(row);
  }
  return box;
}
function panel(spec) {
  const [title, path] = spec;
  const box = document.createElement('section'); box.className = 'panel';
  const h = document.createElement('h3');
  h.textContent = title;
  const em = document.createElement('em'); em.textContent = 'GET ' + path;
  h.append(em); box.append(h);
  const body = document.createElement('div'); body.className = 'muted'; body.textContent = '读取中…';
  box.append(body);
  api(contextual(path)).then(d => body.replaceWith(kv(d)))
    .catch(e => { body.className = 'muted'; body.textContent = '读不到：' + e.message; });
  return box;
}
function action(spec) {
  const [label, method, path, fields] = spec;
  const box = document.createElement('section'); box.className = 'act';
  const row = document.createElement('div'); row.className = 'row';
  const inputs = {};
  for (const [name, value] of fields) {
    const i = document.createElement('input');
    i.value = value; i.placeholder = name; i.setAttribute('aria-label', name);
    i.dataset.field = name; inputs[name] = i; row.append(i);
  }
  const btn = document.createElement('button'); btn.textContent = label; row.append(btn);
  const out = document.createElement('pre'); out.style.display = 'none';
  btn.onclick = async () => {
    btn.disabled = true; const t0 = performance.now();
    const payload = {};
    for (const [name, i] of Object.entries(inputs)) {
      let v = i.value;
      if (v === 'true') v = true; else if (v === 'false') v = false;
      else if (v !== '' && !isNaN(Number(v)) && name !== 'text' && name !== 'content'
               && name !== 'input' && name !== 'note' && name !== 'content') v = v;
      payload[name] = v;
    }
    try {
      const d = await api(path, {method, body: JSON.stringify(payload)});
      out.style.display = 'block';
      out.textContent = '✓ ' + Math.round(performance.now() - t0) + ' ms\n' + JSON.stringify(d, null, 2);
      toast(label + ' 成功');
    } catch (e) {
      out.style.display = 'block'; out.textContent = '✗ ' + e.message;
      toast(label + ' 失败：' + e.message, true);
    } finally { btn.disabled = false; }
  };
  box.append(row, out);
  return box;
}
function render(id) {
  const sys = ALL.find(s => s.id === id) || ALL[0];
  state.view = sys.id;
  document.querySelectorAll('#nav button').forEach(n => n.classList.toggle('active', n.dataset.view === sys.id));
  $('page-title').textContent = sys.title;
  $('page-subtitle').textContent = sys.sub;
  $('page-eyebrow').textContent = sys.id === 'settings' || sys.id === 'lifecycle'
    ? 'AIJIYI OPERATIONS' : 'SYSTEM ' + sys.nav;
  $('page-meta').textContent = '';
  const nodes = NODES[sys.id] || [];
  const host = $('band-nodes'); host.replaceChildren();
  if (nodes.length) {
    const grid = document.createElement('div'); grid.className = 'node-grid';
    const idx = SYSTEMS.findIndex(s => s.id === sys.id) + 1;
    for (const n of nodes) {
      const c = document.createElement('div');
      c.className = 'node band-color-' + (idx || 1);
      const b = document.createElement('b'); b.textContent = n.t;
      const s = document.createElement('small'); s.textContent = n.s || '';
      const f = document.createElement('span'); f.textContent = n.f || '';
      c.append(b, s, f); grid.append(c);
    }
    host.append(grid);
  }
  const panels = $('panels'); panels.replaceChildren(...(sys.panels || []).map(panel));
  const acts = $('actions'); acts.replaceChildren();
  for (const a of (sys.acts || [])) acts.append(action(a));
  history.replaceState(null, '', '#' + sys.id);
}
async function health() {
  try {
    const d = await api('/health');
    $('health-chip').textContent = '● ' + (d.model || '在线') + ' · ' + (d.version || '');
    $('health-chip').style.color = '#8fe3c8';
  } catch (e) {
    $('health-chip').textContent = '○ 核心未连接';
    $('health-chip').style.color = '#e58c93';
  }
}
(function boot() {
  const nav = $('nav');
  SYSTEMS.forEach(s => {
    const b = document.createElement('button');
    b.dataset.view = s.id;
    b.innerHTML = '<span>' + s.nav + '</span><span>' + (NODES[s.id] || []).length + '</span>';
    b.onclick = () => render(s.id);
    nav.append(b);
  });
  const sep = document.createElement('div'); sep.className = 'sep'; sep.textContent = '运维';
  nav.append(sep);
  EXTRA.forEach(s => {
    const b = document.createElement('button');
    b.dataset.view = s.id; b.innerHTML = '<span>' + s.nav + '</span>';
    b.onclick = () => render(s.id);
    nav.append(b);
  });
  $('refresh').onclick = () => render(state.view || SYSTEMS[0].id);
  const want = (location.hash || '').replace('#', '');
  render(ALL.some(s => s.id === want) ? want : SYSTEMS[0].id);
  health();
})();
</script>
</body>
</html>
"""


def build_spec():
    data = json.loads(NODES.read_text(encoding="utf-8"))
    nodes: dict[str, list[dict]] = {}
    for node in data["NODES"]:
        band = node.get("band")
        nodes.setdefault(band, []).append({
            "t": node.get("t", ""), "s": node.get("s", ""), "f": node.get("f", ""),
        })
    return nodes


def render(nodes: dict[str, list[dict]]) -> str:
    return (TEMPLATE
            .replace("__SYSTEMS__", json.dumps(SYSTEMS, ensure_ascii=False))
            .replace("__EXTRA__", json.dumps(EXTRA_VIEWS, ensure_ascii=False))
            .replace("__NODES__", json.dumps(nodes, ensure_ascii=False)))


def main(argv: list[str]) -> int:
    nodes = build_spec()
    html = render(nodes)
    missing = [s["id"] for s in SYSTEMS if not nodes.get(s["id"])]
    total = sum(len(v) for v in nodes.values())
    if "--check" not in argv:
        TARGET.write_text(html, encoding="utf-8")
    print(json.dumps({"target": str(TARGET), "bytes": len(html.encode("utf-8")),
                      "bands": len(nodes), "nodes": total,
                      "bands_without_nodes": missing}, ensure_ascii=False))
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
