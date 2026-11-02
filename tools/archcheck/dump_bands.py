import json

d = json.load(open(r"D:\AIJiYi\tools\archcheck\results\nodes.json", encoding="utf-8"))
lines = []
for b in d["BANDS"]:
    lines.append(f"{b.get('id')} | {b.get('name')} | {b.get('sub', '')}")
    lines.append(f"   {b.get('d', '')[:220]}")
print("\n".join(lines))
