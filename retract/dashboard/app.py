"""Local dashboard (extra `dashboard`): records, lineage, the retraction flow and operations.

Serve it on 127.0.0.1 only (see `retract demo`). Every Retract call runs on one dedicated worker
thread: a SQLite connection belongs to the thread that opened it, and a single thread also
serializes requests. Every state-changing request is a POST carrying the per-process CSRF token.
"""

from __future__ import annotations

import asyncio
import hmac
import secrets
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any, TypeVar, cast

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from jinja2 import DictLoader, Environment, select_autoescape

from retract import Retract
from retract.core.errors import (
    OperationInProgressError,
    RetractError,
    RetractionFailedError,
    StalePlanError,
)
from retract.core.models import Mode, Plan
from retract.dashboard.templates import TEMPLATES

T = TypeVar("T")
MODES = ("auto", "delete", "quarantine")
STALE = (
    "The affected closure changed since your preview, so nothing was touched."
    " Review the new preview and confirm again."
)


def create_app(factory: Callable[[], Retract]) -> FastAPI:
    """`factory` builds the Retract instance; it runs on the worker thread that owns it."""
    worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="retract")
    rt = worker.submit(factory).result()
    csrf = secrets.token_urlsafe(32)
    env = Environment(loader=DictLoader(TEMPLATES), autoescape=select_autoescape(default=True))

    async def run(fn: Callable[[], T]) -> T:
        return await asyncio.get_running_loop().run_in_executor(worker, fn)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await run(rt.close)
        worker.shutdown()

    app = FastAPI(
        title="Retract dashboard",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.rt, app.state.csrf = rt, csrf

    def page(name: str, status: int = 200, **ctx: Any) -> HTMLResponse:
        ctx.setdefault("title", name)
        return HTMLResponse(env.get_template(name).render(csrf=csrf, **ctx), status_code=status)

    def forbidden(token: str) -> HTMLResponse | None:
        if hmac.compare_digest(token.encode(), csrf.encode()):
            return None
        return page("error", 403, title="forbidden", heading="Missing or invalid CSRF token")

    @app.get("/", response_class=HTMLResponse)
    async def records() -> HTMLResponse:
        return page("records", title="Records", rows=await run(lambda: _records(rt)))

    @app.get("/records/{record_id}", response_class=HTMLResponse)
    async def record(record_id: str) -> HTMLResponse:
        ctx = await run(lambda: _record(rt, record_id))
        if ctx is None:
            return page("error", 404, heading=f"No record {record_id}")
        return page("record", title=f"Record {record_id[:8]}", **ctx)

    @app.get("/sources", response_class=HTMLResponse)
    async def sources() -> HTMLResponse:
        return page("sources", title="Sources", rows=await run(lambda: _sources(rt)))

    @app.get("/retract", response_class=HTMLResponse)
    async def preview(source: str = "", mode: str = "auto") -> HTMLResponse:
        if mode not in MODES:
            return page("error", 400, heading=f"Unknown mode {mode!r}")
        plan = await run(lambda: _plan(rt, source, mode)) if source else None
        return page("retract", title="Retract", source=source, mode=mode, modes=MODES, plan=plan)

    @app.post("/retract", response_model=None)
    async def confirm(
        csrf_token: str = Form("", alias="csrf"),
        source: str = Form(...),
        mode: str = Form(...),
        closure_hash: str = Form(...),
    ) -> Response:
        if denied := forbidden(csrf_token):
            return denied
        if mode not in MODES:
            return page("error", 400, heading=f"Unknown mode {mode!r}")

        def execute() -> tuple[str, Any]:
            plan = _plan(rt, source, mode)
            if plan.noop_op_id:
                return "op", plan.noop_op_id
            if plan.closure_hash != closure_hash:
                return "stale", plan
            try:
                return "op", plan.execute().op_id
            except StalePlanError:
                return "stale", _plan(rt, source, mode)
            except OperationInProgressError as e:
                return "blocked", e.op_id
            except RetractionFailedError as e:
                return "op", e.op_id
            except RetractError as e:
                op = rt.journal.op_for_source(rt.source_ref(source))
                return ("op", op["op_id"]) if op is not None else ("error", str(e))

        kind, value = await run(execute)
        if kind == "stale":
            return page(
                "retract", 409, title="Retract", source=source, mode=mode, modes=MODES,
                plan=value, message=STALE, message_kind="warn",
            )  # fmt: skip
        if kind == "error":
            return page("error", 409, heading=f"Retraction failed: {value}")
        query = "?blocked=1" if kind == "blocked" else ""
        return RedirectResponse(f"/ops/{value}{query}", status_code=303)

    @app.get("/ops", response_class=HTMLResponse)
    async def ops() -> HTMLResponse:
        return page("ops", title="Operations", rows=await run(lambda: _ops(rt)))

    @app.get("/ops/{op_id}", response_class=HTMLResponse)
    async def op(op_id: str, blocked: int = 0, resume_failed: int = 0) -> HTMLResponse:
        ctx = await run(lambda: _op(rt, op_id))
        if ctx is None:
            return page("error", 404, heading=f"No operation {op_id}")
        if blocked:
            ctx["message"] = (
                "Your retraction needs records this unfinished operation still holds. Resume"
                " it first, then preview your retraction again."
            )
            ctx["message_kind"] = "warn"
        elif resume_failed:
            ctx["message"], ctx["message_kind"] = "Resume failed; see the error below.", "err"
        return page("op", title=f"Operation {op_id[:8]}", **ctx)

    @app.post("/ops/{op_id}/resume", response_model=None)
    async def resume(op_id: str, csrf_token: str = Form("", alias="csrf")) -> Response:
        if denied := forbidden(csrf_token):
            return denied

        def go() -> bool:
            try:
                rt.resume(op_id)
                return True
            except RetractError:
                return False

        ok = await run(go)
        return RedirectResponse(f"/ops/{op_id}{'' if ok else '?resume_failed=1'}", status_code=303)

    @app.get("/ops/{op_id}/receipt.json", response_model=None)
    async def receipt_json(op_id: str) -> Response:
        receipt = await run(lambda: rt.receipt(op_id))
        if receipt is None:
            return page("error", 404, heading=f"No receipt for {op_id}")
        return Response(receipt.export(), media_type="application/json")

    return app


# -- queries (run on the worker thread) --------------------------------------------------------


def _plan(rt: Retract, source: str, mode: str) -> Plan:
    return rt.retract(source, cast(Mode, mode), dry_run=True)  # mode is checked against MODES


def _labels(rt: Retract) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for rid, sid in rt.lineage.conn.execute("SELECT record_id, source_id FROM labels"):
        out.setdefault(rid, []).append(sid)
    return out


def _records(rt: Retract) -> list[dict[str, Any]]:
    conn = rt.lineage.conn
    labels = _labels(rt)
    derived = {r[0] for r in conn.execute("SELECT DISTINCT child_id FROM edges")}
    rows = conn.execute(
        "SELECT record_id, agent, tier, status, hold_op_id, content FROM records"
        " ORDER BY created_at, rowid"
    )
    return [
        {
            "record_id": r[0],
            "agent": r[1],
            "tier": r[2],
            "status": r[3],
            "held": r[4] is not None,
            "content": r[5],
            "labels": sorted(labels.get(r[0], [])),
            "derived": r[0] in derived,
        }  # fmt: skip
        for r in rows
    ]


def _record(rt: Retract, record_id: str) -> dict[str, Any] | None:
    conn = rt.lineage.conn
    rec = rt.lineage.records([record_id]).get(record_id)
    if rec is None:
        return None
    hold, fn_op, fn_version, supersedes = conn.execute(
        "SELECT hold_op_id, fn_op, fn_version, supersedes FROM records WHERE record_id = ?",
        (record_id,),
    ).fetchone()
    edges = conn.execute(
        "SELECT child_id, kind FROM edges WHERE parent_id = ? ORDER BY child_id", (record_id,)
    ).fetchall()
    kids = rt.lineage.records([c for c, _ in edges])
    return {
        "rec": rec,
        "held": hold,
        "fn": f"{fn_op} v{fn_version}" if fn_op else None,
        "supersedes": supersedes,
        "replaced_by": [
            r[0]
            for r in conn.execute(
                "SELECT record_id FROM records WHERE supersedes = ?", (record_id,)
            )
        ],
        "children": [(kids[c], kind) for c, kind in edges if c in kids],
        "tree": _ancestors(rt, record_id, None, set()),
    }


def _ancestors(rt: Retract, record_id: str, kind: str | None, seen: set[str]) -> dict[str, Any]:
    """Lineage up to the primaries; a node reached twice is shown once."""
    conn = rt.lineage.conn
    node: dict[str, Any] = {
        "record": rt.lineage.records([record_id])[record_id],
        "kind": kind, "seen": record_id in seen, "parents": [], "sources": [],
    }  # fmt: skip
    if node["seen"]:
        return node
    seen.add(record_id)
    parents = conn.execute(
        "SELECT parent_id, kind FROM edges WHERE child_id = ? ORDER BY position", (record_id,)
    ).fetchall()
    node["parents"] = [_ancestors(rt, p, k, seen) for p, k in parents]
    if not parents:
        node["sources"] = sorted(
            r[0]
            for r in conn.execute(
                "SELECT source_id FROM explicit_sources WHERE record_id = ?", (record_id,)
            )
        )
    return node


def _known_sources(rt: Retract) -> list[str]:
    q = "SELECT DISTINCT source_id FROM explicit_sources ORDER BY source_id"
    return [r[0] for r in rt.lineage.conn.execute(q)]


def _sources(rt: Retract) -> list[dict[str, Any]]:
    out = []
    for s in _known_sources(rt):
        primaries = rt.lineage.conn.execute(
            "SELECT COUNT(*) FROM explicit_sources e WHERE e.source_id = ?"
            " AND NOT EXISTS (SELECT 1 FROM edges WHERE child_id = e.record_id)",
            (s,),
        ).fetchone()[0]
        op = rt.journal.op_for_source(rt.source_ref(s))
        reached = rt.lineage.records(sorted(rt.closure(s))).values()
        out.append(
            {"source": s, "primaries": primaries,
             "closure": sum(r.status not in ("retracted", "tombstoned") for r in reached),
             "op": op["op_id"] if op is not None else None,
             "op_state": "retracted" if op is not None and op["state"] == "finalized"
             else (op["state"] if op is not None else None)}
        )  # fmt: skip
    return out


def _ops(rt: Retract) -> list[dict[str, Any]]:
    names = {rt.source_ref(s): s for s in _known_sources(rt)}
    rows = rt.lineage.conn.execute(
        "SELECT op_id, source_ref, mode, state, outcome, error, created_at FROM operations"
        " ORDER BY created_at DESC, rowid DESC"
    )
    return [
        {"op_id": r[0], "source": names.get(r[1], r[1][:12] + "…"), "mode": r[2],
         "state": r[3], "outcome": r[4], "error": r[5], "created_at": r[6]}
        for r in rows
    ]  # fmt: skip


def _op(rt: Retract, op_id: str) -> dict[str, Any] | None:
    op = rt.journal.get(op_id)
    if op is None:
        return None
    names = {rt.source_ref(s): s for s in _known_sources(rt)}
    receipt = rt.receipt(op_id)
    ctx: dict[str, Any] = {
        "op": dict(op), "source": names.get(op["source_ref"], op["source_ref"]),
        "actions": rt.journal.actions(op_id), "receipt": receipt,
    }  # fmt: skip
    if receipt is not None:
        v = receipt.body["verification"]
        s, r = v["structural"], v["retrieval_residue"]
        ctx.update(report=receipt.verify(), structural=s, retrieval=r)
        ctx["structural_detail"] = (
            f"{s.get('passed')}/{s.get('total')} checks · source-specific"
            f" {s.get('source', {}).get('status')} · global {s.get('global', {}).get('status')}"
        )
        quarantined = r.get("raw_quarantined_hits", 0)
        ctx["retrieval_detail"] = (
            f"{r['probes']} probes · guarded hits {r.get('guarded_hits', 0)} · raw purged"
            f" {r.get('raw_purged_hits', 0)} · raw unexpected {r.get('raw_unexpected_hits', 0)}"
            f" · raw quarantined {quarantined}"
            + (" (expected: contained, not purged)" if quarantined else "")
            if "probes" in r
            else r.get("reason", "")
        )
    return ctx
