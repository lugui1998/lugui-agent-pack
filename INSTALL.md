# Installing Lugui Codex Agent Kit

This repository contains Codex instructions, custom agents, project defaults, and the `stop-slop`
skill as an upstream Git submodule.

## Ask Codex to install it

Project installation:

```text
Install the Lugui Codex Agent Kit on this project from
https://github.com/lugui1998/lugui-agent-pack
into this project.

Read INSTALL.md and kit.json first. Run the installer in dry-run mode, show me
the proposed changes, preserve existing instructions and settings, and ask for
confirmation before applying changes. If an AGENTS.md already exists, ask me
whether to append, overwrite, or skip it.
```

Global installation:

```text
Install the Lugui Codex Agent Kit globally from
https://github.com/lugui1998/lugui-agent-pack.

Inspect my CODEX_HOME and existing agent setup first. Run the installer in
dry-run mode, preserve existing settings, report conflicts, and ask for
confirmation before writing outside the current project. If an AGENTS.md
already exists, ask me whether to append, overwrite, or skip it.

Ask whether I want automatic banked resets. Explain the default criteria,
let me customize them or decline, and save my choice for future updates.
```

The agent should inspect the installer and manifest before running them. The repository's normal
`AGENTS.md` is runtime guidance; it is not a request to install itself.

## Run the installer directly

From the kit checkout, inspect every available argument with:

```text
python scripts/install.py --help
```

For a project installation with Python, replace the example target with the project directory:

```text
python scripts/install.py --scope project --target-project C:\path\to\project --instructions append --config merge --dry-run
python scripts/install.py --scope project --target-project C:\path\to\project --instructions append --config merge --apply
```

The PowerShell wrapper accepts the same choices:

```powershell
.\scripts\install.ps1 -Scope Project -TargetProject C:\path\to\project -Instructions Append -Config Merge -DryRun
.\scripts\install.ps1 -Scope Project -TargetProject C:\path\to\project -Instructions Append -Config Merge -Apply
```

On macOS or Linux, use the shell wrapper:

```sh
./scripts/install.sh --scope project --target-project /path/to/project --instructions append --config merge --dry-run
./scripts/install.sh --scope project --target-project /path/to/project --instructions append --config merge --apply
```

For a global installation, `--global-defaults preserve` is the default. Use `merge` to add missing
kit defaults while retaining pre-existing unmanaged values, or `replace` to explicitly select and
take ownership of the kit defaults. Skill installation is a separate opt-in:

```text
python scripts/install.py --scope global --instructions append --global-defaults merge --skills install --dry-run
python scripts/install.py --scope global --instructions append --global-defaults merge --skills install --apply
```

On a first global install, an interactive terminal also asks whether to enable automatic
banked resets, customize their criteria, or disable them. A noninteractive install requires
an explicit `--automatic-resets enable` or `--automatic-resets disable` choice when no saved
choice exists. An unresolved choice blocks apply. Dry-run choices are displayed but are
not saved; pass the same options when applying.

For example, to install globally with automatic resets disabled:

```text
python scripts/install.py --scope global --instructions append --automatic-resets disable --dry-run
python scripts/install.py --scope global --instructions append --automatic-resets disable --apply
```

The PowerShell equivalent is `-AutomaticResets Disable`. The shell wrapper forwards the
Python options unchanged.

## Optional automatic banked resets

The default criteria, once explicitly enabled, are:

- A banked reset expires within five days.
- Either the five-hour or weekly core quota has 0% remaining when credits are available,
  or at most 1% remaining without credits.
- Usage queries begin with a 30-minute interval and adapt within a 5–60-minute range.
  Every redemption requires fresh account, quota and reset details.

To preview enabling the feature with custom criteria:

```text
python scripts/install.py --scope global --instructions append --automatic-resets enable --reset-expiry-days 3 --reset-remaining-with-credits 0 --reset-remaining-without-credits 1 --dry-run
```

Replace `--dry-run` with `--apply` only after reviewing the plan. Installation itself does
not query the account or redeem a reset. Once an enabled hook is trusted and runs in Codex,
it can redeem a reset automatically without asking again.

