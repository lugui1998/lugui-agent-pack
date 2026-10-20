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
python -I -B -m unittest discover -s tests -p "test_hook_trust.py" -v
python -I -B -m unittest discover -s tests -p "test_release_archive.py" -v
```

The hook tests load the production hook inside an isolated runner. That runner
replaces Codex executable discovery and process creation with a strict mapping to
the fake server. Unexpected process commands fail; they cannot fall back to the
real CLI. Each case uses a temporary `CODEX_HOME` and simulated account data.
Installer tests use temporary homes and projects and never execute installed hooks.
Hook-trust tests use simulated metadata and a separate fake trust server. They cover
scoped trust writes and the opt-in gate without contacting the real Codex CLI.
The Windows launcher execution test runs a rendered PowerShell launcher with a
harmless temporary Python child, checking exact input bytes, arguments and exit status.
It never loads the production quota hook.
The archive test uses a disposable local Git repository with credentials and hooks
disabled to verify that release archives omit the test directory.

The fake server belongs to the test suite. Do not register it as a user hook or point
an installed hook at it. The normal installer copies only its declared payload;
it does not copy this directory. `.gitattributes` excludes `tests/` from Git release
archives. Full Git clones retain the tests for development.

These tests establish behavior against simulated protocol responses. They do not
establish compatibility with a live Codex service, live hook activation, or recovery
after an operating-system crash or power loss.

## Recorded result

### Installer hook trust

On October 20, 2026, all 67 Windows installer, reset installer, hook-trust and
release-archive tests passed outside the sandbox. The 13 trust tests also passed
after the final protocol-parser changes. They cover exact hook matching, saved
opt-in, unchanged payloads, backup and version checks, preservation of unrelated
trust and disabled settings, manual fallback and bounded transport failures.
Fresh decline, unresolved opt-in, dry runs, project installs and failed applies
make no trust request.

On Linux, the same selection finished with 59 passing cases and eight skips
across the combined run and a focused rerun. Five cases require Windows; three
require Git, which the cached container lacks. The initial container's non-executable
temporary mount blocked one executable-discovery fixture. All 13 trust tests
passed after allowing executable temporary files. The container retained its
read-only source mount, disabled network and absence of Codex account files.
No production code changed for that environment restriction.

The tests used fake trust APIs and a local fixture process. They performed no live
trust write, quota-hook execution or account/reset request. Independent static
review found no outstanding security blocker. Live activation remains unverified.

### Earlier reset-policy verification

On October 20, 2026, with Python 3.10 on Windows:

- All 61 hook and policy tests passed using the fake server or pure policy functions.
  These cover the natural renewal wait, strict expiration exception, usage and
  expiration boundaries, both quota windows, fresh verification, uncertain retries,
  recovery and polling deadlines. They also cover the credit reserve, explicit
  response helper, consent expiration and binding, concurrent responses, status
  notices, log rotation and redirected paths. One hard-link case required a
  successful rerun outside the sandbox.
- All 26 reset installer tests passed, covering the readable Windows launcher,
  migration from owned encoded registrations, modified files, backups and
  platform-specific installation, custom waiting periods and credit reserves,
  numeric validation and older preferences.
  The launcher test uses the harmless child described above; the other cases block
  process and network calls.
- The archive-exclusion test passed using a disposable local Git repository.
- All 27 existing installer tests passed in the combined installer run
  outside the sandbox with the user's authorization. Sandbox restrictions had blocked Windows
  links/junctions, Git's MSYS subprocess and the launcher execution test.
  Those existing installer checks used temporary homes, disabled
  automatic resets, disabled Git hooks and allowed only local Git file transport.

On Linux, 61 offline hook and policy tests, 21 reset installer tests and 25 existing
installer tests passed: 107 passed and eight skipped out of 115 cases.
Five Windows-only cases were skipped, as were two existing installer cases and
the archive check because the container lacked Git.
These ran in a disposable container using a locally cached image, with networking
disabled, a read-only repository mount and no Codex account files.

The sandbox's default temporary directory also denied atomic file replacement.
The successful runs used a temporary directory inside the writable workspace,
selected with `TMPDIR`, `TEMP` and `TMP`. No production reset logic was changed to
accommodate these environment restrictions.
