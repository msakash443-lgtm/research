"""The per-endpoint role matrix (plan M0.3.4).

Every project-scoped route is listed below with the roles allowed to use it and the
status a permitted call returns. Roles are written out literally on purpose: the matrix
pins the agreed policy, so changing a role set in `dependencies.py` fails here.

Everyone outside the allowed set must be refused: a member with the wrong role gets 403,
a non-member 404 (existence is not revealed), and an anonymous caller 401.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.object_storage import fulltext_key
from app.models import (
    utcnow,
    SOURCE_ORIGIN_RETRIEVED,
    AutoExcludeAudit,
    Cluster,
    ClusterRun,
    Claim,
    Extraction,
    OwnPaperLookup,
    Project,
    ProjectMember,
    ProjectExtractionSchema,
    ProjectRole,
    ProjectStage,
    ScreeningDecision,
    SearchQuery,
    SeedPaper,
    Source,
)
from tests.test_fulltext import make_pdf

UPLOAD_PDF = make_pdf([["Role matrix upload body text."]])

ALL = {"owner", "co_author", "supervisor", "reviewer"}
WRITE = {"owner", "co_author"}
VERIFY = {"owner", "co_author", "supervisor"}
MANAGE = {"owner"}
APPROVE_G1 = {"owner", "supervisor"}  # gate G1; owner-only gates are in test_gate_decisions.py
APPROVE_SECTION = {"owner", "supervisor"}  # G10's required role (M6.11)

# (method, path template, roles allowed, status for an allowed call)
MATRIX = [
    ("GET", "/api/projects/{p}", ALL, 200),
    ("POST", "/api/projects/{p}/archive", MANAGE, 200),  # disposable per-call project; never the shared one
    ("POST", "/api/projects/{p}/unarchive", MANAGE, 200),  # disposable per-call project; never the shared one
    ("DELETE", "/api/projects/{p}", MANAGE, 204),  # disposable per-call project with no audit history; never the shared one
    ("GET", "/api/projects/{p}/context", ALL, 200),
    ("POST", "/api/projects/{p}/context", WRITE, 201),
    ("GET", "/api/projects/{p}/sources", ALL, 200),
    ("GET", "/api/projects/{p}/sources/by-citations", ALL, 200),
    ("GET", "/api/projects/{p}/prisma/checklist", ALL, 200),
    ("GET", "/api/projects/{p}/usage", ALL, 200),
    ("PUT", "/api/projects/{p}/usage/budget", MANAGE, 200),  # owner only: sets this project's own token-budget override
    ("GET", "/api/projects/{p}/ai-settings", ALL, 200),
    ("PUT", "/api/projects/{p}/ai-settings", MANAGE, 200),  # owner only: decides what leaves the project
    ("GET", "/api/projects/{p}/ai-locks", ALL, 200),
    ("PUT", "/api/projects/{p}/ai-locks/source/{s}", WRITE, 200),  # never for this item
    ("DELETE", "/api/projects/{p}/ai-locks/source/{s}", WRITE, 200),
    ("POST", "/api/projects/{p}/searches/suggest-synonyms", WRITE, 200),
    ("GET", "/api/projects/{p}/documents", ALL, 200),
    ("GET", "/api/projects/{p}/documents/numbers", ALL, 200),
    ("POST", "/api/projects/{p}/documents", WRITE, 201),
    ("PATCH", "/api/projects/{p}/documents/{doc}", WRITE, 200),
    ("GET", "/api/projects/{p}/documents/{doc}", ALL, 200),
    ("GET", "/api/projects/{p}/documents/{doc}/versions", ALL, 200),
    ("GET", "/api/projects/{p}/documents/{doc}/versions/{v}", ALL, 200),
    ("GET", "/api/projects/{p}/documents/{doc}/ledger", ALL, 200),
    ("GET", "/api/projects/{p}/documents/{doc}/outline", ALL, 200),
    ("GET", "/api/projects/{p}/documents/{doc}/link-check", ALL, 200),
    ("GET", "/api/projects/{p}/documents/{doc}/overclaim-check", ALL, 200),
    ("POST", "/api/projects/{p}/documents/{doc}/versions", WRITE, 201),
    ("POST", "/api/projects/{p}/documents/{doc}/versions/{v}/restore", WRITE, 200),
    ("POST", "/api/projects/{p}/documents/{doc}/paragraph-from-note", WRITE, 200),
    ("POST", "/api/projects/{p}/documents/{doc}/approve", APPROVE_SECTION, 201),
    ("GET", "/api/projects/{p}/documents/{doc}/approval", ALL, 200),
    # Section drafting from the claim outline (X.46.8 / M6.1.a): request queues a task (202). Behaviour is in
    # test_section_draft_api.py.
    ("POST", "/api/projects/{p}/documents/{doc}/draft-suggestion", WRITE, 202),
    ("POST", "/api/projects/{p}/similarity-checks", WRITE, 202),
    ("GET", "/api/projects/{p}/similarity-checks", ALL, 200),
    ("GET", "/api/projects/{p}/similarity-providers", ALL, 200),
    ("POST", "/api/projects/{p}/provenance-reports", ALL, 201),
    ("GET", "/api/projects/{p}/provenance-reports", ALL, 200),
    ("GET", "/api/projects/{p}/provenance-reports/{rep}", ALL, 200),
    ("GET", "/api/projects/{p}/provenance-reports/{rep}/ai-disclosure", ALL, 200),
    ("GET", "/api/projects/{p}/ai-disclosure-styles", ALL, 200),
    ("GET", "/api/projects/{p}/accuracy-report", ALL, 200),
    ("GET", "/api/projects/{p}/manuscript/export", ALL, 200),
    ("GET", "/api/projects/{p}/manuscript/statements", ALL, 200),
    ("PUT", "/api/projects/{p}/manuscript/statements", WRITE, 200),
    ("GET", "/api/projects/{p}/manuscript/credit", ALL, 200),
    ("PUT", "/api/projects/{p}/manuscript/credit/{cr}", WRITE, 200),
    ("POST", "/api/projects/{p}/sources", WRITE, 201),
    ("POST", "/api/projects/{p}/sources/{s}/verify", WRITE, 200),
    ("POST", "/api/projects/{p}/sources/merge", WRITE, 200),
    ("GET", "/api/projects/{p}/sources/{s}/excerpts", ALL, 200),
    ("POST", "/api/projects/{p}/sources/{s}/excerpts", WRITE, 201),
    ("POST", "/api/projects/{p}/sources/import", WRITE, 201),
    ("POST", "/api/projects/{p}/sources/own", WRITE, 201),
    ("POST", "/api/projects/{p}/sources/own/lookup", WRITE, 202),
    ("GET", "/api/projects/{p}/sources/own/lookup", ALL, 200),
    ("GET", "/api/projects/{p}/sources/export", ALL, 200),
    ("POST", "/api/projects/{p}/sources/{s}/check", WRITE, 202),
    ("POST", "/api/projects/{p}/sources/{s}/fulltext/fetch", WRITE, 202),  # queues only
    ("POST", "/api/projects/{p}/sources/{s}/fulltext/arxiv/fetch", WRITE, 202),  # queues only
    ("POST", "/api/projects/{p}/sources/{s}/fulltext/upload", WRITE, 201),  # raw PDF body; _call gives each principal a fresh source (write-once)
    ("POST", "/api/projects/{p}/sources/{s}/fulltext/withdraw", WRITE, 200),
    ("GET", "/api/projects/{p}/sources/{s}/fulltext", ALL, 200),  # fixture stores full text on source {s} directly
    ("GET", "/api/projects/{p}/searches", ALL, 200),
    ("GET", "/api/projects/{p}/searches/{q}/results", ALL, 200),
    ("GET", "/api/projects/{p}/seeds", ALL, 200),
    ("POST", "/api/projects/{p}/seeds", WRITE, 201),
    ("DELETE", "/api/projects/{p}/seeds/{d}", WRITE, 204),
    ("GET", "/api/projects/{p}/known-items", ALL, 200),
    ("GET", "/api/projects/{p}/recall-report", ALL, 200),
    ("POST", "/api/projects/{p}/searches", WRITE, 202),  # queues only; gate G2 still blocks the run
    ("POST", "/api/projects/{p}/searches/{q}/rerun", WRITE, 202),
    ("GET", "/api/projects/{p}/snowball", ALL, 200),
    ("POST", "/api/projects/{p}/snowball", WRITE, 202),  # queues only; gate G2 still blocks the run
    ("POST", "/api/projects/{p}/searches/{q}/alerts", WRITE, 201),
    ("GET", "/api/projects/{p}/alerts", ALL, 200),
    ("POST", "/api/projects/{p}/alerts/{a}/run", WRITE, 202),  # queues only; gate G2 still blocks the run
    ("DELETE", "/api/projects/{p}/alerts/{a}", WRITE, 204),
    ("GET", "/api/projects/{p}/research-runs", ALL, 200),
    ("POST", "/api/projects/{p}/research-runs", WRITE, 202),
    ("GET", "/api/projects/{p}/claims", ALL, 200),
    ("POST", "/api/projects/{p}/claims", WRITE, 201),
    ("POST", "/api/projects/{p}/claims/{h}/verify", VERIFY, 409),
    ("POST", "/api/projects/{p}/claims/{h}/evidence/{e}/confirm-support", VERIFY, 409),
    ("GET", "/api/projects/{p}/members", ALL, 200),
    ("POST", "/api/projects/{p}/members", MANAGE, 201),
    ("DELETE", "/api/projects/{p}/members/{u}", MANAGE, 204),
    ("GET", "/api/projects/{p}/audit", ALL, 200),
    ("GET", "/api/projects/{p}/audit/export", ALL, 200),
    ("GET", "/api/projects/{p}/export/obsidian", ALL, 200),
    ("GET", "/api/projects/{p}/zotero", ALL, 200),
    ("POST", "/api/projects/{p}/zotero/pull", WRITE, 202),  # queues only; gate G2 still blocks the run
    ("POST", "/api/projects/{p}/zotero/push", WRITE, 202),
    ("GET", "/api/projects/{p}/artifacts", ALL, 200),
    ("GET", "/api/projects/{p}/rerun-path", ALL, 200),
    ("GET", "/api/projects/{p}/stage", ALL, 200),
    ("GET", "/api/projects/{p}/stage/conditions", ALL, 200),
    ("GET", "/api/projects/{p}/profile", ALL, 200),
    ("GET", "/api/projects/{p}/criteria", ALL, 200),
    ("GET", "/api/projects/{p}/prisma", ALL, 200),
    ("GET", "/api/projects/{p}/screening/queue", ALL, 200),
    ("GET", "/api/projects/{p}/screening/decisions", ALL, 200),
    ("POST", "/api/projects/{p}/screening/decisions", WRITE, 201),
    ("POST", "/api/projects/{p}/screening/decisions/undo", WRITE, 201),
    ("POST", "/api/projects/{p}/screening/decisions/adjudicate", APPROVE_G1, 201),  # owner or supervisor rule on a reviewer conflict
    ("POST", "/api/projects/{p}/screening/prescreen", WRITE, 202),  # queues only; gate G2 still blocks the run
    ("POST", "/api/projects/{p}/screening/seed-check", WRITE, 202),  # queues only; gate G2 still blocks the run; writes no screening decision
    ("GET", "/api/projects/{p}/screening/seed-check", ALL, 200),
    ("GET", "/api/projects/{p}/screening/dual-screen", ALL, 200),
    ("PUT", "/api/projects/{p}/screening/dual-screen", MANAGE, 200),  # owner only: whether screening needs two blinded reviewers
    ("GET", "/api/projects/{p}/screening/agreement", ALL, 200),
    ("GET", "/api/projects/{p}/screening/auto-exclude", ALL, 200),
    ("PUT", "/api/projects/{p}/screening/auto-exclude", MANAGE, 200),  # owner only: whether a high-confidence AI exclude can count without a person
    ("GET", "/api/projects/{p}/screening/auto-exclude/audits", ALL, 200),
    ("POST", "/api/projects/{p}/screening/auto-exclude/audits/{k}/review", WRITE, 200),  # write-once per audit row; gives each principal a fresh one
    ("GET", "/api/projects/{p}/screening/ai-agreement", ALL, 200),
    ("PUT", "/api/projects/{p}/screening/ai-prescreen", MANAGE, 200),  # owner only: manually disable/re-enable AI pre-screen
    ("GET", "/api/projects/{p}/screening/active-learning", ALL, 200),
    ("PUT", "/api/projects/{p}/screening/active-learning", MANAGE, 200),  # owner only: whether the queue may rank by predicted relevance
    ("GET", "/api/projects/{p}/extractions", ALL, 200),
    ("GET", "/api/projects/{p}/extractions/{e}", ALL, 200),
    ("GET", "/api/projects/{p}/extractions/verification-queue", ALL, 200),
    ("GET", "/api/projects/{p}/extractions/{e}/pdf", ALL, 200),
    ("POST", "/api/projects/{p}/extractions/{e}/verify", VERIFY, 200),
    ("POST", "/api/projects/{p}/extractions", WRITE, 201),
    ("PUT", "/api/projects/{p}/extractions/{e}", WRITE, 200),
    ("POST", "/api/projects/{p}/extractions/ai", WRITE, 202),
    ("GET", "/api/projects/{p}/meta-analysis", ALL, 200),
    ("POST", "/api/projects/{p}/meta-analysis", WRITE, 409),
    ("POST", "/api/projects/{p}/meta-analysis/{plan_id}/run", WRITE, 404),
    ("GET", "/api/projects/{p}/extraction-schemas", ALL, 200),
    ("GET", "/api/projects/{p}/extraction-schemas/{n}", ALL, 200),
    ("POST", "/api/projects/{p}/extraction-schemas", WRITE, 201),
    ("PUT", "/api/projects/{p}/extraction-schemas/{n}", WRITE, 200),
    ("GET", "/api/projects/{p}/clusters", ALL, 200),
    ("GET", "/api/projects/{p}/coverage-matrix", ALL, 200),
    ("GET", "/api/projects/{p}/bias-warnings", ALL, 200),
    ("GET", "/api/projects/{p}/design/method-recommendation", ALL, 200),
    ("GET", "/api/projects/{p}/design/sample-size", ALL, 200),
    ("GET", "/api/projects/{p}/design/saturation-guidance", ALL, 200),
    ("GET", "/api/projects/{p}/ethics-pack", ALL, 200),
    ("GET", "/api/projects/{p}/preregistration", ALL, 200),
    ("POST", "/api/projects/{p}/instrument/item-checks", ALL, 200),
    ("GET", "/api/projects/{p}/instrument/measures", ALL, 200),
    ("POST", "/api/projects/{p}/instrument/draft", ALL, 200),
    ("POST", "/api/projects/{p}/instrument/validity-plan", ALL, 200),
    ("GET", "/api/projects/{p}/datasets", ALL, 200),
    ("POST", "/api/projects/{p}/datasets", WRITE, 201),
    ("GET", "/api/projects/{p}/datasets/{ds}", ALL, 200),
    ("GET", "/api/projects/{p}/datasets/{ds}/content", ALL, 200),
    ("POST", "/api/projects/{p}/datasets/{ds}/clean", WRITE, 201),
    ("POST", "/api/projects/{p}/datasets/{ds}/exclusion-rules", WRITE, 201),  # M5.7.1: proposing changes no data
    ("GET", "/api/projects/{p}/datasets/{ds}/exclusion-rules", ALL, 200),
    ("POST", "/api/projects/{p}/datasets/{ds}/exclusion-rules/{rule_id}/approve", APPROVE_G1, 200),  # human approval, owner or supervisor
    ("POST", "/api/projects/{p}/datasets/{ds}/exclude", WRITE, 201),  # applies approved rules only, as a new version
    ("GET", "/api/projects/{p}/datasets/{ds}/codebook", ALL, 200),  # M5.7.2: generated on read, stored nowhere
    ("POST", "/api/projects/{p}/datasets/google-forms", WRITE, 503),  # M5.2: no token in the test env; role gate runs first
    ("POST", "/api/projects/{p}/datasets/{ds}/quality", ALL, 200),  # M5.2: read-only flags, nothing stored
    ("POST", "/api/projects/{p}/transcripts/anonymise", WRITE, 200),  # M5.7.3: stateless redaction
    ("POST", "/api/projects/{p}/datasets/{ds}/analysis-plans", WRITE, 201),  # M5.3.1: plans are written by owner and co-author
    ("GET", "/api/projects/{p}/analysis-plans", ALL, 200),
    ("GET", "/api/projects/{p}/analysis-plans/{ap}", ALL, 200),
    ("GET", "/api/projects/{p}/analysis-plans/{ap}/script", ALL, 200),  # M5.3.2: generated text only, nothing runs
    ("POST", "/api/projects/{p}/analysis-plans/{ap}/lock", MANAGE, 200),  # owner only; idempotent after the first lock
    ("POST", "/api/projects/{p}/analysis-plans/{ap}/runs", WRITE, 409),  # M5.3.3: the matrix plan is unlocked, so 409; role gate first
    ("POST", "/api/projects/{p}/qual/suggest", WRITE, 503),  # M5.5.2: no local model in the matrix world, so participant text is refused, never sent to the fake third-party endpoint
    ("GET", "/api/projects/{p}/qual/excerpts", ALL, 200),  # M5.5.2
    ("POST", "/api/projects/{p}/qual/decisions", WRITE, 404),  # M5.5.2: no pending suggestion exists in the matrix world; role gate first
    ("GET", "/api/projects/{p}/qual/codebook", ALL, 200),  # M5.5.2
    ("POST", "/api/projects/{p}/qual/codebook/rename", WRITE, 409),  # M5.5.2: no such code in the matrix world; role gate first
    ("POST", "/api/projects/{p}/qual/codebook/merge", WRITE, 409),  # M5.5.2: no such code in the matrix world; role gate first
    ("GET", "/api/projects/{p}/analysis-plans/{ap}/runs", ALL, 200),  # M5.3.3: stored runs only
    ("POST", "/api/projects/{p}/analysis-plans/{ap}/runs/{run}/robustness", MANAGE, 404),  # M5.4.2: no run exists for the fake id in the matrix world; role gate first
    ("GET", "/api/projects/{p}/analysis-plans/{ap}/runs/{run}/robustness", ALL, 404),  # M5.4.2
    ("GET", "/api/projects/{p}/analysis-plans/{ap}/runs/{run}/notebook", ALL, 404),  # M5.4.2
    ("GET", "/api/projects/{p}/analysis-plans/{ap}/runs/{run}/tables", ALL, 404),  # M5.8.3: no run exists for the fake id in the matrix world
    ("POST", "/api/projects/{p}/analysis-plans/{ap}/deviation-logs", WRITE, 409),  # M5.8.4: the matrix world's plan is unlocked; role gate first
    ("GET", "/api/projects/{p}/analysis-plans/{ap}/deviation-logs", ALL, 200),  # M5.8.4: stored logs only (none in the matrix world)
    ("POST", "/api/projects/{p}/interpretation/generate", WRITE, 409),  # M6.9: no analysis run exists in the matrix world; role gate first
    ("GET", "/api/projects/{p}/interpretation/findings", ALL, 200),
    ("POST", "/api/projects/{p}/interpretation/findings/{finding_id}/review", WRITE, 404),  # M6.9: no such finding in the matrix world; role gate first
    ("POST", "/api/projects/{p}/interpretation/draft", WRITE, 409),  # M6.9: no map generated in the matrix world; role gate first
    ("GET", "/api/projects/{p}/constructs", ALL, 200),
    ("POST", "/api/projects/{p}/constructs", WRITE, 201),
    ("GET", "/api/projects/{p}/theories", ALL, 200),
    ("POST", "/api/projects/{p}/theories", WRITE, 201),
    ("GET", "/api/projects/{p}/conceptual-model", ALL, 200),
    ("POST", "/api/projects/{p}/conceptual-model/paths", WRITE, 201),
    ("DELETE", "/api/projects/{p}/conceptual-model/paths/{cp}", WRITE, 204),
    ("GET", "/api/projects/{p}/gaps", ALL, 200),
    ("POST", "/api/projects/{p}/gaps", WRITE, 201),
    ("PATCH", "/api/projects/{p}/gaps/{gap_id}", WRITE, 200),
    ("GET", "/api/projects/{p}/research-questions", ALL, 200),
    ("POST", "/api/projects/{p}/research-questions", WRITE, 201),
    ("PATCH", "/api/projects/{p}/research-questions/{rq}", WRITE, 200),
    ("GET", "/api/projects/{p}/research-questions/{rq}/finer", ALL, 200),
    ("GET", "/api/projects/{p}/brief", ALL, 200),
    ("PUT", "/api/projects/{p}/brief", WRITE, 200),
    ("GET", "/api/projects/{p}/idea-brief", ALL, 200),
    ("GET", "/api/projects/{p}/idea-brief/semantic-novelty", ALL, 200),
    ("GET", "/api/projects/{p}/idea-brief/landscape-scan", ALL, 200),  # no query in the matrix: empty scan, no search
    ("PUT", "/api/projects/{p}/idea-brief", WRITE, 200),
    ("GET", "/api/projects/{p}/brief/query", ALL, 200),
    ("GET", "/api/projects/{p}/hypotheses", ALL, 200),
    ("POST", "/api/projects/{p}/hypotheses", WRITE, 201),
    ("PATCH", "/api/projects/{p}/hypotheses/{hp}", WRITE, 200),
    ("GET", "/api/projects/{p}/evidence-matrix", ALL, 200),
    ("GET", "/api/projects/{p}/evidence-matrix/contradictions", ALL, 200),
    ("GET", "/api/projects/{p}/evidence-matrix/export.csv", ALL, 200),
    ("GET", "/api/projects/{p}/evidence-matrix/export.xlsx", ALL, 200),
    ("GET", "/api/projects/{p}/evidence-matrix/{e}/fields/{field_key}/source-text", ALL, 200),
    ("GET", "/api/projects/{p}/bibliometrics/publication-trends", ALL, 200),
    ("GET", "/api/projects/{p}/scholar-metrics", ALL, 200),
    ("GET", "/api/projects/{p}/bibliometrics/inputs/{s}", ALL, 200),
    ("PUT", "/api/projects/{p}/bibliometrics/inputs/{s}", WRITE, 200),
    ("GET", "/api/projects/{p}/bibliometrics/networks", ALL, 200),
    ("GET", "/api/projects/{p}/bibliometrics/export/vosviewer/co_citation.zip", ALL, 200),
    ("GET", "/api/projects/{p}/bibliometrics/export/citespace.csv", ALL, 200),
    ("GET", "/api/projects/{p}/export/replication", MANAGE, 200),  # owner only (2026-10-19 decision): per-reviewer screening decisions
    ("GET", "/api/projects/{p}/clusters/{c}", ALL, 200),
    ("POST", "/api/projects/{p}/clusters", WRITE, 202),  # queues only
    ("PATCH", "/api/projects/{p}/clusters/{c}/clusters/{l}", WRITE, 200),
    ("PUT", "/api/projects/{p}/criteria", WRITE, 200),  # who edits criteria; G2 approval is separate
    ("PUT", "/api/projects/{p}/profile", MANAGE, 200),  # owner only: decides databases and reporting norms
    ("PUT", "/api/projects/{p}/research-type", MANAGE, 409),  # matrix project is past idea; auth still must be owner-only
    ("POST", "/api/projects/{p}/stage/advance", WRITE, 200),
    ("POST", "/api/projects/{p}/stage/reenter", MANAGE, 200),  # owner only: it voids approvals and stales work
    ("GET", "/api/projects/{p}/stage/history", ALL, 200),
    ("GET", "/api/projects/{p}/gates", ALL, 200),
    ("POST", "/api/projects/{p}/gates/G1/approve", APPROVE_G1, 200),
    ("POST", "/api/projects/{p}/gates/G1/reject", APPROVE_G1, 200),
    # The project here is past G1's stage, so a permitted caller is told to use re-entry (409); reopening
    # itself is covered in test_gate_reopen.py. This row checks who may get that far.
    ("POST", "/api/projects/{p}/gates/G1/reopen", APPROVE_G1, 409),
    ("GET", "/api/projects/{p}/suggestions", ALL, 200),
    ("GET", "/api/projects/{p}/suggestions/{g}/why", ALL, 200),
    ("POST", "/api/projects/{p}/suggestions/{g}/accept", WRITE, 200),
    ("POST", "/api/projects/{p}/suggestions/{g}/edit-accept", WRITE, 200),
    ("POST", "/api/projects/{p}/suggestions/{g}/discard", WRITE, 200),
    ("POST", "/api/projects/{p}/suggestions/{g}/restore", WRITE, 200),
    ("POST", "/api/projects/{p}/suggestions/{g}/undo", WRITE, 200),
    # No producer is registered for the matrix's target kind, so a permitted caller is told "nothing can answer
    # again" (409); asking again with a producer is covered in test_suggestions_api.py.
    ("POST", "/api/projects/{p}/suggestions/{g}/ask-again", WRITE, 409),
    ("POST", "/api/projects/{p}/suggestions/bulk", WRITE, 200),
    # AI side panel (X.31.11): request queues a task (202); preview sends nothing (200). Behaviour is in test_workspace_ai_api.py.
    ("POST", "/api/projects/{p}/ai/suggestions", WRITE, 202),
    ("POST", "/api/projects/{p}/ai/suggestions/preview", WRITE, 200),
]

BODIES = {
    ("POST", "/api/projects/{p}/qual/suggest"): {"excerpt": "A matrix excerpt about trust."},
    ("POST", "/api/projects/{p}/interpretation/findings/{finding_id}/review"): {"decision": "confirmed"},
    ("POST", "/api/projects/{p}/qual/decisions"): {"suggestion_id": str(uuid.uuid4()), "decision": "accept"},
    ("POST", "/api/projects/{p}/qual/codebook/rename"): {"old": "matrix-code", "new": "matrix-code-2"},
    ("POST", "/api/projects/{p}/qual/codebook/merge"): {"source": "matrix-code", "target": "matrix-code-2"},
    ("POST", "/api/projects/{p}/documents"): {"title": "Matrix section", "position": 9},
    ("PATCH", "/api/projects/{p}/documents/{doc}"): {"title": "Matrix title"},
    ("PUT", "/api/projects/{p}/bibliometrics/inputs/{s}"): {"reference_ids": ["10.1234/ref"], "keywords": ["research"]},
    ("PUT", "/api/projects/{p}/usage/budget"): {"override": 5000},
    ("PUT", "/api/projects/{p}/manuscript/statements"): {"data_availability_statement": "Data on request."},
    ("PUT", "/api/projects/{p}/manuscript/credit/{cr}"): {"roles": ["Investigation"]},
    ("POST", "/api/projects/{p}/sources/{s}/excerpts"): {"content": "Matrix passage.", "locator": "p. 1"},
    ("PUT", "/api/projects/{p}/ai-settings"): {"ai_involvement": "suggestions_only"},
    ("PUT", "/api/projects/{p}/profile"): {"discipline": "economics", "overrides": {"databases": ["openalex"]}},
    ("PUT", "/api/projects/{p}/criteria"): {"framework": "custom", "criteria": [{"kind": "include", "text": "Peer-reviewed studies"}]},
    ("PUT", "/api/projects/{p}/research-type"): {"name": "literature_review"},
    ("POST", "/api/projects/{p}/stage/reenter"): {"stage": "scoped", "reason": "Rework the scope"},
    ("POST", "/api/projects/{p}/context"): {"kind": "question", "content": "Why?"},
    ("POST", "/api/projects/{p}/sources"): {"title": "A source"},
    ("POST", "/api/projects/{p}/constructs"): {"name": "Trust", "definition": "Definition", "author": "Smith"},
    ("POST", "/api/projects/{p}/theories"): {"name": "Social exchange", "description": "A lens."},
    ("POST", "/api/projects/{p}/gaps"): {"type": "empirical", "statement": "A gap", "matrix_cell_ids": ["cell"]},
    ("PATCH", "/api/projects/{p}/gaps/{gap_id}"): {"priority": 5, "selected": True},
    ("PUT", "/api/projects/{p}/brief"): {"topic": "Remote collaboration", "keyword_groups": [{"terms": ["trust"]}], "min_citations": 3},
    ("PUT", "/api/projects/{p}/idea-brief"): {"problem": "Teams stall", "why_it_matters": "Delivery slips"},
    ("POST", "/api/projects/{p}/instrument/item-checks"): {"items": ["The course met my expectations."]},
    ("POST", "/api/projects/{p}/instrument/draft"): {"measure_keys": ["bfi"]},
    ("POST", "/api/projects/{p}/instrument/validity-plan"): {"measure_keys": ["pss"], "own_item_count": 3},
    ("POST", "/api/projects/{p}/datasets"): {"name": "matrix upload", "csv_text": "a,b\n1,2\n"},
    ("POST", "/api/projects/{p}/datasets/{ds}/clean"): {"name": "matrix clean", "steps": [{"op": "trim_whitespace"}]},
    ("POST", "/api/projects/{p}/datasets/google-forms"): {"form_id": "matrixform", "name": "matrix forms"},
    ("POST", "/api/projects/{p}/datasets/{ds}/quality"): {},
    ("POST", "/api/projects/{p}/datasets/{ds}/exclusion-rules"): {"column": "a", "op": "equals", "value": "9"},
    ("POST", "/api/projects/{p}/transcripts/anonymise"): {"text": "Ann met Bo.", "terms": ["Ann"]},
    ("POST", "/api/projects/{p}/datasets/{ds}/analysis-plans"): {"tests": ["descriptives"], "variables": ["a"], "seed": 1},
    ("POST", "/api/projects/{p}/analysis-plans/{ap}/runs/{run}/robustness"): {"kind": "rerun_identity"},
    ("POST", "/api/projects/{p}/analysis-plans/{ap}/deviation-logs"): {"actual_run_id": "00000000-0000-0000-0000-000000000000", "reasons": {}},
    ("POST", "/api/projects/{p}/research-questions"): {"text": "How does trust affect collaboration?", "type": "relational", "gap_ids": []},
    ("PATCH", "/api/projects/{p}/research-questions/{rq}"): {"type": "exploratory"},
    ("POST", "/api/projects/{p}/hypotheses"): {"research_question_id": "{rq}", "statement": "Trust predicts collaboration.", "direction": "positive", "theoretical_basis": "Theory", "empirical_support": "Evidence"},
    ("PATCH", "/api/projects/{p}/hypotheses/{hp}"): {"direction": "non_directional"},
    ("POST", "/api/projects/{p}/searches/suggest-synonyms"): {"block": {"label": "work", "terms": ["remote work"]}},
    ("POST", "/api/projects/{p}/sources/import"): {"format": "bibtex", "text": "@article{k, title={Imported}}"},
    ("POST", "/api/projects/{p}/sources/own"): {"title": "Own paper"},
    ("POST", "/api/projects/{p}/research-runs"): {"question": "A long enough question?"},
    ("POST", "/api/projects/{p}/claims"): {"text": "A claim awaiting evidence"},
    ("POST", "/api/projects/{p}/seeds"): {"title": "Another seed paper", "doi": "10.1234/seedx"},
    ("POST", "/api/projects/{p}/searches"): {"database": "openalex", "blocks": [{"label": "a", "terms": ["remote work"]}]},
    ("POST", "/api/projects/{p}/snowball"): {"connector": "openalex", "rounds": 1},
    ("POST", "/api/projects/{p}/gates/G1/reject"): {"note": "Needs a clearer scope"},
    ("POST", "/api/projects/{p}/gates/G1/reopen"): {"reason": "Scope needs another look"},
    ("POST", "/api/projects/{p}/suggestions/{g}/edit-accept"): {"text": "My own wording"},
    ("POST", "/api/projects/{p}/suggestions/{g}/ask-again"): {"guidance": "Shorter, please"},
    ("POST", "/api/projects/{p}/suggestions/bulk"): {"accept_at_or_above": 0.9},
    ("POST", "/api/projects/{p}/clusters"): {"k": 2},
    ("PATCH", "/api/projects/{p}/clusters/{c}/clusters/{l}"): {"label": "Remote work and wellbeing"},
    ("POST", "/api/projects/{p}/extraction-schemas"): {"name": "copied_schema", "based_on": "default"},
    ("PUT", "/api/projects/{p}/extraction-schemas/{n}"): {"label": "Lab schema", "description": "Edited", "fields": [{"key": "sample_size", "type": "integer", "description": "N"}]},
    ("PUT", "/api/projects/{p}/extractions/{e}"): {"fields": {"research_question": "Why?"}, "evidence": {"research_question": {"quote": "We ask why", "page": 1}}},
    ("PUT", "/api/projects/{p}/screening/auto-exclude"): {"enabled": True, "confidence_threshold": 0.9},
    ("PUT", "/api/projects/{p}/screening/ai-prescreen"): {"disabled": True},
    ("PUT", "/api/projects/{p}/screening/active-learning"): {"enabled": True},
    ("POST", "/api/projects/{p}/extractions/{e}/verify"): {"confirmed_critical_fields": ["measures", "sample", "key_findings"],
                                                                         "confirmed_not_reported_fields": ["measures", "sample", "key_findings"]},
    ("POST", "/api/projects/{p}/extractions/ai"): {"source_id": ""},
    ("POST", "/api/projects/{p}/meta-analysis"): {"outcome": "Outcome", "primary_model": "fixed", "studies": [{"extraction_id": "00000000-0000-0000-0000-000000000001", "finding_index": 0}, {"extraction_id": "00000000-0000-0000-0000-000000000002", "finding_index": 0}]},
}

# Principals: the four roles (the creator is the owner), a second owner added via a member row,
# a user with no membership, and someone not signed in.
PRINCIPALS = ["owner", "owner_member", "co_author", "supervisor", "reviewer", "stranger", "anonymous"]


def _login(email):
    client = TestClient(app)
    user_id = client.post("/api/auth/development/login", json={"email": email, "display_name": "M"}).json()["id"]
    return client, user_id


@pytest.fixture
def world(monkeypatch, fake_llm):
    from app.config import get_settings

    # Only the synonym-suggestion route calls a model here; give it a well-formed reply.
    fake_llm.handler = lambda r: {"choices": [{"message": {"content": '{"suggestions": []}'}}]}

    monkeypatch.setattr(get_settings(), "connectors_enabled", ["openalex", "unpaywall", "arxiv"])
    monkeypatch.setattr(get_settings(), "connector_contact_email", "matrix@example.com")
    monkeypatch.setattr(get_settings(), "obsidian_export_enabled", True)
    monkeypatch.setattr(get_settings(), "zotero_sync_enabled", True)
    from pydantic import SecretStr
    monkeypatch.setattr(get_settings(), "zotero_api_key", SecretStr("matrix-key"))
    monkeypatch.setattr(get_settings(), "zotero_user_id", "12345")
    monkeypatch.setattr(get_settings(), "embedding_model", "test-embedding")  # only POST /clusters checks it; nothing is embedded here
    tag = uuid.uuid4().hex[:8]
    owner, _ = _login(f"matrix-owner-{tag}@example.com")
    owner_id = owner.get("/api/auth/me").json()["id"]
    project_id = owner.post("/api/projects", json={"title": "Matrix"}).json()["id"]
    source_id = owner.post(f"/api/projects/{project_id}/sources", json={"title": "Seed", "doi": "10.1234/seed"}).json()["id"]
    source_pdf = make_pdf([["Introduction", "We ask why"]])
    extraction_source_id = owner.post(
        f"/api/projects/{project_id}/sources", json={"title": "Extraction target"}
    ).json()["id"]
    source_key = fulltext_key(uuid.UUID(project_id), uuid.UUID(extraction_source_id), "pdf")

    class ExtractionStore:
        def get(self, requested_key):
            assert requested_key == source_key
            return source_pdf

    from app.routers import extractions as extraction_routes
    monkeypatch.setattr(extraction_routes, "get_object_store", lambda settings: ExtractionStore())
    from app.routers import evidence_matrix as evidence_routes
    monkeypatch.setattr(evidence_routes, "get_object_store", lambda settings: ExtractionStore())
    with SessionLocal() as db:
        db.get(Source, uuid.UUID(extraction_source_id)).fulltext_path = source_key
        db.commit()
    twin_id = owner.post(f"/api/projects/{project_id}/sources", json={"title": "Seed (copy)", "doi": "10.1234/seed"}).json()["id"]
    with SessionLocal() as db:  # past the first stage, so there is somewhere earlier to re-enter
        db.get(Project, uuid.UUID(project_id)).stage = ProjectStage.search_planned  # next stage (retrieved) has no gate
        db.commit()

    clients = {"owner": owner, "anonymous": TestClient(app), "stranger": _login(f"matrix-stranger-{tag}@example.com")[0]}
    for name, role in (
        ("owner_member", ProjectRole.owner),
        ("co_author", ProjectRole.co_author),
        ("supervisor", ProjectRole.supervisor),
        ("reviewer", ProjectRole.reviewer),
    ):
        client, user_id = _login(f"matrix-{name}-{tag}@example.com")
        with SessionLocal() as db:
            db.add(ProjectMember(project_id=uuid.UUID(project_id), user_id=uuid.UUID(user_id), role=role))
            db.commit()
        clients[name] = client

    with SessionLocal() as db:  # an earlier run of a search, so it can be re-run and watched
        from app.models import SearchAlert

        search_id = uuid.uuid4()
        db.add(SearchQuery(
            project_id=uuid.UUID(project_id), search_id=search_id, database="openalex", query_string='"x"',
            run_at=utcnow(), n_results=1, counts={"retrieved": 1, "unique": 1},
            results=[{"id": "W1", "doi": "10.1000/matrix", "work_key": "matrix-paper"}],
        ))
        # A second, already-run search that already has an alert: the target of the run/delete routes.
        alerted_search_id = uuid.uuid4()
        db.add(SearchQuery(
            project_id=uuid.UUID(project_id), search_id=alerted_search_id, database="openalex", query_string='"y"',
            run_at=utcnow(), n_results=1, counts={"retrieved": 1, "unique": 1},
            results=[{"id": "W2", "doi": "10.1000/matrix-2", "work_key": "matrix-paper-2"}],
        ))
        alert_id = uuid.uuid4()
        db.add(SearchAlert(
            id=alert_id,
            project_id=uuid.UUID(project_id), search_id=alerted_search_id, interval_seconds=86400,
            next_run_at=utcnow(), enabled=True, created_by="matrix",
        ))
        db.commit()

    with SessionLocal() as db:
        seed_id = uuid.uuid4()
        db.add(SeedPaper(id=seed_id, project_id=uuid.UUID(project_id), title="A seed paper title"))
        db.commit()

    with SessionLocal() as db:  # an unverified extraction to read and edit
        extraction_id = uuid.uuid4()
        db.add(Extraction(id=extraction_id, project_id=uuid.UUID(project_id), source_id=uuid.UUID(extraction_source_id), schema_version="default@1",
                          fields_json={"research_question": "Why?"},
                          evidence_spans={"research_question": {"quote": "We ask why", "page": 1}},
                          extracted_by="human", extractor="seed"))
        db.commit()

    with SessionLocal() as db:
        claim_id = uuid.uuid4()
        db.add(Claim(id=claim_id, project_id=uuid.UUID(project_id), text="A pending claim",
                     verified=False, created_by="matrix"))
        db.commit()

    gap_response = owner.post(f"/api/projects/{project_id}/gaps", json={"type": "empirical", "statement": "Matrix gap", "supporting_paper_ids": [source_id, twin_id]})
    gap_id = gap_response.json()["id"]
    assert owner.patch(f"/api/projects/{project_id}/gaps/{gap_id}", json={"selected": True}).status_code == 200
    question_id = owner.post(f"/api/projects/{project_id}/research-questions", json={
        "text": "How does trust affect the matrix project?", "type": "relational", "gap_ids": [gap_id],
    }).json()["id"]
    hypothesis_id = owner.post(f"/api/projects/{project_id}/hypotheses", json={
        "research_question_id": question_id, "statement": "Trust predicts matrix collaboration.", "direction": "positive",
        "theoretical_basis": "Theory", "empirical_support": "Evidence",
    }).json()["id"]

    ai_source = owner.post(f"/api/projects/{project_id}/sources", json={"title": "AI extraction target"}).json()
    with SessionLocal() as db:
        db.get(Source, uuid.UUID(ai_source["id"])).fulltext_path = fulltext_key(uuid.UUID(project_id), uuid.UUID(ai_source["id"]), "pdf")
        db.commit()

    with SessionLocal() as db:  # a project extraction schema to read and edit
        db.add(ProjectExtractionSchema(project_id=uuid.UUID(project_id), name="lab_schema", version=1, label="Lab", description="",
                                       fields=[{"key": "sample_size", "type": "integer", "description": "N", "critical": False}],
                                       based_on="default@1", created_by="seed"))
        db.commit()

    with SessionLocal() as db:  # a stored clustering with one cluster to read and rename
        clustering_id, cluster_id = uuid.uuid4(), uuid.uuid4()
        db.add(ClusterRun(id=clustering_id, project_id=uuid.UUID(project_id), model_id="test-embedding", k=2, seed=0, n_sources=2,
                          created_by="seed", clusters=[Cluster(id=cluster_id, position=1, label="Cluster 1")]))
        db.commit()

    with SessionLocal() as db:  # a sampled AI auto-exclude awaiting review
        autoexclude_decision_id = uuid.uuid4()
        db.add(ScreeningDecision(id=autoexclude_decision_id, project_id=uuid.UUID(project_id), source_id=uuid.UUID(source_id),
                                 stage="title_abstract", seq=1, decision="exclude", reason_code="E1", decided_by="ai",
                                 decider="agent:prescreen", confidence=0.95))
        db.commit()
        audit_id = uuid.uuid4()
        db.add(AutoExcludeAudit(id=audit_id, project_id=uuid.UUID(project_id), source_id=uuid.UUID(source_id), stage="title_abstract",
                                decision_id=autoexclude_decision_id, confidence=0.95))
        db.commit()

    own_paper_lookup_task_id = uuid.uuid4()  # a resolved own-paper lookup to read (M2.14.1)
    with SessionLocal() as db:
        db.add(OwnPaperLookup(project_id=uuid.UUID(project_id), task_id=own_paper_lookup_task_id,
                              doi="10.1234/matrix-lookup", status="not_found",
                              detail="No enabled lookup connector has a matching record for this DOI.",
                              requested_by="matrix"))
        db.commit()

    # Targets for the member routes: an uninvited user to add, and a removable reviewer.
    _login(f"matrix-invitee-{tag}@example.com")
    _, removable_id = _login(f"matrix-removable-{tag}@example.com")
    removable_member_id = uuid.uuid4()
    with SessionLocal() as db:
        db.add(ProjectMember(id=removable_member_id, project_id=uuid.UUID(project_id), user_id=uuid.UUID(removable_id), role=ProjectRole.reviewer))
        db.commit()
    doc_id = _seed_document(project_id, owner_id)
    from app import datasets as dataset_rules  # M5.1: a raw dataset for the dataset routes
    from app.models import DatasetVersion

    dataset_id = uuid.uuid4()
    with SessionLocal() as db:
        db.add(DatasetVersion(id=dataset_id, project_id=uuid.UUID(project_id), version_no=1, parent_id=None, kind="raw",
                              name="matrix", content="a,b\n1,2\n", sha256=dataset_rules.sha256_hex("a,b\n1,2\n"), row_count=1,
                              column_names=["a", "b"], cleaning_steps=[], created_by=str(owner_id)))
        db.commit()
    plan = owner.post(f"/api/projects/{project_id}/datasets/{dataset_id}/analysis-plans",
                      json={"tests": ["descriptives"], "variables": ["a"], "seed": 1})
    assert plan.status_code == 201, plan.text
    rule = owner.post(f"/api/projects/{project_id}/datasets/{dataset_id}/exclusion-rules", json={"column": "a", "op": "is_blank"})  # M5.7.1
    assert rule.status_code == 201, rule.text
    assert owner.post(f"/api/projects/{project_id}/datasets/{dataset_id}/exclusion-rules/{rule.json()['id']}/approve").status_code == 200
    return {
        "clients": clients,
        "ids": {"p": project_id, "s": source_id, "extract_s": extraction_source_id, "ai_s": ai_source["id"], "h": str(claim_id), "gap_id": gap_id, "rq": question_id, "hp": hypothesis_id, "field_key": "research_question", "u": removable_id, "q": str(search_id), "d": str(seed_id), "e": str(extraction_id), "plan_id": str(uuid.uuid4()), "cp": str(uuid.uuid4()), "c": str(clustering_id), "l": str(cluster_id), "n": "lab_schema", "a": str(alert_id), "k": str(audit_id), "g": _fresh_suggestion(project_id, []), "doc": doc_id, "ds": str(dataset_id), "ap": plan.json()["id"], "run": str(uuid.uuid4()), "rule_id": rule.json()["id"], "rep": _seed_report(project_id, owner_id, doc_id), "v": "1", "author": owner_id, "cr": str(removable_member_id), "lo": str(own_paper_lookup_task_id), "finding_id": str(uuid.uuid4())},
        "twin": twin_id,
        "invitee": f"matrix-invitee-{tag}@example.com",
    }


def _fresh_suggestion(project_id, history):
    """A new AI suggestion on the project, moved through `history` (event kinds) by a person."""
    from app import suggestion_store

    with SessionLocal() as db:
        suggestion = suggestion_store.create_suggestion(
            db, project_id=uuid.UUID(project_id), target_kind="matrix_target", target_id="t1", content="AI wording",
            model_id="test-model", prompt_version="v1", producing_agent="matrix", confidence=0.5,
        )
        for kind in history:
            suggestion_store.act(db, suggestion, kind, "user:matrix")
        db.commit()
        return str(suggestion.id)


def _seed_document(project_id, author_id):
    """A manuscript section with one saved version (X.31.9), written straight to the database."""
    from app.models import Document, DocumentVersion

    content = {"type": "doc", "content": [{"type": "paragraph", "content": [{
        "type": "text", "text": "Seed.",
        "marks": [{"type": "prov", "attrs": {"sid": "seed", "origin": "typed", "by": author_id, "sug": None, "note": None, "paste": None, "src": None}}],
    }]}]}
    with SessionLocal() as db:
        document = Document(project_id=uuid.UUID(project_id), title="Matrix section", position=1, created_by=f"user:{author_id}")
        db.add(document)
        db.flush()
        version = DocumentVersion(document_id=document.id, project_id=uuid.UUID(project_id), number=1, content_json=content,
                                  content_text="Seed.", content_hash="0" * 64, saved_by=author_id, client_save_id="seed")
        db.add(version)
        db.flush()
        document.current_version_id = version.id
        db.commit()
        return str(document.id)


def _fresh_search(project_id):
    """A completed search row with no open task and no alert subscription, for routes that refuse
    a second concurrent run or a second alert on the same search (so a success can't 409 the next principal)."""
    with SessionLocal() as db:
        search_id = uuid.uuid4()
        db.add(SearchQuery(
            project_id=uuid.UUID(project_id), search_id=search_id, database="openalex", query_string='"x"',
            run_at=utcnow(), n_results=1, counts={"retrieved": 1, "unique": 1},
            results=[{"id": "W1", "doi": "10.1000/matrix", "work_key": "matrix-paper"}],
        ))
        db.commit()
        return str(search_id)


def _seed_report(project_id, owner_id, document_id):
    """A saved provenance report for the GET-by-id route (X.31.12)."""
    from app import provenance_store
    from app.models import Document, Project, User

    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(project_id))
        user = db.get(User, uuid.UUID(owner_id))
        document = db.get(Document, uuid.UUID(document_id))
        row = provenance_store.generate(db, project, user=user, scope="section", documents=[document], document_id=document.id)
        db.commit()
        return str(row.id)