| Python option | PowerShell option | Default |
| --- | --- | --- |
| `--automatic-resets` | `-AutomaticResets` | `ask` |
| `--reset-expiry-days` | `-ResetExpiryDays` | `5` |
| `--reset-remaining-with-credits` | `-ResetRemainingWithCredits` | `0` |
| `--reset-remaining-without-credits` | `-ResetRemainingWithoutCredits` | `1` |
| `--reset-min-check-minutes` | `-ResetMinCheckMinutes` | `5` |
| `--reset-initial-check-minutes` | `-ResetInitialCheckMinutes` | `30` |
| `--reset-max-check-minutes` | `-ResetMaxCheckMinutes` | `60` |
| `--reset-codex-command` | `-ResetCodexCommand` | `codex` |

`ask` preserves an existing saved choice; it prompts only when no choice exists. `preserve`
also retains saved preferences and reports an unresolved choice if none exists.
`enable` and `disable` explicitly change that choice. Individual criteria override only
their corresponding saved values. Explicit enable or custom reset options require global
scope. Project installs do not read or change these global preferences.

The installer records preferences at `CODEX_HOME/lugui-agent-pack/preferences.json`.
Enabling installs `CODEX_HOME/hooks/usage_quota_check.py` and registers it in
`CODEX_HOME/hooks.json`. Declining a new installation creates neither the hook script nor
its registration. Disabling a previously enabled installation removes the pack's two
registrations and retains the dormant script and history.

If a conflict blocks disable, no preferences have changed. To revoke opt-in immediately,
set `automaticResets.enabled` to `false` in the saved preferences file, preserving its
other fields, then resolve the installer conflict to clean up registrations. A request
already submitted to Codex cannot be recalled.

The feature requires a compatible, signed-in Codex CLI and Codex's normal hook trust
review. It does not enable Codex hooks globally, change redemption permissions, or approve
its own hook definitions. See [Automatic resets](docs/AUTOMATIC_RESETS.md) for the policy,
query schedule, compatibility requirements and limitations. The hook has been tested
against a fake Codex server, and installer checks use temporary homes. Live redemption
and Codex hook activation remain unverified.

## Safety contract

The installers follow these rules:

- Dry-run is available for every scope.
- Planning is read-only. The installer parses the source and target TOML, checks instruction
  markers, ownership, roles, and skills, and computes every change before committing anything.
  Any conflict or unresolved instruction or automatic-reset choice blocks the entire apply
  preflight. Planning and applying never run the quota hook, query account usage, or redeem
  a reset.
- When `kit.json` advertises an agent catalog, preflight checks its schema, role names, and
  model/effort agreement with the source role files. Missing or inconsistent role payloads block
  the whole apply. Legacy manifests without a catalog remain supported. This is an offline
  consistency check; account/runtime model availability is reported as unknown and must be
  verified through the documented fresh-task activation and fallback procedure.
- Existing files are never overwritten silently. A backup is created before each managed update.
- Existing `AGENTS.md` files require an explicit `append`, `overwrite`, or `skip` choice.
- `--instructions skip` also skips updates to an existing managed instruction block.
- Role files are copied byte for byte and tracked with content hashes, so line-ending conversion
  does not cause a false update on the next run.
- User-modified files become conflicts and remain untouched.
- Global `config.toml` is preserved unless `--global-defaults merge` or `--global-defaults replace`
  is supplied. `merge` permits the kit to add or update defaults it owns but preserves a
  pre-existing unmanaged model value, so it may retain the user's current model. `replace`
  explicitly opts into the kit-owned values. Check the effective runtime configuration after
  installation because session and app overrides can still take precedence. Without either
  option, global installation adds roles and instructions, plus explicitly selected optional
  features. Authentication, plugins, MCP
  servers, and unrelated skills are not changed.
- Managed configuration ownership is recorded per TOML key. A later kit update can update an
  unchanged kit-owned key while preserving unrelated user edits. A user edit to a managed key is
  a conflict in merge mode; `replace` explicitly takes ownership of the kit's keys.
