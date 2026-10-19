# X.19 — Performance benchmark against a real model (2026-10-19)

## Why this run

X.19 was reopened 2026-10-13 (`[~]`): the stubbed benchmark in
`backend/tests/test_performance_benchmark.py` proves the *application-side*
overhead (prompt building, decision/audit writes, per-source commit) is
18.4 ms/abstract — well inside the 3.6 s/abstract budget implied by the
spec's "≥ 1,000 abstracts/hour AI-assisted screening" (§10) — but does not
measure the model call itself, which is the dominant cost. Closing X.19
needs a run against a real model.

## What was available in this environment

This sandbox has no third-party `LLM_API_KEY`/`LLM_API_BASE_URL`/`LLM_MODEL`
configured (`.env` only sets `LOCAL_LLM_API_BASE_URL`/`LOCAL_LLM_MODEL`). The
only real, reachable model is a local Ollama server
(`http://localhost:11434/v1`, `qwen2.5:7b-instruct`, CPU inference). This run
therefore measures the **local/self-hosted model path**
(`OpenAICompatibleLLM.local`, the adapter `app/ai_controls.py::llm_for_project`
uses for participant-data projects and for any project routed to
`ai_model_route="local"`). No cloud/third-party model was reachable here, so
that path remains unmeasured; whether a project on the default third-party
route meets the budget depends on that provider's latency, not on anything
tested in this run.

## Method

A throwaway script (reproduced below; not added to the repo — it talks to a
live external service, which the test suite deliberately isolates from,
`backend/tests/conftest.py` pops `LOCAL_LLM_*` for every test) built an
in-memory SQLite project through the real ORM models (`Project`,
`ProjectMember`, `ScreeningCriterion`, `Source`), then called the real
`app/prescreen.py::run_prescreen` — the same function the prescreen task
handler calls — with a real `OpenAICompatibleLLM.local(settings)` pointed at
the reachable Ollama server, against 12 synthetic but realistic
title/abstract pairs (6 relevant, 6 not, matching the kind of record a
screener sees).

```python
import os, sys, time

os.environ["DATABASE_URL"] = "sqlite+pysqlite://"
os.environ["ENVIRONMENT"] = "development"
os.environ["AUTO_CREATE_SCHEMA"] = "true"
os.environ["RUN_RESEARCH_INLINE"] = "true"
os.environ["SESSION_SECRET"] = "bench-session-secret-that-is-long-enough"
sys.path.insert(0, os.path.join(os.getcwd(), "backend"))

from app.config import Settings
Settings.model_config["env_file"] = None
from app.database import Base, engine, SessionLocal
from app.models import Project, ProjectMember, ScreeningCriterion, Source, User
from app.agent.llm import OpenAICompatibleLLM
from app.prescreen import run_prescreen

Base.metadata.create_all(bind=engine)
settings = Settings(
    _env_file=None, database_url="sqlite+pysqlite://",
    session_secret="bench-session-secret-that-is-long-enough",
    local_llm_api_base_url="http://localhost:11434/v1",
    local_llm_model="qwen2.5:7b-instruct", local_llm_timeout_seconds=120,
)

db = SessionLocal()
user = User(email="bench@example.com", display_name="Bench")
db.add(user); db.flush()
project = Project(title="Real-model benchmark", owner_id=user.id)
db.add(project); db.flush()
db.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
db.add(ScreeningCriterion(project_id=project.id, kind="include", code="I1",
       text="Empirical studies on agricultural climate adaptation", position=0))
db.add(ScreeningCriterion(project_id=project.id, kind="exclude", code="E1",
       text="Opinion pieces / editorials", position=1))

ABSTRACTS = [  # 12 synthetic title/abstract pairs, 6 relevant + 6 not
    ("Soil carbon sequestration in tropical farms", "..."),
    # ... see full list in the session that produced this report
]
for title, abstract in ABSTRACTS:
    db.add(Source(project_id=project.id, title=title, abstract=abstract, created_by=str(user.id)))
db.commit()

llm = OpenAICompatibleLLM.local(settings)
t0 = time.time()
result = run_prescreen(db, project, llm=llm,
                        min_confidence=settings.prescreen_min_confidence, actor="bench-script")
elapsed = time.time() - t0
print(result, elapsed, 3600 / (elapsed / len(ABSTRACTS)))
```

## Result (raw)

```
result: PrescreenResult(suggested=12, failed=0, skipped=0, locked=0)
elapsed=379.6s for 12 sources, suggested=12
per-item=31.63s -> 113.8 abstracts/hour sequential (budget: 1000/hour)
```

12/12 abstracts got a usable suggestion (no `LLMResponseError`/schema
failures). Two isolated single-call checks against the same model/endpoint
immediately before this run measured 38.1 s, 31.6 s, 28.0 s per call — in the
same range as the 31.6 s/abstract average inside `run_prescreen`, so the
per-item figure is not an artefact of app overhead (which the stubbed
benchmark already showed is ~18 ms).

## Interpretation

Real, measured sequential throughput for this model/endpoint:
**113.8 abstracts/hour**, ~11% of the spec's 1,000/hour budget. This is not
an app-side bottleneck (X.19's earlier stubbed run already ruled that out);
it is the local CPU model's own latency (~30 s/call for a short JSON
decision). `run_prescreen` calls the model once per source, sequentially, with
no batching or concurrency — that is the current, correct behaviour for
honesty/audit ordering (each decision is committed as it goes, rule: "One
source at a time, committed as it goes, so a stop part-way keeps what was
done"), not a bug this task should change.

**This real-model run does not close X.19** against the spec's literal 1,000/hour
number for the local-model path: the only model reachable in this
environment misses the budget by roughly 9×. Two things remain open, and
both are product/infra decisions, not something this benchmark task should
decide unilaterally:

1. **Which model throughput actually matters for the budget.** The spec
   budget most plausibly describes the default (non-participant-data) path,
   which in production would point at a faster third-party API, not the
   local CPU model this sandbox has. That path has no key configured here
   and remains unmeasured.
2. **Whether the local/participant-data path is expected to meet 1,000/hour
   at all.** M0.8.4's local-model requirement is a privacy control, not a
   throughput promise; if a scholar's participant-data project is capped by
   local hardware, that may be an accepted tradeoff rather than a defect —
   but that is the product owner's call, not this task's.

## Status

X.19 stays `[~]`/open, not `[x]`: the stub-vs-budget regression tests remain
green (`test_performance_benchmark.py`, unchanged), and this report adds the
real-model evidence the 2026-10-13 reopen asked for, but that evidence shows
the budget is **not met** on the only model available here. Recorded on the
X.19 plan.md line; needs the product owner to say which model path the
1,000/hour figure is meant to bind, and whether 113.8/hour local-CPU
throughput is an accepted cost of M0.8.4's privacy requirement.
