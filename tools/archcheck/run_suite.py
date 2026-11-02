"""把全量单测的“逐用例结果”落成 JSON，用于改造前后逐条比对（不依赖临时目录是否可写）。"""
import io, json, sys, unittest

out = sys.argv[1] if len(sys.argv) > 1 else "suite.json"
res = {}


class Rec(unittest.TextTestResult):
    def addSuccess(self, t):
        super().addSuccess(t)
        res[t.id()] = "ok"

    def addError(self, t, err):
        super().addError(t, err)
        res[t.id()] = "error"

    def addFailure(self, t, err):
        super().addFailure(t, err)
        res[t.id()] = "fail"

    def addSkip(self, t, reason):
        super().addSkip(t, reason)
        res[t.id()] = "skip"

    def addExpectedFailure(self, t, err):
        super().addExpectedFailure(t, err)
        res[t.id()] = "xfail"

    def addUnexpectedSuccess(self, t):
        super().addUnexpectedSuccess(t)
        res[t.id()] = "unexpected-ok"


suite = unittest.defaultTestLoader.discover("tests", top_level_dir=".")
buf = io.StringIO()
r = unittest.TextTestRunner(stream=buf, verbosity=0, resultclass=Rec).run(suite)
json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True)
ok = sum(1 for v in res.values() if v == "ok")
print(f"ran={r.testsRun} ok={ok} errors={len(r.errors)} failures={len(r.failures)} -> {out}")
print("error ids (first 10):")
for tid, v in sorted(res.items()):
    if v == "error":
        print("  ", tid)
