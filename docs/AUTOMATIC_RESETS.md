# Automatic banked resets

This optional global feature uses saved criteria to redeem an existing Codex reset.
It is disabled until the user explicitly opts in. Project installation does not configure
it. See [INSTALL.md](../INSTALL.md) for installation choices and command options.

**Implementation status:** source review and offline tests. The hook passes 21 tests
against a fake Codex server; seven additional tests cover reset installation and one
checks release archive exclusions. No real account or redemption API was called.
Live Codex compatibility and hook activation remain unverified.

## Installation and preferences

On the first global installation, the installer presents the defaults and asks the user
to enable them, customize them, or disable the feature. An agent driving a noninteractive
install must obtain the user's choice and pass it explicitly. Updating the pack preserves
the saved choice and criteria unless the user requests a change.

Declining creates no hook script or registration. Enabling installs the hook and two
event registrations. The user still reviews the hook through Codex's normal trust flow.
The installer does not change Codex's permissions or hook trust records.

| File under `CODEX_HOME` | Purpose |
| --- | --- |
| `lugui-agent-pack/preferences.json` | User's opt-in and reset criteria |
| `hooks/usage_quota_check.py` | Installed hook script |
| `hooks.json` | Managed `UserPromptSubmit` and `PostToolUse` entries alongside existing hooks |
| `lugui-agent-pack/usage-quota/state.json` | Shared polling cache and durable redemption attempts |
| `lugui-agent-pack/usage-quota/owner.lock` | Coordination across processes using this Codex home |
| `lugui-agent-pack/usage-quota/diagnostic.json` | Suppression of repeated configuration or damaged-state notices |
| `lugui-agent-pack/state/global.tsv` | Installer ownership records |

`CODEX_HOME` defaults to `~/.codex`. The installer registers absolute paths, including
the Python interpreter used to install the pack. If that interpreter moves, update the
installation. On Windows the command uses an encoded PowerShell invocation to preserve
literal paths; it does not change PowerShell's execution policy.

The default preferences are:

```json
{
  "schemaVersion": 1,
  "automaticResets": {
    "enabled": false,
    "expiresWithinDays": 5,
    "remainingPercentWithCredits": 0,
    "remainingPercentWithoutCredits": 1,
    "minCheckIntervalMinutes": 5,
    "maxCheckIntervalMinutes": 60,
    "initialCheckIntervalMinutes": 30,
    "codexCommand": "codex"
  }
}
```

`enabled` must be an explicit boolean. Expiry may be greater than zero and at most 365
days; fractional days are accepted. Remaining quota thresholds accept fractions from
0 through 10 percent. The threshold with credits cannot exceed the threshold without
credits. Intervals are whole minutes between 5 and 60, with minimum ≤ initial ≤ maximum.
`codexCommand` is one executable name on an absolute PATH entry or an absolute executable
path, without extra arguments. Unknown policy fields are rejected.

The percentages describe quota **remaining**. Only the core five-hour or weekly Codex
window can trigger redemption. Either window reaching its applicable threshold is
sufficient. Model-specific buckets and unrelated limits are excluded.

## When it queries usage

The hook receives prompt submissions and supported tool-completion events. Most
invocations read the small local cache and return without starting the Codex client.
Events do not create a timer or wake an idle chat. The next query happens on the first
eligible event after the saved deadline.

When a query is due:

1. Acquire a shared, nonblocking OS lock. If another hook owns it, return.
2. Read the signed-in account and current usage through the Codex app-server protocol.
3. Look for an available reset with a known expiration inside the configured period.
4. If eligible, perform fresh account and usage checks immediately before redemption.
5. Save the observation and next query deadline.

With a near-expiry reset, the first interval is 30 minutes. Later comparable observations
estimate the rate of quota use and schedule a query halfway to the expected threshold,
bounded by the configured minimum and maximum. Quota recovery starts a new estimate.
When no known reset is near expiry, the hook uses the maximum interval. Query failures
also back off to the maximum. A known quota-reset or reset-expiry boundary can shorten
the interval.

