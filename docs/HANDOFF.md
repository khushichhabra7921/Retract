# Handoff: finishing Retract without Claude Code

Written Nov 19 2026. Everything below is for you to do by hand. Run every command in PowerShell from
the repo folder:

```powershell
cd C:\Users\hp\OneDrive\Desktop\Retract
```

## 0. Where things stand

| Item | State |
|---|---|
| SDK, engine, receipts, dashboard, audit, testbed, FakeLLM experiments | Done and committed |
| Postgres backend, per-closure locks, id-list batching, VERIFY race fix | Done and committed (`44d3b21`, `b510f04`) |
| Scale benchmark (1K–100K records, two runs) | Done; results in `experiments/results/scale.json` and RESULTS 6.12 |
| Groq 5-seed runs | In progress. Cache at Nov 18: gpt-oss 1,136 responses, qwen 1,461. Each needs about 2,740 |
| Tests | 183 pass on SQLite and Postgres; ruff and mypy clean |
| GitHub | Everything pushed (`bf666a3`). CI passed on Nov 18, all four jobs including the new `postgres` job |

The only required work left is finishing the Groq runs and writing up their results (sections 2–4).
Section 7 lists optional engineering work.

## 1. `make` is not installed

The README and older notes use `make test`, `make demo` and so on. `make` is not on this laptop,
so this guide gives the full `uv` command each time. If you install make later, the Makefile
targets run the same commands.

## 2. Daily Groq runs (until both models finish)

### 2.1 Each day

1. Set the key for this PowerShell window only. It is typed hidden and never saved:

   ```powershell
   $s = Read-Host "Groq key" -AsSecureString; $env:GROQ_API_KEY = [System.Net.NetworkCredential]::new('', $s).Password
   ```

2. Run the two models **one after the other, in the same window**. Never in two windows at once:
   both append to `experiments/cache/groq.jsonl`, and two writers can corrupt it. The `;` makes the
   second start even if the first stops at the daily limit:

   ```powershell
   uv run --extra testbed --extra llm python -m experiments.run --llm groq --seeds 5; uv run --extra testbed --extra llm python -m experiments.run --llm groq --model qwen/qwen3.8-27b --seeds 5
   ```

   Once a model has finished (section 3), drop its half of this line.

3. When both stop, commit and push the cache:

   ```powershell
   git add experiments/cache/groq.jsonl
   git commit -m "data(experiments): Groq cache, Nov <day> runs"
   git push
   ```

4. Close the window, or clear the key: `Remove-Item Env:GROQ_API_KEY`.

### 2.2 How to read the output

- Each finished trial prints a line like `[37/105] poisoned_doc seed 11 auto: 98 requests, 60 cached, 38 HTTP, 21004 tokens (total HTTP 412, tokens 190233)`.
- **Daily limit hit:** the run stops with `failed at <scenario> seed <n> <config>; rerun to resume from the cache` and a traceback. That is expected. Nothing is lost: every response already received is in the cache, and the same command tomorrow skips them.
- **Finished:** the run prints a results table and ends with `results: experiments/results/groq` (gpt-oss) or `results: experiments/results/groq-qwen-qwen3.8-27b` (qwen), with no traceback. Go to section 3 for that model.
- **`GROQ_API_KEY is not set in the process environment`:** repeat step 1 in this window.
- **`corrupt cache entry`:** the cache file has a broken line. Do not edit it by hand. Restore the last committed version with `git checkout -- experiments/cache/groq.jsonl` and rerun; you lose only that day's uncommitted responses.

### 2.3 Progress check

```powershell
Get-Content experiments\cache\groq.jsonl | ForEach-Object { $_ | ConvertFrom-Json } | Group-Object model | ForEach-Object { [pscustomobject]@{ Model = $_.Name; Responses = $_.Count; Tokens = (($_.Group | ForEach-Object { $_.usage.total_tokens }) | Measure-Object -Sum).Sum } } | Format-Table -AutoSize
```

Each model needs about 2,740 responses (an estimate: 548 per seed × 5 seeds). The run itself is the
real signal: a model is done only when its command ends with `results: ...`.

### 2.4 Limits and cost

- Groq's free plan allows 200K tokens per day per model. In practice it behaved as a rolling 24-hour window: a second run the same day gets only what is left of the 200K.
- gpt-oss uses about 550 tokens per response, so about 350 responses per day: roughly 5 more days. qwen uses about 265, so about 700 per day: roughly 2 more days.
- Confirm once on the Groq console's billing page (console.groq.com) that the account is on the **Free** plan with no card attached, so nothing can be charged.

## 3. When a model finishes

