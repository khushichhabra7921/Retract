# Errors log

Every error, failure or wrong result hit while building the Mem0 spike and M0–M3, with its cause and fix. Newest milestone last.

## Mem0 spike (`spikes/mem0_spike.py`)

### 1. `uv: command not found`
- **When:** checking tooling before the spike.
- **Cause:** `pip install --user uv` puts `uv.exe` in `%APPDATA%\Python\Python312\Scripts`, which is not on PATH.
- **Fix:** commands prepend that folder to `PATH`. The README documents the permanent PATH step.

### 2. `make: command not found`
- **Cause:** make is not installed on this machine.
- **Fix:** none yet. Commands are run as `uv run python -m pytest` directly. The user still needs to run `choco install make`.

### 3. `OpenAIError: Missing credentials` when constructing `Memory()`
- **Cause:** mem0ai 2.2.1 always builds an OpenAI LLM (and the default embedder) client, even with `infer=False`.
- **Fix:** this is a finding, not a bug. The adapter passes a placeholder LLM `api_key` and a local fastembed embedder, and no network call happens. Recorded in PLAN.md.

### 4. `RuntimeError: Storage folder ...\qdrant is already accessed by another instance of Qdrant client`
- **When:** reopening `Memory.from_config` on the same path after `m.close()`.
- **Cause:** `Memory.close()` does not close the local Qdrant client. Its portalocker lock stays held, and on Windows that blocks a second client.
- **Fix:** run the history purge while Mem0 is still live, then call `m.vector_store.client.close()` explicitly. Recorded as an adapter requirement in PLAN.md.

### 5. Raw byte scan reported `qdrant\.lock (unreadable: [Errno 13] Permission denied)` as a residue hit
- **Cause:** the scan listed unreadable files as hits. `.lock` is locked while open and holds no data.
- **Fix:** skip `.lock`, and label any other unreadable file `(unreadable, not scanned)` instead of calling it a hit.

## M0 (tooling)

### 6. Ruff `E501` / `B904` in `spikes/mem0_spike.py`
- **Cause:** the spike was written before the lint config existed.
- **Fix:** `extend-exclude = ["spikes"]` in `pyproject.toml`. The spike is kept exactly as it was run, as evidence.

### 7. `ruff format --check` wanted to reformat `docs/retract-project-brief.md`
- **Cause:** ruff formats Python code blocks inside Markdown files.
- **Fix:** none, because the brief must never be edited. CI runs `ruff check` only, not `ruff format --check`.

### 8. Files checked out with CRLF (`LF will be replaced by CRLF` warnings)
- **Cause:** `core.autocrlf=true` on Windows. A CRLF Makefile risks make errors.
- **Fix:** `.gitattributes` with `* text=auto eol=lf`. A re-run of the clean clone showed the Makefile as LF.

## M1 (lineage, write path)

### 9. Priya slice failed: `Extra items in the left set` for the closure
- **Cause:** not a bug. The planning step had already read the summary, so the step read-set made the Arjun-only reminder a descendant of Priya. This is the plan's deliberate over-approximation.
- **Fix:** the test asserts `closure == truth | {reminder}` and checks that the reminder holds no Priya facts. The extra record is the over-retraction that M8 measures.

### 10. Ruff `E501` line too long (8 places)
- **Fix:** ran `ruff format` and split one long SQL string by hand.

## M2 (guard, tombstones, races)

### 11. Threaded race test passed but never raced
- **Symptom:** `RACE 0 of 40 writes landed before BLOCK` in 9 of 10 runs.
- **Cause:** BLOCK ran before the writer thread had started writing.
- **Fix:** the writer signals after 5 successful writes, and only then does the main thread run BLOCK. The test now asserts writes happen on both sides of BLOCK.

### 12. Ruff `B007` unused loop variable `i`
- **Fix:** renamed it to `_`.

### 13. Flaky failure (1 run in 3) under load
- **Cause:** the threaded race writer had a fixed 300 attempts. Under load it could finish them all before BLOCK ran, which left `rejected` empty and failed the "both sides" assertion.
- **Fix:** the writer loops until it has seen 3 rejections, capped at 100,000 attempts. 30 parallel stress runs then passed.

## M3 (engine, journal, receipts)

### 14. mypy: `Argument 2 to "purge" ... incompatible type "str"; expected "Literal[...]"`
- **Fix:** annotated the local variable as `status: Status`.

### 15. `RetractError: RETRACT_PSEUDONYM_KEY is not set` / `RETRACT_SIGNING_KEY is not set` in two crash tests
- **Cause:** `monkeypatch.undo()` also undid the autouse fixture's key environment variables, because they share one `monkeypatch` object.
- **Fix:** scoped the patches with `monkeypatch.context()`. Two older tests used the same `undo()` pattern; they passed only by luck and were fixed the same way.

### 16. Ruff `I001` import block unsorted (`sdk.py`, later `engine.py`)
- **Fix:** `ruff check --fix`.

### 17. A filler test with an always-true assertion (`... or True`)
- **Cause:** written by mistake at the end of `test_engine.py`.
- **Fix:** deleted before running the suite.

### 18. `PytestWarning: (rm_rf) error removing ... [WinError 145] The directory is not empty`
- **Cause:** a test artifact, not a bug. Several pytest processes ran in parallel during stress runs and raced to clean the shared temp folder.
- **Fix:** none needed. It does not occur in single runs.

### 19. Review finding: any non-transient exception during replay was quarantined
- **Cause:** a design gap. A software bug such as `AttributeError` was reported as acceptable containment.
- **Fix:** only `ReplayError` (or engine-detected cases) quarantines. `TransientError` and any unexpected exception fail the op with holds kept, record the error, and leave it resumable. Tests were added.

### 20. Review finding: a global orphan failed verification with no indication of why
- **Cause:** one combined structural verdict hid whether the source's own data was still reachable.
- **Fix:** the checks are split into source-specific and global-integrity groups. Failures raise `StructuralVerificationError(source_failures, global_failures)`, which is stored on the op. Tests were added.

### 21. mypy: `Need type annotation for "stray_ours"`
- **Fix:** annotated both lists as `list[str]`.
