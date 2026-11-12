"""Command line: `retract audit <path>...`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from retract.audit import audit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="retract")
    sub = parser.add_subparsers(dest="command", required=True)
    p_audit = sub.add_parser("audit", help="flag likely memory writes that bypass the Retract SDK")
    p_audit.add_argument("paths", nargs="+", type=Path)
    p_demo = sub.add_parser("demo", help="Priya demo on the local dashboard (extra: dashboard)")
    p_demo.add_argument("--port", type=int, default=8765)
    p_demo.add_argument("--dir", type=Path, help="lineage DB directory (default: a temp dir)")
    args = parser.parse_args(argv)
    if args.command == "demo":
        try:
            from retract.dashboard.demo import serve
        except ImportError as e:
            print(f"retract demo needs the dashboard extra ({e.name} missing)", file=sys.stderr)
            return 2
        serve(args.port, args.dir)
        return 0

    total, failed = 0, False
    for path in args.paths:
        try:
            findings, errors = audit(path)
        except (OSError, ValueError) as e:
            print(f"retract audit: {e}", file=sys.stderr)
            return 2
        for f in findings:
            print(f)
        for err in errors:
            print(err, file=sys.stderr)
        total += len(findings)
        failed = failed or bool(findings or errors)
    print(f"{total} likely bypass(es) found", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
