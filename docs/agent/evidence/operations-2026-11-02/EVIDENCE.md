# 运行与功能页面 · 「启动与结束」真机证据（2026-11-02）

## 这是什么

真机 Edge 窗口（`http://127.0.0.1:8787/operations.html#lifecycle`）的桌面截图与对应原始
机器输出。页面所有数字都来自 `GET /api/operations/services` 的实时探测，不使用演示数据。

## 文件

| 文件 | 内容 |
|---|---|
| `../../data/evidence/lifecycle-live-edge-2026-11-02.png` | 真窗口截图，361,166 B，sha256 `7FD809ED…D569C`。可见：状态条「整套服务在线 / 5 / 5 就绪 / 目标状态：运行中（保活可补启）/ 检查于 2026-11-02 11:13:48 / 链路无卡点」，五张依赖链卡片（NapCat 端口 6099 · PID 29436 · QQ；QQ 反向连接 6199 · PID 13192 · python · 连接 1；AstrBot 6185；AIJiYi 8787 · PID 3076 · aiji 核心（本进程）· 连接 4；实时状态 8789） |

`docs/agent/` 在本机被 ACL 限制为"只能由编辑工具写"，图片只能放在 `data/evidence/`。

## 原始输出（磁盘归档）

- CORS + 服务快照：`C:\Users\22166\.dsh\evidence\2026-11-02\110959-verify-cors-and-services-after-restart-4d9bef\stdout.txt`
  —— `Origin: null` 预检 204 且带 `Access-Control-Allow-Origin: null`；陌生来源无该头、预检 403；
  同一次调用里 `/api/operations/services` 返回 `{"ready":5,"total":5,"all_ready":true}`。
- 配置改后读回：`C:\Users\22166\.dsh\evidence\2026-11-02\111552-config-save-roundtrip-live-83ebf8\stdout.txt`
  —— POST `budget_ms:120` → 200；GET 回读 120、`defaults.budget_ms=100`；非法值 400；测试后文件已删除还原。
- DOM 一致性：`C:\Users\22166\.dsh\evidence\2026-11-02\111542-dom-id-consistency-c85deb\stdout.txt`
  —— 脚本引用 58 个 id，全部存在于 HTML；`MISSING: []`。
- 页面重启下发：`C:\Users\22166\.dsh\evidence\2026-11-02\110446-restart-console-to-load-new-code-072499\stdout.txt`
  —— 202 `{"ok":true,"action":"restart"}`。

## 没有验到的部分（别当成已验）

1. **「结束整套服务」按钮没有真按过**：按下去会把这个控制台自己停掉，本次会话就失去操作面。
   只验到 202 下发 + `tools/lifecycle.ps1 -Action stop` 的入参。
2. **`file://` 打开只验了机制**（CORS 头 + 页面走绝对地址 `http://127.0.0.1:8787`），
   没有做视觉复核。
3. **Chrome/Edge headless 在本沙箱不可用**（crashpad `OpenProcess` + mojo `platform_channel`
   拒绝访问），所以视觉证据只有上面那张真窗口截图，带滚动位置偏差。
