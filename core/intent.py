"""Intent detection: turns raw text into a structured Intent.

Detection is rule-based (whole-word regular expressions, no model), so it is
fast, predictable and needs no extra dependencies. Anything that can produce
an Intent (for example a future local-LLM detector) can replace parse_intent()
without touching the handlers.
"""

import re
from dataclasses import dataclass, field

# Every intent name parse_intent() can return. The handler registry must have
# one handler for each of these (a test enforces it).
INTENT_NAMES = (
    "greeting",
    "time",
    "date",
    "help",
    "remember",
    "recall_all",
    "recall",
    "forget",
    "unknown",
)


@dataclass(frozen=True)
class Intent:
    """What the user meant, plus any details extracted from the text.

    name        one of INTENT_NAMES
    entities    extracted details, e.g. {"id": 3} or {"fact": "I like tea"}
    confidence  1.0 for an exact rule match, 0.0 for "unknown". Rule-based
                detection is all-or-nothing; the field exists so a
                probabilistic detector can report uncertainty later.
    raw         the original message
    """

    name: str
    entities: dict = field(default_factory=dict)
    confidence: float = 1.0
    raw: str = ""


# ---------------------------------------------------------------------------
# Memory commands. Matched against the message with extra whitespace collapsed
# and (for recall/forget) trailing ? . ! removed.
# ---------------------------------------------------------------------------

FORGET_ID_RE = re.compile(
    r"^forget(?:\s+memory)?\s+#?(\d+)$",
    re.IGNORECASE
)

# "forget that", "forget it", "forget that one" ... refers to whatever the
# conversation was just talking about; the handler resolves it.
FORGET_REFERENCE_RE = re.compile(
    r"^forget\s+(?:that|it|this|that\s+one|this\s+one|the\s+last\s+one)$",
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

# The topic is mandatory (\s+(.+)), so "Recalling last week" does not match
# the word "recall".
RECALL_SPECIFIC_RE = re.compile(
    r"^(?:what\s+do\s+you\s+remember\s+about|"
    r"what\s+have\s+you\s+remembered\s+about|recall|"
    r"search\s+my\s+memories\s+for|find\s+in\s+my\s+memories)\s+(.+)$",
    re.IGNORECASE
)

# ---------------------------------------------------------------------------
# Basic conversational intents. Matched on whole words/phrases (\b), never on
# raw substrings: the original `"hi" in text` also matched "this" and "which".
# ---------------------------------------------------------------------------

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


def _parse_memory_command(message):
    """Return an Intent for a memory command, or None."""

    text = " ".join(message.split())
    spoken = text.rstrip(" ?.!")

    match = FORGET_ID_RE.match(spoken)
    if match:
        return Intent("forget", {"id": int(match.group(1))}, 1.0, message)

    if FORGET_REFERENCE_RE.match(spoken):
        return Intent("forget", {"reference": "last"}, 1.0, message)

    match = REMEMBER_RE.match(text)
    if match:
        return Intent("remember", {"fact": match.group(1).strip()}, 1.0, message)

    if RECALL_ALL_RE.match(spoken):
        return Intent("recall_all", {}, 1.0, message)

    match = RECALL_SPECIFIC_RE.match(spoken)
    if match:
        return Intent("recall", {"query": match.group(1).strip()}, 1.0, message)

    return None


def _parse_basic(message):
    """Return the name of a basic intent, or 'unknown'."""

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


def parse_intent(message):
    """Turn a message into an Intent. Memory commands take priority."""

    intent = _parse_memory_command(message)

    if intent is not None:
        return intent

    name = _parse_basic(message)
    confidence = 0.0 if name == "unknown" else 1.0

    return Intent(name, {}, confidence, message)


def detect_intent(message):
    """Return just the intent name (kept for simple callers and tests)."""

    return parse_intent(message).name
