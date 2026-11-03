# tools/archive

这里放**已经跑完的一次性脚本**（迁移、改名、数据修补）和**被新工具取代**的扫描脚本。
它们不进任何自动化流程，留着只是为了"当时怎么做的"可追溯。

要恢复某个脚本：直接 `git mv tools/archive/<name> tools/<name>`。

| 文件 | 为什么归档 |
|---|---|
| `patch_tests_identity.py` | 修测试里的身份字段（已并入 tests/） |
| `migrate_drill.py` | 库迁移演练（迁移早已完成，schema 已到 v11） |
| `rename_persona.py` | 改 persona 名字（鱼设→小孩已手工完成，且 persona 已定稿玛卡巴卡） |
| `rename_voice_profile.py` | 改音色档案 id（已改完，档案在 data/voices/） |
| `fix_voice_index.py` | 一次性重建音色索引 |
| `reshape_identity_child.py` | 把身份改造成小孩人格（已应用，且人格已重写为玛卡巴卡） |
| `wire_net_proxy.py` | 给进程接代理环境变量（现在直接读 HTTP_PROXY） |
| `persona_residue_scan.py` | 被 tools/purge_persona_residue.py 取代 |
| `persona_residue_candidates.py` | 被 tools/purge_persona_residue.py 取代 |
| `dead_file_audit.py` | 被 .tmp/audit_tools.py 的一次性盘点取代（结论已进 excluded） |
| `push_voice_pack.ps1` | 把音色包推给另一台机器（当时手工用过） |

归档纪律：归档前确认 `grep -r "<文件名>" docs aiji tests tools` 没有命中（除它自己），
否则先改引用。2026-11-03 归档时 111 个脚本里有 31 个零引用。

复核备注：`repair_data.py` 曾被误归档（`tests/test_data_repair.py` 用 `from tools.repair_data import ...` 引用了它，而第一版扫描只匹配带 `.py` 的文件名，漏掉了这种写法）。结论已写进 excluded：**归档前用 stem 而不是文件名做引用扫描**。
