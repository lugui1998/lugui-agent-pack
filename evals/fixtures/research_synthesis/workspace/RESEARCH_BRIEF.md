# Synthetic frozen-evidence brief

All files under `evidence/` are invented, frozen evaluation packets. They are not real-world sources. Their `.invalid` URLs are identifiers that must be retained as citations; do not browse or try to open them.

Answer two independent questions:

1. For comparable-risk adults at North, does the frozen evidence support a reduction in 30-day readmissions, and how should the apparently adverse audit headline be reconciled with the trial?
2. Did the rollout meet the all-in operating-cost target of at most 120 cents per enrolled patient?

Write `SYNTHESIS.json` with this structure:

```json
{
  "evidence_kind": "synthetic_frozen",
  "questions": {
    "clinical_effect": {
      "answer": "...",
      "effect_pp": {"trial": 0.0, "audit_headline": 0.0, "audit_comparable": 0.0},
      "resolution": "...",
      "citations": [{"url": "...", "published": "YYYY-MM-DD"}]
    },
    "cost_target": {
      "answer": "...",
      "reported_cents": 0,
      "omitted_mandatory_cents": 0,
      "all_in_cents": 0,
      "target_cents": 0,
      "resolution": "...",
      "citations": [{"url": "...", "published": "YYYY-MM-DD"}]
    }
  },
  "limitations": ["..."]
}
```

Use percentage-point effects as signed numbers. Preserve source dates and URLs. Treat an unadjusted headline as applying only to its reported population; do not silently substitute it for comparable-risk results.

Use these declared values for machine-checkable conclusions:

- `clinical_effect.answer`: `benefit_supported_for_comparable_north_adults`, `benefit_not_supported`, or `insufficient_evidence`.
- `clinical_effect.resolution`: `population_scope_shift`, `latest_headline_wins`, or `unresolved_conflict`.
- `cost_target.answer`: `target_met`, `target_missed`, or `insufficient_evidence`.
- `cost_target.resolution`: `all_in_cost_includes_mandatory_license`, `dashboard_total_used`, or `unresolved_conflict`.
- Include `synthetic_fixture_not_real_world_evidence` in `limitations`.
