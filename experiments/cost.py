"""M8 cost split, measured serially (one trial at a time, no worker pool), on auto retraction.

capture overhead   rt.add (lineage + adapter) vs a raw adapter write of the same content
guarded read       rt.search vs the adapter's raw search, same queries
op                 plan.execute, split into re-derivation (LLM calls), residue probes and the
                   rest (BLOCK, journal, purge, structural verification)

FakeLLM makes re-derivation nearly free, so its share is a lower bound; a hosted LLM adds its
latency per call (the call counts are reported).
"""

from __future__ import annotations

import platform
import statistics
import tempfile
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from experiments.configs import THRESHOLD
from retract import Retract
from retract.adapters.simple import SimpleVectorStore
from retract.derive.fakes import FakeLLM
from retract.probes import residue
from testbed.agents import TIERS, Logs, Testbed
from testbed.data import PERSONAS, Oracle, dataset
from testbed.scenarios import make_stores, setup


class Timer:
    def __init__(self) -> None:
        self.total = 0.0

    @contextmanager
    def __call__(self) -> Iterator[None]:
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.total += time.perf_counter() - t0


def timed(fn: Callable[..., Any], timer: Timer) -> Callable[..., Any]:
    def wrapper(*a: Any, **k: Any) -> Any:
        with timer():
            return fn(*a, **k)

    return wrapper


def _median_us(samples: list[float]) -> float:
    return statistics.median(samples) * 1e6


def trial(scenario: str, seed: int, out: Path) -> dict[str, Any]:
    facts = dataset(seed)
    questions, _, source = setup(scenario, Oracle(facts))
    llm = FakeLLM()
    stores = make_stores(out, "simple")
    rt = Retract(out / "lineage.db", stores=stores, similarity_threshold=THRESHOLD)
    try:
        tb = Testbed(rt, llm, Logs(out), facts=())
        add, raw_write = [], []
        raw = SimpleVectorStore()
        for f in facts:  # same writes as Testbed.ingest, timed one by one
            agent, tier = ("support", "private:support") if f.kind == "user" else ("kb", "kb")
            t0 = time.perf_counter()
            rt.add(f.text, agent=agent, tier=tier, sources=[f.source])
            add.append(time.perf_counter() - t0)
            t0 = time.perf_counter()
            raw.write(f.text, {"retract_token": f.fact_id, "record_id": f.fact_id})
            raw_write.append(time.perf_counter() - t0)
        for (persona, name), q in zip(PERSONAS, questions, strict=True):
            tb.run(persona, name, q)

        guarded, raw_read = [], []
        for q in questions:
            for tier in TIERS:
                t0 = time.perf_counter()
                rt.search(q, tier=tier, k=5)
                guarded.append(time.perf_counter() - t0)
                t0 = time.perf_counter()
                rt.stores[tier].search(q, 5)
                raw_read.append(time.perf_counter() - t0)

        rederive, probes = Timer(), Timer()
        llm.summarize = timed(llm.summarize, rederive)  # type: ignore[method-assign]
        measure = residue.measure
        residue.measure = timed(measure, probes)
        try:
            plan = rt.retract(source, "auto", dry_run=True)
            calls0 = llm.calls
            t0 = time.perf_counter()
            plan.execute()
            op = time.perf_counter() - t0
        finally:
            residue.measure = measure
        return {
            "write_us": {"retract": _median_us(add), "raw": _median_us(raw_write)},
            "read_us": {"guarded": _median_us(guarded), "raw": _median_us(raw_read)},
            "op_s": op,
            "rederive_s": rederive.total,
            "probes_s": probes.total,
            "rest_s": op - rederive.total - probes.total,
            "closure": len(plan.actions),
            "llm_calls": llm.calls - calls0,
        }
    finally:
        rt.close()


def _summary(values: list[float]) -> dict[str, float]:
    q = statistics.quantiles(values, n=4) if len(values) > 1 else [values[0]] * 3
    return {"median": statistics.median(values), "p25": q[0], "p75": q[2]}


def benchmark(seeds: Sequence[int], scenarios: Sequence[str]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "method": "serial, one trial at a time; medians with quartiles over seeds",
        "seeds": list(seeds),
        "machine": {"python": platform.python_version(), "platform": platform.platform()},
        "scenarios": {},
    }
    for sc in scenarios:
        rows = []
        for seed in seeds:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
                rows.append(trial(sc, seed, Path(d)))
        flat = {
            "write_us_retract": [r["write_us"]["retract"] for r in rows],
            "write_us_raw": [r["write_us"]["raw"] for r in rows],
            "write_overhead_x": [r["write_us"]["retract"] / r["write_us"]["raw"] for r in rows],
            "read_us_guarded": [r["read_us"]["guarded"] for r in rows],
            "read_us_raw": [r["read_us"]["raw"] for r in rows],
            "read_overhead_x": [r["read_us"]["guarded"] / r["read_us"]["raw"] for r in rows],
            **{k: [r[k] for r in rows] for k in ("op_s", "rederive_s", "probes_s", "rest_s")},
            "closure": [float(r["closure"]) for r in rows],
            "llm_calls": [float(r["llm_calls"]) for r in rows],
        }
        out["scenarios"][sc] = {k: _summary(v) for k, v in flat.items()}
    return out
