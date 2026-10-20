# Installer template: literal paths are rendered when installed; do not run this source file.
# The native Python child inherits stdin directly, preserving the hook event bytes.
$ErrorActionPreference = 'Stop'
& @@PYTHON@@ @@HOOK@@ '--codex-home' @@CODEX_HOME@@
exit $LASTEXITCODE
