"""Responsibility matrix (X.226): who leads or supports an area, kept apart from roles and CRediT."""
import uuid

import pytest

from app.responsibilities import AREAS, InvalidResponsibility, validate_assignments
from tests.api_helpers import create_project, sign_in_user


def team():
    tag = uuid.uuid4().hex[:8]
    owner, owner_id = sign_in_user(f"resp-owner-{tag}@example.com", "Priya")
    reviewer, reviewer_id = sign_in_user(f"resp-rev-{tag}@example.com", "Arun")
    project = create_project(owner, "Matrix")
    assert owner.post(f"/api/projects/{project}/members", json={"email": f"resp-rev-{tag}@example.com", "role": "reviewer"}).status_code == 201
    return owner, owner_id, reviewer, reviewer_id, project


def put(client, project, area, assignments):
    return client.put(f"/api/projects/{project}/responsibilities/{area}", json={"assignments": assignments})


def test_an_area_has_at_most_one_lead_and_each_person_once():
    assert validate_assignments("screening", [("a", "support"), ("b", "lead")]) == [("b", "lead"), ("a", "support")]
    with pytest.raises(InvalidResponsibility, match="at most one lead"):
        validate_assignments("screening", [("a", "lead"), ("b", "lead")])
    with pytest.raises(InvalidResponsibility, match="once"):
        validate_assignments("screening", [("a", "lead"), ("a", "support")])
    with pytest.raises(InvalidResponsibility, match="Unknown area"):
        validate_assignments("catering", [])
    assert [code for code, _ in AREAS][:3] == ["scope", "search", "screening"]


def test_the_owner_sets_an_area_and_everyone_on_the_project_can_read_it():
    owner, owner_id, reviewer, reviewer_id, project = team()
    done = put(owner, project, "screening", [{"user_id": reviewer_id, "level": "lead"}, {"user_id": owner_id, "level": "support"}])
    assert done.status_code == 200, done.text
    seen = reviewer.get(f"/api/projects/{project}/responsibilities").json()
    assert [(a["area"], a["name"], a["level"]) for a in seen["assignments"]] == [("screening", "Arun", "lead"), ("screening", "Priya", "support")]
    assert [a["code"] for a in seen["areas"]][2] == "screening"
    assert put(owner, project, "screening", []).json()["assignments"] == []  # an empty list clears the area


def test_a_lead_on_an_area_gains_no_permission_and_no_credit_role():
    owner, _, reviewer, reviewer_id, project = team()
    assert put(owner, project, "screening", [{"user_id": reviewer_id, "level": "lead"}]).status_code == 200
    assert reviewer.post(f"/api/projects/{project}/sources", json={"title": "Not allowed"}).status_code == 403
    assert put(reviewer, project, "search", []).status_code == 403
    credit = owner.get(f"/api/projects/{project}/manuscript/credit").json()
    assert all(c["roles"] == [] for c in credit)


def test_only_project_members_can_hold_a_responsibility_and_the_change_is_audited():
    owner, owner_id, _, _, project = team()
    _, stranger_id = sign_in_user(f"resp-out-{uuid.uuid4().hex[:8]}@example.com")
    refused = put(owner, project, "ethics", [{"user_id": stranger_id, "level": "lead"}])
    assert refused.status_code == 422 and "members" in refused.json()["detail"]
    assert put(owner, project, "ethics", [{"user_id": owner_id, "level": "lead"}]).status_code == 200
    events = [e for e in owner.get(f"/api/projects/{project}/audit").json() if e["action"] == "responsibility.set"]
    assert events[-1]["payload_json"]["area"] == "ethics" and events[-1]["payload_json"]["to"] == [[owner_id, "lead"]]
    assert put(owner, project, "catering", []).status_code == 422
