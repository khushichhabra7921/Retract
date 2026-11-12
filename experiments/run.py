"""Run the M8 experiments: python -m experiments.run [--seeds 30] [--workers N] [--out DIR].

FakeLLM only, offline. Every (scenario, seed, config) trial runs in its own process and temp
directory. Writes trials.jsonl (one row per trial), summary.json (intervals) and summary.md.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import tempfile
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from experiments.configs import CONFIGS, MODES, RUNS, run_trial
from experiments.stats import bootstrap_ci, clopper_pearson_upper_zero, paired_bootstrap_ci, wilson
from testbed.scenarios import SCENARIOS

Row = dict[str, Any]


def _job(job: tuple[str, int, str]) -> Row:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        return run_trial(*job, out=Path(d))


def run_all(seeds: int, workers: int, scenarios: tuple[str, ...] = SCENARIOS) -> list[Row]:
    jobs = [(sc, seed, cfg) for sc in scenarios for seed in range(1, seeds + 1) for cfg in RUNS]
    with ProcessPoolExecutor(workers) as pool:
        rows = list(pool.map(_job, jobs))
    check_pairing(rows)
    return rows


def check_pairing(rows: list[Row]) -> None:
    """Every retraction run of a seed must start from the same state, or pairing is broken."""
    befores: dict[tuple[str, int], list[Row]] = {}
    for r in rows:
        if "before" in r:
            befores.setdefault((r["scenario"], r["seed"]), []).append(r["before"])
    for key, states in befores.items():
        if any(s != states[0] for s in states):
            raise RuntimeError(f"unpaired trial {key}: retraction runs saw different worlds")


def states(rows: list[Row], scenario: str, config: str) -> list[tuple[Row, Row | None]]:
    """(measured state, op) per seed; unretracted is the auto run's before state."""
    out: list[tuple[Row, Row | None]] = []
    for r in sorted(rows, key=lambda r: r["seed"]):
        if r["scenario"] != scenario:
            continue
        if config == "unretracted" and r["config"] == "auto":
            out.append((r["before"], None))
        elif r["config"] == config:
            out.append((r["after"], r.get("op")))
    return out


def binary(flags: list[bool]) -> dict[str, Any]:
    k, n = sum(flags), len(flags)
    out: dict[str, Any] = {"k": k, "n": n, "rate": k / n, "wilson95": list(wilson(k, n))}
    if k == 0:
        out["clopper_pearson95_upper_one_sided"] = clopper_pearson_upper_zero(n)
    return out


def continuous(values: list[float]) -> dict[str, Any]:
    return {"mean": sum(values) / len(values), "bootstrap95": list(bootstrap_ci(values))}


METRICS: dict[str, Callable[[Row], float]] = {
    "answer_contamination": lambda s: s["answers_with_target"] / s["answers"],
    "utility": lambda s: s["useful_answers"] / s["utility_questions"],
}
FLAGS: dict[str, Callable[[Row], bool]] = {
    "structural_residue": lambda s: s["raw_items_with_target"]["not_quarantined"] > 0,
    "readable_residue": lambda s: s["readable_records_with_target"] > 0,
    "guarded_retrieval_residue": lambda s: s["probes"]["guarded"] > 0,
    "raw_retrieval_residue_unquarantined": lambda s: s["probes"]["raw_unquarantined"] > 0,
    "raw_retrieval_hits_quarantined": lambda s: s["probes"]["raw_quarantined"] > 0,
    "any_contaminated_answer": lambda s: s["answers_with_target"] > 0,
}


def summarize(rows: list[Row]) -> dict[str, Any]:
    scenarios = sorted({r["scenario"] for r in rows})
    summary: dict[str, Any] = {"n_seeds": len({r["seed"] for r in rows}), "scenarios": {}}
    for sc in scenarios:
        per: dict[str, Any] = {}
        for cfg in CONFIGS:
            st = states(rows, sc, cfg)
            ms = [m for m, _ in st]
            entry: dict[str, Any] = {
                **{name: binary([f(m) for m in ms]) for name, f in FLAGS.items()},
                **{name: continuous([f(m) for m in ms]) for name, f in METRICS.items()},
                "readable_records": continuous([float(m["readable_records"]) for m in ms]),
                "honest_mentions": continuous([float(m["honest_mentions"]) for m in ms]),
            }  # fmt: skip
            if cfg in RUNS and cfg != "clean":
                pairs = [(r["before"], r["after"]) for r in _runs(rows, sc, cfg)]
                entry["record_preservation"] = continuous(
                    [a["readable_records"] / b["readable_records"] for b, a in pairs]
                )
                entry["honest_fact_retention"] = continuous(
                    [a["honest_mentions"] / b["honest_mentions"] for b, a in pairs]
                )
            if cfg in MODES:
                ops = [op for _, op in st if op]
                entry["outcomes"] = dict(Counter(op["outcome"] for op in ops))
                entry["closure_size"] = continuous([float(op["closure"]) for op in ops])
                entry["rederive_llm_calls"] = continuous([float(op["llm_calls"]) for op in ops])
                entry["closure_overapproximation"] = continuous(
                    [op["overapprox"] / op["closure"] if op["closure"] else 0.0 for op in ops]
                )
                entry["over_retraction"] = continuous(
                    [op["overapprox_lost"] / op["closure"] if op["closure"] else 0.0 for op in ops]
                )
            per[cfg] = entry
        if sc == "poisoned_doc":
            per["stages"] = _stages(rows, sc)
        per["paired"] = _paired(rows, sc)
        summary["scenarios"][sc] = per
    return summary


