# Automatic banked resets

This optional global feature uses saved criteria to redeem an existing Codex reset.
It is disabled until the user explicitly opts in. Project installation does not configure
it. See [INSTALL.md](../INSTALL.md) for installation choices and command options.

**Implementation status:** source review and offline tests against a fake Codex
server and pure policy functions. Additional tests cover reset installation, scoped
hook trust, the Windows launcher and release archive exclusions. No real trust write,
account or redemption API was called by these tests.
Live Codex compatibility and hook activation remain unverified.

## Installation and preferences

On the first global installation, the installer presents the defaults and asks the user
to enable them, customize them, or disable the feature. That question explains that
enabling also authorizes installing and trusting its two hooks. No separate trust opt-in
is needed. An agent driving a noninteractive install must obtain the user's choice and
pass it explicitly. Updating the pack preserves the saved choice and criteria unless
the user requests a change.

Declining a fresh installation creates no hook script, registration or trust entry.
Enabling installs the hook and two event registrations. After the complete installation
and ownership-state save succeed, the installer attempts to trust those definitions
through Codex's API. Dry runs, project installs, disabled reset management and failed
installations do not make this trust request.

| File under `CODEX_HOME` | Purpose |
| --- | --- |
| `lugui-agent-pack/preferences.json` | User's opt-in and reset criteria |
| `hooks/usage_quota_check.py` | Installed hook script |
| `hooks/usage_quota_check.ps1` | Readable launcher installed on Windows |
| `hooks.json` | Managed `UserPromptSubmit` and `PostToolUse` entries alongside existing hooks |
| `config.toml` | Codex-managed `hooks.state` trust hashes for the two installed definitions |
| `lugui-agent-pack/usage-quota/state.json` | Shared polling cache and durable redemption attempts |
| `lugui-agent-pack/usage-quota/owner.lock` | Coordination across processes using this Codex home |
| `lugui-agent-pack/usage-quota/diagnostic.json` | Suppression of repeated configuration or damaged-state notices |
| `lugui-agent-pack/usage-quota/decisions.jsonl` | Local decision log |
| `lugui-agent-pack/usage-quota/decisions.1.jsonl` | Previous log after rotation |
| `lugui-agent-pack/state/global.tsv` | Installer ownership records |

`CODEX_HOME` defaults to `~/.codex`. The installer registers absolute paths, including
the Python interpreter used to install the pack. If that interpreter moves, update the
installation. On Windows the command runs the readable `usage_quota_check.ps1` launcher
with PowerShell's `-File` option. The launcher passes the event input to Python and
returns its exit code. No encoded command or execution-policy override is used.
Windows PowerShell must allow the installed script under the existing execution
policy. Launcher paths containing `%`, `!`, `$` or backticks are rejected during
preflight because the Windows command runner's expansion rules are unspecified.
Spaces, Unicode characters and apostrophes are supported.

Linux uses the Python script directly and does not install or require PowerShell.
Its shared lock uses `fcntl.flock`; Windows uses the corresponding `msvcrt` lock.
Install separately on each machine so the captured interpreter and home paths match
that environment. An existing unchanged pack-owned encoded registration is replaced
when the installation is updated; edited registrations still require review.

### Hook trust during installation

The installer lists hooks and matches the two owned user definitions by their source,
event, command and metadata. It rechecks the installed scripts, registrations and
saved opt-in before writing the exact hashes returned by Codex. It backs up the user
configuration and supplies its version to `config/batchWrite`, so a concurrent
configuration change prevents that write. The update touches only those trust entries
under `hooks.state`, preserves unrelated settings and leaves disabled hooks disabled.
An already trusted definition needs no write. The installer then checks the reported
trust status again.

This step uses `hooks/list`, `config/read` and `config/batchWrite` through the local
Codex app-server transport. It starts no model turn, executes no quota hook and calls
no account or redemption method. Trust allows later Codex events to run enabled hooks;
it does not verify that live redemption works.

