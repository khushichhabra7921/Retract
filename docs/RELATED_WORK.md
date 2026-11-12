# Related work: what Retract shares with [4] and [25], and what it adds

Nov 12, 2026. Based on reading the arXiv HTML versions of both papers: [4] v2 (Feb 28, 2026) and [25]
v2 (Oct 22, 2026). For [25] we read the main text; its last ~40K characters, the appendices, were
not read. Descriptions below are our paraphrase. Where we could not find something in a paper, we
say "not found" rather than assume it is absent. We have not run either method, so there is no
head-to-head comparison.

## [4] Agentic Unlearning: Synchronized Backflow Unlearning (SBU)

*Wang, Wang, Wang, Cong, Yu, Yin, Han, Wei. "Agentic Unlearning: When LLM Agent Meets Machine
Unlearning," arXiv:2602.17692.*

**What it does.** SBU removes designated information from both of an agent's pathways: its model
parameters and its persistent memory. It orders the two so that neither recontaminates the other.

- **Memory side:**
  - Memory is a dependency graph. Raw episodic memories are the leaves; summaries, reflections and
    knowledge-graph entities are derived from them, and edges record derivation.
  - A deletion request adds the targets to a persistent blocklist checked at retrieval, then
    traverses the graph to the dependent artifacts.
  - Reference counts separate artifacts that depend only on forgotten memories from artifacts that
    are shared with retained ones. Artifacts whose count drops to zero are removed.
- **Parameter side:** an unlearning objective runs afterwards, on the sanitized retrieval context.
- **Audit:** deletions are logged in a tamper-evident, hash-chained write-ahead log.
- **Evaluation:** a single medical QA agent with one memory store, on MedQA, MedMCQA and MedReason.
  Measures are membership-inference scores, forget-set accuracy and retrieval hit rate.
- **Stated limitation:** dependency tracking may not capture cross-agent information flow in shared
  knowledge graphs.

**Shared with Retract:**

- a derivation graph over memory and a closure computed from the deleted items;
- blocking at retrieval (their blocklist, our guard plus tombstones);
- a hash-chained audit trail.

**Where Retract differs:**

- **Repair of shared artifacts.** SBU removes artifacts and preserves shared ones through reference
  counting. We did not find a step that regenerates a shared artifact without the forgotten
  content. Retract's `auto` mode re-derives each affected record from its remaining valid inputs,
  checks the replacement before releasing it, and purges the old version. The modes `delete`
  (closer to removal) and `quarantine` are available for comparison.
- **Scope.** Multi-agent and multi-store: labels propagate across agents, tiers and separate
  adapters, and step read-sets capture influence through reads. This is the setting [4] names as
  its limitation.
- **Operational protocol.**
  - A crash-safe write path that rejects writes racing a retraction.
  - One BLOCK transaction with holds and a stale-plan check.
  - A durable lease journal with `resume`.
  - Adapter reconciliation through write tokens.
  - We found no equivalent in [4] beyond its log.
- **Evidence.** Each operation gets an HMAC-signed receipt that reports structural and
  retrieval-residue verification separately, plus explicit guarantee levels (L1–L5).
- **Out of scope for Retract:** parameter unlearning, which is SBU's other half. Retract makes no
  claim about model weights (L5 not established).

## [25] Dependency-Guided Rollback Repair

*Wang, Yu, Zhang, Duan, Zheng, Wu, Shi, Cai. "From Faulty Memories to Corrected Actions:
Dependency-Guided Rollback Repair for Memory-Augmented Agents," arXiv:2608.10502.*

**What it does.** The input is a failed execution plus a set of faulty memories that have already
been diagnosed; diagnosis happens upstream. The goal is to regenerate the answer and clean up the
affected persistent state while keeping unaffected work.

- **Typed graph:**
  - Built from runtime provenance: user inputs, execution steps and memory records.
  - Edges include cite, support, produce, update, supersede and derive.
  - Captured from memory reads and writes, tool calls and observations, and claims with evidence.
- **Affected set and support:**
  - The affected set is what is reachable from the faulty seeds.
  - A step is kept if at least one direct evidential predecessor lies outside the affected set (a
    deliberately lenient support check).
- **Replay:** only the unsupported steps that reach the answer are replayed, re-executing tools
  where interfaces can be reset.
- **Quarantine:** quarantined memories are excluded from retrieval and kept for audit.
- **Evaluation:**
  - 150 controlled cases with four fault types: poisoned, stale, wrong-user and summary drift.
  - 50 adapted LongMemEval-V2 cases.
  - Measures: recovery, recurrence, benign preservation, invalidation F1 and LLM calls.
  - Results: higher recovery than an LLM-judge repair baseline, with fewer calls, but higher
    recurrence, especially for stale faults.
