"""Scale benchmark: does Retract's own cost grow with the size of memory?

World: U users, each owning 4 primary records, and one team note per pair of users, derived
from both users' records (8 inputs). N = 4.5 U records. Retracting a user deletes their 4
records and re-derives one note, so the closure is 5 records at every N.

Per size and backend:
  write_ms     median Retract write latency over the last 500 primary writes of the build
  read_ms      median guarded search minus the raw adapter search, same queries
  op_s         one auto retraction, split into residue probes and the rest
  parallel_s   4 retractions with disjoint closures, one after another vs all at once on 4
               threads, each re-derivation waiting LLM_DELAY seconds like a hosted LLM call.
               Threads overlap only waiting: CPU-bound work holds the GIL. Separate processes
               would overlap that too, but the in-memory adapter cannot be shared across them.

The adapter is SimpleVectorStore with a 32-dim FakeEmbedder. Its search is a linear scan, so
at large N the probe time is mostly this toy adapter's; a real vector store indexes.

    python -m experiments.scale [--sizes 1000 10000 100000] [--pg postgresql://...]
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import secrets
import statistics
import tempfile
import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from experiments.cost import Timer, timed
from retract import Retract, src
from retract.adapters.simple import SimpleVectorStore
from retract.core.models import Record
from retract.derive.fakes import FakeEmbedder
from retract.probes import residue

SUPPORT, SHARED = "private:support", "shared"
LLM_DELAY = 1.0
OUT = Path("experiments/results/scale.json")


def fact(i: int, j: int) -> str:
    # Few shared words on purpose: near-duplicate templates sit close to the similarity
    # threshold and draw false alarms (RESULTS 6.12), and this benchmark measures cost.
    return f"p{i:06d} picked c{i:06d}{j}."


def register(rt: Retract, delay: float) -> Callable[..., Record]:
    def note(inputs: list[Record]) -> str:
        time.sleep(delay)  # a hosted LLM call
        return "Team note. " + " ".join(r.content or "" for r in inputs)  # prefix: own sentence

    return rt.derives("team_note", agent="planning", tier=SHARED)(note)


def build(rt: Retract, users: int) -> dict[str, float]:
    note = register(rt, 0.0)
    lat: list[float] = []
    t0 = time.perf_counter()
    for pair in range(0, users, 2):
        owned = []
        for i in (pair, pair + 1):
            for j in range(4):
                w = time.perf_counter()
                owned.append(
                    rt.add(fact(i, j), agent="support", tier=SUPPORT, sources=[src.user(f"u{i}")])
                )
                lat.append(time.perf_counter() - w)
        note(inputs=owned)
    total = time.perf_counter() - t0
    return {
        "build_s": total,
        "writes_per_s": len(lat) * 1.125 / total,  # primaries plus one note per 8
        "write_ms": statistics.median(lat[-500:]) * 1e3,
    }


def read_overhead(rt: Retract, users: int) -> dict[str, float]:
    guarded, raw = [], []
    for q in range(20):
        query = f"picked c{(q * 7919) % users:06d}1"
        for tier in (SUPPORT, SHARED):
            t0 = time.perf_counter()
            rt.search(query, tier=tier, k=5)
            guarded.append(time.perf_counter() - t0)
            t0 = time.perf_counter()
            rt.stores[tier].search(query, 5)
            raw.append(time.perf_counter() - t0)
    return {
        "read_ms_raw": statistics.median(raw) * 1e3,
        "read_ms_extra": (statistics.median(guarded) - statistics.median(raw)) * 1e3,
    }


def one_retraction(rt: Retract, user: int) -> dict[str, Any]:
    probes = Timer()
    measure = residue.measure
    residue.measure = timed(measure, probes)
    try:
        t0 = time.perf_counter()
        receipt = rt.retract(src.user(f"u{user}"))
        op = time.perf_counter() - t0
    finally:
        residue.measure = measure
    return {
        "op_s": op,
        "probes_s": probes.total,
        "rest_s": op - probes.total,
        "closure": len(receipt.body["actions"]),
        "outcome": receipt.outcome,
    }


def parallel(db: str | Path, stores: dict[str, SimpleVectorStore], users: int) -> dict[str, Any]:
    """Users from distinct pairs, so closures are disjoint: 4 one after another, 4 at once."""
    serial_users = [users // 2 + 2 * k for k in range(4)]
    together_users = [users // 2 + 8 + 2 * k for k in range(4)]
    rt = Retract(db, stores=stores)
    register(rt, LLM_DELAY)
    t0 = time.perf_counter()
    outcomes = [rt.retract(src.user(f"u{u}")).outcome for u in serial_users]
    serial = time.perf_counter() - t0
    rt.close()

    def run(u: int, out: list[str]) -> None:
        try:  # every thread reports, so a failure shows up in the outcomes
            w = Retract(db, stores=stores)  # one connection per thread, like separate processes
            try:
                register(w, LLM_DELAY)
                out.append(w.retract(src.user(f"u{u}")).outcome)
            finally:
                w.close()
        except Exception as e:
            out.append(f"{type(e).__name__}: {e}"[:200])

    together: list[str] = []
    threads = [threading.Thread(target=run, args=(u, together)) for u in together_users]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    at_once = time.perf_counter() - t0
    return {
        "serial_s": serial,
        "parallel_s": at_once,
        "speedup": serial / at_once,
        "outcomes": dict(Counter(outcomes + together)),  # 8 retractions in all
    }


def run_size(db: str | Path, users: int) -> dict[str, Any]:
    stores = {t: SimpleVectorStore(embed=FakeEmbedder(dim=32)) for t in (SUPPORT, SHARED)}
    rt = Retract(db, stores=stores)
    try:
        row: dict[str, Any] = {"records": users * 9 // 2, "users": users}
        row |= build(rt, users)
        row |= read_overhead(rt, users)
        row |= one_retraction(rt, 0)
    finally:
        rt.close()
    row["parallel"] = parallel(db, stores, users)
    return row


@contextmanager
def target(backend: str, pg: str | None) -> Iterator[str | Path]:
    """A fresh lineage store: a temp SQLite file, or a new schema dropped afterwards."""
    if backend == "sqlite":
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            yield Path(d) / "lineage.db"
        return
    import psycopg

    assert pg is not None
    schema = "scale_" + uuid.uuid4().hex[:8]
    with psycopg.connect(pg, autocommit=True) as c:
        c.execute(f"CREATE SCHEMA {schema}")
    try:
        yield f"{pg}{'&' if '?' in pg else '?'}options=-csearch_path%3D{schema}"
    finally:
        with psycopg.connect(pg, autocommit=True) as c:
            c.execute(f"DROP SCHEMA {schema} CASCADE")


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sizes", type=int, nargs="+", default=[1_000, 10_000, 100_000])
    ap.add_argument("--pg", help="Postgres URL; also run every size on Postgres")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    for var in ("RETRACT_PSEUDONYM_KEY", "RETRACT_SIGNING_KEY"):
        if not os.environ.get(var):  # synthetic data, as in experiments.run
            os.environ[var] = secrets.token_hex(32)
    rows = []
    for backend in ["sqlite"] + (["postgres"] if args.pg else []):
        for n in args.sizes:
            users = max(32, n * 2 // 9 // 2 * 2)  # even, and enough for the parallel users
            with target(backend, args.pg) as db:
                row = {"backend": backend, **run_size(db, users)}
            rows.append(row)
            print(json.dumps(row), flush=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "machine": {"python": platform.python_version(), "platform": platform.platform()},
        "llm_delay_s": LLM_DELAY,
        "rows": rows,
    }
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
