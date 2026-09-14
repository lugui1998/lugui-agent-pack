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
    "source_v1.json": "6436cb5020fcf4d98cdfc9855429d5526c701a6822bd01b7eba4c8fa6010512a",
    "source_v2.json": "5a6b6ab962747e1bab1323320268520e6725923bce75fa8c2d06d9d0a725cc51",
    "target_constraints.json": "d6fd1f49f49eccc5d303b1969d20b683454ff00ca097f6f4575d16ffce81db80",
    "check_migration.py": "312cecaab5a5667be1f24c0dddcbea932b213caabebf9f80fedca7d7a30080cb",
}

def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def check(workspace):
    root = Path(workspace).resolve()
    for name, expected in PROTECTED_SHA256.items():
        assert expected != "TO_BE_FILLED"
        assert sha256(ROOT / "workspace" / name) == expected, f"fixture protected {name} changed"
        assert sha256(root / name) == expected, f"candidate modified protected {name}"
    plan = json.loads((root / "MIGRATION_PLAN.json").read_text(encoding="utf-8"))
    assert plan["selected_protocol"] in {"v2", "v2_envelope"}, "selected_protocol must identify the v2 envelope"
    assert plan["target_gateway"] == "2.3"
    assert plan["required_capability"] == "envelope_v2"
    scopes = plan["source_scopes"]
    if isinstance(scopes, list):
        assert len(scopes) == 2 and all(isinstance(item, dict) for item in scopes), "two source scope records required"
        scopes = {item["source"]: item["target_identity"] for item in scopes}
    assert isinstance(scopes, dict) and set(scopes) == {"source_v1.json", "source_v2.json"}, "both source scopes required"
    assert all(value in {"gateway 2.3", "target gateway 2.3"} for value in scopes.values()), "source scopes must identify target 2.3"
    assert plan["acceptance_shape"] == {"protocol": "v2", "request_id": "request-id", "body": "body"}
    assert plan["validation_command"] == "python check_migration.py ."
    assert isinstance(plan["disagreement_resolution"], str) and plan["disagreement_resolution"].strip()
    assert (root / "EVAL_REPORT.md").read_text(encoding="utf-8").strip()
    with tempfile.TemporaryDirectory(prefix="conflicting-contracts-oracle-") as temp:
        candidate = Path(temp)
        for name in PROTECTED_SHA256:
            shutil.copy2(ROOT / "workspace" / name, candidate / name)
        for name in ("migration.py", "MIGRATION_PLAN.json", "EVAL_REPORT.md"):
            shutil.copy2(root / name, candidate / name)
        subprocess.run([sys.executable, str(candidate / "check_migration.py"), str(candidate)], check=True)

if __name__ == "__main__":
    try:
        check(sys.argv[1])
    except Exception as error:
        print(f"conflicting_contracts oracle failed: {error}", file=sys.stderr)
        raise SystemExit(1)
