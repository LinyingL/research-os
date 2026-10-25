"""Output location settings use the existing HTTP write and project guards."""
import dataclasses
import json
import re

import pytest

from researchos import outputs, workspace
from researchos.agents import runs
from test_outputs import complete, put
from test_webapp_server import _get, _post, _request_with_headers
from test_workspace_api import writing_server  # noqa: F401


def test_get_folder_settings_is_read_only_and_save_uses_project_and_revision(writing_server):
    base, root, _httpd = writing_server
    status, info = _get(base,"/api/outputs/folders")
    assert status == 200 and info["revision"] is None
    assert not (root / "research").exists()
    body = {"project":str(root),"revision":None,"folders":info["folders"]}
    assert _post(base,"/api/outputs/folders",{**body,"project":"/another/project"})[0] == 409
    status, saved = _post(base,"/api/outputs/folders",body)
    assert status == 200 and saved["saved"]
    assert _post(base,"/api/outputs/folders",body)[0] == 409


def test_folder_save_respects_origin_and_project_readonly(writing_server):
    base, root, httpd = writing_server
    info = _get(base,"/api/outputs/folders")[1]
    body = {"project":str(root),"revision":None,"folders":info["folders"]}
    status, _result = _request_with_headers(base,"POST","/api/outputs/folders",
        {"Origin":"https://outside.example","Content-Type":"application/json"},json.dumps(body).encode())
    assert status == 403
    httpd.set_served(dataclasses.replace(httpd.get_served(),read_only=True))
    assert _post(base,"/api/outputs/folders",body)[0] in (403,409)
    assert not (root / outputs.FOLDERS_PATH).exists()


def test_human_import_route_copies_only_a_registered_current_variable_draft(writing_server):
    base, root, _httpd = writing_server
    rel = "research/variables/distance.toml"
    put(root,rel,'name="Distance"\nmeaning="Flight distance"\nunit="m"')
    assert _post(base,"/api/outputs/variable",{"project":str(root),"path":rel,"id":"distance"})[0] == 409
    answer,_pid,_run_id = complete(root,[{"kind":"variable","path":rel}])
    assert answer.startswith("Saved")
    status, saved = _post(base,"/api/outputs/variable",{"project":str(root),"path":rel,"id":"distance"})
    assert status == 200 and saved["id"] == "distance"
    assert _get(base,"/api/variables/card?id=distance")[0] == 200
    assert not (root / ".researchos/variables/distance/log.toml").exists()
    assert _post(base,"/api/outputs/variable",{"project":str(root),"path":rel,"id":"distance"})[0] == 409


def test_completed_outputs_appear_in_the_real_page_endpoints(writing_server):
    base, root, _httpd = writing_server
    put(root,"tables/summary.csv","design,time\nwide,2.3\nnarrow,1.9\n")
    put(root,"literature/pilot.json",'[{"title":"Pilot methods","abstract":"A real field from the local demo export."}]')
    complete(root,[{"kind":"table","path":"tables/summary.csv"},{"kind":"literature","path":"literature/pilot.json"}])
    status, data = _get(base,"/api/results")
    assert status == 200
    assert data["outputs"]["items"][0]["table_preview"][1] == ["wide","2.3"]
    status, data = _get(base,"/api/citations")
    assert status == 200 and data["library"]["items"][0]["abstract"]


# -- V15 step 3: a person registers and unregisters through the same guards (DESIGN 19.5, 19.6) -------------------------


def _recorded(root, declared=()):
    answer, pid, run_id = complete(root, list(declared))
    assert answer.startswith("Saved"), answer
    return workspace.read_tasks(root)[0][0]["report"], pid, run_id


def test_register_and_unregister_are_human_writes_of_the_bound_project(writing_server):
    base, root, _httpd = writing_server
    put(root, "analysis/fit.png", "png")
    report, _pid, _run_id = _recorded(root)
    body = {"project": str(root), "report": report, "items": [{"kind": "figure", "path": "analysis/fit.png", "title": "Fit"}]}
    assert _post(base, "/api/outputs/register", {**body, "project": "/another/project"})[0] == 409
    assert not (root / outputs.catalog_path(report)).exists()
    status, done = _post(base, "/api/outputs/register", body)
    assert status == 200 and done["registered"] == ["analysis/fit.png"] and done["catalog"] == outputs.catalog_path(report)
    assert isinstance(done["generation"], int)
    entry = json.loads((root / outputs.catalog_path(report)).read_text())["items"][0]
    assert (entry["evidence"], entry["kind"], entry["task"], entry["created"]) == ("person", "figure", "t1", False)
    gone = {"project": str(root), "report": report, "path": "analysis/fit.png"}
    assert _post(base, "/api/outputs/unregister", {**gone, "project": "/another/project"})[0] == 409
    assert _post(base, "/api/outputs/unregister", gone)[0] == 200
    assert json.loads((root / outputs.catalog_path(report)).read_text()) == {"version": 1, "items": []}
    assert (root / "analysis/fit.png").read_text() == "png"
    status, refused = _post(base, "/api/outputs/unregister", gone)                    # not registered any more
    assert status == 409 and refused["state"] == "workspace_output"


