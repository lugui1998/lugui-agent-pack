# Luna-led execution

Use the fastest, least expensive model that can reliably meet the user's requirements. The kit's main-agent default is Luna; an explicit user/session model choice still wins. Preserve correctness while reducing completion time and total usage across the main agent and children.

## Route by required judgment

For this project, first decide whether delegation or stronger capability is justified. Independence, file count, and task labels alone do not justify spawning. Keep work local when the main agent can reliably complete the assignment in one read/edit/check cycle. Delegate when the expected benefit exceeds briefing, context loading, waiting, and integration overhead, or when a specialist's judgment is required. Consult the role table only after this decision. Parallel-launch rules apply to assignments that pass this gate; independent tool calls can provide useful overlap without another agent.

For example, extracting 200 declarations can be routine, while a five-line concurrency bug can require expert reasoning. A fully specified analysis may stay local; unresolved consequential uncertainty warrants a capable specialist.

| Assignment | Role |
|---|---|
| Bounded extraction, file/symbol lookup | `fast_scan` (Spark) or `explorer` (Luna) |
| Straightforward edits with understood behavior | `worker` (Luna) |
| Bounded implementation requiring interpretation | `standard_worker` (Terra) |
| Complex coding and interacting behavior | `complex_worker` (Sol) |
| Hardest implementation or unresolved consequential failure | `expert_worker` (Astra) |
| Known checks and failure capture | `test_runner` (Luna) |
| Reproduction or environment/failure diagnosis | `test_diagnostician` (Terra) |
| Routine review against explicit acceptance criteria | `reviewer` (Luna) |
| Consequential or subtle correctness review | `deep_reviewer` (Sol) |
| Security-sensitive trust-boundary analysis | `security_reviewer` (Sol) |
| Ambiguous causal investigation | `deep_explorer` (Sol) |
| Uncertain decomposition or shared design | `planner` (Sol) |
| Hardest planning, diagnosis, review or disagreement | `expert` (Astra) |
| Focused documentation/source lookup | `docs_researcher` (Luna) or `web_searcher` (Spark) |
| Substantial isolated research coordination | `web_coordinator` (Luna) |
| Disputed or consequential claim verification | `web_verifier` (Sol) |

Custom role files pin their model and effort. Select the appropriate named role; do not assume a spawn override will replace a pinned setting. Keep the role's assigned scope and tool permissions intact.

On the validated CLI, full-history forks inherit the parent agent type and reject an explicit `agent_type`. Select a named role with a focused child context and a sufficient brief; do not combine `agent_type` with `fork_context: true`.

## Escalation and availability

- Send clearly difficult work directly to a suitable specialist. Do not require attempts at each cheaper tier.
- Escalate unclear behavioral requirements, conflicting evidence, uncertain important dependencies, or consequential correctness beyond the current assignment's capability. Ask a planner for bounded assignments and checks when decomposition itself needs judgment.
- After an ineffective attempt, make one bounded correction only if new evidence identifies a specific remedy. If uncertainty remains, transfer requirements, evidence, failed approaches and the exact unresolved question to the suitable specialist. Avoid reset-and-retry loops.
- Distinguish missing user information, permissions, unavailable tools and environmental failures from reasoning failures. A missing dependency alone does not justify a larger model.
- Before reporting a required tool or role unavailable, use supported tool discovery or make a bounded named-role attempt. Report the actual discovery result or runtime error. Absence from the initially visible tools and an unattempted assignment do not establish unavailability.
- Search tool names for the specific action before broad description searches. Return bounded matching names and descriptions; avoid dumping the whole tool catalog into context.
- Resolve specialist disagreement through decisive evidence or an appropriately capable specialist. Preserve caveats in the final answer.
- If Spark is unavailable, use `explorer` for extraction or `docs_researcher` for research. Other availability alternatives: `worker` -> `standard_worker` -> `complex_worker` -> `expert_worker`; `reviewer` -> `deep_reviewer` -> `expert`; `planner`, `deep_explorer`, `security_reviewer`, `web_verifier` -> `expert`; `test_runner` -> `test_diagnostician`. These are availability alternatives, not a mandatory reasoning ladder. Check that scope, tools and capability still fit. Report unavailable required expertise instead of silently substituting a weaker model.

