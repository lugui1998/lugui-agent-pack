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

Run the checks with:

```sh
python -m unittest discover -s tests
python scripts/agents.py --check
```
