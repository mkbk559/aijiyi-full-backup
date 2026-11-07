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
def _pick_model() -> Path:
    """权重在 11-03 之后改名成了 makabaka*.pth（dafeiyu 是旧名），这里按存在性挑。"""
    weights = RVC / "assets" / "weights"
    for name in ("makabaka.pth", "dafeiyu.pth"):
        p = weights / name
        if p.exists():
            return p
    found = sorted(weights.glob("*.pth"))
    return found[0] if found else weights / "makabaka.pth"


def _pick_index() -> Path:
    """索引同理；找不到就返回一个不存在的路径（VC 那边会当成"不用索引"处理）。"""
    indices = RVC / "assets" / "indices"
    for pat in ("*makabaka*.index", "*dafeiyu*.index"):
        hits = sorted(indices.glob(pat))
        if hits:
            return hits[0]
    return indices / "none.index"


DEFAULT_MODEL = _pick_model()
DEFAULT_INDEX = _pick_index()



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


def split_wav(path: str, chunk_seconds: float) -> list[str]:
    """把 wav 切成 ≤chunk_seconds 的小段（同目录临时文件，调用方负责删）。

    只处理 PCM wav（语音条/点歌素材都是）。切不开就返回原文件一条，
    让调用方退回"整段推理"的老路径。
    """
    import wave

    if chunk_seconds <= 0:
        return [path]
    with wave.open(str(path), "rb") as src:
        frames = src.getnframes()
        rate = src.getframerate() or 1
        params = src.getparams()
        per_chunk = int(chunk_seconds * rate)
        if frames <= per_chunk or per_chunk <= 0:
            return [path]
        total = (frames + per_chunk - 1) // per_chunk
        out: list[str] = []
        base = Path(path)
        for i in range(total):
            data = src.readframes(per_chunk)
            if not data:
                break
            part = base.with_name(f"{base.stem}.part{i:03d}{base.suffix}")
            with wave.open(str(part), "wb") as dst:
                dst.setparams(params)
                dst.writeframes(data)
            out.append(str(part))
        return out


def merge_wav(parts: list[Path], dst: Path) -> None:
    """按第一段的参数把若干 wav 顺序拼成一个。"""
    import wave

    with wave.open(str(parts[0]), "rb") as first:
        params = first.getparams()
        payload = [first.readframes(first.getnframes())]
    for part in parts[1:]:
        with wave.open(str(part), "rb") as fh:
            if fh.getparams()[:3] != params[:3]:
                raise ValueError(
                    f"chunk params mismatch: {fh.getparams()[:3]} != {params[:3]}")
            payload.append(fh.readframes(fh.getnframes()))
    dst.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dst), "wb") as out:
        out.setparams(params)
        for chunk in payload:
            out.writeframes(chunk)


