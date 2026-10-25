"""DESIGN.md 18.1 item 1 (task V14, G1-1): the engine requires its session for
every write. A program on this Mac that POSTs with the right `Host` and no
`Origin` (what a script an AI agent writes can do) is refused and changes
nothing; the page, which carries the session cookie that `GET /boot` set,
saves as before; a window opened without it reads, and its first refused
write says so in words. The session file `researchos serve` keeps (mode 0600)
and the audit of every GET route (18.1: a GET that writes or starts
something is moved under the same rule) are pinned here too. Every test
names its scenario."""

from __future__ import annotations

import ast
import http.client
import inspect
import json
import os
import re
import shutil
import socket
import socketserver
import stat
import subprocess
import textwrap
import threading
import types
from pathlib import Path
from typing import Any

import pytest

from researchos import cli, paths, project, settings
from researchos.agents import profiles
from researchos.agents import runs as run_store
from researchos.records.identity import read_identity
from researchos.webapp import server

from test_agents_manager import stale_run
from test_v8_phase2 import _git, _project
from test_webapp_server import _init_project

WEBAPP = Path(server.__file__).parent
APP_HTML = WEBAPP / "app.html"
I18N_JS = WEBAPP / "i18n.js"
ROUTEMAP_JS = WEBAPP / "routemap.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

SECRET = "ab" * 32
#: 18.1's two sentences, word for word: the sentence of the design, then the one for a window that is the app
#: already (an app built before the session existed loads the plain address; only `researchos app` makes a new one).
SENTENCE_ZH = ("这个窗口没有连上 Research OS 的写入权限：请从 Research OS 应用打开它。"
               "如果你已经在应用里，说明应用是旧版本：请运行 researchos app 重新生成。")
SENTENCE_EN = ("This window cannot save: open it from the Research OS app. "
               "If you already did, the app is out of date: run researchos app to rebuild it.")


# -- a running engine that asks for its session ------------------------------------------------------------------


