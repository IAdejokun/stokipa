"""Deterministic item-name matching — the backstop that keeps LLM matching
honest. The LLM proposes inventory_item_id; we verify with fuzzy similarity.
If the LLM's match and the fuzzy match disagree badly, we treat the line as
unmatched and ask the owner rather than guessing with their money.
"""

import unicodedata
from difflib import SequenceMatcher

from app.models import Item

MATCH_THRESHOLD = 0.6


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return " ".join(s.lower().split())


def _similarity(a: str, b: str) -> float:
    a, b = normalize(a), normalize(b)
    if not a or not b:
        return 0.0
    if a in b or b in a:  # "rice" inside "rice (50kg bag)"
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def best_match(spoken: str, items: list[Item]) -> tuple[Item | None, float]:
    """Best (item, score) across canonical names and aliases."""
    best: Item | None = None
    best_score = 0.0
    for item in items:
        candidates = [item.name, *(item.aliases or [])]
        score = max(_similarity(spoken, c) for c in candidates)
        if score > best_score:
            best, best_score = item, score
    return best, best_score


def resolve(
    spoken: str, llm_item_id: int | None, items: list[Item]
) -> Item | None:
    """Reconcile the LLM's proposed match with fuzzy matching.

    - LLM proposed an id that exists AND fuzzy agrees it's plausible -> take it.
    - LLM proposed nothing but fuzzy finds a confident match -> take fuzzy.
    - Otherwise -> None (unmatched; ask the owner).
    """
    by_id = {i.id: i for i in items}
    fuzzy_item, fuzzy_score = best_match(spoken, items)

    if llm_item_id is not None and llm_item_id in by_id:
        llm_item = by_id[llm_item_id]
        if _similarity(spoken, llm_item.name) >= 0.3 or any(
            _similarity(spoken, a) >= 0.3 for a in (llm_item.aliases or [])
        ):
            return llm_item
        # LLM picked something that doesn't resemble what was said — distrust.
        return fuzzy_item if fuzzy_score >= MATCH_THRESHOLD else None

    return fuzzy_item if fuzzy_score >= MATCH_THRESHOLD else None