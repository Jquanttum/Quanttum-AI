"""Quanttum Core: the central orchestrator.

Receives a message, asks the intent layer what it means, routes it to the
matching handler, records the exchange and returns the reply. It contains no
capability logic of its own; that lives in core/handlers/.
"""

import logging
import threading
from datetime import datetime

from core.actions import ActionEngine, default_actions
from core.actions.search import index_throttle
from core.apps import AppCatalog, AppPolicy, launch_target
from core.audit import AuditLog
from core.context import ConversationState, Environment
from core.features import FeatureRegistry
from core.handlers import build_registry
from core.intent import parse_intent
from core.llm_brain import LLMBrain
from core.policy import FolderPolicy
from core.resource_monitor import get_memory_status

logger = logging.getLogger(__name__)


class Assistant:
    def __init__(self, memory, clock=datetime.now, features=None, policy=None,
                 index=None, memory_status=get_memory_status, apps=None,
                 app_catalog=None, launcher=launch_target, llm_client=None,
                 voice=None):
        self.memory = memory
        self.state = ConversationState()
        # Without explicit services, use ones that never touch disk: no
        # settings file, and a policy that allows no folders.
        self.features = features if features is not None else FeatureRegistry()
        self.policy = policy if policy is not None else FolderPolicy()
        self.index = index
        self.apps = apps if apps is not None else AppPolicy()
        self.app_catalog = (app_catalog if app_catalog is not None
                            else AppCatalog())
        # One message is handled at a time (the window runs each on a worker
        # thread); the lock also protects the shared database connection.
        self._lock = threading.RLock()
        self.cancel_event = threading.Event()
        self.llm = (LLMBrain(llm_client, self.features)
                    if llm_client is not None else None)
        if self.llm is not None:
            self.features.set_probe("local_llm", self.llm.unavailable_reason)
        self.voice = voice
        if voice is not None:
            self.features.set_probe("voice_input", voice.input_unavailable_reason)
            self.features.set_probe("voice_output",
                                    voice.output_unavailable_reason)
            self.features.set_probe(
                "voice_autosend",
                lambda: "" if self.features.is_enabled("voice_input")
                else "needs voice input")
        self.audit = AuditLog(memory.connection)
        self.actions = ActionEngine(self.audit, default_actions())
        self.env = Environment(
            memory=memory, state=self.state, clock=clock,
            features=self.features, actions=self.actions, policy=self.policy,
            index=self.index, memory_status=memory_status, apps=self.apps,
            app_catalog=self.app_catalog, launcher=launcher, llm=self.llm,
            voice=self.voice, cancel=self.cancel_event)
        self.handlers = build_registry()

    def _llm_commands_enabled(self):
        return (self.llm is not None
                and self.features.is_enabled("llm_commands")
                and self.features.is_enabled("local_llm"))

    def _warm_up_model(self):
        """Load the model into memory now, so the first answer isn't slow."""

        try:
            model = self.llm.selected_model()
            if model:
                self.llm.client.chat(model, [], keep_alive=self.llm.keep_alive(),
                                     cancel=self.cancel_event)
        except Exception:
            logger.debug("model warm-up skipped", exc_info=True)

    def start_background_services(self):
        """Start the file index refresh and model warm-up when enabled."""

        if (self.llm is not None
                and self.features.is_enabled("local_llm")):
            threading.Thread(target=self._warm_up_model, daemon=True,
                             name="quanttum-model-warmup").start()

        if self.index is None or not self.features.is_enabled("file_index"):
            return False

        roots = self.policy.roots

        if not roots:
            return False

        return self.index.refresh_in_background(
            roots, index_throttle(self.features))

    def cancel(self):
        """Stop whatever the model is generating right now."""

        self.cancel_event.set()

    def shutdown(self):
        self.cancel_event.set()

        if self.voice is not None:
            self.voice.stop_speaking()

        if self.index is not None:
            self.index.stop()

    def respond(self, message, on_token=None):
        """Handle one message. on_token(text), if given, receives a streamed
        reply piece by piece (only the local model streams)."""

        with self._lock:
            self.env.on_token = on_token

            try:
                return self._respond(message)
            finally:
                self.env.on_token = None

    def _respond(self, message):
        message = message.strip()
        self.cancel_event.clear()

        if not message:
            return "Please enter a message."

        self.state.begin_turn()
        self.state.message_id = self.memory.save("user", message)

        intent = parse_intent(message)

        if intent.name == "unknown" and self._llm_commands_enabled():
            try:
                routed = self.llm.classify(message, self.env)
            except Exception:
                logger.exception("Command understanding failed")
                routed = None

            if routed is not None:
                intent = routed

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

        # A question that was not answered this turn expires; tell the audit
        # log so an unanswered request is never mistaken for a decision.
        leftover = self.state.previous_pending
        if leftover is not None:
            self.state.previous_pending = None
            if leftover.on_expire is not None:
                try:
                    leftover.on_expire(self.env)
                except Exception:
                    logger.exception("on_expire failed")

        self.memory.save("assistant", answer)

        return answer
