"""Small helpers for JSON files that must never be left half-written."""

import json
import os
import tempfile
from pathlib import Path


def load_json(path, what="settings", fallback="the defaults"):
    """Read a JSON file.

    Returns (data, error). A missing file gives (None, ""). An unreadable or
    corrupt file is renamed to <name>.corrupt (kept for inspection) and gives
    (None, <explanation>).
    """

    path = Path(path)

    if not path.exists():
        return None, ""

    try:
        return json.loads(path.read_text(encoding="utf-8")), ""

    except (OSError, ValueError) as error:
        try:
            os.replace(path, str(path) + ".corrupt")
            kept = f" It was kept as {path.name}.corrupt."
        except OSError:
            kept = ""

        return None, (
            f"Your {what} file could not be read ({error}). "
            f"I'm using {fallback}.{kept}"
        )


def save_json(path, data):
    """Write JSON atomically: temp file first, then swap it into place."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    handle, temp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")

    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2, sort_keys=True)
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
