import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import AutoExcludeAudit, Project, ProjectMember, ProjectRole, ScreeningDecision, SearchQuery


def _login(email):
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": email, "display_name": "P"})
    return client


@pytest.fixture
def project():
    tag = uuid.uuid4().hex[:8]
    owner = _login(f"prisma-{tag}@example.com")
    pid = owner.post("/api/projects", json={"title": "Prisma"}).json()["id"]
    return owner, uuid.UUID(pid), _login(f"prisma-out-{tag}@example.com")


def entry(i, doi=None, key=None):
    return {"id": f"x:{i}", "doi": doi, "work_key": key}


_clock = [datetime(2026, 1, 1, tzinfo=timezone.utc)]


def add_run(pid, database, results, retrieved=None, search_id=None, version=1, truncated=False):
    _clock[0] += timedelta(minutes=1)
    with SessionLocal() as db:
        row = SearchQuery(
            project_id=pid, search_id=search_id or uuid.uuid4(), database=database, query_string="q", version=version,
            run_at=_clock[0], n_results=len(results), exact=True, caveats=[], results=results,
            counts={"retrieved": retrieved if retrieved is not None else len(results), "truncated": truncated},
        )
        db.add(row)
        db.commit()
        return row.search_id


def flow(client, pid):
    r = client.get(f"/api/projects/{pid}/prisma")
    assert r.status_code == 200, r.text
    return r.json()


def test_empty_project_reports_zeros_for_screening(project):
    owner, pid, _ = project
    data = flow(owner, pid)
    assert data["identification"]["identified"] == 0 and data["identification"]["duplicates_removed"] == 0
    assert data["identification"]["searches"] == []
    screening = data["screening"]
    assert screening["unscreened"] == 0
    assert screening["title_abstract"] == {"decided": 0, "included": 0, "excluded": 0, "conflicts": 0, "excluded_by_reason": {}}
    assert screening["full_text"] == {"decided": 0, "included": 0, "excluded": 0, "conflicts": 0, "excluded_by_reason": {}}
    assert screening["exclusion_reasons_missing"] == 0 and screening["exclusion_reasons_complete"] is True


def _criteria_and_sources(owner, pid, n=4):
    owner.put(f"/api/projects/{pid}/criteria", json={"framework": "custom", "criteria": [
        {"kind": "include", "text": "Empirical studies"},
        {"kind": "exclude", "text": "Opinion pieces"},
        {"kind": "exclude", "text": "Not in English"},
    ]})
    return [owner.post(f"/api/projects/{pid}/sources", json={"title": f"Paper {k}"}).json()["id"] for k in range(n)]


def _decide(client, pid, source_id, decision, **extra):
    return client.post(f"/api/projects/{pid}/screening/decisions", json={"source_id": source_id, "decision": decision, **extra})


def _add_member(pid, role):
    client = TestClient(app)
    uid = client.post("/api/auth/development/login", json={"email": f"prisma-{role}-{uuid.uuid4().hex[:8]}@example.com", "display_name": "M"}).json()["id"]
    with SessionLocal() as db:
        db.add(ProjectMember(project_id=uuid.UUID(str(pid)), user_id=uuid.UUID(uid), role=ProjectRole(role)))
        db.commit()
    return client


def test_screening_counts_come_from_settled_decisions_by_stage(project):
    owner, pid, _ = project
    ids = _criteria_and_sources(owner, pid)
    _decide(owner, pid, ids[0], "include")
    _decide(owner, pid, ids[1], "exclude", reason_code="E1")
    _decide(owner, pid, ids[2], "exclude", reason_code="E2")
    _decide(owner, pid, ids[0], "include", stage="full_text")
    screening = flow(owner, pid)["screening"]
    assert screening["unscreened"] == 1  # ids[3]
    assert screening["title_abstract"] == {"decided": 3, "included": 1, "excluded": 2, "conflicts": 0, "excluded_by_reason": {"E1": 1, "E2": 1}}
    assert screening["full_text"] == {"decided": 1, "included": 1, "excluded": 0, "conflicts": 0, "excluded_by_reason": {}}
    assert screening["exclusion_reasons_missing"] == 0 and screening["exclusion_reasons_complete"] is True


def test_an_undone_decision_returns_the_source_to_unscreened(project):
    owner, pid, _ = project
    ids = _criteria_and_sources(owner, pid, n=2)
    _decide(owner, pid, ids[0], "exclude", reason_code="E1")
    assert owner.post(f"/api/projects/{pid}/screening/decisions/undo", json={"source_id": ids[0], "stage": "title_abstract"}).status_code == 201
    screening = flow(owner, pid)["screening"]
    assert screening["unscreened"] == 2 and screening["title_abstract"]["decided"] == 0


