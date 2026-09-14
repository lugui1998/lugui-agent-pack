#!/usr/bin/env python3
import importlib.util
import json
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
spec = importlib.util.spec_from_file_location("migration", root / "migration.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
cases = [("r-1", {"amount": 3}), ("r-2", ["x", 4])]
for request_id, payload in cases:
    actual = module.encode_request(request_id, payload)
    expected = {"protocol": "v2", "request_id": request_id, "body": payload}
    assert actual == expected, f"wrong v2 envelope: {actual!r}"
print("migration checker passed")
