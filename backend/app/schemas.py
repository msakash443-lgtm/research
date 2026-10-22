import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl, StringConstraints, field_validator, model_validator

from app.discipline import EffectiveSettings, ProfileSettings, available_profiles
from app.doi import normalize_doi
from app.exit_conditions import Level
from app.models import (
    ARC_SOURCE_TYPES,
    AiInvolvement,
    AiModelRoute,
    ArtifactStatus,
    ContextKind,
    GateCode,
    GateStatus,
    NoteKind,
    NoteStatus,
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
    stage: ProjectStage
    discipline: str | None = None
    ai_involvement: AiInvolvement
    ai_model_route: AiModelRoute
    archived_at: datetime | None = None
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
    # Set by the list endpoint: whether the item is set to "never for this item" (ai_item_locks.py).
    ai_locked: bool | None = None
    # Read-only links (X.31.2): the note this came from, and for a decision the item it concerns.
    promoted_from_note_id: uuid.UUID | None = None
    related_item_id: uuid.UUID | None = None


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
    fulltext_access: dict | None = None
    coverage: dict | None = None  # full text / abstract only / not accessible, derived (X.76)
    quality_flags: list | None = None
    found_via: dict | None = None  # how an automated method found it, e.g. snowballing (M1.9.2); system-set
    citation_count: int | None = None  # times cited per `citation_count_source` (M2.13); None = unknown, not 0
    citation_count_source: str | None = None
    citation_count_at: datetime | None = None
    language: str | None = None  # two-letter code a connector reported (X.86); None = not reported
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
    confidence: float | None = None
    insufficient_evidence: bool | None = None
    insufficient_reason: str | None = None
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
    # G7 on a research type with no human participants (X.43.8, decision 5): approval needs this
    # explicit confirmation instead of a full ethics pack. Ignored for every other gate/type.
    confirm_no_human_participants: bool = False

    @field_validator("note")
    @classmethod
    def blank_note_is_none(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class GateReopen(BaseModel):
    reason: str = Field(min_length=1, max_length=4000)

    @field_validator("reason")
    @classmethod
    def reason_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Say why the approval is being reopened; the reason is recorded")
        return value.strip()


class GateRead(BaseModel):
    code: GateCode
    status: GateStatus
    decided_by: str | None
    decided_at: datetime | None
    note: str | None
    required_roles: list[ProjectRole]
    can_decide: bool
    # Earlier gates still waiting for approval; this gate can't be decided until they are (M0.5.10).
    waiting_for: list[GateCode] = []
    can_reopen: bool = False


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
    # Reasons for unmet Recommended conditions, by condition id; 10–500 characters each. Required ones cannot be overridden.
    override_reasons: dict[str, Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=500)]] = Field(
        default_factory=dict
    )


class AdvanceRead(BaseModel):
    from_stage: ProjectStage
    to_stage: ProjectStage
    gate: GateCode | None


class ConditionRead(BaseModel):
    id: str
    level: Level
    met: bool
    message: str
    fix: str | None


class SuggestionRead(BaseModel):
    """An unmet exit condition of a next move, explained (X.55). Advice only: it decides nothing."""

    target: ProjectStage
    condition_id: str
    level: Level
    message: str
    fix: str | None
    meaning: str | None  # None only for a condition the guide does not describe
    step: str | None  # the web screen where it is fixed; None = no screen yet


class StageOptionRead(BaseModel):
    stage: ProjectStage
    gate: GateCode | None
    gate_status: GateStatus | None
    ready: bool
    conditions: list[ConditionRead]  # every exit condition for moving to this option (X.48.2)


class StageChainItem(BaseModel):
    stage: ProjectStage
    gate: GateCode | None
    label: str | None = None


class StageRead(BaseModel):
    stage: ProjectStage
    next: list[StageOptionRead]
    chain: list[StageChainItem]  # every stage in workflow order, with the gate that completes it (X.49)
    suggestions: list[SuggestionRead]  # unmet, built exit conditions of the next moves, most binding first (X.55)
    # Whether this project's own research type involves human participants (X.43.6/X.43.8, decision 4b/5).
    human_participants: bool
    # The project's research type name (X.43.9): the Scope screen's picker shows which type is saved.
    research_type: str


class DisciplineProfileRead(BaseModel):
    name: str
    version: int
    label: str
    description: str
    settings: ProfileSettings


class ResearchTypeRead(BaseModel):
    name: str
    version: int
    label: str
    description: str
    human_participants: bool
    stages: list[StageStep]


class ResearchTypeUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=60, pattern=r"^[a-z][a-z0-9_]{1,59}$")


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


