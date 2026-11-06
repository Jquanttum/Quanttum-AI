from datetime import datetime
from core.intent import detect_intent

class Assistant:
    def __init__(self, memory):
        self.memory = memory

    def respond(self, message):
        intent = detect_intent(message)
        now = datetime.now()

        if intent == "greeting":
            answer = "Hello. I'm Quanttum AI. My local assistant core is running."
        elif intent == "time":
            answer = f"The current time is {now.strftime('%I:%M %p')}."
        elif intent == "date":
            answer = f"Today is {now.strftime('%A, %d %B %Y')}."
        elif intent == "help":
            answer = (
                "I can currently handle greetings, the date and time, and basic help. "
                "Persistent chat memory is enabled. Local AI, voice and reminders "
                "will be added in later development stages."
            )
        else:
            answer = (
                "I don't have a response for that yet. My lightweight intent engine "
                "is active, but the local language model has not been connected."
            )

        self.memory.save("user", message)
        self.memory.save("assistant", answer)
        return answer
