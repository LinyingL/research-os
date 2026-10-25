"""Declared task products use configured folders and become readable app objects."""
from __future__ import annotations

import contextlib
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from researchos import bibliography, db, literature, mcp_server, outputs, paths, people, project, workspace
from researchos.ingest import files as ingest_files
from researchos.agents import brief, manager, runs, snapshot
from researchos.records import situation, variables
from researchos.records.identity import read_identity
from researchos.webapp import node_api, variables_api
from test_agents_mcp import _run, _tasks, _task, _versions


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
    assert list(outputs.ROLES)[-1] == "work" and outputs.ROLES["work"] == ("work", "agent-work")
    assert outputs.ROLES["work"][0] == ingest_files.WORK_FOLDER
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
    ("a  -  b", "a-b"), ("a - b", "a-b"), ("a--b", "a-b"), ("-a-- --b-", "a-b"),   # a "-" among them is part of the run
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
    assert summary["filing"] == {"counts": assessed["filing"]["counts"], "unregistered_more": 0}  # no paths in the listing
    assert summary["work_folder"] == "work/20261025-probe"
    reverted = agent_manager.revert(root,pid,run_id,by=None)
    assert {"loose.py","work/20261025-probe/probe.py","tables/counts.csv"} <= set(reverted["removed"])
    assert not (root / "loose.py").exists() and not (root / "work/20261025-probe").exists()


# -- V15 step 2b: the work location stands apart, a candidate that cannot be used is passed over (DESIGN 19.2) ---------


def settle(root, **named):
    """Save the default locations with some named otherwise; the work location is whatever `named` says."""
    chosen = {**outputs.folders(root)["folders"], **named}
    return outputs.save_folders(root, {"revision": outputs.folders(root)["revision"], "folders": chosen})


@pytest.mark.parametrize("named, work", [
    ({}, "figures"),                                      # equal to another role's location
    ({}, "FIGURES"), ({}, "Tables"),                      # ... by the folding the scan uses
    ({}, "tables/runs"),                                  # inside one
    ({"figure": "plots/figs"}, "plots"),                  # containing one
    ({}, "research/variables/work"),                      # inside one, and under research/
    ({}, "research/work"), ({}, "Research/Work"),        # under research/ whatever the other locations are
])
def test_the_work_location_may_not_equal_contain_or_lie_inside_another_location_or_lie_under_research(root, named, work):
    with pytest.raises(workspace.WorkspaceError) as caught:
        settle(root, **named, work=work)
    assert "work location" in str(caught.value) and "过程文件的目录" in caught.value.message_zh
    assert caught.value.status == 409 and not (root / outputs.FOLDERS_PATH).exists()


def test_a_work_location_that_stands_apart_is_saved_and_the_other_seven_may_still_nest(root):
    saved = settle(root, work="process/runs", table="outputs/tables", figure="outputs/figures")
    assert saved["folders"]["work"] == "process/runs" and outputs.folders(root)["folders"] == saved["folders"]
    assert settle(root, work="workers")["folders"]["work"] == "workers"        # a name that merely starts alike
    assert settle(root, work="agent-work", code="agent")["folders"]["code"] == "agent"


def test_an_unsaved_default_that_would_clash_moves_on_to_the_next_candidate(root):
    # a version-1 file saved before the work location existed, whose code lives under work/
    put(root, outputs.FOLDERS_PATH, json.dumps({"version": 1, "folders": {**SEVEN, "code": "work/code"}}))
    got = outputs.folders(root)
    assert got["folders"]["work"] == "agent-work" and "choose" not in got and got["saved"]
    assert ingest_files.work_folder(root) == "agent-work"            # the scan names the same folder
    put(root, "work/code/a.py", "x = 1\n")
    put(root, "agent-work/20261025-x/probe.py", "x = 2\n")
    assert ingest_files.list_source_files(root)["py"] == ["work/code/a.py"]
    # the next candidate may clash too: nothing is usable, and the settings still read
    put(root, outputs.FOLDERS_PATH, json.dumps({"version": 1, "folders": {**SEVEN, "code": "work", "data": "agent-work/data"}}))
    got = outputs.folders(root)
    assert got["folders"]["work"] == "" and got["choose"] == ["work"]


def test_an_existing_candidate_folder_wins_and_one_that_is_a_file_or_a_link_is_passed_over(root, tmp_path):
    assert outputs.folders(root)["folders"]["work"] == "work"
    (root / "agent-work").mkdir()
    assert outputs.folders(root)["folders"]["work"] == "agent-work"       # a folder that is there, as for the other roles
    (root / "agent-work").rmdir()
    put(root, "work", "not a folder")
    assert outputs.folders(root)["folders"]["work"] == "agent-work" and "choose" not in outputs.folders(root)
    (root / "work").unlink()
    (root / "work").symlink_to(tmp_path, target_is_directory=True)
    assert outputs.folders(root)["folders"]["work"] == "agent-work"
    (root / "work").unlink()
    (root / "agent-work").symlink_to(tmp_path, target_is_directory=True)  # only the second is unusable: the first is back
    assert outputs.folders(root)["folders"]["work"] == "work"


def test_with_no_usable_candidate_the_settings_still_read_and_saving_them_asks_for_a_choice(root, tmp_path):
    put(root, "work", "a file")
    (root / "agent-work").symlink_to(tmp_path, target_is_directory=True)
    got = outputs.folders(root)
    assert list(got["folders"]) == list(outputs.ROLES) and got["folders"]["work"] == ""
    assert got["choose"] == ["work"] and got["saved"] is False and got["revision"] is None
    assert got["folders"]["code"] == "code"                                # the other seven are as ever
    with pytest.raises(workspace.WorkspaceError) as caught:
        outputs.save_folders(root, {"revision": None, "folders": got["folders"]})
    assert str(caught.value) == "Choose a folder for process files." and caught.value.message_zh == "请为过程文件选一个目录。"
    assert not (root / outputs.FOLDERS_PATH).exists()
    assert outputs.validate(root, [], got["folders"]) == []                # a declaration does not need the work location
    saved = outputs.save_folders(root, {"revision": None, "folders": {**got["folders"], "work": "process"}})
    assert "choose" not in outputs.folders(root) and outputs.folders(root) == saved
    # a settings file whose work is empty (edited by hand) reads the same way, and names no folder to the scan
    put(root, outputs.FOLDERS_PATH, json.dumps({"version": 1, "folders": {**SEVEN, "work": ""}}))
    assert outputs.folders(root)["choose"] == ["work"] and ingest_files.work_folder(root) == "work"


def test_a_saved_work_location_that_crowds_another_is_refused_like_any_unusable_setting(root):
    put(root, outputs.FOLDERS_PATH, json.dumps({"version": 1, "folders": {**SEVEN, "work": "reports/work"}}))
    with pytest.raises(workspace.WorkspaceError, match="work location"):
        outputs.folders(root)


# -- V15 step 2b: only a document the run created stops being a paper draft (DESIGN 19.7) -----------------------------


def picture_of(root, pid, run_id, **more):
    """The run's before-picture, taken now, as the manager keeps it in the run's folder."""
    rd = runs.run_dir(pid, run_id)
    before = snapshot.take(root, copy_to=rd / "before")
    (rd / "before.json").write_text(json.dumps({**before, **more}))
    return before


def declare(root, pid, run_id, declared, report="A real report."):
    runs.update_run(pid, run_id, output_folders=outputs.folders(root)["folders"])
    return mcp_server.agent_complete_task(root, pid, run_id, report, outputs=declared)


def entries_of(root):
    report = _task(root)["report"]
    return {i["path"]: i for i in json.loads((root / outputs.catalog_path(report)).read_text())["items"]}


def test_an_agents_declaration_says_created_for_a_path_its_before_picture_lacks_and_not_for_one_it_holds(root):
    put(root, "reports/draft.md", "# My own draft\n\nWe reach 87.3% accuracy.\n")        # the researcher's, there before
    pid = read_identity(root).identity.id
    _tasks(root)
    run_id = _run(root, pid, lang="en")
    picture_of(root, pid, run_id)
    put(root, "reports/draft.md", "# My own draft\n\nWe reach 87.3% accuracy, edited by the agent.\n")
    put(root, "reports/plan.md", "# Plan\n")
    put(root, "tables/summary.csv", "n\n2\n")
    answer = declare(root, pid, run_id, [{"kind": "document", "path": "reports/draft.md"}, {"kind": "document", "path": "reports/plan.md"},
                                         {"kind": "table", "path": "tables/summary.csv"}])
    assert answer.startswith("Saved"), answer
    found = entries_of(root)
    assert "created" not in found["reports/draft.md"]                       # it was there: stays what it was
    assert found["reports/plan.md"]["created"] is True and found["tables/summary.csv"]["created"] is True
    assert ingest_files.registered(root)["document"] == frozenset({"reports/plan.md"})     # only the created one leaves the drafts
    assert outputs.catalog(root)["items"] and all(i["state"] == "current" for i in outputs.catalog(root)["items"])


def test_a_later_run_of_the_task_declaring_the_file_again_keeps_it_created(root):
    pid = read_identity(root).identity.id
    _tasks(root)
    first = _run(root, pid, lang="en")
    picture_of(root, pid, first)
    put(root, "reports/plan.md", "# Plan\n")
    assert declare(root, pid, first, [{"kind": "document", "path": "reports/plan.md"}]).startswith("Saved")
    # the task is sent back: the second run's picture holds the file, which it edits and declares again
    second = _run(root, pid, lang="en")
    picture_of(root, pid, second)
    put(root, "reports/plan.md", "# Plan, revised\n")
    assert declare(root, pid, second, [{"kind": "document", "path": "reports/plan.md"}], "A second report.").startswith("Saved")
    entry = entries_of(root)["reports/plan.md"]
    assert entry["created"] is True and entry["run"] == second


