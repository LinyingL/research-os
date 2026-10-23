"""Research OS V14, task G4-5 (DESIGN.md 18.4 item 7): the smaller speed items.
Every one leaves its payload exactly as it was, so each test here keeps a copy of
the old way (or runs the old path beside the new one) and compares what comes
back; the counts that say work was saved (a spy on the reads, on the programs
started) sit beside those comparisons. Every file, node and program here is
made for the test."""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import sys
import threading
import time
from pathlib import Path

import pytest

from researchos import cloud, db, memo, people
from researchos import project as project_identity
from researchos.agents import profiles
from researchos.ingest import dataflow, stata
from researchos.webapp import node_api, server

from test_v11_stata_folders import PI, _package
from test_v11_stata_page import _call, _serve
from test_v9_language import APP_HTML, FILL_VARS_LINE, I18N_JS, T_LINE, WEBAPP, _app_function, _node, needs_node
from test_workspace_js import run as page_run


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


# -- /api/summary counts in SQL ---------------------------------------------------------------------------


def _old_counts(conn) -> tuple[dict, dict]:
    """/api/summary's counts as they were read before 18.4 item 7: every node of a type and every edge, decoded."""
    nodes = {t: len(db.get_nodes_by_type(conn, t)) for t in sorted(db.NODE_TYPES)}
    edges = {t: 0 for t in sorted(db.EDGE_TYPES)}
    for edge in db.query_edges(conn):
        edges[edge["type"]] += 1
    return nodes, edges


def _every_kind_of_graph(conn) -> None:
    """One node of every type, a second figure and two scripts, and edges of every type in every status."""
    for t in sorted(db.NODE_TYPES):
        db.upsert_node(conn, f"{t}:one", t, title=t)
    for extra in ("figure:two", "script:two", "script:three"):
        db.upsert_node(conn, extra, extra.split(":")[0])
    statuses = ["auto", "pending"]
    for k, t in enumerate(sorted(db.EDGE_TYPES)):
        db.upsert_edge(conn, "script:one", "figure:one", t, extractor=f"test{k}", evidence={"note": t},
                       confidence=1.0, status=statuses[k % 2])
    db.upsert_edge(conn, "script:two", "figure:two", "writes", extractor="other", evidence={"note": "w"},
                   confidence=0.5, status="pending")
    db.upsert_edge(conn, "script:three", "figure:two", "writes", extractor="third", evidence={"note": "w3"},
                   confidence=0.5, status="auto")


def test_the_summary_counts_come_from_sql_and_are_the_old_counts(conn, tmp_path):
    """An empty index, then a graph with a node of every type and an edge of every type in two statuses, then
    a confirmed edge and a rejected one: the whole /api/summary payload is the one the old counting gave, key
    for key, with a zero for every type that has none."""
    def old_payload() -> dict:
        nodes, edges = _old_counts(conn)
        return dict(server.summary_payload(conn, tmp_path), nodes=nodes, edges=edges)

    empty = server.summary_payload(conn, tmp_path)
    assert _dumps(empty) == _dumps(old_payload())
    assert set(empty["nodes"]) == set(db.NODE_TYPES) and set(empty["edges"]) == set(db.EDGE_TYPES)
    assert not any(empty["nodes"].values()) and not any(empty["edges"].values())

    _every_kind_of_graph(conn)
    full = server.summary_payload(conn, tmp_path)
    assert _dumps(full) == _dumps(old_payload())
    assert all(full["nodes"][t] >= 1 for t in db.NODE_TYPES) and all(full["edges"][t] >= 1 for t in db.EDGE_TYPES)
    assert full["nodes"]["script"] == 3 and full["edges"]["writes"] == 3 and full["pending"] >= 1

    db.set_edge_status(conn, "script:two", "figure:two", "writes", "other", "confirmed")
    db.set_edge_status(conn, "script:three", "figure:two", "writes", "third", "rejected")
    assert _dumps(server.summary_payload(conn, tmp_path)) == _dumps(old_payload())
    assert server.summary_payload(conn, tmp_path)["edges"]["writes"] == 3  # a verdict is still an edge


def test_the_summary_loads_no_node_and_no_edge_to_count_them(conn, tmp_path, monkeypatch):
    """The counts are two grouped selects: the reader of every node of a type is not called, the reader of
    every edge is not called (the pending edges, which `pending` still reads, are the one filtered call),
    and no statement selects the rows of `nodes` whole or all of `edges`."""
    _every_kind_of_graph(conn)
    seen: list[str] = []
    conn.set_trace_callback(seen.append)
    read_edges = db.query_edges

    def refuse_nodes(*args, **kwargs):
        raise AssertionError("the summary loaded nodes to count them")

    def only_pending(conn_, src=None, dst=None, type=None, status=None):  # noqa: A002
        assert (src, dst, type, status) == (None, None, None, "pending"), "the summary loaded edges to count them"
        return read_edges(conn_, src, dst, type, status)

    monkeypatch.setattr(db, "get_nodes_by_type", refuse_nodes)
    monkeypatch.setattr(db, "query_edges", only_pending)
    payload = server.summary_payload(conn, tmp_path)
    conn.set_trace_callback(None)
    assert payload["nodes"]["figure"] == 2 and payload["pending"] >= 1
    counted = [s for s in seen if "COUNT(*)" in s]
    assert len(counted) == 2 and any("FROM nodes GROUP BY type" in s for s in counted) \
        and any("FROM edges GROUP BY type" in s for s in counted)
    assert not [s for s in seen if "SELECT * FROM nodes" in s]
    assert [s for s in seen if "SELECT * FROM edges" in s] == ["SELECT * FROM edges WHERE status = 'pending'"]


# -- GET /api/agents remembers a program's version ------------------------------------------------------------

HOUR_NS = 3600 * 1_000_000_000


def _program(path: Path, version: str = "1.2.3 (Test)", *, age: int = HOUR_NS) -> Path:
    """A tiny program that prints a version, last modified `age` ns ago (the memory trusts a stamp only when
    the file is older than `memo.RACY_NS`)."""
    path.write_text(f"#!{sys.executable}\nprint({version!r})\n", encoding="utf-8")
    path.chmod(0o755)
    _age(path, age)
    return path


def _age(path: Path, age: int) -> None:
    then = time.time_ns() - age
    os.utime(path, ns=(then, then))


