// DOM 桩里跑 docs/architecture.html 的真实内联脚本，做机器断言。
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const dir = path.join(__dirname, "results");
fs.mkdirSync(dir, {recursive: true});
const code = fs.readFileSync(path.join(dir, "arch0.js"), "utf8");

class ClassList {
  constructor() { this.s = new Set(); }
  add(...c) { c.forEach(x => this.s.add(x)); }
  remove(...c) { c.forEach(x => this.s.delete(x)); }
  toggle(c, force) { const f = force === undefined ? !this.s.has(c) : !!force; f ? this.s.add(c) : this.s.delete(c); return f; }
  contains(c) { return this.s.has(c); }
  toArray() { return [...this.s]; }
}
class El {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase(); this.attrs = {}; this.children = []; this.classList = new ClassList();
    this.style = {}; this.dataset = {}; this._text = ""; this._html = ""; this.onclick = null; this._l = {};
    this.parent = null;
  }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === "class") String(v).split(/\s+/).filter(Boolean).forEach(c => this.classList.add(c)); }
  getAttribute(k) { return this.attrs[k]; }
  appendChild(c) { this.children.push(c); if (c) c.parent = this; return c; }
  addEventListener(ev, fn) { (this._l[ev] = this._l[ev] || []).push(fn); }
  removeEventListener() {}
  set textContent(v) { this._text = String(v); } get textContent() { return this._text; }
  set innerHTML(v) { this._html = String(v); } get innerHTML() { return this._html; }
  querySelector() { return new El("div"); }
  querySelectorAll() { return []; }
  getBoundingClientRect() { return { left: 0, top: 0, width: 1600, height: 900, right: 1600, bottom: 900 }; }
  setPointerCapture() {} releasePointerCapture() {}
  createSVGPoint() { return { x: 0, y: 0, matrixTransform(m) { return { x: this.x, y: this.y }; } }; }
}

const byId = {};
const doc = {
  getElementById(id) { return byId[id] || (byId[id] = new El("div")); },
  createElementNS(ns, tag) { return new El(tag); },
  createElement(tag) { return new El(tag); },
  addEventListener() {}, body: new El("body"),
};
const win = { addEventListener() {}, location: { hash: "", pathname: "/docs/architecture.html", search: "" }, history: { replaceState() {} } };
const ctx = {
  document: doc, window: win, location: win.location, history: win.history,
  console, setTimeout, clearTimeout, Math, Date, JSON, Set, Map, String, Number, Object, Array, RegExp, Boolean,
};
ctx.globalThis = ctx;
ctx.window.document = doc;

const epilogue = `
;globalThis.__P = { BANDS, NODES, EDGES, pos, nodeEl, edgeEl, adj, MAIN, pathFor,
  pin, apply, view, offBands, qInput, bandOf,
  setQuery(v){ query = v; }, getQuery(){ return query; },
  setMain(v){ mainMode = v; }, getMain(){ return mainMode; },
  getPinned(){ return pinned; }, readHash };
`;
vm.createContext(ctx);
vm.runInContext(code + epilogue, ctx, { filename: "architecture-inline.js" });

const P = ctx.__P;
const out = [];
let pass = 0, fail = 0;
function check(name, ok, detail) {
  if (ok) { pass++; out.push(`PASS  ${name}${detail ? "  " + detail : ""}`); }
  else { fail++; out.push(`FAIL  ${name}${detail ? "  " + detail : ""}`); }
}

