from __future__ import annotations

from collections.abc import Iterable

from app.models import ContextKind, ResearchContextItem

MAX_CONTEXT_ITEMS = 20
# Always sent first: the original question/objectives are often the oldest items, so a plain
# newest-N cap would drop exactly the items that define the project.
CORE_CONTEXT_KINDS = frozenset({ContextKind.question, ContextKind.objective})
# Saved for the researcher only; never part of a run's prompt or input_snapshot.
AGENT_EXCLUDED_KINDS = frozenset({ContextKind.idea})


def _created_order(item: ResearchContextItem):
    return (item.created_at, item.id)


def select_agent_context(items: Iterable[ResearchContextItem], limit: int = MAX_CONTEXT_ITEMS) -> list[ResearchContextItem]:
    """The context items a research run is given: research questions and objectives first, then the
    newest other items, up to `limit`. Returned oldest first. The project page uses the same rule to
    mark which items are left out, so the two can't disagree. Ideas are never included."""
    eligible = [item for item in items if item.kind not in AGENT_EXCLUDED_KINDS]
    newest_first = sorted(eligible, key=_created_order, reverse=True)
    core = [item for item in newest_first if item.kind in CORE_CONTEXT_KINDS]
    others = [item for item in newest_first if item.kind not in CORE_CONTEXT_KINDS]
    return sorted((core + others)[:limit], key=_created_order)
