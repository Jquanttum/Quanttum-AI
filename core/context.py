"""Short-term conversation state shared by the handlers.

This is what lets a follow-up like "forget that" make sense: the Core records
which memories the last turn created or displayed, and the next turn can refer
to them. It lives in RAM only and starts empty each time the app opens.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable


@dataclass
class ConversationState:
    # Memory IDs the CURRENT turn created or displayed. Handlers write here.
    focus_ids: list = field(default_factory=list)

    # Memory IDs the PREVIOUS turn created or displayed. Handlers read here.
    previous_focus_ids: list = field(default_factory=list)

    def begin_turn(self):
        """Roll the focus forward. A reference like "that" therefore only
        works on the turn right after the one it points at, so it can never
        silently reach back to something shown several messages ago."""

        self.previous_focus_ids = self.focus_ids
        self.focus_ids = []


@dataclass
class Environment:
    """Everything a handler is allowed to use."""

    memory: Any
    state: ConversationState
    clock: Callable[[], datetime] = datetime.now