@pytest.mark.parametrize("item", [
    {"kind": "work", "path": "research/notes/n.md"}, {"kind": "work", "path": ".researchos/x.log"},
    {"kind": "work", "path": "missing.log"}, {"kind": "figure", "path": "scratch/run.log"},
])
def test_a_refused_registration_answers_409_and_writes_nothing(writing_server, item):
    base, root, _httpd = writing_server
    put(root, "scratch/run.log", "text")
    put(root, "research/notes/n.md", "# n")
    put(root, ".researchos/x.log", "text")
    report, _pid, _run_id = _recorded(root)
    status, body = _post(base, "/api/outputs/register", {"project": str(root), "report": report, "items": [item]})
    assert status == 409 and body["state"] == "workspace_output" and body["message"]
    assert not (root / outputs.catalog_path(report)).exists()
    for bad in ({"report": None}, {"report": "research/results/" + "0" * 32 + ".md"}, {"items": "x"}, {}):
        assert _post(base, "/api/outputs/register", {"project": str(root), "report": report, "items": [item], **bad})[0] == 409
    assert _post(base, "/api/outputs/unregister", {"project": str(root), "report": None, "path": "x"})[0] == 409


def test_register_and_unregister_respect_origin_and_read_only_and_a_member_outside_their_copy(writing_server):
    base, root, httpd = writing_server
    put(root, "scratch/run.log", "text")
    report, _pid, _run_id = _recorded(root)
    body = {"project": str(root), "report": report, "items": [{"kind": "work", "path": "scratch/run.log"}]}
    for path in ("/api/outputs/register", "/api/outputs/unregister"):
        status, _result = _request_with_headers(base, "POST", path, {"Origin": "https://outside.example", "Content-Type": "application/json"},
                                                json.dumps(body).encode())
        assert status == 403
    assert not (root / outputs.catalog_path(report)).exists()
    assert _post(base, "/api/outputs/register", body)[0] == 200
    before = (root / outputs.catalog_path(report)).read_bytes()
    httpd.set_served(dataclasses.replace(httpd.get_served(), read_only=True))
    assert _post(base, "/api/outputs/register", {**body, "items": [{"kind": "work", "path": "scratch/run.log", "title": "Again"}]})[0] in (403, 409)
    assert _post(base, "/api/outputs/unregister", {"project": str(root), "report": report, "path": "scratch/run.log"})[0] in (403, 409)
    assert (root / outputs.catalog_path(report)).read_bytes() == before


def test_a_member_may_register_only_in_their_recognized_working_copy(writing_server, monkeypatch):
    """The write rule is the other saves': `workspace._require_write` -- a member whose working copy the base does not
    name is refused, the PI and the single person are not."""
    base, root, _httpd = writing_server
    put(root, "scratch/run.log", "text")
    report, _pid, _run_id = _recorded(root)
    body = {"project": str(root), "report": report, "items": [{"kind": "work", "path": "scratch/run.log"}]}
    monkeypatch.setattr(workspace.proposals, "role_of", lambda _root: ("member", None, None))
    monkeypatch.setattr(workspace.proposals, "read_base", lambda _pid: None)
    status, refused = _post(base, "/api/outputs/register", body)
    assert status in (403, 409) and not (root / outputs.catalog_path(report)).exists()
    monkeypatch.setattr(workspace.proposals, "role_of", lambda _root: ("pi", None, None))
    assert _post(base, "/api/outputs/register", body)[0] == 200


def test_the_workspace_payload_gives_each_task_with_a_record_its_files_counts_and_work_folder(writing_server):
    base, root, _httpd = writing_server
    put(root, "tables/summary.csv", "n\n2\n")
    report, pid, run_id = _recorded(root, [{"kind": "table", "path": "tables/summary.csv"}])
    put(root, "work/20261025-clean/a.py", "1")
    put(root, "loose.py", "2")
    runs.update_run(pid, run_id, work_folder="work/20261025-clean",
                    changes={"created": ["tables/summary.csv", "work/20261025-clean/a.py", "loose.py"], "modified": [], "deleted": []})
    status, data = _get(base, "/api/workspace")
    assert status == 200 and list(data["task_outputs"]) == ["t1"]
    row = data["task_outputs"]["t1"]
    assert row["entries"] == [{"kind": "table", "path": "tables/summary.csv", "title": "summary.csv", "state": "current",
                               "evidence": "agent_declaration"}]
    assert row["filing"]["counts"] == {"record": 0, "output": 1, "work": 1, "cache": 0, "unregistered": 1}
    assert (row["work_folder"], row["work_files"], row["work_files_more"], row["error"]) == ("work/20261025-clean", 1, False, False)


