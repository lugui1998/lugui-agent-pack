# Offline reset tests

The automatic-reset tests use a small fake Codex app-server over standard input and
output. It returns scripted account, quota and redemption responses and records
simulated requests in a temporary directory. It does not open a network port, read
credentials, or contact Codex.

## Run

From a repository checkout with Python 3.11+ (or 3.10 with `tomli` installed):

```sh
python -I -B -m unittest discover -s tests -p "test_usage_quota_check.py" -v
python -I -B -m unittest discover -s tests -p "test_install.py" -v
python -I -B -m unittest discover -s tests -p "test_reset_install.py" -v
python -I -B -m unittest discover -s tests -p "test_release_archive.py" -v
```

The hook tests load the production hook inside an isolated runner. That runner
replaces Codex executable discovery and process creation with a strict mapping to
the fake server. Unexpected process commands fail; they cannot fall back to the
real CLI. Each case uses a temporary `CODEX_HOME` and simulated account data.
Installer tests use temporary homes and projects and never execute installed hooks.
The archive test uses a disposable local Git repository with credentials and hooks
disabled to verify that release archives omit the test directory.

The fake server belongs to the test suite. Do not register it as a user hook or point
an installed hook at it. The normal installer copies only its declared payload;
it does not copy this directory. `.gitattributes` excludes `tests/` from Git release
archives. Full Git clones retain the tests for development.

These tests establish behavior against simulated protocol responses. They do not
establish compatibility with a live Codex service, hook trust behavior, or recovery
after an operating-system crash or power loss.

## Recorded result

On October 20, 2026, with Python 3.10 on Windows:

- 21 hook tests passed using only the fake server.
- Seven reset installer tests passed with process and network calls blocked.
- The archive-exclusion test passed using a disposable local Git repository.
- All 27 existing installer tests passed across the initial run and targeted
  reruns. Six checks initially blocked by sandbox restrictions on Windows
  links/junctions and Git's MSYS subprocess passed when rerun outside the sandbox
  with the user's authorization. Those reruns used temporary homes, disabled
  automatic resets, disabled Git hooks and allowed only local Git file transport.

The sandbox's default temporary directory also denied atomic file replacement.
The successful runs used a temporary directory inside the writable workspace,
selected with `TMPDIR`, `TEMP` and `TMP`. No production reset logic was changed to
accommodate these environment restrictions.
