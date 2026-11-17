# Lugui Agent Pack

Lugui Agent Pack helps Codex finish tasks with fewer tokens and shorter end-to-end runtimes. Luna handles simple work directly, then chooses the smallest model likely to complete a harder assignment well. Luna covers lookup and routine execution. GPT-6.1 Terra handles scoped code fixes, visual and computer-use tasks, and writing from supplied material. GPT-6.2 Sol handles work needing deeper judgment, and GPT-6.2 Astra handles the hardest or unresolved consequential work.

The pack routes work by the capability each task requires instead of sending every task to a large model. That keeps expensive context and stronger models for the assignments that need them, while smaller adequate models handle the rest. The goal is lower token use, faster completion, and preserved correctness.

The pack also coordinates independent work in parallel, waits for real dependencies, and validates results in proportion to their risk.

This kit contains instruction-driven routing and scheduling policies. It does not enforce a dependency graph or guarantee that a model will select the right role. The evaluation suite supplies fixtures and records available evidence; bounded behavioral and comparative evidence is linked from the completed checklist, with failures and limits retained.

## Current status

Version 1.5.0 updates the Sol and Astra roles to GPT-6.2 and adds an advisor and three evaluator presets. The main default is Luna Medium, and unnamed helpers use Luna Low. Role files pin both model and effort. See the [changelog](CHANGELOG.md) for release changes and [validation notes](validation/README.md) for evidence and limitations.

## Where Terra fits

| Role | Assign to Terra | Use another role when |
|---|---|---|
| `standard_worker` | Fix a known UI bug, adapt a parser to an established pattern, or make a bounded change with understood interfaces | Luna can perform a routine edit directly; GPT-6.2 Sol is needed for uncertain causes, interacting components or consequential design |
| `visual_worker` | Interpret screenshots or diagrams, check a rendered UI against requirements, or complete a defined browser/desktop workflow | A simple lookup fits Luna; ambiguous workflows or persistent failures need GPT-6.2 Sol diagnosis |
| `writing_worker` | Draft documentation or release notes from verified changes; revise supplied text for its audience and tone | A mechanical transformation fits Luna; missing evidence needs research, and ideation needs `brainstorm` |

Visual assignments include allowed actions, exclusive session ownership and a fresh check of the resulting state. Writing assignments include source material and checks for facts, qualifications and voice. Code assignments include targeted regression checks. Tool access alone does not authorize external changes.

This routing follows OpenAI's [model selection guidance](https://developers.openai.com/api/docs/guides/model-selection), checked on October 27, 2026. It is a workload fit hypothesis, not a benchmark ranking. Keep difficult investigation, security review, planning and consequential verification on GPT-6.2 Sol, and expert work and brainstorming on GPT-6.2 Astra. Compare representative Terra and Sol tasks using identical inputs and reasoning effort before expanding Terra's scope.

Evaluation has three levels, selected by the evidence needed rather than run automatically in sequence:

| Preset | Model / effort | Scope |
|---|---|---|
| `quick_evaluator` | Luna / Medium | Timely snippet, log and short service checks; actionable local critique |
| `visual_evaluator` | Terra / Medium | Actual images, screenshots, renders and visual media compared with criteria |
| `evaluator` | GPT-6.2 Sol / High | Deep integrated critique and interactive UI/UX, API, mobile/debugger and file validation |