def _call(world, principal, method, template):
    path = template.format(**world["ids"])
    if (method, template) in (
        ("POST", "/api/projects/{p}/archive"), ("POST", "/api/projects/{p}/unarchive"), ("DELETE", "/api/projects/{p}"),
    ):
        # A disposable project per call, built directly (no `project.created` audit row, so a
        # delete call can actually succeed), membered to match the principal under test, so a
        # real archive/unarchive/delete can never touch the shared matrix project or any other
        # principal's row.
        owner_user_id = world["ids"]["author"]
        role_for = {
            "owner_member": ProjectRole.owner, "co_author": ProjectRole.co_author,
            "supervisor": ProjectRole.supervisor, "reviewer": ProjectRole.reviewer,
        }
        # Fetched before opening the DB session below: a nested TestClient call sharing the same
        # single StaticPool SQLite connection while this session has a pending transaction corrupts it.
        caller_id = world["clients"][principal].get("/api/auth/me").json().get("id") if principal in role_for else None
        with SessionLocal() as db:
            fresh = Project(owner_id=uuid.UUID(owner_user_id), title="Lifecycle matrix target")
            db.add(fresh)
            db.flush()
            if caller_id is not None:
                db.add(ProjectMember(project_id=fresh.id, user_id=uuid.UUID(caller_id), role=role_for[principal]))
            if template == "/api/projects/{p}/unarchive":
                fresh.archived_at = utcnow()
            db.commit()
            fresh_id = str(fresh.id)
        path = path.replace(f"/projects/{world['ids']['p']}", f"/projects/{fresh_id}")
        return world["clients"][principal].request(method, path)
    if method == "GET" and template.endswith("/stage/conditions"):
        path += "?target=scoped"
    if method == "GET" and template.endswith("/design/method-recommendation"):
        path += "?goal=explore"
    if method == "GET" and template.endswith("/design/sample-size"):
        path += "?test=correlation&r=0.3"
    if method == "GET" and template.endswith("/design/saturation-guidance"):
        path += "?design=interviews"
    if method == "GET" and template.endswith("/ledger"):
        path += "?span_id=seed"
    if method == "GET" and template.endswith("/similarity-checks"):
        path += f"?document_id={world['ids']['doc']}"
    if method == "GET" and template == "/api/projects/{p}/sources/own/lookup":
        path += f"?task_id={world['ids']['lo']}"
    body = BODIES.get((method, template))
    if (method, template) == ("POST", "/api/projects/{p}/datasets/{ds}/exclude"):
        body = {"name": "matrix exclude", "rule_ids": [world["ids"]["rule_id"]]}
    if (method, template) == ("POST", "/api/projects/{p}/sources/own/lookup"):
        # A fresh DOI per call, so a success can't 409 the next principal.
        body = {"doi": f"10.9999/{uuid.uuid4().hex}"}
    if (method, template) == ("POST", "/api/projects/{p}/similarity-checks"):
        body = {"document_id": world["ids"]["doc"]}
    if (method, template) in (("POST", "/api/projects/{p}/ai/suggestions"), ("POST", "/api/projects/{p}/ai/suggestions/preview")):
        body = {"action": "challenge", "text": "A passage to check.", "document_id": world["ids"]["doc"]}
    if (method, template) == ("POST", "/api/projects/{p}/provenance-reports"):
        body = {"scope": "section", "document_id": world["ids"]["doc"]}
    if (method, template) == ("POST", "/api/projects/{p}/members"):
        body = {"email": world["invitee"], "role": "reviewer"}
    if (method, template) == ("POST", "/api/projects/{p}/gaps"):
        body = {"type": "empirical", "statement": f"A gap {uuid.uuid4()}", "supporting_paper_ids": [world["ids"]["s"], world["twin"]]}
    if (method, template) == ("POST", "/api/projects/{p}/research-questions"):
        body = {"text": f"How does trust affect collaboration {uuid.uuid4()}?", "type": "relational", "gap_ids": [world["ids"]["gap_id"]]}
    if (method, template) == ("POST", "/api/projects/{p}/hypotheses"):
        question = world["clients"]["owner"].post(
            f"/api/projects/{world['ids']['p']}/research-questions",
            json={"text": f"Hypothesis question {uuid.uuid4()}?", "type": "relational", "gap_ids": [world["ids"]["gap_id"]]},
        ).json()
        body = {"research_question_id": question["id"], "statement": f"Trust predicts collaboration {uuid.uuid4()}.", "direction": "positive", "theoretical_basis": "Theory", "empirical_support": "Evidence"}
    if template.startswith("/api/projects/{p}/conceptual-model/paths"):
        # Fresh constructs per call, so a success can't 409 the next principal; DELETE removes a path the owner just drew.
        owner, pid = world["clients"]["owner"], world["ids"]["p"]
        ends = [owner.post(f"/api/projects/{pid}/constructs", json={"name": f"Matrix {uuid.uuid4()}", "definition": "D", "author": "A"}).json()["id"]
                for _ in range(2)]
        body = {"from_construct_id": ends[0], "to_construct_id": ends[1], "kind": "direct", "source_ids": [world["ids"]["s"]], "rationale": "Matrix."}
        if method == "DELETE":
            drawn = owner.post(f"/api/projects/{pid}/conceptual-model/paths", json=body).json()["id"]
            return world["clients"][principal].delete(f"/api/projects/{pid}/conceptual-model/paths/{drawn}")
    if (method, template) == ("POST", "/api/projects/{p}/sources/merge"):
        # Fresh duplicate-DOI pair per call, so a success can't 409 the next principal.
        owner, pid = world["clients"]["owner"], world["ids"]["p"]
        doi = f"10.9999/{uuid.uuid4().hex}"
        a = owner.post(f"/api/projects/{pid}/sources", json={"title": "Merge dup", "doi": doi}).json()["id"]
        b = owner.post(f"/api/projects/{pid}/sources", json={"title": "Merge dup (copy)", "doi": doi}).json()["id"]
        body = {"source_ids": [a, b]}
    if (method, template) == ("POST", "/api/projects/{p}/sources/{s}/check"):
        # A fresh source per call: the dedup guard refuses a second queued check on the same source.
        fresh = world["clients"]["owner"].post(f"/api/projects/{world['ids']['p']}/sources", json={"title": "Check target"}).json()
        path = path.replace(f"/sources/{world['ids']['s']}/", f"/sources/{fresh['id']}/")
    if (method, template) == ("POST", "/api/projects/{p}/sources/{s}/fulltext/fetch"):
        # A fresh source per call: the dedup guard refuses a second queued fetch on the same source.
        fresh = world["clients"]["owner"].post(f"/api/projects/{world['ids']['p']}/sources", json={"title": "Fulltext fetch target", "doi": f"10.9999/{uuid.uuid4().hex}"}).json()
        path = path.replace(f"/sources/{world['ids']['s']}/", f"/sources/{fresh['id']}/")
    if (method, template) == ("POST", "/api/projects/{p}/searches/{q}/rerun"):
        # A fresh, unsubscribed search per call: the dedup guard refuses a second queued run.
        fresh_search = _fresh_search(world["ids"]["p"])
        path = path.replace(f"/searches/{world['ids']['q']}/", f"/searches/{fresh_search}/")
    if (method, template) == ("POST", "/api/projects/{p}/searches/{q}/alerts"):
        # A fresh search per call: a search can only have one alert subscription.
        fresh_search = _fresh_search(world["ids"]["p"])
        path = path.replace(f"/searches/{world['ids']['q']}/", f"/searches/{fresh_search}/")
    if (method, template) == ("POST", "/api/projects/{p}/seeds"):
        # A fresh DOI per call: the seed DOI is unique per project.
        body = {"title": "Another seed paper", "doi": f"10.1234/{uuid.uuid4().hex}"}
    if (method, template) == ("DELETE", "/api/projects/{p}/seeds/{d}"):
        # A fresh seed per call, so a success can't 404 the next principal.
        fresh = world["clients"]["owner"].post(f"/api/projects/{world['ids']['p']}/seeds", json={"title": "Removable seed", "doi": f"10.4321/{uuid.uuid4().hex}"}).json()
        path = path.replace(f"/seeds/{world['ids']['d']}", f"/seeds/{fresh['id']}")
    if (method, template) == ("POST", "/api/projects/{p}/members"):
        # A fresh, already-signed-in invitee per call: a project member can only be invited once,
        # and invite refuses an email with no account.
        invitee_email = f"matrix-invitee-{uuid.uuid4().hex[:8]}@example.com"
        _login(invitee_email)
        body = {"email": invitee_email, "role": "reviewer"}
    if (method, template) == ("DELETE", "/api/projects/{p}/members/{u}"):
        # A fresh removable member per call, so a success can't 404 the next principal.
        _, removable_id = _login(f"matrix-removable-{uuid.uuid4().hex[:8]}@example.com")
        with SessionLocal() as db:
            db.add(ProjectMember(project_id=uuid.UUID(world["ids"]["p"]), user_id=uuid.UUID(removable_id), role=ProjectRole.reviewer))
            db.commit()
        path = path.replace(f"/members/{world['ids']['u']}", f"/members/{removable_id}")
    if (method, template) == ("POST", "/api/projects/{p}/extraction-schemas"):
        # A fresh schema name per call: the schema name is unique per project.
        body = {"name": f"copied_schema_{uuid.uuid4().hex[:8]}", "based_on": "default"}
    if (method, template) == ("POST", "/api/projects/{p}/alerts/{a}/run"):
        # A fresh, just-subscribed alert per call: the dedup guard refuses a second queued run.
        owner = world["clients"]["owner"]
        fresh_search = _fresh_search(world["ids"]["p"])
        alert = owner.post(f"/api/projects/{world['ids']['p']}/searches/{fresh_search}/alerts", json={}).json()
        path = path.replace(f"/alerts/{world['ids']['a']}/", f"/alerts/{alert['alert_id']}/")
    if (method, template) == ("DELETE", "/api/projects/{p}/alerts/{a}"):
        # A fresh alert per call, so a success can't 404 the next principal.
        owner = world["clients"]["owner"]
        fresh_search = _fresh_search(world["ids"]["p"])
        alert = owner.post(f"/api/projects/{world['ids']['p']}/searches/{fresh_search}/alerts", json={}).json()
        path = path.replace(f"/alerts/{world['ids']['a']}", f"/alerts/{alert['alert_id']}")
    if (method, template) == ("POST", "/api/projects/{p}/theories"):
        # A fresh name per call: the theory name is unique per project.
        body = {"name": f"Social exchange {uuid.uuid4().hex[:8]}", "description": "A lens."}
    if (method, template) == ("POST", "/api/projects/{p}/constructs"):
        # A fresh name per call: the (name, author) pair is unique per project.
        body = {"name": f"Trust {uuid.uuid4().hex[:8]}", "definition": "Definition", "author": "Smith"}
    if (method, template) == ("POST", "/api/projects/{p}/gates/G1/approve"):
        # Reset G1 to pending before each call: the project has already moved past G1's stage, so
        # the reopen endpoint refuses (M0.5.10 rule); write the row directly instead.
        from app.models import Gate as _Gate, GateCode as _GateCode, GateStatus as _GateStatus
        with SessionLocal() as db:
            db.query(_Gate).filter(
                _Gate.project_id == uuid.UUID(world["ids"]["p"]), _Gate.code == _GateCode.G1,
            ).update({"status": _GateStatus.pending, "decided_by": None, "decided_at": None, "note": None})
            db.commit()
    if (method, template) == ("PUT", "/api/projects/{p}/screening/dual-screen"):
        body = {"enabled": True}
    if (method, template) == ("POST", "/api/projects/{p}/screening/decisions"):
        body = {"source_id": world["ids"]["s"], "decision": "maybe"}
    if (method, template) == ("POST", "/api/projects/{p}/screening/decisions/adjudicate"):
        # Needs dual-screen on and a source whose two reviewers disagree, none of them the caller; fresh per call.
        pid = uuid.UUID(world["ids"]["p"])
        fresh = world["clients"]["owner"].post(f"/api/projects/{world['ids']['p']}/sources", json={"title": "Adjudication target"}).json()
        with SessionLocal() as db:
            db.get(Project, pid).dual_screen_enabled = True
            for seq, (who, decision) in enumerate((("reviewer-a", "include"), ("reviewer-b", "maybe")), start=1):
                db.add(ScreeningDecision(project_id=pid, source_id=uuid.UUID(fresh["id"]), stage="title_abstract", seq=seq,
                                         decision=decision, decided_by="human", decider=who))
            db.commit()
        return world["clients"][principal].post(path, json={"source_id": fresh["id"], "decision": "include"})
    if (method, template) == ("POST", "/api/projects/{p}/documents/{doc}/versions"):
        # Each call saves into its own section, authored by the caller, so a success can't 409 the next principal.
        author = world["clients"][principal].get("/api/auth/me").json().get("id", "anonymous")
        fresh_doc = _seed_document(world["ids"]["p"], world["ids"]["author"])
        content = {"type": "doc", "content": [{"type": "paragraph", "content": [{
            "type": "text", "text": "Matrix save.",
            "marks": [{"type": "prov", "attrs": {"sid": f"m-{uuid.uuid4().hex}", "origin": "typed", "by": author, "sug": None, "note": None, "paste": None, "src": None}}],
        }]}]}
        path = path.replace(f"/documents/{world['ids']['doc']}/", f"/documents/{fresh_doc}/")
        return world["clients"][principal].post(path, json={"base_number": 1, "client_save_id": uuid.uuid4().hex, "content_json": content})
    if (method, template) == ("POST", "/api/projects/{p}/documents/{doc}/paragraph-from-note"):
        # Only the caller's own note filed to the project qualifies, so each principal gets one of its own.
        from app.models import Note, NoteKind, NoteStatus

        caller = world["clients"][principal].get("/api/auth/me").json().get("id") or world["ids"]["author"]
        with SessionLocal() as db:
            note = Note(owner_id=uuid.UUID(caller), project_id=uuid.UUID(world["ids"]["p"]), client_id=uuid.uuid4().hex,
                        kind=NoteKind.typed, body="Matrix note.", status=NoteStatus.filed, captured_at=utcnow())
            db.add(note)
            db.commit()
            note_id = str(note.id)
        return world["clients"][principal].post(path, json={"note_id": note_id})
    if (method, template) == ("POST", "/api/projects/{p}/documents/{doc}/versions/{v}/restore"):
        fresh_doc = _seed_document(world["ids"]["p"], world["ids"]["author"])
        path = path.replace(f"/documents/{world['ids']['doc']}/", f"/documents/{fresh_doc}/")
        return world["clients"][principal].post(path, json={"base_number": 1})
    if (method, template) == ("POST", "/api/projects/{p}/extractions"):
        body = {"source_id": world["ids"]["extract_s"], "fields": {"research_question": None}}
    if (method, template) == ("POST", "/api/projects/{p}/extractions/ai"):
        body = {"source_id": world["ids"]["ai_s"]}
    if (method, template) == ("POST", "/api/projects/{p}/screening/decisions/undo"):
        body = {"source_id": world["ids"]["s"]}
        # there must be a decision to take back; the owner records it, whoever is being tested
        owner_body = {"source_id": world["ids"]["s"], "decision": "maybe"}
        world["clients"]["owner"].post(f"/api/projects/{world['ids']['p']}/screening/decisions", json=owner_body)
    if (method, template) == ("POST", "/api/projects/{p}/screening/auto-exclude/audits/{k}/review"):
        # Reviewing an audit row is write-once (status leaves "pending"): give each principal under
        # test a fresh source + pending row so a successful call from one doesn't 409 the next.
        fresh = world["clients"]["owner"].post(
            f"/api/projects/{world['ids']['p']}/sources", json={"title": "Auto-exclude audit target"}
        ).json()
        with SessionLocal() as db:
            decision_id = uuid.uuid4()
            db.add(ScreeningDecision(id=decision_id, project_id=uuid.UUID(world["ids"]["p"]), source_id=uuid.UUID(fresh["id"]),
                                     stage="title_abstract", seq=1, decision="exclude", reason_code="E1", decided_by="ai",
                                     decider="agent:prescreen", confidence=0.95))
            db.commit()
            fresh_audit_id = uuid.uuid4()
            db.add(AutoExcludeAudit(id=fresh_audit_id, project_id=uuid.UUID(world["ids"]["p"]), source_id=uuid.UUID(fresh["id"]),
                                    stage="title_abstract", decision_id=decision_id, confidence=0.95))
            db.commit()
        path = path.replace(f"/audits/{world['ids']['k']}/review", f"/audits/{fresh_audit_id}/review")
        return world["clients"][principal].post(path, json={"outcome": "confirm"})
    if (method, template) == ("POST", "/api/projects/{p}/sources/{s}/fulltext/upload"):
        # Upload is write-once per source: give every principal under test a fresh, never-uploaded
        # source so a successful call from one principal doesn't 409 the next.
        fresh = world["clients"]["owner"].post(
            f"/api/projects/{world['ids']['p']}/sources", json={"title": "Upload target", "evidence_excerpt": "x"}
        ).json()
        path = path.replace(f"/sources/{world['ids']['s']}/", f"/sources/{fresh['id']}/")
        return world["clients"][principal].post(path, content=UPLOAD_PDF, headers={"content-type": "application/pdf"})
    if (method, template) == ("POST", "/api/projects/{p}/sources/{s}/fulltext/withdraw"):
        from app.config import get_settings as _get_settings
        from app.object_storage import fulltext_key, get_object_store

        fresh = world["clients"]["owner"].post(
            f"/api/projects/{world['ids']['p']}/sources", json={"title": "Fulltext withdrawal target"}
        ).json()
        stored = get_object_store(_get_settings()).put(
            fulltext_key(uuid.UUID(world["ids"]["p"]), uuid.UUID(fresh["id"]), "pdf"), UPLOAD_PDF
        )
        with SessionLocal() as db:
            source = db.get(Source, uuid.UUID(fresh["id"]))
            source.fulltext_path = stored.key
            source.fulltext_access = {"status": "stored", "via": "test"}
            db.commit()
        path = path.replace(f"/sources/{world['ids']['s']}/", f"/sources/{fresh['id']}/")
        return world["clients"][principal].post(path)
    if (method, template) == ("GET", "/api/projects/{p}/sources/{s}/fulltext"):
        from app.config import get_settings as _get_settings
        from app.object_storage import fulltext_key, get_object_store

        fresh = world["clients"]["owner"].post(
            f"/api/projects/{world['ids']['p']}/sources", json={"title": "Fulltext read target"}
        ).json()
        stored = get_object_store(_get_settings()).put(fulltext_key(uuid.UUID(world["ids"]["p"]), uuid.UUID(fresh["id"]), "pdf"), UPLOAD_PDF)
        with SessionLocal() as db:
            db.get(Source, uuid.UUID(fresh["id"])).fulltext_path = stored.key
            db.commit()
        path = path.replace(f"/sources/{world['ids']['s']}/", f"/sources/{fresh['id']}/")
        return world["clients"][principal].get(path)
    if (method, template) == ("POST", "/api/projects/{p}/sources/{s}/fulltext/arxiv/fetch"):
        fresh = world["clients"]["owner"].post(
            f"/api/projects/{world['ids']['p']}/sources", json={"title": "arXiv fetch target"}
        ).json()
        with SessionLocal() as db:
            source = db.get(Source, uuid.UUID(fresh["id"]))
            source.origin = SOURCE_ORIGIN_RETRIEVED
            source.source_ids = {"arxiv": "2101.00001"}
            db.commit()
        path = path.replace(f"/sources/{world['ids']['s']}/", f"/sources/{fresh['id']}/")
        return world["clients"][principal].post(path)
    if (method, template) == ("POST", "/api/projects/{p}/stage/advance"):
        # The matrix checks who may advance, not lock levels: supply reasons for the Recommended rows this
        # fixture leaves unmet (lock levels are covered in test_stage_condition_overrides.py). Reset the
        # stage before each call so one allowed principal's success can't 409 the next.
        with SessionLocal() as db:
            db.get(Project, uuid.UUID(world["ids"]["p"])).stage = ProjectStage.search_planned
            db.commit()
        response = world["clients"][principal].post(path, json={})
        detail = response.json().get("detail") if response.status_code == 409 else None
        locked = detail.get("recommended") if isinstance(detail, dict) else None
        if locked:
            reasons = {c["id"]: "Set up by this test; not what it checks." for c in locked}
            response = world["clients"][principal].post(path, json={"override_reasons": reasons})
        return response
    if "/suggestions/{g}" in template:
        # Each call gets its own suggestion in the state the route needs, so one principal's success can't change the next.
        start = {"restore": ["discarded"], "undo": ["accepted"]}.get(template.rsplit("/", 1)[1], [])
        fresh_id = _fresh_suggestion(world["ids"]["p"], start)
        path = path.replace(world["ids"]["g"], fresh_id)
    return world["clients"][principal].request(method, path, json=body)


