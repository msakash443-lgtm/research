import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl, field_validator

from app.discipline import EffectiveSettings, ProfileSettings, available_profiles
from app.doi import normalize_doi
from app.models import (
    ARC_SOURCE_TYPES,
    ArtifactStatus,
    ContextKind,
    GateCode,
    GateStatus,
    ProjectRole,
    ProjectStage,
    ResearchRunStatus,
)


class ProjectBase(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None


def _known_discipline(value: str | None) -> str | None:
    """A discipline must name a shipped profile (checked on input only, so stored data can always be read)."""
    if value is not None and value not in available_profiles():
        raise ValueError(f"unknown discipline {value!r}; available: {available_profiles()}")
    return value


class ProjectCreate(ProjectBase):
    discipline: str | None = Field(default=None, max_length=60)

    _check_discipline = field_validator("discipline")(_known_discipline)


class ProjectRead(ProjectBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    status: str
    stage: ProjectStage
    discipline: str | None = None
    created_at: datetime
    updated_at: datetime


class ContextItemCreate(BaseModel):
    kind: ContextKind
    content: str = Field(min_length=1)
    rationale: str | None = None


class ContextItemRead(ContextItemCreate):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    created_by: str | None = None
    created_at: datetime
    # Set by the list endpoint: whether research runs are given this item (agent/context.py).
    used_by_agent: bool | None = None


class SourceCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    doi: str | None = Field(default=None, max_length=300)
    url: HttpUrl | None = None
    authors: list[str] | None = None
    year: int | None = Field(default=None, ge=1000, le=3000)
    source_type: str = Field(default="article", min_length=1, max_length=50)
    venue: str | None = Field(default=None, max_length=500)
    abstract: str | None = Field(default=None, max_length=20000)
    oa_url: HttpUrl | None = None
    # `source_ids`, `fulltext_path` and `quality_flags` are deliberately not accepted here: the system sets them.
    evidence_excerpt: str | None = Field(default=None, max_length=12000)
    locator: str | None = Field(default=None, max_length=255)

    @field_validator("doi")
    @classmethod
    def normalise_doi(cls, value: str | None) -> str | None:
        return normalize_doi(value)

    @field_validator("source_type")
    @classmethod
    def reject_retrieval_types(cls, value: str) -> str:
        # These types mark machine-retrieved evidence (Source.is_automated); a person-entered
        # source must not be labelled "automatically retrieved, unverified".
        if value.strip().lower() in ARC_SOURCE_TYPES:
            raise ValueError("This source type is reserved for automatically retrieved sources")
        return value

    @field_validator("title", "evidence_excerpt", "locator", "venue", "abstract")
    @classmethod
    def reject_blank_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("This field cannot be blank")
        return value.strip() if value is not None else None


class SourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    title: str
    doi: str | None = None
    url: str | None
    authors: list | None
    year: int | None
    source_type: str
    metadata_verified: bool
    verification_method: str | None = None  # "human" / "automatic" / None (not verified)
    verified_at: datetime | None = None
    verification: dict | None = None  # last automatic check: verdict, reasons, services consulted
    origin: str  # "manual" or "retrieved"; set by the system
    is_automated: bool  # machine-retrieved and not yet verified by a person (Source.is_automated)
    created_by: str | None = None
    venue: str | None = None
    abstract: str | None = None
    oa_url: str | None = None
    source_ids: dict | None = None
    fulltext_path: str | None = None
    quality_flags: list | None = None
    evidence_excerpt: str | None = None
    excerpt_locator: str | None = None
    created_at: datetime


class SourceMerge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_ids: list[uuid.UUID] = Field(min_length=2, max_length=20)
    keep: uuid.UUID | None = None  # default: the verified one, else the oldest


class ResearchRunCreate(BaseModel):
    question: str = Field(min_length=8, max_length=4000)
    use_web_retrieval: bool = False

    @field_validator("question")
    @classmethod
    def reject_blank_question(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Question cannot be blank")
        return value.strip()


class ResearchRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    question: str
    status: ResearchRunStatus
    research_plan: list | None
    input_snapshot: dict | None = None
    answer: str | None
    error_message: str | None
    provider_model: str | None
    prompt_version: str | None = None
    use_web_retrieval: bool
    created_by: str | None = None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class MemberInvite(BaseModel):
    email: EmailStr
    role: ProjectRole


class MemberRead(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str | None
    role: ProjectRole
    created_at: datetime


class AuditEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    timestamp: datetime
    actor: str
    action: str
    payload_json: dict | None
    model_id: str | None
    prompt_version: str | None


class GateDecision(BaseModel):
    note: str | None = Field(default=None, max_length=4000)

    @field_validator("note")
    @classmethod
    def blank_note_is_none(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class GateRead(BaseModel):
    code: GateCode
    status: GateStatus
    decided_by: str | None
    decided_at: datetime | None
    note: str | None
    required_roles: list[ProjectRole]
    can_decide: bool


class ArtifactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    kind: str
    ref_id: str
    stage: ProjectStage
    status: ArtifactStatus
    stale_reason: str | None
    stale_at: datetime | None
    created_by: str | None


class ReentryRequest(BaseModel):
    stage: ProjectStage
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def reason_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Say why you are going back; the reason is recorded")
        return value.strip()


class StageStep(BaseModel):
    stage: ProjectStage
    gate: GateCode | None


class ReentryRead(BaseModel):
    from_stage: ProjectStage
    to_stage: ProjectStage
    reason: str
    stages_to_redo: list[StageStep]
    gates_reset: list[GateCode]
    stale_artifacts: list[ArtifactRead]


class RerunStep(BaseModel):
    stage: ProjectStage
    gate: GateCode | None
    gate_status: GateStatus | None
    artifacts: list[ArtifactRead]


class AdvanceRequest(BaseModel):
    # Optional: only needed where there is a choice (after `submitted`). Passing it also makes a retried
    # request safe: asking twice for the same stage is refused the second time instead of moving twice.
    stage: ProjectStage | None = None


class AdvanceRead(BaseModel):
    from_stage: ProjectStage
    to_stage: ProjectStage
    gate: GateCode | None


class StageOptionRead(BaseModel):
    stage: ProjectStage
    gate: GateCode | None
    gate_status: GateStatus | None
    ready: bool


class StageRead(BaseModel):
    stage: ProjectStage
    next: list[StageOptionRead]


class DisciplineProfileRead(BaseModel):
    name: str
    version: int
    label: str
    description: str
    settings: ProfileSettings


class ProfileUpdate(BaseModel):
    """Replace the project's profile choice and overrides. `discipline: null` means no profile."""

    model_config = ConfigDict(extra="forbid")
    discipline: str | None = Field(default=None, max_length=60)
    overrides: ProfileSettings = Field(default_factory=ProfileSettings)

    _check_discipline = field_validator("discipline")(_known_discipline)


class ProjectProfileRead(BaseModel):
    discipline: str | None
    label: str | None
    overrides: dict
    effective: EffectiveSettings