def test_nothing_is_said_created_without_a_whole_before_picture_or_for_a_folder_it_does_not_walk(root):
    pid = read_identity(root).identity.id
    _tasks(root)
    run_id = _run(root, pid, lang="en")
    put(root, "reports/plan.md", "# Plan\n")
    assert outputs.made_by_run(pid, run_id, ["reports/plan.md"]) == set()         # no picture was taken
    runs.run_dir(pid, run_id).joinpath("before.json").write_text("not json")
    assert outputs.made_by_run(pid, run_id, ["reports/plan.md"]) == set()
    picture_of(root, pid, run_id, partial=True)                                    # cut short: it may have missed the file
    assert outputs.made_by_run(pid, run_id, ["reports/plan.md"]) == set()
    picture_of(root, pid, run_id)
    put(root, "reports/new.md", "# New\n")
    assert outputs.made_by_run(pid, run_id, ["reports/plan.md", "reports/new.md", "_cache/x.md", "Reports/PLAN.md"]) == {"reports/new.md"}
    assert outputs.made_by_run(pid, run_id, []) == set()
    answer, _pid, _run_id = complete(root, [])                                      # the helper's run has no picture at all
    assert answer.startswith("Saved") and not list(root.glob(outputs.CATALOG_PATH))
    put(root, "reports/other.md", "# Other\n")
    complete(root, [{"kind": "document", "path": "reports/other.md"}])
    assert "created" not in entries_of(root)["reports/other.md"]


# -- V15 step 2b: filing (DESIGN 19.4) --------------------------------------------------------------------------------


def test_a_registered_output_inside_the_runs_work_folder_files_as_output_not_work(root):
    put(root, "work/20261025-probe/summary.csv", "n\n2\n")
    put(root, "work/20261025-probe/scratch.csv", "n\n3\n")
    report = workspace.save_document(root, {"kind": "result", "revision": None, "content": "A report"})["path"]
    mark = outputs._fingerprint(root, "work/20261025-probe/summary.csv")
    put(root, outputs.catalog_path(report), json.dumps({"version": 1, "items": [
        {"kind": "table", "path": "work/20261025-probe/summary.csv", "title": "Summary", "fingerprint": mark}]}))
    got = outputs.filing(root, ["work/20261025-probe/summary.csv", "work/20261025-probe/scratch.csv"], records=[],
                         reports=[report], work_folder="work/20261025-probe")
    assert got["counts"] == {"record": 0, "output": 1, "work": 1, "cache": 0, "unregistered": 0}
    assert got["unregistered"] == []


# -- V15 step 3: a person registers and unregisters files; the rows name their task (DESIGN 19.5, 19.6) -----------------


def recorded(root, declared=()):
    """A project whose task t1 has a result record (an agent completed it, declaring `declared`): (report, pid, run id)."""
    answer, pid, run_id = complete(root, list(declared))
    assert answer.startswith("Saved"), answer
    # a real run says when it was made (`created`); the id's time can run ahead of the clock when a test mints many
    runs.update_run(pid, run_id, created=runs.now_iso())
    return _task(root)["report"], pid, run_id


def person(root, report, *items):
    return outputs.register_files(root, {"report": report, "items": [dict(item) for item in items]})


def created_by(pid, run_id, *paths_):
    runs.update_run(pid, run_id, changes={"created": list(paths_), "modified": [], "deleted": []})


def catalog_bytes(root, report):
    full = root / outputs.catalog_path(report)
    return full.read_bytes() if full.exists() else None


def test_a_person_registers_files_wherever_they_are_and_the_entry_says_who_when_and_whether_a_run_made_it(root):
    people.write_me("Li", "li@example.com")
    put(root, "tables/summary.csv", "n\n2\n")
    report, pid, run_id = recorded(root, [{"kind": "table", "path": "tables/summary.csv"}])
    created_by(pid, run_id, "analysis/fit.png", "tables/summary.csv")
    put(root, "analysis/fit.png", "png")                                  # in none of the places
    put(root, "scratch/run.log", "text")                                  # kind work takes any suffix
    put(root, "notes.txt", "text")
    got = person(root, report, {"kind": "figure", "path": "analysis/fit.png", "title": " Fit "},
                 {"kind": "work", "path": "scratch/run.log"}, {"kind": "document", "path": "notes.txt"})
    assert got == {"catalog": outputs.catalog_path(report), "registered": ["analysis/fit.png", "scratch/run.log", "notes.txt"]}
    found = entries_of(root)
    fit = found["analysis/fit.png"]
    assert set(fit) == {"kind", "path", "title", "fingerprint", "report", "task", "evidence", "who", "at", "created"}
    assert (fit["kind"], fit["title"], fit["report"], fit["task"]) == ("figure", "Fit", report, "t1")
    assert (fit["evidence"], fit["who"], fit["created"]) == ("person", "li@example.com", True)
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", fit["at"])
    assert (found["scratch/run.log"]["created"], found["scratch/run.log"]["title"]) == (False, "run.log")
    assert found["notes.txt"]["created"] is False                         # no run of the task made it
    assert found["tables/summary.csv"]["evidence"] == "agent_declaration" and "who" not in found["tables/summary.csv"]
    assert [i["state"] for i in outputs.catalog(root)["items"]] == ["current"] * 4


def test_a_registration_is_a_save_with_history_and_a_backup_like_an_agents(root):
    report, pid, _run_id = recorded(root)
    put(root, "tables/a.csv", "n\n1\n")
    put(root, "tables/b.csv", "n\n2\n")
    rel = outputs.catalog_path(report)
    person(root, report, {"kind": "table", "path": "tables/a.csv"})                # the catalog is new
    first = _versions(pid, root, rel)
    person(root, report, {"kind": "table", "path": "tables/b.csv"})                # and then changed
    second = _versions(pid, root, rel)
    assert len(first) == 1 and len(second) == 2
    assert list((root / ".researchos/backups").glob("*.outputs.json*"))           # the earlier catalog was kept


@pytest.mark.parametrize("item", [
    {"kind": "document", "path": "research/notes/n.md"}, {"kind": "document", "path": "research/results/other.md"},
    {"kind": "document", "path": "REPORT"}, {"kind": "work", "path": "CATALOG"}, {"kind": "work", "path": "research/tasks.json"},
    {"kind": "work", "path": "research/folders.json"}, {"kind": "work", "path": "research/overview.md"},
    {"kind": "work", "path": "Research/Notes/N.md"},                                  # whatever its spelling
    {"kind": "work", "path": ".researchos/state.json"}, {"kind": "work", "path": ".hidden/a.log"},
    {"kind": "work", "path": "scratch/.secret.log"}, {"kind": "work", "path": "node_modules/pkg/index.js"},
    {"kind": "work", "path": "venv/a.txt"}, {"kind": "work", "path": "src/__pycache__/a.pyc"},
    {"kind": "code", "path": "scratch/run.log"}, {"kind": "figure", "path": "scratch/run.log"},   # a kind's suffixes stand
    {"kind": "work", "path": "missing.log"}, {"kind": "work", "path": "../outside.log"}, {"kind": "work", "path": "/etc/hosts"},
    {"kind": "work", "path": "scratch/../scratch/run.log"}, {"kind": "work", "path": "scratch"},   # not normal / not a file
    {"kind": "other", "path": "scratch/run.log"}, {"kind": "work"}, {"path": "scratch/run.log"},
    {"kind": "work", "path": "scratch/run.log", "title": ""}, {"kind": "work", "path": "scratch/run.log", "title": "a\nb"},
    {"kind": "work", "path": "scratch/run.log", "confirmed": True},
    {"kind": "literature", "path": "scratch/bad.bib"}, {"kind": "variable", "path": "scratch/bad.toml"},
])
def test_a_person_cannot_register_what_the_checks_of_17a1527_refuse_and_the_whole_batch_waits(root, item):
    report, _pid, _run_id = recorded(root)
    put(root, "scratch/run.log", "text")
    put(root, "scratch/good.log", "text")
    put(root, "scratch/.secret.log", "text")
    put(root, "scratch/bad.bib", "not a bibliography")
    put(root, "scratch/bad.toml", "not = a variable")
    put(root, "research/notes/n.md", "# n")
    put(root, "research/results/other.md", "# other")
    for rel in (".researchos/state.json", ".hidden/a.log", "node_modules/pkg/index.js", "venv/a.txt", "src/__pycache__/a.pyc"):
        put(root, rel, "text")
    item = {k: (report if v == "REPORT" else outputs.catalog_path(report) if v == "CATALOG" else v) for k, v in item.items()}
    before = catalog_bytes(root, report)
    with pytest.raises(workspace.WorkspaceError):
        person(root, report, {"kind": "work", "path": "scratch/good.log"}, item)
    assert catalog_bytes(root, report) == before                                  # the good one was not registered either


def test_a_person_registers_only_for_a_task_that_owns_the_record_and_each_file_once(root):
    report, _pid, _run_id = recorded(root)
    put(root, "scratch/run.log", "text")
    one = {"kind": "work", "path": "scratch/run.log"}
    for bad in ("research/results/" + "0" * 32 + ".md", "research/results/other.md", "scratch/run.log", None, 7):
        with pytest.raises(workspace.WorkspaceError):
            outputs.register_files(root, {"report": bad, "items": [one]})
    for items in ([], None, "x", [one, one], [one] * 201):
        with pytest.raises(workspace.WorkspaceError):
            outputs.register_files(root, {"report": report, "items": items})
    assert catalog_bytes(root, report) is None
    (root / report).unlink()                                                      # the record itself is gone
    with pytest.raises(workspace.WorkspaceError):
        person(root, report, one)


def many_files(root, count):
    """`count` small code files under gen/ and the items that register them."""
    for i in range(count):
        put(root, f"gen/f{i:04d}.py", f"x = {i}\n")
    return [{"kind": "code", "path": f"gen/f{i:04d}.py"} for i in range(count)]


