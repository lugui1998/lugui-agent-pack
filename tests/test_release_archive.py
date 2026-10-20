"""Check release exclusions using a disposable local Git repository."""

import io
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReleaseArchiveTests(unittest.TestCase):
    def test_archive_excludes_tests_and_keeps_install_payload(self):
        git = shutil.which("git")
        if git is None:
            self.skipTest("Git is required to inspect a generated source archive")

        with tempfile.TemporaryDirectory(prefix="lugui-release-test-") as directory:
            root = Path(directory).resolve()
            repo = root / "repo"
            repo.mkdir()
            empty = root / "empty"
            empty.mkdir()
            # No inherited Git configuration, repository, credentials, or hooks.
            env = {
                key: value for key, value in os.environ.items()
                if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
            }
            env.update({
                "HOME": str(empty),
                "USERPROFILE": str(empty),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ALLOW_PROTOCOL": "file",
            })

            def run(*args):
                return subprocess.run(
                    [
                        git, "-c", f"core.hooksPath={empty}",
                        "-c", "commit.gpgSign=false",
                        "-c", "user.name=Offline test",
                        "-c", "user.email=offline@example.invalid",
                        "-C", str(repo), *args,
                    ],
                    env=env, stdin=subprocess.DEVNULL, capture_output=True,
                    check=True, timeout=20,
                ).stdout

            shutil.copyfile(ROOT / ".gitattributes", repo / ".gitattributes")
            included = {
                "kit.json", "AGENTS.md", "scripts/install.py",
                "scripts/reset_install.py", "scripts/hook_trust.py",
                "scripts/hooks/usage_quota_check.py",
                "scripts/hooks/usage_quota_check.ps1",
            }
            excluded = {
                "tests/README.md", "tests/test_usage_quota_check.py",
                "tests/support/fake_codex_server.py", "tests/test_hook_trust.py",
                "tests/support/fake_hook_trust_server.py", "tests/nested/fixture.json",
            }
            # Placeholders verify archive policy without executing any payload.
            for name in included | excluded:
                path = repo / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("offline archive fixture\n", encoding="utf-8")
            run("init", f"--template={empty}")
            run("add", ".")
            run("commit", "-m", "Offline release fixture")
            payload = run("archive", "--format=zip", "HEAD")
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                names = set(archive.namelist())
            self.assertTrue(included <= names)
            self.assertFalse(any(name.startswith("tests/") for name in names))


if __name__ == "__main__":
    unittest.main()
