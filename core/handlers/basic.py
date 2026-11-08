"""Handlers for conversational basics: greeting, time, date, help, fallback."""


def greeting(intent, env):
    return "Hello. I'm Quanttum AI. My local assistant core is running."


def time(intent, env):
    return f"The current time is {env.clock().strftime('%I:%M %p')}."


def date(intent, env):
    return f"Today is {env.clock().strftime('%A, %d %B %Y')}."


def help(intent, env):
    return (
        "I can currently handle greetings, the date, time, "
        "and long-term memory.\n\n"
        "Try these commands:\n"
        "- Remember that I prefer Python.\n"
        "- What do you remember about me?\n"
        "- What do you remember about Python?\n"
        "- Remember to call my supervisor (saved as a task)\n"
        "- Show my tasks / Show my preferences\n"
        "- Forget #1 (use the number shown in the memory list)\n"
        "- Forget that (right after I save or show a single memory)\n"
        "- What's related to #1?\n"
        "- Show features / Show profile\n"
        "- Enable ram monitor / Disable ram monitor\n"
        "- Use profile standard (lite, standard or full)\n\n"
        "More advanced intelligence, voice and automation "
        "are planned for future versions."
    )


def unknown(intent, env):
    """Fallback: offer any stored facts that share words with the message."""

    records = env.memory.find(intent.raw, limit=3)

    if records:
        env.memory.touch([record.id for record in records])
        results = "\n".join(f"- {record.fact}" for record in records)

        return (
            "I found some stored information that may be "
            f"relevant:\n{results}\n\n"
            "I can retrieve these memories, but my advanced "
            "reasoning engine is not connected yet."
        )

    return (
        "I don't have a response for that yet. My lightweight "
        "intent and memory systems are active, but my advanced "
        "intelligence engine has not been connected."
    )
