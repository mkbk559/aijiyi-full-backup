"""Restart the local AIJiYi service while preserving its current environment."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import psutil


ROOT = Path(r"D:\AIJiYi")
PORT = 8787


def listener_pid() -> int:
    for connection in psutil.net_connections(kind="tcp"):
        if (
            connection.status == psutil.CONN_LISTEN
            and connection.laddr
            and connection.laddr.port == PORT
            and connection.pid
        ):
            return int(connection.pid)
    raise RuntimeError(f"no listener on port {PORT}")


def main() -> None:
    process = psutil.Process(listener_pid())
    command = " ".join(process.cmdline())
    if "aiji.cli" not in command or "serve" not in command:
        raise RuntimeError(f"refusing to restart unexpected process: {command}")
    environment = process.environ()
    environment.update({
        "AIJI_STICKER_UPDATE": "0",
        "AIJI_STICKER_FEED_URL": (
            "https://cdn.jsdelivr.net/gh/cheems135/dsh-qq-bridge@main/"
            "assets/preset/manifest.json"
        ),
        "AIJI_STICKER_UPDATE_INITIAL": "30",
        "AIJI_STICKER_UPDATE_INTERVAL": "3600",
        "AIJI_MCP_RESEARCH_LOOP": "1",
        "AIJI_MCP_RESEARCH_INITIAL": "60",
        "AIJI_MCP_RESEARCH_INTERVAL": "3600",
    })

    process.terminate()
    try:
        process.wait(timeout=10)
    except psutil.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)

    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    with (ROOT / "data" / "console.out.log").open("ab", buffering=0) as stdout, (
        ROOT / "data" / "console.err.log"
    ).open("ab", buffering=0) as stderr:
        child = subprocess.Popen(
            [sys.executable, "-m", "aiji.cli", "serve"],
            cwd=ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            creationflags=flags,
            close_fds=True,
        )
    time.sleep(2)
    if child.poll() is not None:
        raise RuntimeError("AIJiYi exited during startup")
    print(f"PID={child.pid}")


if __name__ == "__main__":
    main()