def test_a_person_registers_more_files_in_one_call_than_an_agent_may_declare(root):
    report, _pid, _run_id = recorded(root)
    items = many_files(root, 250)
    got = person(root, report, *items)                                             # the review list sends a task's files at once
    assert got["registered"] == [i["path"] for i in items]
    assert set(entries_of_report(root, report)) == {i["path"] for i in items}
    assert {e["evidence"] for e in entries_of_report(root, report).values()} == {"person"}
    with pytest.raises(workspace.WorkspaceError, match="at most 200"):             # an agent's declaration stays at 200
        outputs.validate(root, items[:201], outputs.folders(root)["folders"])
    with pytest.raises(workspace.WorkspaceError, match="at most 200"):
        outputs.validate(root, "x", None, anywhere=True)                           # and a person's call is still a list


def test_a_task_whose_catalog_cannot_hold_the_files_says_so_and_changes_nothing(root):
    report, _pid, _run_id = recorded(root)
    items = many_files(root, 1001)
    sentence = "这项任务的登记清单装不下这么多文件。请少勾选一些，或先移除不需要的登记。"
    with pytest.raises(workspace.WorkspaceError) as count:                         # more files than a catalog's count
        person(root, report, *items)
    with pytest.raises(workspace.WorkspaceError) as size:                          # 1,000 entries are more than the 200 KB a record may be
        person(root, report, *items[:1000])
    assert count.value.message_zh == size.value.message_zh == sentence
    assert "cannot hold that many files" in str(count.value) and str(count.value) == str(size.value)
    assert catalog_bytes(root, report) is None
    person(root, report, *items[:300])                                             # what fits goes through
    before = catalog_bytes(root, report)
    with pytest.raises(workspace.WorkspaceError) as more:                          # and what would not fit leaves the catalog as it was
        person(root, report, *items[300:1000])
    assert more.value.message_zh == sentence and catalog_bytes(root, report) == before
    assert len(entries_of_report(root, report)) == 300


def test_a_catalog_past_its_count_is_refused_in_the_same_words(root, monkeypatch):
    report, _pid, _run_id = recorded(root)
    monkeypatch.setattr(outputs, "LIMIT", 5)
    items = many_files(root, 7)
    person(root, report, *items[:3])
    before = catalog_bytes(root, report)
    with pytest.raises(workspace.WorkspaceError, match="cannot hold that many files"):
        person(root, report, *items[3:6])                                          # 3 held and 3 more are 6 of 5
    assert catalog_bytes(root, report) == before
    person(root, report, *items[3:5])                                              # exactly the count is fine
    assert len(entries_of_report(root, report)) == 5


def test_a_person_cannot_register_for_a_task_that_was_removed_but_can_still_unregister(root):
    put(root, "scratch/run.log", "text")
    report, _pid, _run_id = recorded(root)
    person(root, report, {"kind": "work", "path": "scratch/run.log"})
    tasks, revision = workspace.read_tasks(root)
    workspace.save_tasks(root, {"tasks": [t for t in tasks if t["id"] != "t1"], "revision": revision})
    put(root, "scratch/two.log", "text")
    with pytest.raises(workspace.WorkspaceError):
        person(root, report, {"kind": "work", "path": "scratch/two.log"})
    outputs.unregister_file(root, {"report": report, "path": "scratch/run.log"})
    assert entries_of_report(root, report) == {}


def entries_of_report(root, report):
    return {i["path"]: i for i in json.loads((root / outputs.catalog_path(report)).read_text())["items"]}


def test_created_is_true_for_a_file_a_run_of_the_task_made_on_this_mac_and_stays_so(root):
    put(root, "analysis/one.csv", "n\n1\n")
    put(root, "analysis/two.csv", "n\n2\n")
    put(root, "analysis/three.csv", "n\n3\n")
    report, pid, run_id = recorded(root)
    created_by(pid, run_id, "analysis/one.csv", "Analysis/Two.csv")
    other = _run(root, pid, task_id="t2")                                         # another task's run does not count
    created_by(pid, other, "analysis/three.csv")
    person(root, report, {"kind": "data", "path": "analysis/one.csv"}, {"kind": "data", "path": "analysis/two.csv"},
           {"kind": "data", "path": "analysis/three.csv"})
    found = entries_of(root)
    assert [found[p]["created"] for p in ("analysis/one.csv", "analysis/two.csv", "analysis/three.csv")] == [True, True, False]
    runs.update_run(pid, run_id, changes={"created": [], "modified": [], "deleted": []})      # the record of it is gone
    person(root, report, {"kind": "data", "path": "analysis/one.csv"})                          # registered again
    assert entries_of(root)["analysis/one.csv"]["created"] is True                              # once created, created
    runs.update_run(pid, run_id, changes={"created": ["analysis/three.csv"], "modified": [], "deleted": []}, reverted=True)
    person(root, report, {"kind": "data", "path": "analysis/three.csv"})                        # an undone run made nothing
    assert entries_of(root)["analysis/three.csv"]["created"] is False


def test_registering_an_agents_file_again_makes_it_the_persons_and_keeps_what_the_agent_knew(root):
    put(root, "reports/plan.md", "# Plan\n")
    pid = read_identity(root).identity.id
    _tasks(root)
    run_id = _run(root, pid, lang="en")
    picture_of(root, pid, run_id)
    put(root, "reports/new.md", "# New\n")
    assert declare(root, pid, run_id, [{"kind": "document", "path": "reports/new.md"}]).startswith("Saved")
    report = _task(root)["report"]
    assert entries_of(root)["reports/new.md"]["created"] is True and entries_of(root)["reports/new.md"]["evidence"] == "agent_declaration"
    person(root, report, {"kind": "document", "path": "reports/new.md", "title": "Retitled"})
    entry = entries_of(root)["reports/new.md"]
    assert (entry["evidence"], entry["title"], entry["created"]) == ("person", "Retitled", True)
    assert "run" not in entry and "agent" not in entry
    assert len(entries_of(root)) == 1


def test_who_is_the_id_me_toml_names_none_when_it_names_nobody_and_a_broken_one_stops_the_write(root):
    put(root, "scratch/a.log", "text")
    put(root, "scratch/b.log", "text")
    report, _pid, _run_id = recorded(root)
    person(root, report, {"kind": "work", "path": "scratch/a.log"})
    assert entries_of(root)["scratch/a.log"]["who"] is None                      # no me.toml: nobody is named
    people.me_path().parent.mkdir(parents=True, exist_ok=True)
    people.me_path().write_text("this is not toml = = =")
    before = catalog_bytes(root, report)
    with pytest.raises(workspace.WorkspaceError, match="me.toml"):
        person(root, report, {"kind": "work", "path": "scratch/b.log"})
    assert catalog_bytes(root, report) == before                                  # no entry without saying who made it


def test_a_registration_is_a_human_write_and_refused_inside_an_agents_run(root, monkeypatch):
    put(root, "scratch/a.log", "text")
    report, _pid, _run_id = recorded(root)
    monkeypatch.setenv("RESEARCHOS_AGENT_RUN", "r-20261025T090000Z-abcdef")
    with pytest.raises(situation.AgentRunRefused):
        person(root, report, {"kind": "work", "path": "scratch/a.log"})
    with pytest.raises(situation.AgentRunRefused):
        outputs.unregister_file(root, {"report": report, "path": "scratch/a.log"})
    assert catalog_bytes(root, report) is None


def test_unregistering_takes_one_entry_away_leaves_the_file_and_keeps_an_emptied_catalog_valid(root):
    put(root, "tables/summary.csv", "n\n2\n")
    put(root, "scratch/run.log", "text")
    report, pid, _run_id = recorded(root, [{"kind": "table", "path": "tables/summary.csv"}])
    person(root, report, {"kind": "work", "path": "scratch/run.log"})
    rel = outputs.catalog_path(report)
    before = len(_versions(pid, root, rel))
    assert outputs.unregister_file(root, {"report": report, "path": "scratch/run.log"}) == {"catalog": rel, "removed": "scratch/run.log"}
    assert list(entries_of(root)) == ["tables/summary.csv"] and (root / "scratch/run.log").read_text() == "text"
    assert len(_versions(pid, root, rel)) == before + 1                           # a save: history and backup
    outputs.unregister_file(root, {"report": report, "path": "tables/summary.csv"})   # an agent's entry goes the same way
    assert json.loads((root / rel).read_text()) == {"version": 1, "items": []}    # still a valid catalog
    assert (root / "tables/summary.csv").read_text() == "n\n2\n"
    assert outputs.catalog(root) == {"items": [], "error": None}
    person(root, report, {"kind": "work", "path": "scratch/run.log"})              # and it takes entries again
    assert list(entries_of(root)) == ["scratch/run.log"]


@pytest.mark.parametrize("body", [{"path": "scratch/other.log"}, {"path": None}, {"path": 7}, {"path": ""}, {}])
def test_unregistering_something_that_is_not_registered_is_refused_and_changes_nothing(root, body):
    put(root, "scratch/run.log", "text")
    report, _pid, _run_id = recorded(root)
    with pytest.raises(workspace.WorkspaceError):                                 # no catalog at all
        outputs.unregister_file(root, {"report": report, **body})
    person(root, report, {"kind": "work", "path": "scratch/run.log"})
    before = catalog_bytes(root, report)
    with pytest.raises(workspace.WorkspaceError):
        outputs.unregister_file(root, {"report": report, **body})
    for report_ in (None, "scratch/run.log", "research/results/" + "0" * 32 + ".md"):
        with pytest.raises(workspace.WorkspaceError):
            outputs.unregister_file(root, {"report": report_, "path": "scratch/run.log"})
    assert catalog_bytes(root, report) == before


def test_a_corrupt_catalog_is_not_overwritten_by_a_person_either(root):
    put(root, "scratch/run.log", "text")
    report, _pid, _run_id = recorded(root)
    put(root, outputs.catalog_path(report), "not json")
    for call in (lambda: person(root, report, {"kind": "work", "path": "scratch/run.log"}),
                 lambda: outputs.unregister_file(root, {"report": report, "path": "scratch/run.log"})):
        with pytest.raises(workspace.WorkspaceError):
            call()
    assert catalog_bytes(root, report) == b"not json"


