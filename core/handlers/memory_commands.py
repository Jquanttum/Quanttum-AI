"""Handlers for the long-term memory commands."""

from core.context import PendingConfirmation
from core.textutils import categorise

# "Show everything" must not be capped at the default search limit of 5.
LIST_LIMIT = 50

CATEGORY_LABELS = {
    "task": "tasks",
    "preference": "preferences",
    "general": "general facts",
}


def format_records(records):
    """One line per memory: '#3  [task] call my supervisor'.

    Plain facts have no tag; other categories are tagged.
    """

    lines = []

    for record in records:
        tag = "" if record.category == "general" else f"[{record.category}] "
        lines.append(f"#{record.id}  {tag}{record.fact}")

    return "\n".join(lines)


def _show(records, env):
    """Remember what was just displayed so "forget that" can refer to it."""

    env.state.focus_ids = [record.id for record in records]
    env.memory.touch(env.state.focus_ids)


def remember(intent, env):
    fact = " ".join(intent.entities["fact"].split())
    category = intent.entities.get("category") or categorise(fact)
    memory = env.memory

    existing = memory.lookup(fact)

    if existing is not None and existing.status == "active":
        env.state.focus_ids = [existing.id]
        return f"I already remember that (#{existing.id})."

    if category == "task":
        memory_id = memory.remember(fact, "task", env.state.message_id)
        env.state.focus_ids = [memory_id]
        return (
            f"I've noted this as a task: {fact}\n"
            "I can't set reminders or send alerts yet, so I won't notify "
            "you. It's saved under your tasks (say: show my tasks)."
        )

    similar = memory.find_similar(fact)

    if similar is not None:
        message_id = env.state.message_id

        def on_yes(env):
            new_id = env.memory.replace(
                similar.id, fact, category, message_id
            )

            if new_id is None:
                return f"Memory #{similar.id} is no longer active, so I changed nothing."

            env.state.focus_ids = [new_id]
            return (
                f"Done. #{new_id} now replaces #{similar.id}: {fact}\n"
                "The old version is kept in history."
            )

        def on_alt(env):
            new_id = env.memory.remember(fact, category, message_id)
            env.state.focus_ids = [new_id]
            return f"Saved as #{new_id}, alongside #{similar.id}."

        def on_no(env):
            return "Okay, I haven't saved it."

        env.state.pending = PendingConfirmation(on_yes, on_no, on_alt)

        return (
            "I already have a similar memory:\n"
            f"#{similar.id}  {similar.fact}\n\n"
            f"You're telling me: {fact}\n\n"
            "Reply 'yes' to replace it (the old one stays in history), "
            "'keep both' to save both, or 'no' to discard the new one."
        )

    memory_id = memory.remember(fact, category, env.state.message_id)
    env.state.focus_ids = [memory_id]
    return f"I've saved this to long-term memory: {fact}"


def recall_all(intent, env):
    records = env.memory.find(limit=LIST_LIMIT)

    if not records:
        return "I don't have any saved long-term memories yet."

    _show(records, env)

    return (
        "Here is what I remember:\n"
        f"{format_records(records)}\n\n"
        "To remove one, say: forget #<number>"
    )


def recall_category(intent, env):
    category = intent.entities["category"]
    label = CATEGORY_LABELS.get(category, category)
    records = env.memory.find(limit=LIST_LIMIT, category=category)

    if not records:
        return f"I don't have any saved {label}."

    _show(records, env)

    return f"Your {label}:\n{format_records(records)}"


def recall(intent, env):
    query = intent.entities["query"]
    records = env.memory.find(query)

    if not records:
        return f"I couldn't find any saved memories related to '{query}'."

    _show(records, env)

    return (
        "I found these related memories:\n"
        f"{format_records(records)}"
    )


def related(intent, env):
    memory_id = intent.entities["id"]

    if env.memory.get(memory_id) is None:
        return f"I don't have an active memory with ID #{memory_id}."

    records = env.memory.related(memory_id)

    if not records:
        return f"I couldn't find anything related to #{memory_id}."

    _show(records, env)

    return f"Related to #{memory_id}:\n{format_records(records)}"


def forget(intent, env):
    memory_id = intent.entities.get("id")

    if memory_id is None:
        # "forget that": resolve it from what the previous turn showed.
        candidates = env.state.previous_focus_ids

        if not candidates:
            return (
                "I'm not sure which memory you mean. Ask 'what do you "
                "remember?' to see the numbers, then say: forget #<number>"
            )

        if len(candidates) > 1:
            shown = [
                (candidate, env.memory.get(candidate))
                for candidate in candidates
            ]
            lines = "\n".join(
                f"#{candidate}  {fact}"
                for candidate, fact in shown if fact is not None
            )

            return (
                "That could be several memories, so I won't guess:\n"
                f"{lines}\n\n"
                "Say: forget #<number> to choose one."
            )

        memory_id = candidates[0]

    fact = env.memory.get(memory_id)

    if fact is None or not env.memory.forget(memory_id):
        return f"I don't have an active memory with ID #{memory_id}."

    return f"I've forgotten memory #{memory_id}: {fact}"
