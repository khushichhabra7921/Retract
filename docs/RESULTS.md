# Retract: source-level retraction across multi-agent memory — results

Draft, Nov 12 2026. Structured as a paper skeleton. Every number below comes from a committed result
file under `experiments/results/`, and each section names the file. Hosted-LLM results (Groq,
n = 1 seed) are in section 6.8.

## Abstract

Multi-agent systems copy what they learn into many memories: private notes, shared briefs, plans and
summaries, often in different stores. Deleting a user's records, or a document later found to be
false, removes the originals but leaves every derived copy in place. We built Retract, a
Python SDK that records which records were derived from which (lineage). From that, `retract(source)`
finds every affected record across agents, tiers and stores and blocks it from reads. It then
repairs each one: delete it, re-derive it from the inputs that remain, or quarantine it. Finally it
issues a signed receipt with structural and retrieval-residue checks.

We evaluated Retract in a three-agent LangGraph testbed with two scenarios: a user's facts (priya)
and a benign false policy document (poisoned_doc). We used 30 paired test seeds, an offline fake LLM
and an independent fact-level oracle.

- **Retraction leaves no residue.** Every retraction mode left zero structural and zero guarded
  retrieval residue in 30 of 30 trials per scenario (Wilson 95% upper bound 0.114). Naive deletion
  of the source's own records left residue in 30 of 30 trials, and on poisoned_doc 99.3% of answers
  stayed contaminated.
- **Re-derivation keeps the most memory.** On poisoned_doc it kept 99.2% of readable records and
  all honest fact mentions. Deleting or quarantining the whole affected set kept 58.1% of records
  and 11.5% of honest fact mentions.
- **Negative result: re-derivation does not reproduce the counterfactual clean world.** It keeps
  54.5 fewer honest fact mentions than a world that never saw the false document.
- **Negative result: paraphrased leaks are never caught.** The runtime post-repair check catches
  every verbatim leak, but no paraphrased one at any similarity threshold we tried. Those
  retractions still report success.

We state these limits rather than claim oracle-grade verification.

## 1. Problem

An agent memory stores text an agent wrote, and much of that text is derived from other records:

- a brief summarizing a customer's facts,
- a research note built on the brief,
- a plan built on the note.

When a source must be removed, the derived records keep its content. The source might be a user
invoking deletion rights, a revoked document, or a poisoned or simply wrong document. Memory vendors
leave this to the application. Mem0 delegates deletion and audit consistency to the application [4].
Zep links derived facts to source episodes, but deleting an episode does not regenerate the names
or summaries of nodes shared with other episodes [5][6].

The question is operational: can a system find everything derived from a source across agents and
stores, stop agents from reading it immediately, repair what can be repaired, and show evidence of
what happened?

## 2. Related work

We claim nothing new about dependency tracing, selective replay or provenance-based forgetting on
their own. Agentic unlearning [4] and dependency-guided rollback repair for memory-augmented agents
[25] address closely related problems.

Our contribution is narrower: a vendor-neutral prototype that combines four things across
instrumented multi-agent memory stores, plus a controlled evaluation of the residual exposure it
leaves:

- lineage-aware repair,
- guarded retrieval,
- auditable receipts,
- residue verification.

Both build derivation graphs over agent memory, but each works within a single agent and a single
store:

- [4] removes dependent artifacts, using reference counting and a retrieval blocklist, and also
  unlearns model parameters.
- [25] starts from diagnosed faulty memories and selectively replays affected steps to recover a
  failed task.

Retract starts from a source and works across agents and stores. It adds the operational protocol
(guarded reads, a BLOCK transaction, holds, journaled resume) and signed receipts with separate
verification dimensions.

[25] also reports a finding that parallels ours: selective replay leaves more recurrence than
broader recomputation. The full comparison is in [docs/RELATED_WORK.md](RELATED_WORK.md). We have
not compared the methods empirically.

## 3. System

### 3.1 Lineage capture

Records are written through the SDK:

- **Primary records:** `rt.add(content, sources=[...])` writes a record owned by its sources (for
  example `user:u_priya`).
- **Derived records:** functions registered with `@rt.derives(op, agent=..., tier=...)` write
  records whose parent edges are their declared `inputs=`.
