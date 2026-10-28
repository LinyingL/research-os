"""Tests for researchos.semantic.draft and its endpoints (DESIGN.md 16).
The model is a stub, or a tiny OpenAI-compatible HTTP server on a free
loopback port in this process; LM Studio is never reached."""

import http.server
import json
import logging
import os
import socket
import threading

import pytest

from researchos import cloud, settings
from researchos.semantic import backend, draft
from researchos.semantic.backend import LlmError
from researchos.webapp import server

from test_semantic_backend import _misbehaving_server
from test_docx_preview import _write as _docx
from test_v11_dta import VARS, _dta
from test_webapp_server import _init_project, _request_with_headers


@pytest.fixture(autouse=True)
def never_a_real_model_server(monkeypatch):
    """No test here may reach a model server it did not start: the default
    address is a closed loopback port (a refusal is then immediate)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setattr(backend, "DEFAULT_BASE_URL", f"http://localhost:{port}/v1")


class _Stub:
    model = "stub-model"

    def __init__(self, reply=None, error=None, answered=None):
        self.reply, self.error, self.calls, self.last_model = reply, error, [], answered

    def complete_json(self, system, user, schema, name="response"):
        self.calls.append((system, user, schema, name))
        if self.error:
            raise self.error
        return self.reply


def _cell(text, sources):
    return {"text": text, "sources": sources}


EMPTY = _cell("", [])
FILES = [{"path": "code/run.py", "content": "xtreg y x, fe  // 2010-2019", "cut": False},
         {"path": "out/table.csv", "content": "beta,0.120\nse,0.05\nN,\"1,234\"\nshare,−3.5", "cut": True}]


# -- the checks after the model answers (16.1) ------------------------------------------------


def test_a_cell_resting_on_the_given_files_stands():
    stub = _Stub({"description": _cell("Fixed effects, 2010-2019", ["code/run.py"]),
                  "result": _cell("beta = 0.12 (se 0.05), N = 1234, share -3.5%", ["out/table.csv"])})
    out = draft.draft_attempt(FILES, stub)
    assert out["fields"]["description"] == _cell("Fixed effects, 2010-2019", ["code/run.py"])
    assert out["fields"]["result"]["text"].startswith("beta = 0.12") and out["dropped"] == {}
    assert out["model"] == "stub-model" and out["cut"] == ["out/table.csv"]


def test_the_model_the_server_named_is_the_one_reported():
    out = draft.draft_attempt(FILES, _Stub({"description": EMPTY, "result": EMPTY}, answered="loaded/model"))
    assert out["model"] == "loaded/model"


@pytest.mark.parametrize("cell, reason", [
    (_cell("  ", ["code/run.py"]), "empty"),
    (_cell("Fixed effects", []), "no_source"),
    (_cell("Fixed effects", ["code/run.py", "logs/second_stage.log"]), "unknown_source"),
    (_cell("Fixed effects", ["./code/run.py"]), "unknown_source"),
    (_cell("beta = 0.45", ["out/table.csv"]), "unsupported_number"),
    (_cell("beta = 0.12, se 0.05", ["code/run.py"]), "unsupported_number"),  # the numbers are in another file
    (_cell("beta = -0.12", ["out/table.csv"]), "unsupported_number"),  # the sign is part of the number
])
def test_a_cell_that_fails_a_check_is_dropped_with_its_reason(cell, reason, caplog):
    with caplog.at_level(logging.INFO, logger="researchos.semantic.draft"):
        out = draft.draft_attempt(FILES, _Stub({"description": EMPTY, "result": cell}))
    assert out["fields"]["result"] == EMPTY
    assert out["dropped"]["result"] == reason
    assert out["dropped"]["description"] == "empty"
    assert f"draft result dropped: {reason}" in caplog.text and "draft description dropped: empty" in caplog.text


def test_numbers_are_compared_as_written_and_as_values():
    """Thousands separators, U+2212 and % are normalized; "0.12" is the
    file's "0.120"; a number only past the cut is not in the text sent."""
    assert draft._numbers("N=1,234; −3.5%; +2") == ["1234", "-3.5", "+2"]
    assert draft._unsupported_numbers("0.12 and 1,234 and −3.5%", "0.120 1234 -3.5") == []
    assert draft._unsupported_numbers("in 2019-2020", "from 2019 to 2020") == []  # a hyphen is not a sign
    cut = draft.prepare_files([{"path": "a.log", "content": "x" * draft.MAX_CHARS_TOTAL + " beta 0.77"}])
    out = draft.draft_attempt(cut, _Stub({"description": EMPTY, "result": _cell("beta 0.77", ["a.log"])}))
    assert out["dropped"]["result"] == "unsupported_number"


@pytest.mark.parametrize("text, files_text, missing", [
    ("p = 1.23", "p = 1.23e-05", ["1.23"]),  # an exponent belongs to its number
    ("p = 2.3e-05", "2.3e-08 ; obs 5", ["2.3e-05"]),
    ("p = 0.0000123", "p = 1.23e-05", []),  # the same value
    ("coefficient 10", "gen lx = log10(x)", ["10"]),  # digits inside a name are no number
    ("１２．３４", "a 12 b 34", ["12.34"]),  # full-width digits read as ASCII
    ("120 treated and 345 control units", "group,n_treated,n_control\nall,120,345\n", []),  # CSV fields
    ("N = 1500, from 2019", "model,5,1500\nid,year\n1,2019", []),
    ("N = 1,204 and 1204", "Number of obs = 1,204", []),
])
def test_numbers_keep_their_exponent_and_their_fields(text, files_text, missing):
    assert draft._unsupported_numbers(text, files_text) == missing


