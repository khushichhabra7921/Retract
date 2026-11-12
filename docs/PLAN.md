# Retract MVP — PLAN (Rev 3, implementation baseline)

Rev 3 is the implementation baseline. No further architecture revisions unless tests reveal a concrete problem.

## Changelog Rev 2 → Rev 3
- A: EXECUTE now begins with one short `BEGIN IMMEDIATE` BLOCK transaction (ownership, recompute, stale check, tombstone, holds, journal rows). A stale plan rolls back with nothing tombstoned or held.
- A: The global op lock is now a durable logical lock (operation row, `owner_token`, `lease_expires_at`, heartbeat). `resume` is allowed on lease expiry or owner match.
- B: New "Write path" section: pending lineage row → adapter write → activate. New `pending` status. Two new tests (BLOCK race, crash between lineage and adapter).
- C: New "Lineage capture contract": explicit `inputs=` plus the `rt.step` read-set. New over-retraction metric and a step read-set test.
- D: Two IDs (`record_id`, `adapter_record_id`) and a `retract_token` stamped on every adapter write. Adapter gains `find_by_token`, `idempotent_put` and `supports_list_ids`. Resume reconciles via token; non-reconciling adapters are at-least-once.
- D: Mem0 facts from mem0ai 2.2.1 added. The history-DB residue check and per-memory history purge are pending a spike.
- D: New Week 1 Mem0 spike (Nov 9–10) finalizes the adapter interface. Mem0 validation moves earlier than M5.
- E: Probes are generated locally (no LLM), held in memory, never persisted. Each verification dimension is `pass | fail | not_run`. Resume after purge records retrieval as `not_run`. New test `crash_after_purge_before_probes`.
- F: Terminal outcomes are `completed` (retraction receipt), `contained` (containment receipt) and `failed` (no success receipt). The receipt and final op state are written in one transaction. Replacements stay held until their contamination check passes. `outcome` is added to VerificationReport.
- G: L2 and L3 reworded exactly. Notes added on quarantined raw-adapter hits and the Mem0 history L4 note. The audit is "flags likely bypasses" everywhere.
- H: New "Runtime vs experiments" subsection (heuristic runtime check vs independent oracle). FakeLLM leak- and paraphrase-injection modes are used in experiments.
- I: Separate signing and pseudonym keys.
- I: Stats reworded: Wilson / trial-level bootstrap, labeled bounds, 0/30 Wilson ≈ 11.4% and Clopper–Pearson one-sided ≈ 9.5%.
- I: `make experiments` / `experiments-groq` / `experiments-groq-replay`, with a committed cache that carries metadata.
- I: Dashboard uses POST + CSRF.
- I: Test that the guard sees fresh state. Over-fetch utility note.
- I: Windows tooling rules and a Windows clean-clone check in M0.
- J: Capacity note, five hard checkpoints, new cut order, Week 2 Groq budget check, release-gate additions.
- K: The external-validation workstream is removed and replaced by "Validation scope (solo, no external input)". The adoption claim is an untested hypothesis.
- L: New "Deltas vs brief" section.

## Context
Retract is the capstone and startup MVP from `retract-project-brief.md`. It is an open-source Python SDK that records what agents derive from what. `retract(source)` finds every derived record across agents, tiers and stores. It blocks them from reads, repairs them (delete / re-derive / quarantine), and issues a receipt plus residue checks.

This is a solo, part-time build from 9 Nov to 6 Dec 2026.

**Claim we defend:** a vendor-neutral prototype for source-level retraction across instrumented multi-agent memory stores. It combines lineage-aware repair, guarded retrieval, auditable receipts and a controlled evaluation of residual exposure. We claim nothing about dependency tracing, selective replay or provenance-based forgetting being new on their own (see [4], [25]).

**Capacity:** No other work is paused during Nov 9–Dec 6. Capacity is part-time, so the scope checkpoints are mandatory.

## Decisions taken
- **Repo:** `C:\Users\hp\OneDrive\Desktop\Retract` (user decision: all work stays in this folder). Known risk: OneDrive syncing `.venv`, SQLite files and `.git` can cause file locks or slow installs; if that happens, pause OneDrive sync rather than move the repo. The brief is copied to `docs/` and is never edited. Local git commits; no push until the user says so.
- **License:** Apache-2.0.
- **uv:** installed via `pip install --user uv`. The uv PATH step is documented in the README.
- **make:** the user runs `choco install make` once.
- **Groq:** a key is available.
  - FakeLLM runs all configs × 30 paired seeds.
  - Groq runs priya and poisoned_doc with frozen prompts. The seed count is set by the Week 2 budget check.
- **Audit:** fresh stdlib `ast`, without CodeLens.
- **Secrets:** only from the process environment. No `.env` parsing in Make or Python. `.env.example` lists `RETRACT_SIGNING_KEY`, `RETRACT_PSEUDONYM_KEY` and `GROQ_API_KEY`. No key is ever logged or written to receipts or artifacts.

## Trimmed or narrowed vs the review
- **Public modes are `auto | delete | quarantine`.** Re-derive is a per-record action, not a mode.
- **Graph property tests:** a seeded random-DAG generator with stdlib `random`. No `hypothesis`.
- **Concurrency:** one executing retraction at a time, enforced by a **durable logical lock**.
  - The lock is an `operations` row in state `executing` with `owner_token` and `lease_expires_at`, renewed by heartbeat during repair.
  - A second retraction gets `OperationInProgressError(op_id)`.
  - `rt.resume(op_id)` is allowed when the lease has expired or the `owner_token` matches, so a crashed process never blocks future work permanently.
  - `shortcut: one global executing op, upgrade to per-closure locks if parallel retractions matter.`
- **No LangGraph store adapter.** LangGraph does orchestration only; memory goes through Retract adapters. The audit flags likely bypasses via LangGraph `store.put`.

## Guarantee levels (README, dashboard and RESULTS use these exact words)
| Level | MVP |
|---|---|
| L1 Hidden from Retract-guarded reads | Enforced + tested |
| L2 | Purged records are verified absent from supported adapter APIs. Quarantined records may remain physically accessible through the underlying store and are hidden only by Retract-controlled reads. |
| L3 | Not established. `retract audit` provides best-effort detection of potential SDK bypasses but cannot prove complete read/write coverage (dynamic dispatch, indirect clients, external services, uninstrumented access). |
| L4 Physically erased from vector index internals | **Not established** |
| L5 Removed from backups / external caches / model weights | **Not established** |

