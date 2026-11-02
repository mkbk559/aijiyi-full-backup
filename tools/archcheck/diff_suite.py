"""比对改造前后的逐用例结果：状态必须逐条一致（沙箱里那 77 个 tempfile 用例属环境噪声）。

用法： python tools/archcheck/diff_suite.py [before.json] [after.json]
默认读 tools/archcheck/results/suite_before.json 与 suite_after.json。
"""
import json
import sys

b_path = sys.argv[1] if len(sys.argv) > 2 else "tools/archcheck/results/suite_before.json"
a_path = sys.argv[2] if len(sys.argv) > 2 else "tools/archcheck/results/suite_after.json"
before = json.load(open(b_path, encoding="utf-8"))
after = json.load(open(a_path, encoding="utf-8"))

only_b = sorted(set(before) - set(after))
only_a = sorted(set(after) - set(before))
diff = sorted(k for k in set(before) & set(after) if before[k] != after[k])

print(f"before: {len(before)} cases  {sorted(set(before.values()))}")
print(f"after : {len(after)} cases  {sorted(set(after.values()))}")
print(f"只在改造前: {len(only_b)}")
for k in only_b[:10]:
    print("  -", k)
print(f"只在改造后: {len(only_a)}")
for k in only_a[:10]:
    print("  +", k)
print(f"状态变了: {len(diff)}")
for k in diff[:15]:
    print(f"  ~ {k}: {before[k]} -> {after[k]}")
ok = sum(1 for v in after.values() if v == "ok")
print(f"\nVERDICT {'SAME' if not diff and not only_b and not only_a else 'DIFFERENT'}  after_ok={ok}")
sys.exit(0 if not diff and not only_b and not only_a else 1)
