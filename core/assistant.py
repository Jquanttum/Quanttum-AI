
from datetime import datetime
import re

from core.intent import detect_intent

# Memory command patterns. They are matched against the message with extra
# whitespace collapsed and, for recall/forget, trailing ? . ! removed.
FORGET_RE = re.compile(
    r"^forget(?:\s+memory)?\s+#?(\d+)$",
    re.IGNORECASE
)

REMEMBER_RE = re.compile(
    r"^(?:remember(?:\s+that)?|don't\s+forget(?:\s+that)?|"
    r"do\s+not\s+forget(?:\s+that)?)\s+(.+)$",
    re.IGNORECASE
)

RECALL_ALL_RE = re.compile(
    r"^(?:what\s+do\s+you\s+remember(?:\s+about\s+(?:me|myself))?|"
    r"what\s+have\s+you\s+remembered(?:\s+about\s+(?:me|myself))?|"
    r"(?:show|list)(?:\s+all)?(?:\s+my)?\s+memories)$",
    re.IGNORECASE
)

# The topic is mandatory (\s+(.+)), so "Recalling last week" no longer
# matches the word "recall".
RECALL_SPECIFIC_RE = re.compile(
    r"^(?:what\s+do\s+you\s+remember\s+about|"
    r"what\s+have\s+you\s+remembered\s+about|recall|"
    r"search\s+my\s+memories\s+for|find\s+in\s+my\s+memories)\s+(.+)$",
    re.IGNORECASE
)

# "Show everything" must not be capped at the default search limit of 5.
LIST_LIMIT = 50


class Assistant:
    def __init__(self, memory):
        self.memory = memory

    @staticmethod
    def _format_records(records):
        return "\n".join(f"#{memory_id}  {fact}" for memory_id, fact in records)

    def _handle_memory_command(self, message):
        """Handle explicit memory storage, retrieval and forgetting."""

        text = " ".join(message.split())
        spoken = text.rstrip(" ?.!")

        forget_match = FORGET_RE.match(spoken)

        if forget_match:
            memory_id = int(forget_match.group(1))
            fact = self.memory.get(memory_id)

            if fact is None or not self.memory.forget(memory_id):
                return f"I don't have an active memory with ID #{memory_id}."

            return f"I've forgotten memory #{memory_id}: {fact}"

        remember_match = REMEMBER_RE.match(text)

        if remember_match:
            fact = remember_match.group(1).strip()

            if self.memory.remember(fact):
                return f"I've saved this to long-term memory: {fact}"

            return "I couldn't save that memory. Please try again."

        if RECALL_ALL_RE.match(spoken):
            records = self.memory.search_records(limit=LIST_LIMIT)

            if not records:
                return "I don't have any saved long-term memories yet."

            return (
                "Here is what I remember:\n"
                f"{self._format_records(records)}\n\n"
                "To remove one, say: forget #<number>"
            )

        recall_match = RECALL_SPECIFIC_RE.match(spoken)

        if recall_match:
            query = recall_match.group(1).strip()
            records = self.memory.search_records(query)

            if not records:
                return f"I couldn't find any saved memories related to '{query}'."

            return (
                "I found these related memories:\n"
                f"{self._format_records(records)}"
            )

        return None

    def respond(self, message):
        message = message.strip()

        if not message:
            return "Please enter a message."

        self.memory.save("user", message)

        # Process explicit memory commands first.
        memory_answer = self._handle_memory_command(message)

        if memory_answer is not None:
            self.memory.save("assistant", memory_answer)
            return memory_answer

        intent = detect_intent(message)
        now = datetime.now()

        if intent == "greeting":
            answer = (
                "Hello. I'm Quanttum AI. "
                "My local assistant core is running."
            )

        elif intent == "time":
            answer = f"The current time is {now.strftime('%I:%M %p')}."

        elif intent == "date":
            answer = f"Today is {now.strftime('%A, %d %B %Y')}."

        elif intent == "help":
            answer = (
                "I can currently handle greetings, the date, time, "
                "and long-term memory.\n\n"
                "Try these commands:\n"
                "- Remember that I prefer Python.\n"
                "- What do you remember about me?\n"
                "- What do you remember about Python?\n"
                "- Forget #1 (use the number shown in the memory list)\n\n"
                "More advanced intelligence, voice and automation "
                "are planned for future versions."
            )

        else:
            # Lightweight contextual memory retrieval.
            memories = self.memory.search(message)

            if memories:
                results = "\n".join(
                    f"- {fact}" for fact in memories
                )

                answer = (
                    "I found some stored information that may be "
                    f"relevant:\n{results}\n\n"
                    "I can retrieve these memories, but my advanced "
                    "reasoning engine is not connected yet."
                )

            else:
                answer = (
                    "I don't have a response for that yet. My lightweight "
                    "intent and memory systems are active, but my advanced "
                    "intelligence engine has not been connected."
                )

        self.memory.save("assistant", answer)

        return answer
