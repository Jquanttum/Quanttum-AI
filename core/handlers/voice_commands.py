"""Handlers for voice: status, microphone test, stopping speech, model choice."""

from core.voice import VoiceError
from core.voice.models import looks_like_vosk_model

NO_VOICE = "Voice isn't set up in this session."

DOWNLOAD_HELP = (
    "To get a speech model: download one from alphacephei.com/vosk/models "
    "(for example vosk-model-small-en-us-0.15, 40 MB, quick; or "
    "vosk-model-en-us-0.22-lgraph, 128 MB, more accurate), unzip it, and put "
    "the folder in: {folder}")


def _state(status):
    text = status.state

    if status.reason:
        text += f" ({status.reason})"

    return text


def voice_status(intent, env):
    voice = env.voice

    if voice is None:
        return NO_VOICE

    features = env.features
    lines = [
        f"Voice input: {_state(features.status('voice_input'))}",
        f"Voice output: {_state(features.status('voice_output'))}",
        f"Voice auto-send: {_state(features.status('voice_autosend'))}",
    ]

    chosen = voice.model_path()
    found = voice.models()
    folder = voice.model_dirs[0] if voice.model_dirs else "data/models"

    if chosen is None:
        lines.append("Speech model: none found")
        lines.append(DOWNLOAD_HELP.format(folder=folder))
    else:
        lines.append(f"Speech model: {chosen.name}")

        if len(found) > 1:
            lines.append("Other models found: " + ", ".join(
                m.name for m in found if m != chosen)
                + " (say: use voice model <folder path>)")

    mic = voice.microphone_name()
    lines.append(f"Microphone: {mic}" if mic else
                 "Microphone: not detected (or PyAudio isn't installed)")
    lines.append("")
    lines.append("Say: enable voice input, enable voice output, "
                 "test microphone. The mic only listens while you press the "
                 "Mic button.")
    return "\n".join(lines)


def voice_test(intent, env):
    voice = env.voice

    if voice is None:
        return NO_VOICE

    status = env.features.status("voice_input")

    if not status.enabled:
        return (f"Voice input is {status.state}"
                + (f": {status.reason}" if status.reason else "")
                + ". Say: enable voice input")

    try:
        result = voice.listen(cancel=env.cancel, max_seconds=7,
                              silence_timeout=7)
    except VoiceError as error:
        return str(error)

    if result.reason == "cancelled":
        return "Microphone test stopped."

    if result.text:
        return (f"I heard: \"{result.text}\"\n"
                f"The microphone works (peak level {result.peak:.2f}).")

    if result.peak < 0.01:
        return ("I heard nothing at all (level "
                f"{result.peak:.3f}). The microphone may be muted, the wrong "
                "one may be selected, or Windows is blocking access "
                "(Settings > Privacy > Microphone).")

    return (f"I picked up sound (level {result.peak:.2f}) but couldn't make "
            "out words. Try speaking closer and a bit slower, or use a "
            "larger speech model.")


def stop_speaking(intent, env):
    if env.voice is None:
        return NO_VOICE

    env.voice.stop_speaking()
    return "Okay."


def use_voice_model(intent, env):
    voice = env.voice

    if voice is None:
        return NO_VOICE

    path = intent.entities["path"].strip().strip('"').strip("'").strip()

    try:
        if not path:
            env.features.set_option("voice_model", None)
            return "Okay, I'll choose the speech model automatically."

        if not looks_like_vosk_model(path):
            return (f"'{path}' doesn't look like a Vosk speech model folder "
                    "(it should contain 'am' and 'conf' folders).")

        env.features.set_option("voice_model", path)
    except OSError:
        return "I couldn't save your settings, so nothing was changed."

    return f"Now using the speech model: {path}"
