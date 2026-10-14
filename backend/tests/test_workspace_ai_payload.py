"""X.31.11.3: the researcher's selected text leaves the `workspace_ai_action` task row once the task ends.

Retries still need the text, so a task that will run again keeps it. Completed and permanently failed
tasks lose it.
"""

import json
import uuid
from datetime import datetime, timezone

from app import task_handlers  # noqa: F401  registers the handler and its payload scrub
from app import task_queue as q
from app.database import SessionLocal
from app.models import Project, Task, TaskStatus, User

NOW = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)
SELECTED = "Our unpublished trial result is 37 percent."


def _queued_action(max_attempts=3):
    with SessionLocal() as db:
        user = User(email=f"ai-{uuid.uuid4().hex}@example.test")
        db.add(user)
        db.flush()
        project = Project(owner_id=user.id, title="AI payload")
        db.add(project)
        db.flush()
        task = Task(
            project_id=project.id,
            type="workspace_ai_action",
            max_attempts=max_attempts,
            payload={
                "project_id": str(project.id),
                "action": "challenge",
                "text": SELECTED,
                "document_id": str(uuid.uuid4()),
                "actor": "someone@example.test",
            },
        )
        db.add(task)
        db.commit()
        return task.id


def _stored(task_id):
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        return task.status, task.payload


def test_a_completed_action_task_keeps_no_selected_text():
    task_id = _queued_action()
    claimed = q.claim_next_task(NOW)

    assert q.complete_task(task_id, claimed.attempt) is True

    status, payload = _stored(task_id)
    assert status == TaskStatus.completed
    assert "text" not in payload
    assert SELECTED not in json.dumps(payload)
    assert payload["action"] == "challenge"  # the rest of the payload is kept


def test_a_permanently_failed_action_task_keeps_no_selected_text():
    task_id = _queued_action()
    claimed = q.claim_next_task(NOW)

    assert q.fail_task(task_id, claimed.attempt, "refused", NOW, permanent=True) == TaskStatus.failed

    status, payload = _stored(task_id)
    assert status == TaskStatus.failed
    assert SELECTED not in json.dumps(payload)


def test_a_task_that_will_be_retried_keeps_its_text_for_the_next_attempt():
    task_id = _queued_action(max_attempts=3)
    claimed = q.claim_next_task(NOW)

    assert q.fail_task(task_id, claimed.attempt, "model timed out", NOW) == TaskStatus.queued

    status, payload = _stored(task_id)
    assert status == TaskStatus.queued
    assert payload["text"] == SELECTED
