"""Handler registry.

A handler is a function `handler(intent, env) -> str`. To add a capability:
write a handler, give its intent a name in core.intent.INTENT_NAMES, teach
parse_intent() to produce it, and add one line to build_registry().
"""

from core.handlers import basic, confirmation, memory_commands, settings_commands


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
        "recall_category": memory_commands.recall_category,
        "related": memory_commands.related,
        "forget": memory_commands.forget,
        "show_features": settings_commands.show_features,
        "show_profile": settings_commands.show_profile,
        "set_feature": settings_commands.set_feature,
        "set_profile": settings_commands.set_profile,
        "confirm_yes": confirmation.confirm_yes,
        "confirm_no": confirmation.confirm_no,
        "confirm_alt": confirmation.confirm_alt,
    }
