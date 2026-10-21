"""Performance budgets from spec §10 (plan X.19), checked as regression tests.

* Screening: AI-assisted throughput. Owner decision 2026-10-21: the 1,000/hour third-party-API
  target is dropped; the reference is the measured local Ollama rate of 113.8 abstracts/hour
  (`milestone-reports/X.19-real-model-benchmark-2026-10-19.md`), i.e. 31.6 s per abstract. The model
  call is the slow part and it is not measured here (no real model in tests). What this proves is the
  budget the application code leaves for it: prompt building, the decision and audit writes and the
  per-source commit must take under 1% of the 31.6 s each abstract gets at the reference rate.
* Full-text indexing: 500 papers in under one hour. "Indexing" here is what M2.7.3 does for a stored
  PDF: `extract_pdf_text` (page and section anchors) plus a quote lookup, which M3.4 relies on.

The PDFs are built in-test (no files, no network). Thresholds are the spec budgets, not tuned
numbers, so a failure means the budget is gone, not that the machine was busy for a moment.
"""

import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent.llm import OpenAICompatibleLLM
from app.config import get_settings
from app.database import SessionLocal
from app.fulltext import extract_pdf_text
from app.main import app
from app.models import Project, ScreeningDecision, Source
from app.prescreen import run_prescreen
from test_fulltext import make_pdf

SCREEN_COUNT = 1000
SCREEN_BUDGET_PER_HOUR = 113.8
MAX_APP_OVERHEAD_SHARE = 0.01
FULLTEXT_COUNT = 500
FULLTEXT_BUDGET_SECONDS = 3600


class CyclingLLM(OpenAICompatibleLLM):
    """The real adapter (schema validation included) with the HTTP call replaced by a fixed rotation of answers."""

    ANSWERS = [
        {"decision": "include", "reason_code": "I1", "confidence": 0.9, "rationale": "Empirical study.", "evidence_quote": "Benchmark paper"},
        {"decision": "exclude", "reason_code": "E1", "confidence": 0.95, "rationale": "Opinion piece.", "evidence_quote": "Benchmark paper"},
        {"decision": "maybe", "reason_code": None, "confidence": 0.5, "rationale": "Unclear from the abstract.", "evidence_quote": None},
    ]

    def __init__(self):
        super().__init__(get_settings(), api_key=None, api_base_url="http://llm.invalid", model="bench-model", require_api_key=False)
        self.calls = 0

    def complete(self, system_prompt, user_prompt):
        answer = self.ANSWERS[self.calls % len(self.ANSWERS)]
        self.calls += 1
        return json.dumps(answer)


def _screening_project() -> uuid.UUID:
    tag = uuid.uuid4().hex[:8]
    owner = TestClient(app)
    owner.post("/api/auth/development/login", json={"email": f"bench-{tag}@example.com", "display_name": "B"})
    pid = owner.post("/api/projects", json={"title": "Benchmark"}).json()["id"]
    owner.put(
        f"/api/projects/{pid}/criteria",
        json={"framework": "custom", "criteria": [
            {"kind": "include", "text": "Empirical studies"},
            {"kind": "exclude", "text": "Opinion pieces"},
        ]},
    )
    return uuid.UUID(pid)


@pytest.mark.slow
def test_screening_leaves_the_model_nearly_all_of_the_local_rate_budget():
    pid = _screening_project()
    abstract = "We study how teams share knowledge across projects using a survey of 412 engineers. " * 8
    with SessionLocal() as db:
        db.add_all(
            Source(project_id=pid, title=f"Benchmark paper {n} on team knowledge sharing", abstract=abstract, year=2020)
            for n in range(SCREEN_COUNT)
        )
        db.commit()

    llm = CyclingLLM()
    with SessionLocal() as db:
        project = db.get(Project, pid)
        started = time.perf_counter()
        result = run_prescreen(db, project, llm=llm, min_confidence=0.6, actor="benchmark", limit=SCREEN_COUNT)
        elapsed = time.perf_counter() - started

    assert (result.suggested, result.failed, result.skipped) == (SCREEN_COUNT, 0, 0)
    assert llm.calls == SCREEN_COUNT
    with SessionLocal() as db:
        stored = db.scalar(select(ScreeningDecision.id).where(ScreeningDecision.project_id == pid).limit(1))
        assert stored is not None

    per_hour = SCREEN_COUNT / elapsed * 3600
    per_abstract_budget = 3600 / SCREEN_BUDGET_PER_HOUR * MAX_APP_OVERHEAD_SHARE
    assert elapsed / SCREEN_COUNT < per_abstract_budget, (
        f"app-side overhead {elapsed / SCREEN_COUNT * 1000:.1f} ms per abstract; the budget allows "
        f"{per_abstract_budget * 1000:.0f} ms ({MAX_APP_OVERHEAD_SHARE:.0%} of the time each abstract gets at {SCREEN_BUDGET_PER_HOUR}/hour) "
        f"(measured {per_hour:.0f}/hour without model latency)"
    )


@pytest.mark.slow
def test_500_full_texts_are_indexed_within_an_hour():
    pdfs = []
    for n in range(FULLTEXT_COUNT):
        pages = [
            [f"Paper {n}: Knowledge sharing in distributed teams", "Abstract", "We surveyed 412 engineers about shared code."],
            ["1 Introduction", "Teams that share knowledge deliver sooner. " * 6],
            ["3 Methods", "A cross-sectional survey was distributed to four firms. " * 6],
            ["4 Results", "Sharing predicts delivery time (p < .01). " * 6],
            ["5 Discussion", "These findings extend prior work on teams. " * 6],
        ]
        pdfs.append(make_pdf(pages))

    started = time.perf_counter()
    found = 0
    for data in pdfs:
        text = extract_pdf_text(data, max_pages=2000)
        if text.find_quote("Sharing predicts delivery time", section="results"):
            found += 1
    elapsed = time.perf_counter() - started

    assert found == FULLTEXT_COUNT, "every paper's quote should be found in its results section"
    assert elapsed < FULLTEXT_BUDGET_SECONDS, f"indexing {FULLTEXT_COUNT} papers took {elapsed:.1f} s; budget is {FULLTEXT_BUDGET_SECONDS} s"
