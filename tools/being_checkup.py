"""Run the deterministic or live 'being alive' checkup."""

from __future__ import annotations

import argparse
import json

from aiji.being_checkup import BeingCheckup
from aiji.memory import Memory
from aiji.store import Ident


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/aiji.db")
    parser.add_argument("--config")
    parser.add_argument("--platform", default="qq")
    parser.add_argument("--user", default="2216608312")
    parser.add_argument("--scope", default="default")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()

    memory = Memory.open(args.db, config=args.config)
    try:
        checkup = BeingCheckup(memory)
        ident = Ident(args.platform, args.user, args.scope)
        result = checkup.live_probe(ident) if args.live else checkup.run()
    finally:
        memory.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
