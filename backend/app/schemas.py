import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from app.models import ClaimStatus, ContextKind, ResearchRunStatus


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None


class ProjectUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    status: str | None = Field(default=None, min_length=1, max_length=40)

    @field_validator("title")
    @classmethod
    def reject_blank_title(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Title cannot be blank")
        return value.strip() if value is not None else None


class ProjectRead(ProjectCreate):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    status: str
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Context items
# ---------------------------------------------------------------------------

class ContextItemCreate(BaseModel):
    kind: ContextKind
    content: str = Field(min_length=1)
    rationale: str | None = None


class ContextItemRead(ContextItemCreate):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    created_at: datetime


# ---------------------------------------------------------------------------
# Sources & excerpts
# ---------------------------------------------------------------------------

class SourceCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    url: HttpUrl | None = None
    doi: str | None = Field(default=None, max_length=255)
    authors: list[str] | None = None
    year: int | None = Field(default=None, ge=1000, le=3000)
    source_type: str = Field(default="article", min_length=1, max_length=50)
    evidence_excerpt: str | None = Field(default=None, max_length=12000)
    locator: str | None = Field(default=None, max_length=255)

    @field_validator("title", "evidence_excerpt", "locator")
    @classmethod
    def reject_blank_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("This field cannot be blank")
        return value.strip() if value is not None else None


class SourceUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    url: HttpUrl | None = None
    doi: str | None = Field(default=None, max_length=255)
    authors: list[str] | None = None
    year: int | None = Field(default=None, ge=1000, le=3000)
    source_type: str | None = Field(default=None, min_length=1, max_length=50)
    metadata_verified: bool | None = None

    @field_validator("title")
    @classmethod
    def reject_blank_title(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Title cannot be blank")
        return value.strip() if value is not None else None


class ExcerptCreate(BaseModel):
    content: str = Field(min_length=1, max_length=12000)
    locator: str | None = Field(default=None, max_length=255)

    @field_validator("content", "locator")
    @classmethod
    def reject_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("This field cannot be blank")
        return value.strip() if value is not None else None


class ExcerptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    content: str
    locator: str | None
    content_hash: str
    created_at: datetime


class SourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    title: str
    url: str | None
    doi: str | None
    authors: list | None
    year: int | None
    source_type: str
    metadata_verified: bool
    evidence_excerpt: str | None = None
    excerpt_locator: str | None = None
    apa_citation: str
    mla_citation: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Claims & claim evidence
# ---------------------------------------------------------------------------

class ClaimCreate(BaseModel):
    text: str = Field(min_length=1)
    status: ClaimStatus = ClaimStatus.unclear

    @field_validator("text")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Claim text cannot be blank")
        return value.strip()


class ClaimUpdate(BaseModel):
    text: str | None = Field(default=None, min_length=1)
    status: ClaimStatus | None = None

    @field_validator("text")
    @classmethod
    def reject_blank_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Claim text cannot be blank")
        return value.strip() if value is not None else None


class ClaimEvidenceCreate(BaseModel):
    source_id: uuid.UUID
    relationship: ClaimStatus
    excerpt: str | None = Field(default=None, max_length=12000)
    locator: str | None = Field(default=None, max_length=255)


class ClaimEvidenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    claim_id: uuid.UUID
    source_id: uuid.UUID
    relationship: ClaimStatus
    excerpt: str | None
    locator: str | None
    created_at: datetime


class ClaimRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    text: str
    status: ClaimStatus
    created_at: datetime
    updated_at: datetime
    evidence: list[ClaimEvidenceRead] = []


# ---------------------------------------------------------------------------
# Research runs
# ---------------------------------------------------------------------------

class ResearchRunCreate(BaseModel):
    question: str = Field(min_length=8, max_length=4000)

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
    answer: str | None
    error_message: str | None
    provider_model: str | None
    attempt_count: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class DatasetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    file_type: str
    columns: list
    row_count: int
    created_at: datetime


class AnalysisCreate(BaseModel):
    prompt: str = Field(min_length=3, max_length=4000)
    dataset_id: uuid.UUID

    @field_validator("prompt")
    @classmethod
    def reject_blank_prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Analysis prompt cannot be blank")
        return value.strip()


class LiteratureDocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    filename: str
    page_count: int
    created_at: datetime


class LiteratureReviewCreate(BaseModel):
    document_id: uuid.UUID
    citation_style: str = Field(pattern="^(APA|MLA)$")
    research_question: str = Field(min_length=8, max_length=4000)

    @field_validator("research_question")
    @classmethod
    def reject_blank_review_question(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Research question cannot be blank")
        return value.strip()
