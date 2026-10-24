import sqlite3
from pathlib import Path

import pytest

from researchos import db, names


@pytest.fixture
def conn() -> sqlite3.Connection:
    """A fresh in-memory database with the schema fully migrated."""
    connection = db.connect(":memory:")
    db.migrate(connection)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture(autouse=True)
def isolated_researchos_home(tmp_path_factory, monkeypatch) -> Path:
    """Point `RESEARCHOS_HOME` at a throwaway directory for EVERY test in the
    suite (DESIGN.md section 8.10 rule 1).

    Since the graph moved out of the project to `~/.researchos/graphs/<id>/`, a
    test that runs `researchos init` writes into the Research OS home -- so without this,
    the suite would create (and later read) real graphs under the
    developer's own `~/.researchos`, and one test's project path could collide
    with another's across runs. Autouse rather than opt-in precisely
    because forgetting it is silent: the test would still pass, against
    the wrong filesystem.

    `researchos.paths.researchos_home()` reads the variable on every call, so this takes
    effect for modules imported long before the fixture ran. Tests that
    need the registry in a specific place (the `fake_home` fixtures) set
    `RESEARCHOS_HOME` themselves; their `monkeypatch.setenv` runs after this
    autouse one and wins.

    A sibling of `tmp_path`, never inside it (DESIGN.md 9.7): so many
    tests use `tmp_path` itself as the project root, and the project
    lock refuses an `RESEARCHOS_HOME` that sits inside the project it locks.
    """
    home = tmp_path_factory.mktemp("researchos-home")
    monkeypatch.setenv(names.HOME_ENV_VAR, str(home))
    # The old variable (12.8) never leaks in from the developer's shell.
    monkeypatch.delenv(names.LEGACY_HOME_ENV_VAR, raising=False)
    return home


@pytest.fixture(autouse=True)
def no_real_zotero(tmp_path_factory, monkeypatch) -> Path:
    """Every scan reads a Zotero library (DESIGN.md 11.4); no test may read
    the developer's own. `RCE_ZOTERO_DATA_DIR` points at an empty folder
    (an absent library) unless a test sets its own."""
    empty = tmp_path_factory.mktemp("zotero-none")
    monkeypatch.setenv("RCE_ZOTERO_DATA_DIR", str(empty))
    return empty


@pytest.fixture(autouse=True)
def no_engine_probe(monkeypatch) -> None:
    """The migration's retire step asks whether an Research OS engine answers on
    the app's fixed port (DESIGN.md 9.5 step 5). No test may ever touch a
    real engine on this machine, so the probe is off (`RCE_ENGINE_PORT=0`)
    for every test; the tests of the probe itself bind their own port."""
    monkeypatch.setenv("RCE_ENGINE_PORT", "0")


@pytest.fixture(autouse=True)
def no_real_doi_lookup(monkeypatch) -> None:
    """The opt-in DOI lookup (DESIGN.md 11.4) talks to Crossref through
    `researchos.literature._OPENER`; no test may reach it. A test that needs the
    opener's behaviour builds its own against a local stand-in."""
    from researchos import literature

    class _NoNetwork:
        def open(self, request, timeout=None):  # noqa: ANN001
            raise AssertionError(f"a test tried to reach the network: {request.full_url}")

    monkeypatch.setattr(literature, "_OPENER", _NoNetwork())
    monkeypatch.setattr(literature, "_OPENALEX_OPENER", _NoNetwork())
