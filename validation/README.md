# Validation notes

Release `1.0.0` was validated on Windows with the target Codex CLI version
`0.154.0-alpha.6.2`.

- The offline test suite passes, including installer, generator, evaluator,
  runtime-probe, telemetry, scenario-control, and comparison coverage.
- Generated agent files match `agents/catalog.json` and the shared prompts.
- Evaluation fixtures and independent oracles live under `evals/`; generated
  runs are intentionally excluded from the repository.
- Runtime evidence is bounded: Windows installation is covered, while native
  non-Windows installation and broad autonomous routing remain unverified.
- Model and effort defaults are provisional and may be overridden by app or
  session settings.

## GPT-6.1 Terra routing, October 27, 2026

Version 1.4.0 adds Terra Medium for scoped implementation,
visual/computer-use work and writing grounded in supplied material. Luna
defaults and the Sol/Astra specialist assignments remain in place.

All 99 offline tests passed on Windows. The initial restricted run could not
create temporary installer files, Git fixtures and directory links; rerunning
with the required filesystem permissions passed. Catalog coverage checks the
Terra roles, permission defaults, fallback choices and preserved model routes.
Generated role files match the catalog and prompts.

A metadata-only configuration probe on Codex CLI `0.164.0-alpha.11.1` read
all three Terra Medium role declarations and the Luna Medium main / Luna Low
helper defaults. It sent no model turn. The initial restricted probe lacked
access to Codex runtime state; the permitted rerun succeeded and stopped its
temporary client process.

The local global installation was updated using the unmodified installer from
a freshly downloaded published `1.3.1` release with the `1.4.0` payload. All
21 installed role files matched the source byte for byte. Verification confirmed
that personal instructions outside the managed block, global configuration,
reset preferences and hook files, and unrelated ownership records were
preserved. The installed reset/update/scope guidance was retained in the
installation payload because it was newer than this source checkout. No reset
hook or model turn was executed by the installation checks.

These checks establish configuration consistency and installer behavior. They
do not establish Terra's task quality, relative speed or total cost. No live
Terra/Sol comparison was run for this update; earlier runtime evidence does not
validate these new model assignments.

Run the checks with:

```sh
python -m unittest discover -s tests
python scripts/agents.py --check
```
