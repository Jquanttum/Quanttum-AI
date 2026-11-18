"""Handlers for managing the local model."""

from core.llm import LLMError
from core.llm_brain import RAM_SHARE_FOR_WEIGHTS

NO_LLM = "The local model isn't set up in this session."


def _gb(size):
    return f"{size / 1e9:.1f} GB" if size else "size unknown"


def llm_status(intent, env):
    brain = env.llm

    if brain is None:
        return NO_LLM

    snap = brain.snapshot(force=True)
    feature = env.features.status("local_llm")
    commands = env.features.status("llm_commands")

    lines = [f"Local AI model feature: {feature.state}"
             + (f" ({feature.reason})" if feature.reason else ""),
             f"AI command understanding: {commands.state}"
             + (f" ({commands.reason})" if commands.reason else "")]

    if not snap.up:
        lines.append("Ollama: not running. Start the Ollama app.")
        return "\n".join(lines)

    lines.append(f"Ollama: running (version {snap.version})")

    if not snap.models:
        lines.append("No models are installed. In a terminal, run: "
                     f"ollama pull {brain.suggested_pull()}")
        return "\n".join(lines)

    chosen = brain.selected_model()
    by_you = env.features.option("llm_model")
    lines.append("Installed models:")

    for model in snap.models:
        mark = "  <- in use" if model.name == chosen else ""
        lines.append(f"- {model.name} ({_gb(model.size)}){mark}")

    if chosen is None:
        lines.append("None of them suits this PC's profile and RAM. "
                     f"Try: ollama pull {brain.suggested_pull()}")
    elif by_you:
        lines.append("(You chose this model. Say: use default model, to let "
                     "me choose.)")
    else:
        lines.append("(I chose this automatically. Say: use model <name> to "
                     "pick another.)")

    lines.append("Say: benchmark, to measure its speed on this PC.")
    return "\n".join(lines)


def use_model(intent, env):
    brain = env.llm

    if brain is None:
        return NO_LLM

    wanted = intent.entities["model"]

    try:
        if not wanted:
            env.features.set_option("llm_model", None)
            brain.snapshot(force=True)
            return "Okay, I'll choose the model automatically."

        snap = brain.snapshot(force=True)

        if not snap.up:
            return "Ollama isn't running. Start the Ollama app first."

        matches = [m for m in snap.models
                   if m.name.casefold() == wanted.casefold()]

        if not matches:
            matches = [m for m in snap.models
                       if m.name.casefold().startswith(wanted.casefold())]

        if len(matches) != 1:
            names = ", ".join(m.name for m in snap.models) or "none"
            return (f"I couldn't match '{wanted}' to exactly one installed "
                    f"model. Installed: {names}. To get another, run: "
                    "ollama pull <name>")

        model = matches[0]
        env.features.set_option("llm_model", model.name)
        brain.snapshot(force=True)
    except OSError:
        return "I couldn't save your settings, so nothing was changed."

    reply = f"Now using {model.name}."
    total = env.features.total_ram_gb()

    if total and model.size and model.size > RAM_SHARE_FOR_WEIGHTS * total * 1e9:
        reply += (f"\nWarning: it's {_gb(model.size)}, large for this PC's "
                  f"{total:.1f} GB of RAM. It may be slow or squeeze other "
                  "programs.")

    return reply


def benchmark_model(intent, env):
    if env.llm is None:
        return NO_LLM

    try:
        return env.llm.benchmark(env)
    except LLMError as error:
        return f"I couldn't run the benchmark: {error}"
