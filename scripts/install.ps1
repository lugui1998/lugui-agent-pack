[CmdletBinding()]
param(
    [ValidateSet('Project','Global')][string]$Scope = 'Project',
    [string]$TargetProject = '',
    [ValidateSet('Ask','Append','Overwrite','Skip')][string]$Instructions = 'Ask',
    [ValidateSet('Preserve','Merge','Replace')][string]$Config = 'Merge',
    [ValidateSet('Preserve','Merge','Replace')][string]$GlobalDefaults = 'Preserve',
    [ValidateSet('Skip','Install')][string]$Skills = 'Skip',
    [ValidateSet('Ask','Enable','Disable','Preserve')][string]$AutomaticResets = 'Ask',
    [double]$ResetExpiryDays,
    [double]$ResetRemainingWithCredits,
    [double]$ResetRemainingWithoutCredits,
    [int]$ResetMinCheckMinutes,
    [int]$ResetMaxCheckMinutes,
    [int]$ResetInitialCheckMinutes,
    [string]$ResetCodexCommand,
    [switch]$DryRun, [switch]$Apply, [switch]$Update
)
if ($DryRun -and $Apply) { throw 'Choose either -DryRun or -Apply.' }
$python = Get-Command python3.10 -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python -ErrorAction Stop }
$arguments = @((Join-Path $PSScriptRoot 'install.py'), '--scope', $Scope.ToLowerInvariant(), '--instructions', $Instructions.ToLowerInvariant(), '--config', $Config.ToLowerInvariant(), '--global-defaults', $GlobalDefaults.ToLowerInvariant(), '--skills', $Skills.ToLowerInvariant(), '--automatic-resets', $AutomaticResets.ToLowerInvariant())
$resetOptions = @{
    ResetExpiryDays = '--reset-expiry-days'
    ResetRemainingWithCredits = '--reset-remaining-with-credits'
    ResetRemainingWithoutCredits = '--reset-remaining-without-credits'
    ResetMinCheckMinutes = '--reset-min-check-minutes'
    ResetMaxCheckMinutes = '--reset-max-check-minutes'
    ResetInitialCheckMinutes = '--reset-initial-check-minutes'
    ResetCodexCommand = '--reset-codex-command'
}
foreach ($option in $resetOptions.GetEnumerator()) {
    if ($PSBoundParameters.ContainsKey($option.Key)) {
        $value = [System.Convert]::ToString($PSBoundParameters[$option.Key], [System.Globalization.CultureInfo]::InvariantCulture)
        $arguments += @($option.Value, $value)
    }
}
if ($TargetProject) { $arguments += @('--target-project', $TargetProject) }
if ($DryRun) { $arguments += '--dry-run' }
if ($Apply) { $arguments += '--apply' }
if ($Update) { $arguments += '--update' }
& $python.Source @arguments
exit $LASTEXITCODE
