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

For stronger assignments include the actual reason: uncertain shared state, conflicting causal evidence, or another concrete judgment requirement. Preserve original user wording where paraphrasing could lose a constraint.

Name inputs and outputs separately when a request involves existing documents and new reports. Do not ask a read-only specialist to create the parent's output files. For delegated coordination, retain the actual child IDs and tool results; invented task labels and parallel commands do not establish that other agents ran. If delegation is unavailable, return the ready briefs to the parent.

## Terra assignments

Use `standard_worker` for a scoped code fix with understood interfaces. Provide the observed failure, expected behavior, relevant established pattern, owned files and regression checks. Escalate uncertain causes or changes involving concurrency, security boundaries or interacting components to Sol.

Use `visual_worker` for a defined visual or computer-use task. For example: inspect the rendered checkout form against supplied layout requirements and report mismatches, or fill an authorized draft record using a source document. Specify the browser/app session, allowed actions and desired saved state. The worker should verify the result with fresh UI evidence or readback; it must not submit or publish merely because the controls are available.

Use `writing_worker` for text grounded in supplied material. For example: draft release notes from a verified change list, preserving known limitations and using the requested audience and tone. Specify the output file or response format. Verify factual support, qualifications and required content; return missing evidence to the parent instead of inventing it.

All three Terra roles use Medium reasoning effort. Simple extraction and mechanical rewriting still fit Luna. The visual and writing roles have no automatic fallback; the parent must preserve the assignment's actual capabilities and permissions when choosing an alternative.

## Result

```text
Completed: Implemented the agreed parsing behavior.
Evidence: Relevant file locations and changed functions.
Checks: Exact commands, outcomes, and the state they covered.
Remaining: Consumer integration check belongs to the integration owner.
Limitations: One platform-specific test could not run; explain the environmental cause.
```

An extraction result also names its searched scope. An escalation includes the failed approach, new evidence and exact unresolved question. A research result identifies direct sources, relevant dates/versions, supported claims and uncertainty. A planning result assigns ownership, dependencies and acceptance checks; Luna coordinates those assignments.
