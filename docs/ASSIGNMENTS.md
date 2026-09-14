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

## Result

```text
Completed: Implemented the agreed parsing behavior.
Evidence: Relevant file locations and changed functions.
Checks: Exact commands, outcomes, and the state they covered.
Remaining: Consumer integration check belongs to the integration owner.
Limitations: One platform-specific test could not run; explain the environmental cause.
```

An extraction result also names its searched scope. An escalation includes the failed approach, new evidence and exact unresolved question. A research result identifies direct sources, relevant dates/versions, supported claims and uncertainty. A planning result assigns ownership, dependencies and acceptance checks; Luna coordinates those assignments.