@pytest.fixture
def programs(tmp_path, monkeypatch):
    """All runners saved at programs of the test's own, the search path empty (no real agent program
    is looked for), the memory empty, and a count of how often a program was started for its version."""
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(profiles, "EXTRA_PATH_DIRS", ())
    profiles._VERSIONS.clear()
    started: list[str] = []
    run = profiles._run

    def counting(argv, *, env, timeout):
        started.append(argv[0])
        return run(argv, env=env, timeout=timeout)

    monkeypatch.setattr(profiles, "_run", counting)
    claude = _program(tmp_path / "claude-fake", "2.0.1 (Claude Code)")
    opencode = _program(tmp_path / "opencode-fake", "0.9.0")
    codex = _program(tmp_path / "codex-fake", "codex-cli 0.162.0")
    profiles.set_commands({"claude-code": str(claude), "opencode": str(opencode), "codex": str(codex)})
    started.clear()
    yield type("Programs", (), {"claude": claude, "opencode": opencode, "codex": codex, "started": started})
    profiles._VERSIONS.clear()


def test_the_agents_payload_is_the_old_payload_and_the_programs_are_asked_once(programs, monkeypatch):
    """The page asks three times: the payload is the one the old path gives (every program asked every time)
    each time, and the programs were started for their version once each, not once per request."""
    first = server.agents_payload()
    assert programs.started == [str(programs.claude), str(programs.opencode), str(programs.codex)]
    second, third = server.agents_payload(), server.agents_payload()
    assert programs.started == [str(programs.claude), str(programs.opencode), str(programs.codex)]  # nothing started again
    assert _dumps(first) == _dumps(second) == _dumps(third)
    assert [r["version"] for r in first["runners"]] == ["2.0.1 (Claude Code)", "0.9.0", "codex-cli 0.162.0"]
    # the old path: `find` asks for the version every time
    with monkeypatch.context() as old:
        old.setattr(profiles, "_remembered_version", profiles._version_of)
        before = len(programs.started)
        assert _dumps(server.agents_payload()) == _dumps(first)
        assert len(programs.started) == before + 3


