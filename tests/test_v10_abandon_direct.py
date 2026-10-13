"""Research OS V10, increment A2b (DESIGN.md 14.3, 12.4): abandoning a route
asks for its reason and freezes its basis on a direct write too -- the PI's
and a single researcher's, not only an approved proposal's. A write that
TURNS a route abandoned (`route_status`: ☠, `[abandoned_verdicts]`) carries
a reason, else nothing is written (`reason_required`); the reason goes into
the result as an approval writes it, and the basis is frozen in
decisions.toml as an approval freezes it. Rewording a verdict that already
abandons asks nothing again; a member's row is unchanged (their proposal
asks). Every test names its scenario."""

from __future__ import annotations

import json

import pytest

from researchos import desk, history, nodepage, proposals, signing
from researchos.records import decisions as decisions_mod
from researchos.records.identity import read_identity
from researchos.webapp import server

from test_v8_phase2 import _call, _conn, _project, _scan
from test_v8_phase3 import _serve
from test_v8_phase4 import PI, Drive
from test_v9_language import APP_HTML, WEBAPP, _app_function, _i18n_source, _node, english_table, needs_node

A16 = "attempt:map.md#16"
ABANDON = {"verdict": "☠️ 伪协整"}


@pytest.fixture
def drive(tmp_path, monkeypatch) -> Drive:
    """12.10.3's PI and a member who joined from the shared folder."""
    if signing.tool() is None:
        pytest.skip("ssh-keygen is not installed here")
    return Drive(tmp_path, monkeypatch)


def _write(root, fields=ABANDON, reason=None, op="update", number="16"):
    return proposals.write_directly(root, op, number, fields, reason)


def _refused(root, fields=ABANDON, op="update", number="16") -> proposals.ProposalRefused:
    before = (root / "map.md").read_bytes()
    with pytest.raises(proposals.ProposalRefused) as refused:
        _write(root, fields, op=op, number=number)
    assert (root / "map.md").read_bytes() == before  # nothing written
    return refused.value


def _row(root, number="16") -> str:
    return next(line for line in (root / "map.md").read_text().splitlines() if line.startswith(f"| {number} |"))


def test_14_3_a_single_researcher_abandoning_is_asked_why_and_the_basis_is_frozen(tmp_path):
    """One person, no 「我是谁」: ✅ to ☠ without a reason is refused, nothing
    written; with one, the reason follows the result in the table as an
    approval writes it, and decisions.toml holds the abandonment with its
    basis frozen -- the step scripts and their outputs, the text by content
    -- by nobody named, approved by nobody; 研究路线 and the node page read it."""
    root = _project(tmp_path / "proj")
    refused = _refused(root)
    assert refused.code == "reason_required" and refused.message_zh == "放弃一条路线时必须写明理由"
    assert decisions_mod.entries(root) == [] and not list((root / ".researchos" / "backups").glob("map.md.*"))

    done = _write(root, reason="残差不平稳，协整不成立")
    assert _row(root) == ("| 16 | 07-26 | TopicShift volatility (16-17) | TopicShift->RV "
                          "| t=2.91（放弃理由：残差不平稳，协整不成立） | ☠️ 伪协整 |")
    [entry] = decisions_mod.entries(root)
    assert done["decision"]["id"] == entry["id"]
    assert (entry["act"], entry["kind"], entry["attempt"], entry["number"]) == ("abandoned", "verdict_change", A16, "16")
    assert (entry["verdict"], entry["result"], entry["reason"], entry["via"]) == (
        "☠️ 伪协整", "t=2.91", "残差不平稳，协整不成立", "app")
    assert not {"proposal", "approved_by", "by"} & set(entry)
    evidence = {e["path"]: e for e in entry["evidence"]}
    assert evidence["steps/16-build.py"]["role"] == "script" and evidence["steps/16-build.py"]["state"] == "kept"
    assert evidence["data/panel.csv"]["role"] == "output"
    old = (root / "steps" / "16-build.py").read_text()
    (root / "steps" / "16-build.py").write_text("# rewritten\n")
    with history.Store.open(read_identity(root).identity.id) as store:
        assert store.read_blob(evidence["steps/16-build.py"]["sha256"]).decode() == old

    conn = _conn(root)
    try:
        [gone] = [i for g in nodepage.routes(conn, root)["groups"] for i in g["items"] if i["id"] == A16]
        basis = nodepage.evidence(conn, root, A16)["decisions"][0]
        card = next(a for a in desk.payload(conn, root, lang="en")["attempts"] if a["id"] == A16)
    finally:
        conn.close()
    assert (gone["result"], gone["reason"], gone["reason_from"]) == ("t=2.91", "残差不平稳，协整不成立", "decision")
    # The desk's card carries the same decision for its panel (ruled 2026-10-13: 「由 {who} 放弃」).
    assert (card["reason"], card["decision"]["act"], card["decision"]["proposal"], card["decision"]["by"]["label"]) == (
        "残差不平稳，协整不成立", "abandoned", None, "Original researcher")
    assert basis["approved_by"] is None and {f["path"] for f in basis["files"]} == set(evidence)


