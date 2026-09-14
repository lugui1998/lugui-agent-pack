# Evaluation fixtures

Each case in `cases.toml` is copied to a newly created directory under the
system temporary directory before it is run.  This prevents this repository's
instructions from leaking into fixture workspaces.  The fixture prompt is the
same for every compared variant.

Run a case with:

```powershell
python scripts/evaluate.py run --case simple_luna --variant baseline --output evals/results
```

`baseline` uses the frozen `evals/baseline` snapshot, `current` uses the
checked-out configuration, and `single-strong` uses the checked-out
configuration with its main model overridden.  Results include timing,
attempt count, parsed JSONL event counts, acceptance output, and token totals.
Token totals are `UNKNOWN` unless the CLI emitted an explicit token count for
that process; child-agent totals are always `UNKNOWN` unless separately
observed in JSONL.

The unavailable-model and independent-parallel cases are simulated instruction
tests: their prompts describe an assumed unavailable role or eligible parallel
work. They do not prove a real model-availability failure or actual concurrent
execution. Routing observations are diagnostics only; only ordered CLI
lifecycle events can provide runtime scheduling evidence, and the result marks
it unknown when those events are absent.

Use `--repetitions N` to make comparable repeated runs; each is written beneath
its own `repetition-###` directory. `--retries N` only retries a failed
acceptance run and every attempt remains recorded separately.

The runner passes `CODEX_HOME` only to the child process, defaulting to the
local user's `~/.codex` home. `CODEX_HOME`, when set, takes precedence. Use
`--codex-home PATH` to select another home. This
permits the CLI to locate its authentication while the fixture copy continues
to exclude auth and session files.

The runner uses `--approve-for-me` without an explicit `--sandbox` or
`--ephemeral`, matching the approval mode validated by the live probe. Its
evaluation sessions remain in the user's normal local Codex history. It neither
copies authentication nor changes global configuration. Each result includes
the exact sanitized argv; `--ignore-user-config` still has the runtime trust
and instruction-loading limitations recorded in that result.

Every command adds a root TOML `projects={"<temporary-workspace>"={trust_level="trusted"}}`
override and uses `--ignore-user-config` while preserving host exec policies;
the CLI help confirms that it skips only `$CODEX_HOME/config.toml`. Result records hash the
copied project config and retain only observed model/effort event values.
They mark effective main settings and global-instruction loading `UNKNOWN`
when JSONL cannot prove them. The later context audit confirmed that the real global `AGENTS.md` is merged
ahead of the project policy despite `--ignore-user-config`; inspect instruction
precedence when evaluating a conflicting installation.

Acceptance requires both all artifact checks and a successful, non-timeout CLI
exit. Functional-check timeouts, decode failures, and process errors are saved
as failed checks. Result records hash the case and variant specifications, the
runner, and every copied instruction/config/role file for reproduction.
Failed equality checks also retain their sanitized expected and actual values,
so they remain auditable after the temporary fixture is removed.

Use `--read-session-activation` to opt into activation evidence from persisted
session rollouts. The runner reads only files whose filename ends with an exact
thread UUID observed in that run's CLI JSONL, under the selected
`CODEX_HOME/sessions` directory. It reports only rollout hashes and paths,
allowlisted `turn_context` model/effort, and whether the complete normalized
copied `AGENTS.md` text occurred in one user/developer message. It never saves
rollout content, prompts, or private instructions. Missing or ambiguous logs,
child roles, and child usage remain `UNKNOWN`.

When session activation evidence is enabled, each attempt also records
`configuration_validity` separately from artifact acceptance. It compares the
actual allowlisted main model and effort with the variant override or copied
project configuration, and records complete copied-`AGENTS.md` presence. Any
missing telemetry remains `UNKNOWN`; a model or effort mismatch is `false`.
Artifact checks alone never make a configuration comparison valid.

Use `--read-whole-task-telemetry` for a privacy-preserving view of one task's
explicitly linked child threads. It follows only `receiver_thread_ids` from
owned collaboration calls, validates each child rollout's `session_meta`
parent linkage, and may recurse through validated children. It records
allowlisted role/model/effort, task and measurable tool intervals, and unique
per-response token usage for matching thread/root turns. The observed token
subtotal remains distinct from a complete total over known linked threads;
unknown children, unobserved delays, and simultaneous execution remain
`UNKNOWN`. No rollout text or unrelated session content is exported.

Reproducibility hashes are captured before the model starts. A separate
post-run copied-file hash map makes configuration changes during execution
visible without treating them as input state.

The pre-run record includes the selected task specification and its own hash,
so adding an unrelated scenario does not change the identity of an existing
task. Older results with only a whole-manifest hash remain separate unless
equivalence can be established from their original evidence.

Version-2 scenarios can be run with `--scenario NAME` instead of `--case`.
Only `fixture_dir/workspace` is copied into the temporary model workspace;
oracles, references, tests, and role-override sources remain outside it. The
runner copies UTF-8 fixture bytes without newline translation, excludes generated
`__pycache__`, `.pyc` and `.pyo` files consistently from copying and hashing, hashes all
fixture inputs before the model starts (excluding generated Python cache files), and invokes scenario
`external_command` checks with an argv list, `shell=False`, and substituted
`{python}`, `{fixture_dir}`, and `{workspace}` values. Scenario observation
rubrics are recorded as `PENDING_REVIEW` or `UNKNOWN`, never artificial passes,
and remain separate from artifact acceptance and configuration validity.

Instruction-presence comparisons use the normalized copied `AGENTS.md` text
captured before the model starts, so later workspace edits cannot change the
expected context after the fact.

Compare saved results without making new model calls:

```powershell
python scripts/compare_evals.py evals/results/representative-comparison-1 evals/results/representative-followup-1 --output validation/representative-comparison.json
```

The comparison groups equivalent task inputs and separates configuration
revisions. It includes failed attempts and retry time, and reports all-valid-run
and successful-run latency separately. STANDARD reference credit estimates are
dated and distinct from raw tokens and actual account usage. Incomplete or
unsupported telemetry cannot produce a complete usage estimate. Behavioral
rubrics still need an evidence review; artifact acceptance alone does not
establish correct routing or scheduling. See
[the candidate decision record](../validation/DECISIONS.md) for current results
and limitations.
