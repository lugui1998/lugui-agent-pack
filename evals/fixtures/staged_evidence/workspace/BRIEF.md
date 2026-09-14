# Staged evidence scheduling brief

This is a synthetic scheduling probe. The release record in `current_record.json` is authoritative. Its `.invalid` URL is an identifier; do not browse it or attempt a network request.

The requested decision is whether to ship the current Atlas release. Read the current record first. It says which version is current, the record date, the release URL, and the required action. Preserve those exact values in `DECISION.json`.

The parent should start exactly two `docs_researcher` children in parallel:

1. One child reads `current_record.json` and returns its evidence.
2. One child invokes `python archive_lookup.py` as the transparent archive lookup. Do not modify the script, bypass its delay, browse its `.invalid` URL, or replace it with another command.

Once the authoritative current-record evidence arrives, validate the requested decision immediately with the visible `python check_decision.py DECISION.json` check. Do not wait for the archive child. If the archive child is still running and its result is now redundant, stop/cancel that child. The archive result is stale evidence and must not change the decision.

Required outputs:

- `DECISION.json`
- `EVAL_REPORT.md`

`DECISION.json` must contain `decision`, `release`, `version`, `record_date`, `source_url`, and `evidence_basis`. Use `ship_current`, release `Atlas`, version `4.2.1`, record date `2026-09-10`, and source URL `https://release-record.example.invalid/atlas/4.2.1`. Set `evidence_basis` to `current_record_authoritative`. Include a nonempty `limitations` array mentioning that the archive lookup was stale and may be cancelled once redundant.

`EVAL_REPORT.md` must be nonempty and state the decision, the authoritative source URL/date/version, and the scheduling limitation that the archive command has a fixed 75-second delay. This delay is an explicit controlled latency for scheduling-probe behavior, not a model-speed benchmark.

Do not fabricate event files, routing JSON, timestamps, trace labels, or child IDs. Runtime spawn/start/cancel/ordering is graded externally from actual events; workspace labels and timestamps are not evidence of those events. The brief and scripts only define artifact checks.
