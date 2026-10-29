"""A throwaway attempt for the tests that create tasks.

DESIGN.md 21.1: a new task belongs to an attempt of the project, so a test that only needs *a* task puts one attempt
row into the project's graph (the index `workspace._attempts` reads) and names it. The row is not a table the project
has: nothing here reads or writes an attempt file."""

from __future__ import annotations

from pathlib import Path

from researchos import db, paths

TABLE = "research/attempts.md"


def seed_attempt(root: str | Path, number: str = "1", description: str = "A test attempt") -> str:
    """Make attempt `number` exist for `root`'s project and return its node id (`attempt:research/attempts.md#1`)."""
    node_id = f"attempt:{TABLE}#{number}"
    path = paths.graph_db_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = db.connect(path)
    try:
        db.migrate(conn)
        db.upsert_node(conn, node_id, "attempt", title=description,
                       attrs={"number": number, "description": description, "source_file": TABLE})
    finally:
        conn.close()
    return node_id
