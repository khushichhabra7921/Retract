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

## M4 (probes, retrieval residue)

### 22. `test_priya_end_to_end_completed`: `'pass' == 'not_run'`
- **Cause:** expected. Before M4, retrieval residue was always `not_run`. The probes now run and pass.
- **Fix:** the assertion now expects `pass`.

### 23. A true-positive test that could not fail
- **Cause:** it computed `oracle_leaked` as `... or flagged`, so the runtime result leaked into the oracle side.
- **Fix:** the oracle now runs over every replacement the engine wrote, quarantined ones included. The TP, FN and TN tests all use that same independent oracle.

### 24. Working tree rolled back to pre-M4 after the M4 commit
- **Symptom:** after commit 32d6076, six files (engine, lineage, PLAN, ERRORS, testbed data, a test) held their pre-M4 contents again, uncommitted, and `retract/probes/residue.py` was deleted. New files (`tests/test_residue.py`, `retract/probes/__init__.py`) were untouched.
- **Cause:** unknown. The pattern (only files with older versions reverted) fits OneDrive restoring older copies, which PLAN.md lists as a known risk of keeping the repo in OneDrive. A manual undo is also possible.
- **Fix:** a clean clone of the commit passed (ruff, mypy, 62 tests). With the user's approval, `git restore .` discarded the rollback. If it happens again, pause OneDrive sync for this folder.

### 25. Probe searches joined the caller's `rt.step` read-set
- **Cause:** residue probes used public `rt.search`, which records reads into an active step. A retraction run inside a step made every probe result a parent of the step's later writes, creating false lineage.
- **Fix:** `Retract.guarded_search` does the guarded search without recording reads, and `rt.search` wraps it. Probes use `guarded_search`. The regression test fails on the old code.

### 26. Probe measurement re-checked the same content for every probe
- **Cause:** `measure` ran the forbidden-fact heuristic, embeddings included, on each returned item for each probe and tier. Cheap with FakeEmbedder, but slow with fastembed in M8.
- **Fix:** the leak check is memoized per content inside one `measure` call, so the cache lives only as long as that call. A test asserts one check per distinct content.

## M5 (Mem0 adapter, LangGraph testbed)

### 27. Patch to `testbed/data.py` failed: `AssertionError` on the `facts_in` block
- **Cause:** found later (entry 31): double backslashes in a shell-embedded Python patch reach Python as single ones, so `\b` became a backspace character and the search string never matched.
- **Fix:** wrote the patch and the new code block as separate script files and spliced by index. A self-check over all 70 facts then passed.

### 28. Priya scenario showed Priya's facts in 20 of 20 answers
- **Cause:** a measurement artifact, not a bug. Every question said "follow up", and only Priya's fact contains "follow-ups", so her brief ranked high for every persona and the extractive answer kept her sentence.
- **Fix:** the priya question is now "What do we know about {name}?", and FakeLLM's answer stopwords include "about" and "know". The result: 1/20 before, 0 after.

### 29. Ruff `E501` (run.py docstring) and `F401` (unused `FACTS` import)
- **Fix:** shortened the docstring line and ran `ruff check --fix`.

### 30. `retract.core.errors.RetractError: groq HTTP 404` on the first live Groq run
- **Cause:** the model id. Groq shut down `llama-3.3-70b-versatile` on 08/16/26 (replacements `openai/gpt-oss-120b` or `qwen/qwen3.6-27b`). Confirmed by the user's `GET /openai/v1/models`: the model is absent from the list, and both scenarios then ran with `--model openai/gpt-oss-120b`. The client also hid Groq's own error message.
- **Fix:** errors now include Groq's code and message (for example `404: model_not_found: ...`), and `testbed.run --model <id>` selects the model. The default is unchanged until the user picks a replacement, because PLAN.md names Llama 3.3.

### 31. Shell-embedded Python patches cannot carry backslashes
- **Symptom:** a patch asserting `s.count(a) == 1` failed. The search text showed `\x08` (a backspace) where the source had the regex `\b`.
- **Cause:** double backslashes typed in the command arrive as single ones, so Python reads `\b` as a backspace escape. This was also the cause of entry 27, and it hit again while writing this entry.
- **Fix:** edits that contain backslashes go through the editor tool, not a heredoc. The failed patch had written nothing, because it asserts every pair before writing.

### 32. poisoned_doc on Groq: 0 influenced answers before retraction
- **Cause:** a measurement design issue, not a Retract bug. "What is the refund window for {name}'s purchase?" let `gpt-oss-120b` reply "No relevant information" (17/20), because the policy is not about that customer. The poisoned doc was in context in 38 of 40 pre-retraction answer calls. FakeLLM's word-overlap answers had hidden this (20/20).
- **Fix:** the question is now "{name} bought from us 60 days ago. Can {name} still get a refund?", so the policy decides the answer. The oracle also counts the stated window ("90 days"/"90-day") as the false fact. FakeLLM still shows 20/20 → 0. The Groq re-run is pending.

## M6 (`retract audit`)

### 33. Audit test expected `QdrantClient(":memory:").upsert`, got `QdrantClient(':memory:').upsert`
- **Cause:** the test was wrong, not the audit. Findings print the receiver through `ast.unparse`, which normalizes string quotes to single quotes.
- **Fix:** the test expects the unparsed form.

