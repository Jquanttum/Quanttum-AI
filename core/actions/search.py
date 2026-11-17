"""Level 1 actions: finding files and managing the file index."""

import os
import time

from core.actions.base import Action, ActionError, ActionResult, Level
from core.fileindex import live_search

MAX_RESULTS = 15


def index_throttle(features):
    """Pause briefly while indexing on the lite profile, so a slow CPU stays
    responsive for the chat window."""

    if features is not None and features.profile == "lite":
        return 0.002

    return 0.0


def _size(number):
    for unit in ("B", "KB", "MB", "GB"):
        if number < 1024 or unit == "GB":
            return f"{number:.0f} {unit}" if unit == "B" else f"{number:.1f} {unit}"
        number /= 1024


def _stamp(mtime):
    return time.strftime("%Y-%m-%d", time.localtime(mtime))


class FindFiles(Action):
    name = "find_files"
    level = Level.READ_ONLY
    title = "Find files"

    def validate(self, params, env):
        query = " ".join(str(params.get("query", "")).split())
        kind = params.get("kind", "any")

        if not query or len(query) > 200:
            raise ActionError("What should I look for? Say: find <name>")

        if kind not in ("any", "file", "folder"):
            raise ActionError("I can look for files or folders.")

        return {"query": query, "kind": kind}

    def run(self, params, env):
        roots = env.policy.roots

        if not roots:
            return ActionResult(
                "I'm not allowed to look in any folder yet. Say: allow folder "
                "<full path>")

        query, kind = params["query"], params["kind"]
        use_index = (
            env.index is not None
            and (env.features is None or env.features.is_enabled("file_index"))
            and env.index.status()["entries"] > 0
        )

        note = ""
        if use_index:
            hits = env.index.search(query, roots, kind, limit=MAX_RESULTS * 2)
            if env.index.indexing:
                note = "\n(The index is still updating, so this may be incomplete.)"
        else:
            hits, complete = live_search(roots, query, kind, MAX_RESULTS * 2)
            note = ("\n(Live search: the file index isn't ready or is off, so "
                    "this was slower"
                    + ("." if complete else " and stopped after 3 seconds, "
                       "so it may be incomplete.") + ")")

        # Trust the disk over the index: drop files that are gone and
        # double-check every path is inside an allowed folder.
        checked = []
        for hit in hits:
            if not os.path.lexists(hit.path) or not env.policy.is_allowed(hit.path):
                continue
            try:
                st = os.stat(hit.path)
                hit = type(hit)(hit.path, hit.name, hit.is_dir, st.st_size,
                                st.st_mtime)
            except OSError:
                continue
            checked.append(hit)

        checked = checked[:MAX_RESULTS]
        if not checked:
            return ActionResult(f"I couldn't find anything matching "
                                f"'{query}' in your allowed folders.{note}")

        count = len(checked)
        lines = [f"Found {count} result{'s' if count != 1 else ''} "
                 f"for '{query}':"]

        for number, hit in enumerate(checked, 1):
            detail = ("folder" if hit.is_dir
                      else f"{_size(hit.size)}") + f", modified {_stamp(hit.mtime)}"
            lines.append(f"{number}. {hit.path}  ({detail})")

        return ActionResult("\n".join(lines) + note)


class RebuildIndex(Action):
    name = "rebuild_index"
    level = Level.READ_ONLY
    title = "Update the file index"

    def run(self, params, env):
        if env.index is None:
            raise ActionError("There's no file index in this session.")

        if env.features is not None and not env.features.is_enabled("file_index"):
            raise ActionError("The file index is turned off. "
                              "Say: enable file index")

        roots = env.policy.roots

        if not roots:
            raise ActionError("There are no allowed folders to index.")

        if not env.index.refresh_in_background(
                roots, index_throttle(env.features)):
            return ActionResult("The index is already updating.")

        return ActionResult(
            f"Updating the index for {len(roots)} folder"
            f"{'s' if len(roots) != 1 else ''} in the background. "
            "Searches work meanwhile. Say 'index status' to check progress.")
