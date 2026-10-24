"""Declared task products use configured folders and become readable app objects."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from researchos import bibliography, db, literature, mcp_server, outputs, paths, project, workspace
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
    assert {i["kind"] for i in entries} == set(outputs.ROLES)
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
    for runner in ("claude-code","codex","opencode","deepseek-harness"):
        for lang in ("zh","en"):
            text = brief.rules(lang=lang,runner=runner,prot=prot,folders=outputs.folders(root)["folders"])
            assert "outputs" in text and "`figures/`" in text and "decided_by" in text
            assert "attempt table (`research/folders.json`)" not in text


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