@pytest.mark.parametrize("method,template,allowed,success", MATRIX, ids=[f"{m} {t}" for m, t, _, _ in MATRIX])
def test_role_matrix(world, method, template, allowed, success):
    failures = []
    for principal in PRINCIPALS:
        response = _call(world, principal, method, template)
        if principal == "anonymous":
            ok, want = response.status_code == 401, 401
        elif principal == "stranger":
            ok, want = response.status_code == 404, 404
        elif principal.removesuffix("_member") in allowed:
            ok, want = response.status_code == success, success
        else:
            ok, want = response.status_code == 403, 403
        if not ok:
            failures.append(f"{principal}: expected {want}, got {response.status_code}: {response.text}")
    assert not failures, "\n".join(failures)


def test_the_matrix_covers_every_project_scoped_route():
    """A new project route must be added to MATRIX, or this fails."""
    routed = {
            (method, route.path.replace("{project_id}", "{p}").replace("{source_id}", "{s}").replace("{user_id}", "{u}").replace("{code}", "G1").replace("{search_id}", "{q}").replace("{seed_id}", "{d}").replace("{extraction_id}", "{e}").replace("{evidence_id}", "{e}").replace("{plan_id}", "{plan_id}").replace("{claim_id}", "{h}").replace("{gap_id}", "{gap_id}").replace("{question_id}", "{rq}").replace("{hypothesis_id}", "{hp}").replace("{field_key}", "{field_key}").replace("{clustering_id}", "{c}").replace("{cluster_id}", "{l}").replace("{schema_name}", "{n}").replace("{alert_id}", "{a}").replace("{audit_id}", "{k}").replace("{suggestion_id}", "{g}").replace("{document_id}", "{doc}").replace("{number}", "{v}").replace("{target_kind}", "source").replace("{target_id}", "{s}").replace("{kind}", "co_citation").replace("{report_id}", "{rep}").replace("{path_id}", "{cp}").replace("{dataset_id}", "{ds}").replace("{analysis_plan_id}", "{ap}").replace("{run_id}", "{run}").replace("{member_id}", "{cr}"))
        for route in app.routes
        if "{project_id}" in getattr(route, "path", "")
        for method in route.methods
    }
    covered = {(method, template) for method, template, _, _ in MATRIX}

    assert routed == covered


def test_denied_calls_change_nothing(world):
    """A refused write must not have side effects (checked on the cheapest write routes)."""
    p = world["ids"]["p"]
    reviewer = world["clients"]["reviewer"]
    before = world["clients"]["owner"].get(f"/api/projects/{p}/sources").json()

    assert reviewer.post(f"/api/projects/{p}/sources", json={"title": "Sneaky"}).status_code == 403
    assert reviewer.post(f"/api/projects/{p}/sources/{world['ids']['s']}/verify").status_code == 403

    after = world["clients"]["owner"].get(f"/api/projects/{p}/sources").json()
    assert after == before and after[0]["metadata_verified"] is False
    actions = {e["action"] for e in world["clients"]["owner"].get(f"/api/projects/{p}/audit").json()}
    assert "source.verified" not in actions