def test_a_program_that_changed_is_asked_again(programs):
    """The same path with other content (a new size, a new modification time) is asked again and gives the new
    version; so is a program replaced by another file of the same size and the same modification time (it has
    another inode); and a program that was touched only (same content, new time) is asked again too."""
    assert profiles.find("claude-code").version == "2.0.1 (Claude Code)"
    assert profiles.find("claude-code").version == "2.0.1 (Claude Code)"
    assert programs.started == [str(programs.claude)]
    _program(programs.claude, "3.1.0 (Claude Code, newer)", age=HOUR_NS // 2)
    assert profiles.find("claude-code").version == "3.1.0 (Claude Code, newer)"
    assert programs.started == [str(programs.claude)] * 2
    # the same size and the same modification time, another file
    stat_before = programs.claude.stat()
    swap = programs.claude.with_name("swap")
    _program(swap, "3.1.0 (Claude Code, other)", age=0)
    assert swap.stat().st_size == stat_before.st_size
    os.utime(swap, ns=(stat_before.st_atime_ns, stat_before.st_mtime_ns))
    os.replace(swap, programs.claude)
    assert programs.claude.stat().st_mtime_ns == stat_before.st_mtime_ns and programs.claude.stat().st_ino != stat_before.st_ino
    assert profiles.find("claude-code").version == "3.1.0 (Claude Code, other)"
    assert len(programs.started) == 3
    # touched: the content is the same, the stamp is not
    _age(programs.claude, HOUR_NS * 2)
    assert profiles.find("claude-code").version == "3.1.0 (Claude Code, other)"
    assert len(programs.started) == 4
    profiles.find("claude-code")
    assert len(programs.started) == 4


def test_changing_the_saved_location_asks_again_and_so_does_saving_it(programs, tmp_path):
    """agent_commands: a runner pointed at another program is asked; pointed back, it is asked once more, since
    every save of the locations clears the memory (the person who saves a path is asking about it); the other
    runner's answer is asked again with it, and a location nobody saved is left to the search path."""
    assert profiles.find("opencode").version == "0.9.0"
    assert programs.started == [str(programs.opencode)]
    other = _program(tmp_path / "opencode-other", "0.10.2")
    profiles.set_commands({"opencode": str(other)})
    assert profiles.find("opencode").version == "0.10.2"
    assert profiles.find("opencode").version == "0.10.2"
    assert programs.started == [str(programs.opencode), str(other)]
    profiles.set_commands({"opencode": str(programs.opencode)})
    assert profiles.find("opencode").version == "0.9.0"
    assert programs.started[-1] == str(programs.opencode) and len(programs.started) == 3
    profiles.set_commands({"opencode": str(programs.opencode)})  # saved again, unchanged
    profiles.find("opencode")
    assert len(programs.started) == 4
    profiles.set_commands({"opencode": ""})  # removed: the search path finds nothing
    assert profiles.find("opencode").problem == "missing" and len(programs.started) == 4


def test_a_program_that_gave_no_answer_is_asked_every_time(programs, tmp_path):
    """No version (it failed, said nothing, or took too long) is not an answer and is not kept: the program is
    started again at the next request, and a version it gives later is shown at once."""
    quiet = programs.opencode
    quiet.write_text(f"#!{sys.executable}\npass\n", encoding="utf-8")
    _age(quiet, HOUR_NS)
    for n in (1, 2, 3):
        found = profiles.find("opencode")
        assert (found.version, found.problem, found.found) == (None, "no_version", True)
        assert programs.started.count(str(quiet)) == n
    failing = _program(tmp_path / "fails", "never")
    failing.write_text(f"#!{sys.executable}\nimport sys\nsys.exit(3)\n", encoding="utf-8")
    _age(failing, HOUR_NS)
    profiles.set_commands({"opencode": str(failing)})
    assert profiles.find("opencode").problem == "no_version" and profiles.find("opencode").problem == "no_version"
    assert programs.started.count(str(failing)) == 2
    healed = _program(failing, "0.11.0")
    assert profiles.find("opencode").version == "0.11.0" and healed == failing


def test_a_program_that_timed_out_is_not_remembered(programs, monkeypatch):
    monkeypatch.setattr(profiles, "VERSION_TIMEOUT_S", 0.4)
    slow = programs.opencode
    slow.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(30)\n", encoding="utf-8")
    _age(slow, HOUR_NS)
    assert profiles.find("opencode").problem == "no_version"
    assert profiles.find("opencode").problem == "no_version"
    assert programs.started.count(str(slow)) == 2 and len(profiles._VERSIONS) == 0


def test_a_program_written_a_moment_ago_is_not_remembered_until_it_has_settled(programs):
    """A stamp of a file written within `memo.RACY_NS` could be the stamp of a file written again; such a
    program is asked every time. The same file, older, is kept from the next request on."""
    _program(programs.claude, "2.0.2 (Claude Code)", age=0)
    for n in (1, 2, 3):
        assert profiles.find("claude-code").version == "2.0.2 (Claude Code)"
        assert programs.started.count(str(programs.claude)) == n
    _age(programs.claude, memo.RACY_NS + 1_000_000_000)
    assert profiles.find("claude-code").version == "2.0.2 (Claude Code)"
    assert profiles.find("claude-code").version == "2.0.2 (Claude Code)"
    assert programs.started.count(str(programs.claude)) == 4


def test_a_program_replaced_while_it_was_asked_is_not_remembered(programs, monkeypatch):
    """The stamp is looked at before and after `--version` runs: a file that changed in between is not kept
    under the stamp it had before."""
    ask = profiles._version_of

    def replaced_meanwhile(path):
        version = ask(path)
        _program(Path(path), "9.9.9 (replaced)", age=HOUR_NS // 3)
        return version

    monkeypatch.setattr(profiles, "_version_of", replaced_meanwhile)
    assert profiles.find("claude-code").version == "2.0.1 (Claude Code)"
    assert len(profiles._VERSIONS) == 0
    monkeypatch.setattr(profiles, "_version_of", ask)
    assert profiles.find("claude-code").version == "9.9.9 (replaced)"
    assert profiles.find("claude-code").version == "9.9.9 (replaced)"
    assert len(profiles._VERSIONS) == 1


def test_the_path_a_program_is_started_with_is_part_of_what_was_asked(programs, monkeypatch, tmp_path):
    """A program may find its interpreter on the PATH it is given, so a PATH that changed asks again."""
    profiles.find("claude-code")
    profiles.find("claude-code")
    assert len(programs.started) == 1
    monkeypatch.setenv("PATH", str(tmp_path / "elsewhere"))
    profiles.find("claude-code")
    assert len(programs.started) == 2


def test_nothing_is_started_for_a_program_that_is_not_to_be_asked(programs):
    """`check_version=False` neither starts the program nor touches the memory; a missing program and one
    that is not executable never reach it."""
    assert profiles.find("claude-code", check_version=False).version is None
    assert programs.started == [] and len(profiles._VERSIONS) == 0
    programs.opencode.chmod(0o644)
    assert profiles.find("opencode").problem == "not_executable"
    programs.opencode.unlink()
    assert profiles.find("opencode").problem == "missing"
    assert programs.started == [] and len(profiles._VERSIONS) == 0


def test_the_memory_of_versions_is_bounded(programs, tmp_path):
    """Fifty programs, one after the other: the memory holds at most its 32 entries, the newest kept."""
    last = None
    for n in range(50):
        last = _program(tmp_path / f"p{n}", f"v{n}")
        profiles.set_commands({"opencode": str(last)})
        assert profiles.find("opencode").version == f"v{n}"
    assert len(profiles._VERSIONS) <= 32
    before = len(programs.started)
    assert profiles.find("opencode").version == "v49" and len(programs.started) == before


def test_threads_asking_together_all_get_the_version_and_the_memory_stays_whole(programs):
    """Eight handler threads ask at once, twice over: each gets the version, no more than one start per thread
    is made in the first round, none in the second, and the memory holds one entry per program."""
    answers: list[str | None] = []
    failures: list[BaseException] = []
    gate = threading.Barrier(8)

    def ask() -> None:
        try:
            gate.wait(timeout=10)
            answers.append(profiles.find("claude-code").version)
        except BaseException as exc:  # noqa: BLE001
            failures.append(exc)

    for _round in range(2):
        before = len(programs.started)
        threads = [threading.Thread(target=ask) for _ in range(8)]
        [t.start() for t in threads]
        [t.join(timeout=30) for t in threads]
        started = len(programs.started) - before
        assert 0 <= started <= (8 if _round == 0 else 0)
        gate.reset()
    assert not failures and answers == ["2.0.1 (Claude Code)"] * 16
    assert len(profiles._VERSIONS) == 1


# -- the last module-load t() call is a call-time lookup (13.6) ---------------------------------------------


@needs_node
def test_the_name_shown_for_no_project_follows_the_language_in_force_when_it_is_drawn():
    """The page's own `t` and the function that sets the project button's label, run before the language table
    exists and then after it is loaded (as a table that arrives after a block was defined): drawn before, the
    label is Chinese; drawn after, it is English, with no reload. A name that was looked up once at load would
    stay Chinese. A real project name is shown as it is, and the title follows the same lookup."""
    script = f"""
global.window = {{}};
{FILL_VARS_LINE}
{T_LINE}
{_app_function("function noProjectName")}
{_app_function("function setProjectMenuLabel")}
const projectMenuBtn = {{ textContent: '', title: '' }};
const seen = [];
setProjectMenuLabel('', '');
seen.push([projectMenuBtn.textContent, projectMenuBtn.title]);
window.ROI18N_SAVED = 'en';
{I18N_JS.read_text(encoding='utf-8')}
setProjectMenuLabel('', '');
seen.push([projectMenuBtn.textContent, projectMenuBtn.title]);
setProjectMenuLabel('My study', '/projects/study');
seen.push([projectMenuBtn.textContent, projectMenuBtn.title.split('\\n')[0]]);
process.stdout.write(JSON.stringify({{ seen, none: noProjectName() }}));
"""
    out = _node(script)
    assert out["seen"][0] == ["还没有项目", "添加项目"]
    assert out["seen"][1] == ["No project yet", "Add project"]
    assert out["seen"][2] == ["My study", "/projects/study"] and out["none"] == "No project yet"


def test_no_script_calls_t_while_it_loads():
    """13.6: every module looks the language up at call time. The page's own block and each module are read for
    a statement at the top level (the page's block at column 0, a module's IIFE at two spaces) that makes a
    constant out of a t() call that is not inside a function; none is left."""
    top = re.compile(r"^(?:const|let|var)\s+[\w$]+ = (?!\(|async\b|function\b|[\w$]+\s*=>)[^\n]*\bt\(", re.M)
    inside = re.compile(r"^  (?:const|let|var)\s+[\w$]+ = (?!\(|async\b|function\b|[\w$]+\s*=>)[^\n]*\bt\(", re.M)
    found = [f"app.html: {m.group(0)}" for m in top.finditer(APP_HTML.read_text(encoding="utf-8"))]
    for module in sorted(WEBAPP.glob("*.js")):
        if module.name != "i18n.js":
            found += [f"{module.name}: {m.group(0)}" for m in inside.finditer(module.read_text(encoding="utf-8"))]
    assert found == []
    assert "const NO_PROJECT_NAME" not in APP_HTML.read_text(encoding="utf-8")


# -- 项目首页 takes /api/me and /api/share from the page's shared context ---------------------------------------


DESK_JS = (WEBAPP / "desk.js").read_text(encoding="utf-8")

#: A PI's project with a team: who I am, the shared folder's team, one agent on this Mac.
_TEAM = r"""
listed.mode='pi';
meInfo={state:'present',me:{id:'m-lin',name:'Lin Ying',email:'l@x.org'}};
shareInfo={role:'pi',me:{id:'m-lin'},team:[{id:'m-lin',name:'Lin Ying',role:'pi'},{id:'m-wang',name:'Wang Wei',role:'member'}]};
agentsInfo=agentFixture();
const OPTIONS=()=>optionsOf(field('负责人'));
const READS=()=>({me:gets.filter(g=>g==='/api/me').length,share:gets.filter(g=>g==='/api/share').length,
  proposals:gets.filter(g=>g==='/api/proposals').length});
const context=(mode,more)=>Object.assign({me:clone(meInfo),share:mode==='single'?null:clone(shareInfo),proposals:{role:mode},
  mode:mode,single:mode==='single',project:'p-study',epoch:state.dataEpoch},more||{});
"""

_TEAM_OPTIONS = ["不指定", "Lin Ying（我）", "Wang Wei", "其他人…", "Claude Code · sonnet"]


@needs_node
def test_the_owner_list_is_the_same_from_the_shared_context_and_from_the_pages_own_reads():
    """The same project opened twice, once with no desk module (the page reads /api/me and /api/share itself)
    and once with a desk module whose context is ready: the owner control offers the same people in the same
    order; the second makes neither read, and asks the desk for nothing it would have to wait for."""
    out = page_run(_TEAM + r"""
const open=async()=>{ window.ROWorkspace.reset(); gets.length=0; await window.ROWorkspace.load(root); await sleepTick();
  return {options:OPTIONS(),reads:READS()}; };
const own=await open();
let asked=0;
window.RODesk={ensureContext:async()=>{asked+=1;return context('pi');},context:()=>context('pi')};
const shared=await open();
console.log(JSON.stringify({own,shared,asked}));
""")
    assert out["own"]["options"] == out["shared"]["options"]
    assert [text for text, _ in out["shared"]["options"]] == _TEAM_OPTIONS
    assert out["own"]["reads"] == {"me": 1, "share": 1, "proposals": 0}
    assert out["shared"]["reads"] == {"me": 0, "share": 0, "proposals": 0} and out["asked"] == 0


@needs_node
def test_a_project_of_one_person_takes_who_i_am_from_the_context_and_never_asks_for_the_shared_folder():
    out = page_run(_TEAM + r"""
listed.mode='single';
const open=async()=>{ window.ROWorkspace.reset(); gets.length=0; await window.ROWorkspace.load(root); await sleepTick();
  return {options:OPTIONS(),reads:READS()}; };
const own=await open();
window.RODesk={context:()=>context('single')};
const shared=await open();
console.log(JSON.stringify({own,shared}));
""")
    assert out["own"]["options"] == out["shared"]["options"]
    assert [text for text, _ in out["shared"]["options"]] == ["不指定", "Lin Ying（我）", "其他人…", "Claude Code · sonnet"]
    assert out["shared"]["reads"] == {"me": 0, "share": 0, "proposals": 0} and out["own"]["reads"]["share"] == 0


@needs_node
def test_who_i_am_does_not_wait_for_a_desk_context_that_is_still_loading_on_a_shared_folder_that_hangs():
    """The desk module is still loading its context (its /api/proposals and /api/share read the shared folder,
    which hangs) and the page's own /api/share hangs too: the page is on screen, the owner control already offers
    me -- read by the page itself, as it always was -- and only the team waits for the folder. The page asks
    the desk for nothing it would have to wait for, and the team arrives when the folder answers."""
    out = page_run(_TEAM + r"""
const stuck=deferred(), folder=deferred();
let asked=0;
window.RODesk={ensureContext:()=>{asked+=1;return stuck.promise;},context:()=>({me:null,share:null,proposals:null,mode:null,single:false,project:null,epoch:-1})};
customGet=(path)=>path==='/api/share'?folder.promise.then(()=>clone(shareInfo)):undefined;
await window.ROWorkspace.load(root); await sleepTick(); await sleepTick();
const during={options:OPTIONS(),reads:READS(),task:!!field('任务'),asked};
folder.resolve();
await sleepTick(); await sleepTick();
console.log(JSON.stringify({during,after:{options:OPTIONS(),reads:READS(),asked}}));
""")
    assert out["during"]["task"] and out["during"]["asked"] == 0
    assert [text for text, _ in out["during"]["options"]] == ["不指定", "Lin Ying（我）", "其他人…", "Claude Code · sonnet"]
    assert out["during"]["reads"] == {"me": 1, "share": 1, "proposals": 0}
    assert [text for text, _ in out["after"]["options"]] == _TEAM_OPTIONS
    assert out["after"]["reads"] == {"me": 1, "share": 1, "proposals": 0} and out["after"]["asked"] == 0


@needs_node
@pytest.mark.parametrize("ready, why", [
    ("()=>{throw new Error('no context');}", "the desk module throws at once"),
    ("()=>context('pi',{epoch:7})", "a context of another project's time"),
    ("()=>context('pi',{project:'p-other'})", "a context of another project"),
    ("()=>context('pi',{mode:null})", "a context never filled"),
    ("()=>null", "nothing at all"),
    ("undefined", "a desk module without a way to ask what it holds"),
])
def test_without_a_usable_context_the_page_reads_its_people_itself(ready, why):
    out = page_run(_TEAM + f"""
window.RODesk={{ensureContext:async()=>context('pi'),context:{ready}}};
await window.ROWorkspace.load(root); await sleepTick();
console.log(JSON.stringify({{options:OPTIONS(),reads:READS()}}));
""")
    assert [text for text, _ in out["options"]] == _TEAM_OPTIONS, why
    assert out["reads"] == {"me": 1, "share": 1, "proposals": 0}, why


@needs_node
def test_a_context_that_arrives_while_who_i_am_is_read_gives_the_team_without_a_second_read():
    """Nothing is ready when the page asks, so /api/me is read by the page; by the time it answers the desk
    has its context, and the team is taken from it."""
    out = page_run(_TEAM + r"""
let ready=null;
window.RODesk={context:()=>ready||({mode:null,epoch:-1})};
customGet=(path)=>{ if(path==='/api/me'){ ready=context('pi'); } };
await window.ROWorkspace.load(root); await sleepTick();
console.log(JSON.stringify({options:OPTIONS(),reads:READS()}));
""")
    assert [text for text, _ in out["options"]] == _TEAM_OPTIONS
    assert out["reads"] == {"me": 1, "share": 0, "proposals": 0}


@needs_node
def test_a_context_that_did_not_ask_for_the_shared_folder_leaves_that_one_read_to_the_page():
    """The page says the project is a PI's, the desk's context read it as a project of one (its /api/proposals
    failed, say): who I am comes from the context, the team is read by the page as it always was."""
    out = page_run(_TEAM + r"""
window.RODesk={context:()=>context('single',{share:null,proposals:null})};
await window.ROWorkspace.load(root); await sleepTick();
console.log(JSON.stringify({options:OPTIONS(),reads:READS()}));
""")
    assert [text for text, _ in out["options"]] == _TEAM_OPTIONS
    assert out["reads"] == {"me": 0, "share": 1, "proposals": 0}


@needs_node
def test_a_shared_folder_that_could_not_be_read_leaves_the_list_as_a_failed_read_always_did():
    """The context was made and the /api/share inside it failed (null): the team is empty and I am in, the
    page does not read it again."""
    out = page_run(_TEAM + r"""
window.RODesk={context:()=>context('pi',{share:null})};
await window.ROWorkspace.load(root); await sleepTick();
const shared={options:OPTIONS(),reads:READS()};
window.RODesk=undefined; shareInfo=null;
window.ROWorkspace.reset(); gets.length=0; await window.ROWorkspace.load(root); await sleepTick();
console.log(JSON.stringify({shared,own:{options:OPTIONS(),reads:READS()}}));
""")
    assert out["shared"]["options"] == out["own"]["options"]
    assert [text for text, _ in out["shared"]["options"]] == ["不指定", "Lin Ying（我）", "其他人…", "Claude Code · sonnet"]
    assert out["shared"]["reads"]["share"] == 0 and out["own"]["reads"]["share"] == 1


@needs_node
def test_the_real_desk_and_the_page_read_who_i_am_and_the_shared_folder_once_when_the_context_is_ready():
    """desk.js as it is, beside workspace.js. The sidebar's read (refreshSidebar, as reloadAllViews starts it) has
    finished when the page loads: /api/me, /api/share and /api/proposals were each read once, by the sidebar, and
    the page has its people having read nothing. Another load of the page, a refresh, reads nothing of them. A
    page that is the first to ask (no context yet) reads who I am and the team itself, and does not start the
    context. A sidebar read begun together with the page's load is not waited for: the page has its people
    whichever answers first."""
    out = page_run(DESK_JS + _TEAM + r"""
customGet=(path)=>path==='/api/proposals'?{role:'pi',team:true}:undefined;
gets.length=0; window.ROWorkspace.reset();
await window.RODesk.refreshSidebar().catch(()=>{});   // its drawing needs the shell; its read is what counts
const sidebar=READS();
gets.length=0;
await window.ROWorkspace.load(root); await sleepTick();
const ready={options:OPTIONS(),reads:READS()};
gets.length=0; await window.ROWorkspace.load(root,true); await sleepTick();
const refreshed=READS();
// the page first: the desk's context is of an earlier time, and nobody has started another
gets.length=0; window.ROWorkspace.reset(); state.dataEpoch=1;
await window.ROWorkspace.load(root); await sleepTick(); await sleepTick();
const first={options:OPTIONS(),reads:READS()};
// together: the sidebar's read and the page's load begin at once
gets.length=0; window.ROWorkspace.reset(); state.dataEpoch=2;
const begun=window.RODesk.refreshSidebar().catch(()=>{});
await window.ROWorkspace.load(root); await sleepTick(); await begun; await sleepTick();
const together={options:OPTIONS(),reads:READS()};
console.log(JSON.stringify({sidebar,ready,refreshed,first,together}));
""")
    assert out["sidebar"] == {"me": 1, "share": 1, "proposals": 1}
    assert [text for text, _ in out["ready"]["options"]] == _TEAM_OPTIONS
    assert out["ready"]["reads"] == {"me": 0, "share": 0, "proposals": 0}
    assert out["refreshed"] == {"me": 0, "share": 0, "proposals": 0}
    assert [text for text, _ in out["first"]["options"]] == _TEAM_OPTIONS
    assert out["first"]["reads"] == {"me": 1, "share": 1, "proposals": 0}
    assert [text for text, _ in out["together"]["options"]] == _TEAM_OPTIONS
    assert out["together"]["reads"]["proposals"] == 1 and 1 <= out["together"]["reads"]["me"] <= 2 \
        and 1 <= out["together"]["reads"]["share"] <= 2


@needs_node
def test_a_context_read_for_the_project_left_behind_is_never_the_new_pages_people():
    """The researcher switches project while the context of the first is still being read. The late answer
    names another person; the new page asks for its own context (one more read each) and shows its own
    people, not the first project's."""
    out = page_run(DESK_JS + _TEAM + r"""
const slow=deferred();
let mine=0;
customGet=(path)=>{
  if(path==='/api/proposals') return {role:'pi',team:true};
  if(path==='/api/me'){ mine+=1; if(mine===1) return slow.promise.then(()=>({state:'present',me:{id:'m-old',name:'Old Person',email:'o@x.org'}})); }
};
window.ROWorkspace.reset();
const first=window.ROWorkspace.load(root);          // epoch 0: its context read is held up
await sleepTick(); await sleepTick();
state.dataEpoch=1; window.ROWorkspace.reset(); state.currentPath='/projects/other'; state.currentId='p-other'; listed.project='/projects/other'; listed.id='p-other';
await window.ROWorkspace.load(root); await sleepTick(); await sleepTick();
slow.resolve(); await sleepTick(); await sleepTick(); await first;
console.log(JSON.stringify({options:OPTIONS(),reads:READS()}));
""")
    texts = [text for text, _ in out["options"]]
    assert "Lin Ying（我）" in texts and not any("Old Person" in text for text in texts)
    assert out["reads"]["me"] == 2 and out["reads"]["share"] >= 1  # one context per project time (the old one finishes its own reads)


def test_the_page_module_takes_its_people_from_the_context_only_when_it_is_ready_and_otherwise_reads_for_itself():
    """Source check: both reads in workspace.js sit behind a context the desk already holds; the page never
    asks the desk to load one (`ensureContext` would wait for a shared folder that may hang)."""
    source = (WEBAPP / "workspace.js").read_text(encoding="utf-8")
    assert "window.RODesk" in source and "desk.context()" in source and "ensureContext" not in source
    assert source.count('apiGet("/api/me")') == 1 and source.count('apiGet("/api/share")') == 1
    assert "context ? context.me : await apiGet" in source and "context && context.shared ? context.share : await apiGet" in source


# -- /api/stata remembers each script's reading ---------------------------------------------------------------


#: Do-files and .ado files written for the test, each making the lexer or the project pass do something else.
STATA_TEXTS = {
    "code/plain.do": 'use "data/raw.csv", clear\nsave "out/clean", replace\ngraph export "fig/a.pdf", replace\n',
    "code/macros.do": ('global Data "data"\nlocal f "raw"\nforeach v in a b {\n  use "$Data/`f\'_`v\'.dta", clear\n}\n'
                       'save "$Data/out", replace\nglobal Late "$Missing/x"\nuse "$Late", clear\n'),
    "code/delimit.do": '#delimit ;\nuse "a.dta",\n  clear;\nsave "b.dta", replace;\n#delimit cr\nuse "c.dta"\n',
    "code/lost_in_delimit.do": '#delimit ;\nuse "a.dta"\nsave "b.dta"\n',
    "code/open_comment.do": 'use "a"\n/* never closed\nsave "b"\n',
    "code/stray_brace.do": 'use "a"\n}\nsave "b"\n',
    "code/open_brace.do": 'foreach v in a b {\n  use "a"\n',
    "code/mata.do": 'use "a"\nmata:\nsave "no"\nend\nsave "b"\npython:\nuse "no"\nend\n',
    "code/program.do": ('program define mysave\n  syntax anything\n  tempfile t\n  c_local made "x"\n  save `anything\', replace\n'
                        'end\nmysave "viaprogram"\nuse "after"\n'),
    "code/st.do": 'mata:\nst_local("lv", "a")\nst_global("Gv", "b")\nend\nuse "$Gv/x.dta"\nuse "`lv\'.dta"\n',
    "code/chain.do": 'cd "sub"\ndo "plain.do"\nrun "macros.do"\ninclude "delimit.do"\nuse "rel.dta"\n',
    "code/bom_crlf.do": '\ufeffuse "a.dta"\r\nsave "b"\r\n',
    "code/unclosed_string.do": 'use "a\nsave "b"\n',
    "code/outside.do": 'global Elsewhere "C:/Users/someone/pkg"\nuse "$Elsewhere/data/x.dta"\nuse "/Users/else/y.dta"\n',
    "code/drops.do": 'global A "x"\nmacro drop _all\nuse "$A/y.dta"\nglobal B "z"\nmacro drop B\nuse "$B/q.dta"\n',
    "code/continued.do": 'use "a.dta", ///\n   clear\nsave "b", ///\n   replace\n',
    "code/empty.do": "",
    "code/only_comments.do": "* nothing here\n// nor here\n/* or here */\n",
    "code/helpers.ado": 'program define myhelper\n  global HelperG "h"\n  st_global("HelperS")\nend\nprogram define other\nend\n',
    "code/broken.ado": 'program define broken\n  /* never closed\nend\n',
}


def _scans_of(root: Path, texts: dict, *, only: str | None = None) -> dict:
    return stata.scan_project(root, texts, anchors={"Proj": "."}, only=only)


def test_a_scan_made_on_readings_is_the_scan_made_on_the_texts(tmp_path, caplog):
    """Every text above, as it is, as a Reading, and half and half: the scans are equal object for object (calls,
    skips, local roots, parsed), the warnings logged are the same lines in the same order, a Reading used twice
    gives the same scan twice, and `only` picks the same file."""
    caplog.set_level(logging.WARNING)
    for rel in STATA_TEXTS:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("", encoding="utf-8")
    (tmp_path / "code" / "sub").mkdir()
    readings = {rel: stata.reading_of(text) for rel, text in STATA_TEXTS.items()}
    half = {rel: (readings[rel] if k % 2 else STATA_TEXTS[rel]) for k, rel in enumerate(STATA_TEXTS)}

    def logged(texts, **kw):
        caplog.clear()
        return _scans_of(tmp_path, texts, **kw), [r.getMessage() for r in caplog.records]

    from_text, text_log = logged(STATA_TEXTS)
    from_readings, readings_log = logged(readings)
    from_half, half_log = logged(half)
    again, again_log = logged(readings)
    assert from_text == from_readings == from_half == again
    assert text_log == readings_log == half_log == again_log and text_log  # the lexer's complaints are said every time
    assert any("never closed" in line or "not closed" in line for line in text_log)
    assert from_text["code/open_comment.do"].parsed is False and from_text["code/plain.do"].parsed is True
    assert from_text["code/plain.do"].calls and from_text["code/outside.do"].skipped and from_text["code/st.do"].skipped
    for rel in ("code/plain.do", "code/macros.do", "code/chain.do"):
        assert _scans_of(tmp_path, STATA_TEXTS, only=rel)[rel] == _scans_of(tmp_path, readings, only=rel)[rel]
    assert all(isinstance(r, stata.Reading) and isinstance(r.commands, tuple) for r in readings.values())
    for text in STATA_TEXTS.values():
        assert stata.scan_text(text, "t.do") == stata.scan_project(None, {"t.do": stata.reading_of(text)})["t.do"]


def test_a_reading_is_what_the_text_says_by_itself():
    one = stata.reading_of('use "a"\nmata:\nst_local("lv")\nst_global("Gv")\nst_global("Gw")\nend\n')
    assert one.problem is None and one.st_locals == frozenset({"lv"}) and one.st_globals == ("Gv", "Gw")
    assert [c.text for c in one.commands] == ['use "a"', "mata:", 'st_local("lv")', 'st_global("Gv")', 'st_global("Gw")', "end"]
    cmds, problem = stata.commands('use "a"\nmata:\nst_local("lv")\nst_global("Gv")\nst_global("Gw")\nend\n')
    assert list(one.commands) == cmds and one.problem == problem
    assert stata.reading_of("/* never").problem
    with pytest.raises(AttributeError):
        one.problem = "changed"  # frozen: one reading is handed to every scan


def _settle(root: Path, age: int = HOUR_NS) -> None:
    """Every script one hour old: the memory trusts a stamp only for a file that has settled."""
    for path in [*root.rglob("*.do"), *root.rglob("*.ado")]:
        _age(path, age)


def _stata_project(tmp_path: Path) -> Path:
    """The package of the 15.3 tests (a global that heads paths which do not resolve), the scripts above beside
    it, and a vendored library that is listed and never read."""
    root = _package(tmp_path)
    for rel, text in STATA_TEXTS.items():
        target = root / "analysis" / "extra" / Path(rel).name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    (root / "ado" / "plus").mkdir(parents=True)
    (root / "ado" / "plus" / "vendored.ado").write_text("program define vendored\nend\n", encoding="utf-8")
    people.write_me(*PI)
    project_identity.init_project(root)
    _settle(root)
    return root


@pytest.fixture
def stata_spies(monkeypatch):
    """Counts of the lexer's runs (`reading_of`) and of the files read (`cloud.read_text`) while the test goes
    on, and an empty memory of readings."""
    dataflow._READINGS.clear()
    counts = {"lexed": 0, "read": [], "old": 0}
    lexed, read = stata.reading_of, cloud.read_text

    def counting_lexed(text):
        counts["lexed"] += 1
        return lexed(text)

    def counting_read(path):
        counts["read"].append(Path(path).name)
        return read(path)

    monkeypatch.setattr(stata, "reading_of", counting_lexed)
    monkeypatch.setattr(cloud, "read_text", counting_read)
    yield counts
    dataflow._READINGS.clear()


def _old_payload(root: Path, lang: str = "zh") -> dict:
    """/api/stata as it was: every script read and lexed on every request."""
    scans = dataflow._stata_scans

    def forget(*args, **kwargs):
        return scans(*args, **{**kwargs, "remember": False})

    mp = pytest.MonkeyPatch()
    mp.setattr(dataflow, "_stata_scans", forget)
    try:
        return node_api.stata_payload(root, lang)
    finally:
        mp.undo()


def test_the_stata_payload_is_the_old_payload_cold_warm_and_in_both_languages(tmp_path, stata_spies):
    root = _stata_project(tmp_path)
    old = {lang: _old_payload(root, lang) for lang in ("zh", "en")}
    assert old["zh"]["has_do"] and old["zh"]["proposals"] and old["zh"]["unresolved"]
    assert len(dataflow._READINGS) == 0  # the old path remembers nothing
    cold = {lang: node_api.stata_payload(root, lang) for lang in ("zh", "en")}
    warm = {lang: node_api.stata_payload(root, lang) for lang in ("zh", "en")}
    assert _dumps(old) == _dumps(cold) == _dumps(warm)
    assert len(dataflow._READINGS) == len(list(root.rglob("*.do"))) + len(list(root.rglob("*.ado"))) - 1  # not the vendored one


def test_a_refresh_reads_only_the_scripts_that_changed(tmp_path, stata_spies):
    """Scripts: the first request reads and lexes each (the vendored one never); the second reads none; one
    script edited is the one read; a new script is the one read; a deleted script is read by nobody and is gone
    from the answer. Every answer is the old path's."""
    root = _stata_project(tmp_path)
    scripts = [p for p in [*root.rglob("*.do"), *root.rglob("*.ado")] if "plus" not in p.parts]
    node_api.stata_payload(root)
    assert stata_spies["lexed"] == len(scripts) and len(stata_spies["read"]) == len(scripts)
    assert "vendored.ado" not in stata_spies["read"]
    stata_spies["read"].clear(); stata_spies["lexed"] = 0
    warm = node_api.stata_payload(root)
    assert stata_spies["lexed"] == 0 and stata_spies["read"] == []
    assert _dumps(warm) == _dumps(_old_payload(root))
    stata_spies["read"].clear(); stata_spies["lexed"] = 0

    edited = root / "analysis" / "run.do"
    edited.write_text(edited.read_text(encoding="utf-8") + 'use "$Proj/data/more.csv"\n', encoding="utf-8")
    _age(edited, HOUR_NS // 2)
    after_edit = node_api.stata_payload(root)
    assert stata_spies["read"] == ["run.do"] and stata_spies["lexed"] == 1
    stata_spies["read"].clear()
    assert _dumps(after_edit) == _dumps(_old_payload(root))
    stata_spies["read"].clear(); stata_spies["lexed"] = 0

    added = root / "analysis" / "scripts" / "2_new.do"
    added.write_text('use "$Proj/data/raw.csv"\nsave "$Proj/results/new2", replace\n', encoding="utf-8")
    _age(added, HOUR_NS)
    after_add = node_api.stata_payload(root)
    assert stata_spies["read"] == ["2_new.do"]
    assert _dumps(after_add) == _dumps(_old_payload(root))
    assert after_add != after_edit
    stata_spies["read"].clear()

    added.unlink()
    after_delete = node_api.stata_payload(root)
    assert stata_spies["read"] == [] and _dumps(after_delete) == _dumps(_old_payload(root)) and after_delete == after_edit


def test_a_script_changed_without_changing_its_size_or_replaced_by_one_of_the_same_size_and_time_is_read_again(tmp_path, stata_spies):
    root = _stata_project(tmp_path)
    clean = root / "analysis" / "scripts" / "1_clean.do"
    node_api.stata_payload(root)
    stata_spies["read"].clear()
    # the same length, other text, another modification time
    text = clean.read_text(encoding="utf-8")
    same_size = text.replace("raw.csv", "RAW.csv")
    assert len(same_size) == len(text) and same_size != text
    clean.write_text(same_size, encoding="utf-8")
    _age(clean, HOUR_NS // 2)
    assert _dumps(node_api.stata_payload(root)) == _dumps(_old_payload(root)) and stata_spies["read"][:1] == ["1_clean.do"]
    # the same length, the same modification time, another file (another inode)
    stata_spies["read"].clear()
    before = clean.stat()
    other = clean.with_name("swap.tmp")
    other.write_text(same_size.replace("RAW.csv", "Raw.csv"), encoding="utf-8")
    os.utime(other, ns=(before.st_atime_ns, before.st_mtime_ns))
    os.replace(other, clean)
    assert clean.stat().st_size == before.st_size and clean.stat().st_mtime_ns == before.st_mtime_ns
    assert clean.stat().st_ino != before.st_ino
    assert _dumps(node_api.stata_payload(root)) == _dumps(_old_payload(root))
    assert "1_clean.do" in stata_spies["read"]


def test_a_script_written_a_moment_ago_is_read_every_time_until_it_has_settled(tmp_path, stata_spies):
    root = _stata_project(tmp_path)
    fresh = root / "analysis" / "scripts" / "fresh.do"
    fresh.write_text('use "$Proj/data/raw.csv"\n', encoding="utf-8")  # modified just now
    for n in (1, 2, 3):
        node_api.stata_payload(root)
        assert stata_spies["read"].count("fresh.do") == n
    _age(fresh, memo.RACY_NS + 1_000_000_000)
    node_api.stata_payload(root)
    node_api.stata_payload(root)
    assert stata_spies["read"].count("fresh.do") == 4


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read what a mode refuses")
def test_a_remembered_script_that_can_no_longer_be_opened_is_unreadable_as_it_always_was(tmp_path, stata_spies):
    root = _stata_project(tmp_path)
    locked = root / "analysis" / "scripts" / "1_clean.do"
    node_api.stata_payload(root)
    mode = locked.stat().st_mode
    locked.chmod(0)
    try:
        assert _dumps(node_api.stata_payload(root)) == _dumps(_old_payload(root))
    finally:
        locked.chmod(stat.S_IMODE(mode))
    assert _dumps(node_api.stata_payload(root)) == _dumps(_old_payload(root))


def test_a_remembered_script_that_went_to_the_cloud_is_not_served_from_memory(tmp_path, stata_spies, monkeypatch):
    """The cloud check is made on every request, before the memory is asked: a script only the cloud has, whose
    client is not running, reads as unreadable (None) in the answer, remembered or not."""
    root = _stata_project(tmp_path)
    node_api.stata_payload(root)
    gone = root / "analysis" / "scripts" / "1_clean.do"
    real = cloud.blocked_provider
    provider = cloud.Provider("OneDrive", "OneDrive", "OneDrive-x", str(tmp_path))
    monkeypatch.setattr(cloud, "blocked_provider", lambda path: provider if Path(path) == gone else real(path))
    blocked = node_api.stata_payload(root)
    assert _dumps(blocked) == _dumps(_old_payload(root))
    monkeypatch.setattr(cloud, "blocked_provider", real)
    assert _dumps(node_api.stata_payload(root)) == _dumps(_old_payload(root))
    assert _dumps(blocked) != _dumps(node_api.stata_payload(root))  # the blocked script did change the answer


def test_the_ingest_does_not_remember_what_it_reads(tmp_path, stata_spies):
    """`_stata_scans` without `remember` (the scan that writes the index) reads and lexes every script as before and
    leaves the memory empty; `_scan_stata` is that."""
    root = _stata_project(tmp_path)
    dos = sorted(str(p.relative_to(root)) for p in root.rglob("*.do"))
    ados = sorted(str(p.relative_to(root)) for p in root.rglob("*.ado"))
    first = dataflow._stata_scans(root, dos, ados)
    again = dataflow._stata_scans(root, dos, ados)
    outcomes = dataflow._scan_stata(root, dos, ados)
    assert first == again and outcomes and len(dataflow._READINGS) == 0
    assert stata_spies["lexed"] == len(stata_spies["read"]) == 3 * (len(dos) + len(ados) - 1)  # each script, each time
    assert first == dataflow._stata_scans(root, dos, ados, remember=True) == dataflow._stata_scans(root, dos, ados, remember=True)


def test_the_memory_of_readings_is_bounded(tmp_path, stata_spies, monkeypatch):
    """2,000 scripts and 120,000 commands, least recently used first (DESIGN 18.4 item 7); a smaller memory is
    never over its bound and the answer does not depend on how much of it survives."""
    assert (dataflow._READINGS._items, dataflow._READINGS._budget) == (2000, 120_000)
    root = _stata_project(tmp_path)
    monkeypatch.setattr(dataflow, "_READINGS", memo.Memo(5, 60))
    old = _old_payload(root)
    for _ in range(3):
        assert _dumps(node_api.stata_payload(root)) == _dumps(old)
        assert len(dataflow._READINGS) <= 5 and dataflow._READINGS._total <= 60
    monkeypatch.setattr(dataflow, "_READINGS", memo.Memo(0))
    assert _dumps(node_api.stata_payload(root)) == _dumps(old) and len(dataflow._READINGS) == 0


def test_threads_asking_for_the_stata_page_together_all_get_the_old_answer(tmp_path, stata_spies):
    root = _stata_project(tmp_path)
    old = _dumps(_old_payload(root))
    answers: list[str] = []
    failures: list[BaseException] = []
    gate = threading.Barrier(6)

    def ask() -> None:
        try:
            gate.wait(timeout=10)
            answers.append(_dumps(node_api.stata_payload(root)))
        except BaseException as exc:  # noqa: BLE001
            failures.append(exc)

    threads = [threading.Thread(target=ask) for _ in range(6)]
    [t.start() for t in threads]
    [t.join(timeout=60) for t in threads]
    assert not failures and answers == [old] * 6
    stata_spies["read"].clear()
    assert _dumps(node_api.stata_payload(root)) == old and stata_spies["read"] == []


def test_the_page_asks_the_engine_for_the_stata_answer_twice_and_gets_the_same_one(tmp_path, stata_spies):
    """GET /api/stata over HTTP, as the page asks for it: a refresh after the first is answered from the memory
    of readings and says the same, in both languages, and a script changed between is seen."""
    root = _stata_project(tmp_path)
    httpd, thread, url = _serve(root)
    try:
        first = _call(url + "/api/stata", lang="en")
        second = _call(url + "/api/stata", lang="en")
        assert first[0] == 200 and first == second and first[1]["proposals"]
        assert stata_spies["read"].count("run.do") == 1  # read once for the two requests
        run_do = root / "analysis" / "run.do"
        run_do.write_text('do "$Proj/scripts/1_clean.do"\n', encoding="utf-8")
        _age(run_do, HOUR_NS // 2)
        third = _call(url + "/api/stata", lang="en")
        assert stata_spies["read"].count("run.do") == 2 and third[1] != first[1]
        assert third[1] == _old_payload(root, "en")
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
        httpd.server_close()