- **Stated limitations:**
  - provenance must be complete;
  - side-effecting tools need resettable interfaces;
  - a single run per configuration;
  - selective replay trades recurrence for cost.

**Shared with Retract:**

- runtime-captured provenance;
- downstream closure from the seeds;
- preserving state that has independent support;
- replaying only what depends on the removed material;
- quarantine that hides memory from retrieval but keeps it.

The two papers also reach a parallel finding. [25] reports that selective replay leaves more
recurrence than broader recomputation. We find that re-derivation does not reproduce the
counterfactual clean world: 54.5 fewer honest fact mentions than a world that never saw the bad
document (RESULTS §7). In both cases, replaying old computation without the removed input is not
the same as redoing it from scratch.

**Where Retract differs:**

- **Trigger and goal.** Retract starts from a *source* (an owner such as a user, a document or a
  conversation) and removes everything derived from it, for privacy deletion or a revoked or poisoned
  document. It needs no diagnosis or failed run. [25] starts from diagnosed faulty memories and aims
  at task recovery. Retract does not regenerate past answers and does not replay tools; it replays
  only registered, versioned derivation functions over stored inputs.
- **Scope.** Several agents and several stores. [25] describes a single session with one memory
  store.
- **Operational protocol.** Guarded reads with holds during repair, the BLOCK transaction,
  journaled resume after crashes or failed LLM calls, and adapter reconciliation. We did not find a
  persistent journal or resume mechanism described in [25].
- **Evidence.** Signed, chained receipts with separate verification dimensions, and a runtime
  post-repair check on each replacement before release. [25] withholds invalidated nodes from the
  replay context until replacements exist; we did not find a check of the replacements themselves.
- **Evaluation focus.** Residual exposure in three dimensions (structural, retrieval, answers),
  measured by an independent oracle, including where our runtime check fails (paraphrased leaks).
  [25] measures task recovery and recurrence.

## Positioning

**Not new in Retract:**

- dependency or derivation graphs over agent memory;
- closure from deleted or faulty items;
- selective replay;
- quarantine kept for audit;
- hash-chained logs.

[4] and [25] cover these, and so do the memory systems discussed in the brief ([5], [6]).

**What Retract contributes (a prototype, not a claim of superiority):**

1. **Source-level retraction across agents and stores.** Labels propagate from owners through
   derived records in several tiers and adapters, and step read-sets capture influence through
   reads. This is the multi-agent setting [4] lists as future work.
2. **An operational protocol for doing this safely in a live system.**
   - Guarded reads.
   - A write path that rejects writes racing a retraction.
   - One BLOCK transaction with stale-plan detection.
   - Holds during repair.
   - A post-repair check before release.
   - A durable journal with resume and adapter reconciliation.
3. **Auditable outcomes.** Signed, hash-chained receipts with explicit guarantee levels, and
   separate `completed` and `contained` outcomes.
4. **A controlled evaluation of residual exposure, negative results included.**
   - Paraphrased leaks evade the runtime check.
   - Re-derivation diverges from the clean world.
   - Containment cascades.

**Not done.** We have not compared the methods empirically on a shared benchmark. A natural next
step is to run [25]'s poisoned and wrong-user fault types through the Retract testbed, and our
poisoned_doc scenario through an SBU-style remove-only baseline (close to our `delete` mode).

## Other systems in the brief (not reviewed in depth)

- **Zep [5][6]:** links derived facts to source episodes and deletes a fact only when no other
  episode supports it. Its documentation states that deleting an episode does not regenerate the
  names or summaries of nodes shared with other episodes.
- **Mem0:** delegates deletion and audit consistency to the application [4].
- **Not reviewed:** MemTrust [14] and SuperLocalMemory [24]. We read neither and make no claim
  about them.

## References

Numbering follows `docs/retract-project-brief.md`.

- [4] B. Wang et al., "Agentic Unlearning: When LLM Agent Meets Machine Unlearning,"
  arXiv:2602.17692 (v2, Feb 2026).
- [5] Zep docs, "Episode metadata projection."
- [6] Zep docs, "Deleting Data from the Graph."
- [14] "MemTrust: A Zero-Trust Architecture for Unified AI Memory System," arXiv:2601.07004.
- [24] "SuperLocalMemory 4.0: The Governed Memory Operating System for AI Agents,"
  arXiv:2608.08253.
- [25] Y. Wang et al., "From Faulty Memories to Corrected Actions: Dependency-Guided Rollback
  Repair for Memory-Augmented Agents," arXiv:2608.10502 (v2, Oct 2026).
