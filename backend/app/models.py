import enum
import hashlib
import uuid
from datetime import datetime, timezone

from sqlalchemy import event, select, CheckConstraint, ForeignKeyConstraint, and_, or_, DateTime, Enum, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, false, func
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
    # Bumped by "sign out everywhere"; a browser session carries the epoch it was issued at (X.4.3).
    session_epoch: Mapped[int] = mapped_column(default=0, server_default="0")
    # Set when the person's account was pseudonymised (X.4.7.1): email/name blanked, identities removed, row and id
    # kept so audit history stays attributable. An erased user can never sign in again.
    erased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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


class AiInvolvement(str, enum.Enum):
    """How far AI may take part in a project (X.31.7, Q14/Q27). Stored per project.

    `off`: no project text is sent to any AI. `suggestions_only`: AI may comment, challenge, find evidence and
    check structure, but never supplies manuscript words. `full`: also reworded sentences the researcher may
    choose to insert, always labelled in the writing record.
    """

    off = "off"
    suggestions_only = "suggestions_only"
    full = "full"


class AiModelRoute(str, enum.Enum):
    """Which model endpoint a project's AI calls use: the deployment's default, or only the local/self-hosted one."""

    standard = "standard"
    local = "local"


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
    stage: Mapped[ProjectStage] = mapped_column(
        Enum(ProjectStage, name="project_stage"), default=ProjectStage.idea, server_default=ProjectStage.idea.value
    )
    # Names a shipped discipline profile (app/profiles/); `config_json` holds this project's validated overrides.
    discipline: Mapped[str | None] = mapped_column(String(60))
    config_json: Mapped[dict | None] = mapped_column(JSON)
    # Framework the screening criteria are organised by: pico, picoc, spider or custom (app/criteria.py). NULL = not chosen.
    criteria_framework: Mapped[str | None] = mapped_column(String(20))
    # Owner-set override of settings.project_token_budget for this project only (M0.9.3); NULL = use the
    # global default. 0 means "no limit" here too, same as the global setting.
    token_budget_override: Mapped[int | None] = mapped_column()
    # Dual-screen mode (M2.4.1, spec 5.3.3): off by default. When on, screening at both stages needs
    # two independent human deciders (blinded from each other's answer and from the AI suggestion
    # until they submit their own) before a decision counts; disagreement is a conflict, not a decision.
    dual_screen_enabled: Mapped[bool] = mapped_column(default=False, server_default=false())
    # AI auto-exclude (M2.5.1, spec 5.3 human gate): off by default. When on, a title/abstract AI
    # exclude at or above the confidence threshold counts as the final decision without a person
    # deciding that record, as long as it is trusted or confirmed by the sampling audit
    # (`AutoExcludeAudit`); NULL threshold uses `settings.screening_ai_autoexclude_confidence_threshold`.
    ai_autoexclude_enabled: Mapped[bool] = mapped_column(default=False, server_default=false())
    ai_autoexclude_confidence_threshold: Mapped[float | None] = mapped_column()
    # AI pre-screen quality guard (M2.5.2, spec 5.3 quality checks): set automatically when
    # AI-human agreement falls below threshold (`app.ai_quality.should_disable_prescreen`), or
    # manually by the owner; `POST .../screening/prescreen` refuses while this is true.
    ai_prescreen_disabled: Mapped[bool] = mapped_column(default=False, server_default=false())
    ai_prescreen_disabled_reason: Mapped[str | None] = mapped_column(Text)
    # Active-learning prioritisation (M2.6, spec 5.3.2): off by default. When on, the screening queue
    # (`GET .../screening/queue?sort=relevance`) can rank unscreened records by a classifier trained
    # fresh, each request, on the project's current human decisions (`app.active_learning`).
    active_learning_enabled: Mapped[bool] = mapped_column(default=False, server_default=false())
    # Project-wide AI controls (X.31.7). Default Suggestions only for new and existing projects (Q14).
    ai_involvement: Mapped[AiInvolvement] = mapped_column(
        Enum(AiInvolvement, name="ai_involvement"), default=AiInvolvement.suggestions_only, server_default=AiInvolvement.suggestions_only.value
    )
    ai_model_route: Mapped[AiModelRoute] = mapped_column(
        Enum(AiModelRoute, name="ai_model_route"), default=AiModelRoute.standard, server_default=AiModelRoute.standard.value
    )
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
    # Promotion targets from the inbox (X.31.2). Never sent to a research run (agent/context.py).
    gap = "gap"
    claim = "claim"
    memo = "memo"


