# 自我迭代第 5 轮（2026-11-03 16:10–16:35）

这一轮是**安全向**：把"只校验入口"的地方往深处看，果然挖到一个 SSRF 绕过。
另外把 `net_guard` / `humanize` 覆盖率补到 100%。

## 一、SSRF：守卫只看第一跳

`media_audio._default_fetch` 在下载前调用 `validate_public_http_url(url)`，
挡私网/回环/链路本地/保留地址。但 urllib 默认自动跟随 302：

```
用户给 https://evil.example/x        → 校验通过（公网）
响应 302 Location: http://127.0.0.1:8787/api/stickers  → urllib 直接跟过去
```

也就是说**守卫校验的地址和最后真正拉取的地址可以不是同一个**。
处置：`aiji/net.py` 给 direct / proxy 两个 opener 都装 `_GuardedRedirect`，
在 `redirect_request`（urllib 已把 Location 与 base 做过 urljoin）里
对目标 URL 再跑一次 `validate_public_http_url`。

真机验证（重启服务后）：

| 检查 | 结果 |
|---|---|
| `POST /api/stickers/update?wait=1` | `200 {"ok": true, "total": 204, "failed": 0, "preserved": 184, "took_ms": 26091.5}` |
| `/api/stickers` | `count 204`、`last_error None` |
| feed | `cdn.jsdelivr.net/...manifest.json`（会 302 到 CDN） |

合法重定向没被误杀；`tests/test_net_guard.py` 17 条把"该拦（私网/凭证/怪端口/解析失败）
与不该拦（公网 302）"两边都钉住，并断言 direct opener 里**确实**装了守卫。

## 二、覆盖率补到满

| 模块 | 补测前 | 补测后 | 新增 |
|---|---|---|---|
| `throat/net_guard.py` | 79% | **100%** | 17（含 redirect 守卫） |
| `brain/humanize.py` | 79% | **100%** | 10 |
| `net.py` | 84% | **90%** | （同上） |

`humanize` 顺便修正了一个测试写法错误：原来比较"低电量 vs 高电量"两次调用各自
`randint(low,high)`，抽样不同导致断言随机翻车；改成注入返回区间中点的 rng。

## 三、验证

- 全量 `run_suite` → **752/752 ok**（`suite_v32.json`，含重定向守卫）
- `ruff check aiji tools tests` 全过
- 真机：服务重启后 `/api/capabilities` 200、贴纸更新与快照均正常

## 四、下一轮候选

1. `song_service` 62% 与 `voice_service` 75% 的剩余分支都是真跑 separator/ffmpeg/RVC；
   可以用假 `_run` 验命令拼装与产物识别。
2. `admin.py` 70%、`social_service.py` 77%、`onebot.py` 78%。
3. 控制台"点动作按钮 → DOM 更新"还没有自动化（当前只验首屏渲染）。
4. `server.py` 79%：路线很全但错误分支（413/403/404 之外）还有空档。
