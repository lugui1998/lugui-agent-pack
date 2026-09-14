from pathlib import Path


def destination(root, user_name):
    candidate = Path(root) / user_name
    if not str(candidate).startswith(str(root)):
        raise ValueError("outside root")
    return candidate
