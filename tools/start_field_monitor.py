"""Start the field monitor detached from the current shell."""

from __future__ import annotations

import subprocess
from pathlib import Path

import psutil


ROOT = Path(r"D:\AIJiYi")
PYTHONW = Path(r"D:\Python313\pythonw.exe")


def main() -> None:
    for process in psutil.process_iter(["pid", "cmdline"]):
        command = " ".join(process.info.get("cmdline") or ())
        if "tools\\field_monitor.py" in command or "tools/field_monitor.py" in command:
            process.terminate()
            try:
                process.wait(timeout=5)
            except psutil.TimeoutExpired:
                process.kill()

    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    child = subprocess.Popen(
        [str(PYTHONW), "tools\\field_monitor.py"],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
    )
    print(f"MONITOR_PID={child.pid}")


if __name__ == "__main__":
    main()