class NoteCreate(BaseModel):
    """A quick capture (X.31.1). `client_id` is set by the device and makes a retried submit
    idempotent: posting the same (signed-in user, client_id) twice returns the same note.

    Only `typed` (the researcher's own words) and `clip` (a web clip: a link and/or quoted text,
    kept separate from `body`) are accepted so far. `voice`, `highlight` and `photo` need
    attachment storage (M0.10.2) and ship with X.31.17/X.31.3/X.31.18.
    """

    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(min_length=1, max_length=64)
    kind: NoteKind = NoteKind.typed
    body: str = Field(default="", max_length=20000)
    quoted_text: str | None = Field(default=None, max_length=20000)
    source_url: str | None = Field(default=None, max_length=2000)
    locator: str | None = Field(default=None, max_length=255)
    captured_at: datetime | None = None
    device: str | None = Field(default=None, max_length=40)
    ai_locked: bool = False

    @field_validator("kind")
    @classmethod
    def _supported_kind(cls, value: NoteKind) -> NoteKind:
        if value not in (NoteKind.typed, NoteKind.clip):
            raise ValueError(f"Capturing a '{value.value}' note isn't available yet")
        return value

    @field_validator("body")
    @classmethod
    def _strip_body(cls, value: str) -> str:
        return value.strip()

    @field_validator("quoted_text", "source_url", "locator", "device")
    @classmethod
    def _strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def _kind_requirements(self) -> "NoteCreate":
        if self.kind is NoteKind.typed and not self.body:
            raise ValueError("A typed note needs body text")
        if self.kind is NoteKind.clip and not (self.quoted_text or self.source_url):
            raise ValueError("A web clip needs quoted text, a link, or both")
        return self


class NoteAttachmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    media_type: str
    size: int
    sha256: str
    created_at: datetime
class HighlightCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(min_length=1, max_length=64)
    source_id: uuid.UUID
    quoted_text: str = Field(min_length=1, max_length=5000)
    body: str = Field(default="", max_length=20000)
    locator: str | None = Field(default=None, max_length=255)
    captured_at: datetime | None = None

    @field_validator("quoted_text", "body", "locator")
    @classmethod
    def _strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def _require_quote(self) -> "HighlightCreate":
        if not self.quoted_text:
            raise ValueError("A highlight needs quoted text")
        return self



class NoteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    project_id: uuid.UUID | None
    kind: NoteKind
    status: NoteStatus
    body: str
    quoted_text: str | None
    source_id: uuid.UUID | None
    excerpt_id: uuid.UUID | None
    locator: str | None
    ai_locked: bool
    revision: int
    captured_at: datetime
    device: str | None
    owner_id: uuid.UUID
    attachments: list[NoteAttachmentRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

class NotePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_revision: int = Field(ge=1)
    body: str = Field(min_length=1, max_length=20000)

    @field_validator("body")
    @classmethod
    def _strip_body(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("body cannot be blank")
        return stripped


class NoteLock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ai_locked: bool


class NoteRevisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    revision: int
    body: str
    quoted_text: str | None
    edited_by: str | None
    created_at: datetime


class NoteFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: uuid.UUID


class NotePromote(BaseModel):
    """Promote a filed note to a research item (X.31.2). The text is the researcher's, not copied from the note."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["idea", "gap", "hypothesis", "claim", "memo", "decision"]
    content: str = Field(min_length=1, max_length=20000)
    rationale: str | None = Field(default=None, max_length=20000)
    related_item_id: uuid.UUID | None = None

    @field_validator("content")
    @classmethod
    def _strip_content(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("content cannot be blank")
        return stripped


class NoteSuggestionRead(BaseModel):
    """A non-AI triage suggestion (`app/note_suggest.py`). `project` and `source` are things to file the note
    under or cite; `stage` is the suggested project's current stage, shown as a chip."""

    kind: Literal["project", "source", "stage"]
    target_id: str
    project_id: str | None = None
    label: str
    score: float


class JournalCandidateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=300)
    scope_terms: list[str] = Field(default_factory=list, max_length=100)
    indexes: list[str] = Field(default_factory=list, max_length=50)
    apc: float | None = Field(default=None, ge=0)
    review_days: int | None = Field(default=None, ge=0)
    source_url: HttpUrl
    checked_at: str = Field(min_length=1, max_length=64)
    official_predatory: bool = False


class JournalCandidateRead(JournalCandidateCreate):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    project_id: uuid.UUID
    created_by: str
    created_at: datetime
    updated_at: datetime


class JournalMatchRequest(BaseModel):
    scope: list[str] = Field(default_factory=list, max_length=100)
    required_index: str | None = Field(default=None, max_length=100)
    max_apc: float | None = Field(default=None, ge=0)
    max_review_days: int | None = Field(default=None, ge=0)

class SubmissionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    journal_name: str = Field(min_length=1, max_length=300)
    manuscript_url: HttpUrl | None = None
    external_id: str | None = Field(default=None, max_length=255)


class SubmissionTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str


class SubmissionRead(SubmissionCreate):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    project_id: uuid.UUID
    status: str
    submitted_at: datetime | None
    created_by: str
    created_at: datetime
    updated_at: datetime
class SubmissionReminderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    submission_id: uuid.UUID
    due_at: datetime
    message: str = Field(min_length=1, max_length=500)


class SubmissionReminderRead(SubmissionReminderCreate):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    project_id: uuid.UUID
    status: str
    created_by: str
    created_at: datetime
    updated_at: datetime
