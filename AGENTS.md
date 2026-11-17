# Luna-led execution

Use the fastest, least expensive model that can reliably meet the user's requirements. The kit's main-agent default is Luna; an explicit user/session model choice still wins. Use GPT-6.1 Terra for scoped code fixes, visual work and defined computer workflows, and writing grounded in supplied material. Use GPT-6.2 Sol for complex work and deeper judgment; reserve GPT-6.2 Astra for the hardest or still-unresolved consequential work. Preserve correctness while reducing completion time and total usage across the main agent and children.

## Route by required judgment

For this project, first decide whether delegation or stronger capability is justified. Independence, file count, and task labels alone do not justify spawning. Keep work local when the main agent can reliably complete the assignment in one read/edit/check cycle. Delegate when the expected benefit exceeds briefing, context loading, waiting, and integration overhead, or when a specialist's judgment is required. Consult the role table only after this decision. Parallel-launch rules apply to assignments that pass this gate; independent tool calls can provide useful overlap without another agent.

For example, extracting 200 declarations can be routine, while a five-line concurrency bug can require expert reasoning. A fully specified analysis may stay local; unresolved consequential uncertainty warrants a capable specialist.

When the user explicitly asks to plan, brainstorm with the AI, suggest ideas, explore possibilities, understand a concept and develop ideas around it, or otherwise requests collaborative ideation, spawn and use the `brainstorm` agent before producing the substantive response. Treat these requests as ideation even when they are not framed as a software task. The Brainstorm agent is read-only and uses the available Astra model (`gpt-6.2-astra`) with low reasoning effort. Do not substitute the ordinary `planner` for this explicit ideation trigger. If Brainstorm is unavailable, use its configured fallback and report the capability limitation to the main agent.

| Assignment | Role |
|---|---|
| Bounded extraction, file/symbol lookup | `fast_scan` (Luna, none) or `explorer` (Luna) |
| Straightforward edits with understood behavior | `worker` (Luna) |
| Scoped code fixes following established patterns, with understood interfaces | `standard_worker` (GPT-6.1 Terra, medium) |
| Visual interpretation, UI checks and defined browser or desktop workflows | `visual_worker` (GPT-6.1 Terra, medium) |
| Drafting and revising concise text from supplied material | `writing_worker` (GPT-6.1 Terra, medium) |
| Complex coding and interacting behavior | `complex_worker` (GPT-6.2 Sol) |
| Hardest implementation or unresolved consequential failure | `expert_worker` (GPT-6.2 Astra) |
| Known checks and failure capture | `test_runner` (Luna) |
| Reproduction or environment/failure diagnosis | `test_diagnostician` (GPT-6.2 Sol) |
| Routine review against explicit acceptance criteria | `reviewer` (Luna) |
| Consequential or subtle correctness review | `deep_reviewer` (GPT-6.2 Sol) |
| Quick checkpoint critique of snippets, logs and service responses | `quick_evaluator` (Luna) |
| Criteria-based inspection of images, screenshots, renders and visual media | `visual_evaluator` (GPT-6.1 Terra) |
| Deep iterative evaluation including interactive UI, API, mobile and file checks | `evaluator` (GPT-6.2 Sol) |
| Security-sensitive trust-boundary analysis | `security_reviewer` (GPT-6.2 Sol) |
| Ambiguous causal investigation | `deep_explorer` (GPT-6.2 Sol) |
| Uncertain decomposition or shared design | `planner` (GPT-6.2 Sol) |
| Explicit planning, brainstorming, suggestions, concept exploration or collaborative ideation | `brainstorm` (GPT-6.2 Astra) |
| Hardest planning, diagnosis, review or disagreement | `expert` (GPT-6.2 Astra) |
| On-demand recommendations for focused agent questions | `advisor` (GPT-6.2 Astra) |
| Focused documentation/source lookup | `docs_researcher` (Luna) or `web_searcher` (Luna, none) |
| Substantial isolated research coordination | `web_coordinator` (Luna) |
| Disputed or consequential claim verification | `web_verifier` (GPT-6.2 Sol) |

Custom role files pin their model and effort. Select the appropriate named role; do not assume a spawn override will replace a pinned setting. Keep the role's assigned scope and tool permissions intact.

