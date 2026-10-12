"""Research OS V10, increment A1 (DESIGN.md 14.2): the 「源自」 record -- a
`derives_from` entry in `.researchos/mappings.toml` from the child attempt to
the attempt it grew out of. Its write-time rules (both ends in the index, no
line to itself, no duplicate, no circle; several parents allowed), the
circle and the waiting entry when read back, the index migration, and the
endpoints. Every test names its scenario."""

from __future__ import annotations

import threading
import urllib.parse

import pytest

from researchos import cli, db
from researchos.ingest import mappings as mappings_ingest
from researchos.records.situation import write_guard
from researchos.webapp import server

from test_v8_phase2 import _call, _conn, _project, _scan

A16, A17, A18 = "attempt:map.md#16", "attempt:map.md#17", "attempt:map.md#18"


def _map(numbers=("16", "17", "18")) -> str:
    rows = "".join(f"| {n} | 07-{n} | Route {n} | V | r{n} | 🕒 |\n" for n in numbers)
    return ("# Map\n\n## Attempt timeline\n\n| # | Date | Path | Variables | Result | Verdict |\n"
            "|---|---|---|---|---|---|\n" + rows)


@pytest.fixture
def root(tmp_path):
    root = _project(tmp_path / "proj")
    (root / "map.md").write_text(_map())
    _scan(root)
    return root


def _lines(root) -> set[tuple[str, str]]:
    conn = _conn(root)
    try:
        return {(e["src"], e["dst"]) for e in db.query_edges(conn, type="derives_from")
                if e["extractor"] == "mapping" and e["status"] == "confirmed"}
    finally:
        conn.close()


def _refused(root, frm, to) -> str:
    before = mappings_ingest.mappings_path(root).read_bytes() if mappings_ingest.mappings_path(root).exists() else None
    with pytest.raises(mappings_ingest.MappingsWriteError) as err:
        mappings_ingest.add_mapping(root, frm, to, "derives_from")
    after = mappings_ingest.mappings_path(root).read_bytes() if mappings_ingest.mappings_path(root).exists() else None
    assert after == before  # nothing written
    return err.value.code


def test_14_2_a_line_is_written_child_first_and_becomes_a_confirmed_human_line(root):
    map_before = (root / "map.md").read_bytes()
    result = mappings_ingest.add_mapping(root, A17, "map.md#16", "derives_from", note="换成事件轴")
    assert result["entry"] == {"from": A17, "to": A16, "type": "derives_from", "note": "换成事件轴",
                               "date": result["entry"]["date"]}
    text = mappings_ingest.mappings_path(root).read_text()
    assert 'from = "attempt:map.md#17"' in text and 'to   = "attempt:map.md#16"' in text
    _scan(root)
    assert _lines(root) == {(A17, A16)}
    assert (root / "map.md").read_bytes() == map_before  # the map file is never the record of a line
    loaded = mappings_ingest.load_mappings(root)
    assert [m.key for m in loaded.mappings] == [("map.md#17", "map.md#16", "derives_from")]
    assert loaded.problems == []


def test_14_2_each_write_rule_refuses_with_its_own_code_and_several_parents_are_allowed(root):
    assert _refused(root, A17, "attempt:map.md#99") == "attempt_not_found"
    assert _refused(root, "attempt:map.md#99", A16) == "attempt_not_found"
    assert _refused(root, A17, A17) == "self_link"
    assert _refused(root, "attempt:map.md#17", "map.md#17") == "self_link"
    mappings_ingest.add_mapping(root, A17, A16, "derives_from")
    assert _refused(root, A17, A16) == "duplicate"
    assert _refused(root, A16, A17) == "circle"
    mappings_ingest.add_mapping(root, A18, A17, "derives_from")
    assert _refused(root, A16, A18) == "circle"  # through #17
    mappings_ingest.add_mapping(root, A18, A16, "derives_from")  # a second parent: no circle
    _scan(root)
    assert _lines(root) == {(A17, A16), (A18, A17), (A18, A16)}
    with pytest.raises(mappings_ingest.MappingsWriteError) as err:
        mappings_ingest.add_mapping(root, "steps/16-build.py", A16, "derives_from")
    assert err.value.code == "bad_attempt"