def test_a_number_in_a_cited_files_own_path_counts_only_as_written():
    """The model is shown each path; naming the file (or the project's
    "(16-17)") is no guessed number. Only the cited files' paths count."""
    files = [{"path": "steps/16-build.py", "content": "xtreg y x, fe", "cut": False},
             {"path": "steps/17-plot.py", "content": "graph export fig2.png", "cut": False},
             {"path": "out/02_reg.log", "content": "beta 0.31", "cut": False}]
    for cell, kept in ((_cell("Fixed effects in steps/16-build.py (16-17)", ["steps/16-build.py", "steps/17-plot.py"]), True),
                       (_cell("Ran 16-build.py", ["steps/16-build.py"]), True),
                       (_cell("Fixed effects (16-17)", ["steps/16-build.py"]), False),  # 17 is not a cited path
                       (_cell("beta = 2", ["out/02_reg.log"]), False)):  # "02" counts only as written
        out = draft.draft_attempt(files, _Stub({"description": cell, "result": EMPTY}))
        assert (out["fields"]["description"] == cell) is kept, cell
        assert ("description" in out["dropped"]) is not kept


@pytest.mark.parametrize("content, fragments", [
    ("x" * 11_985 + "\nbeta x = 0.4567 (se 0.0123)\n", ["0.456", "0.45"]),  # the share ends inside 0.4567
    ("x" * 11_990 + " N = 12,345 obs", ["12,34", "12", "34"]),
    ("[" + "1," * 5_998 + "0.4567]", ["0.4", "0.45"]),  # no space at all
], ids=["line", "thousands", "no-space"])
def test_a_cut_never_splits_a_number(content, fragments):
    sent = draft.prepare_files([{"path": "a.log", "content": content}])[0]
    assert sent["cut"] and 0 < len(sent["content"]) <= draft.MAX_CHARS_TOTAL
    for part in fragments:
        assert draft._unsupported_numbers(part, sent["content"]) == draft._numbers(part), part


# -- the prompt (16.2) --------------------------------------------------------------------------


@pytest.mark.parametrize("language, sentence", [
    ("zh", "Write in Chinese."), ("en", "Write in English."),
    (None, "Write in the language of the files' own comments and text."),
    ("fr", "Write in the language of the files' own comments and text."),
])
def test_the_prompt_carries_the_files_the_cut_and_the_language_and_no_example_rows(language, sentence):
    stub = _Stub({"description": EMPTY, "result": EMPTY})
    draft.draft_attempt(FILES, stub, language)
    system, user, schema, name = stub.calls[0]
    assert system.endswith(sentence)
    assert "=== file: code/run.py ===\nxtreg y x, fe" in user
    assert "=== file: out/table.csv (cut: only the beginning is shown) ===" in user
    assert "example" not in (system + user).lower()
    assert set(schema["required"]) == {"description", "result"} and "verdict" not in schema["properties"]
    for cell in schema["properties"].values():
        assert cell["properties"]["sources"]["items"]["enum"] == [f["path"] for f in FILES]
    assert "enum" not in draft.SCHEMA["properties"]["description"]["properties"]["sources"]["items"]
    assert name == "attempt_draft"


def test_the_files_share_one_budget_and_a_short_files_share_goes_to_the_others():
    total = draft.MAX_CHARS_TOTAL
    out = draft.prepare_files([{"path": "a", "content": "a" * 100}, {"path": "b", "content": "b" * total},
                               {"path": "c", "content": "c" * total, "truncated": False},
                               {"path": "d", "content": "short", "truncated": True}])
    lengths = [len(f["content"]) for f in out]
    assert lengths[0] == 100 and lengths[3] == 5  # whole: shorter than an equal share
    assert lengths[1] + lengths[2] == total - 105 and abs(lengths[1] - lengths[2]) <= 1
    assert [f["cut"] for f in out] == [False, True, True, True]  # d: file_payload already cut it
    assert [f["path"] for f in out] == ["a", "b", "c", "d"]


def test_model_failure_is_raised_not_guessed():
    with pytest.raises(LlmError):
        draft.draft_attempt(FILES, _Stub(error=LlmError("could not reach")))


# -- the setting (16.2): GET/POST /api/settings/llm ---------------------------------------------