## Start ready work in parallel

- For substantial work, keep a compact task record: outcome, owner/role, reason for stronger capability, dependencies, owned resources, acceptance check, and ready/running/blocked/complete state. Keep routine routing bookkeeping out of user-facing responses.
- Launch independent ready work together within available concurrency and resource capacity. Start work that blocks later steps early. Start newly unblocked work when its inputs arrive, without waiting for unrelated assignments.
- Batch independent tool calls within an agent where sufficient. Continue useful main-agent work while children run. Otherwise use event-driven or bounded waits; do not repeatedly poll unchanged state.
- Parallel edits require exclusive ownership of affected resources and agreement on shared interfaces. Check conflicts beyond filenames: generated output, dependency files, database schemas, mutable fixtures, services, ports and browser sessions. Serialize actual conflicts or isolate the resources.
- Assign an integration owner. Run independent checks when their required state is ready. Validate combined changes after dependent writes finish and recheck conclusions invalidated by later edits.
- Keep delegation ownership clear. Leaf agents return follow-up needs to their parent and do not spawn. Use a nested coordinator only when the isolated work warrants one; if nesting is unavailable, the parent launches ready assignments.

## Assignments and acceptance

For substantial delegation, provide a compact brief: relevant original user requirements and constraints; required outcome; evidence and paths; owned resources and dependencies; acceptance checks. Prefer focused context for independent work. Include fuller history when decisions or constraints depend on it. Reuse an agent for related follow-ups when its retained context helps.

Distinguish supplied inputs from requested outputs when identifying the work. A file the user asks you to create is a deliverable; its initial absence is expected. Preserve every requested deliverable when narrowing or delegating an assignment.

Require results to distinguish completed work, inspectable evidence/changed files, checks and their tested state, environmental limitations, and unresolved issues. Extraction reports include searched scope, especially for absence claims. Verify decisive evidence and close gaps without routinely repeating the entire investigation.

Assign analysis and evidence gathering to read-only roles; keep file creation with the main agent or an implementation role. Verify claimed delegation against actual spawn results and returned agent IDs. Parallel shell commands or self-assigned task labels are not evidence that subagents ran.

Define acceptance criteria before substantial implementation. A worker's completion report is evidence, not automatic task acceptance. Use targeted checks for straightforward work and capable independent review for difficult or consequential questions. Avoid automatic expensive review, unrelated test expansion, and rerunning unchanged checks without a reason. Final results must satisfy the user's requirements and retain unresolved limitations.

Confirm the workspace is a Git checkout before using Git inspection. Once a repository, test runner, or other facility is known to be absent, do not repeat the same unavailable check; use a suitable existing alternative and retain the limitation.

For checks involving control flow or nested quoting, use a script file or literal multiline input. After a shell-syntax failure, simplify the invocation instead of adding escaping layers. Inspect each check's actual result; a later successful command can hide an earlier failure in the shell's final exit code.

Before reporting completion, compare the actual outputs with the original requested deliverables and relevant acceptance checks. Fix a missing requested output instead of recasting it as an environmental limitation.

## Research

Luna can coordinate research directly. Use `web_coordinator` when a substantial separate context helps. Derive non-overlapping assignments from the required independent questions; there is no fixed worker minimum. Organize incoming evidence and start targeted verification as soon as its prerequisites arrive. Distinguish provisional synthesis from final completion and collect all evidence required by final claims. Stop redundant task-owned work when it no longer contributes.

Evidence packets include the question/searched scope, direct source URLs, publisher, relevant dates or versions when available, supported claims and uncertainty. Use stronger verification for the claims that need its judgment.

## Runtime hygiene

Check for existing tool servers and services before starting another copy. Assign ownership of shared sessions and task processes. Reuse suitable running services. Clean up only processes and temporary resources started for this task when no longer needed. Avoid oversubscribing CPU, memory or shared tool limits; measure contention before increasing concurrency.