- **Step read-sets:** inside `with rt.step(agent, tier):`, every record returned by a guarded read
  also becomes a parent of every write in the step. This over-approximates on purpose: if an agent
  read something, we assume it could have influenced the write.

Labels propagate from parents to children, so each record carries the set of sources it was derived
from.

Writes are crash-safe. The SDK first journals a pending lineage row, then writes to the adapter
(stamped with a token), then activates the row. An orphan scan reconciles a crash between the steps.
A write whose inputs are held or tombstoned is rejected, which closes the race with a concurrent
retraction.

### 3.2 Guarded reads

`rt.search` and `rt.get` return only records that are `active` and not held by an operation. Search
over-fetches k×4 candidates from the store and filters them through lineage.

### 3.3 Retraction

`rt.retract(source, mode, dry_run=True)` computes the affected closure (the source's records and all
their descendants) and returns a plan with a closure hash. `plan.execute()` then runs:

1. **BLOCK:** one short transaction re-checks the closure hash, rejecting a stale plan with nothing
   touched. It then tombstones the source, places holds, and journals one action per record.
2. **REPAIR**, in topological order:
   - records the source owns are deleted;
   - in `auto` mode, other records are re-derived from their remaining valid inputs when the
     registered function is replayable, and quarantined otherwise;
   - `delete` mode deletes the whole closure; `quarantine` mode hides it.
3. **Post-repair check:** each replacement stays held until a runtime check finds no *forbidden
   fact* in it. A forbidden fact is a retracted sentence not supported by a valid input; matching
   uses normalized substrings plus embedding similarity. A replacement that fails is quarantined.
4. **PURGE:** deleted and superseded items are removed from their stores.
5. **VERIFY:**
   - **Structural:** no readable record carries the source, purged ids are absent from the
     adapters, and the orphan scan is clean.
   - **Retrieval residue:** probes generated locally from the retracted text run through guarded
     and raw search. They are held in memory only.

The operation journal and a durable lease lock make every step resumable: `rt.resume(op_id)`
continues a failed or crashed operation without repeating finished actions or duplicating writes.

The outcome is one of:

- `completed`, with a retraction receipt;
- `contained`, with a containment receipt, when something had to be quarantined;
- `failed`, with no success receipt; the records stay held.

Receipts are HMAC-signed and hash-chained. They carry keyed hashes and pseudonymous source
references, never raw content.

### 3.4 Guarantees

| Level | MVP |
|---|---|
| L1 Hidden from Retract-guarded reads | Enforced + tested |
| L2 | Purged records are verified absent from supported adapter APIs. Quarantined records may remain physically accessible through the underlying store and are hidden only by Retract-controlled reads. |
| L3 | Not established. `retract audit` provides best-effort detection of potential SDK bypasses but cannot prove complete read/write coverage (dynamic dispatch, indirect clients, external services, uninstrumented access). |
| L4 Physically erased from vector index internals | **Not established** |
| L5 Removed from backups / external caches / model weights | **Not established** |

## 4. Evaluation setup

### 4.1 Testbed

LangGraph runs three agents in sequence per ticket:

- **support** reads the customer's facts and KB docs, and writes an account brief to `shared`;
- **research** reads the brief and a doc, and writes a note to its private memory;
- **planning** reads `shared`, writes a plan, and answers the ticket from guarded reads.

Each agent works inside `rt.step`, and every write is replayable. Each tier is a separate
SimpleVectorStore, so every retraction crosses distinct adapters. The research tier also runs on Mem0
in integration tests.

Data per seed:

- 20 personas with 2 facts each: 3 hand-written and 17 generated;
- 28 KB docs;
- a true refund policy (RP-30, 30 days) and a false one (RP-90, 90 days).

Every fact carries a unique marker present in its text and in both of its paraphrases.

### 4.2 Scenarios

- **priya:** retract `user:u_priya` after 20 tickets. Questions: "What do we know about {name}?"
- **poisoned_doc:** retract the false policy `doc:d30`. Questions: "{name} bought from us 60 days
  ago. Can {name} still get a refund?" The policy decides the answer: RP-30 says no, RP-90 says yes.