Notes:
- **Raw-adapter hits are reported in two groups.** Hits on intentionally quarantined records are expected and reported separately from hits on purged records. Only the latter count against L2.
- **L4 and Mem0 history:** Mem0 keeps old memory text in its SQLite history table after DELETE (mem0ai 2.2.1). The spike (Nov 8) confirmed that the Mem0 adapter can purge that memory's history rows by direct SQL with no byte residue, so Mem0 history is not an L4 item. Qdrant-local `storage.sqlite` still holds deleted text in freed pages: that is L4, not established.

## Semantics

### Sources and labels
- **`explicit_sources`:**
  - On a primary record (`rt.add`), they mean ownership.
  - On a derived record, they mean extra source material that entered without a parent record (for example a tool output).
- **Effective labels** = labels of current input records ∪ `explicit_sources`. The SDK computes them; the model never does.
- **Historical vs effective lineage:** a re-derived version gets edges to its *current valid inputs* and a `supersedes` link to the old version. Labels come only from current inputs.
- **Opaque references:**
  - Tombstones and receipts store `source_ref = HMAC(RETRACT_PSEUDONYM_KEY, source_id)`, plus content hashes keyed with the same pseudonym key.
  - `source_ref` stays stable across rotations of the signing key. Losing or rotating the pseudonym key breaks `source_ref` matching (documented).
  - Keyed hashes are pseudonymous evidence, not anonymous data. Testbed IDs are synthetic.

### Lineage capture contract
- **Parent edges come from:**
  1. explicit `inputs=` passed to an `@rt.derives` function;
  2. a **step-scoped read-set**: inside `with rt.step(agent, tier):`, every record returned by guarded `rt.search`/`rt.get` becomes a parent of every write in that step. This over-approximates on purpose.
- **Edge order:** explicit inputs are ordered by `position`; read-set parents are ordered by read time.
- Testbed agents use `rt.step`. Their summaries and notes are written by `@rt.derives` functions called inside a step, which keeps them replayable.
- Anything outside instrumented paths is not tracked (see L3).
- Over-approximation is measured by the M8 **over-retraction rate**: records retracted or quarantined that the oracle says contain no facts from the retracted source.

### Record state
- **`status ∈ {pending, active, quarantined, retracted, tombstoned}`:**
  - `pending` = the write is in progress (see Write path).
  - `tombstoned` = deleted and purged.
  - `retracted` = superseded by a re-derived version, purged.
  - `quarantined` = hidden, with content kept.
- `hold_op_id` (nullable) marks a record held by an executing or failed operation.
- **Guard rule:** readable ⇔ `status = active AND hold_op_id IS NULL`.
- A record becomes `retracted`/`tombstoned` only after its purge is verified.

### Write path (normal `rt.add` / `@rt.derives`, crash-safe)
1. **One transaction:**
   - verify that none of the record's sources or inputs are tombstoned or held
   - insert the lineage row with `status=pending`, its `record_id`, `retract_token` and edges
   - commit
2. **Adapter write**, stamped with metadata `retract_token`. Store the returned `adapter_record_id`.
3. **Flip `status` to `active`.**

Properties:
- Pending records are unreadable.
- The orphan scan reconciles crashes between steps: pending rows with no adapter record, and adapter records with no lineage row.
- This also closes the race where a derive reads its inputs before BLOCK commits but writes after it: step 1 sees the held or tombstoned input and rejects the write.

Repair writes by the engine use the same path. The one difference is that inputs held by **the same `op_id`** are allowed (see open item 1).

### Per-record action rule (affected closure, topological order)
1. A primary record owned by the source → **delete**.
2. A derived record with the retracted source in its own `explicit_sources` → **quarantine** in auto mode, **delete** in delete mode.
3. A derived record with no valid inputs left → **delete**.
4. A derived record with valid inputs remaining:
   - delete mode → **delete** (strict RQ2 baseline);
   - auto mode → **re-derive** if replayable, otherwise **quarantine**.
5. **Replayable** ⇔ the function is registered, `replayable=True`, and the registered `version` equals the stored `fn_version`. Inputs are passed in stored `position` order.
   - Step-only writes with no `@rt.derives` function are not replayable.
   - A missing function, version mismatch or known replay error (`ReplayError`) → **quarantine** and the failure is recorded. Transient and unexpected errors fail the op (see Implementation decisions).
6. **Post-repair check** (runtime heuristic, see below): the replacement must not contain a *forbidden fact*, meaning a retracted fact that no valid input supports.
   - The replacement is created with `hold_op_id` set. The hold is cleared only after the check passes.
   - Hit → the replacement is quarantined, the old version stays held, and the action is marked `repair_unverified`.
7. **Quarantine mode** → quarantine the whole closure.

### Runtime vs experiments
- **Runtime (SDK):** conservative heuristic verification. Normalized sentence-substring match plus embedding similarity at a frozen threshold. Uncertain records are quarantined.
- **Experiments only:** an independent fact-level oracle from `testbed/data.py` (`fact_id`, `owner`, `marker` tokens, 2 paraphrases each). It measures whether each runtime decision was correct.
- README and RESULTS must not imply that the SDK has oracle-grade ability.
- FakeLLM has **leak-injection** mode (re-emits a forbidden fact) and **paraphrase-injection** mode (re-emits it paraphrased). Both are used in tests *and* in M8 experiments to measure runtime check FN/FP.

### Adapter contract and IDs
- **Two identifiers:**
  - `record_id`: logical, owned by Retract.
  - `adapter_record_id`: physical, returned by the store and stored in lineage.
- `record_id` and `retract_token = "op_id:record_id"` (or `"write:record_id"` for normal writes) are journaled **before** the adapter write. Every adapter write stamps `retract_token` into the record's metadata.
- **`MemoryAdapter`:**
  - methods: `write`, `get`, `search`, `delete`, `update`, `contains`, `find_by_token(token)`, `list_ids()` (when `supports_list_ids`);
  - capability flags: `idempotent_put`, `supports_list_ids`.
- **On resume:** call `find_by_token` before retrying a write. If a record is found, adopt it; otherwise write.
- An adapter that cannot reconcile is documented as **at-least-once with orphan cleanup**. Exactly-once is never claimed for it.

### Mem0 notes
**Facts checked against mem0ai 2.2.1** (re-verify on the pinned version):
- `add()` takes no caller-supplied ID; IDs are uuid4 generated internally, hence `adapter_record_id`.
- Mem0's SQLite history table keeps old text (`old_memory`) after DELETE. No per-record purge was found, only a whole-table reset.

**Plan:**
- `add(..., infer=False)`.
- `MEM0_TELEMETRY` is set inside Python *before* `import mem0`.
- A structural/raw residue check scans Mem0's history DB.
- The adapter purges that `memory_id`'s history rows by direct SQL (confirmed by the spike).

