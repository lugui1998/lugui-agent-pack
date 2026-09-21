# Validated configuration scope

Reviewed 2026-09-12 against Windows Codex CLI `0.154.0-alpha.6.2`.

All 18 generated roles match the catalog name, description, model, and effort. The generator checks model/effort enums, every referenced prompt and role, availability cycles, the main/helper settings, and depth. No stale role descriptions were found in the final static audit. Four generator tests and the drift check passed.

`runtime-thread.json` verifies project-origin Luna Medium and Luna Low helper defaults. `nesting-config-probe.json` verifies depth 2, and actual nested research children demonstrate that path. `installed-activation.json` verifies fresh project and global replacement activation with 18 declarations; merge preserves an unmanaged model, effort, and sentinel. App/session overrides remain authoritative.

`runtime-role-activation-evidence.json` verifies actual role turns spanning Luna, Terra, Sol and Astra. This is representative activation, not a claim of 18 live role turns. Named role files pin settings, and observed roles used their expected models. An explicit conflicting requested spawn-model override was not separately captured in an export; the kit avoids depending on such an override. The full-history/named-role rejection and an actual unavailable-model activation followed by an appropriate fallback are separately recorded in scenario observations.

Offline checks cannot guarantee account access. The installer rejects missing source definitions and inconsistent model/effort declarations, reports conflicting global/project defaults and instructions, detects duplicate and obsolete roles, and states runtime availability as unknown. `installer-update-preflight.json` verifies those notices without target writes; live fallback evidence verifies handling after a real activation error. The real global configuration was not altered.

The supported evidence boundary is this CLI on Windows. Other versions require fresh configuration checks, and native non-Windows installation was not executed. Role read-only settings are defaults, not hard isolation: the parent live permission override was reapplied in the role probe. `--ignore-user-config` did not exclude global `AGENTS.md`, as established by `runtime-context-audit.json`.
