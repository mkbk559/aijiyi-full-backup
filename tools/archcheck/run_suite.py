"""把全量单测的“逐用例结果”落成 JSON，用于改造前后逐条比对（不依赖临时目录是否可写）。

失败不只记 "fail"：把 traceback 一起存进 JSON（键 `<id> → {"status": ..., "traceback": ...}`）。
否则像 v34 那样只有一个用例偶发失败时，日志里没有任何线索，只能整夜守着重跑。
"""
import io, json, sys, traceback, unittest

out = sys.argv[1] if len(sys.argv) > 1 else "suite.json"
res = {}


def _store(t, status, err):
    entry = {"status": status}
    if err is not None:
        entry["traceback"] = "".join(traceback.format_exception(*err))[-4000:]
    res[t.id()] = entry


class Rec(unittest.TextTestResult):
    def addSuccess(self, t):
        super().addSuccess(t)
        _store(t, "ok", None)

    def addError(self, t, err):
        super().addError(t, err)
        _store(t, "error", err)

    def addFailure(self, t, err):
        super().addFailure(t, err)
        _store(t, "fail", err)

    def addSkip(self, t, reason):
        super().addSkip(t, reason)
        _store(t, "skip", None)

    def addExpectedFailure(self, t, err):
        super().addExpectedFailure(t, err)
        _store(t, "xfail", None)

    def addUnexpectedSuccess(self, t):
        super().addUnexpectedSuccess(t)
        _store(t, "unexpected-ok", None)


suite = unittest.defaultTestLoader.discover("tests", top_level_dir=".")
buf = io.StringIO()
r = unittest.TextTestRunner(stream=buf, verbosity=0, resultclass=Rec).run(suite)
json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True)
ok = sum(1 for v in res.values() if v["status"] == "ok")
print(f"ran={r.testsRun} ok={ok} errors={len(r.errors)} failures={len(r.failures)} -> {out}")
print("error ids (first 10):")
for tid, v in sorted(res.items()):
    if v["status"] == "error":
        print("  ", tid)
