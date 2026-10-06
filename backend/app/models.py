import enum
import hashlib
import uuid
from datetime import datetime, timezone

from sqlalchemy import event, CheckConstraint, and_, or_, DateTime, Enum, ForeignKey, Index, JSON, String, Text, UniqueConstraint, false, func
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# Source types written for automatically retrieved evidence (ARC retrieval). Used to
# tell machine-found sources apart from ones a researcher entered.
# Where a source came from. System-set only (never read from a request): `retrieved` = a machine
# (ARC, a connector) put it there; `manual` = a person did. Verification does not change it.
SOURCE_ORIGIN_MANUAL = "manual"
SOURCE_ORIGIN_RETRIEVED = "retrieved"
ARC_SOURCE_TYPES = frozenset({"web_search", "scholar", "crawled_page", "pdf_extract"})


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class User(Timestamped, Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(200))
    projects: Mapped[list["Project"]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    external_identities: Mapped[list["ExternalIdentity"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class ExternalIdentity(Timestamped, Base):
    __tablename__ = "external_identities"
    __table_args__ = (UniqueConstraint("provider", "subject", name="uq_provider_subject"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(50))
    subject: Mapped[str] = mapped_column(String(255))
    user: Mapped[User] = relationship(back_populates="external_identities")


class ProjectStage(str, enum.Enum):
    """Workflow stage (spec Appendix C). Definition order IS the workflow order.

    `submitted` branches to `revision_loop` or `accepted`. Re-entry to an earlier stage is
    allowed from any state (downstream artifacts then become stale; see plan M0.5.5).
    """

    idea = "idea"
    scoped = "scoped"  # G1
    search_planned = "search_planned"  # G2
    retrieved = "retrieved"
    screened = "screened"  # G3
    extracted = "extracted"  # G4
    synthesized = "synthesized"
    gaps_selected = "gaps_selected"  # G5
    framework = "framework"  # G6
    design_approved = "design_approved"  # G7
    data_collected = "data_collected"
    plan_locked = "plan_locked"  # G8
    analyzed = "analyzed"  # G9
    drafted = "drafted"  # G10
    revised = "revised"
    submission_ready = "submission_ready"  # G11
    submitted = "submitted"
    revision_loop = "revision_loop"
    accepted = "accepted"


class Project(Timestamped, Base):
    __tablename__ = "projects"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="active")
    stage: Mapped[ProjectStage] = mapped_column(
        Enum(ProjectStage, name="project_stage"), default=ProjectStage.idea, server_default=ProjectStage.idea.value
    )
    # Names a shipped discipline profile (app/profiles/); `config_json` holds this project's validated overrides.
    discipline: Mapped[str | None] = mapped_column(String(60))
    config_json: Mapped[dict | None] = mapped_column(JSON)
    # Framework the screening criteria are organised by: pico, picoc, spider or custom (app/criteria.py). NULL = not chosen.
    criteria_framework: Mapped[str | None] = mapped_column(String(20))
    owner: Mapped[User] = relationship(back_populates="projects")
    context_items: Mapped[list["ResearchContextItem"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    sources: Mapped[list["Source"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    research_runs: Mapped[list["ResearchRun"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    members: Mapped[list["ProjectMember"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    gates: Mapped[list["Gate"]] = relationship(back_populates="project", cascade="all, delete-orphan")

    def touch(self) -> None:
        """Mark the project as recently changed (project lists sort by `updated_at`)."""
        self.updated_at = utcnow()


class ProjectRole(str, enum.Enum):
    owner = "owner"
    co_author = "co_author"
    supervisor = "supervisor"
    reviewer = "reviewer"


class ProjectMember(Timestamped, Base):
    """A user's role on a project. `Project.owner_id` also counts as owner (see `dependencies.project_role`)."""

    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_project_member"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[ProjectRole] = mapped_column(Enum(ProjectRole, name="project_role"))
    project: Mapped[Project] = relationship(back_populates="members")
    user: Mapped[User] = relationship()


class GateCode(str, enum.Enum):
    """Human approval gates (spec section 8). Definition order is workflow order."""

    G1 = "G1"  # after idea scoping: approve topic direction
    G2 = "G2"  # before bulk search: approve search strategy + criteria
    G3 = "G3"  # after screening: approve included set + audit sample
    G4 = "G4"  # after extraction: verify critical fields
    G5 = "G5"  # after gap analysis: select and justify gaps
    G6 = "G6"  # after RQ/framework: approve RQs, hypotheses, model
    G7 = "G7"  # before data collection: approve design, instrument, ethics
    G8 = "G8"  # before analysis: lock analysis plan
    G9 = "G9"  # after analysis: approve interpretation
    G10 = "G10"  # per manuscript section: approve text and AI-use level
    G11 = "G11"  # before submission: final author sign-off, declarations


class GateStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class Gate(Timestamped, Base):
    """One approval gate on one project. Every project has all eleven, starting `pending`.

    `decided_by` is the deciding user's id. Gates are decided by people only; the
    approve/reject endpoints (M0.5.3) refuse agents and service accounts.
    """

    __tablename__ = "gates"
    __table_args__ = (UniqueConstraint("project_id", "code", name="uq_project_gate"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    code: Mapped[GateCode] = mapped_column(Enum(GateCode, name="gate_code"))
    status: Mapped[GateStatus] = mapped_column(
        Enum(GateStatus, name="gate_status"), default=GateStatus.pending, server_default=GateStatus.pending.value
    )
    decided_by: Mapped[str | None] = mapped_column(String(100))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
    project: Mapped["Project"] = relationship(back_populates="gates")


class ContextKind(str, enum.Enum):
    question = "question"
    objective = "objective"
    hypothesis = "hypothesis"
    methodology = "methodology"
    variable = "variable"
    decision = "decision"
    idea = "idea"


class ResearchContextItem(Timestamped, Base):
    __tablename__ = "research_context_items"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    kind: Mapped[ContextKind] = mapped_column(Enum(ContextKind, name="context_kind"))
    content: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(100))
    project: Mapped[Project] = relationship(back_populates="context_items")


class Source(Timestamped, Base):
    __tablename__ = "sources"
    # `ingest_key` makes automated ingestion idempotent: a retried task derives the same key for the
    # same item, so it can't insert it twice. NULL for sources people add (NULLs never collide).
    __table_args__ = (Index("uq_source_ingest_key", "project_id", "ingest_key", unique=True),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(500))
    doi: Mapped[str | None] = mapped_column(String(255), index=True)
    url: Mapped[str | None] = mapped_column(String(2000))
    authors: Mapped[list | None] = mapped_column(JSON)
    year: Mapped[int | None]
    source_type: Mapped[str] = mapped_column(String(50), default="article")
    metadata_verified: Mapped[bool] = mapped_column(default=False)
    created_by: Mapped[str | None] = mapped_column(String(100))
    ingest_key: Mapped[str | None] = mapped_column(String(100))
    origin: Mapped[str] = mapped_column(String(20), default=SOURCE_ORIGIN_MANUAL, server_default=SOURCE_ORIGIN_MANUAL)
    # Toward the spec's `Paper` (section 4). All optional; connectors (M1.4) and uploads (M2.7) fill them.
    venue: Mapped[str | None] = mapped_column(String(500))  # journal / conference / repository
    abstract: Mapped[str | None] = mapped_column(Text)  # untrusted text when it came from a connector
    oa_url: Mapped[str | None] = mapped_column(String(2000))  # open-access location, if any
    source_ids: Mapped[dict | None] = mapped_column(JSON)  # external ids, e.g. {"openalex": "W123", "pmid": "1"}
    fulltext_path: Mapped[str | None] = mapped_column(String(1000))  # storage key (never an absolute path); set by the system only (M0.10.2)
    quality_flags: Mapped[list | None] = mapped_column(JSON)  # e.g. ["preprint", "no_abstract"]; set by the system only
    # Set when a duplicate was merged into another source (M1.6.4): the row is kept (ids in old run
    # snapshots, ingest keys) but hidden from lists and prompts. Plain id, not a foreign key.
    merged_into: Mapped[uuid.UUID | None] = mapped_column(index=True)
    # How `metadata_verified` came about (M1.10.2): "human" (a person confirmed it) or "automatic" (the
    # citation verifier matched title, authors and year). NULL on a verified row = a person, from before
    # this column existed. `verification` is the last automatic check (verdict + ids, no source text).
    verification_method: Mapped[str | None] = mapped_column(String(20))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by: Mapped[str | None] = mapped_column(String(100))
    verification: Mapped[dict | None] = mapped_column(JSON)
    project: Mapped[Project] = relationship(back_populates="sources")
    excerpts: Mapped[list["SourceExcerpt"]] = relationship(
        back_populates="source", cascade="all, delete-orphan", order_by="SourceExcerpt.created_at"
    )

    @hybrid_property
    def is_automated(self) -> bool:
        """Machine-retrieved and not yet reviewed by a person. Works in Python and in SQL.

        An automatic metadata check does not count as a person's review.
        """
        return self.origin == SOURCE_ORIGIN_RETRIEVED and not self.reviewed_by_person

    @is_automated.inplace.expression
    @classmethod
    def _is_automated_expression(cls):
        return and_(cls.origin == SOURCE_ORIGIN_RETRIEVED, or_(cls.metadata_verified.is_(False), cls.verification_method == "automatic"))

    @property
    def reviewed_by_person(self) -> bool:
        return bool(self.metadata_verified) and self.verification_method != "automatic"


class ScreeningCriterion(Timestamped, Base):
    """One inclusion or exclusion criterion of a project's review (spec 5.2 inputs, 5.3 screening).

    `code` (I1, E2, ...) is the stable reason code screening decisions and PRISMA counts will cite.
    """

    __tablename__ = "screening_criteria"
    __table_args__ = (UniqueConstraint("project_id", "code", name="uq_criterion_code"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # "include" | "exclude"
    code: Mapped[str] = mapped_column(String(10))
    text: Mapped[str] = mapped_column(Text)
    element: Mapped[str | None] = mapped_column(String(40))  # framework element, e.g. "population"
    position: Mapped[int] = mapped_column(default=0)
    created_by: Mapped[str | None] = mapped_column(String(100))


class ScreeningDecision(Base):
    """One screening decision about one source at one stage (spec 4 ScreeningDecision, 5.3). Append-only.

    A person's decision is final; an AI decision is only a suggestion (`decided_by="ai"`). Changing
    your mind appends a new row (`seq` counts up per source and stage) and the latest human row wins;
    `decision="undo"` puts the source back in the queue. Rows are never updated or deleted.
    """

    __tablename__ = "screening_decisions"
    __table_args__ = (UniqueConstraint("source_id", "stage", "seq", name="uq_screening_seq"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), index=True)
    stage: Mapped[str] = mapped_column(String(20))  # "title_abstract" | "full_text"
    seq: Mapped[int]
    decision: Mapped[str] = mapped_column(String(10))  # "include" | "exclude" | "maybe" | "undo"
    reason_code: Mapped[str | None] = mapped_column(String(10))  # a criterion code of the project (I1, E2, ...)
    decided_by: Mapped[str] = mapped_column(String(10))  # "human" | "ai"
    decider: Mapped[str] = mapped_column(String(100))  # user id, or `agent:<name>`
    confidence: Mapped[float | None]
    note: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


@event.listens_for(ScreeningDecision, "before_update")
@event.listens_for(ScreeningDecision, "before_delete")
def _reject_screening_change(mapper, connection, target) -> None:
    raise ValueError("screening_decisions is append-only: rows cannot be updated or deleted")


class SeedPaper(Timestamped, Base):
    """A paper the scholar already knows belongs in the review (spec 5.2 inputs; known-item test, M1.8.2).

    A search that does not find its project's seed papers is flagged as a possible recall problem.
    """

    __tablename__ = "seed_papers"
    __table_args__ = (Index("uq_seed_paper_doi", "project_id", "doi", unique=True),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(500))
    doi: Mapped[str | None] = mapped_column(String(255))
    authors: Mapped[list | None] = mapped_column(JSON)
    year: Mapped[int | None]
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(100))


class SearchQuery(Timestamped, Base):
    """One run of a database search, kept so it can be reported and re-run (spec 5.2.5, 4 SearchQuery).

    Rows sharing a `search_id` are versions of the same search; `version` counts up from 1.
    `run_at` and `n_results` stay NULL until the search has actually run.
    """

    __tablename__ = "search_queries"
    __table_args__ = (
        UniqueConstraint("search_id", "version", name="uq_search_query_version"),
        CheckConstraint("version >= 1", name="ck_search_query_version_positive"),
        CheckConstraint("n_results IS NULL OR n_results >= 0", name="ck_search_query_n_results"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    search_id: Mapped[uuid.UUID] = mapped_column(index=True, default=uuid.uuid4)
    database: Mapped[str] = mapped_column(String(50))
    query_string: Mapped[str] = mapped_column(Text)
    filters: Mapped[dict | None] = mapped_column(JSON)
    run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    n_results: Mapped[int | None] = mapped_column()
    version: Mapped[int] = mapped_column(default=1)
    exact: Mapped[bool | None] = mapped_column()  # did the database apply the query's logic as written?
    caveats: Mapped[list | None] = mapped_column(JSON)  # plain-language reasons it might not have
    counts: Mapped[dict | None] = mapped_column(JSON)  # retrieved / unique / duplicates_removed / ...
    results: Mapped[list | None] = mapped_column(JSON)  # one entry per unique record: id, doi, work key
    created_by: Mapped[str | None] = mapped_column(String(100))


class SourceExcerpt(Timestamped, Base):
    """An auditable, immutable note or quotation supplied for a source."""

    __tablename__ = "source_excerpts"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), index=True)
    content: Mapped[str] = mapped_column(Text)
    locator: Mapped[str | None] = mapped_column(String(255))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_by: Mapped[str | None] = mapped_column(String(100))
    source: Mapped[Source] = relationship(back_populates="excerpts")


def excerpt_hash(content: str) -> str:
    """The `SourceExcerpt.content_hash` for `content`."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class ResearchRunStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    needs_sources = "needs_sources"
    needs_configuration = "needs_configuration"
    failed = "failed"


class ResearchRun(Timestamped, Base):
    """A durable record of one agent request and the exact evidence it was given."""

    __tablename__ = "research_runs"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    question: Mapped[str] = mapped_column(Text)
    status: Mapped[ResearchRunStatus] = mapped_column(
        Enum(ResearchRunStatus, name="research_run_status"), default=ResearchRunStatus.queued, index=True
    )
    research_plan: Mapped[list | None] = mapped_column(JSON)
    input_snapshot: Mapped[dict | None] = mapped_column(JSON)
    answer: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    provider_model: Mapped[str | None] = mapped_column(String(255))
    prompt_version: Mapped[str | None] = mapped_column(String(100))  # e.g. evidence_synthesis@1
    use_web_retrieval: Mapped[bool] = mapped_column(default=False, server_default=false())
    created_by: Mapped[str | None] = mapped_column(String(100))
    attempt_count: Mapped[int] = mapped_column(default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    project: Mapped[Project] = relationship(back_populates="research_runs")


class TaskStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    blocked = "blocked"  # waiting for `blocked_by_gate` to be approved by a person
    paused = "paused"


class Task(Timestamped, Base):
    """A unit of background work for the general task queue (replaces the single-purpose run queue).

    Only the table exists so far: claiming with leases/heartbeats is M0.6.2, the type
    registry and porting `ResearchRun` onto it is M0.6.3, idempotency keys M0.6.4.
    A `blocked` task always names the gate it waits for; nothing but a human gate
    approval may release it (plan rule 21).
    """

    __tablename__ = "tasks"
    __table_args__ = (
        # Enqueueing the same key twice in a project returns the existing task instead of a second one.
        Index("uq_task_idempotency", "project_id", "idempotency_key", unique=True),
        CheckConstraint("max_attempts >= 1", name="ck_tasks_max_attempts_positive"),
        CheckConstraint("status != 'blocked' OR blocked_by_gate IS NOT NULL", name="ck_tasks_blocked_names_gate"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    type: Mapped[str] = mapped_column(String(100), index=True)
    payload: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, name="task_status"), default=TaskStatus.queued, server_default=TaskStatus.queued.value, index=True
    )
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(default=3, server_default="3")
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    run_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # retry backoff: not claimable before this
    blocked_by_gate: Mapped[GateCode | None] = mapped_column(Enum(GateCode, name="gate_code"))
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    error: Mapped[str | None] = mapped_column(Text)


class ArtifactStatus(str, enum.Enum):
    current = "current"
    stale = "stale"  # produced from work the scholar has since gone back on; must be re-run


class Artifact(Timestamped, Base):
    """A produced result (a synthesis run, a search, a screening set, ...) tied to the stage that made it.

    `kind` + `ref_id` identify the underlying record (`ref_id` is "" for one-per-project artifacts).
    When the scholar re-enters an earlier stage, every artifact from a later stage, and everything
    that depends on one through `ArtifactEdge`, becomes `stale`.
    """

    __tablename__ = "artifacts"
    __table_args__ = (UniqueConstraint("project_id", "kind", "ref_id", name="uq_artifact_ref"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(60))
    ref_id: Mapped[str] = mapped_column(String(64), default="", server_default="")
    stage: Mapped[ProjectStage] = mapped_column(Enum(ProjectStage, name="project_stage"))  # stage that produced it
    status: Mapped[ArtifactStatus] = mapped_column(
        Enum(ArtifactStatus, name="artifact_status"), default=ArtifactStatus.current, server_default=ArtifactStatus.current.value
    )
    stale_reason: Mapped[str | None] = mapped_column(Text)
    stale_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(100))


class ArtifactEdge(Base):
    """`downstream` was built from `upstream`: if upstream goes stale, so does downstream."""

    __tablename__ = "artifact_edges"
    __table_args__ = (
        UniqueConstraint("upstream_id", "downstream_id", name="uq_artifact_edge"),
        CheckConstraint("upstream_id != downstream_id", name="ck_artifact_edge_not_self"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    upstream_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("artifacts.id"), index=True)
    downstream_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("artifacts.id"), index=True)


class AuditEvent(Base):
    """One append-only record of who did what. Rows are never updated or deleted (enforced by `audit_guard`).

    `actor` is a user id or `agent:<name>`. It is a plain string, not a foreign key, so
    history survives removing a user or project and can name non-user actors.
    """

    __tablename__ = "audit_events"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    actor: Mapped[str] = mapped_column(String(100), index=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    payload_json: Mapped[dict | None] = mapped_column(JSON)
    model_id: Mapped[str | None] = mapped_column(String(255))
    prompt_version: Mapped[str | None] = mapped_column(String(100))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


# Registers the immutable-excerpt and append-only audit guards (ORM events + DB trigger DDL). Keep at the bottom.
from app import excerpt_guard  # noqa: E402,F401
from app import audit_guard  # noqa: E402,F401
