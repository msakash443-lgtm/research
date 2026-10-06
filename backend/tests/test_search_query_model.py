import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.models import Project, SearchQuery, User


def _project(db) -> Project:
    user = User(email=f"{uuid.uuid4()}@example.org", display_name="Owner")
    db.add(user)
    db.flush()
    project = Project(owner_id=user.id, title="P")
    db.add(project)
    db.flush()
    return project


def test_search_query_round_trips_with_defaults():
    with SessionLocal() as db:
        project = _project(db)
        run_at = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
        db.add(
            SearchQuery(
                project_id=project.id,
                database="openalex",
                query_string='("remote work") AND ("productivity")',
                filters={"year_from": 2015},
                run_at=run_at,
                n_results=120,
            )
        )
        db.commit()
        row = db.scalar(select(SearchQuery).where(SearchQuery.project_id == project.id))

    assert row.version == 1
    assert row.filters == {"year_from": 2015}
    assert row.n_results == 120
    assert row.run_at is not None
    assert row.search_id is not None


def test_a_search_that_has_not_run_has_no_run_time_or_count():
    with SessionLocal() as db:
        project = _project(db)
        db.add(SearchQuery(project_id=project.id, database="crossref", query_string='"x"'))
        db.commit()
        row = db.scalar(select(SearchQuery).where(SearchQuery.project_id == project.id))

    assert row.run_at is None
    assert row.n_results is None


def test_versions_of_one_search_are_unique_and_positive():
    with SessionLocal() as db:
        project = _project(db)
        search_id = uuid.uuid4()
        db.add(SearchQuery(project_id=project.id, search_id=search_id, database="openalex", query_string='"a"', version=1))
        db.add(SearchQuery(project_id=project.id, search_id=search_id, database="openalex", query_string='"a"', version=2))
        db.commit()

        db.add(SearchQuery(project_id=project.id, search_id=search_id, database="openalex", query_string='"a"', version=2))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        db.add(SearchQuery(project_id=project.id, database="openalex", query_string='"a"', version=0))
        with pytest.raises(IntegrityError):
            db.commit()


def test_negative_result_count_is_refused():
    with SessionLocal() as db:
        project = _project(db)
        db.add(SearchQuery(project_id=project.id, database="openalex", query_string='"a"', n_results=-1))
        with pytest.raises(IntegrityError):
            db.commit()
