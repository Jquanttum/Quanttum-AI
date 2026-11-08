"""Lightweight rule-based intent detection.

Matching is done on whole words/phrases (regular expressions with \\b), never
on raw substrings. The old version used `"hi" in text`, which also matched
"this", "which" and "machine".
"""

import re

_GREETING = re.compile(
    r"^(?:hello|hi|hey|good\s+(?:morning|afternoon|evening|day))\b"
)

_TIME_PATTERNS = (
    re.compile(r"\bwhat\s+time\b"),
    re.compile(r"\b(?:the|current)\s+time\b"),
    re.compile(r"\bclock\b"),
    re.compile(r"^time$"),
)

_DATE_PATTERNS = (
    re.compile(r"\bwhat\s+day\b"),
    re.compile(r"\b(?:the|today'?s|current)\s+date\b"),
    re.compile(r"\bdate\s+today\b"),
    re.compile(r"\bwhat(?:'s|\s+is)\s+today\b"),
    re.compile(r"^(?:date|today)$"),
)

_HELP = {
    "help",
    "help me",
    "commands",
    "show commands",
    "what can you do",
    "what can i ask",
    "what can i ask you",
}

# Time/date questions are short. Ignoring long sentences avoids treating
# "by the time I get home..." as a request for the clock.
_MAX_WORDS_FOR_TIME_DATE = 7
_MAX_WORDS_FOR_GREETING = 6


def _normalise(message):
    text = message.strip().lower().replace("\u2019", "'")
    text = re.sub(r"[^\w\s']", " ", text)
    return " ".join(text.split())


def _matches_any(patterns, text):
    return any(pattern.search(text) for pattern in patterns)


def detect_intent(message):
    text = _normalise(message)
    word_count = len(text.split())

    if text in _HELP:
        return "help"

    if word_count <= _MAX_WORDS_FOR_TIME_DATE:
        if _matches_any(_TIME_PATTERNS, text):
            return "time"
        if _matches_any(_DATE_PATTERNS, text):
            return "date"

    if word_count <= _MAX_WORDS_FOR_GREETING and _GREETING.search(text):
        return "greeting"

    return "unknown"
