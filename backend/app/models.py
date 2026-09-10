import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship as sa_relationship

from app.database import Base


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class User(Timestamped, Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(200))
    projects: Mapped[list["Project"]] = sa_relationship(back_populates="owner", cascade="all, delete-orphan")
    external_identities: Mapped[list["ExternalIdentity"]] = sa_relationship(back_populates="user", cascade="all, delete-orphan")


class ExternalIdentity(Timestamped, Base):
    __tablename__ = "external_identities"
    __table_args__ = (UniqueConstraint("provider", "subject", name="uq_provider_subject"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(50))
    subject: Mapped[str] = mapped_column(String(255))
    user: Mapped[User] = sa_relationship(back_populates="external_identities")


class Project(Timestamped, Base):
    __tablename__ = "projects"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="active")
    owner: Mapped[User] = sa_relationship(back_populates="projects")
    context_items: Mapped[list["ResearchContextItem"]] = sa_relationship(back_populates="project", cascade="all, delete-orphan")
    sources: Mapped[list["Source"]] = sa_relationship(back_populates="project", cascade="all, delete-orphan")
    research_runs: Mapped[list["ResearchRun"]] = sa_relationship(back_populates="project", cascade="all, delete-orphan")
    claims: Mapped[list["Claim"]] = sa_relationship(back_populates="project", cascade="all, delete-orphan")
    datasets: Mapped[list["Dataset"]] = sa_relationship(back_populates="project", cascade="all, delete-orphan")
    literature_documents: Mapped[list["LiteratureDocument"]] = sa_relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Dataset(Timestamped, Base):
    __tablename__ = "datasets"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(10))
    columns: Mapped[list] = mapped_column(JSON)
    rows: Mapped[list] = mapped_column(JSON)
    row_count: Mapped[int] = mapped_column(default=0)
    project: Mapped[Project] = sa_relationship(back_populates="datasets")


class LiteratureDocument(Timestamped, Base):
    __tablename__ = "literature_documents"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    text: Mapped[str] = mapped_column(Text)
    page_count: Mapped[int] = mapped_column(default=0)
    project: Mapped[Project] = sa_relationship(back_populates="literature_documents")


class ContextKind(str, enum.Enum):
    question = "question"
    objective = "objective"
    hypothesis = "hypothesis"
    methodology = "methodology"
    variable = "variable"
    decision = "decision"


class ResearchContextItem(Timestamped, Base):
    __tablename__ = "research_context_items"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    kind: Mapped[ContextKind] = mapped_column(Enum(ContextKind, name="context_kind"))
    content: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)
    project: Mapped[Project] = sa_relationship(back_populates="context_items")


class Source(Timestamped, Base):
    __tablename__ = "sources"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(500))
    doi: Mapped[str | None] = mapped_column(String(255), index=True)
    url: Mapped[str | None] = mapped_column(String(2000))
    authors: Mapped[list | None] = mapped_column(JSON)
    year: Mapped[int | None]
    source_type: Mapped[str] = mapped_column(String(50), default="article")
    metadata_verified: Mapped[bool] = mapped_column(default=False)
    project: Mapped[Project] = sa_relationship(back_populates="sources")
    excerpts: Mapped[list["SourceExcerpt"]] = sa_relationship(back_populates="source", cascade="all, delete-orphan")


class SourceExcerpt(Timestamped, Base):
    """An auditable, immutable note or quotation supplied for a source."""

    __tablename__ = "source_excerpts"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), index=True)
    content: Mapped[str] = mapped_column(Text)
    locator: Mapped[str | None] = mapped_column(String(255))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[Source] = sa_relationship(back_populates="excerpts")


class ClaimStatus(str, enum.Enum):
    supports = "supports"
    contradicts = "contradicts"
    qualifies = "qualifies"
    unclear = "unclear"


class Claim(Timestamped, Base):
    __tablename__ = "claims"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[ClaimStatus] = mapped_column(Enum(ClaimStatus, name="claim_status"), default=ClaimStatus.unclear)
    project: Mapped["Project"] = sa_relationship(back_populates="claims")
    evidence: Mapped[list["ClaimEvidence"]] = sa_relationship(back_populates="claim", cascade="all, delete-orphan")


class ClaimEvidence(Timestamped, Base):
    __tablename__ = "claim_evidence"
    __table_args__ = (UniqueConstraint("claim_id", "source_id", name="uq_claim_source"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id"), index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), index=True)
    relationship: Mapped[ClaimStatus] = mapped_column(Enum(ClaimStatus, name="claim_status"))
    excerpt: Mapped[str | None] = mapped_column(Text)
    locator: Mapped[str | None] = mapped_column(String(255))
    claim: Mapped[Claim] = sa_relationship(back_populates="evidence")
    source: Mapped["Source"] = sa_relationship()


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
    attempt_count: Mapped[int] = mapped_column(default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    project: Mapped[Project] = sa_relationship(back_populates="research_runs")
