"""用真实 Windows 字体量 docs/architecture.html 节点里文字的像素宽度，对比该节点实际方框宽度。"""
from PIL import ImageFont
import json, os, re

FD = r"C:\Windows\Fonts"
def pick(paths):
    for p in paths:
        if os.path.exists(p):
            return p
    return None
A = pick([os.path.join(FD, "segoeui.ttf"), os.path.join(FD, "arial.ttf")])
C = pick([os.path.join(FD, "msyh.ttc"), os.path.join(FD, "simhei.ttf")])
cache = {}
def font(path, size):
    k = (path, size)
    if k not in cache:
        cache[k] = ImageFont.truetype(path, size)
    return cache[k]

CJK = re.compile(r"[\u2e80-\u9fff\u3000-\u303f\uff00-\uffef\u00b7]")
def width(s, size):
    w = 0.0
    run, is_cjk = "", None
    for ch in s:
        c = bool(CJK.match(ch))
        if is_cjk is None or c == is_cjk:
            run += ch
        else:
            w += font(C if is_cjk else A, size).getlength(run)
            run = ch
        is_cjk = c
    if run:
        w += font(C if is_cjk else A, size).getlength(run)
    return w

data = json.load(open(r"tools/archcheck/results/nodes.json", encoding="utf-8"))
print("ascii font:", A)
print("cjk font  :", C)
print(f"\n{'id':13}{'boxW':>6}{'avail':>7}{'title':>7}{'sub':>7}  {'':<4}verdict")
bad_t = bad_s = 0
worst = []
for n in data["NODES"]:
    box = n["box"]; avail = box["w"] - 15 - 8
    tw, sw = width(n["t"], 13), width(n["s"], 10)
    v = []
    if tw > avail:
        bad_t += 1; v.append(f"title+{tw-avail:.0f}px")
    if sw > avail:
        bad_s += 1; v.append(f"sub+{sw-avail:.0f}px")
    if v:
        worst.append((max(tw, sw) - avail, n, tw, sw, avail, v))
worst.sort(reverse=True, key=lambda x: x[0])
for d, n, tw, sw, avail, v in worst:
    print(f"{n['id']:13}{n['box']['w']:6.0f}{avail:7.0f}{tw:7.0f}{sw:7.0f}  {'':<4}{'; '.join(v)}  | {n['t']} / {n['s']}")
print(f"\nnodes={len(data['NODES'])}  title_overflow={bad_t}  sub_overflow={bad_s}  worst=+{worst[0][0]:.0f}px ({worst[0][1]['id']})" if worst else "no overflow")
