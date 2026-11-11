"""Enforce that theory-to-construct links stay inside one project."""

from sqlalchemy import DDL, event

from app.models import TheoryConstruct

MESSAGE = "theory construct link must belong to one project"

POSTGRES_FUNCTION = f"""
CREATE OR REPLACE FUNCTION theory_constructs_same_project_guard() RETURNS trigger AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM theories WHERE id = NEW.theory_id AND project_id = NEW.project_id)
       OR NOT EXISTS (SELECT 1 FROM constructs WHERE id = NEW.construct_id AND project_id = NEW.project_id) THEN
        RAISE EXCEPTION '{MESSAGE}';
    END IF;
    RETURN NEW;
END; $$ LANGUAGE plpgsql
"""

POSTGRES_TRIGGER = "CREATE TRIGGER theory_constructs_same_project BEFORE INSERT OR UPDATE ON theory_constructs FOR EACH ROW EXECUTE FUNCTION theory_constructs_same_project_guard()"

event.listen(TheoryConstruct.__table__, "after_create", DDL(POSTGRES_FUNCTION).execute_if(dialect="postgresql"))
event.listen(TheoryConstruct.__table__, "after_create", DDL(POSTGRES_TRIGGER).execute_if(dialect="postgresql"))
