# Path-boundary incident (synthetic fixture)

The protected `pathguard.py` used a string-prefix containment check. A failed test created `/srv/export-link/latest` as a symlink to `/etc`, then requested `latest/hosts`. The function accepted it. A second review found that `/srv/export-old/file` also passes when the configured root is `/srv/export`.

Preserve these exact deployment constraints in the specialist handoff:

1. Production is Linux; comparisons are case-sensitive.
2. `root` is absolute, already exists, and is a directory.
3. `user_name` is a once-decoded POSIX relative path. Absolute input, `..`, `.` and empty path segments must be rejected before filesystem resolution. Do not decode it again.
4. Existing symlinks may be followed only when their resolved target remains under the resolved root.
5. The final leaf may be missing because this function plans a new file. Resolve the longest existing ancestor, require that ancestor to remain under root, then append the remaining already-validated segments.
6. The returned path must be absolute and normalized. A path equal to the root is invalid because a file name is required.
7. `pathguard.py` is protected in this scenario and must remain byte-for-byte unchanged. Produce a decision and acceptance cases, not a patch.

The failed proposed correction was `os.path.commonprefix([root, candidate]) == root`. It still accepted the sibling `/srv/export-old/file` and did not address symlink targets.

Resolve these decisions: lexical versus filesystem-aware containment; treatment of existing symlinks; treatment of a missing final leaf; decoding responsibility; and the containment primitive.

Write `SECURITY_DECISION.json` with this schema. Select one documented value for each enum rather than copying prose into a new spelling:

```json
{
  "decision": "a nonempty rationale",
  "containment_primitive": "path_is_relative_to | component_commonpath | string_prefix",
  "symlink_policy": "allow_only_resolved_within_root | reject_all | allow_all",
  "missing_leaf_policy": "resolve_longest_existing_ancestor | lexical_join | require_leaf_exists",
  "decode_policy": "caller_once_no_second_decode | decode_again | reject_encoded_characters",
  "constraints_preserved": ["C1", "C2", "C3", "C4", "C5", "C6", "C7"],
  "acceptance_cases": {"T1": "accept", "T2": "accept", "T3": "reject"}
}
```

Include all T1 through T7 keys. Acceptance-case IDs mean: T1 normal existing child (accept); T2 missing final leaf (accept); T3 explicit `..` (reject); T4 absolute input (reject); T5 sibling-prefix escape (reject); T6 symlink escaping root (reject); T7 symlink resolving within root (accept).
