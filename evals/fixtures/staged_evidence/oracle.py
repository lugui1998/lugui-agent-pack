#!/usr/bin/env python3
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROTECTED_SHA256 = {
    "current_record.json": "e5efa6c283109dd0523bee57d37343c3ccec40925bd3f79ef4e9c1de40bab185",
    "archive_lookup.py": "b0ccd4aa50068e304a77749a87a503eed04ef56589259445cc827e038a6d69df",
    "check_decision.py": "4886b5d20293be3d39b5ded46a12da3665134b956d400a2623c061eb277e3d59",
}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(workspace):
    root = Path(workspace).resolve()
    for name, expected in PROTECTED_SHA256.items():
        assert expected != "TO_BE_FILLED"
        assert sha256(ROOT / "workspace" / name) == expected, f"protected {name} changed"
        assert sha256(root / name) == expected, f"candidate modified protected {name}"
    with tempfile.TemporaryDirectory(prefix="staged-evidence-oracle-") as temp:
        candidate = Path(temp)
        for name in ("current_record.json", "archive_lookup.py", "check_decision.py"):
            shutil.copy2(ROOT / "workspace" / name, candidate / name)
        shutil.copy2(root / "DECISION.json", candidate / "DECISION.json")
        shutil.copy2(root / "EVAL_REPORT.md", candidate / "EVAL_REPORT.md")
        subprocess.run([sys.executable, str(candidate / "check_decision.py"), str(candidate / "DECISION.json")], check=True)


if __name__ == "__main__":
    try:
        check(sys.argv[1])
    except Exception as error:
        print(f"staged_evidence oracle failed: {error}", file=sys.stderr)
        raise SystemExit(1)
