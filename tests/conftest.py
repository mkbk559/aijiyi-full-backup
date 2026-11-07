"""pytest 全局夹具：修 tempfile.mkdtemp 在本机（DSH 沙箱 + Windows ACL）的坑。

2026-11-07 根因实锤（.tmp A/B 对照实验）：
- `tempfile.mkdtemp()`（含 `TemporaryDirectory()` 内部）建出的目录带 0o700 语义，
  同进程的 sqlite 立即 "unable to open database file"，退出清理时 rmtree 被拒
  （WinError 5）——上一轮全量 453 个环境性报错、本轮 65 个 FAILED 的共同根因。
- `os.makedirs(..., mode=0o777)` 建的同样位置一切正常。

修法：在收集前把 `tempfile.mkdtemp` / `TemporaryDirectory` 换成等价的
makedirs 实现（行为一致：唯一名、自动清理；只有权限语义变了）。
生产代码不受影响（aiji 运行时不 import tests/）。
"""
from __future__ import annotations

import itertools
import shutil
import tempfile
import uuid
from pathlib import Path


def _safe_mkdtemp(suffix: str = "", prefix: str = "tmp", dir=None):  # noqa: A002
    base = Path(dir) if dir else Path(tempfile.gettempdir())
    path = base / f"{prefix}{uuid.uuid4().hex[:12]}{suffix}"
    path.mkdir(parents=True, exist_ok=True, mode=0o777)
    return str(path)


_counter = itertools.count()


def _safe_TemporaryDirectory(*args, **kwargs):  # noqa: N802
    # 兼容 tempfile.TemporaryDirectory 的常用姿势：位置参数 suffix/prefix/dir
    suffix = kwargs.pop("suffix", "")
    prefix = kwargs.pop("prefix", "tmp")
    dir_ = kwargs.pop("dir", None)
    if args:
        positional = ["suffix", "prefix", "dir"]
        for value, name in zip(args, positional):
            if name == "suffix":
                suffix = value
            elif name == "prefix":
                prefix = value
            else:
                dir_ = value
    path = Path(_safe_mkdtemp(suffix=suffix, prefix=prefix, dir=dir_))

    class _Ctx:
        def __enter__(self):
            return str(path)

        def __exit__(self, *exc):
            shutil.rmtree(path, ignore_errors=True)
            return False

        @property
        def name(self):
            return str(path)

        def cleanup(self):
            shutil.rmtree(path, ignore_errors=True)

    return _Ctx()


tempfile.mkdtemp = _safe_mkdtemp
tempfile.TemporaryDirectory = _safe_TemporaryDirectory
