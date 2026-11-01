"""Start the local Seed-VC voice engine with its cached model."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(r"D:\AIJiYi\voice-engine\seed-vc")
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
PID_FILE = ROOT / "aiji-seed-vc.pid"
OUT = ROOT / "aiji-seed-vc.out.log"
ERR = ROOT / "aiji-seed-vc.err.log"


def main() -> None:
    if not PYTHON.is_file():
        raise RuntimeError(f"Seed-VC environment is missing: {PYTHON}")
    cache = ROOT / "checkpoints" / "hf_cache"
    env = os.environ.copy()
    env.update({
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_CACHE": str(cache),
        "HF_HOME": str(cache),
        "TRANSFORMERS_CACHE": str(cache),
        "HF_HUB_DISABLE_SYMLINKS_WARNING": "1",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    with OUT.open("ab", buffering=0) as stdout, (
        ERR.open("ab", buffering=0)
    ) as stderr:
        process = subprocess.Popen(
            [
                str(PYTHON),
                "aiji_seed_vc_server.py",
                "--host", "127.0.0.1",
                "--port", "9880",
                "--device", "auto",
                "--fp16",
            ],
            cwd=ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            creationflags=flags,
            close_fds=True,
        )
    PID_FILE.write_text(str(process.pid), encoding="ascii")
    print(f"SEED_VC_PID={process.pid}")


if __name__ == "__main__":
    main()
