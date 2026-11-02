"""只读：从 server.py 静态提取路由表。"""
from __future__ import annotations

import ast
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")
path = pathlib.Path(r"D:\AIJiYi\aiji\throat\server.py")
src = path.read_text(encoding="utf-8")
tree = ast.parse(src)
gets, posts = [], []
for node in ast.walk(tree):
    if not isinstance(node, ast.FunctionDef) or node.name not in ("do_GET", "do_POST"):
        continue
    bucket = gets if node.name == "do_GET" else posts
    for sub in ast.walk(node):
        if isinstance(sub, ast.Compare) and isinstance(sub.ops[0], ast.Eq):
            left = sub.left
            if isinstance(left, ast.Name) and left.id in ("path", "p"):
                right = sub.comparators[0]
                if isinstance(right, ast.Constant) and isinstance(right.value, str):
                    bucket.append(right.value)
            if isinstance(left, ast.Name) and left.id == "path":
                right = sub.comparators[0]
                if isinstance(right, ast.Tuple):
                    for elt in right.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            bucket.append(elt.value + "*")
print("GET :", len(gets))
for p in sorted(set(gets)):
    print("   ", p)
print("POST:", len(posts))
for p in sorted(set(posts)):
    print("   ", p)
print("\ntotal unique:", len(set(gets) | set(posts)))
