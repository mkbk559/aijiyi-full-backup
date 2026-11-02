"""把 RVC 包装成 AIJiYi `voice_service.postprocess.command` 能直接调的形式。

用法（由 profile.json 的 postprocess.command 调，也可手动跑）：
    <rvc venv python> tools/rvc_convert.py <in.wav> <out.wav> [--pitch 0] [--index-rate 0.75]

为什么要这个包装：RVC 的 `infer/cli.py` 必须能 `import infer`，而这只有在
`voice-engine/rvc` 作为 cwd（或进 sys.path）时才成立。AIJiYi 调用子进程时 cwd 是项目根，
所以这里替它切目录 + 设环境变量。

shortcut: 每次调用都要重新加载模型（实测 ~20 s：载入 0.6 s + F0 4~6 s + 合成 3.5 s），
因为 RVC 的 cli 没有常驻模式。要压到每次 5 s 内，得自己写一个常驻服务（把 VC 对象留住，
读一次模型反复推理）——等实时对话真的需要时再做。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RVC = ROOT / "voice-engine" / "rvc"
MODEL = RVC / "assets" / "weights" / "dafeiyu.pth"
INDEX = RVC / "assets" / "indices" / "dafeiyu_added_IVF948_Flat_nprobe_1_dafeiyu_v2.index"


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: rvc_convert.py <in.wav> <out.wav> [extra cli args]", file=sys.stderr)
        return 2
    src, dst = argv[0], argv[1]
    extra = argv[2:]

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


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
