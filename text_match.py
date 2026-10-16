"""text_match — does a customer's phrase name this supplier value?

One matcher for every "is this the thing they asked for" decision, so a name
lookup, a cuisine filter and a dish search cannot disagree about the same
words.

Why not plain substring
-----------------------
The customer's spelling and the supplier's differ: "Kathiyawadi" is listed as
"Kathhiyawadi Thali Restaurant". A substring test misses that, while a test
that is too loose calls "Indus" a match for "Indian" -- which is how an
unknown restaurant gets resolved to one we sell. Matching is therefore per
word, and every significant word of the query must find a counterpart.

A word matches when it is equal, when it is a prefix of a supplier word
("veg" -> "vegetarian"), or when the two spellings are close by edit
similarity. The similarity floor sits between the two cases above:
"kathiyawadi"/"kathhiyawadi" score 0.96, "indus"/"indian" 0.73,
"italian"/"indian" 0.77.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from difflib import SequenceMatcher

_WORD_RE = re.compile(r"[a-z0-9]+")

#: Edit similarity at or above which two spellings are the same word.
SIMILARITY_FLOOR = 0.8

#: Words shorter than this carry no identity on their own ("of", "at").
MIN_TERM_LEN = 3


def words(text: str | None) -> list[str]:
    """Lower-case alphanumeric words, in order."""
    return _WORD_RE.findall((text or "").lower())


def terms(text: str | None) -> list[str]:
    """The words of a query that carry meaning."""
    return [w for w in words(text) if len(w) >= MIN_TERM_LEN]


def _word_matches(term: str, word: str) -> bool:
    if term == word or word.startswith(term):
        return True
    # Similarity only between words long enough for it to mean anything; short
    # words are compared exactly or by prefix above.
    if min(len(term), len(word)) < 4:
        return False
    return SequenceMatcher(None, term, word).ratio() >= SIMILARITY_FLOOR


def match_fraction(query: str | None, text: str | None) -> float:
    """Share of the query's significant words found in `text` (0.0 - 1.0)."""
    q = terms(query)
    if not q:
        return 0.0
    target = words(text)
    hits = sum(1 for t in q if any(_word_matches(t, w) for w in target))
    return hits / len(q)


def matches(query: str | None, text: str | None) -> bool:
    """True when EVERY significant word of `query` appears in `text`."""
    return bool(terms(query)) and match_fraction(query, text) == 1.0


def matches_any(query: str | None, values: Iterable[str]) -> list[str]:
    """The values that `query` matches on their own, in the order given.

    Values are tested one at a time: "jain food" must not match a restaurant
    tagged "Jain Friendly" + "Gujarati Food" by borrowing a word from each.
    """
    return [v for v in values if matches(query, v)]


def starts_with(text: str | None, query: str | None) -> bool:
    """True when `text` BEGINS with the words of `query`, in order.

    Stricter than `matches`: "Dubai Mall Kidzania" starts with "Dubai Mall",
    while "Apartment near Dubai Mall" -- a different thing named after it --
    does not.
    """
    q, t = words(query), words(text)
    if not q or len(t) < len(q):
        return False
    return all(_word_matches(a, b) for a, b in zip(q, t, strict=False))


def without_words(text: str, drop: str) -> str:
    """`text` minus the words of `drop` ("Dubai Marina" minus "Dubai")."""
    dropped = set(words(drop))
    kept = [w for w in words(text) if w not in dropped]
    return " ".join(kept)