def test_under_dual_screen_only_agreed_decisions_count(project):
    owner, pid, _ = project
    ids = _criteria_and_sources(owner, pid, n=3)
    co_author = _add_member(pid, "co_author")
    assert owner.put(f"/api/projects/{pid}/screening/dual-screen", json={"enabled": True}).status_code == 200
    _decide(owner, pid, ids[0], "include")
    _decide(co_author, pid, ids[0], "include")  # agreed
    _decide(owner, pid, ids[1], "include")
    _decide(co_author, pid, ids[1], "exclude", reason_code="E1")  # conflict
    _decide(owner, pid, ids[2], "include")  # awaiting second reviewer
    screening = flow(owner, pid)["screening"]
    assert screening["title_abstract"] == {"decided": 1, "included": 1, "excluded": 0, "conflicts": 1, "excluded_by_reason": {}}
    assert screening["unscreened"] == 2  # the conflict and the awaiting source


def test_an_exclusion_without_a_valid_reason_is_flagged_and_not_counted_by_reason(project):
    owner, pid, _ = project
    ids = _criteria_and_sources(owner, pid, n=2)
    _decide(owner, pid, ids[0], "exclude", reason_code="E1")
    with SessionLocal() as db:
        db.add(ScreeningDecision(
            project_id=uuid.UUID(str(pid)), source_id=uuid.UUID(ids[1]), stage="title_abstract", seq=1, decision="exclude",
            reason_code=None, decided_by="human", decider=uuid.uuid4().hex,
        ))
        db.commit()
    screening = flow(owner, pid)["screening"]
    assert screening["title_abstract"]["excluded"] == 2 and screening["title_abstract"]["excluded_by_reason"] == {"E1": 1}
    assert screening["exclusion_reasons_missing"] == 1 and screening["exclusion_reasons_complete"] is False


def test_counts_sum_databases_and_remove_cross_database_duplicates(project):
    owner, pid, _ = project
    add_run(pid, "openalex", [entry(1, "10.1/a"), entry(2, None, "k2"), entry(3, "10.1/c")], retrieved=4)  # 1 repeat within run
    add_run(pid, "crossref", [entry(4, "10.1/a"), entry(5, None, "k2"), entry(6, None, "k6")])
    i = flow(owner, pid)["identification"]
    assert i["identified"] == 7
    assert i["after_duplicates_removed"] == 4  # a, k2, c, k6
    assert i["duplicates_removed"] == 3
    assert i["identified"] - i["duplicates_removed"] == i["after_duplicates_removed"]
    assert [s["database"] for s in i["searches"]] == ["openalex", "crossref"]


def test_different_dois_are_never_merged_even_with_same_title_key(project):
    owner, pid, _ = project
    add_run(pid, "openalex", [entry(1, "10.1/pre", "same")])
    add_run(pid, "crossref", [entry(2, "10.1/pub", "same")])
    assert flow(owner, pid)["identification"]["after_duplicates_removed"] == 2


def test_only_the_latest_version_of_a_search_counts(project):
    owner, pid, _ = project
    sid = add_run(pid, "openalex", [entry(1, "10.1/a"), entry(2, "10.1/b")])
    add_run(pid, "openalex", [entry(3, "10.1/c")], search_id=sid, version=2)
    i = flow(owner, pid)["identification"]
    assert i["identified"] == 1 and len(i["searches"]) == 1 and i["searches"][0]["version"] == 2


def test_truncated_run_is_flagged_as_lower_bound(project):
    owner, pid, _ = project
    add_run(pid, "openalex", [entry(1, "10.1/a")], truncated=True)
    data = flow(owner, pid)
    assert data["identification"]["searches"][0]["truncated"] is True
    assert len(data["warnings"]) == 1 and "lower bound" in data["warnings"][0]


def test_other_projects_runs_are_not_counted_and_outsiders_are_refused(project):
    owner, pid, outsider = project
    other = owner.post("/api/projects", json={"title": "Other"}).json()["id"]
    add_run(uuid.UUID(other), "openalex", [entry(1, "10.1/a")])
    assert flow(owner, pid)["identification"]["identified"] == 0
    assert outsider.get(f"/api/projects/{pid}/prisma").status_code == 404
    assert TestClient(app).get(f"/api/projects/{pid}/prisma").status_code == 401


def test_g3_approval_is_refused_while_an_excluded_record_has_no_valid_reason(project):
    owner, pid, _ = project
    ids = _criteria_and_sources(owner, pid, n=1)
    _decide(owner, pid, ids[0], "exclude", reason_code="E1")
    with SessionLocal() as db:
        db.add(ScreeningDecision(
            project_id=uuid.UUID(str(pid)), source_id=uuid.UUID(ids[0]), stage="title_abstract", seq=2, decision="exclude",
            reason_code=None, decided_by="human", decider=uuid.uuid4().hex,
        ))
        db.commit()
    owner.post(f"/api/projects/{pid}/gates/G1/approve")
    owner.post(f"/api/projects/{pid}/gates/G2/approve")
    response = owner.post(f"/api/projects/{pid}/gates/G3/approve")
    assert response.status_code == 409
    assert response.json()["detail"]["exclusion_reasons_missing"] == 1


