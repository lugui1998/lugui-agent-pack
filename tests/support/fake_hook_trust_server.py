"""Offline hook-trust API double; never loads or executes a hook.

Can also be started ONLY as this test fixture for stdio transport checks.
"""

import copy
import hashlib
import json
import os
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


def encode_toml(document, prefix=()):
    lines = []
    if prefix:
        lines.append("[" + ".".join(json.dumps(key) for key in prefix) + "]")
    for key, value in document.items():
        if not isinstance(value, dict):
            lines.append(json.dumps(key) + " = " + json.dumps(value))
    for key, value in document.items():
        if isinstance(value, dict):
            lines.extend(encode_toml(value, (*prefix, key)))
    return lines


class FakeTrustServer:
    def __init__(self, command, home):
        self.home = Path(home)
        self.config = self.home / "config.toml"
        self.calls = []
        self.on_request = None
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def document(self):
        return tomllib.loads(self.config.read_text(encoding="utf-8")) if self.config.exists() else {}

    def version(self):
        return hashlib.sha256(self.config.read_bytes() if self.config.exists() else b"").hexdigest()

    def hook_list(self):
        doc = json.loads((self.home / "hooks.json").read_text(encoding="utf-8"))
        state = self.document().get("hooks", {}).get("state", {})
        hooks = []
        for event, groups in doc["hooks"].items():
            for index, group in enumerate(groups):
                for handler_index, handler in enumerate(group["hooks"]):
                    key = f"fake-user:{event}:{index}:{handler_index}"
                    payload = json.dumps(handler) + (self.home / "hooks" / "usage_quota_check.py").read_text(encoding="utf-8")
                    current_hash = "fake-opaque-" + hashlib.sha256(payload.encode()).hexdigest()
                    saved = state.get(key, {})
                    hooks.append({
                        "key": key, "currentHash": current_hash,
                        "eventName": event[0].lower() + event[1:],
                        "handlerType": handler["type"],
                        "command": handler.get("commandWindows", handler["command"]) if os.name == "nt" else handler["command"],
                        "async": False, "matcher": group.get("matcher"),
                        "timeoutSec": handler.get("timeout", 60),
                        "source": "user", "sourcePath": str(self.home / "hooks.json"),
                        "isManaged": False, "pluginId": None,
                        "enabled": saved.get("enabled", True),
                        "trustStatus": "trusted" if saved.get("trusted_hash") == current_hash else "untrusted",
                    })
        return {"data": [{"cwd": str(self.home), "hooks": hooks, "errors": [], "warnings": []}]}

    def request(self, method, params):
        if method not in {"initialize", "hooks/list", "config/read", "config/batchWrite"}:
            raise AssertionError("forbidden method in offline trust fixture: " + method)
        self.calls.append((method, copy.deepcopy(params)))
        if self.on_request:
            replacement = self.on_request(method, params)
            if replacement is not None:
                return replacement
        if method == "initialize":
            return {"userAgent": "offline-fixture"}
        if method == "hooks/list":
            return self.hook_list()
        if method == "config/read":
            return {"config": self.document(), "origins": {}, "layers": [{
                "name": {"type": "user", "file": str(self.config)},
                "version": self.version(), "config": self.document(),
            }]}
        if params["expectedVersion"] != self.version():
            raise ValueError("offline fixture config conflict")
        assert params["filePath"] == str(self.config)
        assert params["reloadUserConfig"] is True
        assert len(params["edits"]) == 1
        edit = params["edits"][0]
        assert edit["keyPath"] == "hooks.state" and edit["mergeStrategy"] == "upsert"
        document = self.document()
        state = document.setdefault("hooks", {}).setdefault("state", {})
        for key, fields in edit["value"].items():
            assert set(fields) == {"trusted_hash"}
            state.setdefault(key, {}).update(fields)
        self.config.write_text("\n".join(encode_toml(document)) + "\n", encoding="utf-8")
        return {"status": "ok", "filePath": str(self.config), "version": self.version()}


def main():
    # No live Codex command is accepted or delegated by this fixture.
    mode, directory = sys.argv[1:]
    server = FakeTrustServer("offline", Path(directory))
    for line in sys.stdin:
        message = json.loads(line)
        if "id" not in message:
            continue
        if mode == "timeout":
            import time
            time.sleep(10)
            continue
        if mode == "malformed":
            print("not-json", flush=True)
            continue
        if mode == "duplicate-key":
            print('{"id":1,"id":1,"result":{}}', flush=True)
            continue
        if mode == "wrong-id":
            print(json.dumps({"id": 999, "result": {}}), flush=True)
            continue
        if mode == "scalar-result":
            print(json.dumps({"id": message["id"], "result": []}), flush=True)
            continue
        if mode == "server-request":
            print(json.dumps({"id": 999, "method": "account/login/start"}), flush=True)
            continue
        try:
            if mode == "unknown-method":
                raise ValueError("unsupported")
            result = server.request(message["method"], message["params"])
            response = {"id": message["id"], "result": result}
        except Exception:
            response = {"id": message["id"], "error": {"code": -32601, "message": "offline unsupported"}}
        print(json.dumps(response), flush=True)


if __name__ == "__main__":
    main()
