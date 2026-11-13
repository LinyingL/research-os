"""DESIGN.md 22.2 (task V17, B1d): passages of a `.tex` tied to their supporting files, the engine side. The ledger
`.researchos/evidence.toml` (round trip with a CRLF draft, Chinese and an emoji; anchors `\\n`-normalised; offsets in
code points), finding a passage again (every branch), the acts (link, reanchor, withdraw, undo), the trust rules, a
member refused until B4, reading writes nothing, and the endpoints. Every test names its scenario."""

from __future__ import annotations

import hashlib
import http.client
import json
import threading
import time
import tomllib
from pathlib import Path

import pytest

from researchos import latex_edit, paths, people, project, proposals, share
from researchos.records import evidence
from researchos.records import files as record_files
from researchos.webapp import server, watcher

DRAFT = "\\section{引言}\n人民币国际化的进展 😀 很慢。\n\n第二段 $x^2$ 结束。\n\n\\section{数据}\n我们用 BIS 的数据。\n"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    project.init_project(root)
    (root / "figures").mkdir()
    (root / "figures/f1.png").write_bytes(b"png")
    return root


def _put(root: Path, rel: str, text: str, ending: str = "\n") -> str:
    data = text.replace("\n", ending).encode("utf-8")
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _range(root: Path, rel: str, words: str) -> tuple[int, int]:
    """The editor's positions (code points, a CRLF counted as two) of `words` in the file as it is."""
    text = (root / rel).read_bytes().decode("utf-8")
    start = text.index(words.replace("\n", "\r\n") if "\r\n" in text else words)
    return start, start + len(words) + (words.count("\n") if "\r\n" in text else 0)


def _link(root: Path, rel: str, words: str, file: str = "figures/f1.png", **extra) -> dict:
    start, end = _range(root, rel, words)
    revision = hashlib.sha256((root / rel).read_bytes()).hexdigest()
    return latex_edit.evidence_act(root, "link", {"path": rel, "revision": revision, "from": start, "to": end,
                                                  "file": file, **extra})


def _links(root: Path, rel: str = "paper/main.tex") -> list[dict]:
    return latex_edit.evidence_of_draft(root, rel)["links"]


def _refused(code: str, action) -> latex_edit.WorkspaceError:
    with pytest.raises(latex_edit.WorkspaceError) as caught:
        action()
    assert caught.value.code == code, (caught.value.code, str(caught.value))
    return caught.value


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


# -- the ledger --------------------------------------------------------------------------------------------------


def test_a_link_in_a_crlf_draft_round_trips_with_its_anchor_written_with_line_feeds_and_code_point_offsets(root):
    """22.2: quote, prefix and suffix store every line ending as \\n (no \\r reaches the file), the offset counts code
    points of that normalised text (the emoji is one), and the link is found again at the editor's positions."""
    _put(root, "paper/main.tex", DRAFT, "\r\n")
    words = "很慢。\n\n第二段"
    done = _link(root, "paper/main.tex", words, note="见图 1\n第二行")
    assert done["act"] == "link" and done["link"].startswith("pl-") and done["entry"].startswith("pe-")
    raw = evidence.evidence_path(root).read_bytes()
    assert b"\r" not in raw and b"[passage_evidence.anchor]" in raw
    entry = tomllib.loads(raw.decode("utf-8"))["passage_evidence"][0]
    norm = DRAFT
    assert entry["anchor"] == {"quote": words, "prefix": norm[max(0, norm.index(words) - 32):norm.index(words)],
                               "suffix": norm[norm.index(words) + len(words):][:32], "offset": norm.index(words)}
    assert entry["file"] == "figure:figures/f1.png" and entry["draft"] == "paper/main.tex" and entry["via"] == "app"
    assert entry["note"] == "见图 1 第二行"
    assert len("人民币国际化的进展 😀 ") == 12 and entry["anchor"]["offset"] == len("\\section{引言}\n") + 12
    (shown,) = _links(root)
    assert shown["state"] == "found" and (shown["from"], shown["to"]) == _range(root, "paper/main.tex", words)
    assert shown["file_missing"] is False and shown["last"] == done["entry"] and shown["label"]


