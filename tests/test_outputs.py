"""Declared task products use configured folders and become readable app objects."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from researchos import bibliography, db, literature, mcp_server, outputs, paths, project, workspace
from researchos.ingest import files as ingest_files
from researchos.agents import brief, manager, runs, snapshot
from researchos.records import variables
from researchos.records.identity import read_identity
from researchos.webapp import node_api, variables_api
from test_agents_mcp import _run, _tasks, _task


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    project.init_project(root)
    return root


def put(root, rel, value):
    full = root / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(value, encoding="utf-8")
    return full


def configured(root):
    info = outputs.folders(root)
    outputs.save_folders(root, {"revision":info["revision"], "folders":info["folders"]})
    return info["folders"]


def complete(root, declared):
    pid = read_identity(root).identity.id
    _tasks(root)
    run_id = _run(root, pid, lang="en")
    runs.update_run(pid, run_id, output_folders=outputs.folders(root)["folders"])
    answer = mcp_server.agent_complete_task(root, pid, run_id, "A real report.", outputs=declared)
    return answer, pid, run_id


def test_folder_defaults_reuse_existing_directories_without_writes(root):
    (root / "scripts").mkdir()
    (root / "references").mkdir()
    got = outputs.folders(root)
    assert got["folders"]["code"] == "scripts"
    assert got["folders"]["literature"] == "references"
    assert not got["saved"] and got["revision"] is None
    assert not (root / "research").exists()
    assert not (root / "figures").exists()


def test_saving_folder_roles_preserves_files_and_uses_revision(root):
    path = put(root, "scripts/existing.py", "print('keep')")
    chosen = outputs.folders(root)["folders"]
    chosen["figure"] = "outputs/charts"
    saved = outputs.save_folders(root, {"revision":None,"folders":chosen})
    assert outputs.folders(root) == saved
    assert path.read_text() == "print('keep')"
    assert not (root / "outputs").exists()
    with pytest.raises(workspace.WorkspaceError, match="changed|revision|version"):
        outputs.save_folders(root, {"revision":None,"folders":chosen})


@pytest.mark.parametrize("folder", [".", "..", "../outside", "/tmp/outside", ".researchos/data", "safe/../data", "a\\b", "research/notes", "research/results/new", "node_modules/out"])
def test_folder_roles_refuse_unsafe_or_reserved_paths(root, folder):
    chosen = outputs.folders(root)["folders"]
    chosen["figure"] = folder
    with pytest.raises(workspace.WorkspaceError):
        outputs.save_folders(root, {"revision":None,"folders":chosen})
    assert not (root / outputs.FOLDERS_PATH).exists()


def test_symlink_folder_and_incomplete_roles_are_refused(root, tmp_path):
    (root / "linked").symlink_to(tmp_path, target_is_directory=True)
    chosen = outputs.folders(root)["folders"]
    chosen["code"] = "linked/code"
    with pytest.raises(workspace.WorkspaceError):
        outputs.save_folders(root, {"revision":None,"folders":chosen})
    with pytest.raises(workspace.WorkspaceError):
        outputs.save_folders(root, {"revision":None,"folders":{"code":"code"}})


def test_completion_registers_all_product_types_and_links_report(root):
    folders = configured(root)
    files = [("code","compare.py","print(1)"), ("data","derived.csv","x\n1\n"),
             ("figure","plot.svg",'<svg xmlns="http://www.w3.org/2000/svg"/>'), ("table","summary.csv","x\n1\n"),
             ("document","report.md","# Report"), ("variable","distance.toml",'name="Distance"\nmeaning="Flight distance"\nunit="m"'),
             ("literature","pilot-sources.json",json.dumps([{"title":"Pilot methods","abstract":"A local abstract."}]))]
    declarations = []
    for kind, name, content in files:
        rel = folders[kind] + "/" + name
        put(root, rel, content)
        declarations.append({"kind":kind,"path":rel,"title":name})
    answer, pid, run_id = complete(root, declarations)
    assert "marked the task done" in answer
    entries = outputs.catalog(root)["items"]
    assert {i["kind"] for i in entries} == set(outputs.ROLES) - {"work"}  # process files are never declared (DESIGN 19.2)
    assert all(i["run"] == run_id and i["state"] == "current" for i in entries)
    text = (root / _task(root)["report"]).read_text()
    assert "../../figures/plot.svg" in text and "../../literature/pilot-sources.json" in text
    signal, = runs.read_signals(pid, run_id)
    assert outputs.catalog_path(_task(root)["report"]) in signal["paths"] and signal["outputs_sha"]
    with db.connect(paths.graph_db_path(root)) as conn:
        assert len(node_api.results_payload(conn,root,{})["outputs"]["items"]) == 2
        assert len(node_api.data_code_payload(conn,root)["outputs"]["items"]) == 3
        refs = bibliography.catalog(conn,root,{},literature.ZoteroLibrary(None,"absent"))["items"]
        assert refs[0]["title"] == "Pilot methods" and refs[0]["abstract"] == "A local abstract."
        assert len(variables_api.list_payload(conn,root)["outputs"]["items"]) == 1


@pytest.mark.parametrize("declared", [None, {}, [{"kind":"figure","path":"wrong/plot.png"}],
    [{"kind":"figure","path":"figures/missing.png"}], [{"kind":"code","path":"code/data.csv"}],
    [{"kind":"data","path":"../outside.csv"}], [{"kind":"data","path":"data/a.csv","confirmed":True}]])
def test_bad_declarations_do_not_complete_task_or_write_catalog(root, declared):
    answer, pid, run_id = complete(root, declared)
    assert answer.startswith("Not written:"), answer
    assert not _task(root)["done"]
    assert not list(root.glob(outputs.CATALOG_PATH))
    assert runs.read_signals(pid,run_id) == []


def test_empty_output_list_is_valid_for_a_reading_task(root):
    answer, _pid, _run_id = complete(root, [])
    assert "marked the task done" in answer
    assert not list(root.glob(outputs.CATALOG_PATH))


def test_registered_export_does_not_need_a_special_filename_or_rescan(root):
    put(root, "literature/topic-a.json", '[{"title":"Methods","abstract":"Original abstract"}]')
    answer, _pid, _run_id = complete(root,[{"kind":"literature","path":"literature/topic-a.json"}])
    assert "marked the task done" in answer
    with db.connect(paths.graph_db_path(root)) as conn:
        refs = bibliography.catalog(conn,root,{},literature.ZoteroLibrary(None,"absent"))["items"]
    assert refs[0]["abstract_source"] == "literature/topic-a.json"


def test_variable_import_is_a_human_draft_not_a_confirmation(root):
    put(root, "research/variables/distance.toml", 'name="Distance"\nmeaning="Horizontal flight distance"\nunit="m"')
    answer, _pid, _run_id = complete(root,[{"kind":"variable","path":"research/variables/distance.toml"}])
    assert "marked the task done" in answer
    assert variables.read_cards(root) == []
    imported = outputs.import_variable(root, {"path":"research/variables/distance.toml","id":"distance"})
    card = variables.open_card(root, "distance")
    assert card.in_use is None
    assert card.draft == 1
    assert Path(root / imported["file"]).read_text().startswith('name="Distance"')
    assert not (root / ".researchos/variables/distance/log.toml").exists()


def test_variable_cannot_claim_human_adoption(root):
    put(root,"research/variables/v.toml",'name="Distance"\n[decision]\ndecided_by="PI"')
    answer, _pid, _run_id = complete(root,[{"kind":"variable","path":"research/variables/v.toml"}])
    assert answer.startswith("Not written:") and "human decision" in answer
    assert not _task(root)["done"]


def test_missing_and_changed_files_do_not_keep_stale_reference_metadata(root):
    full = put(root,"literature/sources.json", '[{"title":"Original"}]')
    complete(root,[{"kind":"literature","path":"literature/sources.json"}])
    full.write_text('[{"title":"Changed"}]')
    assert outputs.catalog(root)["items"][0]["state"] == "changed"
    with db.connect(paths.graph_db_path(root)) as conn:
        assert bibliography.catalog(conn,root,{},literature.ZoteroLibrary(None,"absent"))["items"] == []
    full.unlink()
    assert outputs.catalog(root)["items"] == []


def test_output_settings_and_catalog_are_protected_but_tool_hash_is_allowed(root):
    before = snapshot.take(root)
    prot = snapshot.protected(root,before)
    assert outputs.FOLDERS_PATH in prot["records"]
    rel = "research/results/" + "a" * 32 + ".outputs.json"
    put(root,rel,'{}')
    after = snapshot.take(root)
    changes = snapshot.compare(before,after)
    assert any(v["path"] == rel for v in snapshot.violations(changes,prot,git_before=None,git_after=None,allowed={},after=after))
    sha = after["files"][rel]["sha256"]
    assert not snapshot.violations(changes,prot,git_before=None,git_after=None,allowed={rel:{sha}},after=after)


def test_routing_instructions_are_present_for_every_runner_and_no_false_attempt_table(root):
    prot = snapshot.protected(root,snapshot.take(root))
    block = {"en":{},"zh":{}}
    for runner in ("claude-code","codex","opencode","deepseek-harness"):
        for lang in ("zh","en"):
            text = brief.rules(lang=lang,runner=runner,prot=prot,folders=outputs.folders(root)["folders"],
                               work_folder="work/20261025-fetch-data",slug="fetch-data")
            assert "outputs" in text and "`figures/`" in text and "decided_by" in text
            assert "attempt table (`research/folders.json`)" not in text
            # DESIGN 19.3: the eight locations, this task's work folder and name, then the rules for files
            assert "- work: `work/`" in text and text.index("- variable:") < text.index("- work:")
            assert "`work/20261025-fetch-data`" in text and "`fetch-data`" in text
            assert "`code/fetch-data/`" in text and "`figures/fetch-data/`" in text
            words = ("work folder","subfolder name","Process files","Create nothing under research/","one deliverable","key numbers",
                     "Save a note only","never as a list of files","A table is a result meant to be read","never by hash or run id",
                     "Process files are not reported") if lang == "en" else (
                     "工作文件夹","子目录名","过程文件","不要在 research/ 下新建任何东西","只交一份成果","关键数字",
                     "笔记只用来保存","不要写成文件清单","表是给人读的结果","不用哈希值或运行编号","过程文件不列入")
            for word in words:
                assert word in text,(runner,lang,word)
            assert not re.search(r"\u2192|->|=>",text)
            # the routing block names no tool, so it is the same words for every runner
            start = text.index("Output folders (" if lang == "en" else "产出目录（")
            end = text.index("\n\n",start)
            block[lang].setdefault(text[start:end],[]).append(runner)
    assert {lang:[len(found) for found in groups.values()] for lang,groups in block.items()} == {"en":[4],"zh":[4]}


def test_corrupt_catalog_does_not_get_overwritten(root):
    report = workspace.save_document(root,{"kind":"result","revision":None,"content":"An earlier report"})
    rel = outputs.catalog_path(report["path"])
    put(root,rel,'not json')
    put(root,"tables/counts.csv","n\n2\n")
    pid = read_identity(root).identity.id
    _tasks(root,report=report["path"])
    run_id = _run(root,pid)
    answer = mcp_server.agent_complete_task(root,pid,run_id,"New report",outputs=[{"kind":"table","path":"tables/counts.csv"}])
    assert answer.startswith("Not written:")
    assert (root / rel).read_text() == 'not json'
    assert not _task(root)["done"]
    assert (root / report["path"]).read_text() == report["content"]


def test_registration_is_allowed_by_run_assessment_and_undo_removes_its_catalog(root):
    pid = read_identity(root).identity.id
    _tasks(root)
    run_id = _run(root,pid,lang="en")
    rd = runs.run_dir(pid,run_id)
    before = snapshot.take(root,copy_to=rd / "before")
    (rd / "before.json").write_text(json.dumps(before))
    put(root,"tables/counts.csv","n\n2\n")
    answer = mcp_server.agent_complete_task(root,pid,run_id,"A report",outputs=[{"kind":"table","path":"tables/counts.csv"}])
    assert answer.startswith("Saved")
    agent_manager = manager.AgentManager()
    assessed = agent_manager._assess(root,pid,run_id,runs.load_run(pid,run_id),before,lambda:[])
    assert assessed["violations"] == []
    catalog_rel = outputs.catalog_path(_task(root)["report"])
    assert catalog_rel in assessed["tool_paths"]
    runs.update_run(pid,run_id,state="done",**{k:v for k,v in assessed.items() if k != "signal_info"})
    reverted = agent_manager.revert(root,pid,run_id,by=None)
    assert catalog_rel in reverted["removed"]
    assert "tables/counts.csv" in reverted["removed"]
    assert outputs.catalog(root)["items"] == []
    assert not _task(root)["done"]


def test_registered_pdf_is_listed_without_invented_reference_metadata(root):
    put(root,"literature/paper.pdf","%PDF-1.4\nfixture")
    complete(root,[{"kind":"literature","path":"literature/paper.pdf","title":"Local paper"}])
    assert outputs.catalog(root,{"literature"})["items"][0]["title"] == "Local paper"
    with db.connect(paths.graph_db_path(root)) as conn:
        assert bibliography.catalog(conn,root,{},literature.ZoteroLibrary(None,"absent"))["items"] == []


def test_completed_tool_schema_accepts_a_real_output_list(root):
    from test_agents_mcp import _call

    pid = read_identity(root).identity.id
    _tasks(root)
    run_id = _run(root,pid)
    put(root,"code/check.py","print('ok')")
    tool_server = mcp_server.build_agent_server(root,pid,run_id)
    answer = _call(tool_server,"researchos_complete_task",{"report":"Checked the pilot","outputs":[{"kind":"code","path":"code/check.py"}]})
    assert answer.startswith("Saved")
    assert outputs.catalog(root)["items"][0]["path"] == "code/check.py"


def test_existing_raw_data_gets_a_separate_derived_output_default(root):
    (root / "data/raw").mkdir(parents=True)
    assert outputs.folders(root)["folders"]["data"] == "data/derived"
    assert not (root / "data/derived").exists()


def test_folder_defaults_do_not_adopt_symlinks(root, tmp_path):
    (root / "code").symlink_to(tmp_path,target_is_directory=True)
    assert outputs.folders(root)["folders"]["code"] == "scripts"


def test_variable_revision_creates_a_new_draft_and_preserves_confirmed_version(root):
    from researchos.records import cards

    text = '''name="Time"
meaning="Flight duration"
unit="seconds"
granularity="launch"
[[input]]
dataset="data/pilot.csv"
fields=["release","landing"]
[construction]
formula="landing - release"
[decision]
why="Compare designs using the same unit."
'''
    rel = "research/variables/time.toml"
    put(root,rel,text)
    complete(root,[{"kind":"variable","path":rel}])
    outputs.import_variable(root,{"path":rel,"id":"time"})
    cards.confirm(root,"time",attested="unknown")
    old = (root / ".researchos/variables/time/v1.toml").read_bytes()
    put(root,rel,text.replace('landing - release','(landing - release) / 1000'))
    complete(root,[{"kind":"variable","path":rel}])
    outputs.import_variable(root,{"path":rel,"id":"time"})
    card = variables.open_card(root,"time")
    assert card.in_use == 1 and card.draft == 2
    assert (root / ".researchos/variables/time/v1.toml").read_bytes() == old
    assert "/ 1000" in (root / ".researchos/variables/time/v2.toml").read_text()


def test_catalog_reuses_fingerprints_of_unchanged_files(root, monkeypatch):
    put(root,"data/output.csv","x\n1\n")
    complete(root,[{"kind":"data","path":"data/output.csv"}])
    monkeypatch.setattr(snapshot,"fingerprint",lambda *_a,**_kw:pytest.fail("unchanged file rehashed"))
    assert outputs.catalog(root)["items"][0]["state"] == "current"


def test_undo_one_task_preserves_later_tasks_outputs_and_registration(root):
    pid = read_identity(root).identity.id
    _tasks(root)
    agent_manager = manager.AgentManager()
    run_ids = []
    for task_id, filename in (("t1","first.csv"),("t2","second.csv")):
        run_id = _run(root,pid,task_id=task_id)
        run_ids.append(run_id)
        rd = runs.run_dir(pid,run_id)
        before = snapshot.take(root,copy_to=rd / "before")
        (rd / "before.json").write_text(json.dumps(before))
        put(root,"tables/"+filename,"n\n2\n")
        answer = mcp_server.agent_complete_task(root,pid,run_id,"Report",outputs=[{"kind":"table","path":"tables/"+filename}])
        assert answer.startswith("Saved")
        assessed = agent_manager._assess(root,pid,run_id,runs.load_run(pid,run_id),before,lambda:[])
        assert assessed["violations"] == []
        runs.update_run(pid,run_id,state="done",**{k:v for k,v in assessed.items() if k != "signal_info"})
    agent_manager.revert(root,pid,run_ids[0],by=None)
    assert not (root / "tables/first.csv").exists()
    assert (root / "tables/second.csv").exists()
    assert not _task(root)["done"] and _task(root,"t2")["done"]
    assert [i["path"] for i in outputs.catalog(root)["items"]] == ["tables/second.csv"]


@pytest.mark.parametrize("tool, payload", [
    ("Write", {"file_path":"research/results/" + "a" * 32 + ".outputs.json", "content":"{}"}),
    ("Bash", {"command":"echo '{}' > research/results/" + "a" * 32 + ".outputs.json"}),
])
def test_agent_cannot_forge_output_records_before_they_exist(root,tool,payload):
    from researchos.agents import guard

    prot = snapshot.protected(root,snapshot.take(root))
    assert guard.check_tool(prot,tool,payload,cwd=str(root)) is not None


def test_team_review_dependencies_include_registered_files_and_metadata(root):
    configured(root)
    put(root,"tables/summary.csv","n\n2\n")
    complete(root,[{"kind":"table","path":"tables/summary.csv"}])
    report = _task(root)["report"]
    attached = outputs.related_files(root,[report])
    assert set(attached) == {outputs.catalog_path(report),"tables/summary.csv",outputs.FOLDERS_PATH}


# -- V15 step 2: the work role and the folder rule (DESIGN 19.2) --------------------------------------------------

SEVEN = {"code":"code","data":"data","figure":"figures","table":"tables","literature":"literature",
         "document":"reports","variable":"research/variables"}


def case_insensitive(root):
    (root / "casecheck").mkdir()
    return (root / "CASECHECK").exists()


def test_work_is_the_eighth_location_and_the_scan_knows_the_same_default(root):
    assert list(outputs.ROLES)[-1] == "work" and outputs.ROLES["work"] == ("work",) == (ingest_files.WORK_FOLDER,)
    got = outputs.folders(root)
    assert list(got["folders"]) == list(outputs.ROLES) and got["folders"]["work"] == "work"
    assert not (root / "work").exists() and not got["saved"]  # Research OS does not create it
    chosen = dict(got["folders"], work="process")
    outputs.save_folders(root, {"revision": None, "folders": chosen})
    assert outputs.folders(root)["folders"]["work"] == "process" and ingest_files.work_folder(root) == "process"


def test_a_folders_file_saved_before_the_work_role_reads_as_naming_the_default_and_saving_writes_all_eight(root):
    put(root, outputs.FOLDERS_PATH, json.dumps({"version": 1, "folders": {**SEVEN, "figure": "plots"}}))
    raw = (root / outputs.FOLDERS_PATH).read_bytes()
    got = outputs.folders(root)
    assert got["saved"] and list(got["folders"]) == list(outputs.ROLES)
    assert got["folders"]["work"] == "work" and got["folders"]["figure"] == "plots"
    assert (root / outputs.FOLDERS_PATH).read_bytes() == raw  # reading wrote nothing
    outputs.save_folders(root, {"revision": got["revision"], "folders": got["folders"]})
    assert json.loads((root / outputs.FOLDERS_PATH).read_text())["folders"] == got["folders"]
    assert len(got["folders"]) == 8 and len(json.loads((root / outputs.FOLDERS_PATH).read_text())["folders"]) == 8
    with pytest.raises(workspace.WorkspaceError):  # saving still wants every location named
        outputs.save_folders(root, {"revision": None, "folders": SEVEN})


def test_a_run_that_froze_seven_locations_before_the_work_role_can_still_complete(root):
    put(root, "figures/plot.svg", '<svg xmlns="http://www.w3.org/2000/svg"/>')
    pid = read_identity(root).identity.id
    _tasks(root)
    run_id = _run(root, pid, lang="en")
    runs.update_run(pid, run_id, output_folders=dict(SEVEN))
    answer = mcp_server.agent_complete_task(root, pid, run_id, "A report.", outputs=[{"kind": "figure", "path": "figures/plot.svg"}])
    assert "marked the task done" in answer


def test_an_agent_cannot_declare_a_process_file(root):
    put(root, "work/20261025-probe/probe.py", "print(1)")
    answer, pid, run_id = complete(root, [{"kind": "work", "path": "work/20261025-probe/probe.py"}])
    assert answer.startswith("Not written:") and "work folder" in answer and "Process files" in answer
    assert not _task(root)["done"] and not list(root.glob(outputs.CATALOG_PATH))
    assert runs.read_signals(pid, run_id) == []
    with pytest.raises(workspace.WorkspaceError) as caught:
        outputs.validate(root, [{"kind": "work", "path": "work/20261025-probe/probe.py"}], outputs.folders(root)["folders"])
    assert "过程文件" in caught.value.message_zh


def test_the_location_check_follows_the_file_systems_own_case_rule(root):
    insensitive = case_insensitive(root)
    put(root, "data/x.csv", "a\n1\n")
    locations = outputs.folders(root)["folders"]
    assert locations["data"] == "data"
    declared = [{"kind": "data", "path": "Data/x.csv"}]
    if insensitive:  # the same folder: the real paths are compared, not the strings
        assert [i["path"] for i in outputs.validate(root, declared, locations)] == ["Data/x.csv"]
    else:
        with pytest.raises(workspace.WorkspaceError, match="Put the data output under data/"):
            outputs.validate(root, declared, locations)
    put(root, "other/x.csv", "a\n1\n")
    put(root, "elsewhere/data/x.csv", "a\n1\n")
    for outside in ("other/x.csv", "elsewhere/data/x.csv", "Other/x.csv"):  # not inside the location on any volume
        with pytest.raises(workspace.WorkspaceError, match="Put the data output under data/|does not exist"):
            outputs.validate(root, [{"kind": "data", "path": outside}], locations)
    assert [i["path"] for i in outputs.validate(root, [{"kind": "data", "path": "data/x.csv"}], locations)] == ["data/x.csv"]


def test_notes_results_and_records_stay_refused_whatever_their_spelling(root):
    for rel in ("research/notes/" + "a" * 32 + ".md", "research/results/" + "a" * 32 + ".md", "research/tasks.json",
                "research/overview.md", "research/folders.json", "Research/Notes/x.md", "RESEARCH/RESULTS/x.outputs.json",
                "Research/Tasks.json", "research/notes/é.md".replace("é", "e\u0301")):
        assert outputs._is_record(rel), rel
    for rel in ("research/variables/v.toml", "data/x.csv", "research/notes.md", "research/notes_old/x.md", "tables/research/notes/x.csv"):
        assert not outputs._is_record(rel), rel
    locations = dict(outputs.folders(root)["folders"], document="Research/Notes")  # a spelling the location rule lets by
    if case_insensitive(root):
        put(root, "research/notes/plan.md", "# Plan")
        with pytest.raises(workspace.WorkspaceError, match="written by Research OS only"):
            outputs.validate(root, [{"kind": "document", "path": "Research/Notes/plan.md"}], locations)
    for rel in ("research/notes/plan.md", "research/results/r.md"):
        put(root, rel, "# Plan")
        with pytest.raises(workspace.WorkspaceError):
            outputs.validate(root, [{"kind": "document", "path": rel}], outputs.folders(root)["folders"])


@pytest.mark.parametrize("text, expected", [
    ("Fetch the macro data", "Fetch-the-macro-data"),
    ("  \n\n  Fetch the macro data\nsecond line", "Fetch-the-macro-data"),   # the first line that has words
    ("ab cd ef gh ij kl mn op qr", "ab-cd-ef-gh-ij-kl-mn-op"),                  # cut at 24, then trimmed of "-"
    ("x" * 40, "x" * 24),
    ("汇率与利率传导：基于 VAR 的分析", "汇率与利率传导-基于-VAR-的分析"),
    ("snake_case-and-dash", "snake_case-and-dash"),
    ("a / b: c? d*", "a-b-c-d"),
    ("a  -  b", "a---b"),                                                        # runs of other characters, not of "-"
    ("???", "task"), ("", "task"), ("   ", "task"), (None, "task"), (42, "task"), ("/-/", "task"),
    ("Cafe\u0301 am Rhein", "Caf\u00e9-am-Rhein"),                               # composed, so the letter is not cut
])
def test_a_slug_is_the_first_line_cut_to_24_with_other_runs_turned_into_one_dash(text, expected):
    assert outputs.slug(text) == expected
    assert len(outputs.slug(text)) <= outputs.SLUG_LIMIT


def test_filing_sorts_a_runs_created_files_into_five_classes_and_changes_nothing(root):
    put(root, "tables/summary.csv", "n\n2\n")
    complete(root, [{"kind": "table", "path": "tables/summary.csv"}])
    report = _task(root)["report"]
    catalog = outputs.catalog_path(report)
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    created = sorted([report, catalog, "tables/summary.csv", "work/20261025-probe/probe.py", "work/20261025-probe/x/data.csv",
                      "Work/20261025-Probe/loud.py", "src/__pycache__/a.cpython-312.pyc", "pkg/mod.pyc", ".pytest_cache/README.md",
                      "work/20261024-other/stray.py", "figures/loose.png", "notes_extra.py"])
    got = outputs.filing(root, created, records=[report, catalog, "research/tasks.json"], reports=[report, None],
                         work_folder="work/20261025-probe")
    assert got["counts"] == {"record": 2, "output": 1, "work": 3, "cache": 3, "unregistered": 3}
    assert got["unregistered"] == ["figures/loose.png", "notes_extra.py", "work/20261024-other/stray.py"]
    assert got["unregistered_more"] == 0 and set(got) == {"counts", "unregistered", "unregistered_more"}
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_filing_takes_the_first_matching_class_and_works_without_a_work_folder_or_catalog(root):
    got = outputs.filing(root, ["work/a/x.py", "work/a/__pycache__/x.pyc", "a.csv"], records=["work/a/x.py"],
                         reports=["research/results/" + "a" * 32 + ".md", "not a report"], work_folder="work/a")
    assert got["counts"] == {"record": 1, "output": 0, "work": 1, "cache": 0, "unregistered": 1}  # record, then work
    none = outputs.filing(root, ["work/a/x.py"], records=[], reports=[], work_folder=None)
    assert none["counts"]["unregistered"] == 1 and none["unregistered"] == ["work/a/x.py"]


def test_filing_keeps_at_most_500_unregistered_paths_and_counts_the_rest(root):
    created = [f"stray/file{i:04d}.py" for i in range(620)]
    got = outputs.filing(root, created, records=[], reports=[], work_folder=None)
    assert got["counts"]["unregistered"] == 620 and got["unregistered"] == created[:500] and got["unregistered_more"] == 120


def test_a_catalog_the_filing_cannot_read_registers_nothing_and_does_not_fail(root):
    report = workspace.save_document(root, {"kind": "result", "revision": None, "content": "A report"})["path"]
    put(root, outputs.catalog_path(report), "not json")
    got = outputs.filing(root, ["tables/a.csv"], records=[], reports=[report], work_folder=None)
    assert got["counts"]["unregistered"] == 1 and got["counts"]["output"] == 0


def test_the_after_run_check_files_what_the_run_created_and_an_undo_still_removes_all_of_it(root):
    """DESIGN 19.4 through the manager: the classes come out of the same comparison that finds the run's changes, in
    run.json's `filing` and the page's summary; it is no violation, and undo is as before."""
    pid = read_identity(root).identity.id
    _tasks(root)
    run_id = _run(root,pid,lang="en")
    runs.update_run(pid,run_id,work_folder="work/20261025-probe",slug="probe")
    rd = runs.run_dir(pid,run_id)
    before = snapshot.take(root,copy_to=rd / "before")
    (rd / "before.json").write_text(json.dumps(before))
    put(root,"tables/counts.csv","n\n2\n")
    put(root,"work/20261025-probe/probe.py","print(1)")
    put(root,"work/20261025-probe/scratch.csv","a\n1\n")
    put(root,"loose.py","print(2)")
    put(root,"pkg/__pycache__/m.cpython-312.pyc","x")
    answer = mcp_server.agent_complete_task(root,pid,run_id,"A report",outputs=[{"kind":"table","path":"tables/counts.csv"}])
    assert answer.startswith("Saved")
    agent_manager = manager.AgentManager()
    assessed = agent_manager._assess(root,pid,run_id,runs.load_run(pid,run_id),before,lambda:[])
    assert assessed["violations"] == []
    assert assessed["filing"] == {"counts":{"record":2,"output":1,"work":2,"cache":1,"unregistered":1},
                                  "unregistered":["loose.py"],"unregistered_more":0}
    runs.update_run(pid,run_id,state="done",**{k:v for k,v in assessed.items() if k != "signal_info"})
    summary = manager.summary(runs.load_run(pid,run_id),latest_of_task=True,active=False)
    assert summary["filing"] == assessed["filing"] and summary["work_folder"] == "work/20261025-probe"
    reverted = agent_manager.revert(root,pid,run_id,by=None)
    assert {"loose.py","work/20261025-probe/probe.py","tables/counts.csv"} <= set(reverted["removed"])
    assert not (root / "loose.py").exists() and not (root / "work/20261025-probe").exists()
