"""把 RVC 包装成 AIJiYi `voice_service.postprocess.command` 能直接调的形式。

用法（由 profile.json 的 postprocess.command 调，也可手动跑）：
    <rvc venv python> tools/rvc_convert.py <in.wav> <out.wav> [--via auto|server|inproc] [--pitch 0] [...]

为什么要这个包装：RVC 的 `infer/cli.py` 必须能 `import infer`，而这只有在
`voice-engine/rvc` 作为 cwd（或进 sys.path）时才成立。AIJiYi 调用子进程时 cwd 是项目根，
所以这里替它切目录 + 设环境变量。

## 两条路径（2026-11-03 起）

- **常驻服务**（默认优先）：`tools/rvc_server.py` 把 `VC` 对象留在内存里，模型只载一次。
  实测 1.5 s 素材、CPU：老路径 **14 064 ms** → 服务热调用 **2 892 ms（×4.86）**，
  证据 `tools/archcheck/results/rvc_bench.json`。
- **一次性进程**（回退）：服务没起、或 `--via inproc` 时走这条，行为和以前一样（每次重载模型）。

服务地址默认 `http://127.0.0.1:18601`，可用 `AIJI_RVC_SERVER` 覆盖；
`AIJI_RVC_AUTOSTART=1` 时服务没起会顺手拉一个（detached，不阻塞本进程）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RVC = ROOT / "voice-engine" / "rvc"
MODEL = RVC / "assets" / "weights" / "dafeiyu.pth"
INDEX = RVC / "assets" / "indices" / "dafeiyu_added_IVF948_Flat_nprobe_1_dafeiyu_v2.index"
SERVER = os.environ.get("AIJI_RVC_SERVER") or "http://127.0.0.1:18601"
RVC_PY = ROOT / "voice-engine" / ".venv" / "Scripts" / "python.exe"


# ---- 常驻服务客户端 ----

def _opener():
    # 本机有全局 HTTP_PROXY（Clash），127.0.0.1 必须绕开它，否则请求会被隧道吃掉
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def health(url: str | None = None, timeout: float = 3.0) -> dict | None:
    try:
        with _opener().open(f"{(url or SERVER).rstrip('/')}/health", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def autostart(url: str | None = None, wait_s: float = 90.0) -> bool:
    """拉一个 detached 的服务进程，并等到 /health 可用。"""
    if not RVC_PY.exists():
        return False
    port = (url or SERVER).rsplit(":", 1)[-1].split("/")[0] or "18601"
    flags = 0
    if os.name == "nt":
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(
            [str(RVC_PY), str(ROOT / "tools" / "rvc_server.py"), "--port", port],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flags, close_fds=True,
        )
    except Exception:
        return False
    t0 = time.time()
    while time.time() - t0 < wait_s:
        if health() is not None:
            return True
        time.sleep(1.0)
    return False


def build_payload(src: str, dst: str, extra: list[str]) -> dict:
    payload: dict = {"in": src, "out": dst}
    mapping = {
        "--pitch": ("pitch", int),
        "--index-rate": ("index_rate", float),
        "--f0-method": ("f0_method", str),
        "--protect": ("protect", float),
        "--rms-mix-rate": ("rms_mix_rate", float),
        "--speaker-id": ("speaker_id", int),
    }
    for i, a in enumerate(extra):
        if a in mapping and i + 1 < len(extra):
            key, cast = mapping[a]
            try:
                payload[key] = cast(float(extra[i + 1])) if cast is not str else extra[i + 1]
            except ValueError:
                pass
    return payload


def convert_via_server(src: str, dst: str, extra: list[str]) -> int:
    # 服务端 cwd 被 RVC 改成 voice-engine/rvc，相对路径会解析到错地方 → 一律发绝对路径
    payload = build_payload(os.path.abspath(src), os.path.abspath(dst), extra)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(f"{SERVER.rstrip('/')}/convert", data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with _opener().open(req, timeout=1800) as r:
            out = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(f"rvc: 服务拒绝：{exc.read().decode('utf-8', 'replace')}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"rvc: 服务调用失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if not out.get("ok") or not Path(dst).exists():
        print(f"rvc: 服务没产出文件：{out}", file=sys.stderr)
        return 1
    return 0


# ---- 回退路径：一次性进程（老行为）----

def convert_inproc(src: str, dst: str, extra: list[str]) -> int:
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    sys.path.insert(0, str(RVC))
    os.chdir(RVC)

    from infer.cli import main as rvc_main  # noqa: E402  延迟导入，切完目录再导

    args = [
        "--model", str(MODEL),
        "--input", src,
        "--output", dst,
        "--index", str(INDEX),
        "--f0-method", "rmvpe",
        *extra,
    ]
    result = rvc_main(args)
    if not Path(dst).exists():
        print("rvc: 没有产出文件", file=sys.stderr)
        return 1
    return 0 if result is None else int(result)


def split_via(argv: list[str]) -> tuple[str, list[str]]:
    via = "auto"
    rest: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "--via" and i + 1 < len(argv):
            via = argv[i + 1]
            i += 2
            continue
        if argv[i].startswith("--via="):
            via = argv[i].split("=", 1)[1]
            i += 1
            continue
        rest.append(argv[i])
        i += 1
    return via, rest


def main(argv: list[str]) -> int:
    via, rest = split_via(argv)
    if len(rest) < 2:
        print("usage: rvc_convert.py <in.wav> <out.wav> [--via auto|server|inproc] [extra]",
              file=sys.stderr)
        return 2
    src, dst = rest[0], rest[1]
    extra = rest[2:]

    if via in ("auto", "server"):
        if health() is None and via == "auto" and os.environ.get("AIJI_RVC_AUTOSTART") == "1":
            autostart()
        if health() is not None:
            return convert_via_server(src, dst, extra)
        if via == "server":
            print(f"rvc: 服务 {SERVER} 不可用", file=sys.stderr)
            return 1
    return convert_inproc(src, dst, extra)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
