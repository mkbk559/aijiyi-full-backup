

## 第 25 轮（2026-11-04 上午）：看视频=听+看（--listen/--look）+ B 站账号能力

- `aiji learn-video <url> --listen N --look N`：音轨 FunASR 转写（conf 0.9，rtf≈0.12）
  + 抽帧 RapidOCR 读画面字（conf≈0.7）→ 全部拼进学习文本走同一条抽取管线。
  e2e 全链路归档 103849（media 338 字含"听到的内容"+"画面里看到的字"）。
- 三个真缺陷顺手修掉：_video 丢调用方文本（#165）、finally 清理删返回值（#166）、
  关停兜底路径漏 knowledge fact（上一轮补丁在 offline 分支没有对应实现）。
- B 站账号能力 `aiji/senses/bili_client.py` + CLI `bili-login`/`bili-comment`：
  cookie（yt-dlp 导出 jar 或 --cookies-from-browser）→ nav 查登录态 → 评论 API。
  护栏：默认 dry-run、--yes 才真发、60s 限速、bili_actions.jsonl 留痕、
  被拒也留痕。BV→av 用确定性算法（官方 API 对拍验证）。
- 机器上 Edge 无 B 站登录态（DPAPI 拒绝=预期）——评论真发待用户开账号后实测，
  写路径已备好 12 条离线测试（dry-run/限速/留痕/拒绝处理）。
- 画面理解边界（诚实）：RapidOCR 只读**字**；看懂画面内容仍需多模态模型，没做。