def test_the_ledger_refuses_an_act_before_its_link_and_a_second_link_act_for_one_key(root):
    path = evidence.evidence_path(root)
    anchor = '[passage_evidence.anchor]\nquote = "a"\nprefix = ""\nsuffix = ""\noffset = 0\n'
    path.write_text(f'[[passage_evidence]]\nid = "pe-1"\nseq = 1\nact = "reanchor"\nlink = "pl-1"\n{anchor}')
    assert evidence.load(root).state.value == "invalid"
    link = '[[passage_evidence]]\nid = "pe-{n}"\nseq = {n}\nact = "link"\nlink = "pl-1"\ndraft = "a.tex"\nfile = "file:a.md"\n'
    path.write_text(link.format(n=1) + anchor + link.format(n=2) + anchor)
    assert evidence.load(root).state.value == "invalid"
    path.write_text(link.format(n=1) + anchor.replace('"a"', '"a\\r"'))
    assert evidence.load(root).state.value == "invalid"


# -- finding a passage again (pure) ----------------------------------------------------------------------------------


def _anchor(text: str, words: str, at: int | None = None) -> dict:
    start = text.index(words) if at is None else at
    return {"quote": words, "prefix": text[max(0, start - 32):start], "suffix": text[start + len(words):][:32],
            "offset": start}


def test_a_quote_found_once_is_found_wherever_its_paragraph_moved():
    text = "甲段落。\n\n乙段落，有依据。\n\n丙段落。\n"
    anchor = _anchor(text, "乙段落，有依据。")
    moved = "乙段落，有依据。\n\n甲段落。\n\n丙段落。\n"
    assert evidence.resolve(anchor, moved) == {"state": "found", "start": 0, "end": 8, "candidates": []}


def test_of_several_occurrences_the_one_whose_context_matches_wins_then_the_nearest_offset():
    text = "A: the effect is large.\nB: the effect is large.\nC: the effect is large.\n"
    second = text.index("the effect", 10)
    anchor = _anchor(text, "the effect is large.", second)
    assert evidence.resolve(anchor, text)["start"] == second
    nowhere = {**anchor, "prefix": "zz", "suffix": "yy", "offset": len(text)}
    assert evidence.resolve(nowhere, text)["start"] == text.rindex("the effect")


def test_reworded_words_are_changed_with_the_text_between_the_same_context_first():
    text = "前文：汇率波动加大。\n\n结果显著，系数为 0.3。\n\n后文：稳健性检验。\n"
    anchor = _anchor(text, "结果显著，系数为 0.3。")
    now = text.replace("结果显著，系数为 0.3。", "结果不显著，系数为 0.1。")
    found = evidence.resolve(anchor, now)
    assert found["state"] == "changed" and found["start"] is None
    assert found["candidates"][0]["text"] == "结果不显著，系数为 0.1。"
    assert len(found["candidates"]) <= evidence.CANDIDATES


def test_without_its_context_a_changed_passage_offers_the_most_similar_paragraphs_by_shared_characters():
    anchor = {"quote": "人民币国际化进展缓慢", "prefix": "已删的前文", "suffix": "已删的后文", "offset": 5}
    text = "完全无关的一段话。\n\n人民币的国际化进展仍然缓慢。\n\nThe exchange rate moved.\n\n国际化\n"
    found = evidence.resolve(anchor, text)
    assert found["state"] == "changed"
    assert [c["text"] for c in found["candidates"]][:2] == ["人民币的国际化进展仍然缓慢。", "国际化"]
    assert all(text[c["start"]:c["end"]] == c["text"] for c in found["candidates"])
    english = {"quote": "the exchange rate moved a lot", "prefix": "x", "suffix": "y", "offset": 0}
    assert evidence.resolve(english, text)["candidates"][0]["text"] == "The exchange rate moved."


