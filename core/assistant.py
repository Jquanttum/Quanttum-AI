
from datetime import datetime
import re

from core.intent import detect_intent


class Assistant:
    def __init__(self, memory):
        self.memory = memory

    def _extract_memory_query(self, message):
        """Remove recall instructions and retain the actual search topic."""

        query = re.sub(
            r"^(what do you remember about|what have you remembered about|"
            r"recall|search my memories for|find in my memories)\s*",
            "",
            message.strip(),
            flags=re.IGNORECASE
        )

        return query.strip(" ?.!")

    def _handle_memory_command(self, message):
        """Handle explicit memory storage and retrieval requests."""

        remember_match = re.match(
            r"^(?:remember(?:\s+that)?|don't forget(?:\s+that)?|"
            r"do not forget(?:\s+that)?)\s+(.+)$",
            message.strip(),
            flags=re.IGNORECASE
        )

        if remember_match:
            fact = remember_match.group(1).strip()

            if self.memory.remember(fact):
                return f"I've saved this to long-term memory: {fact}"

            return "I couldn't save that memory. Please try again."

        recall_all = re.match(
            r"^(what do you remember about me|"
            r"what do you remember|show my memories|"
            r"list my memories|what have you remembered)$",
            message.strip(),
            flags=re.IGNORECASE
        )

        if recall_all:
            memories = self.memory.search()

            if not memories:
                return "I don't have any saved long-term memories yet."

            results = "\n".join(
                f"{index}. {fact}"
                for index, fact in enumerate(memories, start=1)
            )

            return f"Here is what I remember:\n{results}"

        recall_specific = re.match(
            r"^(?:what do you remember about|"
            r"what have you remembered about|recall|"
            r"search my memories for|find in my memories)\s*(.*)$",
            message.strip(),
            flags=re.IGNORECASE
        )

        if recall_specific:
            query = self._extract_memory_query(message)
            memories = self.memory.search(query)

            if not memories:
                return f"I couldn't find any saved memories related to '{query}'."

            results = "\n".join(
                f"{index}. {fact}"
                for index, fact in enumerate(memories, start=1)
            )

            return f"I found these related memories:\n{results}"

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
                "- What do you remember about Python?\n\n"
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
