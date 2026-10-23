"""The 15.3 confirmation loop on the page (DESIGN.md 15.3, 15.5, 13.6; task
V11): 数据与代码 shows the project folder Research OS proposes for a Stata
global, with its evidence in the page's language, 「是」 and 「不是」 for the
PI or a single researcher (a member sees the proposal), the paths 「展开不
了」 by reason; an answer from the page is written and the do-files are
rescanned at once; an answer written elsewhere (the command line, a sync)
is picked up by the watcher. The PI is asked about a global holding their
own project path, which a member's copy needs. Every do-file here is
written for the test."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from researchos import cli, db, paths, people, share, signing
from researchos import project as project_identity
from researchos.ingest import dataflow, stata_folders
from researchos.records import stata as stata_record
from researchos.webapp import node_api, server
from researchos.webapp import watcher as watcher_mod

from test_v11_stata_folders import MEMBER, PI, TEAM, _files, _package, _scans
from test_v9_language import CJK, WEBAPP, _DESK_DOM, _i18n_source, _node, needs_node


def _serve(root: Path):
    httpd = server.build_server(root, 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread, f"http://127.0.0.1:{httpd.server_address[1]}"


def _call(url: str, method: str = "GET", body: dict | None = None, lang: str = "zh"):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", "X-ResearchOS-Lang": lang})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


# -- the payload (15.3, 13.6) -------------------------------------------------------------------------


def test_the_proposal_its_evidence_and_the_unresolved_paths_in_both_languages(tmp_path):
    people.write_me(*PI)
    root = _package(tmp_path)
    project_identity.init_project(root)
    zh, en = node_api.stata_payload(root, "zh"), node_api.stata_payload(root, "en")
    assert zh["has_do"] and zh["role"] == "single" and zh["may_answer"] and zh["refusal"] is None
    assert zh["record"] == {"state": "ok"}
    (proposal,) = zh["proposals"]
    assert {k: proposal[k] for k in ("global", "folder", "reason", "paths", "found", "answer")} == {
        "global": "Proj", "folder": "analysis", "reason": "undefined", "paths": 4, "found": 3, "answer": None}
    assert proposal["sentence"] == "$Proj 很可能指项目里的文件夹 analysis/：以它开头的 4 条路径里，3 条在 analysis/ 下存在"
    assert en["proposals"][0]["sentence"] == ("$Proj probably stands for the project folder analysis/: of the 4 paths "
                                              "that start with it, 3 exist under analysis/")
    assert [g["reason"] for g in zh["unresolved"]] == ["undefined"]
    assert {(i["file"], i["line"]) for i in zh["unresolved"][0]["items"]} == {
        ("analysis/run.do", 2), *(("analysis/scripts/1_clean.do", n) for n in range(1, 6))}
    assert not CJK.search(json.dumps(en, ensure_ascii=False))
    stata_record.answer(root, "Proj", None, via="cli")  # 「不是」: proposed again, the answer shown
    (again,) = node_api.stata_payload(root, "en")["proposals"]
    assert again["answer"]["answer"] == "no" and again["answer"]["by"] == PI[1]
    stata_record.answer(root, "Proj", "analysis", via="cli")
    after = node_api.stata_payload(root, "en")
    assert after["proposals"] == [] and after["unresolved"] == []
    assert [(c["global"], c["folder"], c["by"]) for c in after["confirmed"]] == [("Proj", "analysis", PI[1])]


def test_a_member_sees_the_proposal_and_cannot_answer(tmp_path):
    root = _package(tmp_path)
    project_identity.init_project(root)
    paths.project_dir(root).joinpath("team.toml").write_text(TEAM, encoding="utf-8")
    people.write_me(*MEMBER)
    got = node_api.stata_payload(root, "en")
    assert got["role"] == "member" and not got["may_answer"] and got["proposals"]
    assert got["refusal"] == "Only the PI answers which project folder a global stands for; members can only look"
    with pytest.raises(node_api.NodeApiRefused) as refused:
        node_api.stata_answer_payload(root, None, {"global": "Proj", "folder": "analysis"}, "en", timeout=5)
    assert refused.value.status == 403 and refused.value.code == "stata_not_pi"
    assert refused.value.message_zh == got["refusal"]  # the sentence in the request's language
    assert not stata_record.stata_path(root).exists()


def test_a_project_without_do_files_says_so(tmp_path):
    root = _files(tmp_path / "p", {"a.py": "print(1)\n"})
    project_identity.init_project(root)
    assert node_api.stata_payload(root) == {"has_do": False}


# -- answering from the page: written, then rescanned at once ---------------------------------------


def test_an_answer_from_the_page_is_written_and_the_links_appear_at_once(tmp_path):
    people.write_me(*PI)
    root = _package(tmp_path)
    assert cli.main(["init", str(root)]) == 0
    httpd, thread, base = _serve(root)
    try:
        status, before = _call(f"{base}/api/data-code")
        assert status == 200 and not [f for f in before["files"] if f["path"] == "analysis/data/raw.csv"]
        status, refused = _call(f"{base}/api/stata/answer", "POST", {"global": "Proj", "folder": "nope"}, "en")
        assert status == 400 and refused["state"] == "stata_no_such_folder"
        assert refused["message"] == "There is no such folder in the project"
        _status, generation = _call(f"{base}/api/generation")
        status, done = _call(f"{base}/api/stata/answer", "POST", {"global": "Proj", "folder": "analysis"})
        assert status == 200 and done["changed"] and done["generation"] > generation["generation"]
        assert "ingest_error" not in done
        status, after = _call(f"{base}/api/data-code")
        (raw,) = [f for f in after["files"] if f["path"] == "analysis/data/raw.csv"]
        assert raw["read_by"] == ["analysis/scripts/1_clean.do"]
        status, stata = _call(f"{base}/api/stata", lang="en")
        assert status == 200 and stata["proposals"] == [] and stata["confirmed"][0]["folder"] == "analysis"
        status, node = _call(f"{base}/api/node?node=dataset%3Aanalysis%2Fdata%2Fraw.csv")
        (link,) = [l for l in node["links"] if l["type"] == "reads"]
        assert link["evidence"][0]["anchor"] == {"global": "Proj", "folder": "analysis"}
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_an_answer_written_elsewhere_is_picked_up_by_the_watcher(tmp_path):
    """15.3 (a CLI answer, a sync, a hand edit): stata.toml is its own change
    class -- the dataflow scan runs again though no attempts config exists."""
    people.write_me(*PI)
    root = _package(tmp_path)
    assert cli.main(["init", str(root)]) == 0
    assert cli.main(["ingest", str(root)]) == 0
    w = watcher_mod.ProjectWatcher(lambda: root, interval=0.01)
    w.poll_once()  # the baseline
    stata_record.answer(root, "Proj", "analysis", via="cli")
    assert w.poll_once() is True
    conn = db.connect(paths.graph_db_path(root))
    try:
        reads = {e["dst"] for e in db.query_edges(conn, type="reads") if e["extractor"] == "dataflow"}
    finally:
        conn.close()
    assert "dataset:analysis/data/raw.csv" in reads
    assert w.status_payload()["last_error"] is None


# -- the PI's own project path (15.3, 12.1) ---------------------------------------------------------


def test_a_single_researchers_own_project_path_is_not_proposed(tmp_path):
    people.write_me(*PI)
    root = tmp_path / "solo"
    _files(root, {"analysis/code/x.do": f'global root "{root}/analysis"\nuse "$root/data/a.dta"\n',
                  "analysis/data/a.dta": "x"})
    project_identity.init_project(root)
    assert node_api.stata_payload(root)["proposals"] == []
    assert stata_folders.head_globals(_scans(root)) == {}


@pytest.fixture
def drive(tmp_path, monkeypatch):
    if signing.tool() is None:
        pytest.skip("ssh-keygen is not installed here")
    from test_v8_phase4 import Drive

    return Drive(tmp_path, monkeypatch)


def _calls(root: Path, rel: str) -> list[tuple]:
    dos = sorted(str(p.relative_to(root)) for p in root.rglob("*.do"))
    scan = dataflow._stata_scans(root, dos, [])[rel]
    return [(c.literal, c.anchor) for c in scan.calls], scan.skipped


def test_the_pi_is_asked_about_their_own_project_path_and_a_members_copy_resolves(drive):
    """The PI's do-file hard-codes the PI's own path: it resolves on the PI's
    computer, so nothing is unresolved there -- but the PI, the only one who
    may answer, is asked (LOCAL_ROOT), its folder the value relative to the
    project. The PI's links are the same before and after 「是」; after
    publishing and syncing, the member's copy resolves the same paths under
    analysis/ with the answer in their evidence, nothing left unresolved."""
    rel = "analysis/code/clean.do"
    text = f'global root "{drive.official}/analysis"\nuse "$root/data/a.dta"\nsave "$root/data/b"\n'
    with drive.pi():
        _files(drive.official, {rel: text, "analysis/data/a.dta": "x"})
        before = _calls(drive.official, rel)
        assert before[1] == []  # nothing unresolved on the PI's computer
        got = node_api.stata_payload(drive.official, "en")
        assert got["role"] == "pi" and got["may_answer"]
        (proposal,) = got["proposals"]
        assert (proposal["global"], proposal["folder"], proposal["reason"], proposal["found"]) == (
            "root", "analysis", "local_root", 1)
        assert "this Mac’s project path" in proposal["sentence"] and not CJK.search(proposal["sentence"])
        assert "这台电脑上的项目路径" in node_api.stata_payload(drive.official)["proposals"][0]["sentence"]
        assert stata_record.answer(drive.official, "root", "analysis", via="app")["changed"]
        assert _calls(drive.official, rel) == before  # nothing changes on the PI's computer
        assert node_api.stata_payload(drive.official)["proposals"] == []
        share.publish(drive.official)
    with drive.member():
        share.sync(drive.work)
        calls, skipped = _calls(drive.work, rel)
        assert calls == [("analysis/data/a.dta", ("root", "analysis")), ("analysis/data/b.dta", ("root", "analysis"))]
        assert skipped == []


# -- on the page (13.6) -------------------------------------------------------------------------------

_PAYLOAD = {
    "has_do": True, "role": "single", "may_answer": True, "refusal": None, "record": {"state": "ok"},
    "proposals": [{"global": "MyProject", "folder": "analysis", "reason": "undefined", "paths": 23, "found": 21,
                   "sentence": "<engine sentence>", "answer": {"answer": "no", "by": "li@example.com"}}],
    "confirmed": [{"global": "root", "folder": ".", "by": "li@example.com"}],
    "unresolved": [{"reason": "undefined", "items": [{"file": "a.do", "line": 3, "command": "use", "text": "$x/b"}]},
                   {"reason": "working_folder", "items": [{"file": "a.do", "line": 5, "command": "use", "text": "c"}]}],
}


def _rendered(language: str | None, payload: dict) -> list[str]:
    i18n = "" if language is None else f"window.ROI18N_SAVED = {json.dumps(language)};\n" + _i18n_source()
    script = (
        "global.window = {addEventListener(){}, scrollTo(){}, scrollY: 0};\n" + i18n + _DESK_DOM
        + "global.document.createElementNS = (ns, tag) => Object.assign(el(tag), { setAttribute() {} });\n"
        + "global.humanRecordsBlocked = () => null;\n"
        + "const baseMk = mk;\n"
        + "global.mk = (tag, cls, text) => Object.assign(baseMk(tag, cls, text), "
        + "{ replaceChildren(...kids) { this.kids = []; this.append(...kids); } });\n"
        + "global.renderBlockingError = (node, framing) => { node.textContent = framing; };\n"
        + (WEBAPP / "node.js").read_text(encoding="utf-8")
        + f"\nconst payload = {json.dumps(payload, ensure_ascii=False)};\n"
        + r"""
