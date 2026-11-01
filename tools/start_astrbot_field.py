"""Start AstrBot with the pinned AIJiYi field-test environment."""

from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime
from pathlib import Path


ASTRBOT = Path(r"D:\AstrBot")
PYTHON = ASTRBOT / ".venv" / "Scripts" / "python.exe"
OTHER_BOT_IDS = ",".join((
    "2501440948",
    "3816866825",
    "3353891315",
    "3542372110",
    "260587282",
    "1981435772",
    "3821918282",
    "3889418604",
    "3071336054",
    "2312998322",
    "3487815005",
    "3076712741",
    "2691380753",
    "3500340273",
    "3072341570",
    "3761273108",
))
TRUSTED_USER_IDS = "8846704,2216608312"


def stop_existing() -> None:
    script = r"""
$listener = Get-NetTCPConnection -State Listen -LocalPort 6185 -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($listener) {
    $child = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
    $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($child.ParentProcessId)" -ErrorAction SilentlyContinue
    Stop-Process -Id $listener.OwningProcess -Force
    if ($parent -and $parent.CommandLine -match "AstrBot|main\.py") {
        Stop-Process -Id $parent.ProcessId -Force -ErrorAction SilentlyContinue
    }
}
"""
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", script],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(2)


def main() -> None:
    stop_existing()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output = ASTRBOT / f"astrbot-stable-{stamp}.out.log"
    error = ASTRBOT / f"astrbot-stable-{stamp}.err.log"
    env = os.environ.copy()
    env.update({
        "PYTHONIOENCODING": "utf-8",
        "AIJI_QQ_MODE": "auto",
        "AIJI_QQ_REPLY_TO_BOTS": "0",
        "AIJI_QQ_OTHER_BOT_IDS": OTHER_BOT_IDS,
        "AIJI_QQ_TRUSTED_USER_IDS": TRUSTED_USER_IDS,
        "AIJI_QQ_ALIAS_CLONES_ARE_BOTS": "1",
        "AIJI_QQ_BOT_CHAT_IDS": (
            "2501440948,3816866825,3353891315,3542372110,"
            "260587282,3821918282,3487815005"
        ),
        "AIJI_QQ_BOT_CHAT_CHANCE": "0.4",
        "AIJI_QQ_BOT_CHAT_COOLDOWN": "60",
        "AIJI_QQ_BOT_CHAT_DAILY": "40",
        "AIJI_QQ_ATTENTION": "1",
        "AIJI_QQ_ATTENTION_DEBOUNCE": "3",
        "AIJI_QQ_ATTENTION_DELAY_MIN": "1",
        "AIJI_QQ_ATTENTION_DELAY_MAX": "3",
        "AIJI_QQ_BACKFILL": "0",
        "AIJI_AUTO_LEARN": "0",
        "AIJI_QQ_AUTO_INTERJECT_CHANCE": "0.35",
        "AIJI_QQ_AUTO_ENGAGED_CHANCE": "0.9",
        "AIJI_QQ_AUTO_COOLDOWN": "15",
        "AIJI_QQ_AUTO_HOURLY_MAX": "20",
        "AIJI_QQ_AUTO_DAILY_MAX": "120",
        "AIJI_QQ_AUTO_MIN_RECENT": "2",
        "AIJI_QQ_AUTO_QUIET_START": "3",
        "AIJI_QQ_AUTO_QUIET_END": "5",
        "AIJI_QQ_AUTO_BURST_MAX": "10",
        "AIJI_QQ_AUTO_BURST_WINDOW": "180",
        "AIJI_QQ_AUTO_ENGAGED_SECONDS": "180",
        "AIJI_QQ_STICKER_COLLECT": "0",
        "AIJI_STICKER_CHANCE": "0.35",
        "AIJI_STICKER_MOOD_CHANCE": "0.85",
        "AIJI_STICKER_COOLDOWN": "90",
        "AIJI_STICKER_DAILY": "30",
        "AIJI_QQ_PROACTIVE_VOICE_CHANCE": "0.45",
        "AIJI_QQ_PROACTIVE_STICKER_CHANCE": "0.4",
        "AIJI_QQ_RANDOM_VOICE_CHANCE": "0.15",
        "AIJI_HEART_SILENCE": "180",
        "AIJI_HEART_COOLDOWN": "900",
        "AIJI_HEART_DAILY": "12",
        "AIJI_HEART_INTERVAL": "90",
        "AIJI_QQ_REPLY_MODE": "auto",
    })
    flags = (
        subprocess.DETACHED_PROCESS
        | subprocess.CREATE_NEW_PROCESS_GROUP
    )
    with output.open("ab", buffering=0) as stdout, (
        error.open("ab", buffering=0)
    ) as stderr:
        process = subprocess.Popen(
            [str(PYTHON), "main.py"],
            cwd=ASTRBOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            creationflags=flags,
            close_fds=True,
        )
    print(f"PID={process.pid}")
    print(f"OUT={output}")
    print(f"ERR={error}")


if __name__ == "__main__":
    main()
