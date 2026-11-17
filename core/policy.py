"""Which folders Quanttum AI may touch.

File actions are only allowed inside "allowed folders". Every path is fully
resolved first (symlinks followed, ".." collapsed) and must then sit inside
an allowed folder, so tricks like ..\\..\\Windows or a symlink cannot escape.

If the permissions file is damaged, the policy fails CLOSED: no folders are
allowed until the user grants them again. It never quietly falls back to the
defaults, which could undo a restriction the user had set.
"""

import copy
import os
import sys
import uuid
from pathlib import Path

from core.actions.base import ActionError
from core.safejson import load_json, save_json

KNOWN_FOLDERS = ("documents", "desktop", "downloads")

# Windows "known folder" IDs. Asking Windows is the only reliable way to find
# these: with OneDrive backup on, Documents and Desktop are NOT in C:\Users\<you>.
_WINDOWS_FOLDER_IDS = {
    "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "downloads": "374DE290-123F-4565-9164-39C4925E467B",
}


def _windows_known_folder(name):
    """Ask Windows where a standard folder really is, or None."""

    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    parsed = uuid.UUID(_WINDOWS_FOLDER_IDS[name])
    tail = [parsed.clock_seq_hi_variant, parsed.clock_seq_low,
            *parsed.node.to_bytes(6, "big")]
    guid = GUID(parsed.time_low, parsed.time_mid, parsed.time_hi_version,
                (ctypes.c_ubyte * 8)(*tail))

    pointer = ctypes.c_wchar_p()
    result = ctypes.windll.shell32.SHGetKnownFolderPath(
        ctypes.byref(guid), 0, None, ctypes.byref(pointer))

    if result != 0:
        return None

    value = pointer.value
    ctypes.windll.ole32.CoTaskMemFree(pointer)
    return value


def known_folder(name, home=None):
    """Where Documents / Desktop / Downloads really are.

    Windows is asked first (handles OneDrive redirection). If that fails, or
    on other systems, fall back to <home>/<Name>. When a home is passed in
    explicitly (tests), Windows is not consulted.
    """

    name = name.casefold()

    if home is None and sys.platform == "win32":
        try:
            found = _windows_known_folder(name)
            if found:
                return Path(found)
        except Exception:
            pass

    return (Path(home) if home else Path.home()) / name.capitalize()


def default_user_folders(home=None):
    folders = [known_folder(name, home) for name in KNOWN_FOLDERS]
    return [folder for folder in folders if folder.is_dir()]


def system_protected_dirs():
    """Folders that can never be made an allowed folder."""

    found = []

    for variable in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)",
                     "ProgramData"):
        value = os.environ.get(variable)
        if value:
            found.append(Path(os.path.realpath(value)))

    return found


def _real(path):
    return Path(os.path.realpath(path))


def _inside(child, root):
    return child == root or root in child.parents


class PermissionsStore:
    """The permissions file (allowed folders, approved apps), shared safely.

    One store object is shared by FolderPolicy and AppPolicy so that saving
    one section never overwrites the other. Unknown sections are preserved.
    A damaged file fails CLOSED: it is set aside as <name>.corrupt and every
    section starts out EXPLICITLY empty, so nothing is allowed until the user
    grants it again (it never falls back to defaults).
    """

    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.error = ""
        self._data = {}
        self._load()

    def _load(self):
        if self.path is None:
            return

        data, error = load_json(
            self.path, "permissions", "no permissions until you grant them again")

        if not error and data is not None and not isinstance(data, dict):
            error = ("Your permissions file is not in the expected format. "
                     "No permissions are granted until you add them again.")

        if error:
            self.error = error
            self._data = {"allowed_folders": [], "allowed_apps": {}}
            return

        if data is not None:
            self._data = data

    def has(self, key):
        return key in self._data

    def get(self, key, default=None):
        return copy.deepcopy(self._data.get(key, default))

    def set(self, key, value):
        """Change one section and save. Undone again if saving fails."""

        snapshot = copy.deepcopy(self._data)

        try:
            self._data[key] = value
            self._data["version"] = 1

            if self.path is not None:
                save_json(self.path, self._data)
        except BaseException:
            self._data = snapshot
            raise


class FolderPolicy:
    def __init__(self, path=None, defaults=(), home=None, protected=None,
                 store=None):
        self.store = store if store is not None else PermissionsStore(path)
        self._defaults = [Path(d) for d in defaults]
        self._explicit_home = home is not None
        self.home = Path(home) if home else Path.home()
        self._protected = (list(protected) if protected is not None
                           else system_protected_dirs())

    @property
    def error(self):
        return self.store.error

    # ---- storage ----------------------------------------------------

    def _configured(self):
        """The saved folder list, or None if the user never chose one."""

        if not self.store.has("allowed_folders"):
            return None

        value = self.store.get("allowed_folders")

        if not isinstance(value, list):
            return []                           # malformed: fail closed

        return [f for f in value if isinstance(f, str)]

    def _write(self, folders):
        self.store.set("allowed_folders", folders)

    # ---- queries ----------------------------------------------------

    @property
    def uses_defaults(self):
        return self._configured() is None and not self.error

    @property
    def roots(self):
        """Allowed folders that currently exist."""

        configured = self._configured()
        source = (configured if configured is not None
                  else [str(d) for d in self._defaults])
        return [Path(p) for p in source if Path(p).is_dir()]

    def is_allowed(self, path):
        try:
            real = _real(path)
        except (OSError, ValueError):
            return False

        return any(_inside(real, _real(root)) for root in self.roots)

    # ---- changes ----------------------------------------------------

    def validate_root(self, text):
        """Turn typed text into a safe folder to allow, or raise."""

        text = text.strip().strip('"').strip("'").strip()

        if not text:
            raise ActionError("Which folder? Say: allow folder <full path>")

        expanded = Path(os.path.expanduser(text))

        if not expanded.is_absolute():
            raise ActionError(
                "Please give the full path, for example D:\\Projects")

        real = _real(expanded)

        if not real.is_dir():
            raise ActionError(f"'{real}' isn't an existing folder.")

        home = _real(self.home)

        if real.parent == real or _inside(home, real):
            raise ActionError(
                f"'{real}' is too broad. Pick a specific folder, for example "
                "one inside your Documents.")

        for protected in self._protected:
            if _inside(real, protected):
                raise ActionError(
                    f"'{real}' is a protected system folder, so I won't "
                    "work inside it.")

        return real

    def add(self, path):
        real = self.validate_root(str(path))
        current = [str(r) for r in self.roots]

        if any(_real(c) == real for c in current):
            raise ActionError(f"'{real}' is already allowed.")

        self._write(current + [str(real)])
        return real

    def remove(self, text):
        text = text.strip().strip('"').strip("'").strip()
        target = _real(os.path.expanduser(text))
        current = [str(r) for r in self.roots]
        kept = [c for c in current if _real(c) != target]

        if len(kept) == len(current):
            raise ActionError(f"'{target}' isn't one of my allowed folders.")

        self._write(kept)
        return target

    def location(self, text):
        """Resolve 'Documents' / 'Desktop' / 'Downloads' or a full path."""

        text = text.strip().strip('"').strip("'").strip()

        if text.casefold() in KNOWN_FOLDERS:
            return known_folder(text, self.home if self._explicit_home else None)

        expanded = Path(os.path.expanduser(text))

        if not expanded.is_absolute():
            raise ActionError(
                f"I don't know where '{text}' is. Use Documents, Desktop, "
                "Downloads, or a full path.")

        return expanded
