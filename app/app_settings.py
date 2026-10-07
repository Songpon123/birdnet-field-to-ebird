"""Per-user app settings (e.g. the user's own xeno-canto API key) in data/settings.json — private, never share."""

import json
import os

from paths import SETTINGS_FILE


def load_settings(path=None):
    try:
        return json.loads((path or SETTINGS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(changes, path=None):
    path = path or SETTINGS_FILE
    settings = dict(load_settings(path), **changes)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    os.chmod(temporary, 0o600)            # มี API key: ให้อ่านได้เฉพาะเจ้าของ
    os.replace(temporary, path)
