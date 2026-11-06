"""AI-use disclosure text, generated from the audit log (M6.6, spec Sec5.11.6; Q6: no institutional policy, use
the generic template).

Pure: takes a plain summary of the project's `AuditEvent` rows (nothing is read here, the caller queries the
DB) and renders disclosure text. An AI-assisted action is any audit row carrying a `model_id` (the same
convention `app.audit.record` already uses for "a model was involved") - manual/human-only actions carry no
`model_id` and are not counted. The text always says AI is not an author, whether or not any AI action is
found, so the statement is never silently missing from an export.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

NOT_AN_AUTHOR = "AI is not listed as an author or contributor; all AI-assisted output was reviewed and accepted by the named human authors before use."


@dataclass(frozen=True)
class AiAuditAction:
    """One audit row, as far as disclosure generation needs it."""

    action: str
    model_id: str | None
    prompt_version: str | None


def generate(events: list[AiAuditAction]) -> str:
    """Generic-template disclosure: which actions AI assisted with, which model(s) and prompt version(s), in
    deterministic (sorted) order so the same audit log always renders the same text."""
    ai_events = [e for e in events if e.model_id]
    if not ai_events:
        return "No AI-assisted actions are recorded in this project's audit log. " + NOT_AN_AUTHOR

    actions = sorted({e.action for e in ai_events})
    models_to_versions: dict[str, set[str]] = defaultdict(set)
    for e in ai_events:
        if e.prompt_version:
            models_to_versions[e.model_id].add(e.prompt_version)
        else:
            models_to_versions.setdefault(e.model_id, set())
    model_lines = []
    for model in sorted(models_to_versions):
        versions = sorted(models_to_versions[model])
        model_lines.append(f"{model} (prompt version(s): {', '.join(versions)})" if versions else model)

    return (
        f"AI assistance was used in this project, recorded in the audit log as {len(ai_events)} action(s) "
        f"across the following recorded action types: {', '.join(actions)}. "
        f"Model(s) used: {'; '.join(model_lines)}. " + NOT_AN_AUTHOR
    )