**Spike findings (Nov 8, `spikes/mem0_spike.py`; mem0ai 2.2.1, fastembed 0.9.1, qdrant-client 1.20.0, Windows, py3.12):**
- **Construction without a key fails.** `Memory()` and any config with an `openai` LLM block but no `api_key` raise `OpenAIError: Missing credentials`.
  - The adapter passes a placeholder LLM `api_key`. With `infer=False` the LLM is never called.
  - Zero non-loopback connections were measured after model warm-up.
- **Local config works:** fastembed `BAAI/bge-small-en-v1.5` (384d) plus local qdrant (`path`, `on_disk=True`) and an explicit `history_db_path`.
  - `MEM0_DIR` is set to keep `~/.mem0` untouched.
  - fastembed downloads models once (the embedder and qdrant's lazy `Qdrant/bm25` encoder). Do this warm-up before any network-free test.
- **IDs:** `add()` returns a Mem0-generated uuid4, so `adapter_record_id` is confirmed and `idempotent_put = False`.
- **Metadata round-trip:** `retract_token` and `record_id` come back under `get()["metadata"]`.
- **`find_by_token` works two ways:**
  - `get_all(filters={"user_id": …, "retract_token": tok})`, scoped;
  - `vector_store.list(filters={"retract_token": tok})`, unscoped and raw.
- **`list_ids`:** `vector_store.list(top_k=N)` (qdrant scroll), so `supports_list_ids = True`.
- **Delete:** after delete, `get()` and `vector_store.get()` both return `None`, so `contains` can be built on `vector_store.get`.
- **History residue confirmed:** after delete, the `history` table keeps the text in both the `ADD` row (`new_memory`) and the `DELETE` row (`old_memory`).
- **History purge works:**
  - `DELETE FROM history WHERE memory_id = ?` from a second sqlite3 connection with `PRAGMA secure_delete = ON`, while Mem0 is live.
  - Afterwards `Memory.history(id) == []` and a raw byte scan of `history.db` finds no residue.
  - Decision: **the adapter purges history by direct SQL**, so Mem0 history is not an L4 item.
- **Messages table and entity store:** `infer=False` writes no `messages` rows and never creates the entity store.
  - Residue there applies only to `infer=True`, which Retract does not use.
- **L4 residue in qdrant local:** marker bytes remain in `qdrant/collection/<name>/storage.sqlite` (freed SQLite pages) after delete.
  - This is vector-store internals and is listed under L4 as not established.
- **Windows:** `Memory.close()` does not close the local qdrant client, and its portalocker lock blocks reopening the same path. The adapter must call `vector_store.client.close()` too.
- The spaCy lemma model is optional. Without `mem0ai[nlp]` it logs warnings and BM25 lemmatization degrades. No action needed.

**Adapter interface decided:** `MemoryAdapter` stays as specified. The Mem0 adapter sets `idempotent_put=False` and `supports_list_ids=True`, and its `delete` includes the history purge. Pin `mem0ai==2.2.1`.

## Execution (operation journal)
**Tables:**
- `operations(op_id, source_ref, mode, plan_closure_hash, state, outcome, owner_token, lease_expires_at, error)`
- `op_actions(op_id, record_id, action, state ∈ pending|running|done|failed, new_record_id, retract_token, adapter_record_id, detail)`

**Phases:**
1. **PLAN** (dry run): compute the closure and the per-record actions. No writes.
2. **BLOCK:** one short transaction. It never stays open during LLM or adapter calls.
   ```
   BEGIN IMMEDIATE
     acquire durable operation ownership (no other op 'executing' with a live lease)
     recompute affected closure
     compare with previewed closure
     if stale: ROLLBACK, raise StalePlanError   -- source NOT tombstoned, nothing held
     insert source tombstone
     set hold_op_id on the entire closure
     insert the operation row and all pending op_actions
   COMMIT
   ```
3. **REPAIR:** each action in topological order via the write path, with tokens journaled first and a heartbeat renewing the lease.
   - A failed parent repair means its children are not rebuilt from it; they stay held.
   - Replacements stay held until their post-repair check passes.
4. **PURGE:** adapter `delete` → assert `contains` is false (and Mem0 history purge) → NULL the lineage content → set the final status.
   - Runs after all repairs, because the forbidden-fact checks need the old content.
5. **VERIFY:** structural and adapter checks, then retrieval probes (see Verification).
6. **FINALIZE:** in **one transaction**, insert the receipt (if any) and set the final op state and outcome.

**Terminal outcomes:**
- `completed`: all required repairs and deletions verified, with no quarantined remainder. Issues a **retraction receipt**.
- `contained`: all affected records blocked, but some content remains quarantined (includes quarantine mode and auto-mode fallbacks). Issues a **containment receipt**. "Erasure" is never used in names or text for this outcome.
- `failed`: required actions or checks are incomplete. Records stay held, a failure event is logged, and no success receipt is issued.

**`rt.resume(op_id)`** continues from the journal and reconciles writes via `find_by_token`. Executing a finalized op again returns its stored receipt.

## Verification
Each dimension reports `pass | fail | not_run`. The receipt and dashboard show them separately.
1. **Structural (exact):**
   - no readable record carries the source label
   - no readable record has an edge to a non-readable input
   - `contains` is false for every purged id
   - orphan scan (when `supports_list_ids`)
   - pending-row reconciliation
   - Mem0 history-DB scan
2. **Retrieval residue:** probe queries run through guarded `rt.search` *and* each adapter's raw `search`. Hits on quarantined records and hits on purged records are reported separately.
   - Probes are **generated locally**: sentence split + key tokens, with no LLM call and no network.
   - They are held in memory only and never persisted, because they are the retracted content.
   - Probes run after purge during uninterrupted execution.
   - **On resume:** all structural and adapter checks are rerun. The probe inputs are gone after purge, so `retrieval_residue = not_run` (never pass).
3. **Answer contamination:** testbed and experiments only, judged by the oracle.

**Threshold:** tuned on dev seeds 0–9, frozen in `experiments/configs.py`, and reported on test seeds 10–39 with FP/FN.

**Guarded search:** it over-fetches (`k×4`) so that filtering out held records does not silently shrink top-k. `shortcut: k×4 can still return <k under heavy holds, upgrade to adapter-side id filter.` The utility impact is noted in RESULTS.

**Freshness:** the guard opens no long-lived SQLite read transaction (tested).

## Receipts
- **Body:** op_id, outcome, receipt type (retraction | containment), source_ref, mode, plan summary, actions (ids, keyed hashes, action, state), verification dimensions with status and n/N, timestamp, seq, prev_hash.
- **Signature:** HMAC-SHA256 with `RETRACT_SIGNING_KEY` over canonical JSON.
- **`receipt.verify()`** returns `VerificationReport{outcome, integrity, chain_link, execution_complete, structural, retrieval_residue}`.
- **`rt.verify_chain()`** walks every receipt, recomputing hashes and signatures and checking seq continuity.
- **Documented limit:** anyone with DB write access *and* the signing key can rewrite history.

## Dependencies
- **Core:** `pydantic` (API boundary models).
- **Extras:**
  - `llm`: `httpx` (timeouts, retries, `Retry-After`).
  - `embed`: `fastembed`.
  - `mem0`: `mem0ai==2.2.1` (pinned by the spike).
  - `testbed`: `langgraph`.
  - `dashboard`: `fastapi`, `jinja2`, `uvicorn`, `python-multipart`.
- **Dev:** `pytest`, `pytest-cov`, `ruff`, `mypy`, `httpx`.
- **Rejected:** dotenv, click, scipy, hypothesis, Alembic, NetworkX, Postgres, Docker.
- **SQLite:** WAL, `foreign_keys=ON`, `PRAGMA user_version` migrations.

## Tooling (Windows + CI)
- Every Makefile recipe is a single `uv run python -m ...` command with no shell-specific syntax.
- **Targets:**
  - `test`
  - `demo`
  - `experiments` (FakeLLM only, no secrets)
  - `experiments-groq` (requires `GROQ_API_KEY`)
  - `experiments-groq-replay` (replays from the committed `experiments/cache/` with no key)
- **Cache metadata:** Groq model id, date and prompt version. Request headers and keys are never stored.
- **CI:** Ubuntu. M0 also includes a clean-clone check on Windows.

## File layout
```
retract/
  core/models.py, lineage.py, migrations/001_init.sql, sdk.py (add, derives, step, search, get, retract, resume, verify_chain)
  core/engine.py, operations.py (journal + durable lease lock), verification.py, errors.py, interfaces.py
  adapters/simple.py, adapters/mem0.py
  derive/fakes.py (FakeLLM incl. fault, leak- and paraphrase-injection; FakeEmbedder), groq.py, fastembed.py, prompts.py (frozen)
  receipts/receipt.py, probes/residue.py (local probe generation), audit.py, cli.py, dashboard/
testbed/  data.py (seeded personas, docs, facts, oracle), agents.py, scenarios.py, run.py
experiments/  run.py, configs.py, stats.py (Wilson, Clopper–Pearson, trial-level bootstrap), cache/, results/
spikes/mem0_spike.py
tests/  docs/ (retract-project-brief.md, PLAN.md, RESULTS.md)
```

## Schedule and hard checkpoints

### Week 1 · Nov 9–15
- **Mem0 spike (Nov 9–10):** run all the "pending verification" checks above. The outcome finalizes `MemoryAdapter` and decides history purge vs an L4 note. Findings are recorded in PLAN.md.
- **M0:**
  - uv, git, skeleton, pyproject (ruff, mypy strict on `retract/core`, markers)
  - CI (Ubuntu, py3.11 + 3.12) and a Windows clean-clone check
  - LICENSE, README stub (incl. uv PATH step), `.env.example`, `.gitignore`
- **M1:** LineageStore, migrations, write path, `add`, `@rt.derives(op, agent, tier, version, replayable, inputs=)`, `rt.step`.
- **Priya slice:** oracle facts. Naive delete leaves her facts in the summary. The closure query finds every affected descendant.
- **Related work:** read the full methods of [4] and [25] and confirm the claim statement (optional `docs/RELATED_WORK.md`).
- ✅ Acceptance:
  - 3-level inheritance
  - 3-user union
  - diamond descendants
  - random-DAG closure equals reachability
  - step read-set gives Priya's label with no declared inputs
  - crash between lineage insert and adapter write leaves an unreadable pending record that the orphan scan reports
  - slice shows naive residue > 0
- **Checkpoints:** Nov 10: spike done, adapter interface decided. Nov 15: M1 + slice passing.

### Week 2 · Nov 16–22
- **M2:** SimpleVectorStore (token reconciliation, `list_ids`), guard, tombstones, contract suite.
  - ✅ 50 random queries never return a pending, held or retracted record
  - ✅ a tombstoned write is rejected
  - ✅ the guard sees fresh state right after revocation
  - ✅ a write racing with BLOCK is either rejected or included in the closure, never missed
- **M3:** engine, BLOCK transaction, durable lease lock, journal, outcomes, receipts, `verify_chain`.
  - ✅ Priya end to end with outcome `completed`: no readable record carries her label, the summary is rebuilt from the others, and oracle honest facts are retained.
  - ✅ Failure tests:
    - LLM fails mid-repair → `failed`, records held, no success receipt
    - crash after 5 actions → `resume` reconciles via `find_by_token`, no duplicates
    - expired lease → resumable; live lease → `OperationInProgressError(op_id)`
    - stale preview → `StalePlanError` with nothing tombstoned or held
    - failed parent → child not rebuilt from it
    - adapter delete fails → `failed`
    - already retracted → no-op
    - two retractions sharing descendants → no resurrection
    - tampered receipt → `verify()` / `verify_chain()` fail
    - leak-injected replacement → quarantined, outcome `contained` with a containment receipt
    - independent support survives
    - version mismatch → quarantined
    - quarantine mode → `contained`
- **M4:** local probes, raw-adapter probes, `not_run` handling.
  - ✅ naive delete gives residue > 0
  - ✅ Retract: structural pass and guarded retrieval residue 0 on the slice
  - ✅ planted paraphrase miss measured as FN
  - ✅ `crash_after_purge_before_probes`: blocking persists, no duplicate writes, `retrieval_residue = not_run`
- **Groq budget:** compute calls per trial × configs × seeds × scenarios against current Groq rate limits. If over budget, reduce the Groq seeds and state n. Done Nov 12 (M8c decisions): about 414 requests per seed.
- **Checkpoint Nov 22:** M3 done. If missed, cut M6 (audit) and the Groq runs first.

### Week 3 · Nov 23–29
- **M5:** LangGraph testbed.
  - Agents: support, research, planning, using `rt.step`.
  - Stores: research private memory on Mem0; everything else on SimpleVectorStore (distinct adapters).
  - Seeded data: 20 personas and 30 docs.
  - Logs: message and memory channels in separate JSONL files.
  - `poisoned_doc` (benign false refund policy) measures four stages: entered memory, retrieved or promoted by other agents, influenced answers, influence gone after retraction. No prompt-injection or tool-hijack claims.
  - Mem0 contract test (integration), including the history residue result.
  - ✅ `uv run python -m testbed.run --scenario priya|poisoned_doc` with FakeLLM, and with Groq when a key is set.
- **M6:** `retract audit <path>`, which flags likely bypasses.
  - Looks at names bound to known memory clients with `.add/.upsert/.put/.add_texts/.add_documents`.
  - Allowlist in `[tool.retract.audit]`; exit code 1.
  - `shortcut: intra-module dataflow only, upgrade if real code passes clients across modules.`
  - ✅ fixture tests.
- Groq runs, cached.
- **Checkpoint Nov 29:** feature freeze. Week 4 = minimal dashboard + experiments + docs.

### Week 4 · Nov 30–Dec 6
- **M7:** minimal dashboard on `127.0.0.1`.
  - Destructive actions use POST + a CSRF token.
  - Views:
    - records table
    - record view with lineage graph + "why does the agent know this?"
    - retract flow: preview → confirm (stale → re-preview) → receipt view showing outcome, receipt type and the three dimensions separately
    - failed → resumed op view
  - `make demo` needs no manual DB edits.
  - Fallback if cut: CLI `retract demo`.
  - ✅ TestClient smoke tests, including a CSRF rejection.
- **M8:** experiments and docs.
  - **Configs:** clean, unretracted, naive_delete, strict delete, auto, quarantine. Same 30 paired seeds, data, tasks and queries for all.
  - **Metrics:**
    - structural residue
    - retrieval residue (guarded / raw, purged vs quarantined)
    - answer contamination
    - false-fact influence per stage
    - record preservation
    - honest-fact retention
    - over-retraction rate
    - utility (incl. over-fetch impact)
    - runtime-check FN/FP under leak/paraphrase injection
  - **Cost, split into:** capture overhead, op, re-derive, probes, guarded-read latency.
  - **Stats:**
    - Unit = trial. Probes within a trial are not independent.
    - Wilson 95% two-sided for binary trial outcomes, with n/N.
    - Trial-level bootstrap CIs for continuous per-trial rates and paired strategy differences.
    - For 0/n: report the Wilson upper bound (0/30 ≈ 11.4%) and optionally the exact one-sided Clopper–Pearson bound (≈ 9.5%), always labeled with the method. Never an unlabeled bound.
  - **`docs/RESULTS.md`** is structured as a paper skeleton (abstract, related work, method, evaluation, limitations). It includes negative results and re-derivation meaning drift.
  - **README:** guarantee table, quickstart, Mermaid diagram, API, limits, roadmap. Startup, market and adoption statements are presented as hypotheses.
  - ✅ `make test`, `make demo` and `make experiments` from a clean clone; `make experiments-groq-replay` without a key.
- **Checkpoint Dec 1:** experiment results exist.

### Cut order (triggered by any missed checkpoint)
1. Dashboard scope: keep the records table, retract flow and receipt view; fallback is the CLI `retract demo`.
2. Audit (M6).
3. Groq runs.

Never cut: experiments, receipts, recovery, probes.

## Release gate (definition of done)
- **Engineering:**
  - lineage/closure and step read-set tests
  - pending-write race test
  - revocation survives crash + resume, with adapter reconciliation via `find_by_token`
  - `crash_after_purge_before_probes` gives `not_run`
  - purged records absent from adapter APIs (L2)
  - Mem0 history residue result recorded (purged, or listed under L4)
  - `completed` vs `contained` outcomes and their receipt types
  - independent support preserved
  - receipt and chain tamper detection
- **Research:**
  - clean / unretracted / recovery baselines
  - both scenarios on FakeLLM
  - Groq with frozen prompts, committed cache and paired seeds (n stated)
  - three residue dimensions reported separately
  - over-retraction rate
  - negative results, with labeled intervals
- **Delivery:**
  - clean-clone `make test` on Ubuntu CI and Windows
  - Priya demo (dashboard, or CLI fallback) including one failed → resumed op
  - README guarantees section
  - demo recordable without manual DB edits

## Validation scope (solo, no external input)
- **Validation means** tests, failure-injection tests and the controlled M8 experiments. There is no customer, user or peer validation.
- **One self-directed task in Week 1:** read the full methods of [4] Agentic Unlearning and [25] Dependency-Guided Rollback Repair and confirm the contribution statement. Optionally draft `docs/RELATED_WORK.md`.
- **Explicitly UNTESTED hypothesis:** "Teams will route all agent-memory reads and writes through an SDK."
- **Claims rule:** README and RESULTS present startup, market and adoption claims as hypotheses and claim only what tests and experiments measured.

## Deltas vs brief (the brief itself is not edited)
- Public modes are `auto|delete|quarantine`; the brief says `mode="rederive"`.
- RQ4 (monitors, voting) is out of MVP scope and moved to future work.
- `poisoned_doc` is a benign false-fact scenario we author. There are no prompt-injection or tool-hijack claims; the brief's "reuse published attack setups" becomes a follow-up.
- Customer discovery, discovery calls, design partners, partner letters and pilots are out of scope for this build. So are the brief's kill/pivot criteria and its 30/90/180-day validation milestones. The adoption assumption stays untested (see Validation scope).
- The paper is not in the 4-week scope. RESULTS.md is structured as a paper skeleton, so an arXiv draft is a reformat after Dec 6.
- The audit uses fresh stdlib `ast`, not CodeLens. Pitch wording: "flags likely bypasses".
- No LangGraph store adapter: the brief's feature 1 is narrowed to orchestration only.
- Guarantee levels L2/L3 as in the Guarantee levels section.
- `receipt.export` supports JSON only; the brief shows `export("dpdp")`.
- One hosted model (Groq `openai/gpt-oss-120b`; Llama 3.3 was shut down on 08/16/26); the brief's second model for generalization is future work.

## Implementation decisions (M1–M3)

**M3 implementation decisions (final, approved by the user):**
1. **Errors during repair.**
   - Temporary failures cause a resumable failed operation.
   - Known replay failures quarantine the affected record.
   - Unexpected exceptions fail the operation rather than silently becoming containment.
   - In code:
     - `TransientError` → op `failed`, holds kept, action detail `transient: <type>`, resumable.
     - `ReplayError` (and engine-detected cases: not registered, version mismatch, no valid inputs) → quarantine and continue.
     - Any other exception → op `failed`, holds kept, the error recorded in `operations.error` and the action detail `unexpected: <type>`.
2. **Failed post-repair check.** When a replacement fails post-repair verification, it is quarantined and the old version is purged. Successful containment is reported separately from completed retraction (`contained` with a containment receipt vs `completed` with a retraction receipt). Children are not rebuilt from an unverified replacement.
3. **Structural verification.** It scans every registered store for orphans. Source-specific residue and unrelated global lineage-integrity failures are reported separately. For the MVP, either failure may prevent a completed outcome.
   - Source-specific checks: all actions done, no readable record carries the source label, purged ids absent from adapters, no orphans from this op.
   - Global integrity checks: no readable record has an unreadable input, and no unrelated orphans.
   - A failure raises `StructuralVerificationError(source_failures, global_failures)`, and both lists are stored in `operations.error`. On success, the receipt reports `structural.source` and `structural.global` separately.

**M4 decisions (the user may overrule any):**
- **Retracted material** = content of the op's records that name the source in `explicit_sources` (owned primaries and explicit-source derived records). Both the post-repair check and the probes use it.
- **Probes:** each sentence of the material, plus a key-token query (up to 5 words, 4+ letters, minus stopwords). Each probe runs through guarded `rt.search` (k=5) and raw `store.search` (k=5) on every tier. Counts are probes with at least one hit, per group.
- **What counts as a hit:** an item containing a retracted sentence that is not backed by an *independent primary*, meaning a readable record with no parents that does not carry the source label. This is how independent support survives.
- **Hit groups and their effect:**
  - `raw_quarantined` is expected and reported separately.
  - `guarded`, `raw_purged` and `raw_unexpected` (active or untracked items) fail retrieval residue. That failure joins the source-specific structural failures (`no_retrieval_residue`), so the op ends `failed`.
- **When probes run:** probes are built before PURGE and kept in memory only. If a resume finds the material already purged, `retrieval_residue = not_run`.
- **Probe reads are not lineage.** Probes use `Retract.guarded_search`, which never joins a step's read-set, so running `retract` inside an `rt.step` adds no parents to that step's writes.
- **Measurement:** `testbed.data.confusion(runtime_flagged, oracle_leaked)` labels each runtime decision TP/FP/FN/TN for M8. The planted paraphrase is a measured FN.

**M5 decisions (the user may overrule any):**
- **Tiers, one adapter each:** `private:support`, `private:research` (Mem0), `private:planning`, `shared` and `kb`. Retraction always crosses distinct adapters. `--store simple` swaps Mem0 for a SimpleVectorStore. CI and the default test run use that swap; the Mem0 variants are integration tests.
- **Graph:** support → research → planning (LangGraph), each node inside `rt.step`.
  - **support:** reads the persona's facts (k=2) and KB docs (k=2), and writes an `account_brief` to `shared`.
  - **research:** reads the persona's brief (k=1) and a KB doc (k=1), and writes a `research_note` to Mem0.
  - **planning:** reads `shared` (k=2) and writes an `action_plan`. It then answers from guarded reads of `shared`, `planning` and `kb`, without writing.
  - All writes go through `@rt.derives`, so they are replayable.
- **Data:** 3 hand-written + 17 seeded personas (2 facts each) and 30 docs: 28 KB docs, the true refund policy (`RP-30`, d29) and the poisoned one (`RP-90`, d30). Every fact has a unique code-word marker. Seed 2026.
- **Questions:**
  - priya: "What do we know about {name}?" The first wording ("How should we follow up…") lexically matched only Priya's "follow-ups" fact and inflated contamination to 20/20.
  - poisoned_doc: "{name} bought from us 60 days ago. Can {name} still get a refund?" The policy decides the answer (RP-30: no, RP-90: yes). The earlier "What is the refund window for {name}'s purchase?" let Groq abstain ("No relevant information" in 17/20) even with the poisoned doc in context, so stage 3 read 0.
  - Oracle: the refund facts also match their window ("90 days"/"90-day", "30 days"/"30-day"), because answers may state the window without the policy code.
- **Logs:** `memory.jsonl` holds ids, tiers and labels only, so it carries no residue. `messages.jsonl` holds content; it is outside Retract's scope, and its hits are reported as `message_log_lines_with_target`.
- **Keys:** `testbed.run` generates ephemeral keys when they are unset. The data is synthetic, and receipts from such a run verify only inside that process.
- **Groq:** `retract/derive/groq.py` (httpx; Retry-After and backoff; `TransientError` after retries) with frozen prompts `v1`. First live runs (user's key, Nov 10): `llama-3.3-70b-versatile` returns 404, because Groq shut it down on 08/16/26, so both runs used `--model openai/gpt-oss-120b`. On Nov 12 the user made `openai/gpt-oss-120b` the default (`retract.derive.groq.MODEL`).
  - priya: `completed`, residue 0, contaminated answers 1/20 → 0, 213 calls (rate-limit retries included).
  - poisoned_doc (old question): `completed`, residue 0, 58 records with the false policy → 0, 327 calls. Influenced answers 0/20 before retraction even though the poisoned doc was retrieved in 38 of 40 pre-retraction answer calls: the question let the model abstain. The question was redesigned (above); not yet re-run on Groq.
  - Groq answers differ between runs (2/20 influenced answers during the tickets vs 0/20 in the re-asked "before" measurement), so experiment results need the response cache (below, Nov 12).
- **First FakeLLM results (both research stores):**
  - priya: 6 affected records over 3 agents and 4 tiers, outcome `completed`. Contaminated answers go from 1/20 to 0, and residue is 0.
  - poisoned_doc: entered memory once, promoted to support/shared 19, research 19 and planning 20. It influenced 20/20 answers, and the influence is gone after retraction (59 records, `completed`).
  - Runtime: about 12 s with the simple store, and 50–80 s with Mem0.

**Groq response cache (Nov 12; the user may overrule any):**
- `retract/derive/cache.py`: an append-only JSONL file, `experiments/cache/groq.jsonl` by default, committed. One line per distinct request holds `key`, `model`, `prompt_version`, `date` and `response`. The key is the SHA-256 of the canonical request body (model, temperature, messages) plus the prompt version. Keys and headers are never stored.
- **Read-through, also when live.** A request already in the cache is never re-sent, so identical requests get identical answers within and across runs. A replay then reproduces the live run exactly. The cost: Groq's run-to-run variance for one request is not sampled; M8 states this.
- **Replay:** `--llm groq --replay` serves from the cache only. It needs no key, makes no HTTP call, and a miss raises `RetractError` instead of going to the network.
- **Torn writes:** a crash mid-append can tear only the last line. Loading skips it, and the next append truncates it. A corrupt line anywhere else raises `RetractError`.
- **Test:** priya runs "live" against a FakeLLM-backed mock of Groq, then replays in a fresh process with new ephemeral keys and no Groq key. The replay makes 0 HTTP calls, every request hits the cache, and the summary is identical. So nothing random per run reaches the prompts.

**M8a decisions: FakeLLM experiment harness (Nov 12; the user may overrule any):**
- **Scope.** M8a covers `experiments/` (`stats.py`, `configs.py`, `run.py`), `make experiments`, and committed results in `experiments/results/fake/`. Still open for M8b: leak and paraphrase injection (runtime-check FN/FP), the cost split, over-fetch impact and Groq configs. RESULTS.md and the README follow after that.
- **Seeds.** `testbed.data.dataset(seed)` regenerates the code words and templates of the 17 seeded personas and 28 KB docs. Fact ids, the 3 hand-written users and both refund docs stay fixed, and `dataset(2026)` equals the M5 data. A trial seed also shuffles the ticket order. Reported runs use the test seeds 10–39 (M8b; M8a first used 1–30, ERRORS 46), and every config of a seed is paired on the same data, order and questions. The Oracle class gives each seed's fact oracle.
- **Configs.** Each (scenario, seed) gets 5 runs:
  - `clean`: the target source is never ingested.
  - `naive_delete`: the source's primary items are deleted from their stores directly.
  - `delete`, `auto` and `quarantine`: `rt.retract` with that mode.
  - `unretracted` is the auto run's pre-retraction state. `check_pairing` fails the run unless all four retraction runs of a seed saw an identical pre-retraction state, which also proves determinism across processes.
- **Metrics (per trial, from the oracle):**
  - **Structural residue:** physical items, not quarantined, carrying a target fact.
  - **Readable residue:** lineage-readable records carrying one.
  - **Retrieval residue:** each target fact's text and both paraphrases are run as probes over every tier with k=5. A probe counts as a hit per group: guarded search, raw unquarantined or raw quarantined.
  - **Answer contamination:** the share of the 20 answers that contain a target fact.
  - **Utility:** the share of scored answers containing an expected fact. For priya, that is the persona's own facts, with Priya's own question excluded; for poisoned_doc, it is the true policy RP-30.
  - **Record preservation:** readable records after / before, where "readable" also requires the item to be in its store, because a naive delete skips lineage.
  - **Honest-fact retention:** honest fact mentions in readable derived records, after / before.
  - **Closure over-approximation:** closure records whose content holds no target fact.
  - **Over-retraction:** those of them that are not readable afterwards, directly or through a replacement.
  - poisoned_doc also reports its four stages from the auto runs.
- **Stats.**
  - Binary outcomes use Wilson 95% intervals, with the exact one-sided Clopper–Pearson bound added at 0/n.
  - Continuous rates and paired differences (by seed) use trial-level percentile bootstrap intervals with B=10,000 and a fixed seed.
  - Paired differences: auto, delete and quarantine against clean, and auto against delete.
- **Timing is not reported in M8a.** Wall time under a 12-process pool is not a cost measurement (ERRORS 41), so the cost split comes in M8b and runs serially.
- **Output.** `trials.jsonl` and `summary.md` are byte-identical across reruns, and `summary.json` is too now that timing is gone. A full run takes about 3 minutes on 12 cores.
- **FakeLLM results (test seeds 10–39):**
  - **delete, auto and quarantine:** structural and guarded retrieval residue 0/30 in both scenarios (Wilson upper bound 0.114, one-sided Clopper–Pearson 0.095).
  - **naive_delete:** residue in 30/30 trials. On poisoned_doc it also leaves the answers contaminated (0.993).
  - **auto:** keeps record counts. delete and quarantine lose 41.9% of records on poisoned_doc (preservation 0.581) and keep only 11.5% of honest-fact mentions.
  - **Negative result: auto is not the clean world.** On poisoned_doc, auto has the same number of readable records as clean but 54.5 fewer honest mentions (paired, bootstrap 95% [−55.8, −53.1]).
  - **poisoned_doc stages (auto):** the false policy entered memory once, was promoted into 53.5 records on average, influenced 19.87 of 20 answers, and was gone after retraction in 30/30 trials. Re-derivation replays the old inputs minus the retracted one; it does not re-run the retrieval that would have picked another document.
  - **Closure over-approximation is 0** in the testbed, because every step's read-set equals its declared inputs. Over-approximation shows up only in the M1 slice (the reminder).
  - **Utility** does not separate the configs on FakeLLM (identical everywhere), because FakeLLM's answers are extractive.

**M8b decisions: injection, threshold, cost and over-fetch (Nov 12; the user may overrule any):**
- **Injection configs:**
  - `auto_leak` re-emits one target fact verbatim; `auto_paraphrase` re-emits one of its two paraphrases. FakeLLM adds the text to every summary written during repair only; the fact and paraphrase are chosen per seed.
  - The pre-retraction state is unchanged, so both configs stay paired with the others (`check_pairing`).
  - Every replacement the op writes is labeled TP, FP, FN or TN: runtime flagged (quarantined) vs oracle leaked.
  - Rates are per trial and the intervals resample trials, because the replacements within a trial are not independent.
- **Threshold.** `Retract(similarity_threshold=...)` now reaches the post-repair check and the residue probes; it defaults to `verification.SIMILARITY_THRESHOLD`.
  - **Sweep:** `--tune` ran 0.40–0.95 in steps of 0.05 over auto, auto_leak and auto_paraphrase on the dev seeds 0–9, and wrote `experiments/results/tuning.json`.
  - **Rule (fixed before the sweep):** lowest mean per-trial (FN + FP) / replacements, with ties going to the lower threshold.
  - **Result:** 0.75–0.95 tie, so the rule picks 0.75. It is frozen as `configs.THRESHOLD` and the SDK default, and a test checks all three agree.
  - **The sweep is not monotone.** Below 0.75 even verbatim leaks pass (TP 26–49 instead of 393), because a low threshold lets the "supported by an input" exemption match a look-alike honest sentence (RP-30 for RP-90).
- **Cost split.** `experiments/cost.py` runs serially after the pool has exited, on test seeds 10–14 with auto. It reports medians and quartiles in `cost.json` and `cost.md`. Timings vary run to run, so they are kept apart from the reproducible summary.
- **Over-fetch.** A short guarded read returns fewer than k results although the tier holds at least k readable records. It is measured on the answer reads of every state.
- **FakeLLM results (test seeds 10–39, threshold 0.75):**
  - **Leaks are caught.** Verbatim leaks are TP on every replacement (1098 on poisoned_doc, 91 on priya), with 0 FN and 0 FP, and the outcome is `contained` in 30/30. Without injection, every replacement is TN.
  - **Negative result: paraphrases are never caught.** FN on every replacement (1605 and 92), at every threshold of the sweep. The op still ends `completed`, and the receipt reports retrieval residue `pass`, because the probes use the same heuristic. Meanwhile the oracle finds structural residue in 30/30 trials and 100% contaminated answers on poisoned_doc. So FakeEmbedder's hashed bag-of-words cannot support a claim about paraphrased leaks, and a semantic embedder is future work.
  - **Over-fetch shortfall:** quarantine leaves 43.6% of guarded reads short on poisoned_doc ([37.1%, 49.8%]), because 54 quarantined records crowd the tiers past the k×4 over-fetch. auto, delete and clean have none, and utility is unchanged on FakeLLM.
  - **Cost (medians):** a Retract write takes 7.4–7.9 ms vs 0.13–0.15 ms for a raw adapter write, about 55×, mostly durable SQLite commits on the crash-safe write path. A guarded read is about 1.9× a raw read (1.2 ms vs 0.63 ms). The auto op takes 0.31 s on priya (closure 5) and 1.94 s on poisoned_doc (closure 54), where journal, BLOCK, purge and structural checks dominate (1.91 s) and probes take 0.06 s. Re-derivation is about 0 s on FakeLLM; a hosted LLM adds its latency to each of the 4 or 54 calls.

**M8c decisions: Groq experiment harness (Nov 12; the user may overrule any):**
- **Command.** `python -m experiments.run --llm groq [--seeds N] [--replay]`, also available as `make experiments-groq` and `make experiments-groq-replay`. Results go to `experiments/results/groq/`, and responses to `experiments/cache/groq.jsonl`.
- **Configs.** clean, naive_delete, delete, auto and quarantine (unretracted comes from auto's before state). The injection configs stay FakeLLM-only.
- **Serial.** Groq trials run serially in one process with one shared `ResponseCache`. Parallel processes would interleave appends to the JSONL file, and rate limits leave nothing to gain from parallelism.
- **Shared worlds.** The cache is read-through, so the configs of a seed share one pre-retraction world. The first config pays the Groq calls and the rest are cache hits, so `check_pairing` holds by construction. Replay rows are identical to the live rows (tested offline with a FakeLLM-backed mock of Groq).
- **Resumable.** A crash or a rate limit loses nothing: rerunning the same command serves every finished request from the cache. A `Retry-After` longer than `max_wait` (120 s), such as a daily limit, raises `TransientError` instead of sleeping for hours, and the message includes Groq's own error.
- **Tokens.** Each cache entry stores Groq's `usage` (prompt, completion and total tokens), and the runner prints running request, cache-hit, HTTP and token counts.
- **Budget (the Week 2 check, done Nov 12):**
  - `--estimate` (a FakeLLM dry run on 2 test seeds) counts about 120 distinct requests per seed for priya and 294 for poisoned_doc, so about 414 per seed for both scenarios.
  - Groq's free plan for `openai/gpt-oss-120b` allows 30 requests/min, 1K requests/day, 8K tokens/min and 200K tokens/day (console.groq.com/docs/rate-limits, Nov 12). The account's own limits page is authoritative.
  - At an assumed 700–1,000 tokens per request (prompt plus gpt-oss reasoning), the daily token limit allows about 200–285 requests per day, which is about one seed every 1.5–2 days. The stored `usage` replaces this assumption after the first run.
  - `GROQ_SEED_COUNT = 5` (test seeds 10–14) is a placeholder until the user picks n from the measured token use. RESULTS states n.

**M6 decisions (the user may overrule any):**
- **Entry points:** `retract audit <path>...` (`[project.scripts]`, or `python -m retract.cli audit`). The code is in `retract/audit.py`, stdlib `ast` and `tomllib` only.
- **What is flagged:** a call to `.add/.upsert/.put/.add_texts/.add_documents` on a value bound to a known memory client. On Retract's own adapters, `.write` is flagged too, because it skips lineage and the guard.
  - Known clients: mem0 (`Memory`, `MemoryClient` and the async variants), LangGraph stores (`BaseStore`, `InMemoryStore`, Postgres, SQLite, `get_store()`), LangChain vector stores (`langchain_core.vectorstores` plus anything under `langchain_community.vectorstores`, `langchain_chroma`, `langchain_qdrant`, `langchain_pinecone` and `langchain_postgres`), `qdrant_client`, `chromadb`, `pinecone` and Retract's adapters.
  - Bindings: constructor calls, `from_*` classmethods, `x = y` aliases, `self.x = ...`, `with ... as x`, annotated parameters and variables (`store: BaseStore`, also `Optional[...]` and string annotations), and collection factories (`get_or_create_collection`, `create_collection`, `get_collection`, `Index`).
- **Limits:** intra-module dataflow only. Bindings are also scope-insensitive: a name bound to a client anywhere in the module counts everywhere. Relative imports are not resolved.
- **Allowlist:** `[tool.retract.audit] allow` in the nearest `pyproject.toml` holds fnmatch patterns on paths relative to that file. This repo allows `retract/adapters/*` (the SDK's own write path) and `spikes/*`, and it audits clean.
- **Walk:** directories starting with `.`, `__pycache__` and `node_modules` are skipped, so `.venv` is never scanned.
- **Exit codes:** 0 clean, 1 on any finding or a file that does not parse (a gate never passes it silently), 2 on a missing path or a bad allowlist.

**Other decisions (the user may overrule any):**
- **REPAIR stops at the first failure that fails the op.** Later actions, including children, stay pending and held.
- **Holds by another op.** BLOCK raises `OperationInProgressError(other_op)` if any affected record is held by a non-finalized op. FINALIZE clears the op's holds.
- **A tombstoned source is a no-op.** If the source is already tombstoned by a finalized op, `retract` returns that op's receipt. If that op is failed or executing, it raises `OperationInProgressError`.
- **Receipts carry no raw content or source ids:** record ids, keyed content hashes, actions, effects and verification dimensions only.
- **Schema:** all tables are in `001_init.sql` (no database has shipped yet). `user_version` migrations start from the first release.
- **Delivered early:** the minimal guarded `search` and `SimpleVectorStore` landed in M1, because the step read-set needs them. `MemoryAdapter.update` is left out until a caller needs it.

## Open items (need the user's decision; the default is applied until decided)
1. **Repair writes vs held inputs.** The write path rejects held inputs, but a repair's inputs are held by its own op.
   - Default: allow inputs held by the same `op_id` only, tested.
2. **Step-only writes are not replayable**, so auto mode quarantines them.
   - Default: testbed summaries and notes use `@rt.derives` inside `rt.step`, so they stay replayable.
3. **Nov 22 checkpoint vs general cut order.**
   - Default: a missed Nov 22 checkpoint cuts audit + Groq, because the dashboard isn't started yet. Every other checkpoint follows the general order.
4. **Mem0 is not on the never-cut list.**
   - Default if the spike fails badly: two separate SimpleVectorStore instances keep the cross-store case. The Mem0 limitation is reported in RESULTS.
5. **The committed `experiments/cache/`** holds Groq outputs on synthetic data only, plus metadata. No keys or headers.
   - Default: commit it.
