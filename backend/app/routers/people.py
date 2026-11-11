from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.database import get_db
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.identifiers import normalize_orcid, normalize_ror
from app.models import Project, ResearchInstitution, ResearchPerson, User
from app.schemas import InstitutionCreate, InstitutionRead, PersonCreate, PersonRead

router = APIRouter(prefix="/projects/{project_id}", tags=["people"])


@router.get("/institutions", response_model=list[InstitutionRead])
def list_institutions(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    return db.scalars(
        select(ResearchInstitution).where(ResearchInstitution.project_id == project.id).order_by(ResearchInstitution.name)
    ).all()


@router.post("/institutions", response_model=InstitutionRead, status_code=status.HTTP_201_CREATED)
def create_institution(
    body: InstitutionCreate,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    try:
        ror_id = normalize_ror(body.ror_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    row = ResearchInstitution(project_id=project.id, name=body.name, ror_id=ror_id, created_by=audit.user_actor(user))
    db.add(row)
    db.flush()
    audit.record(db, actor=audit.user_actor(user), action="institution.created", project_id=project.id,
                 payload={"institution_id": str(row.id), "has_ror_id": ror_id is not None})
    db.commit()
    db.refresh(row)
    return row


@router.get("/people", response_model=list[PersonRead])
def list_people(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    return db.scalars(
        select(ResearchPerson).where(ResearchPerson.project_id == project.id).order_by(ResearchPerson.name)
    ).all()


@router.post("/people", response_model=PersonRead, status_code=status.HTTP_201_CREATED)
def create_person(
    body: PersonCreate,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    try:
        orcid = normalize_orcid(body.orcid)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    if body.institution_id is not None:
        # Same-project check; a foreign or unknown id reads as 404 so existence is not leaked.
        institution = db.scalar(
            select(ResearchInstitution).where(
                ResearchInstitution.id == body.institution_id,
                ResearchInstitution.project_id == project.id,
            )
        )
        if institution is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Institution not found")
    row = ResearchPerson(
        project_id=project.id, name=body.name, orcid=orcid, institution_id=body.institution_id,
        created_by=audit.user_actor(user),
    )
    db.add(row)
    db.flush()
    audit.record(db, actor=audit.user_actor(user), action="person.created", project_id=project.id,
                 payload={"person_id": str(row.id), "has_orcid": orcid is not None,
                          "institution_linked": row.institution_id is not None})
    db.commit()
    db.refresh(row)
    return row
