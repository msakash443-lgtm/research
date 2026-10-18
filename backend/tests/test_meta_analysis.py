import math
import uuid

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.meta_analysis import MetaAnalysisError, pool
from app.models import Extraction, Gate, GateCode, GateStatus


def test_pool_calculates_fixed_random_heterogeneity_and_forest_from_code():
    studies = [{"source_id": "a", "extraction_id": "e1", "r": 0.2, "n": 103},
               {"source_id": "b", "extraction_id": "e2", "r": 0.4, "n": 103}]
    result = pool(studies)
    assert result["k"] == 2
    assert result["fixed"]["r"] == pytest.approx(math.tanh((math.atanh(.2) + math.atanh(.4)) / 2))
    assert result["heterogeneity"]["q"] > 0
    assert result["heterogeneity"]["tau_squared"] >= 0
    assert len(result["forest"]) == 2
    assert all(point["ci_low"] < point["r"] < point["ci_high"] for point in result["forest"])


def test_pool_refuses_duplicate_sources_and_invalid_input():
    with pytest.raises(MetaAnalysisError):
        pool([{"source_id": "a", "r": .2, "n": 10}, {"source_id": "a", "r": .3, "n": 10}])
    with pytest.raises(MetaAnalysisError):
        pool([{"source_id": "a", "r": 1, "n": 10}, {"source_id": "b", "r": .3, "n": 10}])


def test_plan_requires_verified_comparable_studies_and_g8_before_results():
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": f"meta-{uuid.uuid4().hex}@example.com", "display_name": "Meta"})
    pid = client.post("/api/projects", json={"title": "Pooling"}).json()["id"]
    source_ids = [client.post(f"/api/projects/{pid}/sources", json={"title": f"Study {i}"}).json()["id"] for i in range(2)]
    extraction_ids = []
    with SessionLocal() as db:
        for i, sid in enumerate(source_ids):
            row = Extraction(project_id=uuid.UUID(pid), source_id=uuid.UUID(sid), schema_version="default@1",
                             fields_json={"sample": {"n": 103}, "key_findings": [{"effect_size": f"r={.2 + .2*i}"}]},
                             evidence_spans={}, extracted_by="human", extractor="test",
                             verified_by_human=i == 0, verified_by="test" if i == 0 else None,
                             verified_at=None)
            if i == 0:
                from app.models import utcnow
                row.verified_at = utcnow()
            db.add(row)
            db.flush()
            extraction_ids.append(str(row.id))
        db.commit()
    base = f"/api/projects/{pid}/meta-analysis"
    body = {"outcome": "Same measured outcome", "primary_model": "random",
            "studies": [{"extraction_id": eid, "finding_index": 0} for eid in extraction_ids]}
    assert client.post(base, json=body).status_code == 409
    with SessionLocal() as db:
        row = db.get(Extraction, uuid.UUID(extraction_ids[1]))
        from app.models import utcnow
        row.verified_by_human, row.verified_by, row.verified_at = True, "test", utcnow()
        db.commit()
    created = client.post(base, json=body)
    assert created.status_code == 201, created.text
    plan_id = created.json()["id"]
    assert client.post(f"{base}/{plan_id}/run").status_code == 409
    with SessionLocal() as db:
        gate = db.query(Gate).filter_by(project_id=uuid.UUID(pid), code=GateCode.G8).one()
        gate.status = GateStatus.approved  # simulate the existing human gate endpoint; this feature never decides it
        db.commit()
    result = client.post(f"{base}/{plan_id}/run")
    assert result.status_code == 200, result.text
    assert result.json()["k"] == 2
    assert result.json()["primary_model"] == "random"
    assert client.post(base, json=body).status_code == 409
    with SessionLocal() as db:
        db.get(Extraction, uuid.UUID(extraction_ids[1])).fields_json = {
            "sample": {"n": 103}, "key_findings": [{"effect_size": "r=0.5"}]}
        db.commit()
    assert client.post(f"{base}/{plan_id}/run").status_code == 409


def test_g8_can_be_approved_once_a_meta_analysis_plan_exists():
    """M5.3.4 regression: meta-analysis freezes its plan by G8, so G8 must not need an AnalysisPlan lock."""
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": f"meta-g8-{uuid.uuid4().hex}@example.com", "display_name": "Meta"})
    pid = client.post("/api/projects", json={"title": "Pooling G8"}).json()["id"]
    source_ids = [client.post(f"/api/projects/{pid}/sources", json={"title": f"Study {i}"}).json()["id"] for i in range(2)]
    from app.models import utcnow
    with SessionLocal() as db:
        extraction_ids = []
        for i, sid in enumerate(source_ids):
            row = Extraction(project_id=uuid.UUID(pid), source_id=uuid.UUID(sid), schema_version="default@1",
                             fields_json={"sample": {"n": 103}, "key_findings": [{"effect_size": f"r={.2 + .2*i}"}]},
                             evidence_spans={}, extracted_by="human", extractor="test",
                             verified_by_human=True, verified_by="test", verified_at=utcnow())
            db.add(row)
            db.flush()
            extraction_ids.append(str(row.id))
        for gate in db.query(Gate).filter_by(project_id=uuid.UUID(pid)).all():
            if gate.code in (GateCode.G1, GateCode.G2, GateCode.G3, GateCode.G4, GateCode.G5, GateCode.G6, GateCode.G7):
                gate.status = GateStatus.approved
        db.commit()
    base = f"/api/projects/{pid}/meta-analysis"
    body = {"outcome": "Same measured outcome", "primary_model": "fixed",
            "studies": [{"extraction_id": eid, "finding_index": 0} for eid in extraction_ids]}
    assert client.post(base, json=body).status_code == 201
    approved = client.post(f"/api/projects/{pid}/gates/G8/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