def test_a_passage_whose_every_character_was_deleted_is_deleted_not_changed():
    text = "第一段。\n\n要删掉的这一段。\n\n第三段。\n"
    anchor = _anchor(text, "要删掉的这一段。")
    assert evidence.resolve(anchor, text.replace("要删掉的这一段。", ""))["state"] == "deleted"
    first = _anchor(text, "第一段。")
    assert first["prefix"] == "" and evidence.resolve(first, text.replace("第一段。", " "))["state"] == "deleted"


def test_a_paragraph_deleted_with_its_blank_line_or_a_line_with_its_line_ending_is_deleted():
    """Review of B1d: deleting as an editor does takes blank space of the context with the passage; it is still
    「段落已被删掉」, not 「依据已变化」 with unrelated paragraphs offered."""
    text = "\\section{结果}\n系数为正，结果显著。\n\n这一段讲别的东西，和数据有关。\n\n\\section{数据}\n我们用 BIS 的数据。\n"
    paragraph = _anchor(text, "这一段讲别的东西，和数据有关。")
    found = evidence.resolve(paragraph, text.replace("这一段讲别的东西，和数据有关。\n\n", ""))
    assert found == {"state": "deleted", "start": text.index("\n\n这一段"), "end": text.index("这一段"), "candidates": []}
    line = _anchor(text, "系数为正，结果显著。")
    assert evidence.resolve(line, text.replace("系数为正，结果显著。\n", ""))["state"] == "deleted"
    last = _anchor(text, "我们用 BIS 的数据。")
    assert last["suffix"] == "\n" and evidence.resolve(last, text.replace("我们用 BIS 的数据。\n", ""))["state"] == "deleted"
    assert evidence.resolve(line, text.replace("系数为正，结果显著。", "另一句话。"))["state"] == "changed"


def test_finding_a_passage_in_a_large_repetitive_generated_table_takes_well_under_a_second():
    """Review of B1d: a prefix found 60,000 times and a suffix that has gone once cost 17 s per link on a GET."""
    row = "1.23 & 4.56 & 7.89 \\\\ \\hline\n"
    text = row * 60_000
    anchor = {"quote": "a row that has gone", "prefix": (row * 3)[-32:], "suffix": "a suffix that has gone too",
              "offset": 10}
    began = time.monotonic()
    assert evidence.resolve(anchor, text)["state"] == "changed"
    assert time.monotonic() - began < 2


