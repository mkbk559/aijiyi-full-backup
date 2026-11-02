"""CDP v2：机械量化“文字被相邻方框盖住多少”，并在 1.4 倍下截图留证。"""
import base64, json, os, time, urllib.request
import websocket

PORT = 9223
OUT = os.path.dirname(os.path.abspath(__file__))

def targets():
    for _ in range(20):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=2) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            time.sleep(0.5)
    raise SystemExit("CDP 端口没起来")

page = [t for t in targets() if t.get("type") == "page" and "architecture.html" in t.get("url", "")]
ws = websocket.create_connection(page[0]["webSocketDebuggerUrl"], timeout=30)
_id = [0]
def cmd(method, params=None):
    _id[0] += 1
    ws.send(json.dumps({"id": _id[0], "method": method, "params": params or {}}))
    while True:
        m = json.loads(ws.recv())
        if m.get("id") == _id[0]:
            return m
def ev(expr):
    r = cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
    return r.get("result", {}).get("result", {}).get("value")

cmd("Page.enable"); cmd("Runtime.enable")

COVER = r"""
(() => {
  const boxes = [...document.querySelectorAll('g.node')].map(g => ({
    id: g.dataset.id,
    r: g.querySelector('rect.box').getBoundingClientRect(),
    // 节点在 gNodes 里的绘制顺序 = DOM 顺序；后画的会盖住先画的
    idx: [...document.querySelectorAll('g.node')].indexOf(g)
  }));
  const out = [];
  boxes.forEach(b => {
    const g = document.querySelector(`g.node[data-id="${b.id}"]`);
    g.querySelectorAll('text').forEach(t => {
      const tr = t.getBoundingClientRect();
      const overR = tr.right - b.r.right;
      if (overR <= 0.3) return;
      // 找盖在文字右端上的后续节点方框
      let coveredBy = null, covered = 0;
      boxes.filter(o => o.idx > b.idx).forEach(o => {
        const ox = Math.min(tr.right, o.r.right) - Math.max(tr.left, o.r.left);
        const oy = Math.min(tr.bottom, o.r.bottom) - Math.max(tr.top, o.r.top);
        if (ox > 0 && oy > 0 && ox > covered) { covered = ox; coveredBy = o.id; }
      });
      out.push({id: b.id, cls: t.getAttribute('class'), text: t.textContent,
                overflowPx: +overR.toFixed(2), coveredBy, coveredPx: +covered.toFixed(2),
                textW: +tr.width.toFixed(1), boxW: +b.r.width.toFixed(1)});
    });
  });
  const k = document.getElementById('viewport').getScreenCTM().a;
  return {zoom: +k.toFixed(3), items: out};
})()
"""
res = ev(COVER)
print(f"zoom={res['zoom']}  items={len(res['items'])}")
for it in res["items"]:
    cov = f"  被 {it['coveredBy']} 方框盖住 {it['coveredPx']:.1f}px" if it["coveredBy"] else "  右侧是空白（未遮挡）"
    print(f"  {it['id']:13}{it['cls']:2} 溢出 {it['overflowPx']:5.1f}px（框宽 {it['boxW']:.0f} / 文字 {it['textW']:.0f}）{cov}  | {it['text']}")
json.dump(res, open(os.path.join(OUT, "cdp_cover.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

# 放大到约 1.4 倍，聚焦 qq 方框右边界
ZOOM = r"""
(() => {
  const g = document.querySelector('g.node[data-id="qq"]');
  const r = g.getBoundingClientRect();
  const cx = r.right + 10, cy = r.top + r.height/2;
  const stage = document.getElementById('stage');
  for (let i = 0; i < 8; i++) {
    stage.dispatchEvent(new WheelEvent('wheel', {deltaY: -300, clientX: cx, clientY: cy, bubbles: true, cancelable: true}));
  }
  const g2 = document.querySelector('g.node[data-id="qq"]');
  const r2 = g2.getBoundingClientRect();
  const n = document.querySelector('g.node[data-id="napcat"]').getBoundingClientRect();
  return {zoom: document.getElementById('viewport').getScreenCTM().a,
          qq: {x: r2.left, y: r2.top, w: r2.width, h: r2.height},
          napcat: {x: n.left, y: n.top, w: n.width, h: n.height}};
})()
"""
z = ev(ZOOM)
print("zoom target:", json.dumps(z, ensure_ascii=False))
time.sleep(0.8)
r = cmd("Page.captureScreenshot", {"format": "png"})
p = os.path.join(OUT, "cdp-zoom14.png")
open(p, "wb").write(base64.b64decode(r["result"]["data"]))
print("screenshot:", p, os.path.getsize(p), "bytes")
ws.close()
