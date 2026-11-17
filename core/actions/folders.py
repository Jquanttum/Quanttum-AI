"""Actions that manage which folders are allowed, and create/remove folders.

Folder creation is Level 2: it asks first and records how to undo it. The
only removal Quanttum AI can do is `remove_empty_folder`, used by undo. It
refuses non-empty folders and never deletes recursively.
"""

import os
import re
from pathlib import Path

from core.actions.base import Action, ActionError, ActionResult, Level
from core.actions.search import index_throttle

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul",
             *(f"com{i}" for i in range(1, 10)),
             *(f"lpt{i}" for i in range(1, 10))}
_MAX_PATH = 240


def _real(path):
    return Path(os.path.realpath(path))


def check_folder_name(name):
    """Reject names Windows would refuse or mangle."""

    if not name or name != name.strip():
        raise ActionError("A folder name can't be empty or start/end "
                          "with a space.")

    if name.endswith("."):
        raise ActionError(f"'{name}' can't end with a dot.")

    if _INVALID_CHARS.search(name):
        raise ActionError(
            f"'{name}' contains characters that aren't allowed in folder "
            "names (< > : \" / \\ | ? *).")

    if name.split(".")[0].casefold() in _RESERVED:
        raise ActionError(f"'{name}' is a reserved Windows name.")

    if len(name) > 100:
        raise ActionError("That folder name is too long.")


def refresh_index(env):
    """Bring the file index up to date after the folder tree changed."""

    if (env.index is not None and env.policy.roots
            and (env.features is None or env.features.is_enabled("file_index"))):
        env.index.refresh_in_background(env.policy.roots,
                                        index_throttle(env.features))


def _describe_roots(policy):
    roots = ", ".join(str(r) for r in policy.roots)
    return roots or "none yet"


class AllowFolder(Action):
    name = "allow_folder"
    level = Level.REVERSIBLE
    title = "Allow a folder"

    def validate(self, params, env):
        real = env.policy.validate_root(params.get("path", ""))

        if any(_real(r) == real for r in env.policy.roots):
            raise ActionError(f"'{real}' is already allowed.")

        return {"path": str(real)}

    def describe(self, params):
        return ("I'm about to give myself permission to search and create "
                f"folders inside:\n{params['path']}\n"
                "(and everything below it).")

    def run(self, params, env):
        real = env.policy.add(params["path"])
        refresh_index(env)
        return ActionResult(
            f"Allowed: {real}",
            undo={"action": "disallow_folder", "params": {"path": str(real)}})


class DisallowFolder(Action):
    name = "disallow_folder"
    level = Level.READ_ONLY      # it only ever reduces what I can touch
    title = "Stop allowing a folder"

    def validate(self, params, env):
        text = params.get("path", "").strip()

        if not text:
            raise ActionError("Which folder? Say: remove allowed folder "
                              "<full path>")
        return {"path": text}

    def run(self, params, env):
        target = env.policy.remove(params["path"])

        refresh_index(env)
        return ActionResult(f"I no longer have access to: {target}")


class CreateFolder(Action):
    name = "create_folder"
    level = Level.REVERSIBLE
    title = "Create a folder"

    def validate(self, params, env):
        name = params.get("name", "").strip().strip('"').strip("'").strip()
        location = params.get("location", "").strip()

        if not name:
            raise ActionError("What should the folder be called?")

        if location:
            target = env.policy.location(location) / name
        else:
            target = Path(os.path.expanduser(name))

            if not target.is_absolute():
                raise ActionError(
                    "Where should I create it? Say: create folder <name> in "
                    "Documents (or Desktop, Downloads, or a full path).")

        real = _real(target)

        if len(str(real)) > _MAX_PATH:
            raise ActionError("That path is too long for Windows.")

        if not env.policy.is_allowed(real):
            raise ActionError(
                "That isn't inside a folder I'm allowed to use. Allowed "
                f"folders: {_describe_roots(env.policy)}. "
                "To add one, say: allow folder <full path>")

        if real.exists():
            raise ActionError(f"'{real}' already exists.")

        missing = []
        probe = real

        while not probe.exists():
            missing.append(probe)
            probe = probe.parent

        for folder in missing:
            check_folder_name(folder.name)

        # deepest first, so undo can remove them in a safe order
        return {"path": str(real), "created": [str(p) for p in missing]}

    def describe(self, params):
        extra = len(params["created"]) - 1
        note = f"\n(plus {extra} missing parent folder{'s' if extra > 1 else ''})" if extra else ""
        return f"I'm about to create this folder:\n{params['path']}{note}"

    def run(self, params, env):
        target = Path(params["path"])

        try:
            target.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            raise ActionError(f"'{target}' already exists.")
        except OSError as error:
            raise ActionError(f"I couldn't create it: {error.strerror or error}")

        refresh_index(env)
        return ActionResult(
            f"Created: {target}",
            undo={"action": "remove_empty_folder",
                  "params": {"paths": params["created"]}})


class RemoveEmptyFolder(Action):
    """Used by undo. Removes only EMPTY folders, never anything with files."""

    name = "remove_empty_folder"
    level = Level.REVERSIBLE
    title = "Remove empty folders"

    def validate(self, params, env):
        paths = [str(_real(p)) for p in params.get("paths", [])]

        if not paths:
            raise ActionError("There's nothing to remove.")

        doomed = set(paths)

        for path in paths:
            folder = Path(path)

            if not env.policy.is_allowed(folder):
                raise ActionError(f"'{path}' isn't inside an allowed folder.")

            if any(_real(root) == folder for root in env.policy.roots):
                raise ActionError(f"'{path}' is an allowed folder itself, so "
                                  "I won't remove it.")

            if not folder.is_dir():
                raise ActionError(f"'{path}' no longer exists as a folder.")

            try:
                leftovers = [str(_real(entry.path))
                             for entry in os.scandir(folder)]
            except OSError as error:
                raise ActionError(f"I can't look inside '{path}': {error}")

            if any(item not in doomed for item in leftovers):
                raise ActionError(
                    f"'{path}' isn't empty, so I won't remove it. "
                    "Nothing was changed.")

        return {"paths": paths}

    def describe(self, params):
        listing = "\n".join(params["paths"])
        return f"I'm about to remove these empty folders:\n{listing}"

    def run(self, params, env):
        removed = []

        for path in params["paths"]:
            try:
                os.rmdir(path)             # never rmtree: fails if not empty
                removed.append(path)
            except OSError as error:
                raise ActionError(
                    f"I couldn't remove '{path}': {error.strerror or error}. "
                    f"Removed so far: {len(removed)}.")

        refresh_index(env)
        return ActionResult("Removed: " + ", ".join(removed))