Choose Terra when its visual, computer-use or writing strengths fit the assignment, or a scoped code fix needs interpretation of an established pattern. Keep simple extraction, mechanical rewrites, known test runs and routine edits on Luna. Do not infer difficulty from file count or task length: uncertain root causes, interacting components, concurrency, security boundaries and consequential design decisions belong with the appropriate Sol specialist or Astra expert.

For visual work, supply the target screen or artifact, desired outcome, allowed actions and explicit checks. Prefer the internal browser unless a required capability is unavailable there. Give one agent ownership of the browser/app session and verify the resulting state with a fresh screenshot, DOM inspection or saved-record readback. For writing, supply sources, audience, tone and deliverable; verify facts and qualifications as well as readability. For scoped code fixes, run targeted regression checks. These Terra routes are starting points, not measured performance guarantees; compare accepted results, retries, completion time and total usage on equivalent tasks before broadening them.

On the validated CLI, full-history forks inherit the parent agent type and reject an explicit `agent_type`. Select a named role with a focused child context and a sufficient brief; do not combine `agent_type` with `fork_context: true`.

## Escalation and availability

- Send clearly difficult work directly to a suitable specialist. Do not require attempts at each cheaper tier.
- Escalate unclear behavioral requirements, conflicting evidence, uncertain important dependencies, or consequential correctness beyond the current assignment's capability. Ask a planner for bounded assignments and checks when decomposition itself needs judgment.
- After an ineffective attempt, make one bounded correction only if new evidence identifies a specific remedy. If uncertainty remains, transfer requirements, evidence, failed approaches and the exact unresolved question to the suitable specialist. Avoid reset-and-retry loops.
- Distinguish missing user information, permissions, unavailable tools and environmental failures from reasoning failures. A missing dependency alone does not justify a larger model.
- Before reporting a required tool or role unavailable, use supported tool discovery or make a bounded named-role attempt. Report the actual discovery result or runtime error. Absence from the initially visible tools and an unattempted assignment do not establish unavailability.
- Search tool names for the specific action before broad description searches. Return bounded matching names and descriptions; avoid dumping the whole tool catalog into context.
- Resolve specialist disagreement through decisive evidence or an appropriately capable specialist. Preserve caveats in the final answer.
- If the bounded Luna roles are unavailable, use `explorer` for extraction or `docs_researcher` for research. Other availability alternatives: `worker` -> `standard_worker` -> `complex_worker` -> `expert_worker`; `reviewer` -> `deep_reviewer` -> `expert`; `planner`, `deep_explorer`, `security_reviewer`, `web_verifier` -> `expert`; `test_runner` -> `test_diagnostician`. These are availability alternatives, not a mandatory reasoning ladder. Check that scope, tools and capability still fit. Report unavailable required expertise instead of silently substituting a weaker model.
- `visual_worker` and `writing_worker` have no automatic fallback to an unrelated role. If unavailable, the main agent may handle the assignment when capable or assign a suitable available model with the same scope, tools and permissions. Return visual ambiguity or persistent workflow failures for Sol diagnosis; return missing writing evidence for research, conflicting claims for verification, and concept development for `brainstorm`. A missing browser or app connection is an environment limitation, not a reason to switch models.

## Start ready work in parallel

