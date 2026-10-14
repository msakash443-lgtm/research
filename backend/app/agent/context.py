from __future__ import annotations

from collections.abc import Iterable

from app.models import ContextKind, ResearchContextItem

MAX_CONTEXT_ITEMS = 20
# Always sent first: the original question/objectives are often the oldest items, so a plain
# newest-N cap would drop exactly the items that define the project.
CORE_CONTEXT_KINDS = frozenset({ContextKind.question, ContextKind.objective})
# Saved for the researcher only; never part of a run's prompt or input_snapshot.
# Inbox promotions (X.31.2) that are working notes rather than research statements stay out too.
AGENT_EXCLUDED_KINDS = frozenset({ContextKind.idea, ContextKind.gap, ContextKind.claim, ContextKind.memo})


def _created_order(item: ResearchContextItem):
    return (item.created_at, item.id)


def select_agent_context(
    items: Iterable[ResearchContextItem], limit: int = MAX_CONTEXT_ITEMS, *, locked_ids: frozenset[str] = frozenset()
) -> list[ResearchContextItem]:
    """The context items a research run is given: research questions and objectives first, then the
    newest other items, up to `limit`. Returned oldest first. The project page uses the same rule to
    mark which items are left out, so the two can't disagree. Ideas are never included, and neither is an
    item set to "never for this item" (`locked_ids`, from `ai_item_locks.locked_ids`); a locked item does not
    use up a place under `limit`."""
    eligible = [item for item in items if item.kind not in AGENT_EXCLUDED_KINDS and str(item.id) not in locked_ids]
    newest_first = sorted(eligible, key=_created_order, reverse=True)
    core = [item for item in newest_first if item.kind in CORE_CONTEXT_KINDS]
    others = [item for item in newest_first if item.kind not in CORE_CONTEXT_KINDS]
    return sorted((core + others)[:limit], key=_created_order)
