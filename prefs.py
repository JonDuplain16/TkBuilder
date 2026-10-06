"""TkBuilder's own preferences (not part of any project).

Stored as JSON in %APPDATA%/TkBuilder/prefs.json (~/.config/TkBuilder on
other systems).  The folder can be overridden with the environment variable
TKBUILDER_CONFIG_DIR; in test mode (TKBUILDER_NO_DIALOGS=1) a temporary
folder is used so automated runs never touch the user's real settings.
"""

import json
import os
import tempfile

MAX_RECENT = 10

DEFAULTS = {
    "editor_mode": "light",     # "light" | "dark"
    "recent": [],               # most recent first, absolute paths
    "autosave": True,           # save a recovery copy every minute
    "dpi_aware": True,          # sharp text on high-resolution screens
}


def config_dir():
    folder = os.environ.get("TKBUILDER_CONFIG_DIR")
    if not folder:
        if os.environ.get("TKBUILDER_NO_DIALOGS"):
            folder = os.path.join(tempfile.gettempdir(), "tkbuilder_test_config")
        else:
            base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
            folder = os.path.join(base, "TkBuilder")
    return folder


def _path(name):
    return os.path.join(config_dir(), name)


def autosave_path():
    return _path("autosave.tkb")


def autosave_info_path():
    return _path("autosave.json")


def load():
    """Return the preferences dict (defaults for anything missing/broken)."""
    prefs = json.loads(json.dumps(DEFAULTS))
    try:
        with open(_path("prefs.json"), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            prefs.update({k: v for k, v in data.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    if not isinstance(prefs.get("recent"), list):
        prefs["recent"] = []
    return prefs


def save(prefs):
    """Write the preferences; returns False (silently) if that isn't possible."""
    try:
        os.makedirs(config_dir(), exist_ok=True)
        tmp = _path("prefs.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(prefs, f, indent=2)
        os.replace(tmp, _path("prefs.json"))
        return True
    except OSError:
        return False


def add_recent(prefs, path):
    """Move `path` to the top of the recent-files list."""
    path = os.path.abspath(path)
    norm = os.path.normcase(path)
    recent = [p for p in prefs.get("recent", []) if os.path.normcase(p) != norm]
    prefs["recent"] = ([path] + recent)[:MAX_RECENT]


def remove_recent(prefs, path):
    norm = os.path.normcase(os.path.abspath(path))
    prefs["recent"] = [p for p in prefs.get("recent", []) if os.path.normcase(p) != norm]