// ── 1. 数据规模 ──
check("BANDS == 9", P.BANDS.length === 9, `实际 ${P.BANDS.length}`);
check("NODES == 68", P.NODES.length === 68, `实际 ${P.NODES.length}`);
check("EDGES 原始条数", P.EDGES.length === 88, `实际 ${P.EDGES.length}`);
const ids = P.NODES.map(n => n.id);
check("节点 id 唯一", new Set(ids).size === ids.length, `唯一 ${new Set(ids).size}/${ids.length}`);
const bandIds = new Set(P.BANDS.map(b => b.id));
const badBand = P.NODES.filter(n => !bandIds.has(n.band)).map(n => n.id);
check("每个节点 band 合法", badBand.length === 0, badBand.join(","));
const dup = {};
P.NODES.forEach(n => { dup[n.id] = (dup[n.id] || 0) + 1; });
check("ORGAN_GROUPS 无重复摆放", Object.values(dup).every(v => v === 1) && Object.keys(P.pos).length === 68,
  `pos ${Object.keys(P.pos).length} 个`);

// ── 2. 落位 ──
const noPos = P.NODES.filter(n => !P.pos[n.id]).map(n => n.id);
check("所有节点有落位", noPos.length === 0, noPos.join(","));
const W = 1900, H = 2400;
const outBox = P.NODES.filter(n => { const p = P.pos[n.id]; return p && (p.x < 0 || p.y < 0 || p.x + p.w > W || p.y + p.h > H); }).map(n => n.id);
check("节点在画布内", outBox.length === 0, outBox.join(","));

// 节点互不重叠
const ov = [];
const list = P.NODES.map(n => ({ id: n.id, p: P.pos[n.id] })).filter(x => x.p);
for (let i = 0; i < list.length; i++) for (let j = i + 1; j < list.length; j++) {
  const a = list[i].p, b = list[j].p;
  const ix = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
  const iy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
  if (ix > 0 && iy > 0) ov.push(`${list[i].id}×${list[j].id}(${ix.toFixed(0)}×${iy.toFixed(0)})`);
}
check("节点之间零重叠", ov.length === 0, ov.slice(0, 8).join(" "));

// ── 3. 边 ──
const missingEnd = P.EDGES.filter(e => !ids.includes(e.a) || !ids.includes(e.b)).map(e => `${e.a}->${e.b}`);
check("边端点都存在", missingEnd.length === 0, missingEnd.join(","));
let noPath = [], nanPath = [], selfEdge = [];
P.EDGES.forEach((e, i) => {
  if (e.a === e.b) selfEdge.push(e.a);
  const p = P.pathFor(e);
  if (!p) { noPath.push(`${e.a}->${e.b}`); return; }
  if (/NaN|undefined/.test(p.d)) nanPath.push(`${e.a}->${e.b}`);
});
check("每条边都画得出路径", noPath.length === 0, noPath.join(","));
check("路径里无 NaN/undefined", nanPath.length === 0, nanPath.join(","));
check("无自环", selfEdge.length === 0, selfEdge.join(","));
const renderedEdges = P.EDGES.filter(e => P.edgeEl[P.EDGES.indexOf(e)]).length;
check("渲染出的边数 == 88", renderedEdges === 88, `实际 ${renderedEdges}`);

// ── 4. 交互 ──
P.pin("head");
const selOk = P.nodeEl["head"].classList.contains("sel");
const nbrs = P.adj["head"];
const nearDim = [...nbrs].filter(id => P.nodeEl[id].classList.contains("dim"));
const farNode = P.NODES.map(n => n.id).find(id => id !== "head" && !nbrs.has(id));
const farDim = P.nodeEl[farNode].classList.contains("dim");
check("点选：自身 sel / 邻居不暗 / 非邻居暗", selOk && nearDim.length === 0 && farDim,
  `sel=${selOk} 邻居被暗=${nearDim.join(",") || "0"} 远端 ${farNode}.dim=${farDim}`);

P.pin(null);           // 先清掉上一步的固定，否则 hood 会把搜索命中一起压暗
P.setQuery("napcat");
P.apply();
const hitIds = P.NODES.filter(n => P.nodeEl[n.id].classList.contains("hit")).map(n => n.id);
const hitClean = hitIds.every(id => !P.nodeEl[id].classList.contains("dim"));
const missDim = P.nodeEl["db"].classList.contains("dim");
check("搜索：命中高亮、未命中被暗", hitIds.length > 0 && hitClean && missDim,
  `hit=${hitIds.join(",")} 未命中db.dim=${missDim}`);