def _ai_exclude(pid, source_id, confidence):
    with SessionLocal() as db:
        db.add(ScreeningDecision(
            project_id=uuid.UUID(str(pid)), source_id=uuid.UUID(source_id), stage="title_abstract", seq=1, decision="exclude",
            reason_code="E1", decided_by="ai", decider="agent:prescreen", confidence=confidence,
        ))
        db.commit()


def _set_autoexclude(pid, **fields):
    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(str(pid)))
        project.ai_autoexclude_enabled = True
        project.ai_autoexclude_confidence_threshold = 0.9
        for name, value in fields.items():
            setattr(project, name, value)
        db.commit()


def test_a_policy_honoured_ai_auto_exclude_is_counted_as_excluded_with_its_reason(project):
    owner, pid, _ = project
    ids = _criteria_and_sources(owner, pid, n=2)
    _set_autoexclude(pid)
    _ai_exclude(pid, ids[0], 0.95)
    screening = owner.get(f"/api/projects/{pid}/prisma").json()["screening"]
    assert screening["title_abstract"]["excluded"] == 1
    assert screening["title_abstract"]["excluded_by_reason"] == {"E1": 1}
    assert screening["unscreened"] == 1
    assert screening["exclusion_reasons_missing"] == 0


def test_an_ai_exclude_below_the_threshold_is_not_counted_as_excluded(project):
    owner, pid, _ = project
    ids = _criteria_and_sources(owner, pid, n=2)
    _set_autoexclude(pid)
    _ai_exclude(pid, ids[0], 0.5)
    screening = owner.get(f"/api/projects/{pid}/prisma").json()["screening"]
    assert screening["title_abstract"]["excluded"] == 0
    assert screening["unscreened"] == 2


def test_once_the_agreement_guard_trips_an_unsampled_ai_exclude_is_not_counted(project):
    owner, pid, _ = project
    ids = _criteria_and_sources(owner, pid, n=2)
    _set_autoexclude(pid, ai_prescreen_disabled=True)
    _ai_exclude(pid, ids[0], 0.95)
    screening = owner.get(f"/api/projects/{pid}/prisma").json()["screening"]
    assert screening["title_abstract"]["excluded"] == 0
    assert screening["unscreened"] == 2


def _ai_exclude_audited(pid, source_id, status):
    """A policy-eligible AI exclude (confidence 0.95) whose sampling audit row has `status`."""
    with SessionLocal() as db:
        decision = ScreeningDecision(
            project_id=uuid.UUID(str(pid)), source_id=uuid.UUID(source_id), stage="title_abstract", seq=1, decision="exclude",
            reason_code="E1", decided_by="ai", decider="agent:prescreen", confidence=0.95,
        )
        db.add(decision)
        db.flush()
        reviewed = {} if status == "pending" else {"reviewed_by": "reviewer", "reviewed_at": datetime.now(timezone.utc)}
        db.add(AutoExcludeAudit(
            project_id=uuid.UUID(str(pid)), source_id=uuid.UUID(source_id), stage="title_abstract",
            decision_id=decision.id, confidence=0.95, status=status, **reviewed,
        ))
        db.commit()


@pytest.mark.parametrize("status,counted", [("confirmed", 1), ("pending", 0), ("overturned", 0)])
def test_a_sampled_ai_exclude_counts_in_prisma_only_once_a_person_confirmed_it(project, status, counted):
    owner, pid, _ = project
    ids = _criteria_and_sources(owner, pid, n=2)
    _set_autoexclude(pid)
    _ai_exclude_audited(pid, ids[0], status)
    screening = owner.get(f"/api/projects/{pid}/prisma").json()["screening"]
    assert screening["title_abstract"]["excluded"] == counted
    assert screening["unscreened"] == 2 - counted


def test_a_persons_decision_replaces_a_counted_ai_exclude_in_prisma(project):
    owner, pid, _ = project
    ids = _criteria_and_sources(owner, pid, n=2)
    _set_autoexclude(pid)
    _ai_exclude(pid, ids[0], 0.95)
    assert _decide(owner, pid, ids[0], "include").status_code in (200, 201)
    screening = owner.get(f"/api/projects/{pid}/prisma").json()["screening"]
    assert screening["title_abstract"]["excluded"] == 0
    assert screening["title_abstract"]["included"] == 1
    assert screening["unscreened"] == 1


def test_under_dual_screen_an_ai_exclude_is_never_counted_in_prisma(project):
    owner, pid, _ = project
    ids = _criteria_and_sources(owner, pid, n=2)
    _set_autoexclude(pid, dual_screen_enabled=True)
    _ai_exclude(pid, ids[0], 0.95)
    screening = owner.get(f"/api/projects/{pid}/prisma").json()["screening"]
    assert screening["title_abstract"]["excluded"] == 0
    assert screening["unscreened"] == 2