class Engine:
    def __init__(self, httpd: server.RceHTTPServer) -> None:
        self.httpd = httpd
        self.port = httpd.server_address[1]
        self.cookie = f"ro_session_{self.port}"

    def call(self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None
             ) -> tuple[int, dict[str, str], bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            data = None if body is None else json.dumps(body).encode("utf-8")
            conn.request(method, path, body=data, headers={"Content-Type": "application/json", **(headers or {})})
            resp = conn.getresponse()
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
        finally:
            conn.close()

    def post(self, path: str, body: Any = None, headers: dict[str, str] | None = None) -> tuple[int, Any]:
        status, _h, raw = self.call("POST", path, {} if body is None else body, headers)
        return status, json.loads(raw)

    def get(self, path: str, headers: dict[str, str] | None = None) -> tuple[int, Any]:
        status, _h, raw = self.call("GET", path, None, headers)
        return status, json.loads(raw)


def _running(root: Path | None, *, session: str | None = SECRET):
    httpd = server.build_server(root, 0, session=session)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _stop(httpd, thread) -> None:
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


@pytest.fixture
def engine(tmp_path):
    root = tmp_path / "proj"
    _init_project(root)
    httpd, thread = _running(root)
    try:
        yield Engine(httpd), root
    finally:
        _stop(httpd, thread)


@pytest.fixture
def engine_without_project():
    httpd, thread = _running(None)
    try:
        yield Engine(httpd)
    finally:
        _stop(httpd, thread)


def _saved_language() -> str | None:
    return settings.read_settings().get("interface_language")


# -- 18.5 #1: a write without the session is refused and changes nothing ----------------------------------------


def test_a_post_without_the_session_gets_403_and_changes_nothing(engine):
    """A program started by an agent run: the right Host (http.client's own), no Origin, no session. A setting
    and a project file are both refused; neither is written."""
    eng, root = engine
    status, body = eng.post("/api/settings/language", {"language": "en"})
    assert status == 403 and body["state"] == body["code"] == "session_required"
    assert body["message"] == SENTENCE_ZH and "ro_session_" in body["detail"] and body["error"] == body["detail"]
    assert _saved_language() is None
    status, body = eng.post("/api/workspace/document", {
        "project": str(root), "path": "research/overview.md", "revision": None, "content": "# Planted\n"})
    assert status == 403 and body["state"] == "session_required"
    assert not (root / "research").exists()


def test_every_post_route_is_refused_without_the_session_before_it_is_routed(engine):
    """The check runs before routing: even an endpoint that does not exist answers 403 session_required, so
    the answer never tells a program which writes there are."""
    eng, _root = engine
    for path in ("/api/shutdown", "/api/open", "/api/agents/runs", "/api/projects/switch", "/api/outputs/folders", "/api/outputs/variable", "/api/outputs/register", "/api/outputs/unregister", "/api/nope", "/"):
        status, body = eng.post(path, {})
        assert (status, body["state"]) == (403, "session_required"), path


def test_the_header_carries_the_session(engine):
    eng, _root = engine
    status, body = eng.post("/api/settings/language", {"language": "en"}, {"X-ResearchOS-Session": SECRET})
    assert status == 200 and body == {"ok": True, "language": "en"}
    assert _saved_language() == "en"


def test_the_cookie_named_for_the_port_carries_the_session(engine):
    eng, _root = engine
    for cookie in (f"{eng.cookie}={SECRET}", f"theme=dark; {eng.cookie}={SECRET}; other=1", f'{eng.cookie}="{SECRET}"'):
        status, body = eng.post("/api/settings/language", {"language": "en"}, {"Cookie": cookie})
        assert status == 200, cookie


@pytest.mark.parametrize("headers", [
    {"X-ResearchOS-Session": "ab" * 31 + "ac"},          # a wrong secret, one character off
    {"X-ResearchOS-Session": ""},                         # an empty header
    {"X-ResearchOS-Session": SECRET[:-1]},                # a prefix of the secret
    {"X-ResearchOS-Session": SECRET + "0"},               # the secret and more
    {"Cookie": "ro_session_1=" + SECRET},                 # the right secret in another port's cookie
    {"Cookie": "ro_session_PORT=wrong"},                  # the right cookie name with a wrong value
    {"Cookie": "session=" + SECRET},                      # any other cookie name
    {"Cookie": "x=ro_session_PORT=" + SECRET},            # the cookie name as part of another cookie's value
    {"X-ResearchOS-Session": "wrong", "Cookie": "ro_session_PORT=wrong"},
])
def test_a_wrong_or_misplaced_secret_is_refused(engine, headers):
    eng, _root = engine
    headers = {k: v.replace("PORT", str(eng.port)) for k, v in headers.items()}
    status, body = eng.post("/api/settings/language", {"language": "en"}, headers)
    assert (status, body["state"]) == (403, "session_required")
    assert _saved_language() is None


def test_either_one_right_carrier_is_enough(engine):
    """A stale cookie from an earlier engine of the same port beside the header the app sends."""
    eng, _root = engine
    status, _body = eng.post("/api/settings/language", {"language": "en"},
                             {"X-ResearchOS-Session": SECRET, "Cookie": f"{eng.cookie}=old-engine"})
    assert status == 200


def test_the_refusal_speaks_the_requests_language(engine):
    eng, _root = engine
    assert SENTENCE_ZH == server.SESSION_REQUIRED_ZH and SENTENCE_EN == server.SESSION_REQUIRED_EN
    got = {lang: eng.post("/api/settings/language", {"language": "en"}, headers)[1]["message"]
           for lang, headers in (("zh", {"X-ResearchOS-Lang": "zh"}), ("en", {"X-ResearchOS-Lang": "en"}), ("none", {}))}
    assert got == {"zh": SENTENCE_ZH, "en": SENTENCE_EN, "none": SENTENCE_ZH}


def test_the_origin_check_comes_first_then_the_session_then_the_body(engine):
    """After the origin check: a foreign Origin is refused as before, with or without the session. Before the
    body is read: a body that never arrives does not hold the refusal back."""
    eng, _root = engine
    for headers in ({"Origin": "https://evil.example"}, {"Origin": "https://evil.example", "X-ResearchOS-Session": SECRET}):
        status, body = eng.post("/api/settings/language", {"language": "en"}, headers)
        assert status == 403 and "Origin" in body["error"] and "state" not in body, headers
    sock = socket.create_connection(("127.0.0.1", eng.port), timeout=10)
    try:
        sock.sendall((f"POST /api/settings/language HTTP/1.1\r\nHost: 127.0.0.1:{eng.port}\r\n"
                      "Content-Type: application/json\r\nContent-Length: 100000\r\nConnection: close\r\n\r\n").encode())
        raw = b""
        while chunk := sock.recv(65536):
            raw += chunk
    finally:
        sock.close()
    head, _, payload = raw.partition(b"\r\n\r\n")
    assert head.split(b" ")[1] == b"403" and json.loads(payload)["state"] == "session_required"


def test_with_no_project_served_a_write_still_needs_the_session(engine_without_project):
    eng = engine_without_project
    assert eng.post("/api/settings/language", {"language": "en"})[0] == 403
    assert eng.post("/api/settings/language", {"language": "en"}, {"X-ResearchOS-Session": SECRET})[0] == 200


def test_a_server_built_without_a_session_asks_for_none(tmp_path):
    """The tests' servers (every other test file builds one): no session is asked for, and /boot sets no cookie."""
    httpd, thread = _running(None, session=None)
    try:
        eng = Engine(httpd)
        assert eng.post("/api/settings/language", {"language": "en"})[0] == 200
        status, headers, _raw = eng.call("GET", f"/boot?session={SECRET}")
        assert status == 303 and "set-cookie" not in headers
    finally:
        _stop(httpd, thread)


# -- GET /boot --------------------------------------------------------------------------------------------------


def test_boot_with_the_session_sets_the_cookie_and_redirects_home(engine):
    eng, _root = engine
    status, headers, raw = eng.call("GET", f"/boot?session={SECRET}")
    assert status == 303 and headers["location"] == "/" and raw == b""
    attributes = [a.strip() for a in headers["set-cookie"].split(";")]
    assert attributes[0] == f"{eng.cookie}={SECRET}"
    assert set(attributes[1:]) == {"HttpOnly", "SameSite=Strict", "Path=/"}
    # and what the browser sends back from then on is accepted
    status, body = eng.post("/api/settings/language", {"language": "en"}, {"Cookie": attributes[0]})
    assert status == 200 and body["language"] == "en"


@pytest.mark.parametrize("query", ["", "?session=", "?session=wrong", f"?session={SECRET}0", f"?session={SECRET[:-1]}",
                                   "?other=" + SECRET, "?session=%ff%fe"])
def test_boot_with_a_wrong_or_missing_session_redirects_without_the_cookie(engine, query):
    eng, _root = engine
    status, headers, raw = eng.call("GET", "/boot" + query)
    assert status == 303 and headers["location"] == "/" and "set-cookie" not in headers and raw == b""


def test_boot_answers_with_no_project_served(engine_without_project):
    eng = engine_without_project
    status, headers, _raw = eng.call("GET", f"/boot?session={SECRET}")
    assert status == 303 and headers["set-cookie"].startswith(f"{eng.cookie}={SECRET};")


def test_boot_is_origin_checked_like_every_route(engine):
    eng, _root = engine
    status, _headers, raw = eng.call("GET", f"/boot?session={SECRET}", headers={"Origin": "https://evil.example"})
    assert status == 403 and "Origin" in json.loads(raw)["error"]
    status, _headers, raw = eng.call("GET", f"/boot?session={SECRET}", headers={"Host": "evil.example"})
    assert status == 403 and "Host" in json.loads(raw)["error"]


def test_a_window_without_the_session_reads(engine):
    """The page and the data load without the session; only writes ask for it."""
    eng, _root = engine
    status, headers, raw = eng.call("GET", "/")
    assert status == 200 and b"<title>" in raw
    assert eng.get("/api/summary")[0] == 200
    assert eng.get("/api/projects")[0] == 200


def test_the_secret_is_never_logged_by_the_handler(engine, caplog):
    eng, _root = engine
    with caplog.at_level("DEBUG"):
        eng.call("GET", f"/boot?session={SECRET}")
        eng.post("/api/settings/language", {"language": "en"})
    assert SECRET not in caplog.text


def test_the_secret_is_not_logged_when_the_window_goes_away_while_boot_answers(engine, caplog, monkeypatch):
    """`GET /boot?session=...` answered into a connection the other end has dropped: the catch-all of `do_GET` used to
    log the raw path, the secret with it, into a file any program of the user can read."""
    eng, _root = engine

    def dropped(self):
        raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr(server.RceRequestHandler, "end_headers", dropped)
    with caplog.at_level("DEBUG"):
        for target in (f"/boot?session={SECRET}", f"/boot?a=1&b=2&session={SECRET}&c=3"):
            with pytest.raises(http.client.RemoteDisconnected):  # nothing was sent: that is the case
                eng.call("GET", target)
    assert SECRET not in caplog.text
    assert not [r for r in caplog.records if r.levelname in ("ERROR", "CRITICAL")]  # a window that left is no error


def test_the_secret_is_not_logged_when_anything_else_fails_while_handling_a_boot_request(engine, caplog, monkeypatch):
    eng, _root = engine

    def broken(self, query):
        raise RuntimeError("boom")

    monkeypatch.setattr(server.RceRequestHandler, "_boot", broken)
    with caplog.at_level("DEBUG"):
        status, _headers, _raw = eng.call("GET", f"/boot?x=1&session={SECRET}&session={SECRET[::-1]}")
    assert status == 500
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert errors and SECRET not in caplog.text and SECRET[::-1] not in caplog.text
    assert "/boot?x=1&session=<hidden>&session=<hidden>" in errors[0].getMessage()


def test_the_redaction_hides_every_session_value_of_a_boot_target_and_nothing_else():
    hide = server._without_secret
    assert hide("GET /boot?session=abc HTTP/1.1") == "GET /boot?session=<hidden> HTTP/1.1"
    assert hide("/boot?a=1&b=2&session=abc&c=3") == "/boot?a=1&b=2&session=<hidden>&c=3"
    assert hide("/boot?session=a&session=b") == "/boot?session=<hidden>&session=<hidden>"
    assert hide("/boot") == "/boot" and hide("/api/settings?session=keep") == "/api/settings?session=keep"
    assert hide("two /boot?session=a and /boot?session=b") == "two /boot?session=<hidden> and /boot?session=<hidden>"


# -- the session file and `serve` -------------------------------------------------------------------------------


def _fake_serve_forever(seen: dict[str, Any]):
    def serve_forever(self) -> None:
        path = paths.engine_session_path(self.server_address[1])
        seen.update(port=self.server_address[1], session=self.session, path=path, text=path.read_text(),
                    mode=stat.S_IMODE(path.stat().st_mode), directory_mode=stat.S_IMODE(path.parent.stat().st_mode),
                    listing=sorted(os.listdir(path.parent)))
    return serve_forever


def test_serve_keeps_the_session_in_a_0600_file_until_shutdown_and_opens_the_browser_at_boot(tmp_path, monkeypatch, capsys):
    _init_project(tmp_path)
    seen: dict[str, Any] = {}
    opened: list[str] = []
    monkeypatch.setattr(server.RceHTTPServer, "serve_forever", _fake_serve_forever(seen))
    monkeypatch.setattr(server.webbrowser, "open", lambda url: opened.append(url))

    server.serve(tmp_path, port=0, open_browser=True)

    secret, port = seen["session"], seen["port"]
    assert re.fullmatch(r"[0-9a-f]{64}", secret)                       # 32 random bytes
    assert seen["path"] == paths.researchos_home() / "engine" / f"{port}.session"
    assert seen["text"] == secret + "\n"
    assert seen["mode"] == 0o600 and seen["directory_mode"] == 0o700
    assert seen["listing"] == [f"{port}.session"]                       # no temp file left beside it
    assert not seen["path"].exists()                                    # removed at shutdown
    url = f"http://127.0.0.1:{port}"
    assert opened == [f"{url}/boot?session={secret}"]
    out = capsys.readouterr()
    assert out.out == f"Research OS app: {url}  (Ctrl+C to stop)\n"
    assert secret not in out.out + out.err


def test_serve_without_a_browser_prints_only_the_plain_address(tmp_path, monkeypatch, capsys):
    """`--no-browser` is how the Mac app starts the engine, and its serve.log is readable by any program of the
    user: the secret is in the 0600 file only."""
    _init_project(tmp_path)
    seen: dict[str, Any] = {}
    opened: list[str] = []
    monkeypatch.setattr(server.RceHTTPServer, "serve_forever", _fake_serve_forever(seen))
    monkeypatch.setattr(server.webbrowser, "open", lambda url: opened.append(url))

    assert cli.main(["serve", str(tmp_path), "--port", "0", "--no-browser"]) == 0

    out = capsys.readouterr()
    assert opened == [] and seen["mode"] == 0o600
    assert out.out == f"Research OS app: http://127.0.0.1:{seen['port']}  (Ctrl+C to stop)\n"
    assert seen["session"] not in out.out + out.err
    assert not seen["path"].exists()


def test_serve_asks_for_its_session(tmp_path, monkeypatch):
    """`serve` never builds a server that needs no session."""
    _init_project(tmp_path)
    built: list[server.RceHTTPServer] = []
    monkeypatch.setattr(server.RceHTTPServer, "serve_forever", lambda self: built.append(self))
    server.serve(tmp_path, port=0, open_browser=False)
    assert re.fullmatch(r"[0-9a-f]{64}", built[0].session)


def test_every_engine_has_its_own_secret(tmp_path, monkeypatch):
    _init_project(tmp_path)
    secrets_seen: list[str] = []
    monkeypatch.setattr(server.RceHTTPServer, "serve_forever", lambda self: secrets_seen.append(self.session))
    for _ in range(3):
        server.serve(tmp_path, port=0, open_browser=False)
    assert len(set(secrets_seen)) == 3


def test_the_file_is_removed_when_the_serve_loop_is_interrupted(tmp_path, monkeypatch):
    _init_project(tmp_path)
    seen: dict[str, Any] = {}

    def serve_forever(self) -> None:
        seen["path"] = paths.engine_session_path(self.server_address[1])
        assert seen["path"].exists()
        raise KeyboardInterrupt

    monkeypatch.setattr(server.RceHTTPServer, "serve_forever", serve_forever)
    server.serve(tmp_path, port=0, open_browser=False)
    assert not seen["path"].exists()


def test_the_file_is_gone_before_the_slow_part_of_the_shutdown(tmp_path, monkeypatch):
    """The agents stop in `server_close`; a newer engine of the same port must not find (or delete) this file."""
    _init_project(tmp_path)
    seen: dict[str, Any] = {}
    real_close = server.RceHTTPServer.server_close

    def close(self) -> None:
        seen["exists_at_close"] = paths.engine_session_path(self.server_address[1]).exists()
        real_close(self)

    monkeypatch.setattr(server.RceHTTPServer, "serve_forever", lambda self: None)
    monkeypatch.setattr(server.RceHTTPServer, "server_close", close)
    server.serve(tmp_path, port=0, open_browser=False)
    assert seen == {"exists_at_close": False}


def test_a_file_replaced_by_a_newer_engine_is_left_alone(tmp_path):
    path = server.write_session_file(51234, "one")
    server.write_session_file(51234, "two")                      # the newer engine of that port
    server.remove_session_file(path, "one")                      # the older one shutting down late
    assert path.read_text() == "two\n"
    server.remove_session_file(path, "two")
    assert not path.exists()
    server.remove_session_file(path, "two")                      # already gone: no error


def test_writing_the_file_replaces_a_stale_one_with_mode_0600(tmp_path):
    """A crashed engine's file (here world-readable, as an old tool might have left it) is replaced whole."""
    stale = paths.engine_session_path(51235)
    stale.parent.mkdir(parents=True)
    stale.write_text("dead engine\n")
    stale.chmod(0o644)
    written = server.write_session_file(51235, SECRET)
    assert written == stale and stale.read_text() == SECRET + "\n"
    assert stat.S_IMODE(stale.stat().st_mode) == 0o600
    assert os.listdir(stale.parent) == ["51235.session"]


def test_serve_refuses_to_start_when_the_file_cannot_be_written(tmp_path, monkeypatch):
    """An engine whose session the app cannot read would leave every window read-only: it does not start."""
    _init_project(tmp_path)
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    monkeypatch.setattr(server.paths, "engine_session_path", lambda port: blocker / f"{port}.session")
    started: list[bool] = []
    monkeypatch.setattr(server.RceHTTPServer, "serve_forever", lambda self: started.append(True))
    with pytest.raises(server.SessionFileError, match="session file"):
        server.serve(tmp_path, port=0, open_browser=False)
    assert started == []


def test_a_live_engine_started_by_serve_refuses_a_write_without_the_session(tmp_path, monkeypatch):
    """End to end: `serve`'s own secret, read from its file, is what a header must carry."""
    _init_project(tmp_path)
    seen: dict[str, Any] = {}
    done = threading.Event()

    def serve_forever(self) -> None:
        thread = threading.Thread(target=socketserver.BaseServer.serve_forever, args=(self,), daemon=True)
        thread.start()
        eng = Engine(self)
        seen["without"] = eng.post("/api/settings/language", {"language": "en"})[0]
        secret = paths.engine_session_path(eng.port).read_text().strip()
        seen["with_file_secret"] = eng.post("/api/settings/language", {"language": "en"}, {"X-ResearchOS-Session": secret})[0]
        self.shutdown()
        thread.join(timeout=5)
        done.set()

    monkeypatch.setattr(server.RceHTTPServer, "serve_forever", serve_forever)
    server.serve(tmp_path, port=0, open_browser=False)
    assert done.is_set() and seen == {"without": 403, "with_file_secret": 200}


# -- the audit of every GET route (18.1) ------------------------------------------------------------------------

#: Every route `do_GET` answers, and what it is, so that a route added later must be looked at:
#:   boot      sets the session cookie (a header, nothing on disk)
#:   page      the page and its scripts and styles
#:   read      reads the project, the index or this Mac's settings; writes nothing a person keeps (SQLite's own side
#:             files while a read connection is open; and, in a team working copy, the hash cache
#:             `<home>/graphs/<id>/share-fingerprints.json` that every officialness mark refreshes -- derived, in the
#:             engine's own home, rewritten only when an entry changed from 18.4 item 4 on)
#:   probe     a read that runs another program for what it says: read-only `git` (GIT_OPTIONAL_LOCKS=0),
#:             `<runner> --version`, `opencode models`, `mdfind` for Zotero; none changes anything
#:   recovers  reads, and also writes -- but only for a request that carries the session (the run list's recovery
#:             of runs a gone engine left active)
#:   none      the 404 for an unknown /api/ path
#: The sample is a request that reaches the route. No route opens an application, changes a setting, writes into
#: the project or starts a scan; those are POSTs, which need the session.
GET_ROUTES: dict[str, tuple[str, str]] = {
    "/boot": ("boot", "/boot"),
    "/": ("page", "/"),
    "/canvas.js": ("page", "/canvas.js"),
    "/i18n.js": ("page", "/i18n.js"),
    "/desk.js": ("page", "/desk.js"),
    "/node.js": ("page", "/node.js"),
    "/team.js": ("page", "/team.js"),
    "/routemap.js": ("page", "/routemap.js"),
    "/workspace.js": ("page", "/workspace.js"),
    "/workspace.css": ("page", "/workspace.css"),
    "/api/workspace": ("read", "/api/workspace"),
    "/api/outputs/folders": ("read", "/api/outputs/folders"),
    "/api/workspace/document": ("read", "/api/workspace/document?path=research/overview.md"),
    "/api/workspace/exists": ("read", "/api/workspace/exists?path=map.md"),
    "/api/workspace/asset": ("read", "/api/workspace/asset?path=figs/fig1.png"),
    "/api/summary": ("read", "/api/summary"),
    "/api/settings/llm": ("read", "/api/settings/llm"),
    "/api/attempts": ("read", "/api/attempts"),
    "/api/tree": ("read", "/api/tree"),
    "/api/lineage": ("read", "/api/lineage"),
    "/api/projects": ("read", "/api/projects"),
    "/api/engine": ("read", "/api/engine"),
    "/api/generation": ("read", "/api/generation"),
    "/api/canvas": ("read", "/api/canvas?scope=all"),
    "/api/review": ("read", "/api/review"),
    "/api/github": ("probe", "/api/github"),
    "/api/github/push-plan": ("probe", "/api/github/push-plan"),
    "/api/citations": ("probe", "/api/citations"),
    "/api/variables": ("read", "/api/variables"),
    "/api/variables/": ("read", "/api/variables/code"),
    "/api/migration": ("read", "/api/migration"),
    "/api/history": ("read", "/api/history?node=attempt:map.md%2316"),
    "/api/history/version": ("read", "/api/history/version?node=script:steps/16-build.py&version=1"),
    "/api/history/diff": ("read", "/api/history/diff?node=script:steps/16-build.py&a=1&b=2"),
    "/api/notes": ("read", "/api/notes?node=script:steps/16-build.py"),
    "/api/trace": ("read", "/api/trace?node=figure:figs/fig2.png"),
    "/api/node": ("read", "/api/node?node=script:steps/16-build.py"),
    "/api/results": ("read", "/api/results"),
    "/api/routes": ("read", "/api/routes"),
    "/api/evidence": ("read", "/api/evidence?node=attempt:map.md%2316"),
    "/api/data-code": ("read", "/api/data-code"),
    "/api/stata": ("probe", "/api/stata"),
    "/api/proposals": ("read", "/api/proposals"),
    "/api/proposals/": ("read", "/api/proposals/pr-0"),
    "/api/desk": ("probe", "/api/desk"),
    "/api/share": ("read", "/api/share"),
    "/api/me": ("read", "/api/me"),
    "/api/me/key": ("read", "/api/me/key"),
    "/api/share/sync-preview": ("read", "/api/share/sync-preview"),
    "/api/agents": ("probe", "/api/agents"),
    "/api/agents/models": ("probe", "/api/agents/models?runner=opencode"),
    "/api/agents/runs": ("recovers", "/api/agents/runs"),
    "/api/agents/runs/": ("read", "/api/agents/runs/r-nothing"),   # one run, and its /revert-plan (18.1 item 5)
    "/api/agents/large-files": ("read", "/api/agents/large-files"),
    "/api/records": ("read", "/api/records"),
    "/api/file": ("probe", "/api/file?path=map.md"),
    "/api/": ("none", "/api/nope"),
}


def _routes_in_do_get() -> set[str]:
    """Every path literal `do_GET` compares `path` with (== or in) or tests with startswith."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(server.RceRequestHandler.do_GET)))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == "path":
            for op, comparator in zip(node.ops, node.comparators):
                if isinstance(op, ast.Eq) and isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                    found.add(comparator.value)
                if isinstance(op, ast.In) and isinstance(comparator, (ast.Tuple, ast.List, ast.Set)):
                    found.update(c.value for c in comparator.elts if isinstance(c, ast.Constant) and isinstance(c.value, str))
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "startswith"
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "path"
                and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
            found.add(node.args[0].value)
    return found


def test_the_get_audit_names_every_route_do_get_answers():
    """A new GET route fails this until it is added to GET_ROUTES with what it is: read it against 18.1 first
    (does it write, start something, open an application, change a setting?)."""
    declared = _routes_in_do_get()
    assert declared == set(GET_ROUTES), (sorted(declared - set(GET_ROUTES)), sorted(set(GET_ROUTES) - declared))
    assert {kind for kind, _sample in GET_ROUTES.values()} == {"boot", "page", "read", "probe", "recovers", "none"}
    assert [r for r, (kind, _s) in GET_ROUTES.items() if kind == "recovers"] == ["/api/agents/runs"]


def _tree_state(*roots: Path) -> dict[str, tuple[int, str]]:
    import hashlib

    state: dict[str, tuple[int, str]] = {}
    for root in roots:
        for here, dirs, files in os.walk(root):
            for name in dirs + files:
                full = Path(here) / name
                if name.endswith(("-wal", "-shm")):   # SQLite's side files while a read connection is open
                    continue
                digest = ""
                if full.is_file() and not full.is_symlink():
                    digest = hashlib.sha256(full.read_bytes()).hexdigest()
                state[str(full)] = (full.lstat().st_mode, digest)
    return state


@pytest.fixture
def audited(tmp_path, monkeypatch):
    """A project with a map, scripts, a figure, research files and a git history, served by an engine that asks for
    its session; the agent programs are stubs, so no real `claude` or `opencode` is ever run."""
    root = _project(tmp_path / "proj")
    (root / "research").mkdir()
    (root / "research" / "overview.md").write_text("# Overview\n")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "start")
    monkeypatch.setattr(server.agent_profiles, "find",
                        lambda runner, check_version=True: profiles.Found(runner, "/stub/" + runner, "stub 1.0", None))
    monkeypatch.setattr(server.agent_profiles, "opencode_models", lambda path: ["stub/model"])
    started: list[str] = []
    real_popen = subprocess.Popen

    class Spy(real_popen):
        def __init__(self, args, *a, **kw):
            started.append(os.path.basename(str(args.split()[0] if isinstance(args, str) else args[0])))
            super().__init__(args, *a, **kw)

    monkeypatch.setattr(subprocess, "Popen", Spy)
    httpd, thread = _running(root)
    try:
        yield Engine(httpd), root, started
    finally:
        _stop(httpd, thread)


def test_no_get_route_writes_or_starts_anything_but_a_read_probe(audited):
    """18.1 audit, run: each route's sample, asked for without the session, answers (is not refused for it),
    leaves the project and Research OS's home exactly as they were, and starts no program but read-only
    `git` / `mdfind`."""
    eng, root, started = audited
    results: dict[str, int] = {}
    for route, (kind, sample) in GET_ROUTES.items():
        before = _tree_state(root, paths.researchos_home())
        started.clear()
        status, _headers, raw = eng.call("GET", sample)
        after = _tree_state(root, paths.researchos_home())
        assert before == after, (route, sorted(set(before.items()) ^ set(after.items()))[:4])
        assert set(started) <= {"git", "mdfind"}, (route, started)
        if kind in ("page", "read"):
            assert not started or set(started) == {"git"}, (route, started)
        results[route] = status
        assert not (status == 403 and b"session_required" in raw), route
        assert status < 500, (route, status, raw[:200])
    assert results["/"] == results["/api/summary"] == results["/api/agents"] == results["/api/agents/models"] == 200


def test_the_run_list_recovers_a_gone_engines_run_only_for_a_request_with_the_session(tmp_path):
    """The one GET that writes: a run recorded as running by an engine that is gone is marked `interrupted` when the
    list is asked for. A window without the session reads it as stored; the app's window recovers it."""
    root = tmp_path / "proj"
    root.mkdir()
    project.init_project(root)
    pid = read_identity(root).identity.id
    run = stale_run(types.SimpleNamespace(pid=pid, root=root), before=False)   # running; its engine's pid is dead
    httpd, thread = _running(root)
    try:
        eng = Engine(httpd)
        status, listing = eng.get("/api/agents/runs")
        assert status == 200 and [r["state"] for r in listing["runs"]] == ["running"] and listing["active"] is None
        assert run_store.load_run(pid, run["id"])["state"] == "running"          # nothing was written
        status, listing = eng.get("/api/agents/runs", {"Cookie": f"{eng.cookie}={SECRET}"})
        assert status == 200 and [r["state"] for r in listing["runs"]] == ["interrupted"]
        stored = run_store.load_run(pid, run["id"])
        assert stored["state"] == "interrupted" and stored["error"]["code"] == "engine_gone"
    finally:
        _stop(httpd, thread)


def test_a_wrong_session_does_not_recover_a_run_either(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    project.init_project(root)
    pid = read_identity(root).identity.id
    run = stale_run(types.SimpleNamespace(pid=pid, root=root), before=False)
    httpd, thread = _running(root)
    try:
        eng = Engine(httpd)
        eng.get("/api/agents/runs", {"X-ResearchOS-Session": "wrong"})
        assert run_store.load_run(pid, run["id"])["state"] == "running"
    finally:
        _stop(httpd, thread)


def test_a_server_without_a_session_still_recovers_on_listing(tmp_path):
    """What every other test file relies on."""
    root = tmp_path / "proj"
    root.mkdir()
    project.init_project(root)
    pid = read_identity(root).identity.id
    run = stale_run(types.SimpleNamespace(pid=pid, root=root), before=False)
    httpd, thread = _running(root, session=None)
    try:
        assert Engine(httpd).get("/api/agents/runs")[1]["runs"][0]["state"] == "interrupted"
        assert run_store.load_run(pid, run["id"])["state"] == "interrupted"
    finally:
        _stop(httpd, thread)


# -- the page ---------------------------------------------------------------------------------------------------


def _app_source() -> str:
    return APP_HTML.read_text(encoding="utf-8")


def _page_error_script(lang: str, body: dict[str, Any]) -> str:
    """The page's own `apiError` and `renderBlockingError` (with its table and `t`), as served, run on a
    refusal body in the given interface language; prints the sentence shown and the text behind 「详情」."""
    src = _app_source()
    t_block = src[src.index("function fillVars("): src.index("// -- Small shared state")]
    api_error = src[src.index("function apiError("): src.index("\n}\n", src.index("function apiError(")) + 3]
    table = src[src.index("const BLOCKING_ERROR_TEXT = {"): src.index("// Replaces `el`'s content")]
    render = src[src.index("function renderBlockingError("): src.index("\n}\n", src.index("function renderBlockingError(")) + 3]
    return (
        "global.window = {};\n"
        "class El { constructor(tag) { this.tag = tag; this.kids = []; this.textContent = ''; this.title = ''; this.className = '';"
        " this.classList = { toggle: () => true }; }\n"
        "  appendChild(k) { this.kids.push(k); return k; } append(...ks) { this.kids.push(...ks); }\n"
        "  setAttribute() {} addEventListener() {} }\n"
        "global.document = { createElement: (tag) => new El(tag) };\n"
        f"window.ROI18N_SAVED = {json.dumps(lang)};\n" + I18N_JS.read_text(encoding="utf-8") + "\n"
        + t_block + api_error + table + render + "\n"
        f"const err = apiError('/api/workspace/document', {{ status: 403 }}, {json.dumps(body)});\n"
        "const el = new El('div');\n"
        "renderBlockingError(el, 'Framing the caller would use', err);\n"
        "process.stdout.write(JSON.stringify({ shown: el.kids[0].textContent, detail: el.kids.length > 2 ? el.kids[2].textContent : null,"
        " state: err.state, engineMessage: err.engineMessage }));\n"
    )


@needs_node
@pytest.mark.parametrize("lang,sentence", [("zh", SENTENCE_ZH), ("en", SENTENCE_EN)])
def test_the_page_shows_the_sentence_where_a_refused_write_shows_its_error(engine, lang, sentence):
    """A window without the session saves nothing; the engine's real refusal, run through the page's own error
    code, reads as the 18.1 sentence in the screen's language, the engine's account behind 「详情」."""
    eng, root = engine
    status, _headers, raw = eng.call("POST", "/api/workspace/document", {
        "project": str(root), "path": "research/overview.md", "revision": None, "content": "x"},
        {"X-ResearchOS-Lang": lang})
    body = json.loads(raw)
    assert status == 403 and body["message"] == sentence
    out = subprocess.run([NODE, "-"], input=_page_error_script(lang, body), capture_output=True, text=True, check=True)
    shown = json.loads(out.stdout)
    assert shown["shown"] == sentence
    assert shown["state"] == "session_required" and shown["engineMessage"] == sentence
    assert shown["detail"] == body["detail"] and "X-ResearchOS-Session" in shown["detail"]


@needs_node
@pytest.mark.parametrize("lang,sentence", [("zh", SENTENCE_ZH), ("en", SENTENCE_EN)])
def test_the_route_map_says_it_where_a_line_or_a_position_is_refused(lang, sentence):
    script = ("global.window = {};\n" + f"window.ROI18N_SAVED = {json.dumps(lang)};\n" + I18N_JS.read_text(encoding="utf-8") + "\n"
              + ROUTEMAP_JS.read_text(encoding="utf-8")
              + "\nprocess.stdout.write(JSON.stringify(window.RORouteMap.lineRefusal('session_required')));\n")
    out = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == sentence


def test_the_page_and_the_engine_say_the_same_words_in_both_languages():
    """The Chinese is the key the page translates; the English is its entry; both are the engine's sentences."""
    src = _app_source()
    table = src[src.index("const BLOCKING_ERROR_TEXT = {"): src.index("// Replaces `el`'s content")]
    assert f'session_required: "{SENTENCE_ZH}"' in table
    assert f'"{SENTENCE_ZH}": "{SENTENCE_EN}"' in I18N_JS.read_text(encoding="utf-8")
    assert f't("{SENTENCE_ZH}")' in ROUTEMAP_JS.read_text(encoding="utf-8")
    assert (server.SESSION_REQUIRED_ZH, server.SESSION_REQUIRED_EN) == (SENTENCE_ZH, SENTENCE_EN)
    assert not re.search(r"->|→|▸|▶|»|⟶|[\U0001F300-\U0001FAFF]", SENTENCE_ZH + SENTENCE_EN)


def test_the_page_makes_no_request_of_its_own_that_would_skip_the_cookie():
    """Every write goes through app.html's apiPost (same origin, so the cookie travels); no module opens a
    request of its own."""
    for name in ("app.html", "canvas.js", "desk.js", "node.js", "team.js", "routemap.js", "workspace.js"):
        text = (WEBAPP / name).read_text(encoding="utf-8")
        own = len(re.findall(r"\bfetch\(", text)) + len(re.findall(r"XMLHttpRequest|navigator\.sendBeacon", text))
        assert own == (2 if name == "app.html" else 0), name
    src = _app_source()
    assert "credentials" not in src  # the default for a same-origin fetch already sends cookies
