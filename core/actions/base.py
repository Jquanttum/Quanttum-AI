"""Building blocks of the Action Engine."""

from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Optional


class Level(IntEnum):
    """How dangerous an action is. The engine enforces what each level needs.

    READ_ONLY    runs immediately (reading system info, searching files).
    REVERSIBLE   asks for a yes/no first and records how to undo it.
    DESTRUCTIVE  needs the user to type an exact authorisation phrase.
    """

    READ_ONLY = 1
    REVERSIBLE = 2
    DESTRUCTIVE = 3


class ActionError(Exception):
    """A refusal or failure whose message is safe to show the user."""


@dataclass
class ActionResult:
    message: str
    undo: Optional[dict] = None      # {"action": name, "params": {...}}
    data: Any = None


@dataclass
class Outcome:
    """What the engine hands back to a handler.

    status: done | failed | denied | needs_confirmation
    pending: set when the engine is waiting for the user's answer.
    """

    status: str
    message: str
    pending: Any = None


class Action:
    """Base class. Subclasses are fixed, named operations with validated
    parameters. There is deliberately no action that runs arbitrary text."""

    name = ""
    level = Level.READ_ONLY
    title = ""

    def validate(self, params, env):
        """Check and normalise parameters; raise ActionError to refuse."""

        return params

    def describe(self, params):
        """A plain-language description shown in confirmation prompts."""

        return self.title or self.name

    def authorisation_phrase(self, params):
        """The exact phrase a DESTRUCTIVE action needs the user to type."""

        return f"confirm {self.name}"

    def run(self, params, env):
        raise NotImplementedError
