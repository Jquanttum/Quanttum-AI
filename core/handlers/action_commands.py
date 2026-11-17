"""Handlers that ask the Action Engine to do things.

None of these touch the computer themselves. They build a request, hand it to
the engine, and show the answer. If the engine needs the user's approval it
returns a pending question, which is parked in the conversation state so the
next "yes" / "no" / typed phrase can answer it.
"""

import json


def _request(env, name, params, undo_of=None):
    outcome = env.actions.request(name, params, env, undo_of)

    if outcome.pending is not None:
        env.state.pending = outcome.pending

    return outcome.message


def system_info(intent, env):
    return _request(env, "system_info", {})


def find_files(intent, env):
    return _request(env, "find_files", {
        "query": intent.entities["query"],
        "kind": intent.entities["kind"],
    })


def allow_folder(intent, env):
    return _request(env, "allow_folder", {"path": intent.entities["path"]})


def disallow_folder(intent, env):
    return _request(env, "disallow_folder", {"path": intent.entities["path"]})


def create_folder(intent, env):
    return _request(env, "create_folder", {
        "name": intent.entities["name"],
        "location": intent.entities["location"],
    })


def open_app(intent, env):
    return _request(env, "open_app", {"name": intent.entities["name"]})


def allow_app(intent, env):
    return _request(env, "allow_app", {"name": intent.entities["name"]})


def disallow_app(intent, env):
    return _request(env, "disallow_app", {"name": intent.entities["name"]})


def show_allowed_apps(intent, env):
    if env.apps.error:
        return env.apps.error

    apps = env.apps.approved()

    if not apps:
        return ("I haven't been approved to open any app yet. Say: allow app "
                "<name> (for example: allow app notepad)")

    lines = ["Apps I'm approved to open:"]
    lines.extend(f"- {app.name}  ({app.path})" for app in apps)
    lines.append("\nSay: open <name>, or: remove allowed app <name>")
    return "\n".join(lines)


def rebuild_index(intent, env):
    return _request(env, "rebuild_index", {})


def undo(intent, env):
    event = env.actions.audit.last_undoable()

    if event is None:
        return "There's nothing to undo."

    recipe = json.loads(event.undo)
    answer = _request(env, recipe["action"], recipe["params"],
                      undo_of=event.request_id)

    return f"Undoing: {event.detail}\n\n{answer}"


def show_allowed_folders(intent, env):
    policy = env.policy

    if policy.error:
        return policy.error

    roots = policy.roots

    if not roots:
        return ("I'm not allowed to use any folder yet. Say: allow folder "
                "<full path>")

    lines = ["Folders I'm allowed to search and create folders in:"]
    lines.extend(f"- {root}" for root in roots)

    if policy.uses_defaults:
        lines.append("\n(These are the defaults: your Documents, Desktop and "
                     "Downloads.)")

    lines.append("\nSay: allow folder <full path>, or: remove allowed folder "
                 "<full path>")
    return "\n".join(lines)


def index_status(intent, env):
    if env.index is None:
        return "There's no file index in this session."

    state = ("on" if env.features is None
             or env.features.is_enabled("file_index") else "OFF")
    status = env.index.status()
    lines = [f"File index: {state}",
             f"Entries: {status['entries']:,}"]

    if status["indexing"]:
        lines.append("Currently updating in the background.")

    if status["last_refresh"]:
        lines.append(f"Last complete update: {status['last_refresh']} UTC")
    elif not status["indexing"]:
        lines.append("It hasn't completed an update yet. Say: rebuild index")

    if status["error"]:
        lines.append(f"Last error: {status['error']}")

    return "\n".join(lines)


def show_action_log(intent, env):
    events = env.actions.audit.recent(20)

    if not events:
        return "No actions have been requested yet."

    lines = ["Recent action events (newest first, UTC):"]

    for event in events:
        detail = f"  {event.detail}" if event.detail else ""
        lines.append(f"{event.created_at[5:16]}  {event.request_id[:6]}  "
                     f"{event.action} (L{event.level})  {event.event}{detail}")

    return "\n".join(lines)