@pytest.mark.parametrize("text, expected", [
    ("Short task", "Short task"), ("  \n\n  First line  \nsecond", "First line"), ("## Heading task", "Heading task"),
    ("", ""), (None, ""), (7, ""), ("   \n  ", ""), ("tabs\tand\0nul", "tabs and nul"),
    ("word " * 30, ("word " * 30)[:80].rsplit(" ", 1)[0] + "…"),
    ("x" * 100, "x" * 80 + "…"),
    ("汇" * 100, "汇" * 80 + "…"),
    ("alpha beta, " * 10, "alpha beta, " * 6 + "alpha…"),
])
def test_a_tasks_label_is_its_first_line_cut_the_way_the_result_records_title_is(text, expected):
    assert outputs.task_title(text) == expected
    assert mcp_server._result_title(None, text if isinstance(text, str) else "") == (expected or "Task result")
    assert mcp_server.DEFAULT_TITLE_LIMIT == outputs.TITLE_CUT == 80


def test_every_registered_row_names_its_task_and_its_time_and_a_removed_task_reads_so(root):
    put(root, "tables/summary.csv", "n\n2\n")
    put(root, "scratch/run.log", "text")
    report, _pid, run_id = recorded(root, [{"kind": "table", "path": "tables/summary.csv"}])
    person(root, report, {"kind": "work", "path": "scratch/run.log"})
    rows = {r["path"]: r for r in outputs.catalog(root)["items"]}
    agent_row, person_row = rows["tables/summary.csv"], rows["scratch/run.log"]
    assert agent_row["task_title"] == person_row["task_title"] == "Clean the survey data" and "task_removed" not in agent_row
    started = datetime.strptime(run_id[2:18], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    assert agent_row["at"] == started.astimezone().isoformat(timespec="seconds")      # no `at`: the run id's UTC time, local
    assert datetime.fromisoformat(person_row["at"]).tzinfo is not None
    assert datetime.fromisoformat(person_row["at"]) == datetime.fromisoformat(entries_of(root)["scratch/run.log"]["at"].replace("Z", "+00:00"))
    tasks, revision = workspace.read_tasks(root)
    workspace.save_tasks(root, {"tasks": [t for t in tasks if t["id"] != "t1"], "revision": revision})
    gone = {r["path"]: r for r in outputs.catalog(root)["items"]}
    assert all(r["task_removed"] is True and "task_title" not in r for r in gone.values())
    put(root, "research/tasks.json", "not json")                                          # a damaged list names no task
    damaged = outputs.catalog(root)
    assert damaged["error"] is None and all("task_title" not in r and "task_removed" not in r for r in damaged["items"])


def test_the_views_rows_come_newest_first_by_their_time_whoever_made_them(root):
    people.write_me("Li", "li@example.com")
    for name in ("old", "mid", "new"):
        put(root, f"tables/{name}.csv", "n\n1\n")
    report, pid, run_id = recorded(root, [{"kind": "table", "path": "tables/old.csv"}])
    later = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(timespec="seconds").replace("+00:00", "Z")
    person(root, report, {"kind": "table", "path": "tables/mid.csv"})
    path = root / outputs.catalog_path(report)
    value = json.loads(path.read_text())
    value["items"][1]["at"] = later
    path.write_text(json.dumps(value))
    assert [r["path"] for r in outputs.catalog(root)["items"]] == ["tables/mid.csv", "tables/old.csv"]
    value["items"][1]["at"] = "2020-01-01T00:00:00Z"
    path.write_text(json.dumps(value))
    assert [r["path"] for r in outputs.catalog(root)["items"]] == ["tables/old.csv", "tables/mid.csv"]
    value["items"][1]["at"] = "not a time"                                                  # unreadable: last, never a failure
    path.write_text(json.dumps(value))
    assert [r["path"] for r in outputs.catalog(root)["items"]] == ["tables/old.csv", "tables/mid.csv"]


def test_task_outputs_gives_each_task_with_a_record_its_files_filing_and_work_folder(root):
    put(root, "tables/summary.csv", "n\n2\n")
    put(root, "tables/gone.csv", "n\n3\n")
    put(root, "figures/fit.png", "png")
    report, pid, run_id = recorded(root, [{"kind": "table", "path": "tables/summary.csv"},
                                           {"kind": "table", "path": "tables/gone.csv"}])
    put(root, "scratch/run.log", "text")
    person(root, report, {"kind": "work", "path": "scratch/run.log"}, {"kind": "figure", "path": "figures/fit.png"})
    (root / "tables/gone.csv").unlink()
    (root / "scratch/run.log").write_text("changed")
    put(root, "work/20261025-clean/a.py", "1")
    put(root, "work/20261025-clean/deep/b.py", "2")
    put(root, "work/20261025-clean/.hidden/c.py", "3")
    put(root, "work/20261025-clean/.d", "4")
    put(root, "loose.py", "5")
    put(root, "also/loose.csv", "6")
    created_by(pid, run_id, report, outputs.catalog_path(report), "research/tasks.json", "tables/summary.csv", "figures/fit.png",
               "work/20261025-clean/a.py", "work/20261025-clean/deep/b.py", "loose.py", "also/loose.csv", "deleted.py",
               "pkg/__pycache__/m.pyc", ".cache/x.py")
    runs.update_run(pid, run_id, work_folder="work/20261025-clean")
    tasks, _revision = workspace.read_tasks(root)
    got = outputs.task_outputs(root, pid, tasks)
    assert list(got) == ["t1"]                                                       # t2 has no record
    row = got["t1"]
    assert [(e["kind"], e["path"], e["title"], e["state"], e["evidence"]) for e in row["entries"]] == [
        ("table", "tables/summary.csv", "summary.csv", "current", "agent_declaration"),
        ("table", "tables/gone.csv", "gone.csv", "gone", "agent_declaration"),    # gone: stays listed, to be removed (19.6)
        ("work", "scratch/run.log", "run.log", "changed", "person"),
        ("figure", "figures/fit.png", "fit.png", "current", "person")]
    assert row["error"] is False
    assert row["filing"] == {"counts": {"record": 3, "output": 2, "work": 2, "cache": 2, "unregistered": 2},
                             "unregistered_more": 0}                              # loose.py, also/loose.csv: there still; deleted.py is gone; .cache and __pycache__ are caches
    assert (row["work_folder"], row["work_files"], row["work_files_more"]) == ("work/20261025-clean", 2, False)


def test_filing_in_a_task_row_follows_a_registration_made_since_and_a_run_from_before_v15(root):
    report, pid, run_id = recorded(root)
    put(root, "loose.py", "1")
    put(root, "also.py", "2")
    created_by(pid, run_id, "loose.py", "also.py")
    tasks, _revision = workspace.read_tasks(root)
    unregistered = lambda: outputs.task_outputs(root, pid, tasks)["t1"]["filing"]["counts"]["unregistered"]  # noqa: E731
    assert "filing" not in runs.load_run(pid, run_id)                                # a run of before V15: no filing of its own
    assert unregistered() == 2                                                       # all it created counts as unregistered
    person(root, report, {"kind": "code", "path": "loose.py"})
    assert unregistered() == 1
    outputs.unregister_file(root, {"report": report, "path": "loose.py"})
    assert unregistered() == 2
    runs.update_run(pid, run_id, reverted=True)                                      # undone: what it created was moved away
    assert outputs.task_outputs(root, pid, tasks)["t1"]["filing"] is None


def test_a_task_row_is_about_the_latest_run_only_and_says_nothing_of_files_without_a_run(root):
    report, pid, run_id = recorded(root)
    put(root, "old.py", "1")
    created_by(pid, run_id, "old.py")
    runs.update_run(pid, run_id, work_folder="work/20261025-old")
    newer = _run(root, pid)
    runs.update_run(pid, newer, work_folder="work/20261025-new")
    tasks, _revision = workspace.read_tasks(root)
    row = outputs.task_outputs(root, pid, tasks)["t1"]
    assert row["work_folder"] == "work/20261025-new" and row["work_files"] == 0           # the folder is not there yet
    assert row["filing"]["counts"]["unregistered"] == 0                                    # the newer run created nothing
    assert outputs.task_outputs(root, "p-" + "0" * 8, tasks)["t1"]["work_folder"] is None  # no runs of that project on this Mac
    assert outputs.task_outputs(root, pid, [])  == {}
    assert outputs.task_outputs(root, pid, [{"id": "t9", "text": "x", "done": False}]) == {}


def test_the_work_folder_count_is_bounded_and_follows_no_links(root, monkeypatch, tmp_path):
    report, pid, run_id = recorded(root)
    for i in range(6):
        put(root, f"work/w/d{i % 2}/f{i}.txt", "x")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.txt").write_text("x")
    (root / "work/w/link").symlink_to(outside, target_is_directory=True)
    (root / "work/w/file-link.txt").symlink_to(outside / "x.txt")
    runs.update_run(pid, run_id, work_folder="work/w")
    tasks, _revision = workspace.read_tasks(root)
    assert outputs.task_outputs(root, pid, tasks)["t1"]["work_files"] == 6
    monkeypatch.setattr(outputs, "WORK_COUNT_LIMIT", 4)
    row = outputs.task_outputs(root, pid, tasks)["t1"]
    assert (row["work_files"], row["work_files_more"]) == (4, True)


def test_a_catalog_a_row_cannot_read_is_said_and_does_not_fail_the_rest(root):
    report, pid, _run_id = recorded(root)
    put(root, outputs.catalog_path(report), "not json")
    tasks, _revision = workspace.read_tasks(root)
    row = outputs.task_outputs(root, pid, tasks)["t1"]
    assert row["error"] is True and row["entries"] == []


# -- V15 step 4: the files of earlier runs, listed for a person to review (DESIGN 19.5, 19.10 item 4) -------------------

#: Paths shaped like the agents' dated folders in Quant (19.0), with the kind each of the eight rules gives (None: rule 8).
SWORD = "research/sword_backtest_review_20261024"
OPENSTOCK = "research/openstock_review_20261024"
RULES = [
    # 1 -- process files, whatever else they look like
    (f"{SWORD}/checks/verify_backtest.py", "work"), (f"{SWORD}/checks/summary.csv", "work"), (f"{SWORD}/checks/plot.png", "work"),
    (f"{OPENSTOCK}/sources/README.md", "work"), (f"{OPENSTOCK}/sources/vendor/lib.py", "work"), (f"{SWORD}/logs/run.txt", "work"),
    (f"{SWORD}/tmp/x.csv", "work"), (f"{SWORD}/scratch/x.py", "work"), (f"{SWORD}/cache/x.json", "work"), ("a/check/x.md", "work"),
    ("a/log/x.csv", "work"), ("fetch_stdout.txt", "work"), (f"{SWORD}/fetch_stderr.txt", "work"), ("run.log", "work"),
    ("report_check.json", "work"), (f"{SWORD}/summary_check.md", "work"), ("pytest_results.xml", "work"),
    (f"{SWORD}/data/prices_sha256.json", "work"), ("Checks/Run.PY", "work"), ("figures/logs/fit.png", "work"),
    # 2 -- figures
    (f"{SWORD}/figures/equity.png", "figure"), ("figures/x/y.png", "figure"), ("plot.JPG", "figure"), ("a/b.svg", "figure"),
    (f"{SWORD}/figures/equity.pdf", "figure"), ("plots/fit.pdf", "figure"), ("fig/a.pdf", "figure"), ("Figures/A.PDF", "figure"),
    # 3 -- references and variable drafts
    ("references/library.bib", "literature"), ("research/variables/gdp.toml", "variable"),
    # 4 -- code
    (f"{SWORD}/scripts/fetch_prices.py", "code"), ("src/sword_v03/x.py", "code"), ("analysis/model.R", "code"), ("do/clean.do", "code"),
    ("Scripts/A.PY", "code"), ("nb/explore.ipynb", "code"), ("run_all.sh", "code"),
    # 5 -- tables
    (f"{SWORD}/tables/attribution.csv", "table"), ("tables/x.tsv", "table"), ("data/tables/summary.csv", "table"),
    # 6 -- data
    (f"{SWORD}/data/prices.csv", "data"), ("data/derived/x/y.csv", "data"), ("tables/summary.xlsx", "data"), ("out/result.json", "data"),
    ("out/panel.dta", "data"), ("figures/panel.csv", "data"), ("x.parquet", "data"),
    # 7 -- documents
    ("reports/X.md", "document"), (f"{SWORD}/report.pdf", "document"), ("reports/plan.docx", "document"), ("notes.txt", "document"),
    ("page.html", "document"), ("paper/main.tex", "document"), ("tables/readme.md", "document"),
    # 8 -- none
    ("LICENSE", None), ("data/notes.xyz", None), ("plot.tiff", None), (f"{SWORD}/other.toml", None), ("a/b.rst", None),
]


@pytest.mark.parametrize("rel, kind", RULES)
def test_the_eight_rules_suggest_a_kind_first_match_wins(rel, kind):
    assert outputs.suggest_kind(rel) == kind
    if kind not in {None, "work"}:                                       # a suggestion a person can really register
        assert Path(rel).suffix.lower() in outputs.SUFFIXES[kind]


def test_a_dated_folder_of_an_agent_reads_as_one_name_across_its_kinds():
    folder = f"{SWORD}"
    got = {rel: outputs.suggest_kind(rel) for rel in (f"{folder}/{p}" for p in (
        "checks/a.py", "scripts/a.py", "data/a.csv", "figures/a.png", "tables/a.csv", "report.md", "a_stdout.txt"))}
    assert list(got.values()) == ["work", "code", "data", "figure", "table", "document", "work"]


def earlier(root, pid, stamp, created, task_id="t1", **more):
    """A finished run of `task_id` that started at `stamp` (UTC, `YYYYMMDDTHHMMSS`) and created `created`: its id."""
    run_id = f"r-{stamp}Z-aaaaaa"
    runs.create_run(pid, {"id": run_id, "project_id": pid, "root": str(root), "task_id": task_id, "task_text": "x",
                          "runner": "claude-code", "model": "sonnet", "owner": "agent:claude-code:sonnet",
                          "label": "Claude Code · sonnet", "state": "done", "reverted": False,
                          "created": f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}T{stamp[9:11]}:{stamp[11:13]}:{stamp[13:15]}.000000Z",
                          "changes": {"created": list(created), "modified": [], "deleted": [], "by_person": []},
                          "tool_paths": [], **more})
    return run_id


