#!/usr/bin/env sh
# Keep shell behavior identical to Windows by delegating to the Python installer.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$script_dir/install.py" "$@"
