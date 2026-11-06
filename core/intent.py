def detect_intent(message):
    text = message.strip().lower()
    if any(word in text for word in ("hello", "hi", "hey", "good morning", "good evening")):
        return "greeting"
    if "time" in text or "clock" in text:
        return "time"
    if "date" in text or "what day" in text or "today" in text:
        return "date"
    if text in {"help", "what can you do?", "what can you do"}:
        return "help"
    return "unknown"