class Engine:
    """把 RVC 的 VC 对象包成"载一次、反复用"。单线程推理（torch CPU 上并发只会更慢）。"""

    def __init__(self, model: Path, index: Path | None, device: str = "cpu",
                 half: bool | None = None, chunk_seconds: float = 10.0):
        self.model = Path(model)
        self.index = str(index) if index else None
        self.device = device
        self.half = half
        self.chunk_seconds = float(chunk_seconds)
        self.last_split_error: str | None = None
        self.is_half = False
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
        # 4 GB 显存上 fp32 会把卡跑满（实测 30.8 s 素材 VRAM 3946/4096 MiB、
        # 换声 65.7 s）。CUDA 下默认开半精度，显存和耗时都减半左右。
        if self.half is None:
            config.is_half = self.device.startswith("cuda")
        else:
            config.is_half = bool(self.half)
        self.is_half = bool(getattr(config, "is_half", False))
        from infer.vc.modules import VC  # noqa: E402

        # faiss 索引每次调用都会被 infer/vc/pipeline.py 重新 read_index（111 MB 的 IVF），
        # 一个来回就是几百毫秒。进程内缓存一份：索引是只读的，命中直接复用。
        self.index_load_ms = None
        try:
            from infer.vc import pipeline as _pipeline  # noqa: E402

            self._index_cache: dict[str, Any] = {}
            _orig_read_index = _pipeline.faiss.read_index

            def _cached_read_index(path: Any, *args: Any, **kwargs: Any) -> Any:
                key = str(path)
                hit = self._index_cache.get(key)
                if hit is not None:
                    return hit
                started = time.perf_counter()
                loaded = _orig_read_index(path, *args, **kwargs)
                self._index_cache[key] = loaded
                self.index_load_ms = round((time.perf_counter() - started) * 1000.0, 1)
                return loaded

            _pipeline.faiss.read_index = _cached_read_index
        except Exception:  # noqa: BLE001 - 缓存不上就算了，不能因此起不来
            self._index_cache = {}

        vc = VC(config)
        vc.get_vc(self.model.name)
        self.vc = vc
        self.loaded_ms = (time.perf_counter() - t0) * 1000.0

    def convert(self, src: str, dst: str, *, pitch: int = 0, index_rate: float = 0.75,
                f0_method: str = "rmvpe", protect: float = 0.33,
                rms_mix_rate: float = 1.0, speaker_id: int = 0,
                chunk_seconds: float | None = None) -> dict[str, Any]:
        from infer.cli import write_audio  # noqa: E402

        chunk = self.chunk_seconds if chunk_seconds is None else float(chunk_seconds)
        t0 = time.perf_counter()
        with self.lock:
            # 长素材一次性推理会把 4 GB 显存打满（实测 30 s → 23.8 s、RTF 0.79），
            # 切成 ≤chunk 秒的小段分别推理再拼回来，耗时近似线性、显存恒定。
            try:
                pieces = split_wav(str(src), chunk)
            except Exception as exc:  # noqa: BLE001 - 分不开就按原文件整段跑
                pieces = []
                self.last_split_error = _short_error(exc)
            if len(pieces) > 1:
                outs: list[Path] = []
                try:
                    for i, piece in enumerate(pieces):
                        status, result = self.vc.vc_single(
                            int(speaker_id), str(piece), int(pitch), str(f0_method),
                            self.index, float(index_rate), 0.0, float(rms_mix_rate),
                            float(protect),
                        )
                        if not result or result[0] is None or result[1] is None:
                            text = str(status)
                            return {"ok": False, "error": _short_error_text(text),
                                    "hint": _hint_for_text(text), "f0_method": str(f0_method),
                                    "chunk": i + 1, "chunks": len(pieces),
                                    "status_code": 400 if "torchfcpe" in text.lower() else 500}
                        part = Path(piece).with_suffix(".out.wav")
                        write_audio(part, result[1], result[0], "wav")
                        outs.append(part)
                    merge_wav(outs, Path(dst))
                    sr = int(result[0])
                    self.calls += 1
                    return {"ok": True, "ms": round((time.perf_counter() - t0) * 1000.0, 1),
                            "out": str(dst), "sr": sr, "status": str(status),
                            "chunks": len(pieces),
                            "chunk_seconds": chunk}
                except Exception as exc:  # noqa: BLE001
                    return {"ok": False, "error": _short_error(exc), "hint": _hint_for(exc),
                            "f0_method": str(f0_method), "chunks": len(pieces)}
                finally:
                    for piece in pieces:
                        Path(piece).unlink(missing_ok=True)
                    for part in outs:
                        part.unlink(missing_ok=True)
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
                "out": str(dst), "sr": int(result[0]), "status": str(status),
                "chunks": 1}


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
                                 "calls": engine.calls, "is_half": engine.is_half,
                                 "chunk_seconds": engine.chunk_seconds,
                                 "index_load_ms": engine.index_load_ms})
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
                    # 单次覆盖分块长度（0 = 整段推理，基准脚本用来对比）
                    chunk_seconds=(float(body["chunk_seconds"])
                                   if body.get("chunk_seconds") is not None else None),
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
    ap.add_argument("--half", default=None, choices=["1", "0"],
                    help="半精度：cuda 下默认 1（4 GB 卡必须），cpu 下无意义")
    ap.add_argument("--chunk-seconds", type=float,
                    default=float(os.environ.get("AIJI_RVC_CHUNK_SECONDS") or 10.0),
                    help="长素材按这个长度切段推理再拼接（0 = 不切）")
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

    half = None if args.half is None else (args.half == "1")
    engine = Engine(Path(args.model), Path(args.index) if args.index else None,
                    args.device, half, args.chunk_seconds)
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
