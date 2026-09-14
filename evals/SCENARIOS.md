# Behavioral scenario fixtures

These scenarios evaluate model work rather than replaying a scripted success. Every scenario directory has three boundaries:

- `workspace/` is the only fixture content copied into the model's temporary workspace.
- `oracle.py` is an external artifact check. It is not copied into the model workspace or named in the task prompt. This separation is not a filesystem permission boundary.
- `solutions/reference/` and `solutions/known_bad/` are evaluator-only controls. Unit tests prove the oracle rejects the initial workspace and a plausible wrong answer, then accepts the independent reference answer.

No fixture program manufactures outputs, routing choices, timestamps, or agent events. Artifact checks and coordination observations are intentionally separate. Passing an oracle establishes the requested work product; delegation, activation, overlap, dependency order, fallback, and handoff claims must come from the runner's real Codex event stream.

## Runner integration contract

`scenarios.toml` version 2 is declarative. A scenario runner must perform these steps:

1. Resolve `fixture_dir` beneath the repository's `evals/fixtures` directory. Create a new temporary workspace and copy only `<fixture_dir>/workspace/**` into it. Never copy `oracle.py` or `solutions/`.
2. Materialize the selected kit configuration in the temporary workspace. For each optional `copied_role_overrides` entry, resolve `source` beneath `fixture_dir`, require `destination` to be exactly `.codex/agents/<role>.toml`, and copy it only into this temporary workspace. Do not modify the source kit.
3. Send the scenario's `prompt` to a live Codex run. The prompt, fixture files, and copied role overrides are model inputs; the reference and known-bad solutions are not.
4. After the run, expand only the documented placeholders in each external check: `{python}`, `{fixture_dir}`, and `{workspace}`. Run the check outside the model process with its stated timeout. Acceptance requires exit code zero from every check.
5. Score `observation_rubric` from authenticated runner telemetry linked to the root run and its actual tool/agent activity. Do not accept workspace files, model prose, or fixture-emitted events as scheduling or routing evidence. Record `UNKNOWN` when the runtime cannot expose required evidence; do not turn missing telemetry into a pass.

`coordination_mode = "autonomous"` leaves routing and fanout to the model. `forced_parallel`, `forced_coordinator`, `forced_specialist`, and `forced_probe` are explicit comparison cases whose prompts require a particular interaction. This prevents a comparison fixture from being mistaken for a universal fanout policy.

Run a live scenario with `python scripts/evaluate.py run --scenario parallel_modules --variant current --output evals/results/example --read-session-activation --read-whole-task-telemetry`. The runner keeps artifact acceptance, configuration validity, and behavior review separate. Record the evaluated revision and keep failed or incomplete runs in the evidence.

## Scenario intent

`parallel_modules` starts with two failing modules and a stable cents-based interface. Pricing and tax work can proceed independently. Invoice verification depends on both, and the hidden oracle exercises each module plus their combined state. Actual overlap is useful observational evidence when the model delegates, but fixed fanout is not required for acceptance.

`parallel_writes_probe` uses the same workspace and oracle while explicitly requesting two workers with exclusive module ownership. It tests actual parallel edits and subsequent integration. Its forced structure is not evidence that delegation is fastest for every small change.

`ambiguity_security_handoff` supplies a vulnerable implementation, failed evidence, deployment constraints, and exact unresolved policy choices. The protected source must remain byte-for-byte unchanged. A real security-specialist handoff is required by telemetry; the external oracle checks that the final structured decision preserves the constraints and resolves the policy safely.

`ambiguity_security_autonomous` reuses the same workspace and oracle without naming a role. Its telemetry shows whether autonomous routing recognizes the consequential uncertainty. Keeping it separate from the forced handoff case prevents prompt compliance from being mistaken for a routing decision.

`research_synthesis` labels every packet as synthetic frozen evidence and uses reserved `.invalid` URLs. The two questions are independent, while each contains conflicting headline and scoped evidence. The oracle checks structured conclusions, scope, dates, URLs, and accounting. It does not require a fixed number of search workers.

`research_coordinator_comparison` reuses that exact workspace and oracle but explicitly requires `web_coordinator` and two nested question assignments. It exists only to compare coordinator configurations such as the current default and `coordinator-max`; its prescribed structure does not apply to the autonomous research scenario.

`unavailable_role_fallback` provides a copied `fast_scan` role whose model string is valid TOML but intentionally absent. The prompt requires an actual spawn attempt and an `explorer` fallback only after runtime failure. The oracle checks the extraction output; runtime telemetry checks the probe and fallback sequence.

Run the fixture-oracle controls without any live model calls:

```powershell
python -m unittest discover -s tests -p test_scenarios.py
```