def test_14_2_the_command_line_draws_and_removes_a_line(root, capsys):
    assert cli.main(["link", A17, A16, "--type", "derives_from", "--note", "n", str(root)]) == 0
    assert _lines(root) == {(A17, A16)}
    capsys.readouterr()
    assert cli.main(["link", A16, A17, "--type", "derives_from", str(root)]) == 1
    assert "not written (circle)" in capsys.readouterr().err
    assert cli.main(["link", A17, A16, "--type", "derives_from", "--remove", str(root)]) == 0
    assert _lines(root) == set()
    assert "derives_from" not in mappings_ingest.mappings_path(root).read_text()


def test_14_2_read_back_a_circle_or_a_self_line_is_ignored_and_reported(root):
    """A hand edit (or two packets) can close a circle: in file order, the
    entry that closes it is ignored with its own code; the earlier ones stand."""
    mappings_ingest.mappings_path(root).write_text(
        '[[mapping]]\nfrom = "attempt:map.md#17"\nto = "attempt:map.md#16"\ntype = "derives_from"\n\n'
        '[[mapping]]\nfrom = "attempt:map.md#18"\nto = "attempt:map.md#17"\ntype = "derives_from"\n\n'
        '[[mapping]]\nfrom = "attempt:map.md#16"\nto = "attempt:map.md#18"\ntype = "derives_from"\n\n'
        '[[mapping]]\nfrom = "attempt:map.md#16"\nto = "attempt:map.md#16"\ntype = "derives_from"\n'
    )
    loaded = mappings_ingest.load_mappings(root)
    assert [(p.index, p.line, p.code) for p in loaded.problems] == [(3, 11, "circle"), (4, 16, "self_link")]
    conn = _conn(root)
    try:
        report = mappings_ingest.ingest_mappings(conn, root)
    finally:
        conn.close()
    assert report.counts["refused"] == 2 and report.counts["waiting"] == 0
    assert _lines(root) == {(A17, A16), (A18, A17)}
    # Removing the line the circle went through lets the ignored one stand.
    mappings_ingest.delete_mapping(root, A18, A17, "derives_from")
    _scan(root)
    assert _lines(root) == {(A17, A16), (A16, A18)}


def test_14_2_a_line_whose_end_is_gone_waits_in_the_file_and_comes_back_with_the_row(root, capsys):
    """A renumbered or deleted row: the line leaves the index, its entry
    stays in the file and waits -- reported as waiting, not refused, and
    neither `records --verify` nor `rebuild` fails on it -- and it returns
    when the row does. The file holds no `belongs_to`, so this is the
    `derives_from` entry's own way back."""
    mappings_ingest.add_mapping(root, A18, A17, "derives_from")
    mappings_ingest.add_mapping(root, A17, A16, "derives_from")
    _scan(root)
    assert _lines(root) == {(A18, A17), (A17, A16)}
    (root / "map.md").write_text(_map(("16", "17")))
    assert cli.main(["attempts", str(root)]) == 0
    assert _lines(root) == {(A17, A16)}
    assert "attempt:map.md#18" in mappings_ingest.mappings_path(root).read_text()
    conn = _conn(root)
    try:
        report = mappings_ingest.ingest_mappings(conn, root)
    finally:
        conn.close()
    assert report.counts["waiting"] == 1 and report.counts["refused"] == 0
    (waiting,) = report.problems
    assert waiting.code == "attempt_not_found" and waiting.waiting and A18 in waiting.message
    capsys.readouterr()
    assert cli.main(["mappings", str(root)]) == 0
    out = capsys.readouterr().out
    assert "waiting=1" in out and "  waiting line" in out and "  refused" not in out
    assert cli.main(["records", "--verify", str(root)]) == 0
    assert cli.main(["rebuild", str(root)]) == 0
    assert "Not swapped" not in capsys.readouterr().out
    # The rebuilt index has the standing line, though mappings are read before attempts.
    assert _lines(root) == {(A17, A16)}
    (root / "map.md").write_text(_map())
    assert cli.main(["attempts", str(root)]) == 0
    assert _lines(root) == {(A18, A17), (A17, A16)}


