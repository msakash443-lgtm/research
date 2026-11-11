"""Active-learning prioritisation for screening (M2.6, spec 5.3.2): rank unscreened title/abstract
records by predicted relevance, learned fresh from the human decisions made so far.

No embeddings, no external model call, no new dependency: a lightweight multinomial Naive Bayes
over the title+abstract words, trained at query time from the project's current human decisions
(`include`/`maybe` are the positive class, `exclude` the negative one) and applied to whatever is
still unscreened. Deterministic (no randomness) and cheap enough to retrain on every request, so it
is always current with "after each human batch" (spec wording) without a stored model to go stale.

With no examples of both classes yet, nothing can be learned (`train` returns None); the caller
should keep the default oldest-first order in that case, not fail.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Sequence, TypeVar

_WORD = re.compile(r"[a-z0-9]+")
_MIN_WORD_LEN = 2

T = TypeVar("T")


def tokenize(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if len(w) >= _MIN_WORD_LEN]


@dataclass(frozen=True)
class Classifier:
    positive_log_prob: dict[str, float]
    negative_log_prob: dict[str, float]
    positive_prior: float  # log P(positive)
    negative_prior: float  # log P(negative)
    positive_n: int
    negative_n: int


def train(positive_texts: Sequence[str], negative_texts: Sequence[str]) -> Classifier | None:
    """A classifier from the positive (include/maybe) and negative (exclude) texts decided so far.
    None if either class has no examples yet: there is nothing to learn a direction from."""
    if not positive_texts or not negative_texts:
        return None
    pos_counts: dict[str, int] = {}
    neg_counts: dict[str, int] = {}
    for text in positive_texts:
        for w in tokenize(text):
            pos_counts[w] = pos_counts.get(w, 0) + 1
    for text in negative_texts:
        for w in tokenize(text):
            neg_counts[w] = neg_counts.get(w, 0) + 1
    vocab = set(pos_counts) | set(neg_counts)
    vocab_size = len(vocab) or 1
    pos_total, neg_total = sum(pos_counts.values()), sum(neg_counts.values())
    # Laplace (add-one) smoothing over the shared vocabulary, so an unseen-in-one-class word still
    # gets a non-zero probability instead of a zero that would wipe out the whole score.
    positive_log_prob = {w: math.log((pos_counts.get(w, 0) + 1) / (pos_total + vocab_size)) for w in vocab}
    negative_log_prob = {w: math.log((neg_counts.get(w, 0) + 1) / (neg_total + vocab_size)) for w in vocab}
    n = len(positive_texts) + len(negative_texts)
    return Classifier(
        positive_log_prob=positive_log_prob, negative_log_prob=negative_log_prob,
        positive_prior=math.log(len(positive_texts) / n), negative_prior=math.log(len(negative_texts) / n),
        positive_n=len(positive_texts), negative_n=len(negative_texts),
    )


def score(classifier: Classifier, text: str) -> float:
    """Log-odds that this record's words look like the positive (include/maybe) class rather than
    the negative one; higher ranks first. A word never seen in training contributes nothing to either
    side (ignored), not a penalty."""
    pos, neg = classifier.positive_prior, classifier.negative_prior
    for w in tokenize(text):
        pos += classifier.positive_log_prob.get(w, 0.0)
        neg += classifier.negative_log_prob.get(w, 0.0)
    return pos - neg


def rank_by_relevance(classifier: Classifier, items: Sequence[T], text_of: "callable[[T], str]") -> list[T]:
    """`items` ordered by predicted relevance, highest first; ties keep their original (oldest-first)
    order, so the ranking is a reordering, never a shuffle of equally-scored items."""
    scored = [(score(classifier, text_of(item)), position, item) for position, item in enumerate(items)]
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [item for _, _, item in scored]


__all__ = ["Classifier", "rank_by_relevance", "score", "tokenize", "train"]