def test_no_candidate_is_longer_than_a_passage_may_be():
    """Review of B1d: the text between a surviving prefix and a far later suffix could run to megabytes, and 「是这段」
    on it would be refused anyway."""
    filler = "填充文字。" * (evidence.MAX_QUOTE // 4)
    text = "前文开头。旧的句子。" + filler + "后文结尾。"
    anchor = {"quote": "已经改掉的句子", "prefix": "前文开头。", "suffix": "后文结尾。", "offset": 5}
    found = evidence.resolve(anchor, text)
    assert found["state"] == "changed"
    assert all(len(c["text"]) <= evidence.MAX_QUOTE for c in found["candidates"])
    near = evidence.resolve(anchor, "前文开头。新的句子。后文结尾。")
    assert near["candidates"][0]["text"] == "新的句子。"


# -- acts -------------------------------------------------------------------------------------------------------------


def test_a_link_survives_saves_before_and_after_it_with_no_ledger_entry_written(root):
    """Acceptance 2: edits before and after the passage, through the editor's own save, leave the ledger untouched and
    the passage found."""
    revision = _put(root, "paper/main.tex", DRAFT, "\r\n")
    _link(root, "paper/main.tex", "我们用 BIS 的数据。")
    ledger_bytes = evidence.evidence_path(root).read_bytes()
    saved = latex_edit.save_tex(root, {"path": "paper/main.tex", "revision": revision,
                                       "edits": [{"from": 0, "to": 0, "insert": "% 新的开头\r\n"}]})
    end = len((root / "paper/main.tex").read_bytes().decode())
    latex_edit.save_tex(root, {"path": "paper/main.tex", "revision": saved["revision"],
                               "edits": [{"from": end, "to": end, "insert": "结尾。\r\n"}]})
    (shown,) = _links(root)
    assert shown["state"] == "found" and (shown["from"], shown["to"]) == _range(root, "paper/main.tex", "我们用 BIS 的数据。")
    assert evidence.evidence_path(root).read_bytes() == ledger_bytes


def test_a_reworded_passage_waits_until_one_reanchor_and_a_stale_or_foreign_draft_is_refused(root):
    _put(root, "paper/main.tex", DRAFT)
    _put(root, "paper/other.tex", DRAFT)
    link = _link(root, "paper/main.tex", "第二段 $x^2$ 结束。")["link"]
    revision = _put(root, "paper/main.tex", DRAFT.replace("第二段 $x^2$ 结束。", "第二段 $x^3$ 结束。"))
    (shown,) = _links(root)
    assert shown["state"] == "changed" and shown["candidates"][0]["text"] == "第二段 $x^3$ 结束。"
    candidate = shown["candidates"][0]
    _refused("conflict", lambda: latex_edit.evidence_act(root, "reanchor", {
        "link": link, "path": "paper/main.tex", "revision": "0" * 64, "from": candidate["from"], "to": candidate["to"]}))
    other = hashlib.sha256((root / "paper/other.tex").read_bytes()).hexdigest()
    _refused("invalid_request", lambda: latex_edit.evidence_act(root, "reanchor", {
        "link": link, "path": "paper/other.tex", "revision": other, "from": 0, "to": 3}))
    before = evidence.load(root).ledger.entries
    done = latex_edit.evidence_act(root, "reanchor", {"link": link, "path": "paper/main.tex", "revision": revision,
                                                      "from": candidate["from"], "to": candidate["to"]})
    assert done["act"] == "reanchor" and len(evidence.load(root).ledger.entries) == len(before) + 1
    assert _links(root)[0]["state"] == "found"


def test_withdraw_and_undo_take_back_the_last_act_only(root):
    _put(root, "paper/main.tex", DRAFT)
    made = _link(root, "paper/main.tex", "我们用 BIS 的数据。")
    gone = latex_edit.evidence_act(root, "withdraw", {"link": made["link"]})
    assert _links(root) == []
    _refused("withdrawn", lambda: latex_edit.evidence_act(root, "withdraw", {"link": made["link"]}))
    _refused("not_written", lambda: latex_edit.evidence_act(root, "undo", {"link": made["link"], "undoes": made["entry"]}))
    latex_edit.evidence_act(root, "undo", {"link": made["link"], "undoes": gone["entry"]})
    assert [s["link"] for s in _links(root)] == [made["link"]]
    latex_edit.evidence_act(root, "undo", {"link": made["link"], "undoes": made["entry"]})
    assert _links(root) == []
    _refused("no_such_link", lambda: latex_edit.evidence_act(root, "withdraw", {"link": "pl-nothing"}))
    _refused("no_such_link", lambda: latex_edit.evidence_act(root, "withdraw", {"link": ["pl-x"]}))
    _refused("invalid_request", lambda: latex_edit.evidence_act(root, "undo", {"link": made["link"], "undoes": 3}))


def test_a_supporting_file_that_has_gone_is_shown_as_gone_and_not_withdrawn(root):
    _put(root, "paper/main.tex", DRAFT)
    _link(root, "paper/main.tex", "我们用 BIS 的数据。")
    (root / "figures/f1.png").unlink()
    (shown,) = _links(root)
    assert shown["file_missing"] is True and shown["state"] == "found"


def test_a_link_needs_a_real_passage_of_a_readable_draft_and_a_file_of_the_project(root):
    revision = _put(root, "paper/main.tex", DRAFT, "\r\n")
    act = lambda **body: latex_edit.evidence_act(root, "link", {"path": "paper/main.tex", "revision": revision,
                                                                "file": "figures/f1.png", **body})
    crlf = (root / "paper/main.tex").read_bytes().decode().index("\r\n")
    _refused("invalid", lambda: act(**{"from": crlf + 1, "to": crlf + 5}))
    _refused("invalid", lambda: act(**{"from": crlf, "to": crlf + 2}))
    _refused("invalid", lambda: act(**{"from": 5, "to": 5}))
    _refused("invalid", lambda: act(**{"from": True, "to": 5}))
    _refused("no_such_file", lambda: act(**{"from": 0, "to": 5, "file": "figures/none.png"}))
    _put(root, ".hidden/x.png", "x")
    _refused("no_such_file", lambda: act(**{"from": 0, "to": 5, "file": ".hidden/x.png"}))
    _refused("invalid_request", lambda: act(**{"from": 0, "to": 5, "file": "../outside.png"}))
    _refused("path", lambda: latex_edit.evidence_act(root, "link", {"path": "../x.tex", "revision": revision}))
    (root / "paper/mixed.tex").write_bytes(b"a\r\nb\nc\n")
    mixed = hashlib.sha256(b"a\r\nb\nc\n").hexdigest()
    _refused("read_only_file", lambda: latex_edit.evidence_act(root, "link", {
        "path": "paper/mixed.tex", "revision": mixed, "from": 0, "to": 1, "file": "figures/f1.png"}))
    _refused("invalid", lambda: latex_edit.evidence_act(root, "rename", {}))
    assert not evidence.evidence_path(root).exists()


def test_the_passages_a_file_supports_name_their_draft_section_and_first_80_characters(root):
    _put(root, "paper/main.tex", DRAFT)
    _link(root, "paper/main.tex", "我们用 BIS 的数据。")
    _link(root, "paper/main.tex", "第二段 $x^2$ 结束。", file="figure:figures/f1.png")
    _link(root, "paper/main.tex", "很慢。", file="paper/main.tex")
    got = latex_edit.passages_supported(root, "figures/f1.png")
    assert got["file"] == "figure:figures/f1.png" and got["trusted"] is True
    assert sorted((p["section"], p["excerpt"], p["state"]) for p in got["passages"]) == [
        ("引言", "第二段 $x^2$ 结束。", "found"), ("数据", "我们用 BIS 的数据。", "found")]
    (root / "paper/main.tex").unlink()
    assert {p["state"] for p in latex_edit.passages_supported(root, "figures/f1.png")["passages"]} == {"draft_missing"}


def test_a_supporting_file_is_stored_under_one_name_and_found_by_it_whatever_it_was_called(root):
    """Review of B1d: `figures/f1.png`, `file:…` and `dataset:…` named one file three ways and 以它为依据的段落 listed one
    link of three; a wrong-case spelling passed on a Mac volume; a claim or attempt id was written unchecked."""
    _put(root, "paper/main.tex", DRAFT)
    _link(root, "paper/main.tex", "很慢。", file="figures/f1.png")
    # review of B1e: the same passage to the same file again is refused, however the file is named
    for name in ("figures/f1.png", "file:figures/f1.png", "dataset:figures/f1.png"):
        assert _refused("already_linked", lambda: _link(root, "paper/main.tex", "很慢。", file=name)).status == 409
    _link(root, "paper/main.tex", "人民币国际化", file="dataset:figures/f1.png")
    assert {e.get("file") for e in evidence.load(root).ledger.entries} == {"figure:figures/f1.png"}
    for name in ("Figures/F1.PNG", "figures/F1.png", "claim:does-not-exist", "attempt:bogus"):
        _refused("no_such_file", lambda: _link(root, "paper/main.tex", "很慢。", file=name))
    for name in ("figures/f1.png", "file:figures/f1.png", "dataset:figures/f1.png"):
        got = latex_edit.passages_supported(root, name)
        assert got["file"] == "figure:figures/f1.png" and len(got["passages"]) == 2


def test_a_long_note_an_unencodable_note_and_a_page_break_in_the_passage_are_refused_with_a_reason(root):
    """Review of B1d: these came back as 409 「关联没有写入」 with no reason. A page break next to the passage only
    shortens its context."""
    _put(root, "paper/main.tex", "前文\f正文一段。\u2028后文\n分页\f符号\n")
    error = _refused("note_too_long", lambda: _link(root, "paper/main.tex", "正文一段。", note="字" * 2001))
    assert error.status == 400 and "2000" in error.message_zh
    assert _refused("invalid_request", lambda: _link(root, "paper/main.tex", "正文一段。", note="\ud800")).status == 400
    error = _refused("line_separator", lambda: _link(root, "paper/main.tex", "分页\f符号"))
    assert error.status == 400 and "分页符" in error.message_zh
    assert not evidence.evidence_path(root).exists()
    _link(root, "paper/main.tex", "正文一段。", note="字" * 2000)
    anchor = evidence.load(root).ledger.entries[0].data["anchor"]
    assert (anchor["prefix"], anchor["suffix"]) == ("", "")
    assert _links(root)[0]["state"] == "found"


# -- trust, who writes, reading ---------------------------------------------------------------------------------------


def test_an_untrusted_ledger_refuses_every_write_and_readers_list_no_link_with_the_reason(root):
    _put(root, "paper/main.tex", DRAFT)
    _link(root, "paper/main.tex", "我们用 BIS 的数据。")
    path = evidence.evidence_path(root)
    assert record_files.snapshots(root, path)  # founded: from now on it is expected
    good = path.read_bytes()
    path.write_bytes(good + b"\n[[passage_evidence]]\nnot valid")
    assert latex_edit.evidence_of_draft(root, "paper/main.tex")["links"] == []
    assert latex_edit.evidence_of_draft(root, "paper/main.tex", "en")["message"].startswith("The record of supporting")
    _refused("untrusted", lambda: _link(root, "paper/main.tex", "很慢。"))
    path.write_bytes(good)
    (path.parent / "evidence (1).toml").write_bytes(good)
    _refused("untrusted", lambda: _link(root, "paper/main.tex", "很慢。"))
    (path.parent / "evidence (1).toml").unlink()
    path.unlink()
    error = _refused("untrusted", lambda: _link(root, "paper/main.tex", "很慢。"))
    assert error.status == 409 and not path.exists()


def test_a_member_is_refused_until_approval_exists_and_nothing_is_written(root):
    _put(root, "paper/main.tex", DRAFT)
    people.team_path(root).write_text('[[member]]\nid="pi@example.com"\nname="PI"\nrole="pi"\n\n'
                                      '[[member]]\nid="member@example.com"\nname="Member"\nrole="member"\n')
    people.write_me("Member", "member@example.com")
    error = _refused("member", lambda: _link(root, "paper/main.tex", "很慢。"))
    assert error.status == 403 and "PI" in error.message_zh and not evidence.evidence_path(root).exists()
    people.write_me("PI", "pi@example.com")
    assert _link(root, "paper/main.tex", "很慢。")["act"] == "link"
    assert evidence.load(root).ledger.entries[0].get("by") == "pi@example.com"


def test_reading_writes_nothing(root):
    _put(root, "paper/main.tex", DRAFT)
    _link(root, "paper/main.tex", "我们用 BIS 的数据。")
    _put(root, "paper/main.tex", DRAFT.replace("BIS", "IMF"))
    before = _files(root)
    assert _links(root)[0]["state"] == "changed"
    latex_edit.passages_supported(root, "figures/f1.png")
    assert _files(root) == before


def test_the_record_travels_in_snapshots_and_the_watcher_refreshes_on_it(root):
    assert "evidence.toml" in share.RECORD_FILES and "evidence.toml" in proposals._RECORD_FILES
    assert "evidence.toml" not in share.TRAVELLING_FILES  # a member's links wait for B4's approval
    _put(root, "paper/main.tex", DRAFT)
    _link(root, "paper/main.tex", "很慢。")
    assert "evidence.toml" in paths.project_readme_text(root)
    assert str(evidence.evidence_path(root)) in watcher.take_snapshot(root).files


# -- the endpoints ------------------------------------------------------------------------------------------------------


@pytest.fixture
def served(root):
    httpd = server.build_server(root, 0)
    httpd.start_scan = lambda target: None
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1], root
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _call(port: int, method: str, path: str, body: dict | None = None, headers: dict | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        conn.request(method, path, body=None if body is None else json.dumps(body).encode(),
                     headers={"Content-Type": "application/json", **(headers or {})})
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read())
    finally:
        conn.close()


