"""Small text helpers shared by the memory engine and the handlers.

Pure functions, no database access, so they are easy to test.
"""

import re

_TOKEN_RE = re.compile(r"[^\W_]+")

# Filler words that carry no search value. Without this, "What is the
# weather like?" would "match" any stored fact containing "the" or "like".
STOPWORDS = frozenset("""
a an the and or but of to in on at for with about from by as than then
what which who whom whose when where why how
do does did done doing am is are was were be been being
i me my mine myself you your yours we our ours it its this that these those
can could would should will shall may might must
have has had having
tell know remember recall think say said get got make use find show give
look like want need please just any some there here one thing things
something anything if so not no yes
""".split())


def tokens(text):
    """All lower-cased alphanumeric words in the text, in order."""

    return _TOKEN_RE.findall(text.casefold())


def significant_terms(text, limit=12):
    """The informative words of a query: no fillers, no 1-letter words,
    no repeats, original order."""

    seen = []

    for word in tokens(text):
        if len(word) < 2 or word in STOPWORDS or word in seen:
            continue
        seen.append(word)

    return seen[:limit]


def fts_query(terms):
    """Build an FTS5 MATCH expression: each term quoted, joined by OR.

    Quoting makes every term a plain string, so user text can never be
    interpreted as FTS5 query syntax. BM25 then ranks rows that match more
    (and rarer) terms higher.
    """

    return " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)


_PREFERENCE_RE = re.compile(
    r"\b(?:prefer|prefers|favou?rite)\b|"
    r"\bi\s+(?:really\s+|also\s+)?(?:like|love|hate|dislike|enjoy|prefer)\b",
    re.IGNORECASE,
)


def categorise(fact):
    """Pick a category for a fact with a few transparent rules.

    'task' is never guessed here: it is only assigned when the user says
    "remember to ...". Anything unrecognised stays 'general'.
    """

    if _PREFERENCE_RE.search(fact):
        return "preference"

    return "general"
