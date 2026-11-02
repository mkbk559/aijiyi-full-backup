import ast
import pathlib
import re

src = pathlib.Path(r"D:\AIJiYi\aiji\endocrine\life.py").read_text(encoding="utf-8")
tree = ast.parse(src)
names = [n.name for n in tree.body if isinstance(n, ast.ClassDef)]
print("life.py classes:", names)

texts = {}
for p in pathlib.Path(r"D:\AIJiYi\aiji").rglob("*.py"):
    if p.name == "life.py":
        continue
    texts[str(p.relative_to(r"D:\AIJiYi"))] = p.read_text(encoding="utf-8")

for name in names:
    hits = [k for k, v in texts.items() if re.search(r"\b" + name + r"\b", v)]
    print(f"{name:26s} -> {hits if hits else 'ONLY-IN-life.py'}")
