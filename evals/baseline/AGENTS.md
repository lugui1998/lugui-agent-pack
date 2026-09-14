# Delegation policy

- Use delegation when work has independent workstreams, is read-heavy, is long-running, or would otherwise pollute the primary context.
- Stay in the primary thread for trivial questions, one-file lookups, and small straightforward edits.
- Make every delegated task bounded, non-overlapping, and explicit about the expected evidence or output.
- Parallelize independent read-only work; coordinate write-heavy work sequentially to avoid conflicts.
- Prefer the cheapest suitable role first and escalate only when ambiguity, risk, or source disagreement justifies it.
- Leaf agents must not spawn more agents. Only coordinator roles may create nested delegation.
- Keep delegated summaries concise and include file paths, symbols, URLs, dates, claims, and confidence where relevant.

# Role routing

- Use `fast_scan` for mechanical, low-risk inspection.
- Use `explorer` for repository mapping, execution tracing, test/log triage, and evidence gathering.
- Use `deep_explorer` for ambiguous or cross-module investigations.
- Use `worker` for bounded implementation work.
- Use `test_runner` for validation and failure reproduction.
- Use `reviewer` for correctness, regressions, edge cases, and final review.
- Use `security_reviewer` only for security-sensitive analysis.
- Use `docs_researcher` for focused API and documentation verification.

# Broad web research

- Use `web_coordinator` for broad, multi-angle, or source-heavy web research.
- Have `web_coordinator` fan out 4-8 independent `web_searcher` tasks in parallel.
- Search workers must return evidence packets, not broad conclusions.
- Use `web_verifier` for disputed, high-impact, time-sensitive, or weakly sourced claims.
- Require direct URLs, publication/update dates, supported claims, and uncertainty in the final synthesis.
- Avoid spawning search workers for a narrow question that can be answered with one focused search.

# Runtime hygiene

- When using terminal commands, be sure to end terminals that are not in use anymore. Take care to not leave unused running processes.
