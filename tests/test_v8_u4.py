"""Research OS V8 phase U4 (DESIGN.md 12.1-12.5, 12.9, 12.11): 研究路线 (the
attempts grouped by status, proposals shown apart, an abandoned route with
its result, its reason and the basis its approval froze), 提议 (the PI's
inbox and a proposal's page; a member's 我的提议 and 提交提议 with the
engine's dry run first) and 成员 (the team, who I am, the shared folder,
adding a member, joining and syncing -- each saying what it will write
before it writes). Every test names its scenario."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from researchos import nodepage, people, proposals, share, signing
from researchos import exchange as exchange_mod
from researchos.records import cards
from researchos.records import notes as notes_mod
from researchos.webapp import node_api, server

from test_v8_phase2 import _call, _conn, _map, _project, _scan
from test_v8_phase3 import _serve
from test_v8_phase4 import MEMBER, PI, Drive, _as, _member_new_attempt
from test_v8_u2 import FORBIDDEN, WEBAPP

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
needs_ssh = pytest.mark.skipif(signing.tool() is None, reason="ssh-keygen is not installed here")

TABLE = (
    "# Map\n\n## Attempt timeline\n\n| # | Date | Path | Variables | Result | Verdict |\n|---|---|---|---|---|---|\n"
    "| 14 | 07-10 | Old route (14) | a | t=0.4（放弃理由：伪协整） | ☠️ 伪协整 |\n"
    "| 15 | 07-12 | Archived (15) | a | 56 | 📋 归档 |\n"
    "| 16 | 07-26 | TopicShift volatility (16-17) | TopicShift->RV | t=2.91 | ✅ 现行 |\n"
    "| 17 | 07-27 | Weak (17) | a | t=1.1 | ⚠️ 弱 |\n"
    "| 18 | 07-30 | Waiting (18) | a |  | 🕒 待拍板 |\n"
    "| 19 | 07-31 | Rejected (19) | a | t=3 | ⛔ 方向被否 |\n"
)


@pytest.fixture
def drive(tmp_path, monkeypatch) -> Drive:
    if signing.tool() is None:
        pytest.skip("ssh-keygen is not installed here")
    return Drive(tmp_path, monkeypatch)


def _routes(root: Path) -> dict:
    conn = _conn(root)
    try:
        return nodepage.routes(conn, root)
    finally:
        conn.close()


# -- 研究路线 (12.9) ---------------------------------------------------------------------------------


def test_u4_routes_are_grouped_live_first_and_an_abandoned_route_keeps_its_table_reason(tmp_path):
    """研究路线: 现行 and 待定 first, then 不利证据, 已放弃, 被否, 归档; each
    row its number, date, the researcher's title, the status and their own
    words; an abandoned route's reason read from the table's own
    「（放弃理由：…）」, the result shown without it."""
    root = _project(tmp_path / "proj")
    (root / "map.md").write_text(TABLE)
    _scan(root)
    out = _routes(root)
    assert [g["label"] for g in out["groups"]] == ["现行", "待定", "不利证据", "已放弃", "被否", "归档"]
    current = out["groups"][0]["items"][0]
    assert (current["number"], current["date"], current["title"]) == ("16", "07-26", "TopicShift volatility (16-17)")
    assert current["verdict_status"] == {"status": "current", "label": "现行", "words": "现行"}
    [gone] = out["groups"][3]["items"]
    assert gone["result"] == "t=0.4" and gone["reason"] == "伪协整" and gone["reason_from"] == "table"
    assert gone["verdict_status"]["words"] == "伪协整" and "decision" not in gone


def test_u4_an_approved_abandonment_shows_its_reason_who_and_its_frozen_basis(drive):
    """12.4 through 研究路线: a member proposes abandoning #16 with a result
    and a reason; once the PI approves, the route reads its reason from
    decisions.toml (提议人, 批准人), 「当时的依据」 lists the step scripts and
    outputs frozen then -- each with whether it is still that version --
    and a kept text version opens as it was, even after it changed."""
    with drive.member():
        (drive.work / "map.md").write_text(_map("☠️ 伪协整").replace("t=2.91", "t=0.41"))
        stored = proposals.write_proposal(drive.work, proposals.make_proposal(
            drive.work, "verdict_change", "这条路线放弃", reason="残差不平稳"))
    with drive.pi():
        proposals.approve(drive.official, stored.id, note="同意")
        _scan(drive.official)
        out = _routes(drive.official)
        [gone] = [g for g in out["groups"] if g["status"] == "abandoned"][0]["items"]
        assert gone["reason"] == "残差不平稳" and gone["reason_from"] == "decision"
        assert gone["result"] == "t=0.41"  # the 「（放弃理由：…）」 the approval wrote is the reason, said once
        assert gone["decision"]["by"]["label"] == MEMBER[0] and gone["decision"]["approved_by"]["label"] == PI[0]
        assert gone["decision"]["evidence_count"] >= 2
        node = _node(drive.official, gone["id"])
        assert node["details"]["basis"] == 1
        (drive.official / "steps" / "16-build.py").write_text("# changed after\n")
        _scan(drive.official)
        conn = _conn(drive.official)
        try:
            basis = nodepage.evidence(conn, drive.official, gone["id"])
        finally:
            conn.close()
        [decision] = basis["decisions"]
        assert decision["reason"] == "残差不平稳" and decision["note"] == "同意"
        files = {f["path"]: f for f in decision["files"]}
        assert files["steps/16-build.py"]["role_label"] == "步骤脚本" and files["steps/16-build.py"]["now"] == "changed"
        assert files["steps/17-plot.py"]["now"] == "same"
        kept = files["steps/16-build.py"]
        assert kept["viewable"]
        then = nodepage.version_content(drive.official, kept["node"], kept["version"])
        assert then["text"] == (drive.work / "steps" / "16-build.py").read_text()
        with pytest.raises(nodepage.NotFound):
            nodepage.version_content(drive.official, kept["node"], "999999")
        with pytest.raises(ValueError):
            nodepage.version_content(drive.official, kept["node"], "x")


def _node(root: Path, node: str) -> dict:
    conn = _conn(root)
    try:
        return nodepage.node(conn, root, node)
    finally:
        conn.close()


# -- 提议 (12.3) -----------------------------------------------------------------------------------


def test_u4_a_proposal_page_says_its_variable_versions_and_not_carried_changes_in_words(drive):
    """12.3: the PI's page of a proposal names the variable versions its
    files use -- a script that reads a card's output -- and every change not
    sent, in words (the engine's English kept for 「详情」); a large file
    says where it lives; no summary carries an arrow (12.11)."""
    with drive.pi():
        cards.new_card(drive.official, "panelx")
        (notes_mod.notes_path(drive.official).parent / "variables" / "panelx" / "v1.toml").write_text(
            'name = "Panel X"\nmeaning = \'\'\'x\'\'\'\nunit = "1"\ngranularity = "月"\n\n'
            '[[input]]\ndataset = "data/raw.csv"\n\n[construction]\nformula = \'\'\'x\'\'\'\n\n'
            '[implementation]\nscript = "steps/16-build.py"\noutput = "data/panel.csv"\nfield = "x"\n\n'
            "[decision]\nwhy = '''because'''\n", encoding="utf-8")
        cards.confirm(drive.official, "panelx", attested="yes")
    with drive.member():
        _member_new_attempt(drive)
        (drive.work / "data" / "raw.csv").write_text("id,x\n1,2\n2,3\n")
        (drive.work / "map.md").write_text((drive.work / "map.md").read_text() + "| 19 | 08-03 | Another | a |  | 🕒 |\n")
        chosen = ["steps/18-big.py", "data/big.bin", "data/raw.csv"]  # 12.13: the member adds two files
        draft = node_api.proposal_create_payload(drive.work, {"kind": "attempt_new", "sentence": "大面板", "number": "18",
                                                              "only": chosen})
        reasons = [n["reason_zh"] for n in draft["proposal"]["not_carried"]]
        assert "尝试表第 19 行（新增）不在这份提议里" in reasons
        assert draft["proposal"]["attempt"]["verdict_status"]["label"] == "待定"
        written = node_api.proposal_create_payload(drive.work, {"kind": "attempt_new", "sentence": "大面板", "number": "18",
                                                                "only": chosen, "write": True,
                                                                "expected": draft["fingerprint"]})
    with drive.pi():
        page = node_api.proposal_payload(drive.official, written["id"])
        uses = {(u["card"], u["path"]) for u in page["uses"]}
        assert ("panelx", "steps/18-big.py") in uses  # it reads data/panel.csv, the card's output
        assert ("panelx", "data/raw.csv") in uses  # the card's input, changed
        assert any(n["reason_zh"] == "尝试表第 19 行（新增）不在这份提议里" for n in page["changes"]["not_carried"])
        files = {f["path"]: f for f in page["changes"]["files"]}
        assert "+1,2" in files["data/raw.csv"]["diff"]  # a small table is text: its lines
        big = files["data/big.bin"]  # a large file: where it lives on the shared folder
        assert big["store"] == "blob" and Path(big["content"]).is_relative_to(drive.shared)
        assert all(not FORBIDDEN.search(f.get("summary_words") or "") for f in files.values())


def test_u4_the_attempt_preview_says_whether_a_verdict_abandons(tmp_path):
    """A member's 提议改判 asks for the result and the reason before anything
    is written: the dry run of a row says whether its verdict abandons the
    route (☠, or a marker the project declares)."""
    root = _project(tmp_path / "proj")
    out = server.attempts_preview_payload(root, {"op": "update", "number": "16", "fields": {"verdict": "☠️ 死"}})
    assert out["abandons"] is True and out["verdict_status"]["label"] == "已放弃"
    out = server.attempts_preview_payload(root, {"op": "update", "number": "16", "fields": {"verdict": "✅"}})
    assert out["abandons"] is False
    out = server.attempts_preview_payload(root, {"op": "update", "number": "16", "fields": {"result": "t=3"}})
    assert "abandons" not in out


# -- 成员: joining and syncing say what they will write (12.3, 12.5) ----------------------------------


def test_u4_join_preview_names_the_pi_key_and_what_it_writes_and_writes_nothing(drive, tmp_path):
    """12.5: before joining, the member sees the PI and the fingerprint of
    the key that will be trusted, the snapshot, how many files and bytes it
    writes where -- and nothing is written; a join whose PI key is no
    longer the one shown is refused, nothing written."""
    other = drive.tmp / "home-other"
    with _as(drive.mp, other):
        people.write_me("赵", "zhao@example.com")
        target = tmp_path / "zhao-copy"
        seen = share.join_preview(drive.shared, target)
        assert not target.exists()
        assert seen["pi"] == PI[1] and seen["pi_name"] == PI[0] and seen["seq"] == drive.first["seq"]
        assert seen["pi_fingerprint"] == drive.joined["pi_fingerprint"] and seen["role"] == "none"
        assert seen["files"] > 0 and seen["size"] > 0 and seen["folder"] == str(target)
        with pytest.raises(share.ShareError) as refused:
            share.join(drive.shared, target, expect_pi="SHA256:not-the-one")
        assert refused.value.code == "changed_since_shown" and not target.exists()
        done = share.join(drive.shared, target, expect_pi=seen["pi_fingerprint"])
        assert done["files"] == seen["files"] and target.is_dir()


def test_u4_sync_preview_lists_what_sync_would_update_and_put_beside_and_writes_nothing(drive):
    """12.3: 同步 says first what it will do -- files updated, the ones both
    sides changed left as the member's with the official copy beside."""
    with drive.pi():
        (drive.official / "steps" / "16-build.py").write_text("# PI's version\n")
        (drive.official / "steps" / "17-plot.py").write_text("# PI's plot\n")
        share.publish(drive.official, note="PI 改了两个脚本")
    with drive.member():
        (drive.work / "steps" / "16-build.py").write_text("# member's version\n")
        before = {p: p.read_bytes() for p in (drive.work / "steps").iterdir()}
        p = share.sync_preview(drive.work)
        assert p["to"] == p["from"] + 1 and p["update"] == ["steps/17-plot.py"]
        assert p["beside"] == [{"path": "steps/16-build.py", "official_copy": "steps/16-build.official.py"}]
        assert {q: q.read_bytes() for q in (drive.work / "steps").iterdir()} == before
        done = share.sync(drive.work)
        assert done["updated"] == ["steps/17-plot.py"] and done["conflicts"][0]["official_copy"] == "steps/16-build.official.py"
        assert share.sync_preview(drive.work)["up_to_date"] is True


# -- the endpoints ----------------------------------------------------------------------------------


@needs_ssh
def test_u4_http_a_single_project_becomes_a_pi_and_a_member_joins_with_no_project_open(tmp_path, monkeypatch):
    """12.1-12.5 through the app's endpoints: one person sets who they are,
    writes the team file (POST /api/team/init), sets the shared folder (POST
    /api/share/set), publishes; a GET of /api/me/key makes no key, POST
    makes one; the PI adds the member from their key file (key_file); the
    member -- whose engine serves no project -- looks (join-preview: writes
    nothing) and joins, and the server opens the copy."""
    pi_home, m_home = tmp_path / "pi-home", tmp_path / "m-home"
    shared = tmp_path / "shared"
    shared.mkdir()
    with _as(monkeypatch, pi_home):
        root = _project(tmp_path / "proj")
        with _serve(root) as base:
            status, body = _call(f"{base}/api/me/key")
            assert status == 200 and body["key"] is None and not Path(body["path"]).exists()
            status, body = _call(f"{base}/api/team/init", "POST", {})
            assert status == 409 and body["state"] == "no_me"
            assert _call(f"{base}/api/me", "POST", {"name": PI[0], "email": PI[1]})[0] == 200
            status, body = _call(f"{base}/api/team/init", "POST", {})
            assert status == 200 and body["pi"] == PI[1] and body["fingerprint"]
            status, body = _call(f"{base}/api/share/set", "POST", {"shared": "relative/x"})
            assert status == 400 and body["state"] == "not_absolute"
            status, body = _call(f"{base}/api/share/set", "POST", {"shared": str(root / "data")})
            assert status == 409 and body["state"].startswith("exchange_") and body["message"]
            status, body = _call(f"{base}/api/share/set", "POST", {"shared": str(shared)})
            assert status == 200 and body["shared"] == str(shared)
            status, body = _call(f"{base}/api/share/publish", "POST", {"note": "起点"})
            assert status == 200 and body["seq"] == 1
    with _as(monkeypatch, m_home):
        people.write_me(*MEMBER)
        key = share.my_key()
        keyfile = tmp_path / "wang.pub"
        keyfile.write_text(key["key"] + "\n")
    with _as(monkeypatch, pi_home):
        with _serve(root) as base:
            status, body = _call(f"{base}/api/team/add", "POST", {"id": MEMBER[1], "name": MEMBER[0], "key_file": "x.pub"})
            assert status == 400 and body["state"] == "not_absolute"
            status, body = _call(f"{base}/api/team/add", "POST",
                                 {"id": MEMBER[1], "name": MEMBER[0], "key_file": str(keyfile)})
            assert status == 200 and body["fingerprint"] == key["fingerprint"] and "编辑权限" in body["sentence"]
            assert _call(f"{base}/api/share/publish", "POST", {})[0] == 200
    with _as(monkeypatch, m_home):
        empty = tmp_path / "m-empty"
        empty.mkdir()
        with _serve_none() as base:
            status, body = _call(f"{base}/api/projects")
            assert body["no_project"] is True
            work = tmp_path / "work"
            status, seen = _call(f"{base}/api/share/join-preview", "POST", {"shared": str(shared), "folder": str(work)})
            assert status == 200 and seen["role"] == "member" and not work.exists()
            status, body = _call(f"{base}/api/share/join", "POST",
                                 {"shared": str(shared), "folder": str(work), "expect_pi": seen["pi_fingerprint"]})
            assert status == 200 and body["opened"] is True and Path(body["working_copy"]) == work.resolve()
            status, projects = _call(f"{base}/api/projects")
            assert Path(projects["current"]).resolve() == work.resolve()
            status, body = _call(f"{base}/api/share/sync-preview")
            assert status == 200 and body["up_to_date"] is True
            status, body = _call(f"{base}/api/share/set", "POST", {"shared": str(shared)})
            assert status == 403 and body["state"] == "not_pi"


def _serve_none():
    """A server with no project (an empty registry, 10.1)."""
    import contextlib
    import threading

    @contextlib.contextmanager
    def run():
        httpd = server.build_server(None, 0)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{httpd.server_address[1]}"
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    return run()


def test_u4_http_routes_evidence_and_versions_answer(drive):
    """GET /api/routes (with officialness), /api/evidence (400 for what is
    not an attempt), /api/history/version (404 for a version not kept)."""
    with drive.pi():
        with _serve(drive.official) as base:
            status, body = _call(f"{base}/api/routes")
            assert status == 200 and body["groups"][0]["items"][0]["officialness"]["state"] == "official"
            status, body = _call(f"{base}/api/evidence?node=attempt%3Amap.md%2316")
            assert status == 200 and body["decisions"] == []
            status, body = _call(f"{base}/api/evidence?node=steps%2F16-build.py")
            assert status == 400 and body["message"] == "这不是一条尝试"
            status, body = _call(f"{base}/api/history/version?node=steps%2F16-build.py&version=999999")
            assert status == 404 and body["state"] == "no_such_version"


@pytest.mark.parametrize("method, path, body", [
    ("GET", "/api/routes", None),
    ("GET", "/api/evidence?node=attempt%3Amap.md%2316", None),
    ("GET", "/api/history/version?node=steps%2F16-build.py&version=1", None),
    ("GET", "/api/me/key", None),
    ("GET", "/api/share/sync-preview", None),
    ("GET", "/team.js", None),
    ("POST", "/api/me/key", {}),
    ("POST", "/api/team/init", {}),
    ("POST", "/api/share/set", {"shared": "/tmp"}),
    ("POST", "/api/share/join-preview", {"shared": "/tmp", "folder": "/tmp/x"}),
    ("POST", "/api/share/join", {"shared": "/tmp", "folder": "/tmp/x"}),
])
def test_u4_every_new_endpoint_is_origin_checked(tmp_path, method, path, body):
    root = _project(tmp_path / "proj")
    with _serve(root) as base:
        status, _ = _call(f"{base}{path}", method, body, headers={"Origin": "http://evil.example"})
        assert status == 403


def test_u4_team_js_is_served_packaged_and_loaded_by_the_page(tmp_path):
    """/team.js is served from the package (and listed in pyproject's
    package data); the page loads it after node.js; nothing placeholders
    提议 or 成员 any more; the desk's 查看 opens a proposal's page."""
    root = _project(tmp_path / "proj")
    with _serve(root) as base:
        import urllib.request

        with urllib.request.urlopen(f"{base}/team.js") as resp:
            assert resp.status == 200 and "window.ROTeam" in resp.read().decode("utf-8")
    pyproject = (Path(server.__file__).parents[3] / "pyproject.toml").read_text(encoding="utf-8")
    assert '"webapp/team.js"' in pyproject
    html = (WEBAPP / "app.html").read_text(encoding="utf-8")
    assert html.index('<script src="/node.js"></script>') < html.index('<script src="/team.js"></script>')
    assert "PLACEHOLDER" not in html and "renderPlaceholder" not in html
    desk = (WEBAPP / "desk.js").read_text(encoding="utf-8")
    assert "ROTeam.openProposal(p.id, p.by)" in desk and "renderPlaceholder" not in desk


# -- the page's wording, run under node against the real team.js ------------------------------------


def _team_call(name: str, *args) -> object:
    script = (
        "global.window = {}; global.document = {getElementById(){return null}};\n"
        + (WEBAPP / "team.js").read_text(encoding="utf-8")
        + f"\nprocess.stdout.write(JSON.stringify(window.ROTeam.{name}(...{json.dumps(list(args))})));\n"
    )
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@needs_node
def test_u4_wording_sizes_states_signatures_and_what_each_act_writes():
    assert _team_call("_sizeWords", 512) == "512 字节"
    assert _team_call("_sizeWords", 209715200) == "200 MB"
    assert _team_call("_stateLabel", {"state": "returned"}) == {"label": "已退回", "tone": "gray"}
    assert _team_call("_signatureWords", {"state": "verified", "trusted": True})["label"] == "已验证"
    unsigned = _team_call("_signatureWords", {"state": "unsigned", "trusted": False, "message": "没有签名：无法确认是谁写的"})
    assert unsigned == {"label": "未验证", "tone": "clay", "reason": "没有签名：无法确认是谁写的"}
    assert _team_call("_approveSentence", {"published": {"published": True, "seq": 4}}) == \
        "已批准，改动已写入正式版本，并发布为第 4 版：成员同步后就会看到。"
    failed = _team_call("_approveSentence", {"published": {"published": False, "message": "公盘文件夹当前无法访问"}})
    assert "没有发布到公盘：公盘文件夹当前无法访问" in failed
    draft = {"file": [{"path": "a.py", "action": "added", "store": "text"},
                      {"path": "big.bin", "action": "added", "store": "blob"}, {"path": "x", "action": "removed"}]}
    sent = _team_call("_sendWrites", draft, "wang@example.com")
    assert "提议/wang@example.com/" in sent and "2 个文件" in sent and "1 个大文件" in sent
    share = {"last_snapshot": {"seq": 3}, "pending_publish": {"count": 2}}
    assert "第 4 版快照" in _team_call("_publishWrites", share) and "2 个文件不同" in _team_call("_publishWrites", share)
    assert "这是第一版" in _team_call("_publishWrites", {})
    sync = _team_call("_syncWrites", {"to": 5, "update": ["a"], "remove": [], "beside": [{"path": "b"}]})
    assert sync.startswith("同步会把工作副本带到第 5 版") and ".official" in sync
    join = _team_call("_joinWrites", {"folder": "/w", "seq": 2, "files": 10, "size": 2048})
    assert "/w" in join and "10 个文件" in join and "2.0 KB" in join
    item = {"reason": "x", "reason_from": "decision", "decision": {"by": {"label": "王"}, "approved_by": {"label": "李"}}}
    assert _team_call("_reasonSource", item) == "提议人 王，批准人 李"
    listed = [{"kind": "attempt_new", "state": "pending"}, {"kind": "files", "state": "pending"},
              {"kind": "verdict_change", "state": "returned"}, {"kind": "attempt_new", "state": "approved"}]
    assert len(_team_call("_routeProposals", listed, "pi")) == 1
    assert len(_team_call("_routeProposals", listed, "member")) == 2
    for text in (sent, sync, join, failed):
        assert not FORBIDDEN.search(text)