class ResearchContextItem(Timestamped, Base):
    __tablename__ = "research_context_items"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    kind: Mapped[ContextKind] = mapped_column(Enum(ContextKind, name="context_kind"))
    content: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(100))
    # The note this item was promoted from (X.31.2). Read-only link: the note's text is never copied into `content`.
    promoted_from_note_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("notes.id"), index=True)
    # A decision points at the item it concerns (X.31.2). Same project only; enforced by the promote route.
    related_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("research_context_items.id"), index=True)
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
    bibliometric_reference_ids: Mapped[list | None] = mapped_column(JSON)  # explicitly supplied cited-work IDs
    bibliometric_keywords: Mapped[list | None] = mapped_column(JSON)  # explicitly supplied author keywords
    fulltext_path: Mapped[str | None] = mapped_column(String(1000))  # storage key (never an absolute path); set by the system only (M0.10.2)
    # Last open-access full-text check (M2.7.1, `app/oa_fetch.py`): status, licence as reported, links, sha256. System-set only.
    fulltext_access: Mapped[dict | None] = mapped_column(JSON)
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
    # How an automated method found this source (M1.9.2), e.g. {"method": "snowball", "direction":
    # "backward", "round": 1, "via": {"title": ...}}. System-set only; NULL for everything else.
    found_via: Mapped[dict | None] = mapped_column(JSON)
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


class AutoExcludeAudit(Base):
    """The human sampling audit for one AI auto-exclude (spec 5.3 human gate, plan M2.5.1).

    Every `screening_ai_autoexclude_sample_rate`-th eligible AI exclude gets one of these rows,
    `status="pending"`, when the suggestion is written (`app.prescreen.run_prescreen`). Until a
    person reviews it (`confirmed` or `overturned`), that one exclude does not count, even though
    other, unsampled excludes at the same confidence already do. Never updated except by the single
    review call that sets `status`/`reviewed_by`/`reviewed_at` once. The review endpoint does a
    conditional `UPDATE ... WHERE status='pending'` and returns 409 when the rowcount is not 1, so two
    concurrent reviews cannot both succeed. The `before_update` ORM guard below refuses a second review
    on the ORM flush path. The DB CHECK only requires the review fields to be set together, so it does
    not stop a second review.
    """

    __tablename__ = "auto_exclude_audits"
    __table_args__ = (
        UniqueConstraint("decision_id", name="uq_auto_exclude_audit_decision"),
        CheckConstraint("status in ('pending','confirmed','overturned')", name="ck_auto_exclude_audit_status"),
        CheckConstraint(
            "(status = 'pending' and reviewed_by is null and reviewed_at is null) or "
            "(status != 'pending' and reviewed_by is not null and reviewed_at is not null)",
            name="ck_auto_exclude_audit_review_fields",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), index=True)
    stage: Mapped[str] = mapped_column(String(20))
    decision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("screening_decisions.id"))
    confidence: Mapped[float]
    status: Mapped[str] = mapped_column(String(10), default="pending", server_default="pending")
    reviewed_by: Mapped[str | None] = mapped_column(String(100))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


@event.listens_for(AutoExcludeAudit, "before_update")
def _guard_auto_exclude_audit_single_review(mapper, connection, target) -> None:
    current = connection.execute(select(AutoExcludeAudit.__table__.c.status).where(AutoExcludeAudit.__table__.c.id == target.id)).scalar()
    if current != "pending":
        raise ValueError("auto_exclude_audits: a reviewed row cannot be reviewed again")


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


class SeedCheckRun(Base):
    """One seed-based false-exclusion check (plan M2.9.1): the AI pre-screen's answer for each seed that
    matches a stored source, with the model, prompt and settings used. A record of what the model said,
    never a screening decision: nothing here counts toward PRISMA or the queue. Rows are never changed.
    """

    __tablename__ = "seed_check_runs"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    created_by: Mapped[str] = mapped_column(String(100))
    model_id: Mapped[str] = mapped_column(String(255))
    prompt_version: Mapped[str] = mapped_column(String(100))
    min_confidence: Mapped[float]
    autoexclude_threshold: Mapped[float]
    summary: Mapped[dict] = mapped_column(JSON)
    entries: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


