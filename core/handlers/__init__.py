"""Handler registry.

A handler is a function `handler(intent, env) -> str`. To add a capability:
write a handler, give its intent a name in core.intent.INTENT_NAMES, teach
parse_intent() to produce it, and add one line to build_registry().
"""

from core.handlers import basic, memory_commands


def build_registry():
    return {
        "greeting": basic.greeting,
        "time": basic.time,
        "date": basic.date,
        "help": basic.help,
        "unknown": basic.unknown,
        "remember": memory_commands.remember,
        "recall_all": memory_commands.recall_all,
        "recall": memory_commands.recall,
        "forget": memory_commands.forget,
    }
