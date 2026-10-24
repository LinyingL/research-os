"""Output location settings use the existing HTTP write and project guards."""
import dataclasses
import json

from researchos import outputs, workspace
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
