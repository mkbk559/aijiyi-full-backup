"""测试临时目录的统一创建与清理（Windows 上 rmtree 会被 SQLite 句柄顶住）。

为什么需要它：`tempfile.mkdtemp()` + `shutil.rmtree(ignore_errors=True)` 在本机
（Windows + 沙箱 ACL）经常删不干净——`style.db` / `life.db` 的句柄还没释放，
rmtree 静默失败，于是 `%TEMP%` 里堆了 331 个目录、82 MB（2026-11-03 实测）。

用法：
    from tests._tempdir import make_tmp
    self.tmp = make_tmp(self, "aiji-style-")     # 自动注册"重试 + 进程退出兜底"清理
"""
from __future__ import annotations

import atexit
import gc
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any


def rmtree_retry(path: Path | str, attempts: int = 8, delay: float = 0.2) -> bool:
    """删目录，句柄没放开时重试几次；返回是否删干净。"""
    target = Path(path)
    for _ in range(attempts):
        gc.collect()
        try:
            shutil.rmtree(target)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            time.sleep(delay)
    return not target.exists()


def _sweep(path: Path) -> None:
    """最后兜底：删不掉就挂到进程退出时再删一次（那时句柄都放了）。"""
    if rmtree_retry(path, attempts=2, delay=0.05):
        return
    atexit.register(lambda p=path: rmtree_retry(p, attempts=3, delay=0.05))


def make_tmp(case: Any, prefix: str = "aiji-test-") -> Path:
    """建临时目录并把它挂到用例的清理链上（tearDown 之后才删，删不掉留到进程退出）。

    2026-11-07：`tempfile.mkdtemp()` 在本机（DSH 沙箱 + Windows ACL）建出的目录
    带 0o700 语义，同进程的 sqlite 都打不开（unable to open database file），
    全套件 453 个同类报错的根因。改用 `os.makedirs`（0o777）——行为一致，
    但 sqlite 能写。位置仍走 tempfile.gettempdir()，所以设 TMP 环境变量依然有效。
    """
    root = Path(tempfile.gettempdir())
    path = root / f"{prefix}{uuid.uuid4().hex[:12]}"
    path.mkdir(parents=True, exist_ok=True, mode=0o777)
    case.addCleanup(_sweep, path)
    return path
