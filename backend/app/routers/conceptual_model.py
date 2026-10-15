"""Conceptual model builder API (M4.2): literature-linked paths, diagram text, construct–measure check.

A person draws every path; nothing here proposes one. The Mermaid and Graphviz text is regenerated
from the stored paths on every read, so editing the model means adding or removing paths.
The alignment check only confirms that a measure is *recorded* for each construct; whether that
measure is validated needs the validated-measure DB (M4.4), so the response says it was not checked.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit
from app.database import get_db
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.models import ConceptualPath, Construct, Project, Source, User

router = APIRouter(prefix="/projects/{project_id}/conceptual-model", tags=["conceptual-model"])

MEASURE_VALIDATION_NOTE = "A measure is recorded; whether it is validated is not checked (validated-measure DB is M4.4)."


class PathIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_construct_id: uuid.UUID
    to_construct_id: uuid.UUID
    kind: Literal["direct", "mediation", "moderation"]
    via_construct_id: uuid.UUID | None = None
    source_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)
    rationale: str = Field(min_length=1, max_length=20_000)

    @model_validator(mode="after")
    def _shape(self) -> "PathIn":
        if (self.kind == "direct") != (self.via_construct_id is None):
            raise ValueError("A direct path has no via construct; mediation and moderation need one")
        used = [self.from_construct_id, self.to_construct_id] + ([self.via_construct_id] if self.via_construct_id else [])
        if len(set(used)) != len(used):
            raise ValueError("A path must use distinct constructs")
        if len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("source_ids must not repeat")
        return self


class PathRead(PathIn):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    project_id: uuid.UUID
    created_by: str
    created_at: datetime
    updated_at: datetime


def _mermaid_text(text: str) -> str:
    return text.replace("#", "#35;").replace('"', "#quot;").replace("(", "#40;").replace(")", "#41;")


def _dot_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _edges(paths: list[ConceptualPath]) -> list[tuple[uuid.UUID, uuid.UUID, str]]:
    """(from, to, style) edges in path order; an edge shared by several paths is drawn once."""
    edges: list[tuple[uuid.UUID, uuid.UUID, str]] = []
    for p in paths:
        if p.kind == "direct":
            new = [(p.from_construct_id, p.to_construct_id, "solid")]
        elif p.kind == "mediation":
            new = [(p.from_construct_id, p.via_construct_id, "solid"), (p.via_construct_id, p.to_construct_id, "solid")]
        else:
            new = [(p.from_construct_id, p.to_construct_id, "solid"), (p.via_construct_id, p.to_construct_id, "moderates")]
        edges += [edge for edge in new if edge not in edges]
    return edges


def _diagrams(paths: list[ConceptualPath], constructs: dict[uuid.UUID, Construct]) -> tuple[str, str]:
    used = sorted({cid for p in paths for cid in (p.from_construct_id, p.to_construct_id, p.via_construct_id) if cid},
                  key=lambda cid: (constructs[cid].name, constructs[cid].author, str(cid)))
    node = {cid: f"c{index}" for index, cid in enumerate(used)}
    label = {cid: f"{constructs[cid].name} ({constructs[cid].author})" for cid in used}
    mermaid = ["flowchart LR"] + [f'    {node[cid]}["{_mermaid_text(label[cid])}"]' for cid in used]
    dot = ["digraph conceptual_model {", "    rankdir=LR;"] + [f'    {node[cid]} [label="{_dot_text(label[cid])}"];' for cid in used]
    for a, b, style in _edges(paths):
        if style == "moderates":
            mermaid.append(f"    {node[a]} -.->|moderates| {node[b]}")
            dot.append(f'    {node[a]} -> {node[b]} [style=dashed, label="moderates"];')
        else:
            mermaid.append(f"    {node[a]} --> {node[b]}")
            dot.append(f"    {node[a]} -> {node[b]};")
    dot.append("}")
    return "\n".join(mermaid), "\n".join(dot)


def _alignment(paths: list[ConceptualPath], constructs: dict[uuid.UUID, Construct]) -> list[dict]:
    used = {cid for p in paths for cid in (p.from_construct_id, p.to_construct_id, p.via_construct_id) if cid}
    return [{"construct_id": c.id, "name": c.name, "author": c.author, "measure_count": len(c.measures or []),
             "aligned": bool(c.measures), "in_model": c.id in used}
            for c in sorted(constructs.values(), key=lambda c: (c.name, c.author, str(c.id)))]


@router.get("")
def get_model(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    paths = list(db.scalars(select(ConceptualPath).where(ConceptualPath.project_id == project.id)
                            .order_by(ConceptualPath.created_at, ConceptualPath.id)))
    constructs = {c.id: c for c in db.scalars(select(Construct).where(Construct.project_id == project.id))}
    mermaid, dot = _diagrams(paths, constructs)
    alignment = _alignment(paths, constructs)
    return {"paths": [PathRead.model_validate(p) for p in paths], "mermaid": mermaid, "graphviz": dot,
            "alignment": alignment, "unaligned_in_model": sum(1 for row in alignment if row["in_model"] and not row["aligned"]),
            "alignment_note": MEASURE_VALIDATION_NOTE}


@router.post("/paths", response_model=PathRead, status_code=status.HTTP_201_CREATED)
def create_path(payload: PathIn, project: Project = Depends(project_access(WRITE_ROLES)), user: User = Depends(current_user), db: Session = Depends(get_db)):
    construct_ids = {payload.from_construct_id, payload.to_construct_id} | ({payload.via_construct_id} if payload.via_construct_id else set())
    known = set(db.scalars(select(Construct.id).where(Construct.project_id == project.id, Construct.id.in_(construct_ids))))
    if known != construct_ids:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Every construct must belong to this project")
    sources = set(db.scalars(select(Source.id).where(
        Source.project_id == project.id, Source.id.in_(payload.source_ids), Source.merged_into.is_(None))))
    if sources != set(payload.source_ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Every source must belong to this project and not be merged into another source")
    # The unique constraint can't catch a repeated direct path: its via_construct_id is NULL, and NULLs never collide.
    duplicate = db.scalar(select(ConceptualPath.id).where(
        ConceptualPath.project_id == project.id, ConceptualPath.from_construct_id == payload.from_construct_id,
        ConceptualPath.to_construct_id == payload.to_construct_id, ConceptualPath.kind == payload.kind,
        ConceptualPath.via_construct_id.is_(None) if payload.via_construct_id is None else ConceptualPath.via_construct_id == payload.via_construct_id))
    if duplicate is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This path is already in the model")
    values = payload.model_dump()
    values["source_ids"] = [str(item) for item in payload.source_ids]
    item = ConceptualPath(project_id=project.id, created_by=str(user.id), **values)
    db.add(item)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "This path is already in the model") from exc
    audit.record(db, actor=audit.user_actor(user), action="conceptual_path.created", project_id=project.id, payload={"path_id": str(item.id)})
    db.commit()
    db.refresh(item)
    return item


@router.delete("/paths/{path_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_path(path_id: uuid.UUID, project: Project = Depends(project_access(WRITE_ROLES)), user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = db.scalar(select(ConceptualPath).where(ConceptualPath.id == path_id, ConceptualPath.project_id == project.id))
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Path not found")
    audit.record(db, actor=audit.user_actor(user), action="conceptual_path.deleted", project_id=project.id,
                 payload={"path_id": str(item.id), "kind": item.kind, "from_construct_id": str(item.from_construct_id),
                          "to_construct_id": str(item.to_construct_id), "source_ids": item.source_ids})
    db.delete(item)
    db.commit()
