# Assignment and result examples

Use these fields for substantial work. Short direct tasks do not need a written task board. The runtime policy in AGENTS.md is self-contained and does not require loading this document on each request.

## Assignment

```text
Outcome: Add the agreed parsing behavior while preserving existing exported names.
Requirements: Preserve the user's accepted error behavior and whitespace rules.
Inputs: Existing parser.py, requirements, and failing tests.
Outputs: Updated parser.py and tests/test_parser.py; return validation evidence to the parent.
Evidence: parser.py and the failing parser tests; related investigation already completed.
Owner: worker; routine change following an established pattern.
Owned resources: parser.py and tests/test_parser.py; no shared fixture/service changes.
Dependencies: Input format decision from task A. Ready after that decision arrives.
Checks: Existing parser cases and targeted new cases; integration owner checks consumers.
State: blocked on A.
```

For related parallel assignments, the parent keeps a compact ownership overview and includes a very short summary of relevant peers' scopes and resources in each brief. Supply actual peer IDs when direct collaboration is useful, and update affected agents only on meaningful changes. Agents report concrete scope to the parent when their chosen approach adds details absent from the assignment; they need not acknowledge or repeat a precise brief. The parent checks for duplicates. Direct peer messages are for useful evidence, questions and dependency handoffs, not introductions or routine progress reports. This is a messaging protocol, not an enforced scheduler or resource lock.

Use these additional fields where they resolve real coordination needs:

```text
Peers: Actual peer IDs/names, complementary scopes and resource ownership.
Ready when: The interface owner publishes the accepted input/output contract.
Produces: Contract and implementation; publish contract readiness before implementation completes.
Shared contract: Named interface, current agreed version and owner authorized to change it.
Invalidates: Consumer checks must be repeated if the accepted contract changes.
Overlap policy: Distinct scope by default; state the purpose of any independent alternative or verification.
Resource budget: Shared machine owner and this assignment's agreed process/thread/accelerator limits.
```

An accepted contract can release consumer work before the producer finishes implementation; final integration still checks both. A scope announcement does not reserve resources unless the parent has assigned them. If overlap is discovered, agents notify each other and the parent, pause only that portion and retain useful independent work.

## Two forms of parallel work

For a request for many distinct ideas, divide useful creative directions across agents according to breadth and expected contribution, without fixed counts. Include a short overview of related directions and exclusions in the briefs; agents report newly chosen scope to the parent when it is not already explicit. Preserve independent thinking where diversity is intended, then deduplicate the combined result and fill missing coverage. Do not count paraphrases as new ideas or silently reduce the requested total after deduplication.

For a batch of media encodes or other mechanical computation, an execution owner can manage multiple processes without creating multiple thinking agents. Inspect available hardware, installed tool capabilities and current load; select compatible acceleration and a bounded process/thread budget. Compare representative throughput where worthwhile, preserving the requested quality and format. Record each input, unique output, process identifier, exit status and validation result; do not launch the same job twice. Coordinate the resource budget with other workstreams. This example specifies a workflow, not a claim about any particular GPU's engine count or encoder support.

For stronger assignments include the actual reason: uncertain shared state, conflicting causal evidence, or another concrete judgment requirement. Preserve original user wording where paraphrasing could lose a constraint.

Name inputs and outputs separately when a request involves existing documents and new reports. Do not ask a read-only specialist to create the parent's output files. For delegated coordination, retain the actual child IDs and tool results; invented task labels and parallel commands do not establish that other agents ran. If delegation is unavailable, return the ready briefs to the parent.

## Terra assignments

Use `standard_worker` for a scoped code fix with understood interfaces. Provide the observed failure, expected behavior, relevant established pattern, owned files and regression checks. Escalate uncertain causes or changes involving concurrency, security boundaries or interacting components to Sol.

Use `visual_worker` for a defined visual or computer-use task. For example: inspect the rendered checkout form against supplied layout requirements and report mismatches, or fill an authorized draft record using a source document. Specify the browser/app session, allowed actions and desired saved state. The worker should verify the result with fresh UI evidence or readback; it must not submit or publish merely because the controls are available.

Use `writing_worker` for text grounded in supplied material. For example: draft release notes from a verified change list, preserving known limitations and using the requested audience and tone. Specify the output file or response format. Verify factual support, qualifications and required content; return missing evidence to the parent instead of inventing it.

All three Terra roles use Medium reasoning effort. Simple extraction and mechanical rewriting still fit Luna. The visual and writing roles have no automatic fallback; the parent must preserve the assignment's actual capabilities and permissions when choosing an alternative.

## Evaluation assignments and checkpoints

Choose `quick_evaluator` for bounded snippet/log/service checks, `visual_evaluator` for visual artifacts, or `evaluator` for deep integrated critique and active UI/API/mobile/file validation. Select by needed evidence, not a mandatory escalation ladder. A routine final review can stay with `reviewer`; avoid duplicate review coverage. The parent supplies the original goal, accepted constraints, audience, outputs, worker IDs, review scope and budget. The evaluator proposes observable criteria early and separates requirements from optional polish. The parent resolves ambiguities and any changes to acceptance criteria without silently weakening requirements.

