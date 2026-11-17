"""The Action Engine: the only door through which Quanttum AI may act.

Handlers never touch the computer directly. They ask the engine to run a
named action, and the engine enforces, in code, what each permission level
needs:

    Level 1  read-only    -> runs immediately
    Level 2  reversible   -> the user must say yes; how to undo it is recorded
    Level 3  destructive  -> the user must type an exact authorisation phrase

Every step is written to the append-only audit log. Unknown actions, a
disabled engine and invalid parameters are all refused (fail closed).
"""

import logging

from core.actions.base import Action, ActionError, ActionResult, Level, Outcome
from core.audit import new_request_id
from core.context import PendingConfirmation

logger = logging.getLogger(__name__)


class ActionEngine:
    def __init__(self, audit, actions=()):
        self.audit = audit
        self._actions = {}

        for action in actions:
            self.register(action)

    def register(self, action):
        if not isinstance(action, Action) or not action.name:
            raise ValueError("An action needs a name and must extend Action.")

        if action.name in self._actions:
            raise ValueError(f"Action '{action.name}' is already registered.")

        self._actions[action.name] = action

    def names(self):
        return sorted(self._actions)

    # ------------------------------------------------------------------

    def request(self, name, params, env, undo_of=None):
        request_id = new_request_id()
        message_id = env.state.message_id
        action = self._actions.get(name)
        level = action.level if action else 0

        def log(event, detail="", **extra):
            self.audit.record(request_id, name, level, event, params=params,
                              detail=detail, message_id=message_id, **extra)

        log("requested")

        if action is None:
            log("denied", "unknown action")
            return Outcome("denied", "I don't know how to do that.")

        if env.features is not None and not env.features.is_enabled(
                "action_engine"):
            log("denied", "the action engine is turned off")
            return Outcome(
                "denied",
                "Actions are turned off. Say: enable action engine")

        raw = dict(params)          # kept so a confirmed action is re-checked
                                    # from what the user actually asked for
        try:
            params = action.validate(dict(raw), env)
        except ActionError as error:
            log("denied", str(error))
            return Outcome("denied", str(error))
        except Exception as error:
            logger.exception("Validation of %r crashed", name)
            log("failed", f"validation crashed: {error!r}")
            return Outcome("failed", "I couldn't check that request safely, "
                                     "so I did nothing.")

        if level == Level.READ_ONLY:
            return self._execute(action, params, env, request_id, log, undo_of)

        def proceed(env_now):
            log("confirmed")
            try:
                fresh = action.validate(dict(raw), env_now)  # state may
            except ActionError as error:                        # have changed
                log("denied", f"no longer valid: {error}")
                return str(error)
            return self._execute(action, fresh, env_now, request_id, log,
                                 undo_of).message

        def decline(env_now):
            log("declined")
            return "Okay, I haven't done anything."

        def expire(env_now):
            log("expired", "the question was not answered")

        description = action.describe(params)

        if level == Level.REVERSIBLE:
            pending = PendingConfirmation(proceed, decline, on_expire=expire)
            prompt = (
                f"{description}\n\n"
                "Reply 'yes' to go ahead or 'no' to cancel."
            )
        else:
            phrase = action.authorisation_phrase(params)
            pending = PendingConfirmation(proceed, decline, phrase=phrase,
                                          on_expire=expire)
            prompt = (
                f"{description}\n\n"
                "This can't easily be undone. To authorise it, type exactly:\n"
                f"{phrase}\n"
                "Anything else cancels."
            )

        log("confirmation_asked")
        return Outcome("needs_confirmation", prompt, pending)

    def _execute(self, action, params, env, request_id, log, undo_of):
        try:
            result = action.run(params, env)

            if not isinstance(result, ActionResult):
                raise TypeError("an action must return an ActionResult")

        except ActionError as error:
            log("failed", str(error))
            return Outcome("failed", str(error))
        except Exception as error:
            logger.exception("Action %r failed", action.name)
            log("failed", repr(error))
            return Outcome(
                "failed",
                "Something went wrong while doing that. Check the action log "
                "(say: show action log).")

        log("succeeded", result.message.splitlines()[0] if result.message else "",
            undo=result.undo)

        if undo_of:
            self.audit.record(undo_of, action.name, action.level, "undone",
                              detail=f"by request {request_id}",
                              message_id=env.state.message_id)

        return Outcome("done", result.message)
