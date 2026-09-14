# Behavior coverage and scoring

Reviewed 2026-09-12. Artifact acceptance, observed workflow, and efficiency are separate judgments. A scenario name, an agent-authored report, or a passed artifact oracle does not prove collaboration. Counts below use actual command/collaboration items; start and completion of one item count once.

## What is measured

| Dimension | Evidence and interpretation |
|---|---|
| Correctness | Each result preserves independent artifact checks and CLI completion. Historical hidden-keyword/comment failures stay unchanged, with separate diagnoses. |
| End-to-end time | Per-attempt elapsed time, including startup, tools, coordination and retries. Thirteen equivalent-input representative attempts retain the timeout and timing variation. |
| Whole-task usage | Validated root/child linkage and per-response records; cached input and reasoning output are subsets. Missing or interrupted records make the full total UNKNOWN. Reference credits are separate from billing. |
| Unnecessary escalation/delegation | Flag choices against required judgment and useful work versus overhead. Two worker spawns on the small implementation were an observed overhead problem; the revised run used zero children. A forced probe is excluded from autonomous efficiency scoring. |
| Missed escalation | Require an unresolved capability need or a missed explicit assignment. The first forced research coordinator produced zero required child assignments and fabricated labels: workflow failure. Low's security run claimed unavailable delegation without evidence; its correct bounded answer does not itself establish that a larger model was necessary. General missed-capability frequency remains unknown. |
| Repeated work | The revised direct implementation ran one initial combined check and one justified check after a specific edit, but made two unnecessary Git inspections in a non-repository fixture. Earlier quoting repairs and mistaken expected values are retained as failures. The final policy tells agents to establish availability once and use an existing alternative. |
| Handoff failures | The initial forced research run had only root and coordinator, not the required children. The corrected Max run violated root output ownership despite correct artifacts. Corrected Medium nesting and parallel writes preserved ownership; the pricing follow-up reused its child to close a specific validation gap. |

The measurements support bounded decisions and expose regressions. They are not a population-level reliability estimate, and UNKNOWN must not be turned into zero. Add a fresh representative case when observed capability needs change; do not manufacture a success by rerunning unchanged failures.

## Coverage already executed

| Required behavior | Evidence |
|---|---|
| Small work remains on Luna | Corrected simple comparisons and `scenario-observations-ordered-gate-1.json`: actual root-only execution. |
| Small but nontrivial semantics | `current-functional-diagnostics.json`, `deceptive_difficult`: actual longest-match implementation and functional checks passed; two mistaken test expectations were corrected. The old report-keyword failure remains. This was bounded local success, not evidence that all hard tasks should stay local. |
| Direct specialist and constraints | Corrected security runs invoke actual Sol High; complete incident constraints and failed-approach evidence reach the specialist. |
| Independent writes and combined validation | `scenario-observations-representative-1.json`: two actual worker edits with separate ownership overlap, then root validates their combined invoice. |
| Conflicting shared-file work | `current-functional-1/overlapping_writes--current--attempt-1.jsonl`: root owns both exports in registry.py, avoiding competing writers. Direct runtime checks pass. Historical comment-preservation oracle failure is retained; this is safe single ownership, not a claim of simultaneous shared-file writes. |
| Dependencies and incoming evidence | `scenario-observations-ordered-gate-1.json`: current child result unblocks validation before pending archive completion. |
| Redundant cancellation | Same review plus `staged-process-check.json`: actual archive interruption and no process at after-run inspection. |
| Environmental failure | Actual connection refusal; accurate unavailable-service report; no server start or model escalation. |
| Unavailable role fallback | Actual failed fast_scan activation, then suitable Luna explorer returns evidence; full usage remains unknown for failed activation. |

Real conflicting specialist recommendations, failed-check handoff and capacity-priority evidence are recorded separately by the final controlled workflow probe. Forced workflow coverage must remain distinct from autonomous model-selection evidence.