P.setQuery(""); P.apply();
P.pin(null); P.apply();

const chip0 = P.BANDS[0].id;
P.offBands.add(chip0); P.apply();
const offAll = P.NODES.filter(n => n.band === chip0).every(n => P.nodeEl[n.id].classList.contains("off"));
check("分层开关：整层 off", offAll, chip0);
P.offBands.delete(chip0); P.apply();

P.setMain(true); P.apply();const nonMainDim = P.NODES.filter(n => !P.MAIN.has(n.id)).every(n => P.nodeEl[n.id].classList.contains("dim"));
const mainVisible = [...P.MAIN].every(id => !P.nodeEl[id].classList.contains("dim"));
check("主链路：只留 MAIN，其余全暗", nonMainDim && mainVisible, `非MAIN全暗=${nonMainDim} MAIN全亮=${mainVisible} 主链路 ${P.MAIN.size} 节点`);
P.setMain(false); P.apply();

win.location.hash = "#inject";
check("hash 深链解析", P.readHash() === "inject", String(P.readHash()));

const drawer = byId["drawer"];
P.pin("song");
check("抽屉渲染到节点标题", String(drawer.innerHTML).includes("点歌管线") && drawer.classList.contains("open"),
  `len=${String(drawer.innerHTML).length}`);
P.pin(null);
check("关闭后回到总览", String(drawer.innerHTML).includes("一具行为像儿童的完整身体"), "");

// ── 5. 每层节点数 ──
const perBand = P.BANDS.map(b => `${b.id}:${P.NODES.filter(n => n.band === b.id).length}`).join(" ");
check("每层都有节点", P.BANDS.every(b => P.NODES.some(n => n.band === b.id)), perBand);

// ── 6. 文字溢出粗估（13px 标题 / 10px 副标题，CJK 按 1em，ASCII 按 0.55em） ──
// 注意：这里只是"见过没"的粗估，ASCII 宽度会被高估；真值以 measure.py（PIL 真字体）
// 和 cdp_check2.py（真实 Edge 量 getBoundingClientRect）为准，所以只打印不判 FAIL。
function textW(s, size) {
  let w = 0;
  for (const ch of String(s)) w += /[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]/.test(ch) ? size : size * 0.55;
  return w;
}
const overflow = [];
P.NODES.forEach(n => {
  const p = P.pos[n.id]; if (!p) return;
  const avail = p.w - 15 - 8;
  const tw = textW(n.t, 13), sw = textW(n.s, 10);
  if (tw > avail || sw > avail) overflow.push(`${n.id}(${tw.toFixed(0)}/${sw.toFixed(0)}>${avail})`);
});
console.log(`粗估可能超框（仅供参考）: ${overflow.length ? overflow.slice(0, 10).join(" ") : "无"}`);

// ── 7. 数据完整性：每条边都有 kind ──
const kinds = new Set(P.EDGES.map(e => e.k));
check("边类型都在图例内", [...kinds].every(k => ["flow", "async", "data", "db", "ext"].includes(k)), [...kinds].join(","));

console.log(out.join("\n"));
console.log(`\nSUMMARY pass=${pass} fail=${fail}`);
const NODEOUT = P.NODES.map(n => ({ id: n.id, band: n.band, t: n.t, s: n.s, f: n.f, d: n.d, sym: n.sym || [], box: P.pos[n.id] }));
fs.writeFileSync(path.join(dir, "nodes.json"),
  JSON.stringify({ BANDS: P.BANDS.map(b => ({ id: b.id, name: b.name, cn: b.cn })), NODES: NODEOUT, EDGES: P.EDGES.map(e => ({ a: e.a, b: e.b, k: e.k, l: e.l })), MAIN: [...P.MAIN] }, null, 1), "utf8");
process.exitCode = fail ? 1 : 0;
