# Seed-VC 集成档案（已弃用，2026-11-02 删除）

> 用户决定"全部走 RVC"，`voice-engine/seed-vc`（8,005 MB / 35,176 文件）与其 venv 已删除。
> 这份档案只保留"当时怎么接的"这些事实，便于以后对比或回滚时重建。

## 当时的事实

- 上游：https://github.com/Plachtaa/seed-vc ，vendored 在 commit `51383efd921027683c89e5348211d93ff12ac2a8`。
- 许可：**GPL-3.0**（这是换 RVC/MIT 的动机之一）。
- 形态：独立进程，监听 `127.0.0.1:9880`，AIJiYi 不把源码拷进记忆服务。
- 本地包装：`aiji_seed_vc_server.py`（235 行）——常驻加载官方模型、把转换请求串行化，
  对外提供 `POST /convert`（接收源 wav + 参考音频 + `diffusion_steps` 等参数，返回转换后 wav）。
- 启停：`start-aiji.ps1` / `stop-aiji.ps1`；首次启动自动下载官方模型（可用 `AIJI_HF_ENDPOINT` 换镜像）。
- 体积构成：`seed-vc/.venv` 6,153 MB（其中 torch 2.7.1+cu128 = 5,525 MB）+ `checkpoints` 1,797 MB
  （whisper-small 922 / bigvgan 428 / DiT 420 / campplus 27）。

## 它当时接在哪儿

`data/voices/dafeiyu/profile.json`：

```json
{ "provider": "edge", "voice": "zh-CN-XiaoyiNeural",
  "postprocess": { "url": "http://127.0.0.1:9880/convert",
                   "reference": "dafeiyu/reference_seedvc.wav",
                   "diffusion_steps": 10, "song_diffusion_steps": 30 } }
```

即"说话"= edge 合成 → Seed-VC 换音色；"唱歌"= `song_service.py:306-315` 分离人声后调同一个 `/convert`。

## 换成 RVC 后不需要的东西

RVC 走 CLI 就够了：`voice_service.py:720-747` 支持 `postprocess.command`（带 `{input}` / `{output}` 占位符的
命令数组），所以**不用再写 HTTP 包装服务**，只要一个 `rvc_convert.bat {input} {output}` 即可。
