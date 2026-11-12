# M8 results (Groq openai/gpt-oss-120b, cached responses, prompts v1, 1 paired seeds: [10])

Binary columns: trials with the event, k/n, with a two-sided 95% Wilson interval.
Continuous columns: mean over trials, with a 95% trial-level bootstrap interval.

## poisoned_doc

| config | structural residue | guarded retrieval residue | contaminated answers | utility | record preservation | honest-fact retention | over-retraction | short guarded reads |
|---|---|---|---|---|---|---|---|---|
| clean | 0/1 [0.000, 0.793] | 0/1 [0.000, 0.793] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | n/a | n/a | n/a | 0.000 [0.000, 0.000] |
| unretracted | 1/1 [0.207, 1.000] | 1/1 [0.207, 1.000] | 1.000 [1.000, 1.000] | 0.050 [0.050, 0.050] | n/a | n/a | n/a | 0.000 [0.000, 0.000] |
| naive_delete | 1/1 [0.207, 1.000] | 1/1 [0.207, 1.000] | 1.000 [1.000, 1.000] | 0.050 [0.050, 0.050] | 0.992 [0.992, 0.992] | 1.000 [1.000, 1.000] | n/a | 0.000 [0.000, 0.000] |
| delete | 0/1 [0.000, 0.793] | 0/1 [0.000, 0.793] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.600 [0.600, 0.600] | 0.159 [0.159, 0.159] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| auto | 0/1 [0.000, 0.793] | 0/1 [0.000, 0.793] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.992 [0.992, 0.992] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| quarantine | 0/1 [0.000, 0.793] | 0/1 [0.000, 0.793] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.600 [0.600, 0.600] | 0.159 [0.159, 0.159] | 0.000 [0.000, 0.000] | 0.600 [0.600, 0.600] |

Runtime post-repair check vs the oracle (poisoned_doc, replacements per config):

| config | TP | FP | FN | TN | trials with FN | trials with FP | FN rate | outcomes |
|---|---|---|---|---|---|---|---|---|
| auto | 0 | 0 | 0 | 51 | 0/1 [0.000, 0.793] | 0/1 [0.000, 0.793] | n/a | {'completed': 1} |

## priya

| config | structural residue | guarded retrieval residue | contaminated answers | utility | record preservation | honest-fact retention | over-retraction | short guarded reads |
|---|---|---|---|---|---|---|---|---|
| clean | 0/1 [0.000, 0.793] | 0/1 [0.000, 0.793] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | n/a | n/a | n/a | 0.000 [0.000, 0.000] |
| unretracted | 1/1 [0.207, 1.000] | 1/1 [0.207, 1.000] | 0.050 [0.050, 0.050] | 1.000 [1.000, 1.000] | n/a | n/a | n/a | 0.000 [0.000, 0.000] |
| naive_delete | 1/1 [0.207, 1.000] | 1/1 [0.207, 1.000] | 0.050 [0.050, 0.050] | 1.000 [1.000, 1.000] | 0.985 [0.985, 0.985] | 1.000 [1.000, 1.000] | n/a | 0.000 [0.000, 0.000] |
| delete | 0/1 [0.000, 0.793] | 0/1 [0.000, 0.793] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.962 [0.962, 0.962] | 0.968 [0.968, 0.968] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| auto | 0/1 [0.000, 0.793] | 0/1 [0.000, 0.793] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.985 [0.985, 0.985] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| quarantine | 0/1 [0.000, 0.793] | 0/1 [0.000, 0.793] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.962 [0.962, 0.962] | 0.968 [0.968, 0.968] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |

Runtime post-repair check vs the oracle (priya, replacements per config):

| config | TP | FP | FN | TN | trials with FN | trials with FP | FN rate | outcomes |
|---|---|---|---|---|---|---|---|---|
| auto | 0 | 0 | 0 | 3 | 0/1 [0.000, 0.793] | 0/1 [0.000, 0.793] | n/a | {'completed': 1} |