const slot = mk("div", "slot");
window.RONode._renderStata(slot, mk("div", "host"), payload);
const words = [];
const walk = (e) => { for (const k of e.kids) { if (typeof k === "string") words.push(k); else walk(k); } };
walk(slot);
process.stdout.write(JSON.stringify(words));
"""
    )
    return _node(script)


@needs_node
def test_the_block_in_english_carries_no_chinese_and_in_chinese_reads_as_written():
    en = _rendered("en", _PAYLOAD)
    assert en[:5] == ["Stata globals", "<engine sentence>", "The last answer was “No” (li@example.com)", "Yes", "No"]
    assert "$root is confirmed as the project folder itself" in en and "Not resolved (2)" in en
    assert "The global is not defined (1)" in en and "The working folder cannot be told (1)" in en
    assert not [w for w in en if CJK.search(w)]
    zh = _rendered(None, _PAYLOAD)
    assert zh[:5] == ["Stata 全局变量", "<engine sentence>", "上次的回答是「不是」（li@example.com）", "是", "不是"]
    assert "$root 已确认指项目文件夹本身" in zh and "展开不了（2）" in zh and "全局变量没有定义（1）" in zh


@needs_node
def test_a_member_sees_no_buttons_and_the_refusal_sentence():
    payload = {**_PAYLOAD, "role": "member", "may_answer": False, "refusal": "<refusal sentence>"}
    words = _rendered("en", payload)
    assert "Yes" not in words and "No" not in words and words.count("<refusal sentence>") == 2