def test_the_views_rows_name_their_task_and_time_over_http(writing_server):
    base, root, _httpd = writing_server
    put(root, "tables/summary.csv", "n\n2\n")
    put(root, "scratch/run.log", "text")
    report, _pid, _run_id = _recorded(root, [{"kind": "table", "path": "tables/summary.csv"}])
    assert _post(base, "/api/outputs/register", {"project": str(root), "report": report,
                                                 "items": [{"kind": "table", "path": "scratch/run.log"}]})[0] == 409   # .log is no table
    put(root, "tables/two.csv", "n\n3\n")
    assert _post(base, "/api/outputs/register", {"project": str(root), "report": report,
                                                 "items": [{"kind": "table", "path": "tables/two.csv"}]})[0] == 200
    rows = _get(base, "/api/results")[1]["outputs"]["items"]
    assert {r["path"] for r in rows} == {"tables/summary.csv", "tables/two.csv"}
    assert all(r["task_title"] == "Clean the survey data" and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d", r["at"]) for r in rows)


# -- V15 step 4: the listing of earlier runs' files (DESIGN 19.5, 19.10 item 4) ------------------------------------------


def _earlier_project(root):
    """Two tasks with result records and a finished run of each that created files; (report1, report2, pid, run1, run2)."""
    from test_outputs import earlier, second_record

    report, pid, _bare = _recorded(root)
    second = second_record(root)
    for rel in ("scripts/fetch.py", "data/prices.csv", "reports/plan.md", "checks/run_stdout.txt"):
        put(root, rel, "x")
    old = earlier(root, pid, "20261024T101010", ["scripts/fetch.py", "data/prices.csv", "checks/run_stdout.txt"])
    new = earlier(root, pid, "20261025T090000", ["reports/plan.md"], task_id="t2")
    return report, second, pid, old, new


def test_the_backfill_route_lists_by_task_and_run_with_kinds_and_writes_nothing(writing_server):
    base, root, httpd = writing_server
    report, second, pid, old, new = _earlier_project(root)
    served = httpd.get_served()
    before = sorted(str(p) for p in root.rglob("*"))
    status, got = _get(base, f"/api/outputs/backfill?project={pid}")
    assert status == 200 and served.project_id == pid
    assert [(g["task"], g["report"]) for g in got["groups"]] == [("t2", second), ("t1", report)]
    assert [(r["id"], [(f["path"], f["kind"]) for f in r["files"]]) for g in got["groups"] for r in g["runs"]] == [
        (new, [("reports/plan.md", "document")]),
        (old, [("scripts/fetch.py", "code"), ("data/prices.csv", "data"), ("checks/run_stdout.txt", "work")])]
    assert (got["listed"], got["more"]) == (4, 0)
    assert sorted(str(p) for p in root.rglob("*")) == before                                  # a read: no catalog, nothing
    assert _get(base, "/api/outputs/backfill")[1] == got                                       # the open project when none is named
    assert _get(base, f"/api/outputs/backfill?project={root}")[1] == got                       # its path names it too
    only = _get(base, f"/api/outputs/backfill?project={pid}&run={old}")[1]
    assert [r["id"] for g in only["groups"] for r in g["runs"]] == [old] and only["listed"] == 3
    assert _get(base, f"/api/outputs/backfill?project={pid}&run=r-20260101T000000Z-aaaaaa")[1]["groups"] == []


def test_the_backfill_route_refuses_a_window_of_another_project_and_still_reads_a_read_only_one(writing_server):
    base, root, httpd = writing_server
    _earlier_project(root)
    status, refused = _get(base, "/api/outputs/backfill?project=p-00000000")
    assert status == 409 and refused["state"] == "workspace_project_changed"
    assert _get(base, "/api/outputs/backfill?project=/another/project")[0] == 409
    httpd.set_served(dataclasses.replace(httpd.get_served(), read_only=True))
    status, got = _get(base, "/api/outputs/backfill")
    assert status == 200 and got["listed"] == 4                                                # a listing is a read, not a write


def test_what_the_route_lists_is_registered_one_call_per_task_and_leaves_the_listing_empty(writing_server):
    base, root, _httpd = writing_server
    report, second, pid, _old, _new = _earlier_project(root)
    got = _get(base, f"/api/outputs/backfill?project={pid}")[1]
    for group in got["groups"]:
        items = [{"kind": f["kind"], "path": f["path"]} for r in group["runs"] for f in r["files"]]
        status, done = _post(base, "/api/outputs/register", {"project": str(root), "report": group["report"], "items": items})
        assert status == 200 and done["registered"] == [i["path"] for i in items]
    entries = {e["path"]: e for r in (report, second) for e in json.loads((root / outputs.catalog_path(r)).read_text())["items"]}
    assert {p: (e["evidence"], e["created"]) for p, e in entries.items()} == {
        "scripts/fetch.py": ("person", True), "data/prices.csv": ("person", True),
        "checks/run_stdout.txt": ("person", True), "reports/plan.md": ("person", True)}
    assert _get(base, f"/api/outputs/backfill?project={pid}")[1] == {"groups": [], "listed": 0, "more": 0}


def test_the_register_route_takes_hundreds_of_a_tasks_files_in_one_call_and_says_when_the_catalog_cannot_hold_more(writing_server):
    base, root, _httpd = writing_server
    report, _pid, _run_id = _recorded(root)
    for i in range(1100):
        put(root, f"gen/f{i:04d}.py", f"x = {i}\n")
    def mine(lo, hi):
        return [{"kind": "code", "path": f"gen/f{i:04d}.py"} for i in range(lo, hi)]

    status, done = _post(base, "/api/outputs/register", {"project": str(root), "report": report, "items": mine(0, 250)})
    assert status == 200 and len(done["registered"]) == 250                                  # the review list's one call for a task
    held = (root / outputs.catalog_path(report)).read_bytes()
    for items in (mine(250, 1100), mine(250, 1000)):                                         # more than a catalog's count; more than it can hold
        status, refused = _post(base, "/api/outputs/register", {"project": str(root), "report": report, "items": items})
        assert status == 409 and refused["state"] == "workspace_output"
        assert refused["message"].startswith("这项任务的登记清单装不下这么多文件。") and "cannot hold that many files" in refused["detail"]
    assert (root / outputs.catalog_path(report)).read_bytes() == held                        # nothing of either call was written


# -- V15 step 4b (DESIGN 19.5, 19.6) -------------------------------------------------------------------------------------


def test_the_workspace_payload_works_a_tasks_row_out_once_per_change_and_not_once_per_poll(writing_server, monkeypatch):
    base, root, httpd = writing_server
    report, _pid, _run_id = _recorded(root)
    worked = []
    real = outputs._task_outputs
    monkeypatch.setattr(outputs, "_task_outputs", lambda *args: worked.append(args[1]["id"]) or real(*args))
    first = _get(base, "/api/workspace")[1]["task_outputs"]
    again = _get(base, "/api/workspace")[1]["task_outputs"]
    assert again == first and list(first) == ["t1"] and worked == ["t1"]                            # a poll: no new work
    put(root, "analysis/fit.png", "png")
    body = {"project": str(root), "report": report, "items": [{"kind": "figure", "path": "analysis/fit.png", "title": "Fit"}]}
    assert _post(base, "/api/outputs/register", body)[0] == 200                                    # the page's own write moves the generation
    after = _get(base, "/api/workspace")[1]["task_outputs"]["t1"]
    assert worked == ["t1", "t1"] and [e["path"] for e in after["entries"]] == ["analysis/fit.png"]
    _get(base, "/api/workspace")
    assert len(worked) == 2
    httpd.watcher.bump_generation()                                                                 # a run that ended, a scan that finished
    _get(base, "/api/workspace")
    _get(base, "/api/workspace")
    assert len(worked) == 3
    (root / "analysis/fit.png").unlink()
    httpd.watcher.bump_generation()
    gone = _get(base, "/api/workspace")[1]["task_outputs"]["t1"]
    assert [(e["path"], e["state"]) for e in gone["entries"]] == [("analysis/fit.png", "gone")] and len(worked) == 4


def test_a_refused_registration_names_the_file_in_the_language_of_the_request(writing_server):
    base, root, _httpd = writing_server
    put(root, "refs/empty.bib", "% no entries in here\n")
    report, _pid, _run_id = _recorded(root)
    body = {"project": str(root), "report": report, "items": [{"kind": "literature", "path": "refs/empty.bib"}]}
    status, refused = _post(base, "/api/outputs/register", body)
    assert status == 409 and refused["state"] == "workspace_output"
    assert "“refs/empty.bib”" in refused["message"] and "refs/empty.bib" in refused["detail"]
    assert refused["message"] != "输出目录或文件不符合设置，请检查后重试"
    (root / "refs/empty.bib").unlink()                                                              # gone since the listing
    status, refused = _post(base, "/api/outputs/register", body)
    assert status == 409 and "“refs/empty.bib”" in refused["message"] and "refs/empty.bib" in refused["detail"]
    assert not (root / outputs.catalog_path(report)).exists()