def test_14_2_an_index_from_before_the_type_upgrades_on_the_first_line_read(tmp_path, monkeypatch):
    """The researcher's index ran migrations 0001-0007 only: reading the
    first line migrates it (under the project lock, as the ingest runs),
    and the line lands."""
    upto_7 = tmp_path / "upto_7"
    upto_7.mkdir()
    for path in sorted(db.DEFAULT_MIGRATIONS_DIR.glob("*.sql")):
        if int(path.stem.split("_", 1)[0]) <= 7:
            (upto_7 / path.name).write_text(path.read_text())
    real = db.DEFAULT_MIGRATIONS_DIR
    monkeypatch.setattr(db, "DEFAULT_MIGRATIONS_DIR", upto_7)
    root = _project(tmp_path / "proj")
    (root / "map.md").write_text(_map())
    _scan(root)
    monkeypatch.setattr(db, "DEFAULT_MIGRATIONS_DIR", real)
    conn = _conn(root)
    try:
        assert not db.has_derives_from(conn)
        edges_before = len(db.query_edges(conn))
    finally:
        conn.close()
    mappings_ingest.add_mapping(root, A17, A16, "derives_from")
    with write_guard(root):
        conn = _conn(root)
        try:
            report = mappings_ingest.ingest_mappings(conn, root)
            assert db.has_derives_from(conn)
            assert len(db.query_edges(conn)) == edges_before + 1
        finally:
            conn.close()
    assert report.problems == [] and report.counts["mappings"] == 1
    assert _lines(root) == {(A17, A16)}


def test_14_2_a_members_line_never_travels_in_a_packet(root):
    """14.2: a member's 「源自」 goes as a proposal the PI approves; the
    no-approval packets of 12.12 neither carry one nor apply one."""
    base = None
    mine = ('[[mapping]]\nfrom = "attempt:map.md#17"\nto = "attempt:map.md#16"\ntype = "derives_from"\nby = "m@x.org"\n\n'
            '[[mapping]]\nfrom = "figs/fig1.png"\nto = "attempt:map.md#16"\ntype = "belongs_to"\nby = "m@x.org"\n')
    added, removed = mappings_ingest.member_changes(root, base, mine, "m@x.org")
    assert [a["type"] for a in added] == ["belongs_to"] and removed == []
    added, removed = mappings_ingest.member_changes(root, mine, "", "m@x.org")
    assert [r["type"] for r in removed] == ["belongs_to"]
    out = mappings_ingest.apply_received(
        root, "n-1", "m@x.org",
        [{"from": A17, "to": A16, "type": "derives_from", "by": "m@x.org"}],
        [{"from": A18, "to": A17, "type": "derives_from", "by": "m@x.org"}],
        at="2026-10-12T10:00:00Z",
    )
    assert out["added"] == 0 and out["removed"] == 0 and len(out["problems"]) == 2
    assert all("proposal" in p for p in out["problems"])
    assert "derives_from" not in mappings_ingest.mappings_path(root).read_text()


# -- the endpoints ------------------------------------------------------------------------------------------


@pytest.fixture
def live(root):
    httpd = server.build_server(root, 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", root
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_14_2_the_endpoints_add_and_delete_a_line_and_name_each_refusal(live):
    base, root = live
    status, body = _call(f"{base}/api/mappings/add", "POST",
                         {"from": A17, "to": A16, "type": "derives_from", "note": "换轴"})
    assert status == 200 and body["ok"] and body["ingest_error"] is None
    assert body["entry"]["from"] == A17 and body["entry"]["to"] == A16
    link = body["link"]
    assert (link["src"], link["dst"], link["type"], link["human"], link["note"]) == (A17, A16, "derives_from", True, "换轴")
    for request, code, state in (
        ({"from": A17, "to": A16}, 409, "mapping_exists"),
        ({"from": A16, "to": A17}, 400, "mapping_circle"),
        ({"from": A16, "to": A16}, 400, "mapping_self_link"),
        ({"from": A16, "to": "attempt:map.md#99"}, 400, "mapping_attempt_not_found"),
    ):
        status, body = _call(f"{base}/api/mappings/add", "POST", {**request, "type": "derives_from"})
        assert (status, body.get("state")) == (code, state), body
    status, page = _call(f"{base}/api/node?node={urllib.parse.quote(A17)}")
    (line,) = [l for l in page["links"] if l["type"] == "derives_from"]
    assert (line["relation"], line["direction"], line["human"]) == ("源自", "out", True)
    assert line["mapping"]["note"] == "换轴"
    status, body = _call(f"{base}/api/mappings/delete", "POST", {"from": A17, "to": A16, "type": "derives_from"})
    assert status == 200 and body["removed"] == 1
    assert _lines(root) == set()
    status, body = _call(f"{base}/api/mappings/delete", "POST", {"from": A17, "to": A16, "type": "derives_from"})
    assert status == 404 and body["error"]