All three share criteria, findings and targeted retesting rules. Workers can submit early artifacts directly; the parent retains scheduling and final acceptance. Evaluators may create assigned test evidence or operate authorized test state, but do not edit implementation or deliverables. Assigned acceptance gates require current evidence for every required criterion before a successful final response. Blocked checks and exhausted review budgets never imply a pass. Routine reviews stay with existing reviewer roles. Evaluation quality, active-tool workflows and efficiency have not been live-benchmarked. See [evaluation assignments](docs/ASSIGNMENTS.md#evaluation-assignments-and-checkpoints).

The `advisor` preset uses GPT-6.2 Astra High, the pack's highest-capability configured tier, for focused on-demand consultation. Agents can ask for recommendations, alternatives and tradeoffs while retaining ownership of their work. Advice neither approves deliverables nor bypasses evaluator gates. The parent creates or reactivates the advisor when needed; routine choices stay local. There is no automatic weaker-model fallback. Its effectiveness has not been live-benchmarked.

## Contents

- `AGENTS.md`: self-contained routing, escalation, parallelism, handoff and acceptance instructions.
- `agents/catalog.json`: model, effort, scope and availability alternatives for each role.
- `agents/prompts/`: shared instructions used to generate standalone role files.
- `.codex/agents/`: generated, project-scoped custom agents.
- `.codex/config.toml`: generated Luna and subagent defaults.
- `scripts/agents.py`: generator and drift checker.
- `scripts/runtime_probe.py`: metadata-only effective-configuration inspection, without sending a model turn.
- `scripts/install.py`, `scripts/install.ps1`, `scripts/install.sh`: cross-platform installation with dry runs and ownership checks.
- `evals/`: fixtures, frozen original configuration, comparison variants and live-run tooling.
- `tests/`: offline checks for generation, installation and evaluation bookkeeping.
- `.agents/skills/stop-slop/`: optional upstream writing skill, tracked as a Git submodule.

Authentication, personal MCP settings, runtime databases and private session files are not part of the kit.

## Use the project

Clone the repository, then open it as a trusted Codex project. Initialize submodules if you want the optional writing skill:

```sh
git clone --recurse-submodules https://github.com/lugui1998/lugui-agent-pack.git
```

Start a fresh task and verify its effective model. An explicit app/session selection or managed setting can override project defaults, and an already-running task may retain old role definitions.

The Terra roles require Codex CLI 0.159.0 or later and account access to `gpt-6.1-terra`; see OpenAI's [model availability guidance](https://learn.chatgpt.com/docs/models). The older `targetCodex` metadata records the original validation target, not Terra compatibility.

The project explicitly sets delegation depth to 2: main agent, optional coordinator, then leaf agents. The isolated CLI check and a live nested research run verified this setting. Omitting it left the coordinator without a spawn tool on the tested version. Leaf roles still receive a no-spawn instruction; this is a policy rule, not a separate permission boundary.

Role sandbox settings are defaults. Codex reapplies the parent turn's live permission overrides to children, so a role's read-only setting alone does not guarantee isolation. Keep the assigned scope explicit and verify the effective permissions when isolation matters. See the [Codex subagent permission guidance](https://learn.chatgpt.com/docs/agent-configuration/subagents).

To install into another project or globally, follow [INSTALL.md](INSTALL.md). Installing global roles alone does not change the main model; the installer provides an explicit global-defaults option. Existing user choices and unrelated settings are preserved according to the selected merge mode.

## Develop and validate

Use Python 3.11 or later, or Python 3.10 with `tomli`:

```sh
python -m pip install -r requirements.txt
python scripts/agents.py --check
python -m unittest discover -s tests
```

Edit the catalog or shared prompts, then regenerate:

```sh
python scripts/agents.py --write
python scripts/agents.py --check
```

The generator reports obsolete role files for review and leaves them in place. Keep the routing table in `AGENTS.md` aligned with the catalog. Generated files are the portable installation payload; target projects do not need the generator to use the roles.

Live evaluations use your existing Codex sign-in and consume usage. Run them in isolated fixtures as described in [evals/README.md](evals/README.md). Compare correctness and end-to-end time on equivalent inputs; include all available agent usage and retain unknown totals when telemetry is incomplete. A routing report or green unit test does not establish model quality or speed.

## Updates

Update the checkout, inspect the changes, regenerate if you edited source prompts, and run the installer with `--update --dry-run` before applying. The update report identifies obsolete managed roles without deleting user files. See the installation guide for platform-specific options and conflict handling.

The parent repository pins the writing skill's commit. Review upstream changes before updating its submodule pointer.
