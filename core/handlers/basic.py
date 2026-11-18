"""Handlers for conversational basics: greeting, time, date, help, fallback."""

from core.llm import LLMError


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
        "- Use profile standard (lite, standard or full)\n"
        "- Find exam notes / Find folder projects\n"
        "- Create folder Reports in Documents (then undo)\n"
        "- Show allowed folders / Allow folder D:\\Projects\n"
        "- Allow app notepad, then: Open notepad\n"
        "- Show allowed apps\n"
        "- Show model / Use model <name> / Benchmark (the local AI model)\n"
        "- Anything else: just talk to me (needs the local AI model)\n"
        "- System info / Show action log / Index status\n\n"
        "More advanced intelligence, voice and automation "
        "are planned for future versions."
    )


def unknown(intent, env):
    """Anything that isn't a command.

    With the local model enabled it answers (using your memories as context).
    Otherwise, or if the model can't be reached, fall back to offering any
    stored facts that share words with the message.
    """

    prefix = ""

    if env.llm is not None and env.features is not None:
        status = env.features.status("local_llm")

        if status.enabled:
            try:
                return env.llm.chat_reply(intent.raw, env)
            except LLMError as error:
                prefix = f"(The local model isn't available: {error})\n\n"

        elif status.state == "unavailable":
            # It is switched on but can't run right now: say why, instead of
            # quietly giving worse answers.
            prefix = f"(The local model isn't available: {status.reason})\n\n"

    records = env.memory.find(intent.raw, limit=3)

    if records:
        env.memory.touch([record.id for record in records])
        results = "\n".join(f"- {record.fact}" for record in records)

        return prefix + (
            "I found some stored information that may be "
            f"relevant:\n{results}\n\n"
            "I can retrieve these memories, but my advanced "
            "reasoning engine is not connected yet."
        )

    return prefix + (
        "I don't have a response for that yet. My lightweight "
        "intent and memory systems are active, but my advanced "
        "intelligence engine has not been connected."
    )