def test_14_3_only_a_write_that_turns_the_route_abandoned_asks(tmp_path):
    """Rewording an abandoned verdict, changing its result, or taking the
    route up again asks nothing and records nothing; abandoning it again
    asks again. A marker the project declares abandons as ☠ does."""
    root = _project(tmp_path / "proj")
    _write(root, reason="伪协整")
    _write(root, {"verdict": "☠️ 伪协整，换一个方向"})
    _write(root, {"result": "t=0.41（放弃理由：伪协整）"})
    _write(root, {"verdict": "✅ 重新启用"})
    assert len(decisions_mod.entries(root)) == 1
    assert _refused(root).code == "reason_required"  # ✅ to ☠ once more: asked again
    toml = root / ".researchos" / "attempts.toml"
    toml.write_text('abandoned_verdicts = ["🗑"]\n' + toml.read_text())
    assert _refused(root, {"verdict": "🗑 不做了"}).code == "reason_required"
    _write(root, {"verdict": "🗑 不做了"}, "样本太短")
    assert decisions_mod.entries(root)[-1]["verdict"] == "🗑 不做了" and len(decisions_mod.entries(root)) == 2


def test_14_3_a_new_abandoned_row_is_asked_and_the_reason_follows_the_cell_as_written(tmp_path):
    """A new row whose verdict abandons is asked too; with no result its
    cell is the reason alone. On an existing row whose result the user did
    not touch, the reason follows the cell as written -- its Markdown, its
    <br> and its escaped bar kept -- never its cleaned text."""
    root = _project(tmp_path / "proj")
    new = {"date": "08-01", "description": "Dead end (18)", "variables": "a", "verdict": "☠️ 死路"}
    assert _refused(root, new, op="append", number="18").code == "reason_required"
    _write(root, new, "样本太短", op="append", number="18")
    assert _row(root, "18") == "| 18 | 08-01 | Dead end (18) | a | 放弃理由：样本太短 | ☠️ 死路 |"
    entry = decisions_mod.entries(root)[-1]
    assert (entry["kind"], entry["attempt"], entry["reason"]) == ("attempt_new", "attempt:map.md#18", "样本太短")
    assert "result" not in entry

    (root / "map.md").write_text((root / "map.md").read_text().replace("| t=2.91 |", "| **t=2.91**<br>p \\| q |"))
    _scan(root)
    _write(root, reason="残差不平稳")
    assert "| **t=2.91**<br>p \\| q（放弃理由：残差不平稳） | ☠️ 伪协整 |" in _row(root)
    assert decisions_mod.entries(root)[-1]["result"] == "t=2.91\np | q"


def test_14_3_the_preview_says_a_reason_is_needed_and_shows_where_it_goes(tmp_path):
    """The dry run says whether this write needs a reason; given one, the
    new row shows it in the result as the write will. Nothing is written."""
    root = _project(tmp_path / "proj")
    before = (root / "map.md").read_bytes()
    body = {"op": "update", "number": "16", "fields": dict(ABANDON)}
    out = server.attempts_preview_payload(root, body)
    assert out["reason_required"] is True and out["abandons"] is True and "放弃理由" not in out["new_row"]
    out = server.attempts_preview_payload(root, {**body, "reason": "残差不平稳"})
    assert out["reason_required"] is True and "| t=2.91（放弃理由：残差不平稳） | ☠️ 伪协整 |" in out["new_row"]
    out = server.attempts_preview_payload(root, {"op": "update", "number": "16", "fields": {"result": "t=3"}})
    assert out["reason_required"] is False
    assert (root / "map.md").read_bytes() == before and decisions_mod.entries(root) == []
    _write(root, reason="残差不平稳")
    out = server.attempts_preview_payload(root, {**body, "fields": {"verdict": "☠️ 伪协整，换一个方向"}})
    assert out["reason_required"] is False and out["abandons"] is True


