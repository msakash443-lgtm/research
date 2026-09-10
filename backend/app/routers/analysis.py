from __future__ import annotations

import io
import uuid

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis import run_analysis
from app.database import get_db
from app.dependencies import owned_project
from app.models import Dataset, Project
from app.schemas import AnalysisCreate, DatasetRead

router = APIRouter(prefix="/projects/{project_id}/analysis", tags=["analysis"])
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


@router.post("/datasets", response_model=DatasetRead, status_code=status.HTTP_201_CREATED)
async def upload_dataset(file: UploadFile = File(...), project: Project = Depends(owned_project), db: Session = Depends(get_db)):
    filename = file.filename or "dataset"
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in {"csv", "xlsx", "xls"}:
        raise HTTPException(status_code=415, detail="Upload a CSV or Excel workbook.")
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Dataset must be 25 MB or smaller.")
    try:
        frame = pd.read_csv(io.BytesIO(content)) if extension == "csv" else pd.read_excel(io.BytesIO(content))
        frame.columns = [str(column) for column in frame.columns]
        frame = frame.astype(object).where(pd.notna(frame), None)
        rows = frame.to_dict(orient="records")
    except (ValueError, TypeError, pd.errors.ParserError) as exc:
        raise HTTPException(status_code=422, detail=f"Could not read dataset: {exc}") from exc
    if frame.empty or not len(frame.columns):
        raise HTTPException(status_code=422, detail="The uploaded dataset has no rows or columns.")
    dataset = Dataset(project_id=project.id, name=filename, file_type=extension, columns=[str(column) for column in frame.columns], rows=rows, row_count=len(frame))
    db.add(dataset)
    db.commit()
    db.refresh(dataset)
    return dataset


@router.get("/datasets", response_model=list[DatasetRead])
def list_datasets(project: Project = Depends(owned_project), db: Session = Depends(get_db)):
    return db.scalars(select(Dataset).where(Dataset.project_id == project.id).order_by(Dataset.created_at.desc())).all()


@router.post("/runs")
def create_analysis(payload: AnalysisCreate, project: Project = Depends(owned_project), db: Session = Depends(get_db)):
    dataset = db.scalar(select(Dataset).where(Dataset.id == payload.dataset_id, Dataset.project_id == project.id))
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    try:
        result = run_analysis(payload.prompt, dataset.rows, dataset.columns)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"dataset_id": dataset.id, "prompt": payload.prompt, "result": result}
