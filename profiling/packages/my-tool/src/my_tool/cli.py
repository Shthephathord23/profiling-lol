"""Command-line front end for the example workload.

Kept stdlib-only and offline so the harness is testable in a bare container.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

from my_tool import core


def load_records(path: Path) -> List[Dict[str, object]]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise SystemExit(f"{path}: expected a JSON array of objects")
    return data


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="my-tool")
    parser.add_argument("--input", type=Path, required=True, help="JSON array of records")
    parser.add_argument("--iterations", type=int, default=1, help="times to process the batch")
    parser.add_argument(
        "--fail",
        action="store_true",
        help="exit non-zero after working (exercises the harness's failure path)",
    )
    args = parser.parse_args(argv)

    if not args.input.is_file():
        print(f"my-tool: no such input file: {args.input}", file=sys.stderr)
        return 2

    records = load_records(args.input)
    result: Dict[str, object] = {}
    for _ in range(max(1, args.iterations)):
        result = core.process(records)

    print(json.dumps(result, indent=2))

    if args.fail:
        print("my-tool: failing on request (--fail)", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
