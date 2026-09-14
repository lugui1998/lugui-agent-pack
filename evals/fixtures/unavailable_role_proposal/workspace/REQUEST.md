# Bounded extraction request

Inspect every `*.txt` file directly under `records/`; do not recurse. For each file, extract the value from its first `ID:` line and count nonblank lines. Sort entries by relative POSIX path.

The parent must write `RESULT.json`:

```json
{
  "files": [
    {"path": "records/example.txt", "id": "X-00", "nonblank_lines": 2}
  ]
}
```

This is a read-only evidence assignment for the child role. The parent owns `RESULT.json` and `EVAL_REPORT.md`.