### 34. Ruff `E501` in `tests/test_audit.py`
- **Fix:** `ruff format` on the test file.

### 35. `pyproject.toml` turned CRLF after a scripted edit
- **Cause:** `Path.write_text` on Windows writes `\r\n`. `.gitattributes` would have normalized it on commit, but the working copy showed a CRLF warning.
- **Fix:** rewrote the file with LF bytes. Scripted edits write bytes or pass `newline="\n"`.

### 36. `uv sync --locked --extra testbed --extra llm` uninstalled 30 packages
- **Cause:** `uv sync` makes the venv match exactly the extras named. Running CI's sync command locally removed the `mem0` and `embed` extras (mem0ai, fastembed, qdrant-client and their deps).
- **Fix:** re-ran it with `--extra mem0 --extra embed` added, which reinstalled the same 30 packages. The Mem0 integration tests passed afterwards. Local syncs name every extra in use.

### 37. `retract audit nope.py` printed only `retract audit: nope.py`
- **Cause:** `FileNotFoundError(path)` stringifies to the bare path.
- **Fix:** the message is now `no such file or directory: <path>` (exit code 2). Findings are also sorted by line, since `ast.walk` yields them breadth-first.

## Groq default model and response cache

### 38. Patch to `retract/derive/cache.py` failed: `AssertionError` on the search text
- **Cause:** entry 31 again. The patch searched for a newline escape inside a shell-embedded Python script, and the doubled backslash reached Python as a single one, so the search text held a real newline.
- **Fix:** nothing had been written, because the patch asserts every pair first. The file was rewritten through the editor tool. Entry 31's rule stands: edits with backslashes never go through a heredoc.

### 39. Ruff `E501` in `cache.py` and `I001` in `tests/test_llm.py`
- **Fix:** `ruff check --fix` sorted the imports, and `ruff format` wrapped the line.

## M8a (FakeLLM experiment harness)

### 40. Stats tests failed: `wilson(30, 30)` upper bound and nested `pytest.approx`
- **Cause:** the tests were wrong. The upper bound comes out as 1 - 1e-16 in floating point, not exactly 1.0, and `pytest.approx` does not compare nested tuples.
- **Fix:** compare the upper bound with `approx`, and unpack the paired result before comparing.

### 41. Quarantine op averaged 89.6 s on poisoned_doc in the first full run
- **Symptom:** mean `op_seconds` was 89.55 with bootstrap 95% [1.28, 265.38], against 1.15 for delete on the same closure. The same op takes 0.75 s serially, and a full rerun gave 0.84 [0.77, 0.92].
- **Cause:** not the code. A few trials whose op window spanned a stall of the machine recorded hundreds of seconds. The same session also saw a misleading 600 s tool timeout on a run whose workers lived about 3 minutes.
- **Fix:** M8a reports no wall-clock timing. Time under a 12-process pool is not a cost measurement anyway. The M8b cost split runs serially and reports medians.

### 42. Shell-embedded patch to `experiments/run.py` failed on a newline escape
- **Cause:** entry 31 again. The patch carried a doubled backslash, which reached Python as a single one.
- **Fix:** `configs.py` (no backslashes) had been patched; `run.py` had not, since the patch asserts every pair before writing. `run.py` was then edited with the editor tool.

### 43. `make: command not found` when running `make experiments`
- **Cause:** entry 2: Git Bash on this machine has no make.
- **Fix:** ran the recipe's single command, `uv run python -m experiments.run`. The Makefile is unchanged; CI does not use make.

### 44. Ruff `E501` in `experiments/run.py` and `testbed/scenarios.py`; mypy errors in `experiments/`
- **Fix:**
  - The residue predicates moved into a module-level `FLAGS` table next to `METRICS`.
  - Long lines were wrapped.
  - A reused variable name in `_probe` was renamed.
  - `states()` got an annotation.
  - `clopper_pearson_upper_zero` uses `math.pow`, so it returns `float` and not `Any`.

### 45. One unidentified test failure and one spinning pytest under CPU load
- **Symptom:** the full suite, run alongside a 12-process experiment run, reported `1 failed, 90 passed` once. The failure's name was lost, because the command kept only the last output line. A later run under the same load left one pytest process burning CPU (242 CPU-seconds in 10 minutes) until it was stopped.
- **Not reproduced:** 7 further runs under load passed: 6 of the guard, engine, testbed and experiment modules, and 1 full verbose run with `faulthandler_timeout`. Without load the suite passes every time.
- **Likely cause (unconfirmed):** `test_threaded_writes_racing_block_are_never_missed` is the only test with a thread. Its non-daemon writer loops up to 100,000 writes until BLOCK rejects 3. If the main thread failed first (`warmed.wait(30)` timing out under load, or `database is locked` after `busy_timeout = 5000` while the tight writer loop starves it), no rejection would ever come and the writer would spin.
- **Fix:** the writer stops when the main thread fails before BLOCK. It is a daemon thread, the test joins it with a 60 s cap and asserts it ended. A throwaway test that forced BLOCK to raise confirmed the test now fails in 0.5 s. If a failure shows up again, the log keeps the full `-rf` report.
