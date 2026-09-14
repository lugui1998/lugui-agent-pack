[CmdletBinding()]
param(
    [ValidateSet('Project','Global')][string]$Scope = 'Project',
    [string]$TargetProject = '',
    [ValidateSet('Ask','Append','Overwrite','Skip')][string]$Instructions = 'Ask',
    [ValidateSet('Preserve','Merge','Replace')][string]$Config = 'Merge',
    [ValidateSet('Preserve','Merge','Replace')][string]$GlobalDefaults = 'Preserve',
    [ValidateSet('Skip','Install')][string]$Skills = 'Skip',
    [switch]$DryRun, [switch]$Apply, [switch]$Update
)
if ($DryRun -and $Apply) { throw 'Choose either -DryRun or -Apply.' }
$python = Get-Command python3.10 -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python -ErrorAction Stop }
$arguments = @((Join-Path $PSScriptRoot 'install.py'), '--scope', $Scope.ToLowerInvariant(), '--instructions', $Instructions.ToLowerInvariant(), '--config', $Config.ToLowerInvariant(), '--global-defaults', $GlobalDefaults.ToLowerInvariant(), '--skills', $Skills.ToLowerInvariant())
if ($TargetProject) { $arguments += @('--target-project', $TargetProject) }
if ($DryRun) { $arguments += '--dry-run' }
if ($Apply) { $arguments += '--apply' }
if ($Update) { $arguments += '--update' }
& $python.Source @arguments
exit $LASTEXITCODE
