"""De-duplicate and cluster the project's ideas, and list candidate constructs (M4.7.c, spec 5.1 task 1).

Pure: no database, no model. Everything is lexical and deterministic:
- Duplicate: an idea whose set of content words is identical to an earlier idea's (case and punctuation ignored,
  word order ignored). A duplicate is reported against the earlier idea and does not count again anywhere.
- Cluster: representatives joined when the Jaccard similarity of their content words is at least LINK_THRESHOLD
  (transitively). Each cluster names the terms at least two of its members share.
- Candidate construct: a content word used by at least two representative ideas, with the ideas that use it.
  These are candidates for the scholar to keep or discard, not extracted constructs in the measurement sense.
Ideas with no content words are never duplicates and never join a cluster.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.note_suggest import tokens as content_tokens

LINK_THRESHOLD = 0.3
MAX_SHARED_TERMS = 12


@dataclass(frozen=True)
class Idea:
    idea_id: str
    text: str


@dataclass(frozen=True)
class Cluster:
    member_ids: list[str]
    shared_terms: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Construct:
    term: str
    idea_ids: list[str]


@dataclass(frozen=True)
class Clustering:
    clusters: list[Cluster]
    constructs: list[Construct]
    duplicates: dict[str, str]


def cluster_ideas(ideas: list[Idea]) -> Clustering:
    representatives: list[Idea] = []
    terms_of: dict[str, frozenset[str]] = {}
    first_by_terms: dict[frozenset[str], str] = {}
    duplicates: dict[str, str] = {}
    for idea in ideas:
        terms = frozenset(content_tokens(idea.text))
        if terms and terms in first_by_terms:
            duplicates[idea.idea_id] = first_by_terms[terms]
            continue
        if terms:
            first_by_terms[terms] = idea.idea_id
        representatives.append(idea)
        terms_of[idea.idea_id] = terms

    parent = {idea.idea_id: idea.idea_id for idea in representatives}

    def root(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for i, left in enumerate(representatives):
        for right in representatives[i + 1 :]:
            a, b = terms_of[left.idea_id], terms_of[right.idea_id]
            if a and b and len(a & b) / len(a | b) >= LINK_THRESHOLD:
                parent[root(right.idea_id)] = root(left.idea_id)

    members: dict[str, list[str]] = {}
    for idea in representatives:
        members.setdefault(root(idea.idea_id), []).append(idea.idea_id)
    clusters = [Cluster(member_ids, _shared(member_ids, terms_of)) for member_ids in members.values()]

    usage: dict[str, list[str]] = {}
    for idea in representatives:
        for term in sorted(terms_of[idea.idea_id]):
            usage.setdefault(term, []).append(idea.idea_id)
    constructs = [Construct(term, ids) for term, ids in usage.items() if len(ids) >= 2]
    constructs.sort(key=lambda c: (-len(c.idea_ids), c.term))

    return Clustering(clusters=clusters, constructs=constructs, duplicates=duplicates)


def _shared(member_ids: list[str], terms_of: dict[str, frozenset[str]]) -> list[str]:
    counts: dict[str, int] = {}
    for member in member_ids:
        for term in terms_of[member]:
            counts[term] = counts.get(term, 0) + 1
    shared = sorted((term for term, n in counts.items() if n >= 2), key=lambda t: (-counts[t], t))
    return sorted(shared[:MAX_SHARED_TERMS])


# Closed, curated vocabularies for the population/setting context extraction (spec 5.1.1, X.189): a term is a
# candidate only when it is one of these exact content words. Deliberately small and explicit rather than a
# heuristic guess, so a hit is always traceable to a named word, never an inferred one.
POPULATION_TERMS = frozenset(
    """patients participants students nurses adults children employees teachers workers clinicians consumers
    users residents caregivers physicians parents adolescents women men athletes managers survivors
    clients families couples infants veterans immigrants refugees volunteers""".split()
)
SETTING_TERMS = frozenset(
    """hospital hospitals school schools workplace workplaces clinic clinics university universities online
    community classroom classrooms ward wards home homes organization organizations organisation
    organisations company companies remote rural urban laboratory clinical outpatient inpatient virtual
    telehealth fieldwork""".split()
)


@dataclass(frozen=True)
class Context:
    kind: str  # "population" | "setting"
    term: str
    idea_ids: list[str]


def extract_contexts(ideas: list[Idea]) -> list[Context]:
    """Population/setting terms named in idea content (spec 5.1.1: "extract constructs & contexts").

    Deterministic and lexical, like `cluster_ideas`: a content word counts only when it matches
    `POPULATION_TERMS` or `SETTING_TERMS` exactly. A candidate list for the scholar to confirm, not a
    guarantee that every population or setting an idea names has been found.
    """
    usage: dict[tuple[str, str], list[str]] = {}
    for idea in ideas:
        terms = content_tokens(idea.text)
        for term in sorted(terms):
            if term in POPULATION_TERMS:
                kind = "population"
            elif term in SETTING_TERMS:
                kind = "setting"
            else:
                continue
            usage.setdefault((kind, term), []).append(idea.idea_id)
    contexts = [Context(kind=kind, term=term, idea_ids=ids) for (kind, term), ids in usage.items()]
    contexts.sort(key=lambda c: (c.kind, -len(c.idea_ids), c.term))
    return contexts