def test_the_endpoints_link_list_withdraw_and_refuse_another_project(served):
    port, root = served
    revision = _put(root, "paper/main.tex", DRAFT)
    start, end = _range(root, "paper/main.tex", "很慢。")
    body = {"project": str(root), "path": "paper/main.tex", "revision": revision, "from": start, "to": end,
            "file": "figures/f1.png"}
    status, made = _call(port, "POST", "/api/latex/evidence/link", body)
    assert status == 200 and made["ok"] and isinstance(made["generation"], int)
    status, listed = _call(port, "GET", "/api/latex/evidence?path=paper/main.tex")
    assert status == 200 and [(x["link"], x["from"], x["to"]) for x in listed["links"]] == [(made["link"], start, end)]
    status, supports = _call(port, "GET", "/api/latex/supports?file=figures/f1.png")
    assert status == 200 and supports["passages"][0]["link"] == made["link"]
    status, refused = _call(port, "POST", "/api/latex/evidence/withdraw", {"project": "/elsewhere", "link": made["link"]})
    assert status == 409 and refused["state"] == "workspace_project_changed"
    status, refused = _call(port, "POST", "/api/latex/evidence/withdraw", {"project": str(root), "link": "pl-x"},
                            {"X-ResearchOS-Lang": "en"})
    assert status == 404 and refused["state"] == "workspace_no_such_link" and refused["message"] == "This link cannot be found"
    status, done = _call(port, "POST", "/api/latex/evidence/withdraw", {"project": str(root), "link": made["link"]})
    assert status == 200 and done["act"] == "withdraw"
    assert (root / "paper/main.tex").read_text() == DRAFT