def second_record(root, task_id="t2"):
    """A result record for a second task; its report path."""
    report = "research/results/" + "b" * 32 + ".md"
    put(root, report, "# Second result\n")
    tasks, revision = workspace.read_tasks(root)
    workspace.save_tasks(root, {"tasks": [{**t, "report": report} if t["id"] == task_id else t for t in tasks], "revision": revision})
    return report


def listing(root, pid, run=None):
    return outputs.backfill(root, pid, run)


def flat(got):
    """`[(task, run, path, kind)]` in the order the listing gives them."""
    return [(g["task"], r["id"], f["path"], f["kind"]) for g in got["groups"] for r in g["runs"] for f in r["files"]]


def utc(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def test_the_listing_gathers_what_earlier_runs_created_by_task_and_run_with_the_suggested_kinds(root):
    report, pid, _bare = recorded(root)                                              # t1 has a record; this run created nothing
    second = second_record(root)
    sword = [f"{SWORD}/checks/verify_backtest.py", f"{SWORD}/scripts/fetch_prices.py", f"{SWORD}/data/prices.csv",
             f"{SWORD}/figures/equity.png", f"{SWORD}/tables/attribution.csv", f"{SWORD}/report.md", f"{SWORD}/checks/run_stdout.txt"]
    stock = [f"{OPENSTOCK}/sources/README.md", "src/sword_v03/x.py", "reports/X.md", "data/derived/x/y.csv", "figures/x/y.png",
             "report_check.json", "LICENSE"]
    for rel in (*sword, *stock):
        put(root, rel, "x")
    old = earlier(root, pid, "20261024T101010", sword)
    new = earlier(root, pid, "20261025T090000", stock, task_id="t2")
    runs.update_run(pid, old, created=None)                                           # no time of its own: the id's
    got = listing(root, pid)
    assert [(g["task"], g["text"], g["report"]) for g in got["groups"]] == [("t2", "Another task", second),
                                                                           ("t1", "Clean the survey data", report)]
    assert (got["listed"], got["more"]) == (14, 0)
    assert [(r["id"], r["agent"]) for g in got["groups"] for r in g["runs"]] == [(new, "Claude Code · sonnet"), (old, "Claude Code · sonnet")]
    assert [utc(r["date"]) for g in got["groups"] for r in g["runs"]] == [datetime(2026, 10, 25, 9, tzinfo=timezone.utc),
                                                                         datetime(2026, 10, 24, 10, 10, 10, tzinfo=timezone.utc)]
    assert flat(got) == [("t2", new, rel, kind) for rel, kind in zip(stock, ["work", "code", "document", "data", "figure", "work", None])] + [
        ("t1", old, rel, kind) for rel, kind in zip(sword, ["work", "code", "data", "figure", "table", "document", "work"])]
    assert utc(runs.load_run(pid, new)["created"]) == utc(got["groups"][0]["runs"][0]["date"])      # the run's own `created`
    runs.update_run(pid, new, started="2026-10-25T10:30:00.500000Z")                                # a start is preferred
    assert utc(listing(root, pid)["groups"][0]["runs"][0]["date"]) == datetime(2026, 10, 25, 10, 30, tzinfo=timezone.utc)


def test_one_task_with_several_runs_is_one_group_newest_run_first(root):
    report, pid, _bare = recorded(root)
    for rel in ("a.py", "b.py", "c.py"):
        put(root, rel, "x")
    first = earlier(root, pid, "20261020T080000", ["a.py"])
    last = earlier(root, pid, "20261022T080000", ["c.py"])
    middle = earlier(root, pid, "20261021T080000", ["b.py"])
    got = listing(root, pid)
    assert [g["task"] for g in got["groups"]] == ["t1"]
    assert [(r["id"], [f["path"] for f in r["files"]]) for r in got["groups"][0]["runs"]] == [(last, ["c.py"]), (middle, ["b.py"]), (first, ["a.py"])]


def test_the_listing_leaves_out_records_caches_process_files_of_the_run_what_is_gone_and_what_is_registered(root):
    report, pid, _bare = recorded(root)
    second = second_record(root)
    here = ["scripts/keep.py", "tables/registered.csv", "tables/elsewhere.csv", "analysis/tool_wrote.md", "pkg/__pycache__/m.pyc", "m.pyc",
            ".pytest_cache/README.md", ".DS_Store", "figures/.DS_Store", ".hidden/x.py", "node_modules/x.js", "work/20261024-x/probe.py",
            "Work/20261024-X/Later.py", "link.py"]
    for rel in here:
        put(root, rel, "x")
    (root / "adir").mkdir()
    (root / "link.py").unlink()
    (root / "link.py").symlink_to(root / "scripts/keep.py")
    created = [*here, "gone.py", "adir", 5, None, "research/tasks.json", report, outputs.catalog_path(report), outputs.FOLDERS_PATH,
               "research/notes/n.md", "keep/../x.py", "/abs.py"]
    old = earlier(root, pid, "20261024T101010", created, work_folder="work/20261024-x", tool_paths=["analysis/tool_wrote.md"])
    person(root, report, {"kind": "table", "path": "tables/registered.csv"})                    # registered for this task
    outputs.register_files(root, {"report": second, "items": [{"kind": "table", "path": "tables/elsewhere.csv"}]})   # and for another
    assert flat(listing(root, pid)) == [("t1", old, "scripts/keep.py", "code")]
    outputs.unregister_file(root, {"report": report, "path": "tables/registered.csv"})           # a registration taken away is a file to review again
    assert [p for _t, _r, p, _k in flat(listing(root, pid))] == ["scripts/keep.py", "tables/registered.csv"]


def test_the_listing_is_about_runs_not_undone_of_tasks_that_still_exist_with_a_result_record(root):
    report, pid, _bare = recorded(root)
    second = second_record(root)
    for rel in ("undone.py", "kept.py", "removed.py", "no_record.py", "lost_record.py", "second.py"):
        put(root, rel, "x")
    earlier(root, pid, "20261020T080000", ["undone.py"], reverted=True)               # moved away by its undo
    kept = earlier(root, pid, "20261021T080000", ["kept.py"])
    earlier(root, pid, "20261022T080000", ["removed.py"], task_id="t9")                         # no such task any more
    tasks, revision = workspace.read_tasks(root)
    workspace.save_tasks(root, {"tasks": [*tasks, {"id": "t3", "text": "No record yet", "done": False, "owner": "", "due": ""},
                                          {"id": "t4", "text": "Record lost", "done": False, "owner": "", "due": "",
                                           "report": "research/results/" + "c" * 32 + ".md"}], "revision": revision})
    earlier(root, pid, "20261023T080000", ["no_record.py"], task_id="t3")                       # a task without a result record
    earlier(root, pid, "20261023T090000", ["lost_record.py"], task_id="t4")                     # the record file is gone
    gone = earlier(root, pid, "20261024T080000", ["second.py"], task_id="t2")
    assert [(t, r, p) for t, r, p, _k in flat(listing(root, pid))] == [("t2", gone, "second.py"), ("t1", kept, "kept.py")]
    tasks, revision = workspace.read_tasks(root)
    workspace.save_tasks(root, {"tasks": [t for t in tasks if t["id"] != "t2"], "revision": revision})   # the task is removed
    assert [(t, r, p) for t, r, p, _k in flat(listing(root, pid))] == [("t1", kept, "kept.py")]


def test_a_run_filter_lists_that_run_alone_and_a_file_made_again_is_listed_under_the_newest_run(root):
    report, pid, _bare = recorded(root)
    for rel in ("shared.py", "only_old.py", "only_new.py"):
        put(root, rel, "x")
    old = earlier(root, pid, "20261020T080000", ["shared.py", "only_old.py"])
    new = earlier(root, pid, "20261021T080000", ["shared.py", "only_new.py"])
    assert flat(listing(root, pid)) == [("t1", new, "shared.py", "code"), ("t1", new, "only_new.py", "code"), ("t1", old, "only_old.py", "code")]
    assert flat(listing(root, pid, new)) == [("t1", new, "shared.py", "code"), ("t1", new, "only_new.py", "code")]
    assert flat(listing(root, pid, old)) == [("t1", old, "shared.py", "code"), ("t1", old, "only_old.py", "code")]
    nothing = {"groups": [], "listed": 0, "more": 0}
    assert listing(root, pid, "r-20261019T080000Z-aaaaaa") == nothing                           # a run that is not there
    assert listing(root, pid, "not a run id") == nothing
    assert listing(root, pid, 7) == listing(root, pid)                                          # not a run id at all: no filter
    runs.update_run(pid, old, reverted=True)
    assert listing(root, pid, old) == nothing                                                   # undone runs are never listed
    assert flat(listing(root, pid)) == [("t1", new, "shared.py", "code"), ("t1", new, "only_new.py", "code")]


def test_a_project_without_runs_or_with_an_unreadable_id_lists_nothing(root):
    recorded(root)
    nothing = {"groups": [], "listed": 0, "more": 0}
    assert outputs.backfill(root, "p-" + "0" * 8) == nothing
    assert outputs.backfill(root, "nope") == nothing
    assert outputs.backfill(root, None) == nothing


def test_the_listing_names_at_most_5000_files_and_counts_the_rest(root, monkeypatch):
    report, pid, _bare = recorded(root)
    assert outputs.BACKFILL_LIMIT == 5000
    monkeypatch.setattr(outputs, "BACKFILL_LIMIT", 4)
    names = [f"gen/f{i}.py" for i in range(7)]
    for rel in names:
        put(root, rel, "x")
    old = earlier(root, pid, "20261020T080000", [names[0], names[1], "gone.py", "pkg/__pycache__/x.pyc"])
    new = earlier(root, pid, "20261021T080000", [names[2], names[3], names[4], names[5], names[6], names[0], "gone2.py"])
    got = listing(root, pid)
    assert (got["listed"], got["more"]) == (4, 3)                                                # names[0] is the newest run's; gone ones do not count
    assert [f["path"] for g in got["groups"] for r in g["runs"] for f in r["files"]] == [names[2], names[3], names[4], names[5]]
    assert [r["id"] for g in got["groups"] for r in g["runs"]] == [new]                          # the older run's files all fall past the bound
    monkeypatch.setattr(outputs, "BACKFILL_LIMIT", 5000)
    assert listing(root, pid)["listed"] == 7


def test_the_listing_writes_nothing_to_the_project_or_to_the_runs(root):
    report, pid, _bare = recorded(root)
    put(root, "scripts/a.py", "x")
    put(root, "reports/plan.md", "# plan")
    earlier(root, pid, "20261020T080000", ["scripts/a.py", "reports/plan.md"])

    def tree(*bases):
        return {str(p): (p.stat().st_mtime_ns, p.read_bytes()) for base in bases for p in base.rglob("*") if p.is_file()}

    before = tree(root, runs.project_runs_dir(pid))
    assert listing(root, pid)["listed"] == 2
    assert tree(root, runs.project_runs_dir(pid)) == before


def test_registering_what_the_listing_offers_says_person_and_created_and_stops_a_runs_documents_being_drafts(root):
    people.write_me("Li", "li@example.com")
    put(root, "reports/mine.md", "# My own draft\n\nWe reach 87.3% accuracy.\n")                # there before any run
    report, pid, _bare = recorded(root)
    second = second_record(root)
    for rel in ("reports/plan.md", "scripts/fetch.py", "data/prices.csv", f"{OPENSTOCK}/sources/README.md"):
        put(root, rel, "x")
    earlier(root, pid, "20261024T101010", ["reports/plan.md", "scripts/fetch.py", f"{OPENSTOCK}/sources/README.md"])
    earlier(root, pid, "20261025T090000", ["data/prices.csv"], task_id="t2")
    got = listing(root, pid)
    assert ingest_files.registered(root).get("document") is None                                  # nothing registered yet
    for group in got["groups"]:                                                                   # one call per task, the suggested kinds
        outputs.register_files(root, {"report": group["report"], "items": [
            {"kind": f["kind"], "path": f["path"]} for r in group["runs"] for f in r["files"] if f["kind"]]})
    mine = outputs.register_files(root, {"report": report, "items": [{"kind": "document", "path": "reports/mine.md"}]})
    assert mine["registered"] == ["reports/mine.md"]
    first, second_entries = entries_of_report(root, report), entries_of_report(root, second)
    assert {p: (e["kind"], e["evidence"], e["created"], e["who"]) for p, e in first.items()} == {
        "reports/plan.md": ("document", "person", True, "li@example.com"), "scripts/fetch.py": ("code", "person", True, "li@example.com"),
        f"{OPENSTOCK}/sources/README.md": ("work", "person", True, "li@example.com"),
        "reports/mine.md": ("document", "person", False, "li@example.com")}
    assert {p: (e["kind"], e["created"]) for p, e in second_entries.items()} == {"data/prices.csv": ("data", True)}
    assert ingest_files.registered(root)["document"] == frozenset({"reports/plan.md"})           # the run's document is no draft; mine still is
    assert listing(root, pid) == {"groups": [], "listed": 0, "more": 0}                           # all of it registered now


def test_the_listing_of_a_finished_run_is_what_its_filing_calls_unregistered_and_an_undo_empties_it(root):
    """Through the manager, as a real run ends: the files the task row counts as new (19.4, 19.6) are the ones the list
    for that run offers -- not the work folder's, the caches', the records' nor the registered output."""
    pid = read_identity(root).identity.id
    _tasks(root)
    run_id = _run(root, pid, lang="en")
    runs.update_run(pid, run_id, work_folder="work/20261025-probe", slug="probe")
    rd = runs.run_dir(pid, run_id)
    before = snapshot.take(root, copy_to=rd / "before")
    (rd / "before.json").write_text(json.dumps(before))
    put(root, "tables/counts.csv", "n\n2\n")
    put(root, "work/20261025-probe/probe.py", "print(1)")
    put(root, "loose.py", "print(2)")
    put(root, "analysis/checks/verify.py", "print(3)")
    put(root, "pkg/__pycache__/m.cpython-312.pyc", "x")
    assert mcp_server.agent_complete_task(root, pid, run_id, "A report", outputs=[{"kind": "table", "path": "tables/counts.csv"}]).startswith("Saved")
    agent_manager = manager.AgentManager()
    assessed = agent_manager._assess(root, pid, run_id, runs.load_run(pid, run_id), before, lambda: [])
    runs.update_run(pid, run_id, state="done", **{k: v for k, v in assessed.items() if k != "signal_info"})
    assert assessed["filing"]["unregistered"] == ["analysis/checks/verify.py", "loose.py"]
    assert [(p, k) for _t, _r, p, k in flat(listing(root, pid))] == [("analysis/checks/verify.py", "work"), ("loose.py", "code")]
    tasks, _revision = workspace.read_tasks(root)
    assert outputs.task_outputs(root, pid, tasks)["t1"]["filing"]["counts"]["unregistered"] == 2          # the number the row shows
    assert flat(listing(root, pid, run_id)) == flat(listing(root, pid))
    agent_manager.revert(root, pid, run_id, by=None)
    assert listing(root, pid) == {"groups": [], "listed": 0, "more": 0}                                    # moved away by the undo


# -- V15 step 4b: what the reviews of steps 2b to 4 left (DESIGN 19.5, 19.6) ------------------------------------------


@pytest.fixture
def local_zone():
    """Sets this process's zone for a test (the conversion to local time overflows at one edge of the calendar or the
    other, depending on the side of UTC the zone lies on) and puts it back."""
    patch = pytest.MonkeyPatch()    # its own, so that putting the zone back undoes nothing else a test patched

    def use(name):
        patch.setenv("TZ", name)
        time.tzset()
    yield use
    patch.undo()
    time.tzset()


#: An `at` that is none, and the zone in which it overflows: the offsets overflow in every zone, the `Z` ones on one side.
BAD_TIMES = [("0001-01-01T00:00:00Z", "America/Los_Angeles"), ("9999-12-31T23:59:59Z", "Europe/Berlin"),
             ("9999-12-31T23:59:59Z", "Pacific/Auckland"), ("0001-01-01T00:00:00+05:00", "UTC"),
             ("9999-12-31T23:59:59-05:00", "UTC"), ("not a time", "UTC"), ("", "UTC"), (12345, "UTC"), ({"x": 1}, "UTC")]


@pytest.mark.parametrize("bad, zone", BAD_TIMES)
def test_an_entry_whose_time_cannot_be_read_or_shown_is_dated_by_its_run_and_never_empties_a_view(root, local_zone, bad, zone):
    local_zone(zone)
    put(root, "tables/a.csv", "n\n1\n")
    put(root, "tables/b.csv", "n\n2\n")
    report, _pid, run_id = recorded(root, [{"kind": "table", "path": "tables/a.csv"}])
    person(root, report, {"kind": "table", "path": "tables/b.csv"})
    path = root / outputs.catalog_path(report)
    value = json.loads(path.read_text())
    value["items"][0]["at"] = bad                                                   # the agent's entry; the person's is fine
    path.write_text(json.dumps(value))
    got = outputs.catalog(root)
    assert got["error"] is None and sorted(r["path"] for r in got["items"]) == ["tables/a.csv", "tables/b.csv"]
    row = next(r for r in got["items"] if r["path"] == "tables/a.csv")
    started = datetime.strptime(run_id[2:18], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    assert row["at"] == started.astimezone().isoformat(timespec="seconds")          # the run id's time stands in
    value["items"][0]["run"] = "not a run id"                                       # and with that gone too: no date, still a row
    path.write_text(json.dumps(value))
    row = next(r for r in outputs.catalog(root)["items"] if r["path"] == "tables/a.csv")
    assert "at" not in row and "task_title" in row


def test_a_run_whose_start_overflows_is_dated_by_the_next_time_it_has(root, local_zone):
    local_zone("Europe/Berlin")
    _report, pid, _bare = recorded(root)
    put(root, "a.py", "x")
    earlier(root, pid, "20261024T101010", ["a.py"], started="9999-12-31T23:59:59Z")
    got = listing(root, pid)
    assert utc(got["groups"][0]["runs"][0]["date"]) == datetime(2026, 10, 24, 10, 10, 10, tzinfo=timezone.utc)   # its `created`


def test_a_row_that_cannot_be_made_costs_only_itself(root, monkeypatch):
    put(root, "tables/a.csv", "n\n1\n")
    put(root, "research/variables/distance.toml", 'name="Distance"\nmeaning="Flight distance"\nunit="m"')
    recorded(root, [{"kind": "table", "path": "tables/a.csv"}, {"kind": "variable", "path": "research/variables/distance.toml"}])
    assert sorted(r["path"] for r in outputs.catalog(root)["items"]) == ["research/variables/distance.toml", "tables/a.csv"]
    def broken(_root, rel):
        raise workspace.WorkspaceError("invalid", f"cannot read {rel}")
    monkeypatch.setattr(outputs, "variable_content", broken)
    got = outputs.catalog(root)
    assert got["error"] is None and [r["path"] for r in got["items"]] == ["tables/a.csv"]


def test_a_registration_whose_file_is_gone_stays_in_the_row_counts_for_nothing_and_stands_again_with_its_file(root):
    put(root, "tables/summary.csv", "n\n2\n")
    put(root, "figures/fit.png", "png")
    report, pid, run_id = recorded(root, [{"kind": "table", "path": "tables/summary.csv"}])
    person(root, report, {"kind": "figure", "path": "figures/fit.png"})
    created_by(pid, run_id, "tables/summary.csv", "figures/fit.png")
    tasks, _revision = workspace.read_tasks(root)
    row = lambda: outputs.task_outputs(root, pid, tasks)["t1"]                      # noqa: E731
    states = lambda: {e["path"]: e["state"] for e in row()["entries"]}               # noqa: E731
    assert states() == {"tables/summary.csv": "current", "figures/fit.png": "current"}
    assert row()["filing"]["counts"]["output"] == 2
    (root / "figures/fit.png").unlink()
    assert states() == {"tables/summary.csv": "current", "figures/fit.png": "gone"}   # still listed, to be removed
    counts = row()["filing"]["counts"]
    assert counts["output"] == 1 and counts["unregistered"] == 0                     # it is no registered file, and no new one: not there
    assert [r["path"] for r in outputs.catalog(root)["items"]] == ["tables/summary.csv"]    # the views leave it out as before
    filed = outputs.filing(root, ["figures/fit.png"], records=[], reports=[report], work_folder=None)
    assert filed["counts"]["output"] == 0 and filed["unregistered"] == ["figures/fit.png"]
    put(root, "figures/fit.png", "png")                                              # the file is back, no run made it new: so is the registration
    assert states()["figures/fit.png"] == "current" and row()["filing"]["counts"]["output"] == 2
    (root / "figures/fit.png").unlink()
    outputs.unregister_file(root, {"report": report, "path": "figures/fit.png"})     # a gone file's registration can be taken away
    assert states() == {"tables/summary.csv": "current"}


def aged(root, report, at):
    """Say when the person's entries of a catalog were made, for a test that cannot wait for the clock: what an entry
    covers follows from it (DESIGN.md 19.6)."""
    full = root / outputs.catalog_path(report)
    value = json.loads(full.read_text())
    for item in value["items"]:
        if item.get("evidence") == "person":
            item["at"] = at
    full.write_text(json.dumps(value))


def test_a_file_made_again_where_a_registration_lost_its_file_is_new_in_the_filing_the_row_and_the_listing(root):
    """DESIGN.md 19.6: a registration whose file is gone does not count as registered for a file that later appears at
    that path. A run creates only what was not there when it began, so an entry made before the run began (an agent's:
    the time its run id carries; a person's: `at`) registered another file, and the new one is not registered."""
    put(root, "tables/summary.csv", "n\n1\n")
    put(root, "figures/fit.png", "png")
    report, pid, _bare = recorded(root, [{"kind": "table", "path": "tables/summary.csv"}])        # an agent's entry
    person(root, report, {"kind": "figure", "path": "figures/fit.png"})                           # a person's, made now
    for rel in ("tables/summary.csv", "figures/fit.png"):
        (root / rel).unlink()
    # a run that began after both registrations (a time later than this test will ever run, so that it is the task's latest run)
    second = earlier(root, pid, "20991101T080000", ["tables/summary.csv", "figures/fit.png"])
    tasks, _revision = workspace.read_tasks(root)
    row = lambda: outputs.task_outputs(root, pid, tasks)["t1"]                                    # noqa: E731
    counts = lambda: (row()["filing"]["counts"]["output"], row()["filing"]["counts"]["unregistered"])   # noqa: E731
    new = lambda: [path for _task, _run, path, _kind in flat(listing(root, pid, second))]         # noqa: E731
    assert counts() == (0, 0) and new() == []                                                     # gone: no registered file, no new one
    for rel in ("tables/summary.csv", "figures/fit.png"):
        put(root, rel, "again")                                                                   # the run made them again
    assert counts() == (0, 2)                                                                     # new files: not registered
    assert new() == ["tables/summary.csv", "figures/fit.png"] == [p for _t, _r, p, _k in flat(listing(root, pid))]
    assert len(new()) == counts()[1]                                                              # the row's N and the list agree
    run = runs.load_run(pid, second)
    kept = dict(records=[], reports=[report], work_folder=None)
    assert outputs.filing(root, run["changes"]["created"], run=run, **kept)["counts"]["output"] == 0   # the run's own end
    assert outputs.filing(root, run["changes"]["created"], **kept)["counts"]["output"] == 2              # no run: the file alone
    person(root, report, {"kind": "table", "path": "tables/summary.csv"}, {"kind": "figure", "path": "figures/fit.png"})
    assert counts() == (0, 2)                                                                     # made now, still before that run began
    aged(root, report, "2099-11-02T00:00:00Z")                                                    # registered once the run had begun
    assert counts() == (2, 0) and new() == []
    aged(root, report, "not a time")                                                              # no time to judge by: the file alone
    assert counts() == (2, 0) and new() == []


def test_an_entry_is_judged_to_the_second_and_a_runs_own_declaration_always_covers_what_it_created(root):
    put(root, "tables/summary.csv", "n\n1\n")
    put(root, "figures/fit.png", "png")
    report, pid, run_id = recorded(root, [{"kind": "table", "path": "tables/summary.csv"}])
    person(root, report, {"kind": "figure", "path": "figures/fit.png"})
    created_by(pid, run_id, "tables/summary.csv", "figures/fit.png")
    tasks, _revision = workspace.read_tasks(root)
    counts = lambda: tuple(outputs.task_outputs(root, pid, tasks)["t1"]["filing"]["counts"][k] for k in ("output", "unregistered"))   # noqa: E731
    aged(root, report, "2026-11-01T08:00:00Z")
    runs.update_run(pid, run_id, started="2026-11-01T08:00:00.900000Z")              # entries are stamped to the second: not before it
    assert counts() == (2, 0)
    runs.update_run(pid, run_id, started="2026-11-01T08:00:01.000000Z")              # a second later: the person's entry is from before
    assert counts() == (1, 1)                                                        # the run's own declaration covers whatever its id carries
    runs.update_run(pid, run_id, started=None, created="2026-11-01T08:00:01.000000Z")   # `created` when it never started
    assert counts() == (1, 1)
    aged(root, report, "2099-01-01T00:00:00Z")                                       # an entry later than any day this test runs
    runs.update_run(pid, run_id, started=None, created=None)                         # and the id's time when it has neither: now
    assert counts() == (2, 0)


def test_the_listing_asks_whether_the_registered_file_is_there(root, monkeypatch):
    """A path registered under a spelling whose file is gone is offered when a file is at the path (the way to have both:
    another spelling of it on a case-sensitive volume), so the listing asks `_there` of what it finds registered."""
    report, pid, _bare = recorded(root)
    put(root, "tables/a.csv", "n\n1\n")
    person(root, report, {"kind": "table", "path": "tables/a.csv"})
    earlier(root, pid, "20261024T101010", ["tables/a.csv"])
    assert flat(listing(root, pid)) == []
    monkeypatch.setattr(outputs, "_there", lambda _root, _rel: False)
    assert [path for _task, _run_id, path, _kind in flat(listing(root, pid))] == ["tables/a.csv"]


def test_the_rows_count_and_the_run_filtered_listing_agree_on_what_is_registered_in_any_catalog(root):
    report, pid, run_id = recorded(root)
    second = second_record(root)
    for rel in ("mine.py", "theirs.py", "tool.py", "scratch/x.py"):
        put(root, rel, "x")
    created_by(pid, run_id, "mine.py", "theirs.py", "tool.py", "scratch/x.py", "research/tasks.json")
    runs.update_run(pid, run_id, tool_paths=["tool.py"], work_folder="scratch")
    tasks, _revision = workspace.read_tasks(root)
    unregistered = lambda: outputs.task_outputs(root, pid, tasks)["t1"]["filing"]["counts"]["unregistered"]   # noqa: E731
    assert unregistered() == 2 == len(flat(listing(root, pid, run_id)))               # mine.py, theirs.py
    outputs.register_files(root, {"report": second, "items": [{"kind": "code", "path": "theirs.py"}]})        # for another task
    assert unregistered() == 1
    assert [p for _t, _r, p, _k in flat(listing(root, pid, run_id))] == ["mine.py"]                            # the same one file
    person(root, report, {"kind": "code", "path": "mine.py"})
    assert unregistered() == 0 and flat(listing(root, pid, run_id)) == []


def test_a_row_is_worked_out_once_per_change_of_the_files_and_not_once_per_read(root, monkeypatch):
    report, pid, run_id = recorded(root)
    os.utime(runs.project_runs_dir(pid), ns=(10**18, 10**18))                       # long ago, so that a new run is a change
    tasks, _revision = workspace.read_tasks(root)
    worked = []
    real = outputs._task_outputs
    monkeypatch.setattr(outputs, "_task_outputs", lambda root_, task, *more: worked.append(task["id"]) or real(root_, task, *more))
    memo: dict = {}
    first = outputs.task_outputs(root, pid, tasks, memo=memo, token=1)
    assert outputs.task_outputs(root, pid, tasks, memo=memo, token=1) is first and worked == ["t1"]    # nothing moved: once
    outputs.task_outputs(root, pid, tasks, memo=memo, token=2)
    assert len(worked) == 2                                                          # the watcher's generation moved
    outputs.task_outputs(root, pid, tasks, memo=memo, token=2)
    assert len(worked) == 2
    put(root, "tables/a.csv", "n\n1\n")
    person(root, report, {"kind": "table", "path": "tables/a.csv"})                  # a catalog changed, whatever the token says
    again = outputs.task_outputs(root, pid, tasks, memo=memo, token=2)
    assert len(worked) == 3 and [e["path"] for e in again["t1"]["entries"]] == ["tables/a.csv"]
    _run(root, pid)                                                                  # a run started
    outputs.task_outputs(root, pid, tasks, memo=memo, token=2)
    assert len(worked) == 4
    outputs.task_outputs(root, pid, [{**t, "report": ""} if t["id"] == "t1" else t for t in tasks], memo=memo, token=2)
    assert len(worked) == 4                                                          # no task with a record left: nothing to work out
    outputs.task_outputs(root, pid, tasks, memo=memo, token=2)
    assert len(worked) == 5                                                          # the tasks asked about differ from the kept answer's
    for kept in ({"memo": memo}, {"token": 2}, {}):                                  # never kept without both a memo and a token
        outputs.task_outputs(root, pid, tasks, **kept)
    assert len(worked) == 8


def test_the_work_folder_count_looks_at_a_bounded_number_of_entries_and_stats_none(root, monkeypatch):
    report, pid, run_id = recorded(root)
    for i in range(20):
        (root / f"work/w/empty{i:02d}").mkdir(parents=True)                          # nothing but folders
    for i in range(3):
        put(root, f"work/w/f{i}.txt", "x")
    runs.update_run(pid, run_id, work_folder="work/w")
    tasks, _revision = workspace.read_tasks(root)
    assert outputs.WORK_VISIT_FACTOR * outputs.WORK_COUNT_LIMIT >= 20000 and outputs.task_outputs(root, pid, tasks)["t1"]["work_files"] == 3
    monkeypatch.setattr(outputs, "WORK_COUNT_LIMIT", 2)                              # looks at 2 * 4 entries, whatever they are
    row = outputs.task_outputs(root, pid, tasks)["t1"]
    assert row["work_files_more"] is True and row["work_files"] <= 2

    real = os.scandir

    class Seen:
        def __init__(self, entry):
            self.entry, self.name, self.path = entry, entry.name, entry.path
        def is_dir(self, **how):
            return self.entry.is_dir(**how)
        def is_file(self, **how):
            return self.entry.is_file(**how)
        def stat(self, **how):
            pytest.fail("the count of a folder's files must not stat what it counts")

    @contextlib.contextmanager
    def scandir(path):
        with real(path) as entries:
            yield [Seen(e) for e in entries]

    monkeypatch.setattr(outputs, "WORK_COUNT_LIMIT", 5000)
    monkeypatch.setattr(outputs.os, "scandir", scandir)       # (the runs' folder is listed with the same call: the count alone)
    assert outputs._count_files(root, "work/w") == (3, False)


def refused_in(root, report, *items):
    with pytest.raises(workspace.WorkspaceError) as caught:
        person(root, report, *items)
    return caught.value


GENERIC_ZH = "输出目录或文件不符合设置，请检查后重试"


@pytest.mark.parametrize("rel, kind, content", [
    ("refs/empty.bib", "literature", "% no entries in here\n"),
    ("refs/broken.bib", "literature", "@article{x, title = \n"),
    ("vars/no_definition.toml", "variable", "not = a variable\n"),
    ("vars/decided.toml", "variable", 'name = "Distance"\nmeaning = "x"\nunit = "m"\n[decision]\ndecided_by = "li"\n'),
    ("scratch/run.log", "figure", "text"),                       # a format the kind does not take
])
def test_a_persons_refusal_of_a_file_names_it_in_both_languages(root, rel, kind, content):
    report, _pid, _run_id = recorded(root)
    put(root, rel, content)
    caught = refused_in(root, report, {"kind": kind, "path": rel})
    assert rel in str(caught) and f"“{rel}”" in caught.message_zh and caught.message_zh != GENERIC_ZH
    assert not re.search(r"[㐀-鿿]", str(caught)) and re.search(r"[㐀-鿿]", caught.message_zh)
    assert catalog_bytes(root, report) is None


def test_a_file_gone_or_changed_since_the_listing_is_refused_by_name_in_both_languages(root, monkeypatch):
    report, _pid, _run_id = recorded(root)
    put(root, "scratch/a.log", "text")
    put(root, "scratch/b.log", "text")
    gone = refused_in(root, report, {"kind": "work", "path": "scratch/a.log"}, {"kind": "work", "path": "scratch/gone.log"})
    assert "scratch/gone.log" in str(gone) and "“scratch/gone.log”" in gone.message_zh and gone.message_zh != GENERIC_ZH
    twice = refused_in(root, report, {"kind": "work", "path": "scratch/a.log"}, {"kind": "work", "path": "scratch/a.log"})
    assert "scratch/a.log" in str(twice) and "“scratch/a.log”" in twice.message_zh and twice.message_zh != GENERIC_ZH
    kept = outputs.snapshot.fingerprint
    monkeypatch.setattr(outputs.snapshot, "fingerprint", lambda *_a, **_k: {"dataless": True, "size": 4})      # only in the cloud now
    cloud = refused_in(root, report, {"kind": "work", "path": "scratch/a.log"})
    assert "scratch/a.log" in str(cloud) and "“scratch/a.log”" in cloud.message_zh and cloud.message_zh != GENERIC_ZH
    monkeypatch.setattr(outputs.snapshot, "fingerprint", kept)
    monkeypatch.setattr(outputs, "_current", lambda _item, _mark: False)                                       # changed meanwhile
    changed = refused_in(root, report, {"kind": "work", "path": "scratch/b.log"})
    assert "scratch/b.log" in str(changed) and "“scratch/b.log”" in changed.message_zh and changed.message_zh != GENERIC_ZH
    assert catalog_bytes(root, report) is None


def test_the_before_picture_is_read_only_when_there_are_outputs_to_flag(root, monkeypatch):
    seen = []
    real = outputs.made_by_run
    monkeypatch.setattr(outputs, "made_by_run", lambda *args: seen.append(args[2]) or real(*args))
    answer, _pid, _run_id = complete(root, [])
    assert answer.startswith("Saved") and seen == []                                 # nothing declared: nothing to flag
    put(root, "tables/a.csv", "n\n1\n")
    answer, _pid, _run_id = complete(root, [{"kind": "table", "path": "tables/a.csv"}])
    assert answer.startswith("Saved") and seen == [["tables/a.csv"]]