@pytest.fixture
def no_llm_env(monkeypatch):
    for name in ("RCE_LLM_BASE_URL", "RCE_LLM_MODEL", "RCE_LLM_API_KEY", "RCE_LLM_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)


def test_llm_setting_defaults_then_saved_then_cleared(no_llm_env, monkeypatch):
    def no_client(*a, **k):
        raise AssertionError("GET /api/settings/llm must not build a client")

    monkeypatch.setattr(backend.LlmBackend, "__init__", no_client)
    assert server.llm_setting_payload() == {
        "base_url": backend.DEFAULT_BASE_URL, "model": backend.DEFAULT_MODEL,
        "saved": {"base_url": None, "model": None}, "where": "this_mac", "host": "localhost"}
    got = server.llm_setting_set_payload({"base_url": "http://192.168.1.9:1234/v1", "model": "qwen"})
    assert got == {"base_url": "http://192.168.1.9:1234/v1", "model": "qwen",
                   "saved": {"base_url": "http://192.168.1.9:1234/v1", "model": "qwen"},
                   "where": "elsewhere", "host": "192.168.1.9"}
    server.llm_setting_set_payload({"base_url": "", "model": ""})
    assert settings.read_settings() == {}
    assert server.llm_setting_payload()["model"] == backend.DEFAULT_MODEL


@pytest.mark.parametrize("url, where", [
    ("http://127.0.0.2:8080/v1", "this_mac"), ("http://[::1]:1234/v1", "this_mac"),
    ("http://LOCALHOST:1234/v1", "this_mac"),
    ("http://studio-mac.local:1234/v1", "elsewhere"), ("https://llm.example.com/v1", "elsewhere"),
])
def test_only_this_machine_is_this_mac_and_a_dot_local_name_is_elsewhere(no_llm_env, url, where):
    assert server.llm_setting_set_payload({"base_url": url})["where"] == where


def test_an_address_with_a_password_is_refused_and_a_password_never_shown(no_llm_env, monkeypatch):
    """urllib cannot use user:password (16.2): saving one is refused without
    repeating it; one saved earlier reads as not saved; one in
    RCE_LLM_BASE_URL is shown without it."""
    with pytest.raises(server.DraftError) as err:
        server.llm_setting_set_payload({"base_url": "http://me:secret@127.0.0.1:1234/v1"})
    assert err.value.state == "draft_bad_setting" and "secret" not in str(err.value)
    assert not settings.settings_path().exists()
    settings.write_json(settings.settings_path(), {"llm_base_url": "http://me:secret@127.0.0.1:1234/v1"})
    assert server.llm_setting_payload()["saved"]["base_url"] is None
    for env in ("http://me:secret@127.0.0.1:1234/v1", "http://me:secret@[::1/v1"):
        monkeypatch.setenv("RCE_LLM_BASE_URL", env)
        got = server.llm_setting_payload()
        assert "secret" not in json.dumps(got)
    assert got["base_url"] == "http://[::1/v1"


def test_the_saved_setting_wins_over_the_environment_which_wins_over_the_default(no_llm_env, monkeypatch):
    monkeypatch.setenv("RCE_LLM_BASE_URL", "http://127.0.0.1:7777/v1/")
    monkeypatch.setenv("RCE_LLM_MODEL", "env-model")
    got = server.llm_setting_payload()
    assert (got["base_url"], got["model"], got["saved"]) == ("http://127.0.0.1:7777/v1", "env-model",
                                                             {"base_url": None, "model": None})
    got = server.llm_setting_set_payload({"model": "saved-model"})
    assert (got["base_url"], got["model"]) == ("http://127.0.0.1:7777/v1", "saved-model")
    client = server._llm_client()
    assert (client.base_url, client.model) == ("http://127.0.0.1:7777/v1", "saved-model")


def test_root_addresses_are_normalized_for_saved_existing_and_environment_settings(no_llm_env, monkeypatch):
    root_url = "http://127.0.0.1:1234"
    got = server.llm_setting_set_payload({"base_url": root_url, "model": "prism-ml/bonsai-27b"})
    assert got["base_url"] == got["saved"]["base_url"] == root_url + "/v1"
    assert settings.read_settings()["llm_base_url"] == root_url + "/v1"
    # An address saved by the old app also works, without a migration write.
    settings.write_json(settings.settings_path(), {"llm_base_url": root_url, "llm_model": "old-model"})
    before = settings.settings_path().read_bytes()
    assert server.llm_setting_payload()["base_url"] == server._llm_client().base_url == root_url + "/v1"
    assert settings.settings_path().read_bytes() == before
    server.llm_setting_set_payload({"base_url": ""})
    monkeypatch.setenv("RCE_LLM_BASE_URL", root_url)
    assert server.llm_setting_payload()["base_url"] == server._llm_client().base_url == root_url + "/v1"


def test_a_partial_post_keeps_the_other_key(no_llm_env):
    server.llm_setting_set_payload({"base_url": "http://localhost:9999/v1", "model": "qwen"})
    server.llm_setting_set_payload({"model": "other"})
    assert settings.read_settings() == {"llm_base_url": "http://localhost:9999/v1", "llm_model": "other"}
    server.llm_setting_set_payload({"base_url": ""})
    assert settings.read_settings() == {"llm_model": "other"}
    server.llm_setting_set_payload({})
    assert settings.read_settings() == {"llm_model": "other"}


@pytest.mark.parametrize("body", [
    {"base_url": "file:///etc"}, {"base_url": "http://[::1"}, {"base_url": "http://localhost:12a4/v1"},
    {"base_url": "http://"}, {"base_url": "http://a b/v1"}, {"base_url": "http://localhost:9/v1\x00"},
    {"base_url": "http://localhost:9/v1\x7f"}, {"base_url": None}, {"model": 5},
    {"base_url": "http://localhost:1/v1", "model": ["m"]},
])
def test_a_bad_setting_is_refused_and_writes_nothing(no_llm_env, body):
    with pytest.raises(server.DraftError) as err:
        server.llm_setting_set_payload(body)
    assert (err.value.state, err.value.status) == ("draft_bad_setting", 400)
    assert not settings.settings_path().exists()


def test_a_damaged_saved_address_reads_as_not_saved(no_llm_env):
    settings.write_json(settings.settings_path(), {"llm_base_url": "http://[::1", "llm_model": "m"})
    got = server.llm_setting_payload()
    assert got["base_url"] == backend.DEFAULT_BASE_URL and got["saved"] == {"base_url": None, "model": "m"}


def test_the_draft_waits_minutes_unless_rce_llm_timeout_says_otherwise(no_llm_env, monkeypatch):
    assert server._llm_client().timeout == draft.TIMEOUT_SECONDS == 300.0
    monkeypatch.setenv("RCE_LLM_TIMEOUT", "7")
    assert server._llm_client().timeout == 7.0


# -- the draft endpoint, in process (16.2) ------------------------------------------------------


def test_paths_are_normalized_and_deduplicated_before_the_model_sees_them(tmp_path, monkeypatch, no_llm_env):
    (tmp_path / "sub").mkdir()
    (tmp_path / "run.py").write_text("xtreg y x, fe", encoding="utf-8")
    seen = {}

    def fake(self, system, user, schema, name="response"):
        seen["user"] = user
        return {"description": _cell("Fixed effects", ["run.py"]), "result": EMPTY}

    monkeypatch.setattr(backend.LlmBackend, "complete_json", fake)
    out = server.attempt_draft_payload(tmp_path, {"files": ["./run.py", "run.py", "sub/../run.py",
                                                            str(tmp_path / "run.py")]})
    assert seen["user"].count("=== file: ") == 1 and "=== file: run.py ===" in seen["user"]
    assert out["fields"]["description"] == _cell("Fixed effects", ["run.py"])
    assert out["dropped"] == {"result": "empty"}


def test_six_distinct_files_are_too_many(tmp_path, no_llm_env, monkeypatch):
    monkeypatch.setattr(backend.LlmBackend, "complete_json", lambda *a, **k: pytest.fail("the model must not be asked"))
    for i in range(draft.MAX_FILES + 1):
        (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
    with pytest.raises(server.DraftError) as err:
        server.attempt_draft_payload(tmp_path, {"files": [f"f{i}.txt" for i in range(draft.MAX_FILES + 1)]})
    assert (err.value.state, err.value.status) == ("draft_too_many_files", 400)
    for body in ({}, {"files": []}, {"files": "a.txt"}, {"files": [""]}, {"files": [3]}):
        with pytest.raises(server.DraftError) as err:
            server.attempt_draft_payload(tmp_path, body)
        assert err.value.state == "draft_no_files"


def _file_refusals(root):
    """Each way one chosen file is refused: (path as typed, state, status)."""
    (root / "folder").mkdir()
    (root / "bin.dat").write_bytes(b"\x00\x01")
    (root / "latin.txt").write_bytes("caf\xe9".encode("latin-1"))
    # over the 200 KB read, no NUL byte: its bad bytes come before the cut
    (root / "cut.pdf").write_bytes(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n" + bytes(range(1, 256)) * (server._FILE_SIZE_LIMIT // 255 + 1))
    (root / "auto.dta").write_bytes(_dta(118, "LSF", VARS, rows=2))
    (root / "broken.dta").write_bytes(b"not a dta")
    (root / "cloud.txt").write_text("beta 0.1", encoding="utf-8")
    (root / "cloud.docx").write_bytes(b"PK")
    (root / "locked.txt").write_text("beta 0.1", encoding="utf-8")
    (root / "locked.txt").chmod(0)
    (root / "broken.docx").write_bytes(b"PK\x03\x04broken")
    (root / "old.doc").write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
    (root / "paper.pdf").write_bytes(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Length 9 >>\nstream\n\x00\x01\x02")
    return [("nope.txt", "draft_file_missing", 404), ("folder", "draft_file_missing", 400),
            ("a\x00b.txt", "draft_file_missing", 400), ("locked.txt", "draft_file_unreadable", 400),
            ("bin.dat", "draft_file_not_text", 400), ("latin.txt", "draft_file_not_text", 400),
            ("cut.pdf", "draft_file_not_text", 400),
            ("auto.dta", "draft_file_not_text", 400), ("broken.dta", "draft_file_not_text", 400),
            ("broken.docx", "draft_file_unreadable", 400),  # 16.4: a Word file the preview cannot read
            ("old.doc", "draft_file_not_text", 400), ("paper.pdf", "draft_file_not_text", 400),
            ("cloud.txt", "draft_file_cloud_only", 503), ("cloud.docx", "draft_file_cloud_only", 503),
            ("../outside.txt", "draft_file_outside", 403)]


def _cloud_only(monkeypatch):
    real = cloud.check_readable

    def only_in_the_cloud(path):
        if str(path).endswith(("cloud.txt", "cloud.docx")):
            raise cloud.CloudOnlyError(path, cloud.Provider("OneDrive", "OneDrive", "OneDrive", "/nowhere"))
        return real(path)

    monkeypatch.setattr(cloud, "check_readable", only_in_the_cloud)


def test_one_unreadable_file_refuses_the_whole_draft_and_names_it(tmp_path, monkeypatch, no_llm_env):
    (tmp_path / "ok.txt").write_text("beta 0.1", encoding="utf-8")
    _cloud_only(monkeypatch)
    monkeypatch.setattr(backend.LlmBackend, "complete_json",
                        lambda *a, **k: pytest.fail("the model must not be asked"))
    for typed, state, status in _file_refusals(tmp_path):
        with pytest.raises(server.DraftError) as err:
            server.attempt_draft_payload(tmp_path, {"files": ["ok.txt", typed]})
        assert (err.value.state, err.value.status, err.value.extra) == (state, status, {"file": typed}), typed


def test_one_file_under_two_spellings_is_one_file(tmp_path, monkeypatch, no_llm_env):
    """APFS ignores case: "run.py" and "RUN.PY" are one file, sent once."""
    (tmp_path / "run.py").write_text("xtreg y x, fe", encoding="utf-8")
    if not (tmp_path / "RUN.PY").exists():
        pytest.skip("this file system tells case apart")
    seen = {}
    monkeypatch.setattr(backend.LlmBackend, "complete_json",
                        lambda self, system, user, *a, **k: seen.setdefault("user", user) and {"description": EMPTY, "result": EMPTY})
    out = server.attempt_draft_payload(tmp_path, {"files": ["run.py", "RUN.PY", "Run.py"]})
    assert seen["user"].count("=== file: ") == 1 and "=== file: run.py ===" in seen["user"] and out["cut"] == []


def _p(text):
    return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"


def test_a_word_document_is_sent_as_its_paragraphs_and_tables_by_its_path_in_the_project(tmp_path, monkeypatch,
                                                                                         no_llm_env):
    """16.4: the panel's Word preview as plain text -- a paragraph a line, a table a row a line, its cells
    joined by " | ", a line break in a cell a space, an equation and an image as markers, a tracked deletion
    left out. Typed whole or from the project folder it is one file, sent by its path in the project, and
    the number check reads its table."""
    (tmp_path / "paper").mkdir()
    _docx(tmp_path / "paper" / "论文.docx",
          _p("Currency Resilience")
          + '<w:p><w:r><w:t>We compare </w:t></w:r><w:del><w:r><w:t>0.731 </w:t></w:r></w:del>'
          '<w:r><w:t>prices</w:t><w:tab/><w:t>and use</w:t></w:r><m:oMath xmlns:m="urn:math"><m:t>x</m:t></m:oMath>'
          '<w:r><w:drawing/></w:r></w:p>'
          '<w:tbl><w:tr><w:tc>' + _p("Test") + '</w:tc><w:tc>' + _p("Estimate") + '</w:tc></w:tr>'
          '<w:tr><w:tc>' + _p("H1") + '</w:tc><w:tc>' + _p("2.444%") + _p("[0.829%]") + '</w:tc></w:tr></w:tbl>')
    seen = {}

    def fake(self, system, user, schema, name="response"):
        seen["user"], seen["enum"] = user, schema["properties"]["result"]["properties"]["sources"]["items"]["enum"]
        return {"description": _cell("Compares prices and use", ["paper/论文.docx"]),
                "result": _cell("H1: 2.444% (lower bound 0.829%)", ["paper/论文.docx"])}

    monkeypatch.setattr(backend.LlmBackend, "complete_json", fake)
    out = server.attempt_draft_payload(tmp_path, {"files": [str(tmp_path / "paper" / "论文.docx"), "paper/论文.docx"]})
    assert ("=== file: paper/论文.docx ===\nCurrency Resilience\nWe compare prices\tand use[Equation][Image or object]\n"
            "Test | Estimate\nH1 | 2.444% [0.829%]\n\n") in seen["user"]
    assert seen["user"].count("=== file: ") == 1 and "0.731" not in seen["user"] and seen["enum"] == ["paper/论文.docx"]
    assert out["fields"]["result"] == _cell("H1: 2.444% (lower bound 0.829%)", ["paper/论文.docx"])
    assert out["dropped"] == {} and out["cut"] == []


def test_a_long_word_document_is_cut_from_its_beginning_and_a_later_table_is_not_sent(tmp_path, monkeypatch,
                                                                                      no_llm_env):
    """16.4: a Word document shares the same characters as any file and is cut from its beginning (the
    researcher chose this over sending the tables first): a results table further on is not sent, so a
    number only in it is dropped."""
    words = " ".join(f"word{i}" for i in range(3000))
    _docx(tmp_path / "paper.docx", _p("Abstract: valuation deteriorates.") + _p(words)
          + "<w:tbl><w:tr><w:tc>" + _p("2.444%") + "</w:tc></w:tr></w:tbl>")
    (tmp_path / "run.py").write_text("xtreg y x, fe\n", encoding="utf-8")
    seen = {}

    def fake(self, system, user, schema, name="response"):
        seen["user"] = user
        return {"description": _cell("Valuation deteriorates", ["paper.docx"]), "result": _cell("2.444%", ["paper.docx"])}

    monkeypatch.setattr(backend.LlmBackend, "complete_json", fake)
    out = server.attempt_draft_payload(tmp_path, {"files": ["paper.docx", "run.py"]})
    sent = seen["user"].split("=== file: paper.docx (cut: only the beginning is shown) ===\n", 1)[1].split("\n\n", 1)[0]
    assert sent.startswith("Abstract: valuation deteriorates.\nword0 word1 ")
    assert len(sent) + len("xtreg y x, fe\n") <= draft.MAX_CHARS_TOTAL and "2.444" not in seen["user"]
    assert out["cut"] == ["paper.docx"] and out["dropped"] == {"result": "unsupported_number"}
    assert out["fields"]["description"] == _cell("Valuation deteriorates", ["paper.docx"])


def test_a_name_too_long_is_a_refusal_naming_it(tmp_path, monkeypatch, no_llm_env):
    monkeypatch.setattr(backend.LlmBackend, "complete_json", lambda *a, **k: pytest.fail("the model must not be asked"))
    with pytest.raises(server.DraftError) as err:
        server.attempt_draft_payload(tmp_path, {"files": ["a" * 300]})
    assert err.value.state in ("draft_file_missing", "draft_file_unreadable") and err.value.extra == {"file": "a" * 300}


def test_a_timeout_that_is_not_a_number_is_a_refusal(tmp_path, monkeypatch, no_llm_env):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    monkeypatch.setenv("RCE_LLM_TIMEOUT", "5m")
    with pytest.raises(server.DraftError) as err:
        server.attempt_draft_payload(tmp_path, {"files": ["a.txt"]})
    assert (err.value.state, err.value.status) == ("draft_model_failed", 502) and "RCE_LLM_TIMEOUT" in str(err.value)


def test_a_refusal_never_shows_a_password(tmp_path, monkeypatch, no_llm_env):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(backend.LlmBackend, "complete_json", lambda *a, **k: (_ for _ in ()).throw(
        LlmError("could not reach an LLM server at http://me:secret@127.0.0.1:9/v1 (refused)")))
    with pytest.raises(server.DraftError) as err:
        server.attempt_draft_payload(tmp_path, {"files": ["a.txt"]})
    assert "secret" not in str(err.value) and "http://127.0.0.1:9/v1" in str(err.value)


@pytest.mark.parametrize("url, logged", [("http://studio.local:1234/v1", "studio.local"),
                                         ("https://llm.example.com/v1", "llm.example.com"),
                                         ("http://127.0.0.1:1234/v1", None)])
def test_a_draft_sent_off_this_mac_is_logged_in_its_own_words(tmp_path, monkeypatch, no_llm_env, caplog, url, logged):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    settings.set_llm_settings(base_url=url)
    monkeypatch.setattr(backend.LlmBackend, "complete_json", lambda *a, **k: {"description": EMPTY, "result": EMPTY})
    with caplog.at_level(logging.INFO):
        server.attempt_draft_payload(tmp_path, {"files": ["a.txt"]})
    assert "researchos judge" not in caplog.text and "NON-LOCAL" not in caplog.text
    assert ("draft: sending the contents of 1 chosen file(s) to " + str(logged) in caplog.text) is (logged is not None)


def test_model_failures_are_refusals(tmp_path, monkeypatch, no_llm_env):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    for error, state, status in ((backend.LlmContextError("greater than the context length"), "draft_too_long", 413),
                                 (LlmError("could not reach"), "draft_model_failed", 502)):
        monkeypatch.setattr(backend.LlmBackend, "complete_json", lambda *a, _e=error, **k: (_ for _ in ()).throw(_e))
        with pytest.raises(server.DraftError) as err:
            server.attempt_draft_payload(tmp_path, {"files": ["a.txt"]})
        assert (err.value.state, err.value.status) == (state, status) and str(error) in str(err.value)


# -- over HTTP, with a fake OpenAI-compatible model server -------------------------------------

# LM Studio's exact answer when the prompt is longer than the loaded context.
LM_STUDIO_OVERFLOW = (
    '{"error":"The number of tokens to keep from the initial prompt is greater than the context length. '
    'Try to load the model with a larger context length, or provide a shorter input"}'
)


class _FakeModel:
    """A tiny OpenAI-compatible server: answers every POST with `status`
    and `reply`, and keeps each request body it received."""

    def __init__(self):
        self.status, self.reply, self.bodies, self.keys, self.paths = 200, {}, [], [], []
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                fake.paths.append(self.path)
                fake.bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                fake.keys.append(self.headers.get("Authorization"))
                raw = (fake.reply if isinstance(fake.reply, str) else json.dumps(fake.reply)).encode("utf-8")
                self.send_response(fake.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"

    def answer(self, draft_reply, model="lmstudio/loaded-model"):
        self.status = 200
        self.reply = {"model": model, "choices": [{"message": {"content": json.dumps(draft_reply)}}]}

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def fake_model(no_llm_env):
    fake = _FakeModel()
    settings.set_llm_settings(base_url=fake.base_url, model="asked-model")
    try:
        yield fake
    finally:
        fake.close()


@pytest.mark.parametrize("rel", ["run.py", "分析/确认性检验.py"])
def test_http_root_address_and_lm_studio_misrouted_json_produce_a_draft(live, fake_model, rel):
    base, root = live
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text("# Fixed effects regression.\n# beta = 0.12\n", encoding="utf-8")
    cells = {"description": _cell("Fixed effects regression", [rel]),
             "result": _cell("beta = 0.12", [rel])}
    fake_model.reply = {"model": "prism-ml/bonsai-27b", "choices": [{"message": {
        "content": "", "reasoning_content": json.dumps(cells)}, "finish_reason": "stop"}]}
    assert _call(base, "POST", "/api/settings/llm", {"base_url": fake_model.base_url.removesuffix("/v1")})[0] == 200
    before = (root / rel).read_bytes()
    status, body = _call(base, "POST", "/api/attempts/draft", {"files": [rel]})
    assert status == 200 and body["fields"] == cells and body["dropped"] == {}
    assert body["model"] == "prism-ml/bonsai-27b" and fake_model.paths == ["/v1/chat/completions"]
    assert (root / rel).read_bytes() == before


def _serve(root=None, served=None):
    if root is not None:
        _init_project(root)
    httpd = server.build_server(root, 0, served=served)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread, f"http://127.0.0.1:{httpd.server_address[1]}"


@pytest.fixture
def live(tmp_path):
    root = tmp_path / "proj"
    httpd, thread, base = _serve(root)
    try:
        yield base, root
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _call(base, method, path, body=None, headers=None):
    port = base.rsplit(":", 1)[1]
    data = None if body is None else json.dumps(body).encode("utf-8")
    return _request_with_headers(base, method, path, {"Host": f"127.0.0.1:{port}",
                                                      "Content-Type": "application/json", **(headers or {})}, data)


def test_http_a_good_draft(live, fake_model):
    base, root = live
    (root / "code").mkdir()
    (root / "code" / "run.do").write_text("xtreg y x, fe\n* beta 0.120 se 0.05\n", encoding="utf-8")
    fake_model.answer({"description": _cell("Fixed effects", ["code/run.do"]),
                       "result": _cell("beta = 0.12", ["code/run.do"])})
    status, body = _call(base, "POST", "/api/attempts/draft", {"files": ["./code/run.do"], "language": "en",
                                                               "examples": [{"description": "OLS"}]})
    assert status == 200, body
    assert body == {"fields": {"description": _cell("Fixed effects", ["code/run.do"]),
                               "result": _cell("beta = 0.12", ["code/run.do"])},
                    "dropped": {}, "model": "lmstudio/loaded-model", "cut": []}
    sent = fake_model.bodies[0]
    assert sent["model"] == "asked-model" and sent["response_format"]["type"] == "json_schema"
    system, user = sent["messages"][0]["content"], sent["messages"][1]["content"]
    assert system.endswith("Write in English.") and "=== file: code/run.do ===" in user
    assert "OLS" not in system + user and "example" not in (system + user).lower()


def test_http_an_invented_number_is_dropped_without_losing_a_valid_description(live, fake_model):
    base, root = live
    (root / "run.do").write_text("xtreg y x, fe\n* beta 0.120\n", encoding="utf-8")
    fake_model.answer({"description": _cell("Fixed effects", ["run.do"]),
                       "result": _cell("beta = 0.48", ["run.do"])})
    status, body = _call(base, "POST", "/api/attempts/draft", {"files": ["run.do"]})
    assert status == 200
    assert body["fields"] == {"description": _cell("Fixed effects", ["run.do"]), "result": EMPTY}
    assert body["dropped"] == {"result": "unsupported_number"}
    assert fake_model.bodies[0]["messages"][0]["content"].endswith(
        "Write in the language of the files' own comments and text.")


def test_http_unknown_sources_are_refused_after_the_schema_repair_retry(live, fake_model):
    base, root = live
    (root / "run.py").write_text("# Fixed effects regression.\n", encoding="utf-8")
    fake_model.answer({"description": _cell("Fixed effects", ["other.py"]), "result": EMPTY})
    status, body = _call(base, "POST", "/api/attempts/draft", {"files": ["run.py"]})
    assert (status, body["state"]) == (502, "draft_model_failed")
    assert len(fake_model.bodies) == 2 and "failed schema validation twice" in body["error"]


def test_http_lm_studios_context_overflow_is_draft_too_long(live, fake_model):
    base, root = live
    (root / "run.do").write_text("xtreg y x, fe", encoding="utf-8")
    fake_model.status, fake_model.reply = 400, LM_STUDIO_OVERFLOW
    status, body = _call(base, "POST", "/api/attempts/draft", {"files": ["run.do"]})
    assert (status, body["state"]) == (413, "draft_too_long")
    assert "greater than the context length" in body["error"]
    assert len(fake_model.bodies) == 1  # never retried as "response_format unsupported"


def test_http_an_unreachable_model_is_draft_model_failed(live, no_llm_env):
    base, root = live
    (root / "run.do").write_text("xtreg y x, fe", encoding="utf-8")
    with socket.socket() as s:  # a free port, closed again: nothing listens there
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    settings.set_llm_settings(base_url=f"http://127.0.0.1:{port}/v1")
    status, body = _call(base, "POST", "/api/attempts/draft", {"files": ["run.do"]})
    assert (status, body["state"]) == (502, "draft_model_failed")


@pytest.mark.parametrize("mode", ["close", "short"])
def test_http_a_model_server_that_breaks_off_is_draft_model_failed(live, no_llm_env, mode):
    """LM Studio unloading the model or stopping mid-answer (16.2): the LM
    Studio remedy, never a bare 500."""
    base, root = live
    (root / "run.do").write_text("xtreg y x, fe", encoding="utf-8")
    settings.set_llm_settings(base_url=_misbehaving_server(mode))
    status, body = _call(base, "POST", "/api/attempts/draft", {"files": ["run.do"]})
    assert (status, body["state"]) == (502, "draft_model_failed") and "LM Studio" in body["error"]


def test_http_the_environment_key_goes_only_to_its_own_address(live, fake_model, monkeypatch):
    """RCE_LLM_API_KEY was set beside RCE_LLM_BASE_URL (or the default): a
    different address saved here never receives it (16.2)."""
    base, root = live
    (root / "run.do").write_text("xtreg y x, fe", encoding="utf-8")
    fake_model.answer({"description": EMPTY, "result": EMPTY})
    monkeypatch.setenv("RCE_LLM_API_KEY", "sk-env-only")
    monkeypatch.setenv("RCE_LLM_BASE_URL", "https://paid.example.invalid/v1")
    assert _call(base, "POST", "/api/attempts/draft", {"files": ["run.do"]})[0] == 200
    monkeypatch.setenv("RCE_LLM_BASE_URL", fake_model.base_url.replace("/v1", "/other/"))  # the same origin
    assert _call(base, "POST", "/api/attempts/draft", {"files": ["run.do"]})[0] == 200
    assert fake_model.keys == [None, "Bearer sk-env-only"]


def test_http_each_file_refusal_names_the_file(live, fake_model, monkeypatch):
    base, root = live
    _cloud_only(monkeypatch)
    for typed, state, status in _file_refusals(root):
        got_status, body = _call(base, "POST", "/api/attempts/draft", {"files": [typed]})
        assert (got_status, body["state"], body["file"]) == (status, state, typed), typed
    assert fake_model.bodies == []


def test_http_a_word_document_typed_by_its_whole_path_produces_a_draft(live, fake_model):
    """16.4 through the endpoint the page calls: a Word document typed whole inside the project is read as
    text and named by its path in the project."""
    base, root = live
    _docx(root / "paper.docx", _p("Non-inferiority tests") + "<w:tbl><w:tr><w:tc>" + _p("2.444%") + "</w:tc></w:tr></w:tbl>")
    fake_model.answer({"description": _cell("Non-inferiority tests", ["paper.docx"]),
                       "result": _cell("2.444%", ["paper.docx"])})
    status, body = _call(base, "POST", "/api/attempts/draft", {"files": [str(root / "paper.docx")]})
    assert status == 200 and body["dropped"] == {} and body["cut"] == []
    assert body["fields"]["result"] == _cell("2.444%", ["paper.docx"])
    assert "=== file: paper.docx ===\nNon-inferiority tests\n2.444%\n\n" in fake_model.bodies[0]["messages"][1]["content"]


@pytest.mark.parametrize("path, body", [("/api/attempts/draft", {"files": ["run.do"]}),
                                        ("/api/settings/llm", {"model": "evil"})])
def test_http_another_origin_is_refused_and_nothing_is_written_or_sent(live, fake_model, path, body):
    base, root = live
    (root / "run.do").write_text("xtreg y x, fe", encoding="utf-8")
    before = settings.read_settings()
    status, _ = _call(base, "POST", path, body, {"Origin": "http://evil.example"})
    assert status == 403
    assert settings.read_settings() == before and fake_model.bodies == []


def test_http_the_setting_answers_with_no_project_served(no_llm_env):
    assert "/api/settings/llm" in server._NO_PROJECT_GET and "/api/settings/llm" in server._NO_PROJECT_POST
    httpd, thread, base = _serve()
    try:
        status, body = _call(base, "GET", "/api/settings/llm")
        assert status == 200 and body["where"] == "this_mac"
        status, body = _call(base, "POST", "/api/settings/llm", {"model": "qwen"})
        assert status == 200 and body["saved"] == {"base_url": None, "model": "qwen"}
        status, body = _call(base, "POST", "/api/settings/llm", {"base_url": "ftp://x"})
        assert (status, body["state"]) == (400, "draft_bad_setting")
        status, body = _call(base, "POST", "/api/attempts/draft", {"files": ["run.do"]})
        assert (status, body["state"]) == (409, "no_project")
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    assert settings.read_settings() == {"llm_model": "qwen"}


def test_http_a_blocked_project_refuses_the_draft(tmp_path, fake_model):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "run.do").write_text("xtreg y x, fe", encoding="utf-8")
    blocked = {"situation": "copy", "answers": ["fork"], "blocked": True}
    httpd, thread, base = _serve(served=server.ServedProject(root, None, blocked=blocked))
    try:
        status, body = _call(base, "POST", "/api/attempts/draft", {"files": ["run.do"]})
        assert (status, body["state"]) == (409, "project_blocked")
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    assert fake_model.bodies == []
