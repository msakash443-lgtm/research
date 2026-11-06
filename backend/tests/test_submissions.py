import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.submissions import integration_enabled, transition
from gate_helpers import approve_earlier_gates


def test_submission_lifecycle_requires_explicit_valid_transitions():
    assert transition("draft", "submitted") == "submitted"
    assert transition("under_review", "accepted") == "accepted"
    with pytest.raises(ValueError):
        transition("draft", "accepted")


def test_external_integrations_are_off_by_default():
    assert integration_enabled({}, "orcid") is False
    assert integration_enabled({"zenodo_enabled": True}, "zenodo") is True


def test_a_submission_cannot_be_submitted_until_the_owner_approves_g11():
    tag = uuid.uuid4().hex[:8]
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": f"sub-{tag}@example.com", "display_name": "S"})
    project_id = client.post("/api/projects", json={"title": "Submit"}).json()["id"]
    submission_id = client.post(f"/api/projects/{project_id}/submissions", json={"journal_name": "Example"}).json()["id"]
    url = f"/api/projects/{project_id}/submissions/{submission_id}/transition"
    refused = client.post(url, json={"status": "submitted"})
    assert refused.status_code == 409
    assert "G11" in refused.json()["detail"]
    approve_earlier_gates(project_id, "G11")
    assert client.post(f"/api/projects/{project_id}/gates/G11/approve").status_code == 200
    accepted = client.post(url, json={"status": "submitted"})
    assert accepted.status_code == 200 and accepted.json()["status"] == "submitted"
