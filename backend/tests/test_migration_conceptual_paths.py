import uuid

import pytest
import sqlalchemy as sa
from alembic import command


def test_conceptual_paths_downgrade_refuses_to_drop_researcher_paths(migrator):
    """A downgrade must not silently destroy person-drawn conceptual paths (M4.2 review)."""
    config, engine = migrator
    command.upgrade(config, "20261015_0065")
    owner, project, first, second = (uuid.uuid4() for _ in range(4))
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO users (id, email) VALUES (:id, 'owner@example.test')"), {"id": owner})
        connection.execute(
            sa.text("INSERT INTO projects (id, owner_id, title) VALUES (:id, :owner, 'Paths')"),
            {"id": project, "owner": owner},
        )
        for construct_id, name in ((first, "a"), (second, "b")):
            connection.execute(
                sa.text(
                    "INSERT INTO constructs (id, project_id, name, definition, measures, author, "
                    "jingle_jangle_flags, created_by) "
                    "VALUES (:id, :project, :name, 'def', '[]', 'Author', '[]', 'alice')"
                ),
                {"id": construct_id, "project": project, "name": name},
            )
        connection.execute(
            sa.text(
                "INSERT INTO conceptual_paths (id, project_id, from_construct_id, to_construct_id, kind, "
                "source_ids, rationale, created_by) "
                "VALUES (:id, :project, :first, :second, 'direct', '[]', 'why', 'alice')"
            ),
            {"id": uuid.uuid4(), "project": project, "first": first, "second": second},
        )

    with pytest.raises(RuntimeError, match="conceptual_paths still has rows"):
        command.downgrade(config, "20261015_0064")
    assert "conceptual_paths" in sa.inspect(engine).get_table_names()

    with engine.begin() as connection:
        connection.execute(sa.text("DELETE FROM conceptual_paths"))
    command.downgrade(config, "20261015_0064")
    assert "conceptual_paths" not in sa.inspect(engine).get_table_names()
