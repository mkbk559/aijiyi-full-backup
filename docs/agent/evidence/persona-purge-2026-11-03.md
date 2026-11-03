# 清除"会影响人设"的旧数据（2026-11-03）

> 用户：「你决定，还有之前影响人设的全部删除」。
> 脚本 `tools/purge_persona_residue.py`（默认 dry-run，`--apply` 才删），
> 每一条被删记录都在 `tools/archcheck/results/persona_purge_audit.json`，
> 整库备份在 `data/backups/aiji.db.bak-before-persona-purge-*`。

## 0. 判定口径（我定的，理由写在前面）

人设只应由 `persona.CHILD` + `selfhood.FLOOR` + 成长阶段决定。**库里任何"描述他长成什么样"的
数据都会把八九岁的孩子往旧人格（孙吧抽象话：调侃/自嘲/阴阳/锅碗鱼意象）上拽**。所以：

| 目标 | 处理 | 依据 |
|---|---|---|
| `trait`（"你长成了什么样"注入块） | **全量删除 927 行** | 全是旧人格时期学的口吻与意象；新人格会从零重新学 |
| `profile` 里旧名字/旧身份（45 行） | 删 | 大肥鱼/本鱼/蓝毛鲸鱼尾/爸爸养在电脑里的小鱼鱼 |
| `style` 里 `本鱼` 自指（3 行） | 删 | 同样的自指词 |
| `being_*` 的 `persona='sunba'` 行（4 行） | 删 | 迁移漏网；默认人格已改 child |
| `data/persona/sunba.json`（学到的成年语气语料 8 条） | 删 | 只服务旧人格 |

**没删**：`profile`/`style` 的其余 1105/1920 行——它们记的是**对方**的事（昵称、喜好、口癖、
语气），是"怎么适应这个人"，不是"他是什么人"。删了会让人物对每个人失忆。

## 1. 三步走的原始数字

| 轮次 | 命中 | 删了什么 | 剩余 |
|---|---|---|---|
| 第 1 轮（词表 = `selfhood._ADULT_TONE_WORDS` 18 词 + 旧身份词） | trait 314 / profile 47 / style 3 / being 4 | 调侃·自嘲·损对方·阴阳·毒舌…；大肥鱼·本鱼·鱼缸·咕噜·在电脑里 | trait 1207 |
| 第 2 轮（补旧人格特有姿态：吐槽/回怼/阴阳/抽象/玩梗/冷笑） | trait 110 / profile 1 | 吐槽类 | trait 1097 |
| 第 3 轮（补 反问） | trait 170 | 「倾向于用反问的方式回应」 | trait 927 |
| 第 4 轮（`--all-traits`，全量） | trait 927 | 剩下的锅/碗/白饭/开饭/尾巴一摆/🐟/待机/号牌等意象 | **trait 0** |

第 1 轮就发现必须往上加：清掉"调侃/自嘲"以后，剩下的高频 trait 是
「用锅、米、开饭等厨房意象打趣」「句尾常加🐟」「几乎每句结尾都加🐟强化拟物人设」
——**这些比"调侃"更贴脸**，也正是用户说的"影响人设"。所以按词根多轮清到 0。

## 2. 顺带修掉的一个真 bug：旧进程还在按 sunba 写库

第 1 轮删完 4 行 `persona='sunba'` 的 being 行，**几秒后 scanning 又出现 1 条 state / 1 条 event / 1 条 note**。
追因：

- `aiji/hippocampus/memory.py:219`、`aiji/endocrine/being_checkup.py:277` 的默认值是
  `getattr(settings, "persona", "sunba")` —— 兜底仍是旧人格名（已改成 `"child"`）。
- 更要紧的是**正在跑的服务进程是今天早些时候起的**，内存里 `settings.persona` 还是 `sunba`，
  每次生命层写库都带旧人格名。

处理：`Stop-Process 17208` → `Start-ScheduledTask 'AIJiYi Console Watchdog'` →
新进程 17780（11:22:10 启动）→ 再 purge 一次，之后数量稳定在
`being_state child 1 / being_event child 1002 / being_note child 31`，`sunba` 归零。

## 3. 删完后人设有没有垮（真机）

`python tools/archcheck/persona_child_live.py --age 8`：

```
SUMMARY {"n": 9, "clean_ratio": 1.0, "avg_chars": 38.8, "with_child_sign": 8, "forbidden_hits": {}}
"你叫什么名字" → 玛卡巴卡！你喊我我会应的那种哦。你呢你呢，你叫什么呀？
"你现在在哪呢" → 在家呀，窗边这里。刚才在发呆，看天上的云。
"我要睡觉了"   → 晚安呀～要做甜甜的梦哦！
```

`python tools/archcheck/persona_child_trait_clash.py` → `{"leak": false}`（成年语感 trait 重新注入时仍被拦）。

结论：**人设不依赖 trait 库**——`persona.CHILD` + `FLOOR` + 成长阶段就够；trait 是后学的，
现在从零开始学，学到的会是这个人格自己的习惯。

## 4. 现在库里剩什么

```
trait     0      （旧人格口吻全清；之后由 reflect 层按新人格重新学）
profile   1105   （对方的事；旧名字/旧身份 45 行已删）
style     1920   （对方的说话风格；本鱼自指 3 行已删）
being     1002 条事件 / 31 条笔记，全部 persona=child
内嵌语料   data/persona/{growth.json, learn_state.json}（sunba.json 已删）
```

## 5. 可回滚

- 四次 purge 各有一份整库备份：`data/backups/aiji.db.bak-before-persona-purge-20261103-1120*` ~ `1127*`。
- `persona_purge_audit.json` 记着每一行的 `rid + value + 命中词`（最后一轮全量 trait 也在里面）。
- 想恢复某一批：从对应备份里 `ATTACH` 后按 `rowid` 拷回即可。
