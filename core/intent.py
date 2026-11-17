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
    "recall_category",
    "recall",
    "related",
    "forget",
    "show_features",
    "show_profile",
    "set_feature",
    "set_profile",
    "system_info",
    "find_files",
    "show_allowed_folders",
    "allow_folder",
    "disallow_folder",
    "open_app",
    "allow_app",
    "disallow_app",
    "show_allowed_apps",
    "create_folder",
    "undo",
    "index_status",
    "rebuild_index",
    "show_action_log",
    "authorise",
    "confirm_yes",
    "confirm_no",
    "confirm_alt",
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

# "remember to call X" is a task, not a fact about the user.
TASK_RE = re.compile(
    r"^(?:remember|don't\s+forget|do\s+not\s+forget)\s+to\s+(.+)$",
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

CATEGORY_WORDS = {
    "task": "task", "tasks": "task", "todo": "task", "todos": "task",
    "to-do": "task", "to-dos": "task",
    "preference": "preference", "preferences": "preference",
    "fact": "general", "facts": "general",
}

RECALL_CATEGORY_RE = re.compile(
    r"^(?:what\s+do\s+you\s+remember\s+about\s+my|show\s+my|list\s+my)"
    r"\s+(tasks?|to-?dos?|preferences?|facts?)$",
    re.IGNORECASE
)

RELATED_RE = re.compile(
    r"^(?:what(?:'s|\s+is)\s+)?related\s+to\s+#?(\d+)$",
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
# Settings commands: profiles and feature toggles.
# ---------------------------------------------------------------------------

SHOW_FEATURES_RE = re.compile(
    r"^(?:(?:show|list)(?:\s+(?:my|all|the))?\s+)?(?:features|settings|toggles)$",
    re.IGNORECASE
)

SHOW_PROFILE_RE = re.compile(
    r"^(?:(?:show|what(?:'s|\s+is))(?:\s+(?:my|the|current))*\s+)?profile$",
    re.IGNORECASE
)

SET_PROFILE_RES = (
    re.compile(
        r"^(?:use|set|switch\s+to)\s+(?:the\s+)?(lite|standard|full)\s+profile$",
        re.IGNORECASE),
    re.compile(
        r"^(?:use|set|switch\s+to)\s+profile\s+(?:to\s+)?(lite|standard|full)$",
        re.IGNORECASE),
)

SET_FEATURE_RE = re.compile(
    r"^(enable|disable|turn\s+on|turn\s+off)\s+(?:feature\s+)?(.+)$",
    re.IGNORECASE
)

RESET_FEATURE_RE = re.compile(
    r"^reset\s+feature\s+(.+)$",
    re.IGNORECASE
)


def _parse_settings_command(message):
    """Return an Intent for a settings command, or None."""

    text = " ".join(message.split()).replace("\u2019", "'")
    spoken = text.rstrip(" ?.!")

    if SHOW_FEATURES_RE.match(spoken):
        return Intent("show_features", {}, 1.0, message)

    if SHOW_PROFILE_RE.match(spoken):
        return Intent("show_profile", {}, 1.0, message)

    for pattern in SET_PROFILE_RES:
        match = pattern.match(spoken)
        if match:
            entities = {"profile": match.group(1).casefold()}
            return Intent("set_profile", entities, 1.0, message)

    match = RESET_FEATURE_RE.match(spoken)
    if match:
        entities = {"feature": match.group(1).strip(), "enabled": None}
        return Intent("set_feature", entities, 1.0, message)

    match = SET_FEATURE_RE.match(spoken)
    if match:
        enabled = match.group(1).casefold().split()[-1] in ("enable", "on")
        entities = {"feature": match.group(2).strip(), "enabled": enabled}
        return Intent("set_feature", entities, 1.0, message)

    return None


# ---------------------------------------------------------------------------
# Action commands: system, files, folders.
# ---------------------------------------------------------------------------

SYSTEM_INFO_RE = re.compile(
    r"^(?:show\s+)?(?:system\s+(?:info|information|status)|sysinfo)$",
    re.IGNORECASE
)

FIND_RES = (
    re.compile(
        r"^(?:find|locate)\s+(?:the\s+|my\s+)?"
        r"(?:(files?|folders?|directory|directories)\s+)?(.+)$",
        re.IGNORECASE),
    re.compile(
        r"^where(?:'s|\s+is)\s+(?:the\s+|my\s+)?"
        r"(files?|folders?|directory)\s+(.+)$",
        re.IGNORECASE),
    re.compile(
        r"^search\s+(?:for\s+)?(files?|folders?)\s+(?:for\s+)?(.+)$",
        re.IGNORECASE),
)

SHOW_ALLOWED_RE = re.compile(
    r"^(?:show|list)\s+(?:my\s+)?allowed\s+folders$", re.IGNORECASE)

ALLOW_FOLDER_RE = re.compile(
    r"^allow\s+(?:folder|directory)\s+(.+)$", re.IGNORECASE)

DISALLOW_FOLDER_RES = (
    re.compile(r"^(?:remove|revoke)\s+allowed\s+(?:folder|directory)\s+(.+)$",
               re.IGNORECASE),
    re.compile(r"^disallow\s+(?:folder\s+|directory\s+)?(.+)$", re.IGNORECASE),
    re.compile(r"^stop\s+allowing\s+(?:folder\s+|directory\s+)?(.+)$",
               re.IGNORECASE),
)

CREATE_FOLDER_RE = re.compile(
    r"^(?:create|make)\s+(?:a\s+|the\s+)?(?:new\s+)?(?:folder|directory)"
    r"\s+(?:called\s+|named\s+)?(.+)$",
    re.IGNORECASE
)

_APP_WORD = r"(?:app|application|program)"

OPEN_APP_RES = (
    # "open file/folder ..." is reserved for opening files later
    re.compile(r"^(?:open|launch)\s+"
               r"(?!(?:the\s+)?(?:files?|folders?|directory|directories)\b)"
               r"(?:the\s+)?(?:" + _APP_WORD + r"\s+)?(.+)$",
               re.IGNORECASE),
    re.compile(r"^start\s+(?:the\s+)?" + _APP_WORD + r"\s+(.+)$",
               re.IGNORECASE),
)

ALLOW_APP_RE = re.compile(r"^allow\s+" + _APP_WORD + r"\s+(.+)$", re.IGNORECASE)

DISALLOW_APP_RES = (
    re.compile(r"^(?:remove|revoke)\s+allowed\s+" + _APP_WORD + r"\s+(.+)$",
               re.IGNORECASE),
    re.compile(r"^disallow\s+" + _APP_WORD + r"\s+(.+)$", re.IGNORECASE),
    re.compile(r"^stop\s+allowing\s+" + _APP_WORD + r"\s+(.+)$", re.IGNORECASE),
)

SHOW_ALLOWED_APPS_RE = re.compile(
    r"^(?:show|list)\s+(?:my\s+)?allowed\s+(?:apps|applications|programs)$",
    re.IGNORECASE)

UNDO_RE = re.compile(
    r"^undo(?:\s+(?:that|it|last|the\s+last)(?:\s+action)?)?$", re.IGNORECASE)

INDEX_STATUS_RE = re.compile(
    r"^(?:show\s+)?(?:file\s+)?index\s+status$", re.IGNORECASE)

REBUILD_INDEX_RE = re.compile(
    r"^(?:rebuild|refresh|update)\s+(?:the\s+)?(?:file\s+)?index$",
    re.IGNORECASE)

ACTION_LOG_RE = re.compile(
    r"^(?:show|view)\s+(?:the\s+)?(?:action|audit)\s+(?:log|history)$",
    re.IGNORECASE)

# Typed authorisation for destructive actions, e.g. "confirm delete reports".
AUTHORISE_RE = re.compile(r"^confirm\s+.+$", re.IGNORECASE)

_ABSOLUTE_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|/|~)")
_IN_SPLIT = re.compile(r"^(.*?)\s+(?:in|inside|under)\s+(.+)$", re.IGNORECASE)
_IN_TAIL = re.compile(r"^(?:in|inside|under)\s+(.+)$", re.IGNORECASE)

_FIND_KIND = {"file": "file", "files": "file", "folder": "folder",
              "folders": "folder", "directory": "folder",
              "directories": "folder"}


def _split_name_and_location(rest):
    """'Reports in Documents' -> ('Reports', 'Documents'). A full path is
    never split, and a quoted name can contain the word 'in'."""

    rest = rest.strip()

    if rest[:1] in ("\"", "'"):
        closing = rest.find(rest[0], 1)
        if closing > 0:
            name = rest[1:closing]
            tail = _IN_TAIL.match(rest[closing + 1:].strip())
            return name, (tail.group(1).strip() if tail else "")

    if _ABSOLUTE_PATH.match(rest):
        return rest, ""

    match = _IN_SPLIT.match(rest)

    if match:
        return match.group(1).strip(), match.group(2).strip()

    return rest, ""


def _parse_action_command(message):
    """Return an Intent for an action command, or None."""

    text = " ".join(message.split()).replace("\u2019", "'")
    spoken = text.rstrip(" ?.!")

    if SYSTEM_INFO_RE.match(spoken):
        return Intent("system_info", {}, 1.0, message)

    if SHOW_ALLOWED_RE.match(spoken):
        return Intent("show_allowed_folders", {}, 1.0, message)

    if UNDO_RE.match(spoken):
        return Intent("undo", {}, 1.0, message)

    if INDEX_STATUS_RE.match(spoken):
        return Intent("index_status", {}, 1.0, message)

    if REBUILD_INDEX_RE.match(spoken):
        return Intent("rebuild_index", {}, 1.0, message)

    if ACTION_LOG_RE.match(spoken):
        return Intent("show_action_log", {}, 1.0, message)

    if SHOW_ALLOWED_APPS_RE.match(spoken):
        return Intent("show_allowed_apps", {}, 1.0, message)

    match = ALLOW_APP_RE.match(spoken)
    if match:
        return Intent("allow_app", {"name": match.group(1).strip()}, 1.0,
                      message)

    # App patterns come before the folder ones: "disallow app x" must not
    # be read as "disallow folder 'app x'".
    for pattern in DISALLOW_APP_RES:
        match = pattern.match(spoken)
        if match:
            return Intent("disallow_app", {"name": match.group(1).strip()},
                          1.0, message)

    match = ALLOW_FOLDER_RE.match(spoken)
    if match:
        return Intent("allow_folder", {"path": match.group(1).strip()}, 1.0,
                      message)

    for pattern in DISALLOW_FOLDER_RES:
        match = pattern.match(spoken)
        if match:
            return Intent("disallow_folder", {"path": match.group(1).strip()},
                          1.0, message)

    match = CREATE_FOLDER_RE.match(text.rstrip(" ?!"))
    if match:
        name, location = _split_name_and_location(match.group(1))
        return Intent("create_folder", {"name": name, "location": location},
                      1.0, message)

    for pattern in OPEN_APP_RES:
        match = pattern.match(spoken)
        if match:
            return Intent("open_app", {"name": match.group(1).strip()}, 1.0,
                          message)

    for pattern in FIND_RES:
        match = pattern.match(spoken)
        if match:
            kind = _FIND_KIND.get((match.group(1) or "").casefold(), "any")
            return Intent("find_files",
                          {"query": match.group(2).strip(), "kind": kind},
                          1.0, message)

    if AUTHORISE_RE.match(spoken):
        return Intent("authorise", {"phrase": spoken}, 1.0, message)

    return None


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

# Answers to a question the assistant just asked. They only mean something
# when a question is pending; otherwise the handler says nothing is waiting.
_YES = {"yes", "y", "yeah", "yep", "replace", "replace it", "confirm", "do it"}
_NO = {"no", "n", "nope", "cancel", "never mind", "nevermind", "don't",
       "dont", "no don't", "discard"}
_ALT = {"keep both", "both", "save both", "keep them both", "keep both of them"}

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

    text = " ".join(message.split()).replace("\u2019", "'")
    spoken = text.rstrip(" ?.!")

    match = FORGET_ID_RE.match(spoken)
    if match:
        return Intent("forget", {"id": int(match.group(1))}, 1.0, message)

    if FORGET_REFERENCE_RE.match(spoken):
        return Intent("forget", {"reference": "last"}, 1.0, message)

    match = TASK_RE.match(text)
    if match:
        entities = {"fact": match.group(1).strip(), "category": "task"}
        return Intent("remember", entities, 1.0, message)

    match = REMEMBER_RE.match(text)
    if match:
        return Intent("remember", {"fact": match.group(1).strip()}, 1.0, message)

    if RECALL_ALL_RE.match(spoken):
        return Intent("recall_all", {}, 1.0, message)

    match = RECALL_CATEGORY_RE.match(spoken)
    if match:
        category = CATEGORY_WORDS[match.group(1).casefold()]
        return Intent("recall_category", {"category": category}, 1.0, message)

    match = RELATED_RE.match(spoken)
    if match:
        return Intent("related", {"id": int(match.group(1))}, 1.0, message)

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

    if text in _YES:
        return "confirm_yes"

    if text in _NO:
        return "confirm_no"

    if text in _ALT:
        return "confirm_alt"

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

    if intent is None:
        intent = _parse_settings_command(message)

    if intent is None:
        intent = _parse_action_command(message)

    if intent is not None:
        return intent

    name = _parse_basic(message)
    confidence = 0.0 if name == "unknown" else 1.0

    return Intent(name, {}, confidence, message)


def detect_intent(message):
    """Return just the intent name (kept for simple callers and tests)."""

    return parse_intent(message).name
