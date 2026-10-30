"""Research OS V16, the fixes to task B3 (DESIGN.md 21.5, 21.8): the pages under node, in both languages, one test per
finding of the review -- the day and the person of a file put out of the paper, the paper block of a project with no
draft to choose, a file name that is text and not the control of the picker, a fold of old copies that is built when it is
opened, 撤销 that stays in view, and the word for a file a task registered. Every test names its scenario."""

from __future__ import annotations

import re

import pytest

from researchos import project as project_identity
from researchos.records import papers

from test_v16_b3_fixes import _extra_work, _register_extra
from test_v16_paper_rows import A2, EXTRA_FIG, EXTRA_SCRIPT, root  # noqa: F401 -- the scenario's fixtures
from test_v16_paper_rows_js import _page_run
from test_v16_provenance import ATTEMPTS, FIG, MAP, PKG, _choose, _data_code, _page, _results, _write, thesis
from test_v16_provenance_js import _json, _run
from test_v8_phase2 import _scan
from test_v9_language import WEBAPP, needs_node

_PLACED = r"""
(async () => {
  await window.RONode.loadResults(host, false);
  const fold = byClass(host, "dc-fold").find((d) => text(d.kids[0]).startsWith(%(fold_title)s));
  toggle(fold);
  const row = rowOf2(%(needs)s);
  const fields = byClass(row, "res-field").map(text);
  process.stdout.write(JSON.stringify(fields));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_node
@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("team", [False, True])
def test_a_row_put_out_of_the_paper_says_the_day_and_in_a_team_who_put_it_out(root, language, team):  # noqa: F811
    zh = language == "zh"
    _choose(root)
    papers.set_not_in_paper(root, FIG["needs"], True, via="app")
    page = _results(root)
    entry = page["excluded"][0]["paper"]
    assert entry["out"] is True and entry["at"] and entry["by"] is None  # one person: the engine names nobody
    if team:
        entry["by"] = "Wang Mei"  # what `nodepage.results` sends in a team (`_person`)
    day = entry["at"][:10]  # the harness words a day as its date
    fields = _page_run([page], _PLACED % {"fold_title": _json("不属于论文（" if zh else "Not part of the paper ("), "needs": _json(FIG["needs"])},
                       language=language)
    label = "放入「不属于论文」" if zh else "Put under “Not part of the paper”"
    sep = "、" if zh else ", "
    assert [f for f in fields if f.startswith(label)] == [label + day + (sep + "Wang Mei" if team else "")]


_NO_DRAFT = r"""
(async () => {
  await window.RONode.loadResults(host, false);
  const block = byClass(host, "res-paper")[0];
  process.stdout.write(JSON.stringify({ text: byClass(block, "node-empty").map(text), sub: byClass(block, "node-sub").map(text),
    buttons: block.all().filter((k) => k.tag === "BUTTON").map(text) }));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_node
@pytest.mark.parametrize("language", ["zh", "en"])
def test_a_project_whose_only_draft_is_the_attempt_table_does_not_tell_the_person_to_press_a_control_that_is_not_there(tmp_path, language):
    root = tmp_path / "tasks_only"
    _write(root, "map.md", MAP)
    _write(root, f"{PKG}/code/02-plots.py", "import matplotlib.pyplot as plt\n" + 'plt.savefig("../current/Plots/fig_main.png")\n')
    _write(root, f"{PKG}/current/Plots/fig_main.png", b"\x89PNG main")
    project_identity.init_project(root)
    _write(root, ".researchos/attempts.toml", ATTEMPTS)
    _scan(root)
    page = _results(root)
    assert [d["file"] for d in page["drafts"]] == ["map.md"] and page["papers"]["recent"] == []  # the table is read as a draft, and is never offered
    zh = language == "zh"
    out = _page_run([page], _NO_DRAFT, language=language)
    assert out["text"] == ["还没有读到文稿。" if zh else "No drafts have been found yet."]
    assert out["buttons"] == [] and not [s for s in out["sub"] if "设为论文正文" in s or "Set as the paper" in s]


_FOLD = r"""
(async () => {
  const host = el("div", "host"); views.lineage = host;
  await window.RONode.loadDataCode(host, false);
  const row = rowOf(host, "dc-row", %(path)s);
  const fold = byClass(row, "dc-fold")[0];
  const out = { summary: text(fold.kids[0]), before: fold.kids[1].kids.length };
  toggle(fold);
  out.after = fold.kids[1].kids.map(text);
  out.word = text(fold.kids[0]);
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_node
@pytest.mark.parametrize("language", ["zh", "en"])
def test_the_old_copies_of_a_file_are_built_when_its_fold_is_first_opened(tmp_path, language):
    root = thesis(tmp_path / "thesis")
    data = _data_code(root)
    path = next(f["path"] for f in data["files"] if len(f.get("other_copies") or []) >= 2)
    copies = next(f for f in data["files"] if f["path"] == path)["other_copies"]
    body = (f"global.apiGet = async (p) => (p === '/api/data-code' ? {_json(data)} : {{ has_do: false }});\n"
            + _FOLD % {"path": _json(path)})
    out = _run(body, language=language)
    zh = language == "zh"
    assert out["summary"].startswith(f"其他同名文件 {len(copies)} 个" if zh else f"{len(copies)} other same-name files")
    assert out["before"] == 0  # nothing built for a row nobody opened
    assert out["after"] == copies and out["word"].endswith("收起" if zh else "Show less")


_VIA = r"""
(async () => {
  const host = el("div", "host");
  window.RONode.renderTrace(host, %(page)s, {});
  process.stdout.write(JSON.stringify(byClass(host, "trace-belongs").map(text)));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_node
@pytest.mark.parametrize("language", ["zh", "en"])
def test_a_figure_page_names_the_attempt_of_the_task_that_registered_it_and_says_how(root, language):  # noqa: F811
    _extra_work(root)
    _register_extra(root)
    page = _page(root, f"figure:{EXTRA_FIG}")
    out = _run(_VIA % {"page": _json(page)}, language=language, view="node")
    zh = language == "zh"
    assert len(out) == 1 and "#2 Agent plots" in out[0] and ("（任务登记）" if zh else "(Registered by a task)") in out[0]
    assert "还没有归入任何尝试" not in out[0]


def test_the_notice_with_撤销_stays_in_view_while_a_long_list_is_read():
    css = (WEBAPP / "app.html").read_text(encoding="utf-8")
    rule = re.search(r"^\.dc-undo \{([^}]*)\}", css, re.M)
    assert rule is not None
    assert "position: sticky" in rule.group(1) and "top: 0" in rule.group(1) and "background: var(--paper)" in rule.group(1)
