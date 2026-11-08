"""Handlers for the long-term memory commands."""

# "Show everything" must not be capped at the default search limit of 5.
LIST_LIMIT = 50


def _format_records(records):
    return "\n".join(f"#{memory_id}  {fact}" for memory_id, fact in records)


def remember(intent, env):
    fact = intent.entities["fact"]
    memory_id = env.memory.remember(fact)

    if memory_id is None:
        return "I couldn't save that memory. Please try again."

    env.state.focus_ids = [memory_id]
    return f"I've saved this to long-term memory: {fact}"


def recall_all(intent, env):
    records = env.memory.search_records(limit=LIST_LIMIT)

    if not records:
        return "I don't have any saved long-term memories yet."

    env.state.focus_ids = [memory_id for memory_id, _ in records]

    return (
        "Here is what I remember:\n"
        f"{_format_records(records)}\n\n"
        "To remove one, say: forget #<number>"
    )


def recall(intent, env):
    query = intent.entities["query"]
    records = env.memory.search_records(query)

    if not records:
        return f"I couldn't find any saved memories related to '{query}'."

    env.state.focus_ids = [memory_id for memory_id, _ in records]

    return (
        "I found these related memories:\n"
        f"{_format_records(records)}"
    )


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
            shown = [(c, fact) for c, fact in shown if fact is not None]

            return (
                "That could be several memories, so I won't guess:\n"
                f"{_format_records(shown)}\n\n"
                "Say: forget #<number> to choose one."
            )

        memory_id = candidates[0]

    fact = env.memory.get(memory_id)

    if fact is None or not env.memory.forget(memory_id):
        return f"I don't have an active memory with ID #{memory_id}."

    return f"I've forgotten memory #{memory_id}: {fact}"
