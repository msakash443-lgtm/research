"""Keep M4.1 question links inside their project in metadata-created and migrated schemas."""

from sqlalchemy import DDL, event

from app.models import Hypothesis, ResearchQuestionGap


def _link_guard(table: str, left_table: str, left_column: str, right_table: str | None, right_column: str | None, message: str) -> None:
    right_check = "" if right_table is None else f"\n  OR NOT EXISTS (SELECT 1 FROM {right_table} WHERE id = NEW.{right_column} AND project_id = NEW.project_id)"
    sqlite = f"""
    CREATE TRIGGER {table}_same_project_{{event}}
    BEFORE {{event}} ON {table}
    WHEN NOT EXISTS (SELECT 1 FROM {left_table} WHERE id = NEW.{left_column} AND project_id = NEW.project_id){right_check}
    BEGIN SELECT RAISE(ABORT, '{message}'); END
    """
    postgres_body = f"""
    CREATE OR REPLACE FUNCTION {table}_same_project_guard() RETURNS trigger AS $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM {left_table} WHERE id = NEW.{left_column} AND project_id = NEW.project_id){right_check.replace('OR NOT EXISTS', 'OR NOT EXISTS')} THEN
            RAISE EXCEPTION '{message}';
        END IF;
        RETURN NEW;
    END; $$ LANGUAGE plpgsql
    """
    for operation in ("insert", "update"):
        event.listen(ResearchQuestionGap.__table__ if table == "research_question_gaps" else Hypothesis.__table__, "after_create", DDL(sqlite.format(event=operation)).execute_if(dialect="sqlite"))
    target = ResearchQuestionGap.__table__ if table == "research_question_gaps" else Hypothesis.__table__
    event.listen(target, "after_create", DDL(postgres_body).execute_if(dialect="postgresql"))
    event.listen(target, "after_create", DDL(f"CREATE TRIGGER {table}_same_project BEFORE INSERT OR UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION {table}_same_project_guard()").execute_if(dialect="postgresql"))


_link_guard("research_question_gaps", "research_questions", "research_question_id", "gaps", "gap_id", "research question gap link must belong to one project")
_link_guard("hypotheses", "research_questions", "research_question_id", None, None, "hypothesis must belong to its research question project")
