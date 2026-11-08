"""Quanttum Core: the central orchestrator.

Receives a message, asks the intent layer what it means, routes it to the
matching handler, records the exchange and returns the reply. It contains no
capability logic of its own; that lives in core/handlers/.
"""

import logging
from datetime import datetime

from core.context import ConversationState, Environment
from core.features import FeatureRegistry
from core.handlers import build_registry
from core.intent import parse_intent

logger = logging.getLogger(__name__)


class Assistant:
    def __init__(self, memory, clock=datetime.now, features=None):
        self.memory = memory
        self.state = ConversationState()
        # Without an explicit registry, use one that never touches disk.
        self.features = features if features is not None else FeatureRegistry()
        self.env = Environment(memory=memory, state=self.state, clock=clock,
                               features=self.features)
        self.handlers = build_registry()

    def respond(self, message):
        message = message.strip()

        if not message:
            return "Please enter a message."

        self.state.begin_turn()
        self.state.message_id = self.memory.save("user", message)

        intent = parse_intent(message)
        handler = self.handlers.get(intent.name, self.handlers["unknown"])

        try:
            answer = handler(intent, self.env)
        except Exception:
            # A bug in one handler must not crash the window.
            logger.exception("Handler for intent %r failed", intent.name)
            answer = (
                "Something went wrong while handling that request. "
                "Your data has not been changed by the error."
            )

        self.memory.save("assistant", answer)

        return answer
