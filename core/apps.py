"""Finding apps, remembering which ones you approved, and launching them.

Only two kinds of file can ever be approved: Start Menu shortcuts (.lnk) and
programs (.exe). Scripts (.bat, .cmd, .ps1, .vbs ...) are refused because
they run arbitrary code. Launching never uses a command shell and never
passes arguments: the stored, approved path is opened and nothing else.
"""

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from core.actions.base import ActionError
from core.policy import PermissionsStore

APP_EXTENSIONS = (".lnk", ".exe")
_SKIP_WORDS = ("uninstall", "uninstaller")


@dataclass(frozen=True)
class AppCandidate:
    name: str
    path: str


def start_menu_dirs(environ=None):
    environ = os.environ if environ is None else environ
    found = []

    for variable in ("ProgramData", "APPDATA"):
        base = environ.get(variable)
        if base:
            found.append(Path(base) / "Microsoft" / "Windows" / "Start Menu"
                         / "Programs")

    return [folder for folder in found if folder.is_dir()]


def _clean(text):
    text = text.strip().strip('"').strip("'").strip().casefold()

    for suffix in APP_EXTENSIONS:
        if text.endswith(suffix):
            text = text[: -len(suffix)]

    return text.strip()


class AppCatalog:
    """Looks for apps in the Start Menu and (for single names) on PATH."""

    def __init__(self, folders=None, which=shutil.which):
        self._folders = folders           # None = work them out when needed
        self._which = which

    def _shortcuts(self):
        folders = (self._folders if self._folders is not None
                   else start_menu_dirs())

        for folder in folders:
            for current, _dirs, files in os.walk(folder):
                for filename in files:
                    if filename.casefold().endswith(".lnk"):
                        yield Path(filename).stem, str(Path(current) / filename)

    def search(self, text, limit=10):
        query = _clean(text)

        if not query:
            return []

        words = query.split()
        exact, partial = [], []

        for name, path in self._shortcuts():
            lowered = name.casefold()

            if any(skip in lowered for skip in _SKIP_WORDS):
                continue

            if lowered == query:
                exact.append(AppCandidate(name, path))
            elif all(word in lowered for word in words):
                partial.append(AppCandidate(name, path))

        found = exact or sorted(partial, key=lambda c: len(c.name))

        if not found and len(words) == 1 and not any(
                sep in query for sep in ("/", "\\", ":")):
            located = self._which(query)

            if located and located.casefold().endswith(".exe"):
                found = [AppCandidate(Path(located).stem, located)]

        unique, seen = [], set()

        for candidate in found:
            key = os.path.normcase(candidate.path)
            if key not in seen:
                seen.add(key)
                unique.append(candidate)

        return unique[:limit]


@dataclass(frozen=True)
class ApprovedApp:
    name: str
    path: str


class AppPolicy:
    """The apps you have approved. Stored in the shared permissions file."""

    SECTION = "allowed_apps"

    def __init__(self, store=None):
        self.store = store if store is not None else PermissionsStore()

    @property
    def error(self):
        return self.store.error

    def _records(self):
        value = self.store.get(self.SECTION, {})

        if not isinstance(value, dict):
            return {}

        return {key: record for key, record in value.items()
                if isinstance(record, dict)
                and isinstance(record.get("name"), str)
                and isinstance(record.get("path"), str)}

    def approved(self):
        apps = [ApprovedApp(r["name"], r["path"])
                for r in self._records().values()]
        return sorted(apps, key=lambda app: app.name.casefold())

    def find(self, text):
        """Approved apps matching what was typed: an exact name wins,
        otherwise every word must appear in the name."""

        query = _clean(text)
        apps = self.approved()

        if not query:
            return []

        exact = [a for a in apps if a.name.casefold() == query]

        if exact:
            return exact

        words = query.split()
        return [a for a in apps if all(w in a.name.casefold() for w in words)]

    def add(self, name, path):
        if not str(path).casefold().endswith(APP_EXTENSIONS):
            raise ActionError(
                "I only approve Start Menu shortcuts (.lnk) and programs "
                "(.exe), never scripts.")

        records = self._records()
        records[name.casefold()] = {"name": name, "path": str(path)}
        self.store.set(self.SECTION, records)

    def remove(self, text):
        matches = self.find(text)

        if not matches:
            raise ActionError(f"'{text}' isn't one of my approved apps.")

        if len(matches) > 1:
            names = ", ".join(a.name for a in matches)
            raise ActionError(f"Several approved apps match '{text}': {names}. "
                              "Please be more specific.")

        records = self._records()
        records.pop(matches[0].name.casefold(), None)
        self.store.set(self.SECTION, records)
        return matches[0]


def launch_target(path):
    """Open an approved file. No shell, no arguments."""

    if sys.platform == "win32":
        os.startfile(path)                      # noqa: S606 - fixed, approved path
    else:
        subprocess.Popen([path], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