Do this once per model. The commands below are for gpt-oss; for qwen, add
`--model qwen/qwen3.8-27b` and use the folder `experiments/results/groq-qwen-qwen3.8-27b`.

1. Commit the results and the cache:

   ```powershell
   git add experiments/results/groq experiments/cache/groq.jsonl
   git commit -m "data(experiments): Groq gpt-oss-120b, 5 seeds, all scenarios"
   ```

2. Check that the cache alone reproduces them, with no key and no network:

   ```powershell
   Remove-Item Env:GROQ_API_KEY -ErrorAction SilentlyContinue
   uv run --extra testbed --extra llm python -m experiments.run --llm groq --seeds 5 --replay
   git status --short experiments/results
   ```

   `git status` must print nothing: the replay rewrote identical files. If it lists changed files,
   run `git diff experiments/results` to see what changed, and do not write up the results until
   that is understood.

3. `git push`.

## 4. After both models finish: write up the results

### 4.1 Make the default match the new runs

In `experiments/configs.py`, change

```python
GROQ_SEED_COUNT = 1  # n for the Groq runs: ~234K tokens per seed vs a 200K/day free-plan limit
```

to

```python
GROQ_SEED_COUNT = 5  # n for the Groq runs (Nov 2026: about 5 days of the free plan per model)
```

Otherwise a replay without `--seeds` (including `make experiments-groq-replay`) replays 1 seed
and overwrites the 5-seed files with 1-seed results.

### 4.2 Read the numbers

Open `experiments/results/groq/summary.md` and `experiments/results/groq-qwen-qwen3.8-27b/summary.md`.
For each scenario (priya, poisoned_doc, wrong_user) they give, per config:

- binary columns (structural residue, guarded retrieval residue) as k/n with a Wilson 95% interval;
- continuous columns (contaminated answers, utility, record preservation, honest-fact retention, over-retraction, short guarded reads) as mean [bootstrap 95% interval];
- a "Runtime post-repair check vs the oracle" table: TP, FP, FN, TN per config. **FP is the false-alarm count on real LLM output**, the number the docs currently give as "0 of 54".

For token totals, use the progress command in 2.3.

### 4.3 Edit `docs/RESULTS.md`

Line numbers drift, so search for the quoted text.

1. **Top bullet**, starting `- **Hosted-LLM results** (Groq, n = 1 seed)`. Replace the whole bullet with:
   `- **Hosted-LLM results** (Groq, two models, n = 5 seeds each) are in section 6.11, produced by the current code.`
2. **Abstract**, the line `raised no false alarms on 54` / `replacements written by a hosted LLM.`: put in the new FP total and the total number of replacements (FP + TN, summed over both models and scenarios). If FP is not 0, say so plainly.
3. **Section 6.11.** Replace everything from `### 6.11 Hosted LLM` up to (not including) `### 6.12 Scale` with a new section. Template, fill the `<...>` from summary.md:

   ```markdown
   ### 6.11 Hosted LLM (Groq, two models, n = 5)

   **Files:** `experiments/results/groq/` (`openai/gpt-oss-120b`) and
   `experiments/results/groq-qwen-qwen3.8-27b/` (`qwen/qwen3.8-27b`, a preview model, a different
   family). Both replay from `experiments/cache/groq.jsonl` with no key:
   `python -m experiments.run --llm groq --seeds 5 --replay`, adding
   `--model qwen/qwen3.8-27b` for qwen.

   **Setup.** Test seeds 10–14, paired across configs. Scenarios priya, poisoned_doc and
   wrong_user. Configs clean, naive_delete, refcount_remove, delete, auto, refetch and quarantine;
   the injection configs are FakeLLM-only. Prompts frozen (`v1`), temperature 0. Cached responses:
   <N> for gpt-oss (<T> tokens) and <N> for qwen (<T> tokens).

   **n = 5 per cell.** A 0/5 result has a Wilson 95% interval of [0.000, 0.434]: it rules out
   rates above about 43%, nothing finer. Read the intervals, not just the means.

   | poisoned_doc | contaminated answers (gpt-oss / qwen) | utility | record preservation | honest-fact retention |
   |---|---|---|---|---|
   | clean | <...> | <...> | n/a | n/a |
   | unretracted | <...> | <...> | n/a | n/a |
   | naive_delete | <...> | <...> | <...> | <...> |
   | refcount_remove | <...> | <...> | <...> | <...> |
   | delete | <...> | <...> | <...> | <...> |
   | auto | <...> | <...> | <...> | <...> |
   | refetch | <...> | <...> | <...> | <...> |
   | quarantine | <...> | <...> | <...> | <...> |

   - **Residue:** <structural and guarded residue per Retract mode, both models>.
   - **Utility:** <does unretracted/naive_delete lose utility, and do Retract modes restore it?>.
   - **False alarms on real output:** <FP> of <FP + TN> replacements (gpt-oss <..>, qwen <..>).
   - **refcount_remove ([4]-style) vs auto:** <...>.
   - **refetch vs auto:** <honest-fact retention gap to clean>.
   - **wrong_user:** <...>.
   - **The two models:** <where they differ, if anywhere>.
   ```

   Keep a finding only if the intervals support it. The old 6.11 bullets (utility separates the
   configs, the four stages hold, same trade-offs as FakeLLM) are claims to re-check against the new
   numbers, not to copy. Delete the old "Budget for the re-run" block.
