"""常驻 RVC 换声服务（2026-11-03）。

解决的问题（原文写死在 `tools/rvc_convert.py` 的 shortcut 里）：
每次换声都走一次 `infer/cli.py`，模型要重新加载，实测 **~20 s/次**
（载入 0.6 s + F0 4~6 s + 合成 3.5 s + 进程与 import 开销）。
本服务把 `VC` 对象留在内存里，模型只载一次，之后每次只做 F0 + 合成。

必须用 RVC 自己的 venv 跑，**并且要那个带 CUDA 的**（`voice-engine/rvc/.venv`，
Python 3.12 + torch 2.7.1+cu128）。同一颗素材实测：CPU 环境（`voice-engine/.venv`）
1.5 s 素材 2 892 ms / 6 s 素材 9 519 ms；GPU 环境 **384 ms / 503 ms**（RTF 0.26 / 0.08）。
选错解释器就是 7~19 倍差距。

    voice-engine/rvc/.venv/Scripts/python.exe tools/rvc_server.py --port 18601 --device cuda

（`tools/rvc_convert.py --via auto` 和 `tools/rvc_daemon.ps1` 都会自己挑 GPU 环境。）

接口（HTTP + JSON，只绑 127.0.0.1）：
    GET  /health          -> {"ok":true,"model":"dafeiyu.pth","device":"cuda","loaded_ms":...}
    POST /convert         -> {"in":"a.wav","out":"b.wav","pitch":0,"index_rate":0.75,
                              "f0_method":"rmvpe","protect":0.33,"rms_mix_rate":1.0}
                             返回 {"ok":true,"ms":812.4,"out":"b.wav","sr":40000}
    POST /shutdown        -> {"ok":true}

调用方：`tools/rvc_convert.py`（发现服务在跑就走服务，否则退回一次性 CLI，行为不变）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RVC = ROOT / "voice-engine" / "rvc"
DEFAULT_MODEL = RVC / "assets" / "weights" / "dafeiyu.pth"
DEFAULT_INDEX = RVC / "assets" / "indices" / "dafeiyu_added_IVF948_Flat_nprobe_1_dafeiyu_v2.index"


def _short_error(exc: BaseException) -> str:
    """一行原因：`异常类型: 最后一行消息`，不带 traceback。"""
    msg = str(exc).strip().splitlines()
    return f"{type(exc).__name__}: {msg[-1][:200] if msg else ''}".strip()


def _short_error_text(text: str) -> str:
    """RVC 常把整段 traceback 当 `status` 返回：只留最后一行（真正的异常）。"""
    lines = [ln.strip() for ln in str(text).strip().splitlines() if ln.strip()]
    if not lines:
        return "unknown error"
    last = lines[-1]
    return last if len(lines) == 1 else f"… {last[:200]}"


def _hint_for_text(text: str) -> str:
    low = text.lower()
    if "torchfcpe" in low:
        return ("该 F0 方法要 torchfcpe：uv pip install --python "
                "voice-engine\\rvc\\.venv\\Scripts\\python.exe torchfcpe；"
                "不改也行，用默认的 rmvpe（CPU 上两者总耗时差不多）")
    if "hubert" in low and ("no such file" in low or "not found" in low):
        return "缺 hubert 权重：检查 voice-engine/rvc/assets/hubert 是否存在"
    if "rmvpe" in low and ("no such file" in low or "not found" in low):
        return "缺 rmvpe 权重：检查 voice-engine/rvc/assets/rmvpe 是否存在"
    if "index" in low and ("no such file" in low or "not found" in low):
        return "特征索引路径不对：检查 --index 参数"
    return ""


def _hint_for(exc: BaseException) -> str:
    return _hint_for_text(f"{type(exc).__name__}: {exc}")


class Engine:
    """把 RVC 的 VC 对象包成"载一次、反复用"。单线程推理（torch CPU 上并发只会更慢）。"""

    def __init__(self, model: Path, index: Path | None, device: str = "cpu"):
        self.model = Path(model)
        self.index = str(index) if index else None
        self.device = device
        self.loaded_ms = 0.0
        self.lock = threading.Lock()
        self.calls = 0
        self.vc = None
        self._boot()

    def _boot(self) -> None:
        t0 = time.perf_counter()
        # RVC 要求 cwd 在 voice-engine/rvc（靠相对路径找 hubert/rmvpe 资源），
        # 所以先记下"调用方视角"的 cwd，之后收到的相对路径按它解析。
        self.base = Path.cwd()
        os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
        sys.path.insert(0, str(RVC))
        os.chdir(RVC)
        os.environ["weight_root"] = str(self.model.parent)
        from infer.cli import create_config  # noqa: E402

        config = create_config()
        try:
            config.device = self.device
        except Exception:
            pass
        from infer.vc.modules import VC  # noqa: E402

        vc = VC(config)
        vc.get_vc(self.model.name)
        self.vc = vc
        self.loaded_ms = (time.perf_counter() - t0) * 1000.0

    def convert(self, src: str, dst: str, *, pitch: int = 0, index_rate: float = 0.75,
                f0_method: str = "rmvpe", protect: float = 0.33,
                rms_mix_rate: float = 1.0, speaker_id: int = 0) -> dict[str, Any]:
        from infer.cli import write_audio  # noqa: E402

        t0 = time.perf_counter()
        with self.lock:
            try:
                status, result = self.vc.vc_single(
                    int(speaker_id), str(src), int(pitch), str(f0_method),
                    self.index, float(index_rate), 0, float(rms_mix_rate), float(protect),
                )
            except Exception as exc:  # noqa: BLE001
                # 别把整段 traceback 塞回调用方（客户端只会当成"换声坏了"）。
                # 只给一行原因 + 一句能照做的提示。
                return {"ok": False, "error": _short_error(exc),
                        "hint": _hint_for(exc), "f0_method": str(f0_method)}
            if not result or result[0] is None or result[1] is None:
                text = str(status)
                hint = _hint_for_text(text)
                return {"ok": False, "error": _short_error_text(text), "hint": hint,
                        "f0_method": str(f0_method),
                        # 参数类问题（比如这个 F0 方法要额外依赖）按 400 报，别当成服务端崩了
                        "status_code": 400 if "torchfcpe" in text.lower() else 500}
            write_audio(Path(dst), result[1], result[0], "wav")
            self.calls += 1
        return {"ok": True, "ms": round((time.perf_counter() - t0) * 1000.0, 1),
                "out": str(dst), "sr": int(result[0]), "status": str(status)}


def make_handler(engine: Engine, stop: threading.Event):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # 静音
            return

        def _send(self, code: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            if self.path.startswith("/health"):
                self._send(200, {"ok": True, "model": engine.model.name,
                                 "device": engine.device,
                                 "loaded_ms": round(engine.loaded_ms, 1),
                                 "calls": engine.calls})
                return
            self._send(404, {"ok": False, "error": "not found"})

        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            if self.path.startswith("/shutdown"):
                self._send(200, {"ok": True})
                stop.set()
                return
            if not self.path.startswith("/convert"):
                self._send(404, {"ok": False, "error": "not found"})
                return
            try:
                body = json.loads(raw or b"{}")
                src = str(body.get("in") or "")
                dst = str(body.get("out") or "")
                if not src or not dst:
                    raise ValueError("in/out required")
                src = str(Path(src) if Path(src).is_absolute() else engine.base / src)
                dst = str(Path(dst) if Path(dst).is_absolute() else engine.base / dst)
                if not Path(src).exists():
                    raise FileNotFoundError(src)
                out = engine.convert(
                    src, dst,
                    pitch=int(body.get("pitch") or 0),
                    index_rate=float(body.get("index_rate") or 0.75),
                    f0_method=str(body.get("f0_method") or "rmvpe"),
                    protect=float(body.get("protect") or 0.33),
                    rms_mix_rate=float(body.get("rms_mix_rate") or 1.0),
                    speaker_id=int(body.get("speaker_id") or 0),
                )
                self._send(int(out.get("status_code") or (200 if out.get("ok") else 500)), out)
            except Exception as exc:  # noqa: BLE001
                self._send(400, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    return Handler


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("AIJI_RVC_PORT") or 18601))
    ap.add_argument("--model", default=str(DEFAULT_MODEL))
    ap.add_argument("--index", default=str(DEFAULT_INDEX))
    ap.add_argument("--device", default=os.environ.get("AIJI_RVC_DEVICE") or "cpu")
    ap.add_argument("--log", default="", help="把 stdout/stderr 写进这个文件（守护脚本用）")
    args = ap.parse_args(argv)

    if args.log:
        # 自己写日志，而不是让启动方做重定向：Windows 上 Start-Process 带重定向时
        # 子进程会握住父进程的句柄，调用方（尤其被管道捕获的 shell）会一直等下去。
        log_path = Path(args.log)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(log_path, "a", encoding="utf-8", buffering=1)
        sys.stdout = handle
        sys.stderr = handle
        print(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} boot ---")

    engine = Engine(Path(args.model), Path(args.index) if args.index else None, args.device)
    print(json.dumps({"ok": True, "listening": f"http://{args.host}:{args.port}",
                      "model": engine.model.name,
                      "loaded_ms": round(engine.loaded_ms, 1)}, ensure_ascii=False), flush=True)
    stop = threading.Event()
    srv = ThreadingHTTPServer((args.host, args.port), make_handler(engine, stop))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        stop.wait()
    except KeyboardInterrupt:
        pass
    srv.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