def _runs(rows: list[Row], scenario: str, config: str) -> list[Row]:
    return sorted(
        (r for r in rows if r["scenario"] == scenario and r["config"] == config),
        key=lambda r: r["seed"],
    )


def _stages(rows: list[Row], scenario: str) -> dict[str, Any]:
    """poisoned_doc's four stages, from the auto runs (before = stages 1-3, after = stage 4)."""
    runs = _runs(rows, scenario, "auto")
    return {
        "1_entered_memory": continuous([float(r["stages_before"]["entered_memory"]) for r in runs]),
        "2_promoted_records": continuous(
            [float(sum(r["stages_before"]["retrieved_or_promoted"].values())) for r in runs]
        ),
        "3_influenced_answers": continuous(
            [float(r["before"]["answers_with_target"]) for r in runs]
        ),
        "4_influence_gone": binary([
            r["after"]["answers_with_target"] == 0
            and r["after"]["readable_records_with_target"] == 0
            and r["after"]["raw_items_with_target"]["not_quarantined"] == 0
            for r in runs
        ]),
    }  # fmt: skip


def _paired(rows: list[Row], scenario: str) -> dict[str, Any]:
    """Paired differences by seed, with trial-level bootstrap intervals."""
    out: dict[str, Any] = {}
    for metric in ("utility", "honest_mentions", "readable_records"):

        def value(cfg: str, metric: str = metric) -> list[float]:
            ms = [m for m, _ in states(rows, scenario, cfg)]
            if metric in METRICS:
                return [METRICS[metric](m) for m in ms]
            return [float(m[metric]) for m in ms]

        for a, b in (
            ("auto", "clean"),
            ("delete", "clean"),
            ("quarantine", "clean"),
            ("auto", "delete"),
        ):
            mean, ci = paired_bootstrap_ci(value(a), value(b))
            out[f"{metric}: {a} - {b}"] = {"mean": mean, "bootstrap95": list(ci)}
    return out


def markdown(summary: dict[str, Any]) -> str:
    n = summary["n_seeds"]
    lines = [
        f"# M8 results (FakeLLM, {n} paired seeds)",
        "",
        "Binary columns: trials with the event, k/n, with a two-sided 95% Wilson interval.",
        "Continuous columns: mean over trials, with a 95% trial-level bootstrap interval.",
        "",
    ]
    for sc, per in summary["scenarios"].items():
        lines += [f"## {sc}", "",
                  "| config | structural residue | guarded retrieval residue | contaminated answers"
                  " | utility | record preservation | honest-fact retention | over-retraction |",
                  "|---|---|---|---|---|---|---|---|"]  # fmt: skip
        for cfg in CONFIGS:
            e = per[cfg]
            lines.append(
                f"| {cfg} | {_b(e['structural_residue'])} | {_b(e['guarded_retrieval_residue'])}"
                f" | {_c(e['answer_contamination'])} | {_c(e['utility'])}"
                f" | {_c(e.get('record_preservation'))} | {_c(e.get('honest_fact_retention'))}"
                f" | {_c(e.get('over_retraction'))} |"
            )
        lines.append("")
    return "\n".join(lines)


def _b(e: dict[str, Any]) -> str:
    lo, hi = e["wilson95"]
    return f"{e['k']}/{e['n']} [{lo:.3f}, {hi:.3f}]"


def _c(e: dict[str, Any] | None) -> str:
    if e is None:
        return "n/a"
    lo, hi = e["bootstrap95"]
    return f"{e['mean']:.3f} [{lo:.3f}, {hi:.3f}]"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m experiments.run")
    ap.add_argument("--seeds", type=int, default=30)
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--out", type=Path, default=Path("experiments/results/fake"))
    args = ap.parse_args(argv)
    for var in ("RETRACT_PSEUDONYM_KEY", "RETRACT_SIGNING_KEY"):
        if not os.environ.get(var):  # synthetic data; worker processes inherit the key
            os.environ[var] = secrets.token_hex(32)
    rows = run_all(args.seeds, args.workers)
    summary = summarize(rows)
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "trials.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for r in sorted(rows, key=lambda r: (r["scenario"], r["seed"], r["config"])):
            f.write(json.dumps(r, sort_keys=True) + "\n")
    (args.out / "summary.json").write_bytes((json.dumps(summary, indent=2) + "\n").encode())
    md = markdown(summary)
    (args.out / "summary.md").write_bytes((md + "\n").encode())
    print(md)
    print(f"results: {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
