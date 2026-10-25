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