4. **Section 6.4**, `LLM (6.11): 0 of 54 replacements flagged.`: put in the new FP count.
5. **Section 7**, `of 54 replacements on one seed (6.11)`: same.
6. **Section 7**, `Groq run (6.11) shows utility separating the configs on poisoned_doc, but it is one model on one` / `seed.`: rewrite for two models and 5 seeds.
7. If a result goes against the claims (FP > 0, residue > 0, a model that behaves differently), add it to the numbered "Negative results and limitations" list in section 7.

### 4.4 Edit `README.md`

1. The bullet starting `- **Hosted LLM** (Groq \`openai/gpt-oss-120b\`, 1 seed`: rewrite as two models, 5 seeds, with the headline numbers. Its link must point at the new heading: `docs/RESULTS.md#611-hosted-llm-groq-two-models-n--5` (that is the anchor GitHub generates for `6.11 Hosted LLM (Groq, two models, n = 5)`; if you choose another heading, click it on GitHub and copy the anchor from the address bar).
2. In the Roadmap, delete the line `- Hosted-LLM results with more seeds and a second model ...`.
3. Optional: under `make experiments-groq-replay` in the command table, add a row for the qwen replay command.

### 4.5 Edit `docs/PLAN.md`

- The line containing `The question was redesigned (above); not yet re-run on Groq.`: change the end to `re-run on Groq with 5 seeds on Nov <day>`.
- The line containing `**n = 1** (test seed 10; user decision Nov 12)`: add a sentence `Superseded: 5 seeds per model, two models (Nov <day>); GROQ_SEED_COUNT = 5.`
- The line containing `The full Groq results predate M9`: mark it done.

### 4.6 Check and commit

```powershell
uv run ruff check .
uv run mypy
uv run --extra testbed --extra llm --extra dashboard --extra postgres python -m pytest
```

All must pass. Without `RETRACT_TEST_PG` (6.2) only the SQLite variants run, so expect fewer
than the 183 tests counted with Postgres. Then:

```powershell
git add experiments/configs.py docs/RESULTS.md README.md docs/PLAN.md
git commit -m "docs: Groq results, two models, 5 seeds"
git push
```

`uv run ruff format --check .` flags `README.md` and `docs/retract-project-brief.md` (Markdown
code blocks). That is pre-existing and CI does not check it; ignore it.

## 5. CI on GitHub

```powershell
gh run list --limit 5
gh run watch
```

Or open https://github.com/khushichhabra7921/Retract/actions. Each push runs four jobs: three test
jobs (Ubuntu 3.11, Ubuntu 3.12, Windows 3.12) and `postgres`. All four passed on Nov 18. To see the
jobs of one run: `gh run view <run id>`. If a job fails, click it and read the first red step.

The repo is private, so Actions minutes come out of your plan's monthly quota (Windows minutes
count extra). Check Settings → Billing → Actions if you push often.

## 6. Everyday commands

### 6.1 Tools