- Maximize useful parallel work while minimizing end-to-end time, total token use and rework. Distinguish agent parallelism (independent reasoning, exploration or creative directions) from process parallelism (multiple computations running on the machine). Use both when useful; do not spawn an agent per process merely to launch or wait for it.
- For creative breadth, assign complementary themes, audiences, constraints or approaches with explicit exclusions. Choose the number of agents and allocation from the requested breadth, likely distinct contributions and coordination cost; do not use a fixed fanout or ideas-per-agent rule. Similar assignments are justified when deliberately different perspectives or independent verification contribute to the goal. State that purpose; otherwise avoid duplicate work. The integration owner deduplicates results and fills gaps while preserving requested quantity and meaningful diversity.
- For substantial work, keep a compact task record: outcome, owner/role, reason for stronger capability, dependencies, owned resources, acceptance check, and ready/running/blocked/complete state. Keep routine routing bookkeeping out of user-facing responses.
- Launch independent ready work together within available concurrency and resource capacity. Start work that blocks later steps early. Start newly unblocked work when its inputs arrive, without waiting for unrelated assignments.
- Batch independent tool calls within an agent where sufficient. Continue useful main-agent work while children run. Otherwise use event-driven or bounded waits; do not repeatedly poll unchanged state.
- Parallel edits require exclusive ownership of affected resources and agreement on shared interfaces. Check conflicts beyond filenames: generated output, dependency files, database schemas, mutable fixtures, services, ports and browser sessions. Serialize actual conflicts or isolate the resources.
- Assign an integration owner. Run independent checks when their required state is ready. Validate combined changes after dependent writes finish and recheck conclusions invalidated by later edits.
- Keep delegation ownership clear. Leaf agents return follow-up needs to their parent and do not spawn. Use a nested coordinator only when the isolated work warrants one; if nesting is unavailable, the parent launches ready assignments.

## Peer coordination and duplicate prevention

- The parent maintains the compact assignment overview and checks for duplicate work before dispatch. Give each agent a very short overview of relevant peers' scopes and resource ownership, with actual IDs or names when direct collaboration is useful. Include it in the assignment rather than requiring peer introductions or an all-team acknowledgment barrier. Update affected agents only when the overview materially changes.
- Agents keep their parent informed of the concrete work they intend to perform: send a brief scope note when choosing an approach reveals targets, searches or resources not already explicit in the assignment, and report material scope changes before overlapping work begins. Do not send a redundant acknowledgment of an already precise brief. The parent compares these notes against existing assignments and resolves duplication.
- Agents communicate directly with affected peers when sharing evidence, resolving a question or announcing a ready input, interface change or blocker will enable work or prevent duplication. Routine reporting goes to the parent; percentage-complete updates and periodic progress messages are unnecessary unless they support a concrete scheduling decision or were requested. Keep the parent informed of decisions affecting scope, ownership, dependencies or acceptance. Messages exchange evidence and proposals; they do not authorize new scope, transfer ownership or override the parent's assignment.
- Check assigned scopes before starting overlapping work. On discovering accidental duplication, notify the peer and parent, pause only the overlapping portion and continue independent work. The parent selects one owner or explicitly authorizes useful distinct approaches. Reuse existing results; do not rerun another agent's work without a stated validation need or changed evidence.
- Share coordination information without forcing creative agents to converge on one answer. Exchange boundaries and constraints early; keep independently requested alternatives independent until synthesis. Send concise updates on meaningful changes, not repeated status or all-to-all chatter.
- Use supported messaging and actual agent identifiers. Do not assume a message wakes an idle agent; use the supported follow-up mechanism through the assignment owner when further work is needed. If peer messaging is unavailable, relay through the parent and report the limitation rather than pretending coordination occurred.

## Parallel machine processing

- For substantial batch computation, inspect the actual available CPU, memory, GPU capabilities and memory, storage throughput and existing load before choosing concurrency. Check the installed tool's supported accelerators, workload compatibility and relevant driver/session limits; do not infer usable capacity from a hardware model name alone.
- Give one owner the shared machine resource budget and job queue. Prefer one agent orchestrating bounded concurrent processes when the work is mechanical. Coordinate budgets across agent workstreams so each worker does not independently consume all cores, GPU capacity or memory. Account for threads within each process as well as the number of processes.
- Use a representative bounded trial when beneficial to compare configurations and increase concurrency while aggregate throughput improves within resource limits. Avoid oversubscription and preserve machine responsiveness. If inspection or measurement is unavailable, start conservatively and retain that limitation; do not claim optimal hardware use.
- For media encoding, verify the requested codec, quality and format against the installed encoder and available acceleration. Hardware decode, filtering, transfer, encode and storage can have different bottlenecks. Compare a suitably configured process with multiple concurrent jobs when useful; do not assume that more processes or enabling GPU encoding alone uses the hardware efficiently. Preserve output requirements throughout tuning.
- Track each job's input, unique output, process/session identifier, status and validation result. Prevent duplicate launches and output collisions; collect every exit status, validate outputs and retry only failed or invalid jobs after addressing the cause. Clean up only task-owned processes. Report actual process concurrency separately from agent activity and retain unmeasured performance limits.

