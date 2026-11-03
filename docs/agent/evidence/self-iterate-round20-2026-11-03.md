# 自我迭代第 20 轮（2026-11-04 00:10–00:25）：v49 那条偶发失败——查明成因链、让失败自带信息

## 现象

v49（1158 条）唯一失败：

```
tests.test_being_checkup.BeingCheckupTest.test_live_probes_capture_replies_without_writing_turns
AssertionError: False is not true : name_respected
```

单独跑、连跑 3 次都过。v50 全量跑 **1158/1158 ok**——确认是偶发，不是我这轮改坏的东西。

## 成因链（读代码得到）

`BeingCheckup.live_probe()` 用 **3 个线程**并行跑三个探针，每个探针：
克隆一份库（`sqlite3.backup`）→ `_probe_memory()` 开一个只读用途的 `Memory` →
`probe.pool = self.memory.pool`（测试里是 `FakePool`，模型这一层是确定的）→
`Head(probe).reply(...)`。

而 `Head.reply` 在 `PoolExhausted` 或任何异常时**不抛错**，返回
`FALLBACK_TEXT = "刚才没接上，等我一下。"` 并标 `degraded=True`。
`name_respected` 要求"回复里含用户名字（玛卡巴卡）"，于是任何一环降级都会让它变 False，
且失败信息只剩一句 `False is not true`，看不出降级发生在哪。

## 处理：不弱化断言，让失败自带信息

- 补一条前置断言 `memory.preferred_name(ident) == "玛卡巴卡"`
  （区分"事实没写进库"与"回复不对"两种情况）
- 三个 `assertTrue` 后面都挂上 `json.dumps(report, ensure_ascii=False)[:1500]`
  ——下次再红，报告里直接能看到每个探针的 `text` / `route` / `degraded` / `elapsed_ms`

## 验证

- `tests/test_being_checkup.py` 2/2
- 全量 suite **v50：1158 ok / 0 errors / 0 failures**
- ruff 全过

## 记入 excluded

`#147`：这条用例的降级链与"下次如何一眼定位"，避免将来又把 `FALLBACK_TEXT` 当成产品回复。
