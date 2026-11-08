"""Short-term conversation state shared by the handlers.

This is what lets a follow-up like "forget that" make sense: the Core records
which memories the last turn created or displayed, and the next turn can refer
to them. It lives in RAM only and starts empty each time the app opens.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional


@dataclass
class PendingConfirmation:
    """A question the Core has asked and is waiting to have answered.

    Each callback takes the Environment and returns the reply text. on_alt is
    the optional third answer (for example "keep both"). The same pattern
    will carry confirmations for Level 2/3 computer actions later.
    """

    on_yes: Callable
    on_no: Callable
    on_alt: Optional[Callable] = None


@dataclass
class ConversationState:
    # Memory IDs the CURRENT turn created or displayed. Handlers write here.
    focus_ids: list = field(default_factory=list)

    # Memory IDs the PREVIOUS turn created or displayed. Handlers read here.
    previous_focus_ids: list = field(default_factory=list)

    # A question asked this turn / the question the user is answering now.
    pending: Optional[PendingConfirmation] = None
    previous_pending: Optional[PendingConfirmation] = None

    # ID of the user's message in the database for the current turn.
    message_id: Optional[int] = None

    def begin_turn(self):
        """Roll the state forward. A reference like "that", or a yes/no, only
        works on the turn right after the one it points at, so it can never
        silently reach back to something from several messages ago. A
        question that is not answered straight away is dropped."""

        self.previous_focus_ids = self.focus_ids
        self.focus_ids = []
        self.previous_pending = self.pending
        self.pending = None


@dataclass
class Environment:
    """Everything a handler is allowed to use."""

    memory: Any
    state: ConversationState
    clock: Callable[[], datetime] = datetime.now
    features: Any = None            # FeatureRegistry
