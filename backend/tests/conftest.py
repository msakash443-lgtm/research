import os

# Set this before importing app modules. Tests must never reuse a developer's
# DATABASE_URL from .env, because the fixture below drops all test tables.
os.environ["DATABASE_URL"] = "sqlite+pysqlite://"
os.environ["ENVIRONMENT"] = "development"
os.environ["AUTO_CREATE_SCHEMA"] = "true"
os.environ["RUN_RESEARCH_INLINE"] = "true"
os.environ["SESSION_SECRET"] = "test-session-secret-that-is-long-enough"

import pytest

from app.database import Base, engine


@pytest.fixture(autouse=True)
def isolated_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