def test_14_3_the_pi_is_asked_too_and_a_members_row_is_unchanged(drive):
    """12.10.3's two people: the PI abandoning directly is asked and the
    basis frozen, by the PI, approved by nobody. A member's row in their own
    copy is not asked and records nothing -- their verdict change becomes a
    proposal, which asks for the result and the reason as before."""
    with drive.pi():
        assert _refused(drive.official).code == "reason_required"
        _write(drive.official, reason="残差不平稳")
        entry = decisions_mod.entries(drive.official)[-1]
        assert entry["by"] == PI[1] and not {"proposal", "approved_by"} & set(entry) and entry["evidence"]
        assert "t=2.91（放弃理由：残差不平稳）" in _row(drive.official)
    with drive.member(), _serve(drive.work) as base:  # the member's own app opens the copy
        body = {"op": "update", "number": "16", "fields": dict(ABANDON)}
        status, out = _call(f"{base}/api/attempts/preview", "POST", body)
        assert status == 200 and out["reason_required"] is False and out["abandons"] is True
        status, out = _call(f"{base}/api/attempts/write", "POST", body)
        assert status == 200 and out["decision"] is None
        assert _row(drive.work).endswith("| t=2.91 | ☠️ 伪协整 |") and decisions_mod.entries(drive.work) == []
        with pytest.raises(proposals.ProposalRefused) as refused:
            proposals.make_proposal(drive.work, "verdict_change", "放弃 16")
        assert refused.value.code == "reason_required"


def test_14_3_a_decisions_file_that_cannot_take_the_basis_refuses_before_anything_is_written(tmp_path):
    root = _project(tmp_path / "proj")
    decisions_mod.decisions_path(root).write_text("[[decision]\nbroken\n")
    with pytest.raises(proposals.ProposalRefused) as refused:
        _write(root, reason="伪协整")
    assert refused.value.code == "decisions_unwritable" and "decisions.toml" in refused.value.message_zh
    assert "☠" not in _row(root)


def test_14_3_http_and_the_page(tmp_path):
    """Through the app: the preview asks, the write without a reason is
    refused with its own state and sentence, with one it lands and the node
    page shows the frozen basis. The form asks only when the preview says
    so and sends the reason; its words are in the English table."""
    root = _project(tmp_path / "proj")
    body = {"op": "update", "number": "16", "fields": dict(ABANDON)}
    with _serve(root) as base:
        status, out = _call(f"{base}/api/attempts/preview", "POST", body)
        assert status == 200 and out["reason_required"] is True
        status, out = _call(f"{base}/api/attempts/write", "POST", body)
        assert status == 400 and out["state"] == "attempt_reason_required"
        assert out["message_zh"] == "放弃一条路线时必须写明理由" and "☠" not in _row(root)
        status, out = _call(f"{base}/api/attempts/write", "POST", {**body, "reason": 5})
        assert status == 400 and "☠" not in _row(root)
        status, out = _call(f"{base}/api/attempts/write", "POST", {**body, "reason": "残差不平稳"})
        assert status == 200 and out["decision"] == decisions_mod.entries(root)[-1]["id"]
        status, page = _call(f"{base}/api/evidence?node=attempt%3Amap.md%2316")
        assert status == 200 and page["decisions"][0]["reason"] == "残差不平稳" and page["decisions"][0]["files"]

    sentence = "放弃一条路线时必须写明理由：它会写进结果一栏，这条路线当时的依据也会冻结下来。"
    assert f'attempt_reason_required: "{sentence}"' in APP_HTML.read_text(encoding="utf-8")
    form = _app_function("function renderAttemptForm(")
    assert "if (!proposing) {" in form and 'directReason.classList.toggle("hidden", !preview.reason_required);' in form
    assert "body.reason = reasonInput.value.trim();" in form
    table = english_table()
    assert table[sentence].startswith("Abandoning a route needs a reason")
    for key in ("放弃的理由（必填）", "为什么放弃这条路线", "放弃一条路线时必须写明理由",
                "理由会写进结果一栏，这条路线当时的依据也会冻结下来。"):
        assert key in form and key in table


