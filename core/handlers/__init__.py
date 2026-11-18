"""Handler registry.

A handler is a function `handler(intent, env) -> str`. To add a capability:
write a handler, give its intent a name in core.intent.INTENT_NAMES, teach
parse_intent() to produce it, and add one line to build_registry().
"""

from core.handlers import (
    action_commands, basic, confirmation, llm_commands, memory_commands,
    settings_commands, voice_commands,
)


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
        "llm_status": llm_commands.llm_status,
        "use_model": llm_commands.use_model,
        "benchmark_model": llm_commands.benchmark_model,
        "voice_status": voice_commands.voice_status,
        "voice_test": voice_commands.voice_test,
        "stop_speaking": voice_commands.stop_speaking,
        "use_voice_model": voice_commands.use_voice_model,
        "system_info": action_commands.system_info,
        "find_files": action_commands.find_files,
        "show_allowed_folders": action_commands.show_allowed_folders,
        "allow_folder": action_commands.allow_folder,
        "disallow_folder": action_commands.disallow_folder,
        "create_folder": action_commands.create_folder,
        "open_app": action_commands.open_app,
        "allow_app": action_commands.allow_app,
        "disallow_app": action_commands.disallow_app,
        "show_allowed_apps": action_commands.show_allowed_apps,
        "undo": action_commands.undo,
        "index_status": action_commands.index_status,
        "rebuild_index": action_commands.rebuild_index,
        "show_action_log": action_commands.show_action_log,
        "authorise": confirmation.authorise,
        "confirm_yes": confirmation.confirm_yes,
        "confirm_no": confirmation.confirm_no,
        "confirm_alt": confirmation.confirm_alt,
    }