@event.listens_for(SeedCheckRun, "before_update")
@event.listens_for(SeedCheckRun, "before_delete")
def _reject_seed_check_change(mapper, connection, target) -> None:
    raise ValueError("seed_check_runs is append-only: rows cannot be updated or deleted")


class ProjectExtractionSchema(Timestamped, Base):
    """One version of a scholar-edited extraction schema owned by a project (spec 5.4.1; plan M3.1.2).

    It starts as a copy of a built-in schema (`based_on`, e.g. `default@1`) or of another project schema.
    Rows are never changed: an edit adds the next `version`, so extractions recording
    `project:<name>@<version>` keep pointing at the exact fields they were checked against.
    """

    __tablename__ = "project_extraction_schemas"
    __table_args__ = (
        UniqueConstraint("project_id", "name", "version", name="uq_project_extraction_schema_version"),
        CheckConstraint("version >= 1", name="ck_project_extraction_schema_version_positive"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    version: Mapped[int]
    label: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    fields: Mapped[list] = mapped_column(JSON)
    based_on: Mapped[str] = mapped_column(String(80))
    created_by: Mapped[str] = mapped_column(String(100))


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


class SearchAlert(Timestamped, Base):
    """An alert subscription on a saved search (spec 5.2.4, plan M1.12): re-run it on a schedule,
    surface new papers.

    One alert per saved search. The worker sweeps `next_run_at` and enqueues a G2-gated
    `query_alert` task per due slot (idempotent per slot); a successful run records `last_run_at`
    / `last_run_version` / `last_new`. A paper that appeared in any earlier version of the search
    is never re-surfaced, even if it left and came back.
    """

    __tablename__ = "search_alerts"
    __table_args__ = (
        UniqueConstraint("search_id", name="uq_search_alert_search"),
        CheckConstraint("interval_seconds >= 3600", name="ck_search_alert_interval_min"),
        CheckConstraint("interval_seconds <= 2592000", name="ck_search_alert_interval_max"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    search_id: Mapped[uuid.UUID] = mapped_column(index=True)
    interval_seconds: Mapped[int] = mapped_column(default=86400)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_version: Mapped[int | None] = mapped_column()
    last_new: Mapped[int | None] = mapped_column()
    enabled: Mapped[bool] = mapped_column(default=True)
    created_by: Mapped[str | None] = mapped_column(String(100))


class SnowballRun(Timestamped, Base):
    """One completed citation-chasing run (spec 5.2.3, M1.9): where it started, how far it went, what it found.

    Saved only when the run finished; a failed run leaves an audit event and no row (plan rule 22).
    Sources it added carry `found_via.run_id` pointing here.
    """

    __tablename__ = "snowball_runs"
    __table_args__ = (CheckConstraint("rounds >= 1", name="ck_snowball_run_rounds"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    connector: Mapped[str] = mapped_column(String(50))
    directions: Mapped[list] = mapped_column(JSON)  # ["backward"] / ["forward"] / both
    rounds: Mapped[int] = mapped_column()
    caps: Mapped[dict] = mapped_column(JSON)  # max_per_paper / max_new
    starts: Mapped[list] = mapped_column(JSON)  # the papers it started from, resolved or not
    counts: Mapped[dict] = mapped_column(JSON)  # per round + totals; `capped` / truncated papers are recorded
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
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


class Extraction(Timestamped, Base):
    """Data pulled out of one paper by one extraction schema (spec 4 Extraction, 5.4; plan M3.2).

    `fields_json` maps field key -> value (None = not in the paper) and `evidence_spans` maps field
    key -> {quote, page, section}; `app.extraction_schema.check_extraction` keeps the two consistent.
    `schema_version` is `name@version` of the schema that produced it. `verified_by_human` is set only
    by the verification step (M3.4/M3.6), never on create or edit, and needs who and when (DB check).
    `model_id`/`prompt_version` are filled for AI extractions (M3.3) and NULL for a person's.
    """

    __tablename__ = "extractions"
    __table_args__ = (
        UniqueConstraint("task_id", name="uq_extraction_task"),
        CheckConstraint(
            "verified_by_human = false OR (verified_by IS NOT NULL AND verified_at IS NOT NULL)",
            name="ck_extraction_verified_has_verifier",
        ),
        CheckConstraint("extracted_by IN ('human', 'ai')", name="ck_extraction_extracted_by"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), index=True)  # the spec's `paper_id`
    schema_version: Mapped[str] = mapped_column(String(80))
    fields_json: Mapped[dict] = mapped_column(JSON)
    evidence_spans: Mapped[dict] = mapped_column(JSON)
    confidence_json: Mapped[dict | None] = mapped_column(JSON)  # per-field confidence for AI extractions; NULL for human entries
    agreement_json: Mapped[dict | None] = mapped_column(JSON)  # second sampled extraction and field agreement; NULL when unsampled
    verified_by_human: Mapped[bool] = mapped_column(default=False, server_default=false())
    verified_by: Mapped[str | None] = mapped_column(String(100))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extracted_by: Mapped[str] = mapped_column(String(10))  # "human" | "ai"
    extractor: Mapped[str] = mapped_column(String(100))  # user id, or `agent:<name>`
    model_id: Mapped[str | None] = mapped_column(String(200))
    prompt_version: Mapped[str | None] = mapped_column(String(100))


class MetaAnalysisPlan(Timestamped, Base):
    """A scholar's selected comparable correlations, frozen by G8 before pooling."""

    __tablename__ = "meta_analysis_plans"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    outcome: Mapped[str] = mapped_column(String(200))
    primary_model: Mapped[str] = mapped_column(String(10))
    studies_json: Mapped[list] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(String(100))
    result_json: Mapped[dict | None] = mapped_column(JSON)


class Construct(Timestamped, Base):
    """A named construct with author-specific definition and measurement notes (M3.7)."""

    __tablename__ = "constructs"
    __table_args__ = (
        UniqueConstraint("project_id", "name", "author", name="uq_construct_project_name_author"),
        UniqueConstraint("id", "project_id", name="uq_construct_id_project"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    definition: Mapped[str] = mapped_column(Text)
    measures: Mapped[list] = mapped_column(JSON, default=list)
    author: Mapped[str] = mapped_column(String(200))
    jingle_jangle_flags: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(100))


class Theory(Timestamped, Base):
    """A theory/lens linked to a project's construct inventory (M3.7)."""

    __tablename__ = "theories"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_theory_project_name"),
        UniqueConstraint("id", "project_id", name="uq_theory_id_project"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(100))
    construct_links: Mapped[list["TheoryConstruct"]] = relationship(back_populates="theory", cascade="all, delete-orphan")

    @property
    def construct_ids(self) -> list[uuid.UUID]:
        return [link.construct_id for link in self.construct_links]


class TheoryConstruct(Base):
    """A project-scoped theory-to-construct link; composite FKs prevent cross-project links."""

    __tablename__ = "theory_constructs"
    __table_args__ = (
        UniqueConstraint("theory_id", "construct_id", name="uq_theory_construct"),
        ForeignKeyConstraint(["theory_id", "project_id"], ["theories.id", "theories.project_id"]),
        ForeignKeyConstraint(["construct_id", "project_id"], ["constructs.id", "constructs.project_id"]),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(index=True)
    theory_id: Mapped[uuid.UUID] = mapped_column(index=True)
    construct_id: Mapped[uuid.UUID] = mapped_column(index=True)
    theory: Mapped[Theory] = relationship(back_populates="construct_links")


class Claim(Timestamped, Base):
    """A proposed synthesis statement; only a person may verify linked evidence."""

    __tablename__ = "claims"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    extraction_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("extractions.id"), index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("research_runs.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(default=False, server_default=false())
    strength: Mapped[str | None] = mapped_column(String(30))
    section_id: Mapped[uuid.UUID | None] = mapped_column()
    created_by: Mapped[str] = mapped_column(String(100))
    evidence: Mapped[list["ClaimEvidence"]] = relationship(back_populates="claim", cascade="all, delete-orphan")


class ClaimEvidence(Base):
    __tablename__ = "claim_evidence"
    __table_args__ = (
        UniqueConstraint("claim_id", "source_id", name="uq_claim_evidence_source"),
        CheckConstraint(
            "(support_confirmed_by IS NULL) = (support_confirmed_at IS NULL)",
            name="ck_claim_evidence_support_confirmation_paired",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id"), index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), index=True)
    excerpt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_excerpts.id"), index=True)
    quote: Mapped[str] = mapped_column(Text)
    quote_matches_source: Mapped[bool] = mapped_column(default=False, server_default=false())
    support_confirmed_by: Mapped[str | None] = mapped_column(String(100))
    support_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim: Mapped[Claim] = relationship(back_populates="evidence")


class Gap(Timestamped, Base):
    """A scholar-curated literature gap with auditable supporting evidence (M3.9)."""

    __tablename__ = "gaps"
    __table_args__ = (UniqueConstraint("project_id", "statement", name="uq_gap_project_statement"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    type: Mapped[str] = mapped_column(String(30))
    statement: Mapped[str] = mapped_column(Text)
    supporting_paper_ids: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    matrix_cell_ids: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    future_research: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    priority: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    selected: Mapped[bool] = mapped_column(default=False, server_default=false())
    created_by: Mapped[str] = mapped_column(String(100))


class ResearchQuestion(Timestamped, Base):
    """A human-owned draft question, linked to the curated gaps it addresses (M4.1)."""

    __tablename__ = "research_questions"
    __table_args__ = (
        UniqueConstraint("project_id", "text", name="uq_research_question_project_text"),
        UniqueConstraint("id", "project_id", name="uq_research_question_id_project"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(30))
    created_by: Mapped[str] = mapped_column(String(100))
    gap_links: Mapped[list["ResearchQuestionGap"]] = relationship(back_populates="research_question", cascade="all, delete-orphan")
    hypotheses: Mapped[list["Hypothesis"]] = relationship(back_populates="research_question", cascade="all, delete-orphan")


class ResearchQuestionGap(Base):
    """Project-scoped link that makes a question's gap provenance auditable."""

    __tablename__ = "research_question_gaps"
    __table_args__ = (
        UniqueConstraint("research_question_id", "gap_id", name="uq_research_question_gap"),
        ForeignKeyConstraint(["research_question_id", "project_id"], ["research_questions.id", "research_questions.project_id"]),
        ForeignKeyConstraint(["gap_id"], ["gaps.id"]),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(index=True)
    research_question_id: Mapped[uuid.UUID] = mapped_column(index=True)
    gap_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("gaps.id"), index=True)
    research_question: Mapped[ResearchQuestion] = relationship(back_populates="gap_links")


class Hypothesis(Timestamped, Base):
    """A human-owned hypothesis with explicitly recorded theoretical and empirical bases."""

    __tablename__ = "hypotheses"
    __table_args__ = (
        UniqueConstraint("project_id", "statement", name="uq_hypothesis_project_statement"),
        ForeignKeyConstraint(["research_question_id", "project_id"], ["research_questions.id", "research_questions.project_id"]),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(index=True)
    research_question_id: Mapped[uuid.UUID] = mapped_column(index=True)
    statement: Mapped[str] = mapped_column(Text)
    direction: Mapped[str] = mapped_column(String(30))
    theoretical_basis: Mapped[str] = mapped_column(Text)
    empirical_support: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(100))
    research_question: Mapped[ResearchQuestion] = relationship(back_populates="hypotheses")


class NoteKind(str, enum.Enum):
    """How a note was captured (X.31.1). `voice`, `highlight` and `photo` are defined for the data
    model's sake but not yet accepted by the API: they need attachment storage (M0.10.2, X.31.17/.3/.18)."""

    typed = "typed"
    voice = "voice"
    clip = "clip"
    highlight = "highlight"
    photo = "photo"


class NoteStatus(str, enum.Enum):
    inbox = "inbox"
    filed = "filed"
    archived = "archived"


class Note(Timestamped, Base):
    """A researcher's quick capture: one tap/keystroke, no required project or tag (spec 5.1, X.31.1).

    `body` is always the researcher's own words; text clipped or highlighted from elsewhere lives in
    `quoted_text` and is never mixed into `body`. `client_id` makes a retried capture from an offline
    device idempotent: the same (owner, client_id) always resolves to the same note.
    """

    __tablename__ = "notes"
    __table_args__ = (UniqueConstraint("owner_id", "client_id", name="uq_note_owner_client"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"), index=True)
    client_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[NoteKind] = mapped_column(Enum(NoteKind, name="note_kind"))
    body: Mapped[str] = mapped_column(Text, default="")
    quoted_text: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(2000))
    locator: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[NoteStatus] = mapped_column(Enum(NoteStatus, name="note_status"), default=NoteStatus.inbox, server_default=NoteStatus.inbox.value)
    ai_locked: Mapped[bool] = mapped_column(default=False, server_default=false())
    revision: Mapped[int] = mapped_column(default=1, server_default="1")
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    device: Mapped[str | None] = mapped_column(String(40))
    revisions: Mapped[list["NoteRevision"]] = relationship(back_populates="note", cascade="all, delete-orphan", order_by="NoteRevision.revision")


class NoteRevision(Base):
    """One version of a note's text. Append-only (enforced by `note_guard`): editing writes a new row
    and bumps `Note.revision`/`Note.body`; nothing already written is changed or removed."""

    __tablename__ = "note_revisions"
    __table_args__ = (UniqueConstraint("note_id", "revision", name="uq_note_revision"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    note_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("notes.id"), index=True)
    revision: Mapped[int]
    body: Mapped[str] = mapped_column(Text)
    quoted_text: Mapped[str | None] = mapped_column(Text)
    edited_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    note: Mapped[Note] = relationship(back_populates="revisions")


class AiSuggestion(Base):
    """One thing an AI proposed for one target (X.46.1). It is never the researcher's own text and is not
    edited after it is written: what a person did with it lives in the append-only `AiSuggestionEvent` list,
    and its state is computed from those events (`app/suggestions.py`). `parent_id` links an "ask again"
    answer to the suggestion it replaces."""

    __tablename__ = "ai_suggestions"
    __table_args__ = (Index("ix_ai_suggestions_target", "project_id", "target_kind", "target_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    target_kind: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[str] = mapped_column(String(64))
    target_field: Mapped[str | None] = mapped_column(String(60))
    content: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    evidence_json: Mapped[list | None] = mapped_column(JSON)
    model_id: Mapped[str] = mapped_column(String(255))
    prompt_version: Mapped[str] = mapped_column(String(100))
    producing_agent: Mapped[str] = mapped_column(String(60))
    guidance: Mapped[str | None] = mapped_column(Text)
    # Workspace side panel (X.31.11): which action made it, and what it was given (a hash and a size, never the text).
    action: Mapped[str | None] = mapped_column(String(40))
    input_hash: Mapped[str | None] = mapped_column(String(64))
    input_chars: Mapped[int | None]
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ai_suggestions.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    events: Mapped[list["AiSuggestionEvent"]] = relationship(order_by="AiSuggestionEvent.seq")


class AiSuggestionEvent(Base):
    """What a person did with a suggestion. Append-only (enforced by `suggestion_guard`). `final_text` is set
    only on `edited_and_accepted` (the AI text stays on the suggestion, so both are kept); `result_ref`
    names the target version an accept or undo wrote, when the target kind writes one."""

    __tablename__ = "ai_suggestion_events"
    __table_args__ = (UniqueConstraint("suggestion_id", "seq", name="uq_ai_suggestion_event_seq"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    suggestion_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_suggestions.id"), index=True)
    seq: Mapped[int]
    kind: Mapped[str] = mapped_column(String(30))
    actor: Mapped[str] = mapped_column(String(100))
    final_text: Mapped[str | None] = mapped_column(Text)
    result_ref: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AiItemLock(Base):
    """"Never for this item" (X.46.3): while a row exists, no AI step may be given this item (project, target kind, target id).
    Setting and clearing are audited, so the history of the lock lives in the audit log; the row itself is only the current state.
    A note's lock is `Note.ai_locked`, read through the same check (`app/ai_item_locks.py`)."""

    __tablename__ = "ai_item_locks"
    __table_args__ = (UniqueConstraint("project_id", "target_kind", "target_id", name="uq_ai_item_lock"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    target_kind: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[str] = mapped_column(String(100))
    locked_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Document(Timestamped, Base):
    """One manuscript section (X.31.8). The text is never stored here: every save writes a new `DocumentVersion`,
    and `current_version_id` only points at the latest one. One manuscript per project in V1 is the project's
    documents ordered by `position`."""

    __tablename__ = "documents"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    position: Mapped[int]
    ai_locked: Mapped[bool] = mapped_column(default=False, server_default=false())
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    created_by: Mapped[str] = mapped_column(String(100))
    versions: Mapped[list["DocumentVersion"]] = relationship(back_populates="document", order_by="DocumentVersion.number")


class AuthorshipKind(str, enum.Enum):
    inserted = "inserted"
    removed = "removed"
    changed = "changed"
    resolved = "resolved"
    restored = "restored"


class SpanOrigin(str, enum.Enum):
    typed = "typed"
    dictated = "dictated"
    pasted = "pasted"
    imported = "imported"
    from_note = "from_note"
    ai_edited = "ai_edited"
    ai_suggested = "ai_suggested"


class DocumentVersion(Base):
    """One saved state of a document. Append-only (enforced by `ledger_guard`): saving writes the next number and
    nothing already written is changed or removed, so every autosave is kept (Q21). `content_json` is the
    ProseMirror document; `content_text` is its plain-text projection; `content_hash` is sha256 of the canonical JSON."""

    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "number", name="uq_document_version_number"),
        UniqueConstraint("document_id", "client_save_id", name="uq_document_version_client_save"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(index=True)
    number: Mapped[int]
    parent_number: Mapped[int | None]
    content_json: Mapped[dict] = mapped_column(JSON)
    content_text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    saved_by: Mapped[str] = mapped_column(String(100))
    client_save_id: Mapped[str] = mapped_column(String(64))
    client_session_id: Mapped[str | None] = mapped_column(String(64))
    client_saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    restored_from_number: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    document: Mapped[Document] = relationship(back_populates="versions")


class AuthorshipEvent(Base):
    """One change to one authorship span, recorded against the version that made it (X.31.8). Append-only
    (enforced by `ledger_guard`). It never holds the text itself: that lives in the versions, which are also
    append-only."""

    __tablename__ = "authorship_events"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id"), index=True)
    span_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[AuthorshipKind] = mapped_column(Enum(AuthorshipKind, name="authorship_kind"))
    origin: Mapped[SpanOrigin] = mapped_column(Enum(SpanOrigin, name="span_origin"))
    paste_status: Mapped[str | None] = mapped_column(String(12))
    actor: Mapped[str] = mapped_column(String(100))
    chars_before: Mapped[int]
    chars_after: Mapped[int]
    text_hash: Mapped[str] = mapped_column(String(64))
    suggestion_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ai_suggestions.id"), index=True)
    model_id: Mapped[str | None] = mapped_column(String(255))
    prompt_version: Mapped[str | None] = mapped_column(String(100))
    note_id: Mapped[uuid.UUID | None]
    detected_source_id: Mapped[uuid.UUID | None]
    detected_document_id: Mapped[uuid.UUID | None]
    cited_source_id: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SimilarityCheck(Base):
    """One similarity check of a saved section version against the project's sources (X.31.14). Append-only
    (enforced by `ledger_guard`): a result is never edited, and a re-check is a new row. `findings_json` holds
    offsets, kinds, ids and scores only, never the text of the section or of a source."""

    __tablename__ = "similarity_checks"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id"), index=True)
    provider: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(12))
    requested_by: Mapped[str] = mapped_column(String(100))
    findings_json: Mapped[list] = mapped_column(JSON)
    summary_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProvenanceReport(Base):
    """A frozen writing provenance report (X.31.12). Append-only (enforced by `ledger_guard`): a report is never
    edited, and a regenerated report is a new row. Its Artifact (`kind="provenance_report"`) goes stale when a
    covered section is saved again. `report_json` holds counts, ids and timestamps, never manuscript or source text."""

    __tablename__ = "provenance_reports"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    scope: Mapped[str] = mapped_column(String(12))
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id"), index=True)
    covered_versions: Mapped[list] = mapped_column(JSON)
    report_json: Mapped[dict] = mapped_column(JSON)
    statement_text: Mapped[str] = mapped_column(Text)
    statement_template: Mapped[str] = mapped_column(String(60))
    report_hash: Mapped[str] = mapped_column(String(64))
    generated_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
    confidence: Mapped[float | None] = mapped_column(Float)  # the model's own 0-1 estimate (v4+ prompts)
    insufficient_evidence: Mapped[bool | None] = mapped_column()  # True: the model abstained
    insufficient_reason: Mapped[str | None] = mapped_column(Text)  # what evidence is missing
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
        # Serves the worker claim query: filter on status, take the oldest by created_at.
        Index("ix_tasks_claim", "status", "created_at"),
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


class LlmUsage(Base):
    """Tokens one model call used (M0.9.1). Tokens are NULL when the provider reported none."""

    __tablename__ = "llm_usage"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(index=True)  # plain value, not an FK: spend history outlives a project
    run_id: Mapped[uuid.UUID | None] = mapped_column()
    purpose: Mapped[str] = mapped_column(String(50))  # research_run | prescreen | ...
    model: Mapped[str] = mapped_column(String(255))
    prompt_tokens: Mapped[int | None] = mapped_column()
    completion_tokens: Mapped[int | None] = mapped_column()
    total_tokens: Mapped[int | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceEmbedding(Base):
    """One source's embedding (M3.8.1). Kept per (model, text hash), so changed text or a new model re-embeds.

    The vector is stored as a JSON list of floats so SQLite and Postgres both work; pgvector can replace it
    when similarity search over many projects is needed.
    """

    __tablename__ = "source_embeddings"
    __table_args__ = (UniqueConstraint("source_id", "model", "text_hash", name="uq_source_embedding"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), index=True)
    model: Mapped[str] = mapped_column(String(255))
    text_hash: Mapped[str] = mapped_column(String(64))  # sha256 of the exact text that was embedded
    dims: Mapped[int] = mapped_column()
    vector: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ClusterRun(Base):
    """One thematic clustering of a project's sources (spec 5.5.1, M3.8.1). Never edited after it is saved,
    except the human labels of its clusters. Same embeddings + k + seed give the same clusters."""

    __tablename__ = "cluster_runs"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    model_id: Mapped[str] = mapped_column(String(255))  # embedding model
    k: Mapped[int] = mapped_column()
    seed: Mapped[int] = mapped_column()
    n_sources: Mapped[int] = mapped_column()
    created_by: Mapped[str] = mapped_column(String(100))  # the person who asked for it
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    clusters: Mapped[list["Cluster"]] = relationship(order_by="Cluster.position", cascade="all, delete-orphan")


class Cluster(Base):
    """One group in a `ClusterRun`. `label` starts as "Cluster N" and only a person changes it (no AI labels)."""

    __tablename__ = "clusters"
    __table_args__ = (UniqueConstraint("run_id", "position", name="uq_cluster_position"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cluster_runs.id"), index=True)
    position: Mapped[int] = mapped_column()  # 1-based, largest cluster first
    label: Mapped[str] = mapped_column(String(200))
    label_edited_by: Mapped[str | None] = mapped_column(String(100))
    label_edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    members: Mapped[list["ClusterMember"]] = relationship(order_by="ClusterMember.distance", cascade="all, delete-orphan")


class ClusterMember(Base):
    __tablename__ = "cluster_members"
    __table_args__ = (UniqueConstraint("cluster_id", "source_id", name="uq_cluster_member"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    cluster_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clusters.id"), index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), index=True)
    distance: Mapped[float] = mapped_column(Float)  # cosine distance to the cluster centre (0 = at the centre)


class ConnectorBreakerState(Base):
    """Cross-process circuit breaker state for one connector (M1.3.8).

    `ConnectorHttpClient` (M1.3.2) built its own in-memory breaker, but `connectors/factory.py`
    builds a fresh client per task/request, so that breaker never actually accumulated failures
    across separate searches, let alone across the web and worker processes. This row is the
    shared source of truth instead: `connectors/breaker_store.py::DbBreakerSync` reads/writes
    it in its own short transaction, independent of the caller's, so a remembered failure
    survives even if the caller's own transaction later rolls back. `open_until` is wall-clock
    (`time.time()`) seconds — `None` means the breaker is closed."""

    __tablename__ = "connector_breaker_state"
    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    open_until: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# Registers the immutable-excerpt and append-only audit/note guards (ORM events + DB trigger DDL). Keep at the bottom.
from app import excerpt_guard  # noqa: E402,F401
from app import audit_guard  # noqa: E402,F401
from app import note_guard  # noqa: E402,F401
from app import suggestion_guard  # noqa: E402,F401
from app import project_schema_guard  # noqa: E402,F401
from app import source_gate_guard  # noqa: E402,F401
from app import claim_guard  # noqa: E402,F401
<<<<<<< Updated upstream
from app import ledger_guard  # noqa: E402,F401
=======
from app import theory_construct_guard  # noqa: E402,F401
from app import research_question_guard  # noqa: E402,F401
>>>>>>> Stashed changes