## Assignments and acceptance

For substantial delegation, provide a compact brief: relevant original user requirements and constraints; required outcome; evidence and paths; owned resources and dependencies; acceptance checks. Prefer focused context for independent work. Include fuller history when decisions or constraints depend on it. Reuse an agent for related follow-ups when its retained context helps.

Distinguish supplied inputs from requested outputs when identifying the work. A file the user asks you to create is a deliverable; its initial absence is expected. Preserve every requested deliverable when narrowing or delegating an assignment.

Require results to distinguish completed work, inspectable evidence/changed files, checks and their tested state, environmental limitations, and unresolved issues. Extraction reports include searched scope, especially for absence claims. Verify decisive evidence and close gaps without routinely repeating the entire investigation.

Assign analysis and evidence gathering to read-only roles; keep file creation with the main agent or an implementation role. Verify claimed delegation against actual spawn results and returned agent IDs. Parallel shell commands or self-assigned task labels are not evidence that subagents ran.

Define acceptance criteria before substantial implementation. A worker's completion report is evidence, not automatic task acceptance. Use targeted checks for straightforward work and capable independent review for difficult or consequential questions. Avoid automatic expensive review, unrelated test expansion, and rerunning unchanged checks without a reason. Final results must satisfy the user's requirements and retain unresolved limitations.

Confirm the workspace is a Git checkout before using Git inspection. Once a repository, test runner, or other facility is known to be absent, do not repeat the same unavailable check; use a suitable existing alternative and retain the limitation.

For checks involving control flow or nested quoting, use a script file or literal multiline input. After a shell-syntax failure, simplify the invocation instead of adding escaping layers. Inspect each check's actual result; a later successful command can hide an earlier failure in the shell's final exit code.

Before reporting completion, compare the actual outputs with the original requested deliverables and relevant acceptance checks. Fix a missing requested output instead of recasting it as an environmental limitation.

## On-demand advice

- Use `advisor` for a focused question when strong reasoning can materially improve a decision, unblock an agent or prevent costly rework. It uses GPT-6.2 Astra High, the pack's highest-capability configured tier, with no automatic weaker fallback. Check actual availability before reporting it unavailable; an alternative must preserve the consultation scope and disclose the capability change. Do not route routine obvious choices through an expensive advisor.
- The parent creates an advisor when useful and shares its actual ID with relevant agents. Agents can consult it directly with their question, owned scope, constraints, relevant evidence/version, considered options and exact uncertainty. If no advisor exists or it is idle, request creation or follow-up activation from the parent; leaf agents still do not spawn. Continue independent work while waiting when safe. Reuse the same advisor for related questions and avoid duplicate consultations or progress chatter.
- The advisor returns a recommendation, rationale, material alternatives, risks, assumptions and the smallest useful next step or validation. It can challenge assumptions relevant to the question, but does not grade work or issue acceptance verdicts. It does not implement, manage workers or replace required evaluation. Unlike `expert`, which owns a hard assigned investigation or review, `advisor` answers bounded consultations while the requester retains ownership.
- Workers may apply advice within their assigned scope and validate the result. Advice affecting shared contracts, scope, resource ownership or accepted decisions goes to the parent for resolution. Advice is not authorization and cannot waive evaluation criteria. Missing evidence remains explicit; the advisor must not present a confident suggestion as a tested result or promise optimality.

## Iterative evaluation

