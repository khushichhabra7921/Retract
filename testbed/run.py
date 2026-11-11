"""Run a testbed scenario: python -m testbed.run --scenario priya|poisoned_doc.

Options: --llm fake|groq, --model GROQ_MODEL_ID, --cache FILE, --replay, --store mem0|simple,
--out DIR.
FakeLLM by default (offline, deterministic). `--llm groq` needs GROQ_API_KEY and the `llm` extra;
its responses are read from and added to the --cache file. `--replay` serves Groq answers from
that file only, with no key, and fails on a request the cache does not hold.
`--store mem0` (default) needs the `mem0` and `embed` extras.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path

from retract.derive.fakes import FakeLLM
from testbed.agents import LLM
from testbed.scenarios import SCENARIOS, run_scenario


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m testbed.run")
    ap.add_argument("--scenario", choices=SCENARIOS, required=True)
    ap.add_argument("--llm", choices=("fake", "groq"), default="fake")
    ap.add_argument("--store", choices=("mem0", "simple"), default="mem0",
                    help="backing store for the research agent's private memory")  # fmt: skip
    ap.add_argument("--model", help="Groq model id (default: retract.derive.groq.MODEL)")
    ap.add_argument("--cache", type=Path, default=Path("experiments/cache/groq.jsonl"),
                    help="Groq response cache (JSONL)")  # fmt: skip
    ap.add_argument("--replay", action="store_true",
                    help="Groq answers from --cache only; no key, no network")  # fmt: skip
    ap.add_argument("--out", type=Path, help="run directory (default: runs/<scenario>-<time>)")
    args = ap.parse_args(argv)
    if args.replay and args.llm != "groq":
        ap.error("--replay needs --llm groq")

    for var in ("RETRACT_PSEUDONYM_KEY", "RETRACT_SIGNING_KEY"):
        if not os.environ.get(var):
            # Synthetic data only: a per-run key keeps the run self-contained. Receipts from
            # this run cannot be verified by another process.
            os.environ[var] = secrets.token_hex(32)
            print(f"note: {var} not set; using an ephemeral key for this run", file=sys.stderr)

    llm: LLM
    if args.llm == "groq":
        from retract.derive.cache import ResponseCache
        from retract.derive.groq import MODEL, GroqLLM

        cache = ResponseCache(args.cache)
        llm = GroqLLM(model=args.model or MODEL, cache=cache, replay=args.replay)
    else:
        llm = FakeLLM()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    out = args.out or Path("runs") / f"{args.scenario}-{args.llm}-{stamp}"
    summary = run_scenario(args.scenario, llm, out, store=args.store)
    summary["llm"] = {"name": args.llm, "calls": getattr(llm, "calls", None),
                      "cache_hits": getattr(llm, "cache_hits", None),
                      "replay": args.replay, "model": getattr(llm, "model", None)}  # fmt: skip
    print(json.dumps(summary, indent=2))
    print(f"run directory: {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