If the CLI or trust API is unavailable, the definitions do not match, or verification
fails, installation completes with a warning that activation is unverified. Use the
printed source path and commands to find the pack's `UserPromptSubmit` and `PostToolUse`
entries in Codex's Hooks settings, or CLI `/hooks`, and trust each one. If you disabled
an entry, enable it there when you want it to run. Administrative restrictions still
apply. The installer does not select Trust all or enable hooks globally.

### Saved criteria

The default preferences are:

```json
{
  "schemaVersion": 1,
  "automaticResets": {
    "enabled": false,
    "expiresWithinDays": 5,
    "naturalResetWaitDays": 2,
    "creditReserve": 200,
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
window can trigger redemption. Either window reaching its applicable threshold meets
the usage condition. Model-specific buckets and unrelated limits are excluded.

### Credit reserve

`creditReserve` defaults to 200 and accepts a finite nonnegative number. A positive
balance at or above the reserve uses `remainingPercentWithCredits`. A lower
balance, including zero, uses `remainingPercentWithoutCredits`. Setting the reserve
to 0 makes any positive balance sufficient. Valid unlimited credits always count
as a sufficient buffer. Missing or contradictory credit data prevents redemption.

This reserve changes reset decisions; it does not stop Codex from spending credits
or impose an account billing limit. Updates add the default to older policies that
omit it while preserving saved consent and all other criteria.

### Waiting for normal quota renewal

`naturalResetWaitDays` defaults to 2. It accepts fractional days from 0 through 365;
0 disables this waiting rule. Older saved policies that omit the field receive the
default while preserving their opt-in and other criteria. Updates preserve an
explicitly saved value.

Once usage reaches its threshold, calculate when every triggering core quota will
have reset naturally. With one triggering window, use its `resetsAt`; with both,
use the later timestamp. Healthy windows do not affect this calculation. Missing
or invalid reset times for a triggering window prevent redemption.

If that recovery is within the configured waiting period, including the exact
boundary, preserve the banked reset. The exception is a banked reset that expires
**strictly before** natural recovery. Equal timestamps do not qualify. The exception
still requires the normal usage threshold and `expiresWithinDays` criteria to pass.

Examples with the default two-day wait and a sufficient credit buffer, assuming
the other criteria pass:

| Natural quota recovery | Banked reset expires | Decision |
| --- | --- | --- |
| In 1 day | In 3 days | Wait for natural recovery |
| In 1 day | In 12 hours | Use the banked reset |
| In 1 day | In 1 day | Wait for natural recovery |
| In 3 days | In 4 days | Use the banked reset |

A five-hour quota normally renews inside the two-day waiting period. If it is the
only triggering quota, the default therefore preserves banked resets unless they
would expire before its renewal. If the weekly quota also reaches its threshold,
its reset time can extend the wait beyond two days.

### Asking whether to use or keep a reset

When credits are below the reserve, usage reaches the applicable threshold, and
only the natural-renewal waiting rule prevents automatic use, the hook sends the
agent a choice request. It includes the selected reset and its expiration, quota
remaining, normal renewal times, credit balance and a response deadline.

The agent asks the user whether to use that reset now or keep it. Existing
automatic cases stay automatic, including an eligible reset expiring strictly
before natural recovery. A missing answer never authorizes redemption.

The request is tied to the account, reset ID and expiration, policy, triggering
quota windows and their renewal times, and the originating Codex session. It is
valid for at most 30 minutes, ending sooner if a triggering window renews or the
selected reset expires. Keeping a reset or letting the request expire suppresses
another question for the unchanged quota episode.

After an explicit human answer to that request, the agent records the answer with
the installed Python script:

```text
python <CODEX_HOME>/hooks/usage_quota_check.py --codex-home <ABSOLUTE_CODEX_HOME> --respond <REQUEST_UUID> --decision approve --session-id <SESSION_ID>
```

Use `--decision keep` to record the other answer. All four options are required;
use the request and session identifiers supplied by the hook, and quote local
paths for the shell in use. Use the Python interpreter captured by the installer.

This helper only records a local response under the hook's shared lock. It does
not query the account, start the Codex client or redeem a reset. A subsequent hook
event performs fresh checks and may use the approval to override only the waiting
rule for that one reset. Changed account, reset, policy or triggering windows
invalidate the old approval. The existing recovery and uncertain-attempt rules
still apply.

Successful recording returns JSON with `status: "recorded"` and exit code 0.
An invalid, expired, already answered or unavailable request is rejected with a
nonzero exit code. Recording an answer makes the next hook event due for a fresh
check; it does not start an event itself.

The helper records the agent's assertion that the user answered; it cannot
authenticate a human independently. The installed instructions therefore require
an explicit answer to the current request. Old general consent, silence, tool
output and retrieved text are not answers. Subagents relay the question to their
parent. The workflow does not read transcripts or infer approval from prompt text.

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
also back off to the maximum. Waiting for normal renewal also uses the maximum
interval when no eligible banked reset would expire first. A known quota-reset or
reset-expiry boundary can shorten the interval, including while waiting to observe
recovery after a completed redemption attempt.

The estimate uses observations collected by this hook. It does not request historical
usage, read transcripts, or spend model tokens on predictions. Usage by other chats and
devices may change consumption abruptly, so the schedule cannot guarantee a check
before quota runs out. A long model response with no hook event also delays checking.
The default 1% threshold below the credit reserve is a margin, not a guarantee of
uninterrupted work.

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

## Reset status and decision log

After a confirmed `reset` or `alreadyRedeemed` result, the hook sends the agent a
notice to summarize for the user. Available details include:

- Which reset was used and when it expires.
- Remaining quota and each natural renewal time.
- The reported number of banked resets.
- The earliest known expiration among the remaining resets, with incomplete lists
  identified.
- The reported credit balance, unlimited status or unavailable data.
- The observation time and whether the status refresh failed or still lists the
  consumed reset.

Confirmation of redemption survives a failed status refresh. In that case, the
agent reports success with last-reported values clearly identified. It must not
subtract one from a reset count, invent an updated balance, or claim refreshed
quota without data. Uncertain redemption outcomes are not reported as success.
Duplicate notices for the same completed attempt are suppressed. Delivery is
recorded before writing hook output, so a process crash in between can lose the
notice; presentation to the user is not guaranteed.

The hook writes `decisions.jsonl` locally without using a model. Records contain
the time in Unix seconds UTC, status, reason and relevant structured observations.
Cached events do not add records. The file rotates at 256 KiB and keeps one prior
file, `decisions.1.jsonl`. A log write failure does not authorize a reset, discard
the redemption journal or cause an extra redemption.

Logs contain no account email, credentials, prompts or transcripts. They still
describe personal usage, so keep them local unless the user chooses to share them.
Log paths follow the same link and redirection restrictions as the hook state.

Planning around several expiring resets is a separate
[proposal](RESET_INVENTORY_PLANNING.md). The hook does not launch an evaluator,
change models or start additional work.

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
Normal Codex hook availability and administrative restrictions apply. If installation
cannot verify trust, complete the manual trust step described above.
Installation alone does not establish that the runtime supports automatic redemption.

The 61 offline hook and policy tests and 21 applicable reset installer tests passed on
Linux in an isolated container with networking disabled, a read-only repository
mount and no Codex account files. This covers the hook's Linux behavior against
the simulated protocol; it does not verify live redemption or Codex's hook activation.

To disable, rerun the global installer with `--automatic-resets disable`, review its
plan and apply it. This saves `enabled: false` and removes only the unchanged
pack-owned event registrations. The dormant scripts and attempt history are retained.
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