The estimate uses observations collected by this hook. It does not request historical
usage, read transcripts, or spend model tokens on predictions. Usage by other chats and
devices may change consumption abruptly, so the schedule cannot guarantee a check
before quota runs out. A long model response with no hook event also delays checking.
The 1% no-credit threshold is a margin, not a guarantee of uninterrupted work.

## Redemption and recovery

An opted-in hook redeems automatically once all criteria match. It selects the
eligible reset that expires soonest. Unknown or contradictory credit information,
unknown expiry, absent core quota, an unsupported protocol, or an unavailable
ChatGPT account identity prevents redemption.

Before sending the request, the hook rechecks the account, current quota, selected
reset and saved policy. It records the exact reset ID and a new idempotency key to disk
before calling `account/rateLimitResetCredit/consume`. Concurrent hooks sharing the
same home are serialized through verification, redemption and durable recording.

If the request's outcome is uncertain, a later eligible retry retains the original
reset ID and idempotency key. It cannot substitute a different reset. A completed
or uncertain attempt is retained until fresh data shows recovery above the threshold in every
window that originally triggered it. This prevents repeated resets while usage
reporting catches up. After observing recovery, that invocation cannot consume another
reset. If recovery cannot be established, automation remains paused.

The journal is flushed before sending a redemption request and replaced atomically.
The Windows implementation is intended to survive a hook process crash, but persistence
of the replacement across an OS crash or power loss is not guaranteed. Live behavior
and recovery from OS crashes or power loss remain untested.

Do not delete the state file or an unresolved attempt to force a retry: that discards
the information used to avoid another redemption. Disable the feature and review the
account state first. The hook's coordination applies to one `CODEX_HOME`; separate
homes and devices do not share its local lock.

The account protocol supplies a nullable email rather than a guaranteed account or
workspace ID. State is partitioned by a hash of the normalized email; no email,
credentials, raw account responses or transcripts are saved. Accounts with the same
email in different workspaces cannot be distinguished. Switching accounts is detected
at the next query rather than by launching an account query for every event.

Ordinary checks stay silent. The hook adds short context for a redemption or a changed
actionable failure. It exits without blocking a user prompt when metadata, state or
permissions are unavailable. The installed handler has a 60-second timeout; individual
RPCs and the helper's total lifetime also have bounds.

## Compatibility and disabling

The repository includes a fake Codex server under `tests/support/` for offline
development. It simulates the protocol over standard input/output and has no live
Codex connection. The installer does not copy it, and Git release archives exclude
the entire test directory. See `tests/README.md` in a full checkout for test commands.

The hook requires Python 3.10+ and a locally available, signed-in Codex CLI exposing
`account/read`, `account/rateLimits/read`, and `account/rateLimitResetCredit/consume`.
It uses the documented app-server transport, with an existing daemon proxy when
available, and otherwise starts its own stdio helper. It starts no model thread or
turn and does not read authentication files or implement private HTTP endpoints.

This pack preference does not change Codex's account-level permissions or the
confirmation requirements of separate agent tools. If the account protocol rejects
redemption, the hook stops that attempt and backs off. It does not grant approvals.
Normal Codex hook availability, trust review and administrative restrictions apply.
Installation alone does not establish that the runtime supports automatic redemption.

To disable, rerun the global installer with `--automatic-resets disable`, review its
plan and apply it. This saves `enabled: false` and removes only the unchanged
pack-owned event registrations. The dormant script and attempt history are retained.
An already submitted redemption request cannot be recalled by disabling the feature.
Unrelated hooks and preferences are preserved.

For immediate revocation, edit `automaticResets.enabled` to `false` in
`CODEX_HOME/lugui-agent-pack/preferences.json`, preserving the other fields. The hook
reads this before checking usage and again before submitting a redemption. This also
works when an installer conflict prevents cleanup. A failed installer preflight
changes nothing: a requested disable is not applied until it succeeds. Rerun the
installer afterward to remove its registrations.

Protocol references: [Codex hooks](https://learn.chatgpt.com/docs/hooks) and
[Codex app-server](https://learn.chatgpt.com/docs/app-server).