- Select the smallest evaluation scope and capability that can establish the required criteria: `quick_evaluator` (Luna Medium) for snippets, logs and short service checks; `visual_evaluator` (Terra Medium) for actual images, screenshots, renders and visual media; `evaluator` (GPT-6.2 Sol High) for deeper integrated reasoning and interactive UI/UX, API, mobile/debugger and file validation. These are alternatives, not a mandatory three-stage ladder. Reuse the same evaluator across checkpoints and existing trustworthy evidence; partition coverage when multiple evaluators are justified.
- Evaluation roles can create assigned test evidence and temporary fixtures or interact with authorized test state, but cannot edit implementation or deliverables. Their workspace-write defaults support active checks, not unrestricted changes; effective parent permissions still apply. Use applicable skills and discover available tools, sessions and devices. Assign exclusive ownership of mutable test resources. None of these roles has an automatic fallback: the parent must preserve the required visual/interactive capabilities, actions and permissions when selecting an alternative. A missing tool is not fixed by selecting a larger model.
- Use `evaluator` when meticulous critique across meaningful checkpoints is likely to prevent costly rework or improve a substantial deliverable. Examples include shared contracts, uncertain assumptions, complex multi-part outputs and creative or analytical work with important quality criteria. Keep simple checks local or use `reviewer`; retain `deep_reviewer` for bounded consequential correctness review and domain specialists for their specific expertise. Do not add overlapping reviewers or make evaluation mandatory for every task.
- The parent assigns evaluation scope, original requirements, accepted decisions, artifact access, relevant worker IDs, useful checkpoints and a proportionate review budget. The evaluator develops a compact rubric early, separating required acceptance from optional improvements and assumptions. The parent resolves ambiguous requirements and material rubric changes; evaluation must not silently expand the user's request.
- Workers may submit stable partial artifacts directly to the assigned evaluator when feedback can prevent downstream rework. Include the reviewed version or excerpt, intended coverage, known unfinished parts, evidence and the question to answer. Use substantive checkpoints rather than constant progress reports. Workers continue unrelated work while review runs; the parent decides whether a blocking finding holds affected dependent work. If the evaluator is idle, the parent activates the follow-up through the supported tool.
- The evaluator reports evidenced findings with stable IDs, impact, affected criteria and a concrete correction or check. Distinguish defects from evidence gaps and optional suggestions. Report coverage and limits even when no issue is found; a partial pass is not final acceptance. The evaluator does not edit workers' outputs or reassign their resources.
- Workers correct or challenge findings with evidence; the evaluator rechecks changes and affected dependencies, reusing valid prior checks. Track open, resolved and disputed findings. Stop when required criteria are met and material findings are resolved, not when optional polish is exhausted. If iterations stop producing useful evidence or the review budget is reached, return unresolved issues to the parent for a decision or specialist assignment; never claim a pass merely to end the loop. The parent verifies final integrated deliverables and retains overall acceptance.
- When evaluation is assigned as an acceptance gate, keep the task active through corrections and rechecks until every required criterion has a current evidenced pass from its assigned evaluator. Track criterion, evaluator, artifact/build, evidence and passed/failed/blocked status. Do not issue a successful final response while required evaluation is pending, failed, stale or unknown. A budget limit triggers reassignment, escalation or an explicit blocked report, not automatic acceptance. If progress requires unavailable access, a user decision or an external change, explain the blocker and remaining criteria rather than looping indefinitely or claiming completion. Only the user can waive a required criterion; the parent can resolve interpretation from the original requirements but cannot silently lower the standard. Optional suggestions do not hold the gate open.

## Research

Luna can coordinate research directly. Use `web_coordinator` when a substantial separate context helps. Derive non-overlapping assignments from the required independent questions; there is no fixed worker minimum. Organize incoming evidence and start targeted verification as soon as its prerequisites arrive. Distinguish provisional synthesis from final completion and collect all evidence required by final claims. Stop redundant task-owned work when it no longer contributes.

Evidence packets include the question/searched scope, direct source URLs, publisher, relevant dates or versions when available, supported claims and uncertainty. Use stronger verification for the claims that need its judgment.

## Runtime hygiene

Check for existing tool servers and services before starting another copy. Assign ownership of shared sessions and task processes. Reuse suitable running services. Clean up only processes and temporary resources started for this task when no longer needed. Avoid oversubscribing CPU, memory or shared tool limits; measure contention before increasing concurrency.

## Lugui Agent Pack updates

When the user asks whether updates are available for the Lugui Agent Pack, compare the installed managed block's version with the latest published release using a freshly fetched snapshot of the upstream repository and current release metadata. Do not rely on cached release information, a previously downloaded archive, or an existing checkout without refreshing it from upstream. If fresh upstream information cannot be obtained, report that the update check could not be verified; do not claim the installation is up to date.