```text
Owner: evaluator; cross-deliverable consistency and early flaw detection need deeper judgment.
Inputs: Original requirements, accepted decisions, rubric if available, and stable artifacts.
Outputs: Rubric, evidenced findings with stable IDs, scoped verdict and remaining limits.
Checkpoints: Accepted interface, representative working slice, final combined output.
Peers: Actual worker IDs and scopes; parent owns assignment changes and final acceptance.
Review budget: Proportionate time/usage or rounds set by the parent for this assignment.
Stop when: Required criteria are met and material findings are resolved; report unresolved issues if the budget is reached.
Test resources: Assigned browser/device/service, allowed actions, test data and evidence output paths.
Acceptance gate: Criterion -> evaluator -> current artifact/build -> evidence -> passed/failed/blocked.
```

Workers submit a stable version or excerpt, what it is meant to demonstrate, intentionally unfinished parts, evidence and a question. For example, an interface can be reviewed for contradictory field definitions before consumers implement it. A declared missing final section in an outline is not itself a defect; a structure that cannot cover a required topic may be. Submissions are event-driven, not periodic status messages. If a message cannot activate an idle evaluator, the parent issues the supported follow-up.

Findings identify the criterion, location/evidence, impact, minimum correction or check, and an ID for follow-up. Workers return corrections or counterevidence against those IDs. The evaluator rechecks the affected state and dependencies, tracking open, resolved and disputed findings without reopening unchanged work. A scoped verdict is ready to proceed, changes needed or unable to assess; it includes coverage and uncertainty. The evaluator remains independent and does not edit deliverables. The parent owns blocking decisions, escalations and final integrated acceptance.

These are instruction-level behaviors. Configuration tests establish the preset's wiring, not that agents reliably detect flaws or reduce rework. Behavioral evaluation should include an early cross-artifact contradiction, adequate work requiring no changes, a deliberately partial submission, a correction with regression risk and missing evidence that must remain inconclusive.

Quick feedback is triggered by meaningful submissions such as a changed function, an endpoint response or a completed test. It is not literal uninterrupted monitoring of another agent's edits. Suggestions for clearer logs or more auditable structure should identify a concrete diagnostic benefit and remain optional unless necessary for an agreed criterion.

Visual review identifies the inspected image/render/frame and its reference. It does not prove hidden model geometry, temporal video behavior or UI functionality from a screenshot. Deep review uses relevant available skills for actual interactions and captures steps, environment, expected versus observed outcomes and saved-state evidence. Assign mutable sessions exclusively; allow temporary test evidence without allowing source fixes. Missing capabilities leave the affected criteria blocked and are reported to the parent.

Before successful completion, the parent gathers a current pass for each required gated criterion. A new build that invalidates evidence requires the relevant recheck. A partial pass, a service returning HTTP 200, a screenshot or a worker's assertion alone cannot substitute for an unperformed required check. When a review budget is exhausted, reassign or escalate the remaining work; if genuinely blocked, report the exact missing input/access and unfinished criteria. Do not silently waive criteria or keep retrying without new evidence. User-requested changes or waivers must be recorded explicitly.

## Advisor consultations

Use `advisor` when a focused decision benefits from strong reasoning. The parent supplies the advisor's actual ID to relevant workers and activates follow-ups when needed. Workers send a compact consultation rather than asking for a review of everything:

```text
Requester: Actual agent ID and owned scope.
Question: Which approach best satisfies the stated constraint, and why?
Goal/constraints: User requirements and decisions that must be preserved.
Evidence: Relevant artifact/version, observations and previous checks.
Considered: Options, prior attempts and the exact unresolved tradeoff.
Needed: Recommendation, assumptions and a small validation step.
```

The advisor answers the requester with the best-supported recommendation and meaningful alternatives, qualifying missing evidence. The worker implements within its scope and validates the result. Shared-contract or ownership changes go through the parent. Advice is not a pass/fail review, permission to expand scope or a substitute for required evaluation. Reuse related consultations; avoid repeated questions with no changed evidence. Unavailable access or tools must not be disguised as a reasoning problem.

## Result

```text
Completed: Implemented the agreed parsing behavior.
Evidence: Relevant file locations and changed functions.
Checks: Exact commands, outcomes, and the state they covered.
Remaining: Consumer integration check belongs to the integration owner.
Limitations: One platform-specific test could not run; explain the environmental cause.
```

An extraction result also names its searched scope. An escalation includes the failed approach, new evidence and exact unresolved question. A research result identifies direct sources, relevant dates/versions, supported claims and uncertainty. A planning result assigns ownership, dependencies and acceptance checks; Luna coordinates those assignments.
