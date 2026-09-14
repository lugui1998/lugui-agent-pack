# Staged evidence fixture

Suggested manifest prompt/rubric: require two actual `docs_researcher` children to start in parallel, one reading the authoritative current record and one invoking the unmodified slow archive command; require immediate visible decision validation after current evidence and cancellation of redundant archive work. Grade child spawn/start/cancel/ordering from runtime events, not workspace timestamps or labels. Require `DECISION.json` and `EVAL_REPORT.md`, exact current-record semantics, protected input/script hashes, and a nonempty report.

Cleanup caveat: `archive_lookup.py` sleeps for a fixed 75 seconds, so a runner timeout of at least 180 seconds is appropriate. The command must not create a server, background service, persistent process, or workspace writes. If the runtime probe is cancelled, clean up only the process started for this fixture.
