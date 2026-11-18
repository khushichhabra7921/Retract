# Retract: source-level retraction across multi-agent memory — results

Draft, Nov 17 2026. Structured as a paper skeleton. Every number below comes from a committed result
file under `experiments/results/`, and each section names the file.

- **Hosted-LLM results** (Groq, n = 1 seed) are in section 6.11. They were produced by the Nov 12
  code and predate the M9 changes; a re-run under the current code is pending.
- **M9 changes** (Nov 17): a second runtime leak rule (novel tokens), a refetch repair, a
  remove-only baseline in the style of [4], and a third scenario (wrong_user).

## Abstract

Multi-agent systems copy what they learn into many memories: private notes, shared briefs, plans and
summaries, often in different stores. Deleting a user's records, or a document later found to be
false, removes the originals but leaves every derived copy in place. We built Retract, a Python SDK
that records which records were derived from which (lineage). From that, `retract(source)` finds
every affected record across agents, tiers and stores and blocks it from reads. It then repairs each
one: delete it, re-derive it, or quarantine it. Finally it issues a signed receipt with structural
and retrieval-residue checks.

We evaluated Retract in a three-agent LangGraph testbed with three scenarios, 30 paired test seeds,
an offline fake LLM and an independent fact-level oracle:

- **priya:** a user's deletion request;
- **poisoned_doc:** a benign false policy document;
- **wrong_user:** a CRM sync that misrouted data into other customers' memory.

**Findings:**

- **Retraction leaves no residue; deletion-only methods do.** Every retraction mode left zero
  structural and zero guarded retrieval residue in 30 of 30 trials per scenario (Wilson 95% upper
  bound 0.114). Two methods did not:
  - Naive deletion of the source's own records left residue in 30/30 trials (29/30 on wrong_user).
    On poisoned_doc, 99.3% of answers stayed contaminated.
  - A remove-only baseline in the style of [4] behaved exactly like naive deletion, because no
    derived record ever lost all of its parents.
- **Rebuilding from fresh retrieval reaches the clean world.**
  - Replaying old inputs minus the retracted one keeps 54.5 fewer honest fact mentions than a world
    that never saw the false document.
  - Re-running the retrieval behind each record (refetch) closes that gap to 1.1, with the same
    number of LLM calls.
  - On wrong_user, replay also lowers answer quality; refetch restores it.
- **Paraphrased leaks are now caught, with a caveat.**
  - A second runtime rule flags replacements carrying the retracted text's *novel tokens*: words no
    remaining input supplies.
  - It caught every injected paraphrase (from 0% before), and raised no false alarms on 54
    replacements written by a hosted LLM.
  - It relies on paraphrases keeping identifying details, the same property the oracle uses, so
    this is not evidence for paraphrases that drop them.

## 1. Problem

An agent memory stores text an agent wrote, and much of that text is derived from other records:

- a brief summarizing a customer's facts,
- a research note built on the brief,
- a plan built on the note.

When a source must be removed, the derived records keep its content. The source might be a user
invoking deletion rights, a revoked document, a poisoned or simply wrong document, or a faulty tool.
Memory vendors leave this to the application. Mem0 delegates deletion and audit consistency to the
application [4]. Zep links derived facts to source episodes, but deleting an episode does not
regenerate the names or summaries of nodes shared with other episodes [5][6].

The question is operational: can a system find everything derived from a source across agents and
stores, stop agents from reading it immediately, repair what can be repaired, and show evidence of
what happened?

## 2. Related work

We claim nothing new about dependency tracing, selective replay or provenance-based forgetting on
their own. Agentic unlearning [4] and dependency-guided rollback repair for memory-augmented agents
[25] address closely related problems.

Both build derivation graphs over agent memory, but each works within a single agent and a single
store:

- [4] removes dependent artifacts, using reference counting and a retrieval blocklist, and also
  unlearns model parameters.
- [25] starts from diagnosed faulty memories and selectively replays affected steps to recover a
  failed task.

Retract starts from a source and works across agents and stores. It adds the operational protocol
(guarded reads, a BLOCK transaction, holds, journaled resume) and signed receipts with separate
verification dimensions.