`uv` lives in `C:\Users\hp\AppData\Roaming\Python\Python312\Scripts`. If PowerShell says `uv` is
not recognized, add that folder to PATH (Windows search → "Edit environment variables for your
account" → Path → New), then open a new window. `uv sync --extra testbed --extra llm --extra dashboard --extra postgres`
installs everything.

### 6.2 Postgres tests (optional; CI runs them anyway)

PostgreSQL 17 is installed in `C:\Program Files\PostgreSQL\17`. The throwaway test server's data is
in `%TEMP%\retract-pg`. Start it, run the tests, stop it:

```powershell
& "C:\Program Files\PostgreSQL\17\bin\pg_ctl.exe" -D "$env:TEMP\retract-pg" -o "-p 54329 -c listen_addresses=127.0.0.1" -l "$env:TEMP\retract-pg.log" -w start
$env:RETRACT_TEST_PG = "postgresql://postgres@127.0.0.1:54329/postgres"
uv run --extra testbed --extra llm --extra dashboard --extra postgres python -m pytest
& "C:\Program Files\PostgreSQL\17\bin\pg_ctl.exe" -D "$env:TEMP\retract-pg" -w stop
```

If you deleted `%TEMP%\retract-pg`, recreate it once with:

```powershell
& "C:\Program Files\PostgreSQL\17\bin\initdb.exe" -D "$env:TEMP\retract-pg" -U postgres -A trust -E UTF8
```

### 6.3 Demo, experiments, benchmark

| Command | What |
|---|---|
| `uv run --extra dashboard python -m retract.cli demo` | Priya demo on the local dashboard (http://127.0.0.1:8765) |
| `uv run --extra testbed python -m experiments.run` | FakeLLM experiments (offline, writes `experiments/results/fake`) |
| `uv run --extra testbed --extra llm python -m experiments.run --llm groq --seeds 5 --replay` | Groq results from the cache, no key (add `--model qwen/qwen3.8-27b` for qwen) |
| `uv run python -m experiments.scale --pg postgresql://postgres@127.0.0.1:54329/postgres` | Scale benchmark, 1K–100K records, about 1.5 hours (needs the server from 6.2) |

For the scale benchmark: laptop plugged in, lid open, and set Windows sleep to "Never" for the run.
In our runs Windows went into standby despite the keep-awake setting and inflated the timings. Add
`--sizes 1000 10000` for a 10-minute run. It overwrites `experiments/results/scale.json`; pass
`--out somewhere.json` to keep the committed results.

## 7. Optional engineering work (roadmap)

None of this is needed for the MVP. In order of value:

1. **Retraction cost that tracks the closure, not the store.** Today a 5-record retraction takes about 1 s at 1K records but 54–68 s at 100K (RESULTS 6.12). Three whole-store scans cause it:
   - `orphan_scan()` (`retract/core/sdk.py`, `orphan_scan`) lists every adapter id and does a linear `find_by_token` per pending row. VERIFY calls it in `retract/core/engine.py` (`_verify`).
   - `readable_with_unreadable_parent()` (`retract/core/lineage.py`) joins all records.
   - `independent_primaries(source)` (`retract/core/lineage.py`) returns every independent primary record, which `residue.measure` (`retract/probes/residue.py`) and the post-repair check compare against.

   Fix: restrict the orphan check to the closure's tiers and the ids this op touched, restrict the
   parent check to the closure and its children, and look up supports through an index (or only
   the records the probes actually retrieved). Keep `test_orphan_scan_snapshot_reconciled_since_is_not_an_orphan`
   and the other tests in `tests/test_concurrency.py` passing. Done when `--sizes 1000 100000` shows
   the "everything else" time flat across sizes.
2. **Parallel retractions in processes, not threads.** Threads gave 1.8–2.4× at 1K and nothing at 10K–100K, because the checks are CPU-bound Python (GIL). Needs an adapter that several processes can share, such as Mem0 or a managed vector store.
3. **Paraphrases that drop identifying details** ("a customer prefers a messaging app") are caught by neither the novel-token rule nor the oracle. Needs a meaning-based check and a labeled test set.
4. **More adapters:** managed vector stores, LangGraph store.
5. **`retract audit`:** cross-module dataflow.
6. **Erasure beyond L2:** vector-index internals.

## 8. Cleanup and housekeeping

- `e -i --root` in the repo root is a stray text file: the output of a `git log` saved by accident on Nov 12. Delete it: `Remove-Item -LiteralPath "e -i --root"`.
- The local branch `backup-before-removing-claude` holds the history from before it was rewritten (commits such as `a003a94` and `e7e6860` that are not on GitHub). It was never pushed. Keep it as a record, or delete it with `git branch -D backup-before-removing-claude`; deleting is permanent once git garbage-collects.
- `%TEMP%\retract-pg` is the throwaway Postgres server. Delete it if you won't run Postgres tests locally: `Remove-Item -Recurse -Force "$env:TEMP\retract-pg"` (the server must be stopped).
- Commit `dead9b1` has no Co-Authored-By line, unlike the other commits written with Claude Code. It is already on GitHub, so changing it would need a history rewrite and force-push. Leave it.

## 9. Rules that still apply

- Keep the repo **private**. Never paste keys into files: `.env.example` lists the variables, but Retract reads them only from the environment.
- No paid models. Use only the Groq free plan, or the offline FakeLLM.
- Every number in README and RESULTS must come from a committed file under `experiments/results/`, named in the section. Claims about the market or adoption stay labeled as hypotheses.
- When a result is bad, report it (RESULTS section 7) rather than tuning until it looks good.
