"""Actions for approving and opening applications.

Approving an app is Level 2 (you confirm the exact file). Opening an
approved app is also Level 2 (you confirm each launch). Only apps on the
approved list can ever be opened, and only the exact file you approved.
"""

import os

from core.actions.base import Action, ActionError, ActionResult, Level
from core.apps import APP_EXTENSIONS


def _name(params):
    return str(params.get("name", "")).strip().strip('"').strip("'").strip()


class AllowApp(Action):
    name = "allow_app"
    level = Level.REVERSIBLE
    title = "Approve an app"

    def validate(self, params, env):
        wanted = _name(params)

        if not wanted:
            raise ActionError("Which app? Say: allow app <name>")

        candidates = env.app_catalog.search(wanted)

        if not candidates:
            raise ActionError(
                f"I couldn't find an app called '{wanted}' in the Start Menu "
                "or on your PATH.")

        if len(candidates) > 1:
            names = ", ".join(c.name for c in candidates)
            raise ActionError(
                f"Several apps match '{wanted}': {names}. "
                "Please be more specific.")

        found = candidates[0]

        if any(a.name.casefold() == found.name.casefold()
               for a in env.apps.approved()):
            raise ActionError(f"'{found.name}' is already approved.")

        return {"name": found.name, "path": found.path}

    def describe(self, params):
        return (
            "I'm about to approve this app, so I can open it when you ask:\n"
            f"{params['name']}\n{params['path']}\n"
            "I will only ever open this exact file, never with extra commands."
        )

    def run(self, params, env):
        env.apps.add(params["name"], params["path"])
        return ActionResult(
            f"Approved: {params['name']}",
            undo={"action": "disallow_app",
                  "params": {"name": params["name"]}})


class DisallowApp(Action):
    name = "disallow_app"
    level = Level.READ_ONLY      # it only ever reduces what I can open
    title = "Stop approving an app"

    def validate(self, params, env):
        wanted = _name(params)

        if not wanted:
            raise ActionError("Which app? Say: remove allowed app <name>")

        return {"name": wanted}

    def run(self, params, env):
        removed = env.apps.remove(params["name"])
        return ActionResult(f"I will no longer open: {removed.name}")


class OpenApp(Action):
    name = "open_app"
    level = Level.REVERSIBLE
    title = "Open an app"

    def validate(self, params, env):
        wanted = _name(params)

        if not wanted:
            raise ActionError("Which app? Say: open <name>")

        matches = env.apps.find(wanted)

        if not matches:
            raise ActionError(
                f"'{wanted}' isn't an approved app yet. "
                f"Say: allow app {wanted}")

        if len(matches) > 1:
            names = ", ".join(a.name for a in matches)
            raise ActionError(
                f"Several approved apps match '{wanted}': {names}. "
                "Please be more specific.")

        app = matches[0]

        if not app.path.casefold().endswith(APP_EXTENSIONS):
            raise ActionError(f"'{app.name}' isn't a program or shortcut, "
                              "so I won't open it.")

        if not os.path.isfile(app.path):
            raise ActionError(
                f"'{app.name}' is no longer at {app.path}. Remove it "
                f"(remove allowed app {app.name}) and approve it again.")

        return {"name": app.name, "path": app.path}

    def describe(self, params):
        return f"I'm about to open {params['name']}\n({params['path']})"

    def run(self, params, env):
        try:
            env.launcher(params["path"])
        except OSError as error:
            raise ActionError(
                f"I couldn't open {params['name']}: {error.strerror or error}")

        return ActionResult(f"Opened {params['name']}.")
