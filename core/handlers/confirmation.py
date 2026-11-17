"""Handlers for yes / no / keep-both answers to a pending question."""

NOTHING_PENDING = "There's nothing waiting for your confirmation."


def _answer(env, option):
    pending = env.state.previous_pending

    if pending is None:
        return NOTHING_PENDING

    env.state.previous_pending = None
    callback = getattr(pending, option)

    if callback is None:
        return "That isn't one of the options for my last question."

    return callback(env)


def _rearm_for_phrase(env, pending):
    """A destructive action needs the typed phrase; a plain yes isn't enough.
    Keep the question open for one more turn."""

    env.state.pending = pending
    env.state.previous_pending = None
    return (
        "A plain 'yes' isn't enough for this. To authorise it, type exactly:\n"
        f"{pending.phrase}\n"
        "Anything else cancels."
    )


def confirm_yes(intent, env):
    pending = env.state.previous_pending

    if pending is not None and pending.phrase:
        return _rearm_for_phrase(env, pending)

    return _answer(env, "on_yes")


def authorise(intent, env):
    """The user typed 'confirm ...'. It only works if it matches exactly."""

    pending = env.state.previous_pending

    if pending is None:
        return NOTHING_PENDING

    env.state.previous_pending = None

    if not pending.phrase:
        env.state.pending = pending
        return "That question only needs a yes or no."

    typed = " ".join(intent.entities["phrase"].split()).rstrip(" .!?").casefold()

    if typed == " ".join(pending.phrase.split()).casefold():
        return pending.on_yes(env)

    return ("That doesn't match the authorisation phrase, so I've cancelled "
            "it. " + pending.on_no(env))


def confirm_no(intent, env):
    return _answer(env, "on_no")


def confirm_alt(intent, env):
    return _answer(env, "on_alt")
