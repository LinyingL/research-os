"""Research OS V14, task G4-1 (DESIGN.md 18.4 item 1): 图表与论断 traces once
per request. `trace.Shared` holds what every trace of a request would read
again -- the link flags, the attempts configuration with its verdict words,
the attempt rows and the step-file index, the variable cards, the file
checks -- and `trace.chain(..., shared=)` answers byte for byte as it does
without. The page's 设为论文正文 keeps the generation its POST returns.
Every test names its scenario."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from researchos import db, nodepage, trace
from researchos.ingest import attempts as attempts_ingest
from researchos.ingest import mappings as mappings_ingest
from researchos.records import cards, judgements
from researchos.records import variables as variables_mod

from test_v8_phase2 import ATTEMPTS_TOML, _conn, _csv, _scan
from test_v9_language import WEBAPP, _app_function, _node, needs_node


def _plot(figures: list[str], data: str = "data/panel.csv") -> str:
    lines = ["import pandas as pd", "import matplotlib.pyplot as plt", f'df = pd.read_csv("{data}")']
    lines += [f'plt.savefig("{figure}")' for figure in figures]
    return "\n".join(lines) + "\n"


def _write_card(root: Path, card_id: str, implementation: str) -> None:
    cards.new_card(root, card_id)
    (root / ".researchos" / "variables" / card_id / "v1.toml").write_text(
        f'name = "{card_id}"\nmeaning = \'\'\'x\'\'\'\nunit = "1"\ngranularity = "月"\n\n'
        '[[input]]\ndataset = "data/raw.csv"\n\n[construction]\nformula = \'\'\'x\'\'\'\n\n'
        f"[implementation]\n{implementation}\n\n[decision]\nwhy = '''because'''\n", encoding="utf-8")
    cards.confirm(root, card_id, attested="yes")


def _traced_project(root: Path, *, plots: int = 3, per_plot: int = 3, with_cards: bool = True) -> Path:
    """A project whose figures differ in what they trace to: figures of
    attempts 16 and 17 (by step number), one attached to attempt 18 by hand,
    plots no attempt claims, a figure nothing generates, one figure two
    sections include, a loose figure; two variable cards (one by output, one
    by a column); a link confirmed, one rejected, one hand-drawn."""
    for sub in ("steps", "data", "figs"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "data" / "raw.csv").write_text(_csv(3, ("id", "x", "y")))
    (root / "data" / "panel.csv").write_text(_csv(20, ("id", "x", "y")))
    (root / "steps" / "16-build.py").write_text(
        'import pandas as pd\ndf = pd.read_csv("data/raw.csv")\ndf.to_csv("data/panel.csv")\n')
    figures: list[list[str]] = []
    for k in range(plots):
        names = [f"figs/p{k}f{j}.png" for j in range(per_plot)]
        figures.append(names)
        (root / "steps" / f"{17 + k}-plot{k}.py").write_text(_plot(names))
        for name in names:
            (root / name).write_bytes(b"\x89PNG " + name.encode())
    (root / "figs" / "orphan.png").write_bytes(b"\x89PNG orphan")
    (root / "figs" / "loose.png").write_bytes(b"\x89PNG loose")
    (root / "steps" / f"{17 + plots}-loose.py").write_text(_plot(["figs/loose.png"]))
    body = "# Results\n\n"
    for k, names in enumerate(figures):
        body += f"## Section {k}\n\n" + "".join(f"![{n}]({n})\n\n" for n in names)
    body += "## Again\n\n![again](figs/p0f0.png)\n\n![orphan](figs/orphan.png)\n"
    body += "\n## Claims\n\nThe first effect is 0.42 in the panel.\n\nThe second effect is 0.17 in the panel.\n\n"
    body += "The third effect is 0.93 in the panel.\n\nThe fourth effect is 0.58 in the panel.\n"
    (root / "paper.md").write_text(body)
    (root / "map.md").write_text(
        "# Map\n\n## Attempt timeline\n\n| # | Date | Path | Variables | Result | Verdict |\n|---|---|---|---|---|---|\n"
        "| 16 | 07-26 | Build and first plot (16-17) | x | t=2.91 | ✅ |\n"
        "| 17 | 07-27 | Second plot (18) | y | t=1.10 | 🕒 |\n"
        "| 18 | 07-28 | By hand | z | | ☠️ |\n"
        "| 19 | 07-29 | Build again (16) | x | | ✅ |\n")
    from researchos import project as project_identity

    project_identity.init_project(root)
    (root / ".researchos" / "attempts.toml").write_text(ATTEMPTS_TOML)
    _scan(root)
    mappings_ingest.add_mapping(root, f"figs/p{plots - 1}f0.png", "map.md#18", "belongs_to")
    _scan(root)
    if with_cards:
        _write_card(root, "panelx", 'script = "steps/16-build.py"\noutput = "data/panel.csv"\nfield = "x"')
        _write_card(root, "ycol", 'script = "steps/16-build.py"\nfield = "y"')
    judgements.judge(root, ("script:steps/16-build.py", "dataset:data/raw.csv", "reads", "dataflow"), "confirmed", via="cli")
    judgements.judge(root, ("script:steps/18-plot1.py", "dataset:data/panel.csv", "reads", "dataflow"), "rejected", via="cli")
    _scan(root)
    # What the claims rest on, as a machine found it: a figure with its attempts, one without,
    # nothing at all, and a data file no attempt claims.
    conn = _conn(root)
    try:
        found = sorted(db.get_nodes_by_type(conn, "claim"), key=lambda c: c["attrs"].get("line") or 0)
        for claim, target in zip(found, ("figure:figs/p0f0.png", "figure:figs/orphan.png", None, "dataset:data/raw.csv")):
            if target:
                db.upsert_edge(conn, claim["id"], target, "backed_by", "claims", {"occurrences": [{"line": 1}]}, 0.8)
        conn.commit()
    finally:
        conn.close()
    return root


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=False)


def _every_node(conn) -> list[str]:
    ids = [n["id"] for kind in ("figure", "script", "dataset", "attempt") for n in db.get_nodes_by_type(conn, kind)]
    return [*ids, "figure:figs/never-existed.png", "file:paper.md"]


def test_a_trace_with_the_shared_context_is_byte_identical_to_one_without(tmp_path):
    """Every node of a project with several attempts, figures, two variable
    cards and judged links: `chain` alone, `chain` with one `Shared` reused
    for all of them (in the index's order and in reverse), and `chain` with
    `variables=False` (the same answer, no variables) all say the same
    thing, key for key and in the same order."""
    root = _traced_project(tmp_path / "proj")
    conn = _conn(root)
    try:
        nodes = _every_node(conn)
        alone = {n: _dump(trace.chain(conn, root, n)) for n in nodes}
        shared = trace.Shared(conn, root)
        forward = {n: _dump(trace.chain(conn, root, n, shared=shared)) for n in nodes}
        backward = {n: _dump(trace.chain(conn, root, n, shared=shared)) for n in reversed(nodes)}
        bare = {n: trace.chain(conn, root, n, shared=shared, variables=False) for n in nodes}
        full = {n: json.loads(alone[n]) for n in nodes}
    finally:
        conn.close()
    assert forward == alone and backward == alone
    for n in nodes:
        assert bare[n] == {**full[n], "variables": []}, n
    # The answers themselves, as the trace gave them before 18.4: an attempt's `via` lists its step files in
    # the order its description names them, not the order the chain meets them; the belongs_to claims follow.
    def via(node: str) -> list[tuple[str, list[str]]]:
        return [(a["id"], [f"{v['via']}:{v['through']}" for v in a["via"]]) for a in full[node]["attempts"]]

    build, plot0 = "step_files:script:steps/16-build.py", "step_files:script:steps/17-plot0.py"
    assert via("figure:figs/p0f0.png") == [("attempt:map.md#16", [build, plot0]), ("attempt:map.md#19", [build])]
    assert via("figure:figs/p2f0.png") == [("attempt:map.md#16", [build]), ("attempt:map.md#18", ["belongs_to:figure:figs/p2f0.png"]),
                                           ("attempt:map.md#19", [build])]
    assert [(v["variable"], v["matched_by"]) for v in full["figure:figs/p0f0.png"]["variables"]] == [("panelx", "output"), ("ycol", "field")]
    assert [(v["variable"], v["matched_by"]) for v in full["script:steps/16-build.py"]["variables"]] == [("panelx", "field"), ("ycol", "field")]
    # The fixture is rich enough to mean something.
    traced = [json.loads(alone[n]) for n in nodes]
    assert any(t["variables"] for t in traced) and any(t["attempts"] for t in traced)
    assert any(len(a["via"]) > 1 for t in traced for a in t["attempts"])
    assert any(l["judgement"] for t in traced for l in t["links"]) and any(l["human"] for t in traced for l in t["links"])
    assert {b["kind"] for t in traced for b in t["breaks"]} >= {"no_generating_script", "no_attempt"}


def _legacy_attempts(conn, root, chain_nodes, links):
    """`trace._attempts` as it was before 18.4, word for word: the configuration read twice, every attempt
    walked for its step files, the verdict words read apart."""
    from researchos.records import notes as notes_mod

    config = trace._attempts_config(root)
    steps_dir = config.steps_dir if config is not None else None
    labels = attempts_ingest.verdict_labels(root)
    paths_in_chain = {notes_mod.node_path(n): n for n in chain_nodes if notes_mod.node_path(n)}
    found: dict = {}

    def claim(attempt, via, through):
        item = found.setdefault(attempt["id"], {
            "id": attempt["id"], "number": attempt["attrs"].get("number"), "title": attempt.get("title"),
            "verdict": (attempt.get("human_fields") or {}).get("verdict", ""),
            "verdict_status": attempts_ingest.verdict_status((attempt.get("human_fields") or {}).get("verdict", ""), labels),
            "result": (attempt.get("human_fields") or {}).get("result", ""), "via": [],
        })
        item["via"].append({"via": via, "through": through})

    attempts = db.get_nodes_by_type(conn, "attempt")
    if steps_dir:
        for attempt in attempts:
            for name in attempt["attrs"].get("step_files") or []:
                path = f"{steps_dir.rstrip('/')}/{name}"
                if path in paths_in_chain:
                    claim(attempt, "step_files", paths_in_chain[path])
    by_id = {a["id"]: a for a in attempts}
    flags = judgements.link_flags(conn)
    for node in chain_nodes:
        for edge in [e for e in db.query_edges(conn, src=node, type="belongs_to") if e["status"] != "rejected"]:
            attempt = by_id.get(edge["dst"])
            if attempt is None:
                continue
            links[judgements.key_of(edge)] = trace._link(edge, flags)
            claim(attempt, "belongs_to", node)
    return sorted(found.values(), key=lambda a: a["id"])


@pytest.mark.parametrize("broken", [False, True])
def test_the_attempts_of_every_chain_are_what_the_walk_over_all_attempts_gave(tmp_path, monkeypatch, broken):
    """The step-file index and the one read of the configuration against the old walk, on the chain nodes
    in the order the chain really met them: the attempts, each one's `via`, and the links the claims add --
    for every node of the project, with a good configuration and with one that does not parse."""
    root = _traced_project(tmp_path / "proj")
    if broken:
        (root / ".researchos" / "attempts.toml").write_text("not = = toml\n")
    real, seen = trace._attempts, []

    def spy(shared, chain_nodes, links):
        before = dict(links)
        expected = _legacy_attempts(shared.conn, shared.root, chain_nodes, before)
        got = real(shared, chain_nodes, links)
        assert _dump(got) == _dump(expected)
        assert _dump(list(links.items())) == _dump(list(before.items()))
        seen.append(got)
        return got

    monkeypatch.setattr(trace, "_attempts", spy)
    conn = _conn(root)
    try:
        shared = trace.Shared(conn, root)
        for node in _every_node(conn):
            trace.chain(conn, root, node, shared=shared)
            trace.chain(conn, root, node)
    finally:
        conn.close()
    vias = [v["via"] for found in seen for a in found for v in a["via"]]
    if broken:  # no steps directory: only what was attached by hand
        assert vias and set(vias) == {"belongs_to"}
    else:
        assert any(len(found) > 1 for found in seen) and any(len(a["via"]) > 1 for found in seen for a in found)
        assert set(vias) == {"step_files", "belongs_to"}


def _alone(monkeypatch) -> None:
    """The way before 18.4: every trace on its own, reading everything it needs, and the variable
    cards too. `nodepage` reaches `chain` through the module, so this puts it back for the page."""
    original = trace.chain

    def alone(conn, root, node, *, shared=None, variables=True):
        return original(conn, root, node)

    monkeypatch.setattr(trace, "chain", alone)


def _payloads(root: Path) -> tuple[str, str]:
    conn = _conn(root)
    try:
        return _dump(nodepage.results(conn, root)), _dump(nodepage.results(conn, root, "paper.md"))
    finally:
        conn.close()


def test_the_results_payload_is_byte_identical_to_tracing_each_figure_on_its_own(tmp_path, monkeypatch):
    """The page of figures and the page of one draft's claims, with one shared context for the
    request and with every figure traced alone as before: the same bytes, in the same order."""
    root = _traced_project(tmp_path / "proj")
    shared_way = _payloads(root)
    _alone(monkeypatch)
    assert _payloads(root) == shared_way
    page, claims = (json.loads(text) for text in shared_way)
    states = {i["trace"]["label"] for g in page["sections"] for i in g["items"]} | {i["trace"]["label"] for i in page["loose"]}
    assert states == {"完整", "缺来源"}
    assert [c["trace"]["label"] for g in claims["sections"] for c in g["claims"]] == ["完整", "缺尝试", "缺来源", "缺尝试"]


def test_a_figure_two_sections_include_is_traced_once_and_each_item_has_a_state_of_its_own(tmp_path, monkeypatch):
    root = _traced_project(tmp_path / "proj")
    original, traced = trace.chain, []

    def spy(conn, root_, node, **kwargs):
        traced.append(node)
        return original(conn, root_, node, **kwargs)

    monkeypatch.setattr(trace, "chain", spy)
    conn = _conn(root)
    try:
        page = nodepage.results(conn, root)
    finally:
        conn.close()
    items = [i for g in page["sections"] for i in g["items"]] + page["loose"]
    ids = [i["id"] for i in items]
    assert len(ids) == len(set(ids)) + 1 and len(traced) == len(set(ids))  # p0f0 is in two sections, traced once
    assert sorted(traced) == sorted(set(ids))
    twice = [i for i in items if i["id"] == "figure:figs/p0f0.png"]
    assert len(twice) == 2 and twice[0]["trace"] == twice[1]["trace"]
    assert twice[0]["trace"] is not twice[1]["trace"] and twice[0]["trace"]["breaks"] is not twice[1]["trace"]["breaks"]
    twice[0]["trace"]["breaks"].append("written in place, as officialness.annotate does")
    twice[0]["trace"]["officialness"] = {"state": "official"}
    assert twice[1]["trace"]["breaks"] == [] and "officialness" not in twice[1]["trace"]


def test_a_request_reads_the_flags_the_configuration_the_attempts_and_the_cards_once_not_once_per_figure(tmp_path, monkeypatch):
    root = _traced_project(tmp_path / "proj")
    reads = {"flags": 0, "config": 0, "attempts": 0, "cards": 0}

    def counted(module, name, key, test=lambda *a: True):
        original = getattr(module, name)

        def wrapper(*args, **kwargs):
            if test(*args):
                reads[key] += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(module, name, wrapper)

    counted(judgements, "link_flags", "flags")
    counted(attempts_ingest, "load_config", "config")
    counted(db, "get_nodes_by_type", "attempts", lambda conn, kind: kind == "attempt")
    counted(variables_mod, "read_cards", "cards")
    conn = _conn(root)
    try:
        page = nodepage.results(conn, root)
        claims = nodepage.results(conn, root, "paper.md")
        figures = sum(len(g["items"]) for g in page["sections"]) + len(page["loose"])
        assert figures >= 10 and claims["shown"] == 4
        # Each call of the page: one read of each, whatever it traces; the cards not at all (the
        # states read the breaks and the attempts only).
        assert reads == {"flags": 2, "config": 2, "attempts": 2, "cards": 0}
        nodes = _every_node(conn)  # (reads the attempts for itself)
        reads.update(flags=0, config=0, attempts=0, cards=0)
        shared = trace.Shared(conn, root)
        for node in nodes:
            trace.chain(conn, root, node, shared=shared)
        assert reads == {"flags": 1, "config": 1, "attempts": 1, "cards": 1}  # with the cards: still once
        reads.update(flags=0, config=0, attempts=0, cards=0)
        trace.chain(conn, root, "figure:figs/p0f0.png")
        assert reads == {"flags": 1, "config": 1, "attempts": 1, "cards": 1}  # a lone trace reads what it did
    finally:
        conn.close()


@pytest.mark.parametrize("config", [
    "this is = = not toml\n",                          # not TOML at all
    'file = "map.md"\nheading = "Attempt timeline"\n',  # no [columns]
])
def test_an_attempts_file_that_does_not_parse_reads_as_none_and_the_page_is_the_same_as_before(tmp_path, monkeypatch, config):
    """Neither the steps nor the verdict words can be had: no attempt claims a file by its step numbers,
    the words are the defaults -- in the shared context and alone alike."""
    root = _traced_project(tmp_path / "proj")
    good = _payloads(root)
    (root / ".researchos" / "attempts.toml").write_text(config)
    broken = _payloads(root)
    _alone(monkeypatch)
    assert _payloads(root) == broken
    assert broken != good
    def states(text: str) -> dict[str, str]:
        page = json.loads(text)
        return {i["path"]: i["trace"]["label"] for g in page["sections"] for i in g["items"]}

    # p0f0 belonged to attempt 16 by its step numbers; without the steps directory nothing says so. p2f0 was
    # attached to attempt 18 by hand and still is.
    assert states(good[0])["figs/p0f0.png"] == "完整" and states(broken[0])["figs/p0f0.png"] == "缺尝试"
    assert states(broken[0])["figs/p2f0.png"] == "完整" and states(broken[0])["figs/orphan.png"] == "缺来源"
    conn = _conn(root)
    try:
        shared = trace.Shared(conn, root)
        assert shared.config is None and shared.labels == attempts_ingest.verdict_labels(root)
        assert shared.labels == dict(attempts_ingest.DEFAULT_VERDICT_LABELS)
    finally:
        conn.close()


def test_the_verdict_words_come_from_the_same_file_as_the_steps_directory(tmp_path):
    """One read gives both: the steps directory the step numbers resolve under, and the project's own
    words for a marker (`attempts.verdict_labels` says the same)."""
    root = _traced_project(tmp_path / "proj")
    (root / ".researchos" / "attempts.toml").write_text(ATTEMPTS_TOML + '[verdict_labels]\n"✅" = "采用"\n"🧪" = "试验"\n')
    conn = _conn(root)
    try:
        shared = trace.Shared(conn, root)
        assert shared.config.steps_dir == "steps"
        assert shared.labels == attempts_ingest.verdict_labels(root) and shared.labels["✅"] == "采用"
        alone = trace.chain(conn, root, "figure:figs/p0f0.png")
        with_shared = trace.chain(conn, root, "figure:figs/p0f0.png", shared=shared)
        assert _dump(alone) == _dump(with_shared)
        assert {a["verdict_status"]["label"] for a in alone["attempts"]} == {"采用"}  # #16 and #19 are ✅
        waiting = trace.chain(conn, root, "figure:figs/p1f0.png", shared=shared)["attempts"]
        assert [(a["id"], a["verdict_status"]["label"]) for a in waiting] == [("attempt:map.md#17", "待定")]
    finally:
        conn.close()


def test_an_attempts_file_that_cannot_be_read_fails_the_first_trace_as_it_always_did(tmp_path, monkeypatch):
    """Bytes that are not text: the trace that needs the configuration raises what it raised, the
    same exception from the shared context and alone, and nothing is kept of the failed read (the
    next trace meets the failure again). A trace that never gets as far as the attempts -- a node
    that is nowhere -- does not touch the file, and building the context reads nothing."""
    root = _traced_project(tmp_path / "proj")
    (root / ".researchos" / "attempts.toml").write_bytes(b"file = \xff\xfe\x00 \x80")
    conn = _conn(root)
    try:
        with pytest.raises(UnicodeDecodeError) as new_way:
            nodepage.results(conn, root)
        with pytest.raises(UnicodeDecodeError) as claims_way:
            nodepage.results(conn, root, "paper.md")
        _alone(monkeypatch)
        with pytest.raises(UnicodeDecodeError) as old_way:
            nodepage.results(conn, root)
        assert str(new_way.value) == str(old_way.value) == str(claims_way.value)
        monkeypatch.undo()
        shared = trace.Shared(conn, root)  # reads nothing: cannot fail here
        assert trace.chain(conn, root, "figure:figs/never-existed.png", shared=shared)["found"] is False
        for _ in range(2):
            with pytest.raises(UnicodeDecodeError):
                trace.chain(conn, root, "figure:figs/p0f0.png", shared=shared)
    finally:
        conn.close()


def test_a_shared_context_of_another_connection_or_project_is_refused(tmp_path):
    root, other = _traced_project(tmp_path / "proj"), tmp_path / "other"
    other.mkdir()
    conn, second = _conn(root), _conn(root)
    try:
        with pytest.raises(ValueError):
            trace.chain(conn, root, "figure:figs/p0f0.png", shared=trace.Shared(second, root))
        with pytest.raises(ValueError):
            trace.chain(conn, root, "figure:figs/p0f0.png", shared=trace.Shared(conn, other))
    finally:
        conn.close()
        second.close()


def test_without_variables_the_cards_are_not_read_and_the_rest_is_the_same(tmp_path, monkeypatch):
    root = _traced_project(tmp_path / "proj")
    cards_read = []
    original = variables_mod.read_cards
    monkeypatch.setattr(variables_mod, "read_cards", lambda r: cards_read.append(r) or original(r))
    conn = _conn(root)
    try:
        bare = trace.chain(conn, root, "figure:figs/p0f0.png", variables=False)
        assert cards_read == [] and bare["variables"] == []
        full = trace.chain(conn, root, "figure:figs/p0f0.png")
        assert len(cards_read) == 1 and [v["variable"] for v in full["variables"]] == ["panelx", "ycol"]
        assert bare == {**full, "variables": []}
    finally:
        conn.close()


def test_18_5_7_the_results_page_of_400_figures_and_10_variable_cards_answers_in_under_half_a_second(tmp_path):
    import time

    root = _traced_project(tmp_path / "proj", plots=40, per_plot=10, with_cards=True)
    for number in range(8):
        _write_card(root, f"more{number}", 'script = "steps/16-build.py"\nfield = "x"')
    _scan(root)
    conn = _conn(root)
    try:
        best = 9.0
        for _ in range(3):
            began = time.perf_counter()
            page = nodepage.results(conn, root)
            best = min(best, time.perf_counter() - began)
        figures = sum(len(g["items"]) for g in page["sections"]) + len(page["loose"])
    finally:
        conn.close()
    assert figures >= 400 and len(variables_mod.read_cards(root)) == 10
    assert best < 0.5, f"{best:.2f} s"


# -- 设为论文正文 keeps the generation its POST returns (18.4 item 1) ------------------------------------

_POLL_SCRIPT = r"""
global.window = { scrollTo() {}, scrollY: 0 };
%(dom)s
global.mk = (tag, cls, text) => { const e = el(tag, cls, text); Object.defineProperty(e, "childElementCount", { get: () => e.kids.filter((k) => typeof k !== "string").length });
  e.contains = (child) => e.all().includes(child);
  e.insertBefore = (n, before) => { n.parentNode = e; e.kids.splice(before ? e.kids.indexOf(before) : e.kids.length, 0, n); return n; };
  return e; };
global.RODesk = { ensureContext: async () => ({}), context: () => ({ mode: "single" }), refreshSidebar() {} };
global.clearProjectState = () => {};
global.renderBlockingError = (node, framing) => { node.textContent = framing; };
global.openFilePanel = () => {};
global.activateView = () => {};
global.SECTION_OF_VIEW = { results: "results", node: "results" };
global.SECTION_TITLES = { results: "图表与论断" };
// app.html's own: the counter the generation poll compares, the view on show, and the poll and the refresh it runs.
let lastGeneration = 7;
let currentView = %(view)s;
let refreshInFlight = false;
global.markViewsStale = () => { Object.values(views).forEach((v) => { delete v.dataset.loaded; }); };
global.loadProjects = async () => true;
global.loadProjectSummary = () => {};
global.revalidatePanel = async () => {};
global.updateScanChip = () => {};
global.updateChipFromStatus = () => {};
global.showRefreshingChip = () => {};
%(app)s
%(node)s
global.RONode = window.RONode;
const DRAFT = %(draft)s;
let serverGeneration = 7;
global.calls = [];
global.apiGet = async (path) => {
  calls.push("GET " + path);
  if (path === "/api/generation") return { generation: serverGeneration, refreshing: false };
  if (path === "/api/results") return { sections: [], loose: [], claims: [], states: {},
    drafts: [{ file: "paper.md", marked: DRAFT.marked, claims: 2, node: "file:paper.md" }],
    papers: { marked: DRAFT.marked ? ["paper.md"] : [], trusted: true, message: null, path: ".researchos/papers.toml" } };
  if (path.startsWith("/api/node?")) return { id: "file:paper.md", type: "file", type_label: "文件", name: "paper.md", path: "paper.md",
    exists: true, in_index: true, details: {}, links: [], paper: { draft: "paper.md", marked: DRAFT.marked },
    trace: { columns: [], lines: [], breaks: [], attempts: [], variables: [] } };
  if (path.startsWith("/api/history?")) return { versions: [] };
  if (path.startsWith("/api/notes?")) return { notes: [] };
  if (path === "/api/me") return { me: { id: "li@example.com" } };
  throw new Error("no fixture for " + path);
};
global.apiPost = async (path, body) => {
  calls.push("POST " + path + " " + JSON.stringify(body));
  if (%(refused)s) throw Object.assign(new Error("refused"), { state: "untrusted" });
  serverGeneration += 1; // the write moved the project's generation
  DRAFT.marked = body.marked;
  return { ok: true, generation: serverGeneration };
};
(async () => {
  views.results = mk("div"); views.node = mk("div");
  if (currentView === "node") { window.RONode.openNode("file:paper.md"); await window.RONode.loadNode(views.node, false); }
  else await window.RONode.loadResults(views.results, false);
  const host = currentView === "node" ? views.node : views.results;
  const start = calls.length;
  const press = (label) => host.all().find((k) => k.tag === "BUTTON" && k.kids.join("") === label);
  const button = press(%(label)s);
  button.click();
  await settle();
  const written = calls.slice(start);
  const tick = calls.length;
  await pollGeneration(); // the page's two-second tick, after the write
  await settle();
  process.stdout.write(JSON.stringify({ written, after: calls.slice(tick), lastGeneration, loaded: host.dataset.loaded || null,
    stale: Object.entries(views).filter(([k, v]) => !v.dataset.loaded).map(([k]) => k), reopened: !!press(%(flipped)s) }));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def _mark_run(view: str, *, marked: bool = False, refused: bool = False) -> dict:
    from test_v10_desk_panel import _PANEL_DOM

    script = _POLL_SCRIPT % {
        "dom": _PANEL_DOM + 'global.settle = () => new Promise((r) => setTimeout(r, 0));',
        "view": json.dumps(view),
        # The stand-in DOM sets a pollGeneration of its own; the page's is put over it.
        "app": _app_function("async function refreshCurrentView()") + _app_function("async function pollGeneration()").replace(
            "async function pollGeneration()", "global.pollGeneration = async function pollGeneration()", 1) + ";",
        "node": (WEBAPP / "node.js").read_text(encoding="utf-8"),
        "draft": json.dumps({"marked": marked}),
        "refused": "true" if refused else "false",
        "label": json.dumps("不再作为论文正文" if marked else "设为论文正文"),
        "flipped": json.dumps("设为论文正文" if marked else "不再作为论文正文"),
    }
    return _node(script)


@needs_node
@pytest.mark.parametrize("view", ["results", "node"])
def test_marking_a_draft_reads_the_page_once_and_the_poll_finds_nothing_new(view):
    """设为论文正文 on 图表与论断 and on the draft's own page: the POST answers
    with the generation it moved to; the page keeps it and reads its views
    again at once (the other views marked for their next showing, as the
    poll would have), so the poll that follows finds the generation it
    already has and reads nothing -- the page was read twice before: here,
    and again when the poll saw the move."""
    page = "/api/results" if view == "results" else "/api/node?node=file%3Apaper.md"
    out = _mark_run(view)
    assert out["written"][0] == 'POST /api/papers {"draft":"paper.md","marked":true}'
    assert out["written"][1:] == [f"GET {page}"] + ([] if view == "results" else [
        "GET /api/history?node=file%3Apaper.md", "GET /api/notes?node=file%3Apaper.md"])
    assert out["after"] == ["GET /api/generation"]  # no second read of the page
    assert out["lastGeneration"] == 8
    assert out["loaded"] == "1" and out["reopened"] is True  # drawn again, now offering 不再作为论文正文
    assert set(out["stale"]) == ({"node"} if view == "results" else {"results"})  # the view not shown reloads on its next showing


@needs_node
def test_unmarking_a_draft_keeps_the_generation_the_same_way():
    out = _mark_run("results", marked=True)
    assert out["written"][0] == 'POST /api/papers {"draft":"paper.md","marked":false}'
    assert out["after"] == ["GET /api/generation"] and out["lastGeneration"] == 8


@needs_node
def test_a_refused_mark_leaves_the_generation_and_the_page_alone():
    """The server refused (the record file is damaged): nothing was written,
    so the generation the page holds stays, nothing is read again, and the
    button is free to press once more."""
    out = _mark_run("results", refused=True)
    assert out["written"] == ['POST /api/papers {"draft":"paper.md","marked":true}']
    assert out["lastGeneration"] == 7 and out["after"] == ["GET /api/generation"]