**What we compared, inside our own testbed (not against the authors' implementations):**

- **A remove-only baseline modeled on the memory side of [4]** (6.7).
- **A refetch repair** that re-runs retrieval for affected records (6.6). This is close to the
  broader recomputation that [25] reports lowers recurrence compared with selective replay.

The full comparison is in [docs/RELATED_WORK.md](RELATED_WORK.md).

## 3. System

### 3.1 Lineage capture

Records are written through the SDK:

- **Primary records:** `rt.add(content, sources=[...])` writes a record owned by its sources (for
  example `user:u_priya`, `doc:d30`, `tool:crm_sync`).
- **Derived records:** functions registered with `@rt.derives(op, agent=..., tier=...)` write
  records whose parent edges are their declared `inputs=`.
- **Step read-sets:** inside `with rt.step(agent, tier):`, every record returned by a guarded read
  also becomes a parent of every write in the step. This over-approximates on purpose: if an agent
  read something, we assume it could have influenced the write.
- **Query logs (opt-in):** a derive registered with `refetch=True` also stores its step's guarded
  searches (query, tier, k and the ids returned). The log is purged with the record, because a
  query can name a person.

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
   - `delete` mode deletes the whole closure; `quarantine` mode hides it;
   - in `auto` mode, other records are re-derived when their registered function is replayable, and
     quarantined otherwise. Re-derivation has two forms:
     - **Replay** (default) calls the function on the record's old inputs minus the invalid ones.
     - **Refetch** (derives registered with `refetch=True`) re-runs each logged search whose results
       fed a now-invalid input, and uses the fresh results in that input's place. The record is
       rebuilt from what retrieval returns today.
3. **Post-repair check:** each replacement stays held until a runtime check finds no *forbidden
   fact* in it, that is, a retracted sentence that no valid input supports. Either of two rules
   flags it:
   - **Similarity:** a normalized substring or embedding match at a frozen threshold, unless an
     input holds a matching sentence too.
   - **Novel tokens:** the retracted sentence's distinctive tokens (names, codes, numbers, content
     words) that appear in no input. A replacement sentence carrying 2 of them, or all of them if
     there are fewer, is flagged.

   A replacement that fails the check is quarantined.
4. **PURGE:** deleted and superseded items are removed from their stores, together with their
   content and query logs.
5. **VERIFY:**
   - **Structural:** no readable record carries the source, purged ids are absent from the
     adapters, and the orphan scan is clean.
   - **Retrieval residue:** probes generated locally from the retracted text run through guarded
     and raw search, judged by the same two rules. They are held in memory only.

The operation journal and a durable lease lock make every step resumable: `rt.resume(op_id)`
continues a failed or crashed operation without repeating finished actions or duplicating writes. A
refetched replacement that was journaled before a crash keeps the inputs it was built from.

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
- a true refund policy (RP-30, 30 days) and a false one (RP-90, 90 days);
- for wrong_user only: 3 records written by a CRM sync tool, each assigning a customer a wrong
  billing contact.

Every fact carries a unique marker present in its text and in both of its paraphrases.

### 4.2 Scenarios

- **priya:** retract `user:u_priya` after 20 tickets. Questions: "What do we know about {name}?"
- **poisoned_doc:** retract the false policy `doc:d30`. Questions: "{name} bought from us 60 days
  ago. Can {name} still get a refund?" The policy decides the answer: RP-30 says no, RP-90 says yes.
- **wrong_user** (the wrong-user fault type of [25]):
  - Retract `tool:crm_sync`, whose 3 records gave 3 customers someone else's billing contact.
  - Questions: "What do we know about {name}?" Every customer's own facts must survive.

### 4.3 Configs (paired by seed)

