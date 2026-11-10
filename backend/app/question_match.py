"""Lexical question-match ordering for the screening queue (X.199, before active learning can learn
anything from decisions): a BM25-style score of each record's title+abstract against the project's
research-question text and inclusion-criteria terms, with the matched terms returned for display.

No model, no embeddings, no learning from decisions — a fixed, deterministic scoring function over
whatever the project's current research questions and inclusion criteria say. This is an order only:
it never excludes a record and never counts as, or feeds, a decision.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Callable, Sequence, TypeVar

_WORD = re.compile(r"[^\W\d_]+|\d+", re.UNICODE)
_MIN_WORD_LEN = 2
K1 = 1.5
B = 0.75

# A small, fixed English stopword set: common function words that would otherwise dominate the
# matched-terms list without saying anything about relevance.
STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "but", "by", "can", "did", "do",
    "does", "doing", "down", "for", "from", "had", "has", "have", "having", "how", "if", "in",
    "into", "is", "it", "its", "itself", "may", "might", "more", "most", "must", "no", "nor",
    "not", "of", "off", "on", "once", "only", "or", "other", "our", "out", "over", "own", "same",
    "shall", "should", "so", "some", "such", "than", "that", "the", "their", "them", "then",
    "there", "these", "they", "this", "those", "through", "to", "too", "under", "until", "up",
    "very", "was", "we", "were", "what", "when", "where", "which", "while", "who", "whom", "why",
    "will", "with", "would", "you", "your",
})

T = TypeVar("T")


def tokenize(text: str) -> list[str]:
    """Unicode-aware lowercasing and word splitting, with stopwords and very short tokens dropped."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return [w for w in _WORD.findall(folded) if len(w) >= _MIN_WORD_LEN and w not in STOPWORDS]


@dataclass(frozen=True)
class Scored:
    index: int
    score: float
    matched_terms: tuple[str, ...]  # sorted, deduplicated, lowercase


def score_documents(documents: Sequence[str], query_terms: Sequence[str]) -> list[Scored]:
    """BM25 (k1=1.5, b=0.75) score of each of `documents` against the vocabulary of `query_terms`,
    with IDF computed from `documents` itself (the candidate set). `documents[i]` scores `Scored(i, ...)`.

    Deterministic: no randomness, no state outside the arguments. An empty `documents` or no
    (tokenizable) `query_terms` scores everything 0 with no matched terms.
    """
    query_tokens = sorted({token for term in query_terms for token in tokenize(term)})
    n = len(documents)
    doc_tokens = [tokenize(doc) for doc in documents]
    if n == 0 or not query_tokens:
        return [Scored(index=i, score=0.0, matched_terms=()) for i in range(n)]
    doc_term_freq = [
        {token: toks.count(token) for token in query_tokens if token in toks}
        for toks in doc_tokens
    ]
    doc_freq = {
        token: sum(1 for freqs in doc_term_freq if token in freqs)
        for token in query_tokens
    }
    avg_len = sum(len(toks) for toks in doc_tokens) / n
    results = []
    for i, toks in enumerate(doc_tokens):
        length = len(toks)
        length_norm = K1 * (1 - B + B * (length / avg_len if avg_len else 0.0))
        total = 0.0
        matched = []
        for token, tf in doc_term_freq[i].items():
            df = doc_freq[token]
            idf = math.log((n - df + 0.5) / (df + 0.5) + 1)
            total += idf * (tf * (K1 + 1)) / (tf + length_norm)
            matched.append(token)
        results.append(Scored(index=i, score=total, matched_terms=tuple(sorted(set(matched)))))
    return results


def rank(items: Sequence[T], query_terms: Sequence[str], text_of: Callable[[T], str]) -> list[tuple[T, tuple[str, ...]]]:
    """`items` paired with their matched terms, ordered by BM25 score (desc) against `query_terms`;
    ties keep `items`' original order (oldest-first, by convention of the caller), so this is a
    reordering, never a shuffle of equally-scored items. Never drops or adds an item."""
    documents = [text_of(item) for item in items]
    scored = score_documents(documents, query_terms)
    paired = [(scored[i].score, i, items[i], scored[i].matched_terms) for i in range(len(items))]
    paired.sort(key=lambda t: (-t[0], t[1]))
    return [(item, matched) for _, _, item, matched in paired]


__all__ = ["STOPWORDS", "Scored", "rank", "score_documents", "tokenize"]