### 4.3 Configs (paired by seed)

| config | what happens |
|---|---|
| clean | the target source is never ingested: the reference world |
| unretracted | the target is ingested and never removed (each retraction run's pre-retraction state) |
| naive_delete | the source's own primary items are deleted from their stores, bypassing Retract |
| delete | `rt.retract(mode="delete")` |
| auto | `rt.retract(mode="auto")` |
| quarantine | `rt.retract(mode="quarantine")` |
| auto_leak | auto, while the fake LLM re-emits one target fact verbatim during repair |
| auto_paraphrase | auto, while it re-emits a paraphrase of that fact |

### 4.4 Seeds, LLM and oracle

- **Seeds:** a seed changes the code words and templates of the generated personas and docs, and
  the ticket order.
- **Dev seeds 0–9** were used only to tune the similarity threshold. **Test seeds 10–39** produced
  every reported result.
- **Pairing check:** every config of a seed sees the same data, order and questions. The runner
  fails unless all retraction runs of a seed start from an identical world.
- **LLM:** FakeLLM, which is deterministic and offline. Its summaries concatenate their inputs and
  its answers are extractive. This makes residue propagation maximal and utility insensitive (see
  section 7).
- **Oracle:** an independent fact-level oracle, which the SDK never sees, decides whether a text
  contains a fact.

### 4.5 Metrics (per trial)

- **Structural residue:** physical items that are not quarantined and carry a target fact.
- **Guarded retrieval residue:** probes with a target-carrying hit through guarded search. Probes
  are each target fact's text and both paraphrases, run on every tier with k=5.
- **Answer contamination:** the share of the 20 answers containing a target fact.
- **Utility:** the share of scored answers containing an expected fact. For priya that is the
  persona's own facts, with Priya's question excluded; for poisoned_doc it is RP-30.
- **Record preservation:** readable records after / before. "Readable" also requires the item to be
  present in its store.
- **Honest-fact retention:** non-target fact mentions in readable derived records, after / before.
- **Over-approximation and over-retraction:**
  - closure over-approximation: closure records whose content holds no target fact;
  - over-retraction: those that are not readable afterwards, directly or through a replacement.
- **Runtime check:** every replacement is labeled TP, FP, FN or TN — runtime flagged (quarantined)
  vs oracle leaked.
- **Short guarded reads:** reads that return fewer than k results although the tier holds k
  readable records.

### 4.6 Statistics

The unit is the trial; probes and replacements within a trial are not independent.

- **Binary outcomes:** k/n with a two-sided 95% Wilson interval. At 0/n we also give the exact
  one-sided 95% Clopper–Pearson upper bound.
- **Continuous rates and paired differences:** 95% trial-level percentile bootstrap, with
  B = 10,000 and a fixed seed.

## 5. Reproducing

```bash
make experiments              # FakeLLM, 30 test seeds, plus the serial cost benchmark (about 4 min)
make experiments-groq-replay  # Groq results from the committed response cache, no key needed
```

| Output | File |
|---|---|
| Per-trial rows | `experiments/results/fake/trials.jsonl` |
| Intervals | `experiments/results/fake/summary.json` and `summary.md` |
| Cost | `experiments/results/fake/cost.json` |
| Threshold sweep | `experiments/results/tuning.json` |

`trials.jsonl` and `summary.*` are byte-identical across reruns. Timings in `cost.*` vary from run
to run.

## 6. Results (FakeLLM, test seeds 10–39, n = 30 per cell)

Source: `experiments/results/fake/summary.json`, except where noted.

### 6.1 Residue

| config | structural residue (priya / poisoned_doc) | guarded retrieval residue | answer contamination, poisoned_doc |
|---|---|---|---|
| clean | 0/30 / 0/30 | 0/30 / 0/30 | 0.000 |
| unretracted | 30/30 / 30/30 | 30/30 / 30/30 | 0.993 [0.983, 1.000] |
| naive_delete | 30/30 / 30/30 | 30/30 / 30/30 | 0.993 [0.983, 1.000] |
| delete | 0/30 / 0/30 | 0/30 / 0/30 | 0.000 |
| auto | 0/30 / 0/30 | 0/30 / 0/30 | 0.000 |
| quarantine | 0/30 / 0/30 | 0/30 / 0/30 | 0.000 |

- **Upper bounds:** for every 0/30 cell, the Wilson 95% interval is [0.000, 0.114] and the exact
  one-sided Clopper–Pearson 95% upper bound is 0.095. For every 30/30 cell, Wilson is
  [0.886, 1.000].
- **priya contamination:** answers carried Priya's facts in 0.050 of answers before retraction
  (her own question, 1 of 20) and 0.000 after any retraction mode.
- **Quarantine leaves content in the stores.** Raw search through the store still finds the
  quarantined items in 30/30 trials of both scenarios, as L2 states: they are hidden from guarded
  reads, not purged.

### 6.2 What each mode keeps

| | record preservation | honest-fact retention | readable records vs clean (paired) |
|---|---|---|---|
| poisoned_doc, auto | 0.992 [0.992, 0.992] | 1.000 [1.000, 1.000] | +0.0 [0.0, 0.0] |
| poisoned_doc, delete / quarantine | 0.581 [0.572, 0.589] | 0.115 [0.095, 0.134] | −53.5 [−54.7, −52.4] |
| priya, auto | 0.985 [0.985, 0.985] | 1.000 [1.000, 1.000] | +0.0 [0.0, 0.0] |
| priya, delete / quarantine | 0.961 [0.960, 0.962] | 0.969 [0.968, 0.970] | −3.1 [−3.2, −3.0] |

On poisoned_doc, the false policy reached a closure of 54.5 [53.4, 55.7] records. Deleting or
quarantining all of them removes most of the derived memory. Re-derivation keeps every record that
had other valid inputs, rebuilding them with 53.5 [52.4, 54.7] LLM calls per trial.

### 6.3 Stages of the false policy (poisoned_doc, auto runs)

1. **Entered memory:** once (the document itself).
2. **Retrieved or promoted:** into 53.5 [52.4, 54.7] derived records across the support, research
   and planning memories.
3. **Influenced answers:** 19.87 [19.67, 20.00] of 20.
4. **Gone after retraction:** 30/30 trials, with no contaminated answer, no readable record and no
   unquarantined item.

### 6.4 Runtime post-repair check under injection

Each cell counts replacements: the oracle's verdict vs the runtime check's.

| config | TP | FP | FN | TN | trials with ≥1 FN | outcome |
|---|---|---|---|---|---|---|
| priya auto | 0 | 0 | 0 | 92 | 0/30 | completed 30 |
| priya auto_leak | 91 | 0 | 0 | 0 | 0/30 | contained 30 |
| priya auto_paraphrase | 0 | 0 | 92 | 0 | 30/30 | completed 30 |
| poisoned_doc auto | 0 | 0 | 0 | 1605 | 0/30 | completed 30 |
| poisoned_doc auto_leak | 1098 | 0 | 0 | 0 | 0/30 | contained 30 |
| poisoned_doc auto_paraphrase | 0 | 0 | 1605 | 0 | 30/30 | completed 30 |

- **Verbatim leaks are always caught.** The leaking replacements are quarantined and the operation
  reports `contained`.
- **Paraphrased leaks are never caught.**
  - Because the residue probes use the same heuristic, the operation still reports `completed`
    with retrieval residue `pass`. Meanwhile the oracle finds structural residue in 30/30 trials,
    and 100% of poisoned_doc answers are contaminated (29/30 trials with a contaminated answer on
    priya).
  - The receipt's retrieval-residue `pass` therefore means "no residue the runtime heuristic can
    see", not "no residue".
- **Cascade cost of containment:** a quarantined replacement is never used to rebuild its children,
  so on poisoned_doc only 36.6 [36.0, 37.2] of the 53.5 re-derivations happen.

### 6.5 Threshold tuning (dev seeds 0–9; `experiments/results/tuning.json`)

| threshold | TP | FN | FP | TN |
|---|---|---|---|---|
| 0.40 | 49 | 1073 | 0 | 561 |
| 0.45 | 42 | 1080 | 0 | 561 |
| 0.50 | 26 | 1096 | 0 | 561 |
| 0.55–0.70 | 30 | 1092 | 0 | 561 |
| 0.75–0.95 | 393 | 561 | 0 | 561 |

- **Rule, fixed before the sweep:** the lowest mean per-trial (FN + FP) / replacements, with ties
  going to the lower threshold. It picked 0.75, which is now frozen as the SDK default.
- **The sweep is not monotone.** Below 0.75 even verbatim leaks pass. A low threshold makes the
  check consider a retracted sentence "supported" by a look-alike honest input (RP-30 for RP-90),
  so the exemption swallows real leaks.
- **No threshold catches paraphrases.** With the hashed bag-of-words embedder, all 561 paraphrase
  replacements are FN at every threshold.

### 6.6 Over-approximation and over-fetch

- **Closure over-approximation was 0.000 in every mode and scenario.** In this testbed every step's
  read-set equals its declared inputs, so the read-set adds no extra parents. Over-approximation
  appears in the M1 slice instead, where a reminder about Arjun is tied to Priya through a step that
  read her summary. The testbed does not measure it.
- **Over-fetch shortfall under quarantine:** quarantine leaves 0.436 [0.371, 0.498] of guarded
  reads short on poisoned_doc. 54 quarantined records crowd the tiers past the k×4 over-fetch. auto,
  delete and clean have no short reads, and priya has none in any mode (5 quarantined records). On
  FakeLLM, utility is unchanged.

### 6.7 Cost (serial, auto, test seeds 10–14; `experiments/results/fake/cost.json`)

| | priya | poisoned_doc |
|---|---|---|
| write, Retract vs raw adapter (µs, median) | 7389 vs 132 (55.9×) | 7923 vs 146 (54.6×) |
| guarded vs raw read (µs, median) | 1191 vs 641 (1.8×) | 1206 vs 621 (1.9×) |
| retraction op (s, median) | 0.31 | 1.94 |
| … re-derivation / probes / rest (s) | 0.000 / 0.13 / 0.17 | 0.000 / 0.06 / 1.91 |
| LLM calls in the op | 4 | 54 |

- **Writes:** the write overhead is mostly durable SQLite commits on the crash-safe write path.
- **The op:** journal, BLOCK, purge and structural checks dominate it.
- **Re-derivation:** it is about zero only because the fake LLM is local. A hosted model adds its
  latency for each of the 4 or 54 calls.
- **Machine:** one Windows 11 laptop, Python 3.12.

### 6.8 Hosted LLM (Groq `openai/gpt-oss-120b`, n = 1; `experiments/results/groq/`)

**Setup.** Test seed 10 only, with clean, naive_delete, delete, auto and quarantine. The injection
configs are FakeLLM-only. Prompts are frozen (`v1`), temperature is 0, and all 423 responses are
cached in `experiments/cache/groq.jsonl` (233,946 tokens in total, 553 per request on average).
`make experiments-groq-replay` reproduced every result file byte for byte, with no key and no HTTP
calls.

**n = 1 is an illustration, not an estimate.** With one trial per cell, a binary outcome of 0/1 has
a Wilson 95% interval of [0.000, 0.793]. The table shows what happened on one seed; it does not give
rates. n was set by Groq's free-plan budget (200K tokens per day): one seed spanned two daily
windows.

| poisoned_doc, seed 10 | contaminated answers | useful answers (true policy RP-30) | readable records | honest mentions |
|---|---|---|---|---|
| clean | 0/20 | 20/20 | 129 | 279 |
| unretracted | 20/20 | 1/20 | 130 | 227 |
| naive_delete | 20/20 | 1/20 | 129 | 227 |
| delete | 0/20 | 20/20 | 78 | 36 |
| auto | 0/20 | 20/20 | 129 | 227 |
| quarantine | 0/20 | 20/20 | 78 | 36 |

- **Utility now separates the configs.** FakeLLM's extractive answers quoted both policies, so
  utility was 1.000 everywhere (6.1). The hosted model answers the question: with the false policy
  in memory, 20/20 answers grant the refund and only 1/20 cites RP-30. Naive deletion changes
  nothing. Every Retract mode restores 20/20 correct answers.
- **The four stages hold on a real model.** The false policy entered memory once, was promoted into
  51 derived records, influenced 20/20 answers, and was gone after retraction.
- **The same trade-offs as FakeLLM:** zero structural and guarded retrieval residue for every mode;
  auto kept 99.2% of records and all honest mentions; delete and quarantine kept 60.0% of records
  and 15.9% of honest mentions; quarantine left 60% of guarded reads short.
- **The runtime check released every replacement correctly:** TN 51 on poisoned_doc and TN 3 on
  priya, with no false positives. No injection was run.
- **Re-derivation vs clean:** the gap is −52 honest mentions on poisoned_doc, close to FakeLLM's
  −54.5. On priya, auto had **5 more** honest mentions than clean (282 vs 277), because hosted
  summaries differ in what they repeat. One seed cannot tell whether that is systematic.
- **Closure over-approximation was still 0.** We expected a real summarizer to drop facts that
  lineage still attributes to the source. With the `v1` prompt ("keep every distinct fact"), every
  record in the closure still contained the target fact. A prompt that compresses harder may differ.
- **priya** matches FakeLLM: 1/20 contaminated answers before retraction (Priya's own question) and
  0/20 after, with utility 19/19 throughout (Priya's own question is not scored).

## 7. Negative results and limitations

### Negative results

1. **Paraphrased leaks evade the runtime check** (6.4), at every threshold (6.5), and such
   operations report success. The runtime check is a heuristic, not an oracle. With FakeEmbedder no
   paraphrase is caught; a semantic embedder is untested.
2. **Re-derivation is not the counterfactual clean world.** On poisoned_doc, auto has the same
   number of readable records as clean, but 54.5 [53.1, 55.8] fewer honest fact mentions (paired).
   On priya the gap is 4.0 [3.1, 4.6]. Replay rebuilds a record from its old inputs minus the
   retracted one. It does not re-run the retrieval that would have picked another document in the
   first place, so the "meaning" of repaired memory drifts from what a clean history would have
   produced.
3. **Containment cascades.** One flagged replacement keeps all of its descendants out of
   re-derivation (6.4). Under leak injection, auto keeps only as much as delete.
4. **Over-fetch is insufficient under heavy quarantine** (6.6).
5. **Repairs drop read-set edges.** A replacement's parents are its declared inputs only, so
   over-approximated lineage from step read-sets disappears after a repair. Seen in the demo: after
   one repair, a reminder no longer carries Priya's label.

### Threats to validity

- **FakeLLM:**
  - It concatenates when it summarizes, so every derived record keeps every input fact. Residue
    propagation is therefore maximal, closure over-approximation is zero, and utility cannot
    separate the configs. The Groq run (6.8) shows utility separating the configs on poisoned_doc,
    but it is one model on one seed.
- **Synthetic data:** the facts and the oracle (unique code-word markers) are synthetic, and the
  oracle detects paraphrases only because they keep the marker.
- **One testbed topology:** three sequential agents, one store per tier. Mem0 is exercised in
  integration tests, not in the reported experiments.
- **Coverage (L3):** results hold only for reads and writes that go through the SDK.
  `retract audit` flags likely bypasses within a module; it cannot prove coverage.
- **Physical erasure (L4/L5):** not established. Local Qdrant, used by Mem0, keeps deleted text in
  freed SQLite pages.
- **Single machine** for all timings.

## 8. Hypotheses we did not test

These are startup, market and adoption claims from the brief. We did not validate any of them:

- That teams will route all agent-memory reads and writes through an SDK.
- That privacy and compliance buyers will accept signed receipts plus residue checks as deletion
  evidence.
- That source-level retraction is a purchase driver rather than a feature memory vendors add.

## References

The numbering follows `docs/retract-project-brief.md`.

- [4] "Agentic Unlearning: When LLM Agent Meets Machine Unlearning," arXiv:2602.17692.
- [5] Zep docs, "Episode metadata projection."
- [6] Zep docs, "Deleting Data from the Graph."
- [25] "From Faulty Memories to Corrected Actions: Dependency-Guided Rollback Repair for
  Memory-Augmented Agents," arXiv:2608.10502.