def test_the_endpoints_reanchor_and_undo(served):
    """Review of B1d: 「是这段」 and 撤销 through the HTTP routes, and a refusal with its reason."""
    port, root = served
    revision = _put(root, "paper/main.tex", DRAFT)
    start, end = _range(root, "paper/main.tex", "第二段 $x^2$ 结束。")
    body = {"project": str(root), "path": "paper/main.tex", "revision": revision, "from": start, "to": end,
            "file": "figures/f1.png"}
    status, made = _call(port, "POST", "/api/latex/evidence/link", body)
    assert status == 200
    status, refused = _call(port, "POST", "/api/latex/evidence/link", {**body, "note": "x" * 2001},
                            {"X-ResearchOS-Lang": "en"})
    assert status == 400 and refused["message"] == "A note is at most 2,000 characters"
    revision = _put(root, "paper/main.tex", DRAFT.replace("$x^2$", "$x^3$"))
    status, listed = _call(port, "GET", "/api/latex/evidence?path=paper/main.tex")
    (shown,) = listed["links"]
    assert shown["state"] == "changed"
    candidate = shown["candidates"][0]
    status, moved = _call(port, "POST", "/api/latex/evidence/reanchor", {
        "project": str(root), "link": made["link"], "path": "paper/main.tex", "revision": revision,
        "from": candidate["from"], "to": candidate["to"]})
    assert status == 200 and moved["act"] == "reanchor"
    assert _call(port, "GET", "/api/latex/evidence?path=paper/main.tex")[1]["links"][0]["state"] == "found"
    status, undone = _call(port, "POST", "/api/latex/evidence/undo",
                           {"project": str(root), "link": made["link"], "undoes": moved["entry"]})
    assert status == 200 and undone["act"] == "undone"
    assert _call(port, "GET", "/api/latex/evidence?path=paper/main.tex")[1]["links"][0]["state"] == "changed"
