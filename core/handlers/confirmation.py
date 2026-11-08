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


def confirm_yes(intent, env):
    return _answer(env, "on_yes")


def confirm_no(intent, env):
    return _answer(env, "on_no")


def confirm_alt(intent, env):
    return _answer(env, "on_alt")