| config | what happens |
|---|---|
| clean | the target source is never ingested: the reference world |
| unretracted | the target is ingested and never removed (each retraction run's pre-retraction state) |
| naive_delete | the source's own primary items are deleted from their stores, bypassing Retract |
| refcount_remove | remove-only baseline modeled on [4]: delete the source's records, then every derived record whose parents are all deleted, repeatedly; shared records stay as they are |
| delete | `rt.retract(mode="delete")` |
| auto | `rt.retract(mode="auto")`, repairing by replay |
| refetch | `auto`, with the testbed's derives registered `refetch=True` |
| quarantine | `rt.retract(mode="quarantine")` |
| auto_leak | auto, while the fake LLM re-emits one target fact verbatim during repair |
| auto_paraphrase | auto, while it re-emits a paraphrase of that fact |

### 4.4 Seeds, LLM and oracle

- **Seeds:** a seed changes the code words and templates of the generated personas and docs, which
  customers the CRM sync hits, and the ticket order.
- **Dev seeds 0–9** were used only to tune the similarity threshold and the novel-token count.
  **Test seeds 10–39** produced every reported result.
- **Pairing check:** every config of a seed sees the same data, order and questions. The runner
  fails unless all retraction runs of a seed start from an identical world.
- **LLM:** FakeLLM, which is deterministic and offline. Its summaries concatenate their inputs and
  its answers are extractive. This makes residue propagation maximal and utility insensitive, and
  it can never introduce words of its own (see section 7).
- **Oracle:** an independent fact-level oracle, which the SDK never sees, decides whether a text
  contains a fact.

### 4.5 Metrics (per trial)

- **Structural residue:** physical items that are not quarantined and carry a target fact.
- **Guarded retrieval residue:** probes with a target-carrying hit through guarded search. Probes
  are each target fact's text and both paraphrases, run on every tier with k=5.
- **Answer contamination:** the share of the 20 answers containing a target fact.
- **Utility:** the share of scored answers containing an expected fact:
  - priya: the persona's own facts, with Priya's question excluded;
  - wrong_user: the persona's own facts;
  - poisoned_doc: RP-30.
- **Record preservation:** readable records after / before. "Readable" also requires the item to be
  present in its store.
- **Honest-fact retention:** non-target fact mentions in readable derived records, after / before.
  It can exceed 1 when a repair brings in content that the false source had displaced.
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
make experiments              # FakeLLM, 30 test seeds, plus the serial cost benchmark (about 25 min)
```

| Output | File |
|---|---|
| Per-trial rows | `experiments/results/fake/trials.jsonl` |
| Intervals | `experiments/results/fake/summary.json` and `summary.md` |
| Cost | `experiments/results/fake/cost.json` |
| Threshold sweep (similarity rule alone) | `experiments/results/tuning.json` |
| Novel-token sweep | `experiments/results/tuning_novel.json` |

`trials.jsonl` and `summary.*` are deterministic. Timings in `cost.*` vary from run to run.

## 6. Results (FakeLLM, test seeds 10–39, n = 30 per cell)

Source: `experiments/results/fake/summary.json`, except where noted.

### 6.1 Residue

Structural residue / guarded retrieval residue, trials with any, per scenario:

| config | priya | poisoned_doc | wrong_user |
|---|---|---|---|
| clean | 0/30 · 0/30 | 0/30 · 0/30 | 0/30 · 0/30 |
| unretracted | 30/30 · 30/30 | 30/30 · 30/30 | 30/30 · 30/30 |
| naive_delete | 30/30 · 30/30 | 30/30 · 30/30 | 29/30 · 29/30 |
| refcount_remove | 30/30 · 30/30 | 30/30 · 30/30 | 29/30 · 29/30 |
| delete, auto, refetch, quarantine | 0/30 · 0/30 | 0/30 · 0/30 | 0/30 · 0/30 |
| auto_leak, auto_paraphrase | 0/30 · 0/30 | 0/30 · 0/30 | 0/30 · 0/30 |

- **Upper bounds:**
  - every 0/30 cell: Wilson 95% [0.000, 0.114], exact one-sided Clopper–Pearson 95% upper bound
    0.095;
  - 30/30: Wilson [0.886, 1.000];
  - 29/30: Wilson [0.833, 0.994]. In one seed the misrouted records were never copied anywhere, so
    deleting them was enough.
- **Answer contamination** before retraction, unchanged by naive delete and refcount_remove:
  - poisoned_doc: 0.993 [0.983, 1.000];
  - wrong_user: 0.120 [0.105, 0.133];
  - priya: 0.050 (her own question).

  Every retraction mode brings it to 0.000.
- **Quarantine leaves content in the stores.** Raw search still finds the quarantined items in
  30/30 trials of every scenario, as L2 states: they are hidden from guarded reads, not purged.

### 6.2 What each mode keeps

| | record preservation | honest-fact retention | honest mentions vs clean (paired) |
|---|---|---|---|
| poisoned_doc, auto (replay) | 0.992 [0.992, 0.992] | 1.000 [1.000, 1.000] | −54.5 [−55.8, −53.1] |
| poisoned_doc, refetch | 0.992 [0.992, 0.992] | 1.238 [1.232, 1.245] | −1.1 [−2.1, −0.3] |
| poisoned_doc, delete / quarantine | 0.581 [0.572, 0.589] | 0.115 [0.095, 0.134] | −252.9 [−257.2, −248.7] |
| priya, auto (replay) | 0.985 [0.985, 0.985] | 1.000 [1.000, 1.000] | −4.0 [−4.6, −3.1] |
| priya, refetch | 0.985 [0.985, 0.985] | 1.020 [1.019, 1.022] | +1.5 [0.9, 2.2] |
| priya, delete / quarantine | 0.961 [0.960, 0.962] | 0.969 [0.968, 0.970] | −12.2 [−13.0, −11.4] |
| wrong_user, auto (replay) | 0.977 [0.977, 0.977] | 1.000 [1.000, 1.000] | −8.6 [−9.7, −7.4] |
| wrong_user, refetch | 0.977 [0.977, 0.977] | 1.033 [1.028, 1.037] | +0.1 [−0.1, 0.3] |
| wrong_user, delete / quarantine | 0.912 [0.902, 0.921] | 0.876 [0.856, 0.896] | −41.5 [−47.8, −35.3] |

The affected closures averaged 5.1 (priya), 54.5 [53.4, 55.7] (poisoned_doc) and 11.8 [10.5, 13.0]
(wrong_user) records. Deleting or quarantining all of them removes much of the derived memory.
Re-derivation keeps every record that had other valid inputs, rebuilding them with 3.1, 53.5 and
8.8 LLM calls per trial.

### 6.3 Stages of the false policy (poisoned_doc, auto runs)

1. **Entered memory:** once (the document itself).
2. **Retrieved or promoted:** into 53.5 [52.4, 54.7] derived records across the support, research
   and planning memories.
3. **Influenced answers:** 19.87 [19.67, 20.00] of 20.
4. **Gone after retraction:** 30/30 trials, with no contaminated answer, no readable record and no
   unquarantined item.

### 6.4 Runtime post-repair check under injection

Each cell counts replacements: the oracle's verdict vs the runtime check's (both rules on).

| config | priya TP / FN / TN | poisoned_doc TP / FN / TN | wrong_user TP / FN / TN | outcome |
|---|---|---|---|---|
| auto | 0 / 0 / 92 | 0 / 0 / 1605 | 0 / 0 / 263 | completed 30 / 30 / 30 |
| refetch | 0 / 0 / 92 | 0 / 0 / 1605 | 0 / 0 / 263 | completed 30 / 30 / 30 |
| auto_leak | 91 / 0 / 0 | 1098 / 0 / 0 | 256 / 0 / 0 | contained 30 / 30 / 29 |
| auto_paraphrase | 91 / 0 / 0 | 1098 / 0 / 0 | 256 / 0 / 0 | contained 30 / 30 / 29 |

There were no false positives in any cell. Trials with at least one FN: 0/30 in every cell.

- **Paraphrased leaks are now caught.**
  - With the similarity rule alone (section 6.5, *m* = 0), every paraphrased replacement was a
    false negative. Those operations reported `completed` with retrieval residue `pass`, while
    the oracle found residue in 30/30 trials.
  - With the novel-token rule they are quarantined, and the operation reports `contained`. The one
    wrong_user trial that ended `completed` had no replacements at all: its misrouted records were
    never copied.
- **No false alarms here, but FakeLLM cannot test for them.** Its summaries never add words, so an
  honest replacement can never carry a novel token. The only false-alarm evidence is from a hosted
  LLM (6.11): 0 of 54 replacements flagged.
- **Cascade cost of containment:** a quarantined replacement is never used to rebuild its children,
  so under injection only 36.6 of the 53.5 re-derivations on poisoned_doc happen.

### 6.5 Tuning (dev seeds 0–9)

**Similarity threshold, with the novel-token rule off** (`experiments/results/tuning.json`, Nov 12,
priya and poisoned_doc):

| threshold | TP | FN | FP | TN |
|---|---|---|---|---|
| 0.40 | 49 | 1073 | 0 | 561 |
| 0.45 | 42 | 1080 | 0 | 561 |
| 0.50 | 26 | 1096 | 0 | 561 |
| 0.55–0.70 | 30 | 1092 | 0 | 561 |
| 0.75–0.95 | 393 | 561 | 0 | 561 |

- **Rule, fixed before the sweep:** the lowest mean per-trial (FN + FP) / replacements, with ties
  going to the lower threshold. It picked 0.75.
- **The sweep is not monotone.** Below 0.75 even verbatim leaks pass. A low threshold makes the check
  consider a retracted sentence "supported" by a look-alike honest input (RP-30 for RP-90), so the
  exemption swallows real leaks.
- **No threshold catches paraphrases:** all 561 paraphrase replacements are FN at every threshold.

**Novel-token count *m* at threshold 0.75** (`experiments/results/tuning_novel.json`, all three
scenarios):

| *m* | TP | FN | FP | TN |
|---|---|---|---|---|
| 0 (rule off) | 486 | 657 | 0 | 657 |
| 1 | 972 | 0 | 0 | 657 |
| 2 | 972 | 0 | 0 | 657 |
| 3 | 929 | 44 | 0 | 657 |
| 4 | 882 | 92 | 0 | 657 |

- **Rule, fixed before the sweep:** the lowest error, with ties going to the larger *m*, because
  false alarms are unmeasurable on FakeLLM. It picked 2, now frozen as the SDK default.
- **Why the novel-token rule does not suffer from look-alikes:** RP-90's only novel token is "90",
  which RP-30 does not contain.

### 6.6 Refetch vs replay: does repair reach the clean world?

Replay rebuilds a record from its old inputs minus the retracted one. In the clean world, though,
the agent's search would have returned *another* document in that slot. Refetch re-runs the search.

| honest mentions vs clean (paired) | replay (auto) | refetch | refetch − replay |
|---|---|---|---|
| poisoned_doc | −54.5 [−55.8, −53.1] | −1.1 [−2.1, −0.3] | +53.4 [52.3, 54.6] |
| priya | −4.0 [−4.6, −3.1] | +1.5 [0.9, 2.2] | +5.5 [5.1, 5.9] |
| wrong_user | −8.6 [−9.7, −7.4] | +0.1 [−0.1, 0.3] | +8.7 [7.4, 9.9] |

- **Refetch closes 98% of the gap on poisoned_doc**, with the same number of LLM calls. The extra
  searches are local and free.
- **It also restores answer quality.** On wrong_user, replay lowers utility by 0.017
  [0.007, 0.028] versus clean, because briefs rebuilt from fewer inputs miss facts the questions
  need. Refetch's difference is −0.002 [−0.005, 0.000], and refetch − replay is +0.015
  [0.005, 0.027].
- **It does not land exactly on the clean world.** On priya it overshoots by 1.5 mentions, and on
  poisoned_doc it stays 1.1 short:
  - Records of the closure that are not yet repaired are held, so a re-run search cannot see them
    and returns something else instead (a documented shortcut).
  - Repairs keep declared inputs only, not step read-sets.

### 6.7 A remove-only baseline in the style of [4]

`refcount_remove` deletes the source's records, then every derived record whose parents were all
deleted, until nothing changes. Records that still have a retained parent are kept, unchanged.

- **It removed exactly the same records as naive deletion in all 90 trials:** 2 per trial on
  priya, 1 on poisoned_doc and 3 on wrong_user. No derived record ever had *all* of its parents in
  the forget set. Every brief, note and plan mixes the target with other sources, so reference
  counts never reach zero.
- **Its residue, answer contamination and utility therefore equal naive deletion's** (6.1): 30/30
  residue on priya and poisoned_doc, 29/30 on wrong_user.
- **Why it matters:** where memory mixes sources, removing only exclusively dependent artifacts
  leaves every shared artifact holding the retracted content. That is the case re-derivation exists
  for.
- **Scope:** this models the memory side of [4] only, in our testbed. [4] also unlearns model
  parameters, which Retract does not do.

### 6.8 wrong_user: a misrouted CRM sync

The sync gave 3 customers someone else's billing contact. Before retraction, 0.120 [0.105, 0.133]
of answers repeated a wrong contact, in 29/30 trials.

- **Every retraction mode removed all of them** (0/30 residue) and kept each customer's own facts.
  Retraction is by source, so only the sync's records and their descendants were touched.
- **Utility per mode:**

  | clean | replay | refetch | delete / quarantine |
  |---|---|---|---|
  | 0.968 | 0.952 | 0.967 | 0.850 [0.828, 0.872] |

  Delete and quarantine throw away whole briefs about the affected customers, so their answers
  lose the customers' real facts too.

### 6.9 Over-approximation and over-fetch

- **Closure over-approximation was 0.000 in every mode and scenario.** In this testbed every step's
  read-set equals its declared inputs, so the read-set adds no extra parents. Over-approximation
  appears in the M1 slice instead, where a reminder about Arjun is tied to Priya through a step that
  read her summary. The testbed does not measure it.
- **Over-fetch shortfall under quarantine:**
  - On poisoned_doc, quarantine leaves 0.436 [0.371, 0.498] of guarded reads short, and so do
    auto_leak (0.436) and auto_paraphrase (0.447), which now end contained. 54 quarantined records crowd
    the tiers past the k×4 over-fetch.
  - Replay, refetch, delete and clean have no short reads. priya has none in any mode, and
    wrong_user almost none (0.002).
  - On FakeLLM, utility is unchanged.

### 6.10 Cost (serial, auto, test seeds 10–14; `experiments/results/fake/cost.json`)

| | priya | poisoned_doc |
|---|---|---|
| write, Retract vs raw adapter (µs, median) | 5161 vs 126 (39.2×) | 5356 vs 128 (42.1×) |
| guarded vs raw read (µs, median) | 1387 vs 728 (1.9×) | 1453 vs 775 (1.9×) |
| retraction op (s, median) | 0.32 | 1.44 |
| … re-derivation / probes / rest (s) | 0.000 / 0.18 / 0.14 | 0.000 / 0.09 / 1.35 |
| LLM calls in the op | 4 | 54 |

- **Ratios** are medians of the per-seed ratios.
- **Writes:** the write overhead is mostly durable SQLite commits on the crash-safe write path. An
  earlier run on the same machine measured about 55×; timings vary between runs.
- **The op:** journal, BLOCK, purge and structural checks dominate it.
- **Re-derivation:** it is about zero only because the fake LLM is local. A hosted model adds its
  latency for each of the 4 or 54 calls.
- **Machine:** one Windows 11 laptop, Python 3.12.

### 6.11 Hosted LLM (Groq `openai/gpt-oss-120b`, n = 1; `experiments/results/groq/`)

**Produced by the Nov 12 code**, before the M9 changes. It has no novel-token rule, no refetch, no
remove-only baseline and no wrong_user scenario. `make experiments-groq-replay` reproduces these
files only at that code (commit `3a9eadf`). Under the current code, the new configs send prompts
the cache does not hold, so a live re-run is needed first.

**Setup.** Test seed 10 only, with clean, naive_delete, delete, auto and quarantine. The injection
configs are FakeLLM-only. Prompts are frozen (`v1`) and temperature is 0. All 423 responses are
cached in `experiments/cache/groq.jsonl` (233,946 tokens in total, 553 per request on average).

**n = 1 is an illustration, not an estimate.** With one trial per cell, a binary outcome of 0/1 has
a Wilson 95% interval of [0.000, 0.793]. The table shows what happened on one seed; it does not give
rates.

| poisoned_doc, seed 10 | contaminated answers | useful answers (true policy RP-30) | readable records | honest mentions |
|---|---|---|---|---|
| clean | 0/20 | 20/20 | 129 | 279 |
| unretracted | 20/20 | 1/20 | 130 | 227 |
| naive_delete | 20/20 | 1/20 | 129 | 227 |
| delete | 0/20 | 20/20 | 78 | 36 |
| auto | 0/20 | 20/20 | 129 | 227 |
| quarantine | 0/20 | 20/20 | 78 | 36 |

- **Utility separates the configs.** FakeLLM's extractive answers quoted both policies, so utility
  was 1.000 everywhere. The hosted model answers the question: with the false policy in memory,
  20/20 answers grant the refund and only 1/20 cites RP-30. Naive deletion changes nothing. Every
  Retract mode restores 20/20 correct answers.
- **The four stages hold on a real model.** The false policy entered memory once, was promoted into
  51 derived records, influenced 20/20 answers, and was gone after retraction.
- **The same trade-offs as FakeLLM:** zero structural and guarded retrieval residue for every mode;
  auto kept 99.2% of records and all honest mentions; delete and quarantine kept 60.0% of records
  and 15.9% of honest mentions; quarantine left 60% of guarded reads short.
- **Novel-token rule on real output (re-checked Nov 17, offline).** We replayed seed 10's auto runs
  under the current code. Repair sends the same prompts, so no request left the cache. All 54
  Groq-written replacements (3 priya, 51 poisoned_doc) were released, with 0 false alarms and both
  operations `completed`. This is one seed and no injection, so it is evidence, not a rate.
- **Re-derivation vs clean:** the gap is −52 honest mentions on poisoned_doc, close to FakeLLM's
  −54.5. On priya, auto had 5 *more* honest mentions than clean (282 vs 277), because hosted
  summaries differ in what they repeat. One seed cannot tell whether that is systematic.
- **Closure over-approximation was still 0.** With the `v1` prompt ("keep every distinct fact"),
  every record in the closure still contained the target fact.
- **priya** matches FakeLLM: 1/20 contaminated answers before retraction (Priya's own question) and
  0/20 after, with utility 19/19 throughout (Priya's own question is not scored).

**Budget for the re-run.**

- **Size:** `--estimate` counts about 548 distinct requests per seed for all three scenarios: 127
  priya, 294 poisoned_doc and 126 wrong_user. Refetch adds almost nothing, because it rebuilds the
  prompts the clean world already sent, which come from the cache.
- **Tokens:** at 553 tokens per request, that is about 300K tokens per seed. The free plan allows
  200K per day.
- **Price:** at Groq's listed prices (Nov 17), one seed costs about $0.11 on `openai/gpt-oss-120b`
  ($0.15 / $0.60 per million input / output tokens). On `qwen/qwen3.8-27b` (a preview model, a
  different family) it is about $0.72, assuming similar token counts.

### 6.12 Scale (synthetic, 1K–100K records, two runs; `experiments/results/scale.json`)

**Setup** (`python -m experiments.scale --pg <url>`):
- **World:** U users, each owning 4 records, plus one team note per pair of users derived from both users' records. N = 4.5 U.
- **Closure:** retracting a user deletes their 4 records and re-derives one note, so the closure is 5 records at every size; only the size of memory changes.
- **Adapter:** `SimpleVectorStore` with a 32-dimension test embedder. Its search is a linear scan, where a real vector store would use an index.
- **Machine:** one Windows 11 laptop, Python 3.12.10, with Postgres 17.10 running locally.
- **Runs:** two full runs, plus a re-run of the SQLite 100K cell after a fix (run 2b, below). One sample per cell per run, so treat these as indicative.

Run 2, with the SQLite 100K column from run 2b. Cells are SQLite / Postgres.

| | 1K records | 10K | 100K |
|---|---|---|---|
| write latency, median of the last 500 writes (ms) | 6.5 / 5.5 | 5.7 / 6.1 | 6.6 / 3.3 |
| raw adapter search (ms, median) | 4.1 / 6.2 | 35.0 / 53.5 | 593 / 420 |
| one retraction (s) | 0.89 / 1.08 | 6.33 / 7.74 | 66.3 / 53.7 |
| … residue probes (s) | 0.79 / 0.83 | 5.96 / 7.48 | 54.4 / 52.9 |
| … everything else: BLOCK, journal, purge, structural checks (s) | 0.09 / 0.25 | 0.37 / 0.25 | 11.9 / 0.81 |
| 4 disjoint retractions, one after another vs at once (s) | 6.5 vs 2.7 / 6.8 vs 3.1 | 24.8 vs 21.6 / 25.9 vs 26.1 | see standby note vs 233 / 215 vs 227 |

What held in both runs:
- **Writes do not slow down as memory grows.** The median write stayed at 5.5–10 ms on SQLite and 2–6 ms on Postgres from 1K to 100K records. Which backend wrote faster did not hold up: Postgres was 2.5–5× faster in run 1, but 0.9–2.6× in run 2.
- **Retraction does slow down, although the closure stays at 5 records.** One retraction took about 1 s at 1K records and 54–68 s at 100K in both runs. Cost grows with the size of memory, not of the closure. Two parts grow:
  - **Residue probes** go from about 0.8 s to 53–56 s. Most of that is the toy adapter's linear scan: raw search grows from about 5 ms to 0.4–0.6 s. The rest is the post-repair check on each hit, which compares it against every independent primary record in memory.
  - **Retract's own work** goes from 0.1–0.25 s to 0.8–1.1 s on Postgres, and to 12–13 s on SQLite. VERIFY's global checks scan the whole store: the orphan scan lists every adapter id, and the unreadable-parent check joins all records.
  - **Next step:** scope those checks to the closure and its neighbors, and index the support lookup, so cost tracks the closure.
- **Parallel retractions help only when waiting dominates.** Each re-derivation waits 1 s, standing in for a hosted LLM call.
  - At 1K, 4 retractions with disjoint closures finished 1.8–2.4× faster at once than one after another, on both backends. Before M10 the other three would have been refused.
  - At 10K and 100K there was no gain (0.76–1.2×). Each op's probe work is CPU-bound Python, and threads in one process share the GIL. Separate processes would overlap that work too, but the in-memory adapter cannot be shared across processes, so that is not measured.
- **Guarded reads:** the guard added 1.5–6.4 ms per search at 1K. At larger sizes the variance of the raw scan swamps the difference (−164 to +165 ms around a 0.4–0.6 s scan at 100K), so this benchmark does not resolve the guard's cost there.

Found along the way:
- **A race in VERIFY, seen only at 100K records** (ERRORS 68).
  - In run 2, 2 of the 4 parallel retractions on SQLite at 100K failed with `no_unrelated_orphans`.
  - VERIFY's orphan scan is a snapshot. Another op's replacement, pending in the scan, was activated before the check read it again, so it was counted as an orphan.
  - The scan's duration grows with memory, so the window only opened at 100K. Run 1 had the same bug, but its harness counted only the threads that returned, so it reported every retraction as completed.
  - VERIFY now re-checks each candidate against current state. In run 2b, with the fix, all 8 retractions completed.
- **A false alarm on templated text** (ERRORS 66).
  - In the first version of this world every fact used one template, so unrelated sentences already scored 0.714 against the 0.75 threshold.
  - A hash collision in the 256-dimension test embedder pushed one sentence to 0.756, and the post-repair check quarantined a clean replacement.
  - The similarity rule has little margin on near-duplicate text. The benchmark text was changed so that it measures cost. This was seen with the offline test embedder; a real embedding model was not tried.
- **Standby caveat.** Windows entered Modern Standby several times, even with the app keeping the machine awake (that hold prevents idle sleep only). The system event log gives the windows:
  - In runs 1 and 2 standby fell inside the 100K builds, whose wall time is not reported. Each write median comes from the last 500 writes, after the standby periods.
  - In run 2b, 8,691 s of standby fell inside the 4 serial retractions (measured 8,986 s), so that cell is not reported. Net of standby it would be about 295 s, close to run 2's 289 s. The single retraction and the parallel phase had no standby.

## 7. Negative results and limitations

### Negative results

1. **Replay is not the counterfactual clean world** (6.2, 6.6). It keeps 54.5 fewer honest fact
   mentions than clean on poisoned_doc, and on wrong_user it costs answer quality. Refetch closes
   most of the gap, but it needs query logging and does not hit the clean world exactly.
2. **Remove-only deletion fails where memory mixes sources** (6.7). Reference counting removed
   nothing beyond the originals in 90 of 90 trials.
3. **Containment cascades.** One flagged replacement keeps all of its descendants out of
   re-derivation (6.4). Under injection, auto keeps only as much as delete.
4. **Over-fetch is insufficient under heavy quarantine** (6.9).
5. **Repairs drop read-set edges.** A replacement's parents are its declared inputs only, so
   over-approximated lineage from step read-sets disappears after a repair. Seen in the demo: after
   one repair, a reminder no longer carries Priya's label.
6. **The similarity rule alone never catches paraphrases** (6.5). The novel-token rule fixes this
   for paraphrases that keep identifying details, with the limits below.
7. **Retraction cost grows with the size of memory, not of the closure** (6.12). A 5-record
   retraction took about 1 s at 1K records and 54–68 s at 100K, because verification and residue
   probes scan the whole store.
8. **The similarity rule has little margin on templated text** (6.12). Near-duplicate synthetic
   facts drew a false alarm through one hash collision in the test embedder.
9. **A concurrency race showed only at 100K records** (6.12, ERRORS 68). The small-scale
   concurrency tests and the first benchmark run did not catch it. It is fixed, with a regression
   test, but other races that need scale to show may remain.

### Threats to validity

- **The novel-token rule and the oracle share an assumption.** The oracle detects a fact by its
  unique marker, and every paraphrase in the data keeps that marker. The novel-token rule flags
  sentences carrying the retracted text's rare tokens, which include the marker. So the 100% catch
  rate partly follows from how the data was built. Paraphrases that drop every identifying detail
  ("a customer prefers a messaging app") are untested; neither the rule nor the oracle can see them.
- **False alarms are barely measured.** FakeLLM cannot produce them (6.4). On real output we have 0
  of 54 replacements on one seed (6.11). A model that adds rare words, such as names or numbers,
  near retracted topics could trigger the rule.
- **FakeLLM:** it concatenates when it summarizes, so every derived record keeps every input fact.
  Residue propagation is therefore maximal, closure over-approximation is zero, and utility barely
  separates the configs. Wrong_user is the exception, because there replay drops whole facts. The
  Groq run (6.11) shows utility separating the configs on poisoned_doc, but it is one model on one
  seed.
- **Synthetic data:** the facts and the oracle (unique code-word markers) are synthetic.
- **Baselines are our re-implementations in our testbed,** not the authors' code ([4], [25]).
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
