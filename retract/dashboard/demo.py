"""`retract demo`: the Priya world on the local dashboard, with one failed operation to resume.

Three users' facts, a shared weekly summary, and a planning step that writes a plan and a reminder
(the reminder is about Arjun, but the step also read the summary, so lineage ties it to everyone).
Retracting Mei then fails mid-repair (the fake LLM goes down), leaving a failed operation whose
records stay held until you press Resume. Everything runs offline on FakeLLM.
"""

from __future__ import annotations

import os
import secrets
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from retract import Record, Retract, src
from retract.adapters.simple import SimpleVectorStore
from retract.core.errors import RetractError
from retract.derive.fakes import FakeLLM

SUPPORT, PLANNING, SHARED = "private:support", "private:planning", "shared"
FACTS = (
    ("priya", "Priya prefers WhatsApp for follow-ups."),
    ("priya", "Priya's company is migrating to AWS in Q2."),
    ("arjun", "Arjun's renewal is due in March."),
    ("arjun", "Arjun asked for invoices in Hindi."),
    ("mei", "Mei's team escalates through Slack."),
    ("mei", "Mei requested a demo of the audit export."),
)


def build(path: Path) -> Retract:
    llm = FakeLLM()
    rt = Retract(
        path / "lineage.db", stores={t: SimpleVectorStore() for t in (SUPPORT, PLANNING, SHARED)}
    )

    def summarize(prefix: str) -> Callable[[list[Record]], str]:
        return lambda inputs: prefix + llm.summarize([r.content or "" for r in inputs])

    weekly = rt.derives("weekly_summary", agent="support", tier=SHARED)(
        summarize("Weekly summary: ")
    )
    follow_up = rt.derives("follow_up_plan", agent="planning", tier=PLANNING)(summarize("Plan: "))
    reminder = rt.derives("reminder", agent="planning", tier=PLANNING)(summarize("Reminder: "))

    facts = [rt.add(text, agent="support", tier=SUPPORT, sources=[src.user(f"u_{who}")])
             for who, text in FACTS]  # fmt: skip
    weekly(inputs=facts)
    with rt.step("planning", PLANNING):
        follow_up(inputs=rt.search("whatsapp follow-ups", tier=SHARED, k=1))
        reminder(inputs=[rt.get(facts[2].record_id)])

    llm.fail_after = llm.calls  # the LLM goes down during the repair...
    try:
        rt.retract(src.user("u_mei"))
    except RetractError:
        pass  # ...so the operation ends `failed`, its records held
    llm.fail_after = None  # back up: Resume will succeed
    return rt


def ensure_keys() -> None:
    for var in ("RETRACT_PSEUDONYM_KEY", "RETRACT_SIGNING_KEY"):
        if not os.environ.get(var):
            os.environ[var] = secrets.token_hex(32)
            print(f"note: {var} not set; using an ephemeral key for this demo", file=sys.stderr)


def serve(port: int, directory: Path | None = None) -> None:
    import uvicorn

    from retract.dashboard.app import create_app

    ensure_keys()
    path = directory or Path(tempfile.mkdtemp(prefix="retract-demo-"))
    path.mkdir(parents=True, exist_ok=True)
    app = create_app(lambda: build(path))
    print(f"Retract demo on http://127.0.0.1:{port}  (lineage: {path / 'lineage.db'})")
    print("Try: Operations > the failed op > Resume; then Sources > user:u_priya > preview.")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