- Every projected configuration is parsed again before it can be written. Root keys are kept in
  the TOML root, and ordinary `[agents]` and dotted `agents.*` layouts are supported. If a required
  nested edit would rewrite an inline `agents = { ... }` table, the installer reports a conflict
  and leaves all targets untouched.
- Instruction changes use a managed block with explicit markers. Duplicate or malformed markers
  are conflicts; unmarked duplicate kit text produces a notice.
- Installed instruction blocks record the selected scope and absolute target. Automatic-reset
  preferences survive updates. Existing unrelated JSON preferences and hook entries are
  preserved; a modified or ambiguous pack-owned hook entry blocks the update.
- The installers do not remove files automatically; manual cleanup remains explicit.
- State format v2 uses checkout-relative source identities and per-key configuration records.
  Compatible v1 state is read and upgraded conservatively. Moving the kit checkout therefore does
  not make all installed roles appear obsolete.
- If an external operation fails after commit starts, the installer stops and saves ownership for
  operations that already completed. Review the reported partial result before retrying.
- The installer reports duplicate role names and layered global/project instructions so they can
  be reviewed without treating every overlap as a destructive conflict.
- Manifest source paths must be relative descendants of the kit checkout, and manifest leaf names
  cannot contain traversal or path separators. Before apply, resolved target ancestors must remain
  inside the selected project or `CODEX_HOME`. Redirected `.codex`, agent, configuration, and state
  paths are conflicts. An explicitly selected project or home symlink is resolved once and treated
  as the intended root. A verified global skill link may point from its safe leaf to the kit source;
  its parent directory must still remain inside `CODEX_HOME`.

## Scopes

`project` installs agents under `.codex/agents`, merges the kit instructions into the target
project's `AGENTS.md`, and merges the kit's Luna defaults into `.codex/config.toml`. Existing
`model`, `model_reasoning_effort`, and `[agents]` values are protected in `--config merge` mode;
use `--config preserve` or `--config replace` deliberately. Merge adds missing keys, adopts exact
matches, and updates only unchanged keys previously owned by the kit. Unrelated TOML and comments
remain.

`global` installs agents under `CODEX_HOME/agents`, merges the kit instructions into the global
`CODEX_HOME/AGENTS.md`, and can link the upstream skill in the personal skill location. The link is
a directory symlink where supported, with a directory-junction fallback on Windows.

Use `--dry-run` first. `--apply` commits the displayed plan only when the complete preflight has no
blockers. `--update` also reports stale managed role files without deleting them. The installer
reports the effective file defaults; session and app selections can still override them. Skill
setup is explicit (`--skills install`) and never deletes or replaces an existing skill path.
Repeating a skill install is idempotent after the installer verifies that the existing global link
or project submodule points to the expected source. An uninitialized but correctly configured
project submodule is initialized instead of added again.

Project installation is the safest default. Global installation changes the user's Codex home and
must be explicitly confirmed.

When appending, review the resulting `AGENTS.md` carefully. The kit's delegation, role-routing,
and research instructions may duplicate or conflict with instructions already in the file.

## Verify the effective setup

From the kit checkout, inspect the installed target with the supported local Codex CLI:

```sh
python scripts/runtime_probe.py --cwd "/absolute/path/to/target" --start-thread
```

The probe starts an ephemeral metadata-only task, disables configured MCP servers for that task,
sends no model turn, and stops its own process. Compare `configuration.effective.model` and
`model_reasoning_effort` with `thread.model` and `thread.reasoning_effort`. Inspect the layer origins
and disabled reasons if project settings did not load. For a global-default check, select a
directory outside projects with their own instructions or configuration.

This checks configuration activation, not task quality or every child role. Pre-turn instruction
lists can omit project guidance loaded later. A fresh evaluated task and its actual session
metadata are needed to verify what reached the model; see `evals/README.md` and the validation
record. Explicit app/session model choices can override the installed default.