def test_14_3_while_the_team_file_cannot_be_read_an_abandoning_write_is_refused(tmp_path):
    """Ruled 2026-10-13: with team.toml unreadable nobody can tell the PI's
    direct write from a member's own row, so a write that would abandon a
    route is refused with its own sentence -- with or without a reason, in
    the preview and the write -- and nothing is written or recorded; a write
    that abandons nothing still lands."""
    root = _project(tmp_path / "proj")
    (root / ".researchos" / "team.toml").write_text("[[member]\nbroken\n")
    assert proposals.role_of(root)[0] == "unknown"
    refused = _refused(root)
    assert refused.code == "role_unknown" and refused.message_zh == proposals.DIRECT_ROLE_UNKNOWN
    with pytest.raises(proposals.ProposalRefused) as again:
        _write(root, reason="伪协整")
    assert again.value.code == "role_unknown" and "☠" not in _row(root) and decisions_mod.entries(root) == []
    body = {"op": "update", "number": "16", "fields": dict(ABANDON), "reason": "伪协整"}
    with _serve(root) as base:
        for endpoint in ("preview", "write"):
            status, out = _call(f"{base}/api/attempts/{endpoint}", "POST", body)
            assert (status, out["state"], out["message_zh"]) == (400, "attempt_role_unknown", proposals.DIRECT_ROLE_UNKNOWN)
    assert "☠" not in _row(root) and decisions_mod.entries(root) == []
    assert _write(root, {"result": "t=3.05"})["decision"] is None and "| t=3.05 |" in _row(root)
    assert english_table()[proposals.DIRECT_ROLE_UNKNOWN].startswith("The members file (team.toml) cannot be read")


#: 当时的依据 (team.js openEvidence) under node with app.html's helpers stood in: the meta lines
#: and the headings of each decision, in the order drawn.
_EVIDENCE_RUN = r"""
const el = (tag, cls, text) => ({ tag, cls: cls || "", text: text == null ? "" : String(text), kids: [],
  set innerHTML(v) { this.kids = []; }, appendChild(c) { this.kids.push(c); return c; }, append(...cs) { this.kids.push(...cs); } });
Object.assign(global, { mk: el, mkButton: (text, cls) => el("button", cls, text), state: {}, openPanel() {},
  panelTitle: el("h2"), panelSubtitle: el("div"), panelBody: el("div"), document: { getElementById: () => null },
  RODesk: { whenWords: () => "W", verdictInline: () => el("span", "status-label") } });
"""


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_14_3_a_direct_abandonment_reads_abandoned_by_never_proposer(lang):
    """Ruled 2026-10-13: an abandonment with no proposal reads 「由 {who}
    放弃」 ("Abandoned by {who}") wherever a decision is shown -- 研究路线's
    reason line and 「当时的依据」, whose file list is 「放弃时冻结的文件」;
    an approved one still names who proposed and who approved it."""
    direct = {"act": "abandoned", "proposal": None, "at": "2026-10-13T10:00:00+02:00", "by": {"label": "李"},
              "approved_by": None, "files": []}
    approved = {**direct, "proposal": "p-1", "by": {"label": "王"}, "approved_by": {"label": "李"}}
    i18n = f"window.ROI18N_SAVED = {json.dumps(lang)};\n" + _i18n_source()
    script = ("global.window = {};\n" + i18n + _EVIDENCE_RUN + (WEBAPP / "team.js").read_text(encoding="utf-8")
              + f"\nglobal.apiGet = async () => ({{ decisions: {json.dumps([direct, approved], ensure_ascii=False)} }});\n"
              "(async () => { await window.ROTeam.openEvidence('attempt:map.md#16', { number: '16', title: 't' });\n"
              "  const out = []; const walk = (n) => { if (!n || typeof n !== 'object') return;\n"
              "    if (n.cls === 'desk-meta' || n.cls === 'panel-h3') out.push(n.cls + ': ' + n.text); n.kids.forEach(walk); };\n"
              "  walk(panelBody);\n"
              "  const source = (d) => window.ROTeam._reasonSource({ reason: 'x', reason_from: 'decision', decision: d });\n"
              f"  out.push(source({json.dumps(direct, ensure_ascii=False)}), source({json.dumps(approved, ensure_ascii=False)}));\n"
              "  process.stdout.write(JSON.stringify(out)); })();\n")
    got = _node(script)
    by = "Abandoned by 李" if lang == "en" else "由 李 放弃"
    frozen = "Files frozen when it was abandoned" if lang == "en" else "放弃时冻结的文件"
    assert got == [f"desk-meta: W · {by}", f"panel-h3: {frozen}",
                   "desk-meta: W · 提议人 王 · 批准人 李", "panel-h3: 批准时冻结的文件",
                   by, "提议人 王，批准人 李"]
    assert not any("提议人" in line for line in got[:2])
