"""Research OS V9, first increment, the plumbing (DESIGN.md 13.2, 13.3,
13.6): the interface language saved per Mac in settings.json, the request
header the page names it in, the served /i18n.js with the saved choice
written above it, t() and the markup helper run under node in both
languages, the switch beside 停止服务 -- and the COMPLETENESS helpers every
later increment extends: for each converted surface, every key it passes to
t() has an English entry, no Chinese literal is left outside t(), and every
English entry obeys 13.4 and 12.11 (no CJK, no arrows, no emoji, “ ” for
「 」). Every test names its scenario."""

from __future__ import annotations

import email.message
import http.client
import html as html_mod
import json
import re
import shutil
import subprocess
import threading
import tomllib
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest

from researchos import github, literature, settings
from researchos.webapp import server

from test_v7_connections import GITHUB_URL, _colleague_pushes, linked, linked_server, stand_in  # noqa: F401 -- fixtures
from test_v8_phase2 import _csv, _map, _project, _scan
from test_v8_u2 import _KEYWORDS_BEFORE, _REGEX_BEFORE, FORBIDDEN, _served_scripts

WEBAPP = Path(server.__file__).parent
APP_HTML = WEBAPP / "app.html"
I18N_JS = WEBAPP / "i18n.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

#: The per-module pattern (13.6), verbatim in every module or app.html block
#: that calls t(): resolved at CALL time, Chinese (filled) without /i18n.js.
FILL_VARS_LINE = (
    'function fillVars(s, v) { return String(s).replace(/\\{(\\w+)\\}/g, function (m, k) { return v && '
    'Object.prototype.hasOwnProperty.call(v, k) ? String(v[k]) : m; }); }'
)
T_LINE = 'function t(s, v) { var I = typeof window !== "undefined" && window.ROI18N; return I ? I.t(s, v) : fillVars(s, v); }'


# -- the completeness helpers (later increments extend CONVERTED) ------------------------------------------

#: 13.7: the surfaces converted so far -- a served file, or the stretch of
#: one between two markers (a start marker of None is the file's start, an
#: end marker of None its end). In each: every Chinese literal is the whole
#: first argument of t() (or listed in ALLOWED_CHINESE), and every such key
#: has an English entry. T2, T3 and later increments add theirs here.
CONVERTED: list[tuple[str, str | None, str | None]] = [
    ("app.html", "// -- The interface language (DESIGN.md 13.6)", "// -- Small shared state"),
    ("app.html", "// -- The language switch (DESIGN.md 13.2, 13.3)", "// -- Native shell bridge"),
    # T2, the frame (13.7): the page titles, the stopped page and its question; in desk.js the
    # dates (13.5), the shared folder's line and the sidebar's live parts (the count's title, who
    # I am). 新增尝试 in renderSidebar belongs to 研究路线's increment, so it is left between them.
    ("app.html", "// A section's title on screen (13.4)", "let lastDataView"),
    ("app.html", "function renderStoppedState() {", "// -- The language switch (DESIGN.md 13.2, 13.3)"),
    # T3, the desk (13.7), and the frame's part B (13.7 as amended: the top bar's 新增尝试 /
    # 提议新的尝试 belong to the frame; the attempt form they open is later): all of desk.js.
    ("desk.js", None, None),
    # The frame's part B: the switcher button, the project menu and its GitHub lines; then the
    # menu's own actions (switch, choose a new location, rescan) up to the rename and remove
    # steps, which are flows of their own (a later increment).
    ("app.html", "// -- The project menu (DESIGN.md 10.1, 10.4; task V6)", "// -- 「推送到 GitHub…」 (DESIGN.md 11.3, task V7)"),
    ("app.html", "function backToMenuList() {", "// 10.4: 「重命名显示名称…」 changes the registry label only"),
]
#: Chinese a converted surface may keep outside t(), with the reason (13.1:
#: fixed on-disk names, the researcher's vocabulary; 13.3: a language's own
#: name). Keys are the exact literal.
_ERROR = "13.7: an error sentence -- errors everywhere are converted in their own increment"
ALLOWED_CHINESE: dict[str, str] = {
    "停止服务失败": _ERROR,
    # The project menu's error framings (8.8 "Errors"), shown before the engine's own text.
    "没能从 GitHub 获取": _ERROR,
    "切换项目失败": _ERROR,
    "没能使用这个位置": _ERROR,
    "没能开始扫描": _ERROR,
}

CJK = re.compile("[\u2e80-\u2fff\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]")
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def js_literals(src: str) -> list[tuple[int, str, str]]:
    """Every string and template literal of a script as (offset of its
    opening quote, the quote, its raw text) -- comments and regular
    expressions skipped by test_v8_u2's rules (`js_strings`), with the
    offsets that t()'s argument check needs."""
    i, n, last, out = 0, len(src), "", []
    while i < n:
        c = src[i]
        if c in " \t\r\n":
            i += 1
        elif src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c in "'\"`":
            j = i + 1
            while j < n and src[j] != c:
                if src[j] == "\\":
                    j += 2
                    continue
                if c == "`" and src.startswith("${", j):
                    depth, j = 1, j + 2
                    while j < n and depth:
                        depth += {"{": 1, "}": -1}.get(src[j], 0)
                        j += 1
                    continue
                j += 1
            out.append((i, c, src[i + 1:j]))
            i, last = j + 1, "str"
        elif c == "/" and (last == "" or last in _REGEX_BEFORE or last in _KEYWORDS_BEFORE):
            j, in_class = i + 1, False
            while j < n and src[j] != "\n":
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == "[":
                    in_class = True
                elif src[j] == "]":
                    in_class = False
                elif src[j] == "/" and not in_class:
                    break
                j += 1
            j += 1
            while j < n and src[j].isalpha():
                j += 1
            i, last = j, "re"
        else:
            word = re.match(r"[A-Za-z_$][\w$]*|\d[\w.]*", src[i:i + 64])
            if word:
                last = word.group(0) if not word.group(0)[0].isdigit() else "num"
                i += len(word.group(0))
            else:
                last = c
                i += 1
    return out


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}


def _unescape(raw: str) -> str:
    def one(m: re.Match[str]) -> str:
        e = m.group(1)
        if e[0] in "ux" and len(e) > 1:
            return chr(int(e[1:].strip("{}"), 16))
        return _ESCAPES.get(e, e)
    return re.sub(r"\\(u\{[0-9a-fA-F]+\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|.)", one, raw, flags=re.S)


def t_calls(src: str) -> list[tuple[str, bool]]:
    """Each string literal passed to t() -- `t("…")`, `I.t('…')` -- with
    whether it is the WHOLE first argument (a `+` after it is a sentence
    built by concatenation, which English word order cannot reuse: 13.6)."""
    found = []
    for start, quote, raw in js_literals(src):
        before = src[:start].rstrip()
        if quote == "`" or not before.endswith("t(") or re.search(r"[\w$]t\($", before[-3:]):
            continue
        after = src[start + len(raw) + 2:].lstrip()
        found.append((_unescape(raw), after[:1] in (")", ",")))
    return found


def markup_keys(html: str) -> list[str]:
    """The keys the page's markup gives /i18n.js: the text of each element
    marked data-i18n (an element holding text alone) and each attribute
    named in data-i18n-attr. A marked element with no text of its own is
    reported as such."""
    body = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    body = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", body, flags=re.S)
    keys = []
    for m in re.finditer(r"<([a-zA-Z][\w-]*)\b([^>]*)>([^<]*)", body):
        attrs = dict((a, html_mod.unescape(v or "")) for a, v in re.findall(r'([\w-]+)(?:="([^"]*)")?', m.group(2)))
        if "data-i18n" in attrs:
            text = html_mod.unescape(m.group(3)).strip()
            keys.append(text or f"<{m.group(1)} data-i18n> holds no text of its own")
        for name in attrs.get("data-i18n-attr", "").split():
            keys.append(attrs.get(name, f"<{m.group(1)}> names {name!r} in data-i18n-attr but has none"))
    return keys


def _slice(text: str, start: str | None, end: str | None) -> str:
    a = text.index(start) if start else 0
    return text[a:text.index(end, a) if end else len(text)]


def _scripts_of(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return "\n".join(re.findall(r"<script>(.*?)</script>", text, re.S)) if path.suffix == ".html" else text


def english_table() -> dict[str, Any]:
    """ROI18N.en as /i18n.js defines it, read under node."""
    return _node("global.window = {};\n" + I18N_JS.read_text(encoding="utf-8")
                 + "\nprocess.stdout.write(JSON.stringify(window.ROI18N.en));\n")


def missing_entries(table: dict[str, Any]) -> list[str]:
    """Every t() key in the page's scripts and in each served script, and
    every marked markup text, that has no English entry -- converted or not:
    a key written is a key that must be translated."""
    missing = []
    for path in [APP_HTML, *_served_scripts()]:
        for key, _whole in t_calls(_scripts_of(path)):
            if key not in table:
                missing.append(f"{path.name}: t({key!r})")
    missing += [f"app.html markup: {key!r}" for key in markup_keys(APP_HTML.read_text(encoding="utf-8"))
                if key not in table]
    return missing


def untranslated(table: dict[str, Any]) -> list[str]:
    """In each CONVERTED surface: a Chinese literal that is not the whole
    argument of t() (and not in ALLOWED_CHINESE), and a key with no entry."""
    found = []
    for name, start, end in CONVERTED:
        src = _slice((WEBAPP / name).read_text(encoding="utf-8"), start, end)
        keys = {key: whole for key, whole in t_calls(src)}
        for _pos, _quote, raw in js_literals(src):
            text = _unescape(raw)
            if not CJK.search(text) or text in ALLOWED_CHINESE:
                continue
            if text not in keys:
                found.append(f"{name} ({start}): {text!r} is not passed to t()")
            elif not keys[text]:
                found.append(f"{name} ({start}): t({text!r} + ...) is built by concatenation")
            elif text not in table:
                found.append(f"{name} ({start}): t({text!r}) has no English entry")
    return found


def english_offenders(table: dict[str, Any]) -> list[str]:
    """Entries that break 13.4 / 13.6 / 12.11: a key that is not Chinese
    source text; a blank or non-string form; a plural that is not exactly
    {one, other}; CJK, an arrow or an emoji in the English; a straight
    double quote, or 「 」 without “ ” for it; a {name} the key lacks (or,
    beyond the singular, one it has that the English drops)."""
    bad = []
    for key, value in table.items():
        if not CJK.search(key):
            bad.append(f"{key!r}: the key is not the Chinese source text")
        if isinstance(value, dict):
            if set(value) != {"one", "other"}:
                bad.append(f"{key!r}: a plural is exactly {{one, other}}, not {sorted(value)}")
                continue
            forms = [("one", value["one"]), ("other", value["other"])]
        else:
            forms = [("", value)]
        want = set(PLACEHOLDER.findall(key))
        for form, text in forms:
            where = f"{key!r}{'.' + form if form else ''}"
            if not isinstance(text, str) or not text.strip():
                bad.append(f"{where}: blank")
                continue
            if CJK.search(text):
                bad.append(f"{where}: CJK in the English {text!r}")
            if FORBIDDEN.search(text):
                bad.append(f"{where}: an arrow or emoji in {text!r}")
            if '"' in text or ("「" in key and "“" not in text):
                bad.append(f"{where}: quotation marks are “ ” in English ({text!r})")
            got = set(PLACEHOLDER.findall(text))
            if got - want or (form != "one" and got != want):
                bad.append(f"{where}: fields {sorted(got)} where the key has {sorted(want)}")
    return bad


# -- node --------------------------------------------------------------------------------------------------


def _node(script: str) -> Any:
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def _i18n_source(entries: dict[str, Any] | None = None) -> str:
    """i18n.js, with extra English entries written into its table (a test's
    own keys for the plural and field rules)."""
    src = I18N_JS.read_text(encoding="utf-8")
    if entries:
        marker = "  var EN = {\n"
        assert src.count(marker) == 1
        src = src.replace(marker, marker + "".join(
            f"    {json.dumps(k, ensure_ascii=False)}: {json.dumps(v, ensure_ascii=False)},\n" for k, v in entries.items()))
    return src


def _with_i18n(expr: str, *, saved: Any = None, languages: list[str] | None = None,
               entries: dict[str, Any] | None = None, prelude: str = "") -> Any:
    """Evaluates `expr` after loading i18n.js as served: the saved choice
    written above it, the browser's languages as given (None: no
    navigator at all), no document unless `prelude` makes one."""
    nav = json.dumps({"languages": languages}) if languages is not None else "undefined"
    script = (
        "global.window = {};\n"
        f"Object.defineProperty(globalThis, 'navigator', {{value: {nav}, configurable: true}});\n"
        + prelude
        + f"window.ROI18N_SAVED = {json.dumps(saved)};\n"
        + _i18n_source(entries)
        + f"\nprocess.stdout.write(JSON.stringify({expr}));\n"
    )
    return _node(script)


def _app_function(head: str) -> str:
    src = APP_HTML.read_text(encoding="utf-8")
    start = src.index(head)
    return src[start:src.index("\n}\n", start) + 3]


def _app_block(start: str, end: str) -> str:
    return _slice(APP_HTML.read_text(encoding="utf-8"), start, end)


# -- the live engine ---------------------------------------------------------------------------------------


class _Live:
    def __init__(self, httpd: server.RceHTTPServer) -> None:
        self.port = httpd.server_address[1]

    def call(self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None
             ) -> tuple[int, dict[str, str], bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        try:
            data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode("utf-8"))
            conn.request(method, path, body=data, headers={"Host": f"127.0.0.1:{self.port}",
                                                           "Content-Type": "application/json", **(headers or {})})
            resp = conn.getresponse()
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
        finally:
            conn.close()

    def post(self, path: str, body: Any, headers: dict[str, str] | None = None) -> tuple[int, Any]:
        status, _h, raw = self.call("POST", path, body, headers)
        return status, json.loads(raw)


def _serving(root: Path | None):
    httpd = server.build_server(root, 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


@pytest.fixture
def live():
    httpd, thread = _serving(None)  # no project: the switch works on the welcome too (10.1)
    try:
        yield _Live(httpd)
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _settings_file() -> dict[str, Any] | None:
    path = settings.settings_path()
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


# -- 13.2: the setting --------------------------------------------------------------------------------------


def test_13_2_no_choice_reads_none_and_writes_nothing():
    """With nothing saved the setting is None (the page and the shell follow
    the Mac); reading never creates the file."""
    assert settings.interface_language() is None
    assert not settings.settings_path().exists()


def test_13_2_the_choice_round_trips_and_keeps_the_doi_setting():
    """Saving writes exactly `interface_language` beside `doi_online_lookup`
    (13.8 #3); switching back keeps the DOI key as it was."""
    literature.set_doi_lookup(True)
    settings.set_interface_language("en")
    assert settings.interface_language() == "en"
    assert _settings_file() == {"doi_online_lookup": True, "interface_language": "en"}
    settings.set_interface_language("zh")
    assert settings.interface_language() == "zh" and literature.doi_lookup_enabled() is True
    assert _settings_file() == {"doi_online_lookup": True, "interface_language": "zh"}


@pytest.mark.parametrize("stored", ['{"interface_language": "fr"}', '{"interface_language": "EN"}',
                                    '{"interface_language": 1}', '{"interface_language": ["en"]}',
                                    '{"interface_language": null}', '["en"]', '{not json', ""])
def test_13_2_anything_saved_but_zh_or_en_is_no_choice(stored):
    path = settings.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stored, encoding="utf-8")
    assert settings.interface_language() is None


@pytest.mark.parametrize("bad", ["fr", "EN", "", None, 1, ["en"]])
def test_13_2_only_zh_or_en_can_be_saved(bad):
    """Saving anything else is refused and writes nothing."""
    with pytest.raises(ValueError):
        settings.set_interface_language(bad)
    assert not settings.settings_path().exists()


def test_13_2_the_doi_switch_alone_writes_no_language_and_uses_the_shared_helpers():
    """The helpers moved to `researchos.settings`; the DOI switch writes only
    its own key (tests/test_v7_literature_view.py pins the same), and
    `literature` still answers with the one settings path."""
    literature.set_doi_lookup(True)
    assert _settings_file() == {"doi_online_lookup": True}
    assert literature.settings_path is settings.settings_path
    assert literature.settings_path() == settings.settings_path()


@pytest.mark.parametrize("value, lang", [("en", "en"), (" EN ", "en"), ("zh", "zh"), (None, "zh"), ("", "zh"),
                                         ("fr", "zh"), ("en-US", "zh"), ("english", "zh")])
def test_13_2_the_request_header_names_en_or_else_zh(value, lang):
    assert settings.request_language(value) == lang


def test_13_2_the_request_handler_reads_its_header():
    """`RceRequestHandler._lang`: the header, case-insensitively named as
    HTTP headers are; Chinese without it."""
    msg = email.message.Message()
    msg["x-researchos-lang"] = "en"
    assert server.RceRequestHandler._lang(type("H", (), {"headers": msg})()) == "en"
    assert server.RceRequestHandler._lang(type("H", (), {"headers": email.message.Message()})()) == "zh"


def test_13_6_say_picks_the_sentence_by_language_and_fills_it():
    assert settings.say("共 {n} 个文件", "{n} files", "en", n=3) == "3 files"
    assert settings.say("共 {n} 个文件", "{n} files", "zh", n=3) == "共 3 个文件"
    assert settings.say("工作台", "Desk", "fr") == "工作台"


# -- 13.2, 13.3: the endpoint and the served /i18n.js --------------------------------------------------------


def test_13_3_the_switch_endpoint_saves_and_i18n_js_carries_the_choice(live):
    """POST /api/settings/language with no project open saves the choice;
    /i18n.js is the file under one line naming it, never cached."""
    status, headers, raw = live.call("GET", "/i18n.js")
    assert status == 200 and headers["content-type"].startswith("text/javascript")
    assert headers["cache-control"] == "no-store"
    assert raw.decode("utf-8") == "window.ROI18N_SAVED = null;\n" + I18N_JS.read_text(encoding="utf-8")
    assert _settings_file() is None  # serving the page writes nothing (13.2: never by default)

    assert live.post("/api/settings/language", {"language": "en"}) == (200, {"ok": True, "language": "en"})
    assert _settings_file() == {"interface_language": "en"}
    first = live.call("GET", "/i18n.js")[2].decode("utf-8").split("\n", 1)[0]
    assert first == 'window.ROI18N_SAVED = "en";'

    assert live.post("/api/settings/language", {"language": "zh"})[0] == 200
    assert live.call("GET", "/i18n.js")[2].decode("utf-8").startswith('window.ROI18N_SAVED = "zh";\n')


@pytest.mark.parametrize("body", [{"language": "fr"}, {"language": "EN"}, {"language": ["en"]}, {"language": None},
                                  {}, {"lang": "en"}])
def test_13_3_anything_but_zh_or_en_is_refused(live, body):
    status, res = live.post("/api/settings/language", body)
    assert (status, res["state"]) == (400, "language_bad_setting")
    assert _settings_file() is None


@pytest.mark.parametrize("method, path", [("POST", "/api/settings/language"), ("GET", "/i18n.js")])
@pytest.mark.parametrize("headers", [{"Origin": "http://evil.example"}, {"Host": "evil.example:80"}])
def test_13_3_the_new_routes_refuse_another_origin(live, method, path, headers):
    status, _h, _raw = live.call(method, path, {"language": "en"} if method == "POST" else None, headers)
    assert status == 403
    assert _settings_file() is None


def test_13_1_switching_in_a_project_writes_nothing_into_it(tmp_path):
    """The language is a property of the screen (13.1): switching with a
    project open changes settings.json and not one byte of the project."""
    root = _project(tmp_path / "proj")
    _scan(root)
    httpd, thread = _serving(root)
    try:
        before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
        assert _Live(httpd).post("/api/settings/language", {"language": "en"})[0] == 200
        assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    assert settings.interface_language() == "en"


def test_13_6_i18n_js_is_served_without_a_project_and_ships():
    assert "/i18n.js" in server._NO_PROJECT_GET and "/api/settings/language" in server._NO_PROJECT_POST
    pyproject = tomllib.loads((Path(server.__file__).parents[3] / "pyproject.toml").read_text(encoding="utf-8"))
    assert "webapp/i18n.js" in pyproject["tool"]["setuptools"]["package-data"]["researchos"]


# -- the page: loaded first, the header, the switch -------------------------------------------------------------


def test_13_6_i18n_js_is_the_first_script_after_the_markup():
    """Loaded first (every module finds ROI18N), and after the markup it
    translates, with the tag shape the 12.11 scanner follows; the page's
    own <html lang> stays zh-CN (i18n.js sets it)."""
    html = re.sub(r"<!--.*?-->", "", APP_HTML.read_text(encoding="utf-8"), flags=re.S)
    tag = '<script src="/i18n.js"></script>'
    assert html.count(tag) == 1 and html.index(tag) == html.index("<script")
    assert html.index('id="lang-btn"') < html.index(tag) and html.index('id="panel-body"') < html.index(tag)
    assert [p.name for p in _served_scripts()][0] == "i18n.js"
    assert '<html lang="zh-CN">' in html


def test_13_3_the_switch_sits_beside_records_and_stop_service():
    """A quiet text button (the neighbours' class) after 记录 and 停止服务,
    named in the language it switches to; its title is marked for /i18n.js."""
    html = APP_HTML.read_text(encoding="utf-8")
    tools = html[html.index('<div class="side-tools">'):html.index("</div>", html.index('<div class="side-tools">'))]
    assert re.findall(r'<button id="([\w-]+)" class="shutdown-btn"', tools) == ["records-btn", "shutdown-btn", "lang-btn"]
    assert ('<button id="lang-btn" class="shutdown-btn" type="button" lang="en" title="切换界面语言" '
            'data-i18n-attr="title" data-i18n-switch>English</button>') in tools


@needs_node
@pytest.mark.parametrize("roi18n, lang", [(None, "zh"), ({"lang": "zh"}, "zh"), ({"lang": "en"}, "en")])
def test_13_2_every_request_names_the_language(roi18n, lang):
    """apiGet and apiPost -- the page's only two fetch() calls -- send
    X-ResearchOS-Lang with ROI18N.lang; Chinese without /i18n.js."""
    html = APP_HTML.read_text(encoding="utf-8")
    assert len(re.findall(r"\bfetch\(", "\n".join(re.findall(r"<script>(.*?)</script>", html, re.S)))) == 2
    script = (
        f"global.window = {json.dumps({} if roi18n is None else {'ROI18N': roi18n})};\n"
        "const sent = [];\n"
        "global.fetch = async (path, opts) => { sent.push([path, opts.method || 'GET', opts.headers]);"
        " return { ok: true, status: 200, text: async () => '{}' }; };\n"
        + _app_block("// -- The interface language (DESIGN.md 13.6)", "// -- Small shared state")
        + _app_function("function apiError(") + _app_function("async function apiGet(")
        + _app_function("async function apiPost(")
        + "(async () => { await apiGet('/api/desk'); await apiPost('/api/notes', {a: 1});"
        " process.stdout.write(JSON.stringify(sent)); })();\n"
    )
    sent = _node(script)
    assert [(p, m, h.get("X-ResearchOS-Lang")) for p, m, h in sent] == [("/api/desk", "GET", lang), ("/api/notes", "POST", lang)]
    assert sent[0][2]["Accept"] == "application/json" and sent[1][2]["Content-Type"] == "application/json"


def _switch_run(lang: str, fail: bool) -> list:
    script = (
        "const calls = [];\n"
        f"global.window = {{ ROI18N: {{ lang: {json.dumps(lang)}, t: (s) => 'T:' + s }},"
        " webkit: { messageHandlers: { rce: { postMessage: (m) => calls.push(['shell', m]) } } },"
        " location: { reload: () => calls.push(['reload']) } };\n"
        "const handlers = {}; const btn = { disabled: false, addEventListener: (e, fn) => { handlers[e] = fn; } };\n"
        "global.document = { getElementById: (id) => id === 'lang-btn' ? btn : null };\n"
        "async function apiPost(path, body) { calls.push(['post', path, body, btn.disabled]);"
        f" if ({json.dumps(fail)}) throw new Error('disk full'); return {{ ok: true }}; }}\n"
        "function showHeaderError(framing, err) { calls.push(['error', framing, err.message]); }\n"
        + _app_block("// -- The interface language (DESIGN.md 13.6)", "// -- Small shared state")
        + _app_block("// -- The language switch (DESIGN.md 13.2, 13.3)", "// -- Native shell bridge")
        + _app_function("function postShellLanguage(")
        + "(async () => { await handlers.click(); calls.push(['disabled', btn.disabled]);"
        " process.stdout.write(JSON.stringify(calls)); })();\n"
    )
    return _node(script)


@needs_node
def test_13_3_one_click_saves_tells_the_shell_and_reloads():
    """In order: the POST of the other language, {type: "language"} to the
    shell (which re-reads settings.json itself), the reload -- the
    remembered view brings the same section back."""
    assert _switch_run("zh", fail=False) == [
        ["post", "/api/settings/language", {"language": "en"}, True],
        ["shell", {"type": "language"}], ["reload"], ["disabled", True]]
    assert _switch_run("en", fail=False)[0] == ["post", "/api/settings/language", {"language": "zh"}, True]


@needs_node
def test_13_3_a_refused_switch_says_so_and_changes_nothing():
    """No shell message, no reload, the button usable again; the framing
    goes through t() (here a stand-in that marks it)."""
    assert _switch_run("en", fail=True) == [
        ["post", "/api/settings/language", {"language": "zh"}, True],
        ["error", "T:没能切换界面语言", "disk full"], ["disabled", False]]


# -- /i18n.js under node ------------------------------------------------------------------------------------------


@needs_node
@pytest.mark.parametrize("saved, languages, lang", [
    ("en", ["zh-Hans-CN"], "en"), ("zh", ["en-US"], "zh"),          # the saved choice wins
    (None, ["zh-Hans-CN", "en"], "zh"), (None, ["zh-TW"], "zh"),    # no choice: the Mac's first language
    (None, ["en-GB", "zh-CN"], "en"), (None, ["de-DE"], "en"),
    (None, [], "en"), (None, None, "en"),                            # nothing known: anything else is English
    ("fr", ["zh-CN"], "zh"), (1, ["en-US"], "en"),                   # an invalid saved value is no choice
])
def test_13_2_the_page_resolves_its_language(saved, languages, lang):
    got = _with_i18n("[window.ROI18N.lang, window.ROI18N.saved]", saved=saved, languages=languages)
    assert got == [lang, saved if saved in ("zh", "en") else None]


def _expected_language(case: dict[str, Any]) -> str:
    """13.2 as amended: the saved choice, then the shell's answer, then the
    browser's first language, then English; anything but exactly "zh" or
    "en" in the first two counts as none."""
    for value in (case["saved"], case.get("system")):
        if value in ("zh", "en"):
            return value
    first = (case["languages"] or [""])[0]
    return "zh" if first.lower().startswith("zh") else "en"


@needs_node
def test_13_2_the_saved_choice_then_the_shells_answer_then_the_browsers_in_every_combination():
    """Inside the shell the page does not trust navigator.languages (a web
    view in an app with no localizations may say English on a Chinese Mac):
    the shell injects window.ROI18N_SYSTEM at document start. Every
    combination of a saved choice, the shell's answer (absent, valid or
    not) and the browser's languages (no navigator at all included), each
    run through /i18n.js as served, in one node process."""
    cases = []
    for saved in (None, "zh", "en", "fr", 1):
        for system in ("<absent>", None, "zh", "en", "ZH", "", "fr", 1):
            for languages in (None, [], ["zh-Hans-CN", "en-US"], ["zh-TW"], ["en-US", "zh-Hans-CN"], ["de-DE"]):
                case = {"saved": saved, "languages": languages}
                if system != "<absent>":
                    case["system"] = system
                cases.append(case)
    script = (
        "const vm = require('vm');\n"
        f"const src = {json.dumps(_i18n_source())};\n"
        f"const cases = {json.dumps(cases)};\n"
        "const got = cases.map((c) => {\n"
        "  global.window = {};\n"
        "  if ('system' in c) window.ROI18N_SYSTEM = c.system;\n"
        "  Object.defineProperty(globalThis, 'navigator', {value: c.languages === null ? undefined"
        " : {languages: c.languages}, configurable: true});\n"
        "  window.ROI18N_SAVED = c.saved;\n"
        "  vm.runInThisContext(src);\n"
        "  return [window.ROI18N.lang, window.ROI18N.saved];\n"
        "});\n"
        "process.stdout.write(JSON.stringify(got));\n"
    )
    got = _node(script)
    assert len(got) == len(cases) == 240
    wrong = [(case, lang) for case, (lang, _saved) in zip(cases, got) if lang != _expected_language(case)]
    assert wrong == []
    assert [s for _lang, s in got] == [c["saved"] if c["saved"] in ("zh", "en") else None for c in cases]
    # The shell's answer decides only where it should: a Chinese Mac whose web view says English.
    assert _expected_language({"saved": None, "system": "zh", "languages": ["en-US"]}) == "zh"


@needs_node
@pytest.mark.parametrize("saved, line, languages, lang", [
    (None, 'window.ROI18N_SYSTEM = "zh";', ["en-US"], "zh"),        # 13.8 #1: a Chinese Mac stays Chinese in the shell
    (None, 'window.ROI18N_SYSTEM = "en";', ["zh-Hans-CN"], "en"),
    ("en", 'window.ROI18N_SYSTEM = "zh";', ["zh-Hans-CN"], "en"),   # the saved choice still wins
    ("zh", 'window.ROI18N_SYSTEM = "en";', ["en-US"], "zh"),
    (None, "", ["zh-Hans-CN"], "zh"),                                # a browser: exactly as before
])
def test_13_2_the_shells_line_as_it_injects_it(saved, line, languages, lang):
    """The line RCEShell.swift's systemLanguageScript writes (pinned in
    test_webapp_macapp), run at document start before /i18n.js; the page's
    <html lang> follows."""
    got = _with_i18n("[window.ROI18N.lang, document.documentElement.lang]", saved=saved, languages=languages,
                     prelude=line + "\nglobal.document = { documentElement: { setAttribute(k, v) { this[k] = v; } },"
                     " querySelectorAll: () => [] };\n")
    assert got == [lang, "en" if lang == "en" else "zh-CN"]


@needs_node
def test_13_3_the_page_and_the_shell_agree_on_the_language_message():
    """After a successful save the page posts exactly {type: "language"}:
    one key, no language named (the shell would not trust it). The shell,
    merged from v9-shell, accepts exactly that shape: `body.count == 1`."""
    script = (
        "const sent = [];\n"
        "global.window = { webkit: { messageHandlers: { rce: { postMessage: (m) => sent.push(m) } } } };\n"
        + _app_function("function postShellLanguage(")
        + "postShellLanguage();\n"
        "process.stdout.write(JSON.stringify(sent.map((m) => [Object.keys(m), m.type])));\n"
    )
    assert _node(script) == [[["type"], "language"]]
    swift = (WEBAPP / "shell" / "RCEShell.swift").read_text(encoding="utf-8")
    handler = swift[swift.index("didReceive message: WKScriptMessage"):]
    handler = handler[: handler.index("\n    }\n")]
    assert re.findall(r'body\.count == (\d+), body\["type"\] as\? String == "language"', handler) == ["1"]


@needs_node
def test_13_6_t_in_both_languages_with_fields_plurals_and_fallback():
    entries = {"{n} 个提议": {"one": "{n} proposal", "other": "{n} proposals"},
               "{who} 退回了「{what}」": "{who} returned “{what}”", "空": ""}
    calls = ("[T('{n} 个提议', {n: 1}), T('{n} 个提议', {n: 2}), T('{n} 个提议', {n: '1'}), T('{n} 个提议'),"
             " T('{who} 退回了「{what}」', {who: 'Li', what: '#16'}), T('没有英文 {x}', {x: 3}),"
             " T('空'), T('{a}{b}', {a: 1}), T('切换界面语言')]")
    expr = f"(function () {{ var T = window.ROI18N.t; return {calls}; }})()"
    assert _with_i18n(expr, saved="en", entries=entries) == [
        "1 proposal", "2 proposals", "1 proposal", "{n} proposals", "Li returned “#16”", "没有英文 3",
        "空", "1{b}", "Switch the interface language"]
    assert _with_i18n(expr, saved="zh", entries=entries) == [
        "1 个提议", "2 个提议", "1 个提议", "{n} 个提议", "Li 退回了「#16」", "没有英文 3",
        "空", "1{b}", "切换界面语言"]


_DOM = """
function el(attrs, text, children) {
  return { attrs: Object.assign({}, attrs), textContent: text, children: children || [],
    hasAttribute(n) { return Object.prototype.hasOwnProperty.call(this.attrs, n); },
    getAttribute(n) { return this.hasAttribute(n) ? this.attrs[n] : null; },
    setAttribute(n, v) { this.attrs[n] = String(v); } };
}
const asked = [];
const els = [
  el({"data-i18n": ""}, "  切换界面语言 \\n"),
  el({"data-i18n-attr": "title aria-label onclick", title: "切换界面语言", "aria-label": "没有英文", onclick: "切换界面语言"}, "x"),
  el({"data-i18n": ""}, "切换界面语言", [el({}, "child")]),
  el({"data-i18n-switch": "", lang: "en"}, "English"),
];
global.document = { documentElement: el({lang: "zh-CN"}, ""), querySelectorAll(sel) {
  asked.push(sel);
  if (sel === "[data-i18n], [data-i18n-attr]") return els.filter((e) => e.hasAttribute("data-i18n") || e.hasAttribute("data-i18n-attr"));
  if (sel === "[data-i18n-switch]") return els.filter((e) => e.hasAttribute("data-i18n-switch"));
  return [];
} };
"""


@needs_node
def test_13_3_markup_in_english_and_untouched_in_chinese():
    """On an English screen: marked text and the named attributes (title,
    aria-label, placeholder only) in English, a marked element with
    children left whole, the switch named 「中文」 in Chinese, <html lang="en">.
    On a Chinese screen the markup is not even looked at; lang stays zh-CN."""
    expr = ("{lang: document.documentElement.attrs.lang, asked: asked,"
            " els: els.map((e) => [e.textContent, e.attrs])}")
    en = _with_i18n(expr, saved="en", prelude=_DOM)
    assert en["lang"] == "en" and en["asked"] == ["[data-i18n], [data-i18n-attr]", "[data-i18n-switch]"]
    assert en["els"] == [
        ["Switch the interface language", {"data-i18n": ""}],
        ["x", {"data-i18n-attr": "title aria-label onclick", "title": "Switch the interface language",
               "aria-label": "没有英文", "onclick": "切换界面语言"}],
        ["切换界面语言", {"data-i18n": ""}],
        ["中文", {"data-i18n-switch": "", "lang": "zh-CN"}],
    ]
    zh = _with_i18n(expr, saved="zh", prelude=_DOM)
    assert zh["lang"] == "zh-CN" and zh["asked"] == []
    assert zh["els"][0][0] == "  切换界面语言 \n" and zh["els"][3] == ["English", {"data-i18n-switch": "", "lang": "en"}]


@needs_node
def test_13_6_the_served_file_runs_as_served():
    """The served text (the injected line, then the file) defines ROI18N
    in the saved language, with no navigator and no document."""
    settings.set_interface_language("en")
    script = ("global.window = {};\n" + server._i18n_js()
              + "\nprocess.stdout.write(JSON.stringify([window.ROI18N.lang, window.ROI18N.t('切换界面语言')]));\n")
    assert _node(script) == ["en", "Switch the interface language"]


# -- 13.6: the module pattern -----------------------------------------------------------------------------------


def test_13_6_every_module_that_calls_t_carries_the_one_helper():
    """Each served script or page that calls t() defines it with the exact
    pattern (lazy, Chinese fallback) and the same fill line as /i18n.js --
    so a module evaluated alone fills its fields the same way."""
    assert FILL_VARS_LINE in I18N_JS.read_text(encoding="utf-8")
    for path in [APP_HTML, *_served_scripts()]:
        src = _scripts_of(path)
        if path.name != "i18n.js" and t_calls(src):
            assert T_LINE in src and FILL_VARS_LINE in src, path.name


@needs_node
def test_13_6_a_block_run_alone_is_chinese_and_follows_roi18n_when_present():
    helpers = _app_block("// -- The interface language (DESIGN.md 13.6)", "// -- Small shared state")
    expr = "[t('切换界面语言'), t('第 {n} 版', {n: 2}), uiLang()]"
    alone = _node("global.window = {};\n" + helpers + f"\nprocess.stdout.write(JSON.stringify({expr}));\n")
    assert alone == ["切换界面语言", "第 2 版", "zh"]
    with_en = _with_i18n(f"(function () {{ {helpers} return {expr}; }})()", saved="en")
    assert with_en == ["Switch the interface language", "第 2 版", "en"]


# -- 13.6, 13.8 #6: completeness and the rules for English strings ---------------------------------------------


@needs_node
def test_13_6_every_key_has_an_english_entry_and_every_entry_obeys_the_rules():
    """Every t() key and marked markup text anywhere on the page has an
    entry; each CONVERTED surface keeps no Chinese outside t(); every
    English entry follows 13.4 and 12.11."""
    table = english_table()
    assert "切换界面语言" in table and "没能切换界面语言" in table
    assert missing_entries(table) == []
    assert untranslated(table) == []
    assert english_offenders(table) == []


def test_13_6_the_completeness_scanner_is_not_vacuous():
    """t() keys, concatenation, stray Chinese, comments and markup are seen
    as they are; bad English entries are caught."""
    src = (
        'const a = t("甲"); const b = I18N.t(\'乙\', {n: 1}); const c = t(`丙`);\n'
        'const d = "丁"; const e = t("戊" + x); const f = alert("庚"); // t("己")\n'
        'const g = t( "壬\\u4e00" ); const re = /t("x")/;\n'
    )
    assert t_calls(src) == [("甲", True), ("乙", True), ("戊", False), ("壬一", True)]
    assert markup_keys('<p data-i18n>工作台</p><b data-i18n><i>x</i></b>'
                       '<button title="停止" data-i18n-attr="title placeholder">x</button><!-- <p data-i18n>注</p> -->') == [
        "工作台", "<b data-i18n> holds no text of its own", "停止",
        "<button> names 'placeholder' in data-i18n-attr but has none"]
    assert english_offenders({
        "好": "Fine", "Desk": "Desk", "停止": "停止 service", "读取": "Reads -> writes", "打开「{x}」": 'Open "{x}"',
        "{n} 个": {"one": "one", "other": "{n} items"}, "{n} 条": {"one": "1", "many": "x"}, "空": " ",
        "第 {n} 版": "Version {m}", "共 {n} 项": "Some items",
    }) == [
        "'Desk': the key is not the Chinese source text",
        "'停止': CJK in the English '停止 service'",
        "'读取': an arrow or emoji in 'Reads -> writes'",
        "'打开「{x}」': quotation marks are “ ” in English ('Open \"{x}\"')",
        "'{n} 条': a plural is exactly {one, other}, not ['many', 'one']",
        "'空': blank",
        "'第 {n} 版': fields ['m'] where the key has ['n']",
        "'共 {n} 项': fields [] where the key has ['n']",
    ]


# -- 13.7: the frame -- the sidebar, the page titles, the stopped page (T2) --------------------------------------

#: The frame's markup, as stretches of the served page: the whole sidebar (its
#: label and brand, the project line with its eyebrow, switcher and menu, the
#: eight sections, the shared folder's line, who I am and the tool row), the
#: page title, the top bar's action button and the welcome shown without a
#: project (13.7).
FRAME_MARKUP = [
    ('<aside class="sidebar"', "</aside>"),
    ('<h1 class="page-title"', "</h1>"),
    ('<button id="new-attempt-btn"', "</button>"),
    ('<section id="welcome"', "</section>"),
]
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


def _elements(markup: str) -> list[dict[str, Any]]:
    """Each element of a stretch of markup as /i18n.js sees it: its
    attributes, the text directly inside it and its number of child
    elements (comments are not elements)."""
    els: list[dict[str, Any]] = []
    stack: list[dict[str, Any]] = []

    class _Parser(HTMLParser):
        def handle_starttag(self, tag, attrs):
            el = {"tag": tag, "attrs": {k: v or "" for k, v in attrs}, "text": "", "children": 0}
            if stack:
                stack[-1]["children"] += 1
            els.append(el)
            if tag not in _VOID:
                stack.append(el)

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)
            if tag not in _VOID:
                stack.pop()

        def handle_endtag(self, tag):
            if stack and stack[-1]["tag"] == tag:
                stack.pop()

        def handle_data(self, data):
            if stack:
                stack[-1]["text"] += data

    parser = _Parser(convert_charrefs=True)
    parser.feed(markup)
    parser.close()
    return els


def _frame_on_screen(page: str, i18n_js: str, languages: list[str] | None) -> tuple[list[dict], list[dict]]:
    """The frame's elements before and after the served /i18n.js runs over
    them under node (its own translateMarkup, on a small stand-in DOM)."""
    before = [el for start, end in FRAME_MARKUP for el in _elements(_slice(page, start, end))]
    nav = json.dumps({"languages": languages}) if languages is not None else "undefined"
    script = (
        "global.window = {};\n"
        f"Object.defineProperty(globalThis, 'navigator', {{value: {nav}, configurable: true}});\n"
        f"const data = {json.dumps(before, ensure_ascii=False)};\n"
        "const els = data.map((d) => ({ attrs: Object.assign({}, d.attrs), textContent: d.text,"
        " children: new Array(d.children).fill(null),"
        " hasAttribute(n) { return Object.prototype.hasOwnProperty.call(this.attrs, n); },"
        " getAttribute(n) { return this.hasAttribute(n) ? this.attrs[n] : null; },"
        " setAttribute(n, v) { this.attrs[n] = String(v); } }));\n"
        "global.document = { documentElement: { setAttribute() {} }, querySelectorAll(sel) {\n"
        "  if (sel === '[data-i18n], [data-i18n-attr]') return els.filter((e) => e.hasAttribute('data-i18n') || e.hasAttribute('data-i18n-attr'));\n"
        "  if (sel === '[data-i18n-switch]') return els.filter((e) => e.hasAttribute('data-i18n-switch'));\n"
        "  return []; } };\n"
        + i18n_js
        + "\nprocess.stdout.write(JSON.stringify(els.map((e) => ({ text: e.textContent, attrs: e.attrs }))));\n"
    )
    after = _node(script)
    return before, [{**el, "text": got["text"], "attrs": got["attrs"]} for el, got in zip(before, after)]


def _served_page_and_i18n(live: _Live, saved: str | None) -> tuple[str, str]:
    if saved is not None:
        assert live.post("/api/settings/language", {"language": saved})[0] == 200
    page = live.call("GET", "/")[2].decode("utf-8")
    return page, live.call("GET", "/i18n.js")[2].decode("utf-8")


def _words(els: list[dict], cls: str) -> list[str]:
    return [el["text"].strip() for el in els if cls in el["attrs"].get("class", "").split()]


@needs_node
@pytest.mark.parametrize("saved, languages", [("en", ["zh-Hans-CN"]), (None, ["en-GB"])])
def test_13_8_the_served_frame_in_english_carries_no_chinese(live, saved, languages):
    """13.8 #2: English by choice, or by a browser whose first language is
    not Chinese with nothing saved -- the sidebar's words, its labels and
    hover titles and the page title carry no Chinese; the switch alone is
    named 「中文」 in Chinese (13.3). Section names per 13.4."""
    page, i18n_js = _served_page_and_i18n(live, saved)
    _before, after = _frame_on_screen(page, i18n_js, languages)
    chinese = []
    for el in after:
        if "data-i18n-switch" in el["attrs"]:
            assert el["text"] == "中文"
            continue
        if CJK.search(el["text"]):
            chinese.append((el["tag"], el["text"].strip()))
        chinese += [(el["tag"], name, el["attrs"][name]) for name in ("title", "aria-label", "placeholder", "alt")
                    if CJK.search(el["attrs"].get(name, ""))]
    assert chinese == []
    assert _words(after, "brand-sub") == ["Research provenance"]
    assert _words(after, "side-label") == ["Desk", "Research routes", "Outputs", "Variables", "Literature",
                                           "Data & code", "Proposals", "Members"]
    assert _words(after, "shutdown-btn") == ["Records", "Stop service", "中文"]
    assert _words(after, "page-title") == ["Desk"]
    assert _words(after, "side-eyebrow") == ["Project"] and _words(after, "new-attempt-btn") == ["New attempt"]
    labels = {el["attrs"]["id"]: (el["attrs"].get("aria-label"), el["attrs"].get("title"))
              for el in after if el["attrs"].get("id") in ("sidebar", "side-nav", "records-btn", "shutdown-btn",
                                                           "project-menu", "welcome")}
    assert labels == {"sidebar": ("Project and sections", None), "side-nav": ("Sections", None),
                      "records-btn": (None, "See where this project’s human records are kept"),
                      "shutdown-btn": (None, "Stop the local service process"),
                      "project-menu": ("Project", None), "welcome": ("No project yet", None)}
    welcome = [el["text"].strip() for el in after if el["tag"] in ("p", "button") and el["text"].strip()
               and el["attrs"].get("id") not in ("project-menu-btn", "lang-btn", "new-attempt-btn")
               and "shutdown-btn" not in el["attrs"].get("class", "") and "side-item" not in el["attrs"].get("class", "")]
    assert welcome == [
        "Research OS reads the scripts, data and drafts in a research folder and works out how they connect, step by step.",
        "Research OS records the judgments you make about these links and keeps them with the study’s files.",
        "Add project…", "Join a project from the shared folder…", "A project is the folder that holds one study."]


@needs_node
@pytest.mark.parametrize("saved, languages", [("zh", ["en-US"]), (None, ["zh-Hans-CN"])])
def test_13_8_the_served_frame_in_chinese_is_untouched(live, saved, languages):
    """13.8 #1: a Chinese screen -- chosen, or the Mac's with nothing
    saved -- shows the frame's markup exactly as written: every word and
    attribute as before, the eight sections as 12.9 names them."""
    page, i18n_js = _served_page_and_i18n(live, saved)
    before, after = _frame_on_screen(page, i18n_js, languages)
    assert after == before
    assert _words(after, "side-label") == ["工作台", "研究路线", "成果", "变量", "文献", "数据与代码", "提议", "成员"]
    assert _words(after, "brand-sub") == ["研究脉络"] and _words(after, "page-title") == ["工作台"]
    assert _words(after, "shutdown-btn") == ["记录", "停止服务", "English"]
    assert _words(after, "side-eyebrow") == ["项目"] and _words(after, "new-attempt-btn") == ["新增尝试"]
    assert [el["text"].strip() for el in after if el["attrs"].get("id") in ("welcome-add-btn", "welcome-join-btn")] == [
        "添加项目…", "从公盘加入项目…"]


def test_13_3_the_frame_wraps_and_never_cuts_a_word():
    """13.3: labels fit the sidebar's fixed width in both languages; a
    longer English word wraps, never cut with an ellipsis -- no rule for
    the frame's words sets nowrap or text-overflow, and the width is 12.9's."""
    css = re.search(r"<style>(.*?)</style>", APP_HTML.read_text(encoding="utf-8"), re.S).group(1)
    frame = (".sidebar", ".side-brand", ".brand-sub", ".side-nav", ".side-item", ".side-label", ".side-count",
             ".side-foot", ".share-line", ".me-line", ".me-role", ".me-unset", ".side-tools", ".shutdown-btn",
             ".page-title", ".stopped-note")
    rules = [(sel, body) for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)
             if any(re.search(re.escape(cls) + r"(?![\w-])", sel) for cls in frame)]
    assert len(rules) >= 15
    assert [sel.strip() for sel, body in rules if "nowrap" in body or "text-overflow" in body] == []
    assert re.search(r"\.sidebar \{[^}]*width: 236px;", css)


@needs_node
@pytest.mark.parametrize("saved", [None, "zh", "en"])
def test_13_7_page_titles_follow_the_language(saved):
    """The page title (activateView) names each section in the screen's
    language; SECTION_TITLES stays the Chinese node.js's 「返回…」 reads."""
    html = APP_HTML.read_text(encoding="utf-8")
    assert 'document.getElementById("page-title").textContent = sectionTitle(section);' in html
    assert ('const SECTION_TITLES = {\n  desk: "工作台", routes: "研究路线", results: "成果", variables: "变量", '
            'literature: "文献",\n  data: "数据与代码", proposals: "提议", members: "成员",\n};') in html
    code = (_app_block("// -- The interface language (DESIGN.md 13.6)", "// -- Small shared state")
            + _app_function("function sectionTitle("))
    sections = ["desk", "routes", "results", "variables", "literature", "data", "proposals", "members", "node", None]
    expr = f"(function () {{ {code} return {json.dumps(sections)}.map(sectionTitle); }})()"
    got = _node("global.window = {};\n" + f"process.stdout.write(JSON.stringify({expr}));\n") if saved is None \
        else _with_i18n(expr, saved=saved)
    if saved == "en":
        assert got == ["Desk", "Research routes", "Outputs", "Variables", "Literature", "Data & code", "Proposals",
                       "Members", "", ""]
    else:
        assert got == ["工作台", "研究路线", "成果", "变量", "文献", "数据与代码", "提议", "成员", "", ""]


_STOPPED_ZH = ('<main id="main"><div class="stopped-note"><h2>服务已停止</h2><p>本地服务已经关闭，可以关闭此页。</p>'
               "<p>下次要查看时，双击 Research OS.app，或在终端运行 <code>researchos serve</code>。</p></div></main>")


@needs_node
@pytest.mark.parametrize("saved", [None, "zh", "en"])
def test_13_7_the_stopped_page_and_its_question(saved):
    """停止服务's question and the page left once the service stopped, in
    the screen's language: Chinese byte for byte as before; English with
    the command still set in <code>."""
    code = (_app_block("// -- The interface language (DESIGN.md 13.6)", "// -- Small shared state")
            + _app_function("function escapeHtml(")
            + _app_block("function renderStoppedState() {", "// -- The language switch (DESIGN.md 13.2, 13.3)"))
    prelude = (
        "const asked = []; const appEl = { innerHTML: '' }; const handlers = {};\n"
        "window.confirm = (q) => { asked.push(q); return false; };\n"
        "global.document = { getElementById: (id) => id === 'app' ? appEl : id === 'shutdown-btn'"
        " ? { addEventListener: (e, fn) => { handlers[e] = fn; } } : null };\n"
        "let pollTimer = null; function closePanel() {}\n"
    )
    expr = f"(function () {{ {code} renderStoppedState(); handlers.click(); return [appEl.innerHTML, asked]; }})()"
    if saved is None:
        page, asked = _node("global.window = {};\n" + prelude + f"process.stdout.write(JSON.stringify({expr}));\n")
    else:
        page, asked = _with_i18n(expr, saved=saved, prelude=prelude)
    if saved == "en":
        assert page == ('<main id="main"><div class="stopped-note"><h2>Service stopped</h2>'
                        "<p>The local service has stopped. You can close this page.</p>"
                        "<p>To look again, double-click Research OS.app or run <code>researchos serve</code> in Terminal.</p>"
                        "</div></main>")
        assert asked == ["Stop the local service? Once it stops, this page can no longer be used."]
    else:
        assert page == _STOPPED_ZH
        assert asked == ["确定要停止本地服务吗？停止后这个页面将无法继续使用。"]


_NOW = "2026-10-11T15:00:00"
_MEMBER_SHARE = {"shared": "/x", "reachable": True, "role": "member", "joined": {"synced": "2026-10-11T08:00:00"},
                 "verification": "verified", "refused": [], "behind": False}
_PI_SHARE = {"shared": "/x", "reachable": True, "role": "pi", "last_snapshot": {"at": "2026-10-10T18:00:00"},
             "pending_publish": {"count": 2}}
_WHEN_CASES = ["2026-10-11T09:05:00", "2026-10-10T23:30:00", "2026-10-04T12:00:00", "2025-03-01T12:00:00",
               "2026-01-31T07:00:00", "not a date"]
_SHARE_CASES = [
    _MEMBER_SHARE, {**_MEMBER_SHARE, "behind": True}, {**_MEMBER_SHARE, "verification": "no_tool"},
    {**_MEMBER_SHARE, "reachable": False}, {**_MEMBER_SHARE, "refused": ["s-3"]},
    {**_MEMBER_SHARE, "joined": {"synced": None}, "verification": "no_tool"},
    _PI_SHARE, {**_PI_SHARE, "pending_publish": {"count": 0}, "last_snapshot": {"at": "2026-10-04T09:00:00"}},
    {**_PI_SHARE, "last_snapshot": None}, {"shared": None},
]


def _desk_lines(saved: str | None) -> list:
    """desk.js's dates and the shared folder's line, with /i18n.js loaded
    AFTER desk.js (t() is looked up at call time), or with no /i18n.js."""
    desk_js = (WEBAPP / "desk.js").read_text(encoding="utf-8")
    expr = (f"(function () {{ const now = new Date({json.dumps(_NOW)}); const D = window.RODesk;"
            f" return [{json.dumps(_WHEN_CASES)}.map((a) => D._whenWords(a, now)),"
            f" {json.dumps(_SHARE_CASES)}.map((s) => D._shareLineText(s, now))]; }})()")
    if saved is None:
        return _node("global.window = {};\n" + desk_js + f"\nprocess.stdout.write(JSON.stringify({expr}));\n")
    return _with_i18n(expr, saved=saved, prelude=desk_js + "\n")


_DESK_LINES_ZH = [
    ["今天 09:05", "昨天 23:30", "10月4日", "2025年3月1日", "1月31日", ""],
    ["已同步 今天 08:00", "有新的官方版本", "未验证 · 已同步 今天 08:00", "公盘现在打不开", "公盘上有未通过验证的版本",
     "未验证 · 已同步", "有改动尚未发布 · 已发布 昨天 18:00", "已发布 10月4日", "还没有发布过", ""],
]


@needs_node
@pytest.mark.parametrize("saved", [None, "zh"])
def test_13_8_the_sidebar_lines_in_chinese_are_as_before(saved):
    assert _desk_lines(saved) == _DESK_LINES_ZH


@needs_node
def test_13_5_the_sidebar_lines_in_english_dates_and_the_shared_folder():
    """13.5's dates (Today 09:05, Yesterday 23:30, 4 Oct, 1 Mar 2025); the
    shared folder's line per 13.4, its date lower case inside the line."""
    assert _desk_lines("en") == [
        ["Today 09:05", "Yesterday 23:30", "4 Oct", "1 Mar 2025", "31 Jan", ""],
        ["Synced today 08:00", "A new official version is available", "Unverified · Synced today 08:00",
         "The shared folder cannot be opened right now", "The shared folder holds a version that failed verification",
         "Unverified · Synced", "Changes not yet published · Published yesterday 18:00", "Published 4 Oct",
         "Not published yet", ""],
    ]


_SIDEBAR_PRELUDE = """
function fakeEl() {
  return { textContent: "", title: "", kids: [], dataset: {}, hidden: false,
    classList: { toggle(c, on) { this.owner.hidden = !!on; } },
    append(...xs) { this.kids.push(...xs); } };
}
function els() {
  const out = {};
  for (const id of ["side-count-proposals", "share-line", "me-line", "new-attempt-btn"]) {
    out[id] = fakeEl();
    out[id].classList.owner = out[id];
  }
  return out;
}
let shown = els();
global.document = { getElementById: (id) => shown[id] || null, querySelectorAll: () => [] };
global.mk = (tag, cls, text) => ({ tag, cls, textContent: text });
global.state = { noProject: false, dataEpoch: 0 };
"""

_SIDEBAR_CASES = """
[
  { mode: "pi", me: { id: "li@example.com", name: "Li" }, share: { shared: "/OneDrive/proj", reachable: false },
    pending: 2 },
  { mode: "pi", me: { id: "li@example.com", name: "Li" }, share: null, pending: 1 },
  { mode: "member", me: { id: "wang@example.com", name: "Wang" }, share: { shared: "/x", reachable: false }, pending: 3 },
  { mode: "none", me: { id: "zhao@example.com" }, share: null, pending: 0 },
  { mode: "single", me: null, share: null, pending: 0 },
].map((c) => {
  shown = els();
  const ctx = window.RODesk.context();
  ctx.mode = c.mode; ctx.share = c.share; ctx.me = c.me ? { me: c.me } : null;
  ctx.proposals = { proposals: Array.from({ length: c.pending }, () => ({ state: "pending" })) };
  window.RODesk.renderSidebar();
  return [shown["side-count-proposals"].textContent, shown["side-count-proposals"].title,
          shown["share-line"].textContent, shown["share-line"].title,
          shown["me-line"].kids.map((k) => [k.cls, k.textContent]), shown["me-line"].title];
})
"""


def _sidebar(saved: str | None, cases: str = _SIDEBAR_CASES) -> list:
    desk_js = (WEBAPP / "desk.js").read_text(encoding="utf-8")
    if saved is None:
        return _node("global.window = {};\n" + _SIDEBAR_PRELUDE + desk_js
                     + f"\nprocess.stdout.write(JSON.stringify({cases}));\n")
    return _with_i18n(cases, saved=saved, prelude=_SIDEBAR_PRELUDE + desk_js + "\n")


_SIDEBAR_ZH = [
    ["2", "2 条提议等你批准", "公盘现在打不开", "公盘：/OneDrive/proj", [["me-name", "Li"], ["me-role", "PI"]], "li@example.com"],
    ["1", "1 条提议等你批准", "", "", [["me-name", "Li"], ["me-role", "PI"]], "li@example.com"],
    ["", "", "公盘现在打不开", "公盘：/x", [["me-name", "Wang"], ["me-role", "成员"]], "wang@example.com"],
    ["", "", "", "", [["me-name", "zhao@example.com"], ["me-role", "尚未加入团队"]], "zhao@example.com"],
    ["", "", "", "", [["me-unset", "还没有设置你是谁"]], "在「成员」一栏里设置"],
]


@needs_node
@pytest.mark.parametrize("saved", [None, "zh"])
def test_13_8_who_i_am_and_the_count_in_chinese_are_as_before(saved):
    assert _sidebar(saved) == _SIDEBAR_ZH


@needs_node
def test_13_4_who_i_am_and_the_count_in_english():
    """The role word (PI stays PI; 成员 as a role is “Member”), the count's
    hover title with its plural, the shared folder's hover title; the
    person's name and id as written."""
    assert _sidebar("en") == [
        ["2", "2 proposals awaiting your approval", "The shared folder cannot be opened right now",
         "Shared folder: /OneDrive/proj", [["me-name", "Li"], ["me-role", "PI"]], "li@example.com"],
        ["1", "1 proposal awaiting your approval", "", "", [["me-name", "Li"], ["me-role", "PI"]], "li@example.com"],
        ["", "", "The shared folder cannot be opened right now", "Shared folder: /x",
         [["me-name", "Wang"], ["me-role", "Member"]], "wang@example.com"],
        ["", "", "", "", [["me-name", "zhao@example.com"], ["me-role", "Not in the team yet"]], "zhao@example.com"],
        ["", "", "", "", [["me-unset", "Who you are is not set yet"]], "Set it in “Members”"],
    ]


# -- 13.7: the desk (T3) ------------------------------------------------------------------------------------------

#: A stand-in DOM for the whole desk under node: an element keeps its class,
#: title and children in order (text as strings); app.html's helpers stubbed.
#: Each rendered desk is one line per element holding words: "class: words".
_DESK_DOM = r"""
function el(tag, cls, text) {
  const e = { tag, className: cls || "", title: "", dataset: {}, disabled: false, kids: [],
    get textContent() { return this.kids.map((k) => (typeof k === "string" ? k : k.textContent)).join(""); },
    set textContent(v) { this.kids = [String(v)]; },
    set innerHTML(v) { this.kids = v ? ["<html>" + v] : []; },
    appendChild(c) { this.kids.push(c); return c; }, append(...cs) { this.kids.push(...cs); }, addEventListener() {},
    querySelector(sel) {
      const walk = (n) => { for (const k of n.kids) if (typeof k !== "string") {
        if (k.className.split(" ").includes(sel.slice(1))) return k; const f = walk(k); if (f) return f; } return null; };
      return walk(this);
    }, classList: { toggle() {} } };
  if (text != null) e.textContent = text;
  return e;
}
global.mk = (tag, cls, text) => el(tag, cls, text);
global.mkButton = (label, cls) => el("button", cls || "btn btn-small", label);
global.document = { getElementById: () => null, querySelectorAll: () => [] };
global.state = { noProject: false, dataEpoch: 0, currentId: "p-1" };
global.views = {};
global.ownMark = (item) => item && item.officialness && item.officialness.state !== "official"
  ? el("span", "own-mark", item.officialness.label) : null;
global.setStatus = (node, kind, text) => { node.textContent = text; };
global.renderViewFailure = () => {};
global.ROTeam = { syncWrites: () => "[team.js]", openProposal() {}, differenceBlock: () => el("div", "diff") };
global.RONode = { openNode() {} };
function dump(node, out) {
  const own = node.kids.filter((k) => typeof k === "string").join("");
  const all = node.kids.map((k) => (typeof k === "string" ? k : "[" + k.className + "]")).join("");
  if (own || node.title) out.push(node.className + ": " + all + (node.title ? " (title: " + node.title + ")" : ""));
  node.kids.forEach((k) => { if (typeof k !== "string") dump(k, out); });
  return out;
}
async function renderDesk(c) {
  const answers = { "/api/me": c.me, "/api/proposals": c.proposals, "/api/share": c.share, "/api/share/sync-preview": {} };
  global.apiGet = async (path) => (path.startsWith("/api/desk") ? c.desk : answers[path]);
  const host = el("div", "host");
  await window.RODesk.loadDesk(host, false);
  return dump(host, []);
}
"""

#: The researcher's own words in the fixture (13.1: shown as written in either language).
_THEIRS = ["新路线", "TopicShift 波动", "伪协整", "成立", "放弃了", "先补稳健性", "这里的缺失值要先处理"]


def _desk_cases(lang: str) -> list[dict]:
    """Every kind of desk row and every desk layout (PI, member behind, up to
    date, without the shared folder, not in the team, a single person) with
    /api/desk's words as the engine sends them in `lang`."""
    def w(zh: str, en: str) -> str:
        return en if lang == "en" else zh

    def st(status: str, label: str, words: str = "") -> dict:
        return {"status": status, "label": label, "words": words}

    who = {"approved_by": [], "versions": 1, "notes": []}
    changes = [
        {**who, "kind": "file", "path": "steps/16-build.py", "at": "2024-03-01T12:00:00", "who": ["Li", w("最初的研究者", "Original researcher")],
         "approved_by": ["Wang"], "versions": 3, "notes": ["dropna first"], "what_code": "lines", "uncommitted": True,
         "what": w("增加 12 行，删去 1 行", "12 lines added, 1 line removed"),
         "officialness": {"state": "mine", "label": w("我的改动，尚未提交", "My change, not yet proposed")}},
        {**who, "kind": "file", "path": "figs/fig3.png", "at": "2024-03-01T11:00:00", "who": ["Li"], "what_code": "new_file",
         "what": w("新文件", "New file"), "exists": False},
        {**who, "kind": "attempt", "node": "a16", "number": "16", "title": "TopicShift 波动", "at": "2024-03-01T10:00:00",
         "who": ["Li"], "what_code": "verdict_changed", "what": w("判决改了", "Verdict changed"),
         "old_status": st("pending", "待定"), "new_status": st("abandoned", "已放弃", "伪协整")},
        {**who, "kind": "attempt", "node": "a18", "number": "18", "title": "New route", "at": "2024-03-01T09:00:00",
         "who": ["Wang"], "approved_by": ["Li"], "what_code": "new_attempt", "what": w("新增尝试", "New attempt"),
         "new_status": st("labelled", "成立", "robust")},
        {**who, "kind": "attempt", "node": "a19", "number": "19", "at": "2024-03-01T08:00:00", "who": ["Li"], "versions": 2,
         "what_code": "result_changed", "what": w("结果改了", "Result changed"), "result": ""},
        {**who, "kind": "decision", "node": "a16", "number": "16", "at": "2024-02-28T12:00:00", "act": "approved",
         "proposal_kind": "verdict_change", "who": ["Wang"], "approved_by": ["Li"], "verdict_status": st("other", "其它", "?"),
         "notes": ["agreed"]},
        {**who, "kind": "decision", "node": "a17", "number": "17", "title": "Plot", "at": "2024-02-27T12:00:00",
         "act": "abandoned", "who": [w("最初的研究者", "Original researcher")], "verdict_status": st("abandoned", "已放弃"),
         "reason": "spurious"},
        {**who, "kind": "decision", "at": "2024-02-26T12:00:00", "act": "approved", "proposal_kind": "files", "who": ["Wang"],
         "approved_by": ["Li"]},
        {"kind": "note", "path": "steps/17-plot.py", "at": "2024-02-25T12:00:00", "who": ["Wang"], "text": "这里的缺失值要先处理"},
        {"kind": "note", "path": None, "label": None, "at": "2024-02-23T12:00:00", "who": ["Wang"], "text": "?"},
    ]
    breaks = [{"kind": "no_generating_script", "label": w("没有找到生成它的脚本", "No script that generates it was found"),
               "path": "figs/fig1.png", "repair": w("补上来源", "Add source")},
              {"kind": "data_missing", "label": w("数据文件不在磁盘上", "The data file is not on disk"), "path": "data/raw.csv"}]
    busy = {"since": "2024-02-20T12:00:00", "since_default": False, "changes": changes, "more_changes": 4, "review": 1497,
            "results": [{"path": "figs/fig1.png", "exists": False, "review": 2, "drafts": ["paper.md", "notes.md"],
                         "breaks": breaks, "officialness": {"state": "proposed", "label": w("提议中", "Proposed")}},
                        {"path": "figs/fig2.png", "review": 1, "drafts": [], "breaks": []}],
            "abandoned": [{"number": "16", "title": "TopicShift 波动", "verdict_status": st("abandoned", "已放弃", "伪协整"),
                           "result": "t=0.4", "reason": "spurious"},
                          {"number": "", "verdict_status": st("abandoned", "放弃了"),
                           "officialness": {"state": "approved", "label": w("已批准，待同步", "Approved, awaiting sync")}}]}
    quiet = {"since_default": True, "default_days": 7, "changes": [], "results": [], "review": 0, "abandoned": []}
    proposals = [
        {"kind": "attempt_new", "state": "pending", "by": "wang@example.com", "at": "2024-03-01T12:00:00",
         "attempt": {"number": "18", "description": "新路线"}, "files": [{"path": "steps/18.py"}],
         "outcomes": [{"act": "discussed", "text": "先补稳健性"}]},
        {"kind": "verdict_change", "state": "conflict", "by": "wang@example.com", "at": "2024-03-01T11:00:00", "files": [{"path": "a/x.csv"}, {"path": "b/y.csv"}],
         "verdict": {"number": "16", "old_status": st("pending", "待定"), "new_status": st("abandoned", "已放弃"), "abandons": True}},
        {"kind": "variable_version", "state": "returned", "at": "2024-03-01T10:00:00", "variable": {"card": "log_ret", "version": 2},
         "outcomes": [{"act": "returned", "text": "redo it"}], "sentence": "Redefine log_ret"},
        {"kind": "files", "state": "approved", "at": "2024-03-01T09:00:00", "files": [{"path": "a/1.py"}, {"path": "a/2.py"}, {"path": "a/3.py"}]},
        {"id": "p5", "kind": "odd", "state": "superseded", "at": "2024-03-01T08:00:00"},
        {"kind": "verdict_change", "state": "returned", "at": "2024-03-01T07:00:00",
         "verdict": {"number": "#17", "old_status": st("current", "现行"), "new_status": st("labelled", "成立")}},
    ]
    me = {"me": {"id": "li@example.com", "name": "Li"}}
    team = [{"id": "wang@example.com", "name": "Wang"}]
    member = {"shared": "/x", "reachable": True, "role": "member", "team": team,
              "last_snapshot": {"at": "2024-03-01T12:00:00", "seq": 1497, "note": "fixes"}}
    return [
        {"me": me, "proposals": {"role": "pi", "proposals": proposals}, "share": {"shared": "/x", "role": "pi", "team": team}, "desk": busy},
        {"me": me, "proposals": {"role": "pi", "proposals": []}, "share": {}, "desk": quiet},
        {"me": me, "proposals": {"role": "member", "proposals": proposals}, "share": {**member, "behind": True}, "desk": {**quiet, "default_days": 1}},
        {"me": me, "proposals": {"role": "member", "proposals": []}, "share": {**member, "last_snapshot": {"seq": 3}}, "desk": quiet},
        {"me": me, "proposals": {"role": "member", "proposals": []}, "share": {"shared": "/x", "reachable": True}, "desk": quiet},
        {"me": me, "proposals": {"role": "member", "proposals": []}, "share": {"shared": "/x", "reachable": False}, "desk": quiet},
        {"me": me, "proposals": {"role": "member", "proposals": []}, "share": {"shared": None}, "desk": quiet},
        {"me": me, "proposals": {"role": "none", "proposals": []}, "share": {}, "desk": quiet},
    ]


def _desks(saved: str | None, desk_js: str | None = None) -> list[list[str]]:
    """Every desk of the fixture rendered by desk.js (or `desk_js`), with
    /i18n.js in the saved language, or alone (None)."""
    i18n = "" if saved is None else f"window.ROI18N_SAVED = {json.dumps(saved)};\n" + _i18n_source()
    cases = json.dumps(_desk_cases(saved or "zh"), ensure_ascii=False)
    return _node("global.window = {};\n" + i18n + _DESK_DOM + (desk_js or (WEBAPP / "desk.js").read_text(encoding="utf-8"))
                 + f"\n(async () => {{ const out = []; for (const c of {cases}) out.push(await renderDesk(c));"
                 " process.stdout.write(JSON.stringify(out)); })();\n")


#: sha256 of the Chinese desks above as rendered by desk.js before this increment (commit 041c510;
#: `_desks(None, <that file>)`): 13.8 #1, a Chinese screen reads byte for byte as before.
_DESK_ZH_SHA256 = "188bf3eca8bb323c481b4118e7186da503a6b2a9748296bfedb0c3db80ce5dda"


@needs_node
@pytest.mark.parametrize("saved", [None, "zh"])
def test_13_8_the_desk_in_chinese_is_as_before(saved):
    import hashlib

    desks = _desks(saved)
    assert hashlib.sha256(json.dumps(desks, ensure_ascii=False).encode("utf-8")).hexdigest() == _DESK_ZH_SHA256
    assert "desk-what: 判决 [verdict-inline] 改为 [verdict-inline]" in desks[0]
    assert "desk-sub: 1497 条判断的依据变了，等你复核。[desk-link]" in desks[0]


@needs_node
def test_13_7_the_desk_in_english():
    """Every word the desk writes is English (13.4's words, 13.5's dates,
    numbers and plurals, 12.13's sentences without arrows); the
    researcher's own words, a [verdict_labels] word among them, as
    written; the page picks a row's form by what_code, never by words."""
    desks = _desks("en")
    lines = [line for desk in desks for line in desk]
    left = [line for line in lines if CJK.search(re.sub("|".join(map(re.escape, _THEIRS)), "", line))]
    assert left == [] and not any(FORBIDDEN.search(line) for line in lines)
    for line in [
        "desk-title: Awaiting your approval", "desk-when: 1 Mar 2024", "status-label tone-plain: New attempt",
        "desk-what strong: New attempt #18: 新路线, with 1 file",
        "desk-what strong: #16: verdict changed from “Pending” to “Abandoned”, abandoning the route, with 2 files",
        "desk-sub: Last looked: 20 Feb 2024", "desk-link mono: steps/16-build.py (title: Open this file)",
        "desk-what: 12 lines added, 1 line removed (not committed yet)", "own-mark: My change, not yet proposed",
        "desk-meta: Li, Original researcher · Approved by Wang · Changed 3 times in this period",
        "desk-what: Verdict changed from [verdict-inline] to [verdict-inline]", "status-label tone-ochre: Pending",
        "desk-what: New attempt, verdict [verdict-inline]", "status-label tone-plain: 成立",
        "desk-what: Result changed to: (empty)", "status-label tone-plain: Other", "desk-note: Reason: spurious",
        "desk-what: Approved a proposal to change a verdict", "desk-meta: Proposed by Wang · Approved by Li",
        "desk-what: Note on", "desk-link: a record", "desk-repair: Fix with “Add source”",
        "desk-break: The data file is not on disk: data/raw.csv",
        "desk-break: 2 linked judgments in its sources need review[desk-link]", "desk-meta: Cited by paper.md, notes.md",
        "desk-sub: The basis of 1,497 judgments changed; they await your review.[desk-link]",
        "status-label tone-gray: 放弃了", "desk-link: See their basis at the time in Research routes",
    ]:
        assert line in desks[0], line
    assert desks[1] == ["desk-title: Awaiting your approval", "desk-empty: No proposals await your approval.",
                        "desk-title: Changes since you last looked",
                        "desk-sub: When you last looked is not recorded yet, so this lists the changes of the past 7 days.",
                        "desk-empty: No changes in this period.", "desk-title: Outputs whose basis changed",
                        "desk-empty: Every output still has its sources.", "desk-title: Abandoned routes",
                        "desk-empty: No route has been abandoned yet."]
    for line in ["desk-meta: Awaiting PI approval · The PI says: 先补稳健性", "desk-note plain: Variable log_ret: definition version 2",
                 "desk-meta: Returned: redo it", "desk-what strong: 3 files: 1.py, 2.py and others", "status-label tone-plain: Proposal",
                 "desk-what strong: #17: verdict changed from “Current” to “成立”", "desk-title: Official version updated",
                 "desk-sub: The PI published version 1,497 (1 Mar 2024): fixes", "btn: Sync",
                 "desk-sub: When you last looked is not recorded yet, so this lists the changes of the past 1 day."]:
        assert line in desks[2], line
    assert "desk-empty: Your working copy is the latest official version (version 3)." in desks[3]
    assert "desk-empty: The PI has not published an official version yet." in desks[4]
    assert "desk-alone: You are not a member of this project yet: ask the PI to add you to the team, then sync" in desks[7]
    assert "item.what ===" not in (WEBAPP / "desk.js").read_text(encoding="utf-8")  # 13.6


#: Payload keys that hold the researcher's text or a fixed name (13.1), shown as written in either language.
_THEIR_KEYS = {"title", "words", "result", "reason", "notes", "text", "path", "node", "id", "drafts", "number"}


def _strings(value: Any, key: str = ""):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _strings(v, k)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v, key)
    elif isinstance(value, str):
        yield key, value


def test_13_7_api_desk_speaks_the_requests_language(tmp_path):
    """/api/desk without the header (the command line's and MCP's case) and
    with zh is the Chinese it was; with en every sentence the engine
    composes is English, the codes are the same, and nothing Chinese is
    left but the researcher's text and the verdict vocabulary (13.1)."""
    from researchos.ingest import attempts as attempts_ingest
    from test_v8_u2 import _backdate

    root = _project(tmp_path / "proj")
    _backdate(root)
    (root / "steps" / "16-build.py").write_text((root / "steps" / "16-build.py").read_text() + "df = df.dropna()\n")
    (root / "data" / "panel.csv").write_text(_csv(97, ("id", "x", "log_ret")))
    (root / "figs" / "fig3.png").write_bytes(b"\x89PNG fig3")
    (root / "map.md").write_text(_map("☠️ 伪协整"))
    _scan(root)
    since = "2000-01-01T00:00:00%2B00:00"
    httpd, thread = _serving(root)
    try:
        got = [json.loads(_Live(httpd).call("GET", f"/api/desk?since={since}", headers=h)[2])
               for h in ({}, {"X-ResearchOS-Lang": "zh"}, {"X-ResearchOS-Lang": "en"})]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    zh, zh_asked, en = got
    assert zh == zh_asked
    by = [{c["node"]: c for c in payload["changes"]} for payload in (zh, en)]
    words = [{n: (c["what_code"], c["what"], c["who"]) for n, c in b.items()} for b in by]
    assert words[0]["script:steps/16-build.py"] == ("lines", "增加 1 行", ["最初的研究者"])
    assert words[1]["script:steps/16-build.py"] == ("lines", "1 line added", ["Original researcher"])
    assert words[0]["dataset:data/panel.csv"] == ("table", "行数从 96 增至 97，新增列 log_ret", ["最初的研究者"])
    assert words[1]["dataset:data/panel.csv"] == ("table", "Rows from 96 to 97; column added: log_ret", ["Original researcher"])
    assert words[1]["figure:figs/fig3.png"][:2] == ("new_file", "New file")
    assert words[1]["attempt:map.md#16"][:2] == ("verdict_changed", "Verdict changed")
    assert words[0]["attempt:map.md#16"][:2] == ("verdict_changed", "判决改了")
    assert {n: w[0] for n, w in words[0].items()} == {n: w[0] for n, w in words[1].items()}
    assert [(b["label"], b["repair"]) for r in zh["results"] for b in r["breaks"]] == [("没有找到生成它的脚本", "补上来源")]
    assert [(b["label"], b["repair"]) for r in en["results"] for b in r["breaks"]] == [
        ("No script that generates it was found", "Add source")]
    assert en["officialness_mode"]["labels"] == {"mine": "My change, not yet proposed", "proposed": "Proposed",
                                                 "approved": "Approved, awaiting sync"}
    vocabulary = set(attempts_ingest.DEFAULT_VERDICT_LABELS.values()) | {attempts_ingest.OTHER_VERDICT_LABEL}
    left = [(k, s) for k, s in _strings(en) if CJK.search(s) and k not in _THEIR_KEYS and not (k == "label" and s in vocabulary)]
    assert left == []


def test_13_7_a_git_projects_desk_and_the_officialness_words_in_english(tmp_path):
    """Edits git has not seen yet are 「本机」's, "This Mac" in English; a
    member's own work is marked in the page's language (13.4)."""
    from researchos import desk, officialness
    from test_v8_phase2 import _conn, _git

    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q")
    _project(root)
    _git(root, "add", "steps", "data", "figs", "paper.md", "map.md")
    _git(root, "commit", "-q", "-m", "first", date="2026-01-01T10:00:00+00:00")
    (root / "paper.md").write_text((root / "paper.md").read_text() + "\nMore.\n")
    conn = _conn(root)
    try:
        out = [desk.desk(conn, root, "2026-06-01T00:00:00+00:00", lang=lang) for lang in ("zh", "en")]
    finally:
        conn.close()
    paper = [next(c for c in o["changes"] if c.get("path") == "paper.md") for o in out]
    assert [(p["what_code"], p["what"], p["who"]) for p in paper] == [("changed", "内容有改动", ["本机"]),
                                                                      ("changed", "Content changed", ["This Mac"])]
    assert officialness.mark(("mine", None)) == {"state": "mine", "label": "我的改动，尚未提交"}
    assert officialness.mark(("proposed", "p-1"), "en") == {"state": "proposed", "label": "Proposed", "proposal": "p-1"}


# -- 13.7: the frame, part B -- the switcher and its menu, the GitHub lines, the top bar's button ----------------

#: A stand-in DOM for the project menu under node, run with app.html's own mk, basename and menu
#: block: an element keeps its class, title, disabled state and children; innerHTML only clears
#: (the drawn check mark). The menu reads back as [class, label, flag, title, disabled] per item
#: and [class, words] per other element holding words.
_MENU_DOM = r"""
function el(tag) {
  return { tag, className: "", title: "", disabled: false, kids: [], attrs: {},
    get textContent() { return this.kids.map((k) => (typeof k === "string" ? k : k.textContent)).join(""); },
    set textContent(v) { this.kids = [String(v)]; },
    set innerHTML(v) { this.kids = []; },
    appendChild(c) { this.kids.push(c); return c; }, append(...cs) { this.kids.push(...cs); },
    setAttribute(n, v) { this.attrs[n] = String(v); }, addEventListener() {},
    querySelector(sel) {
      const walk = (n) => { for (const k of n.kids) if (typeof k !== "string") {
        if (k.className.split(" ").includes(sel.slice(1))) return k; const f = walk(k); if (f) return f; } return null; };
      return walk(this);
    },
    classList: { contains: () => false, add() {}, remove() {}, toggle() {} } };
}
const ids = { "project-menu-btn": el("button"), "project-menu": el("div"), "project-menu-wrap": el("span") };
global.document = { getElementById: (id) => ids[id] || null, createElement: el };
global.state = {};
function read(menu) {
  const out = [];
  const visit = (n) => {
    if (typeof n === "string") return;
    const part = (cls) => { const k = n.kids.find((x) => typeof x !== "string" && x.className === cls); return k ? k.textContent : ""; };
    if (n.tag === "button") { out.push([n.className, part("pm-label"), part("pm-flag"), n.title, n.disabled]); return; }
    const own = n.kids.filter((k) => typeof k === "string").join("");
    if (own) out.push([n.className, own]);
    n.kids.forEach(visit);
  };
  menu.kids.forEach(visit);
  return out;
}
"""

_MENU_CASES = r"""
(async () => {
  const P = [
    { label: "我的论文", path: "/r/a", id: "p-1", initialized: true, available: true },
    { label: "旧项目", path: "/r/b", id: "p-2", initialized: true, available: false, missing: true },
    { label: "old", path: "/r/c", initialized: false, available: true },
  ];
  const GH = { linked: true, owner: "rce-test", repo: "demo", url: "https://github.com/rce-test/demo", message: "<engine>" };
  const out = [
    { noProject: true, projects: [], currentPath: "", currentEntry: null, projectLabel: "" },
    { projects: P, currentPath: "/r/a", currentId: "p-1", currentEntry: P[0], projectLabel: "我的论文", github: GH },
    { projects: P, currentPath: "/r/x", currentId: null, currentEntry: null, projectLabel: "x", github: GH,
      githubFetching: true },
  ].map((c) => {
    global.state = Object.assign({ noProject: false, dataEpoch: 0, currentBlocked: false, githubFetching: false }, c);
    projectMenuMode = "list";
    setProjectMenuLabel(c.projectLabel, c.currentPath);
    renderProjectMenu();
    return [projectMenuBtn.textContent, projectMenuBtn.title, read(projectMenu)];
  });
  // 「从 GitHub 获取最新状态」: its progress word, and back after a failure under the framing.
  global.state.githubFetching = false;
  global.apiPost = () => Promise.reject(new Error("offline"));
  global.renderBlockingError = (node, framing) => { node.textContent = framing; };
  const status = mk("div", "pm-status");
  const button = pmItem(t("从 GitHub 获取最新状态"), () => {});
  const pending = fetchGitHub(status, button);
  const during = button.querySelector(".pm-label").textContent;
  await pending;
  out.push([during, button.querySelector(".pm-label").textContent, status.textContent]);
  process.stdout.write(JSON.stringify(out));
})();
"""


def _menu(saved: str | None) -> list:
    """The switcher button and the menu, rendered by app.html's own code, with
    /i18n.js in the saved language or alone (None)."""
    code = (_app_block("// -- The interface language (DESIGN.md 13.6)", "// -- Small shared state")
            + _app_function("function mk(") + _app_function("function basename(")
            + _app_block("// -- The project menu (DESIGN.md 10.1, 10.4; task V6)",
                         "// -- 「推送到 GitHub…」 (DESIGN.md 11.3, task V7)"))
    i18n = "" if saved is None else f"window.ROI18N_SAVED = {json.dumps(saved)};\n" + _i18n_source()
    return _node("global.window = {};\n" + i18n + _MENU_DOM + code + _MENU_CASES)


def _menu_expected(w: dict[str, str]) -> list:
    """The three menus and the fetch's words, the screen's words given by `w`
    (the Chinese key when absent); the researcher's project names as written."""
    def x(key: str) -> str:
        return w.get(key, key)

    tail = [["pm-item", x("推送到 GitHub…"), "", "", False], ["pm-item", x("添加项目…"), "", "", False],
            ["pm-item", x("从公盘加入项目…"), "", "", False]]
    listed = [["pm-item", "旧项目", x("找不到文件夹"), "/r/b", True], ["pm-item pm-sub", x("选择新位置…"), "", "", False],
              ["pm-item", "old", x("未初始化"), "/r/c", True]]

    def github_lines(state: str, item: str, fetching: bool) -> list:
        return [["pm-github", x("GitHub：")], ["", "rce-test/demo"], ["pm-github-state", state],
                ["pm-item", item, "", "", fetching]]

    return [
        [x("还没有项目"), x("添加项目"), [["pm-empty", x("列表里还没有项目")]]
         + [["pm-item", x("推送到 GitHub…"), "", "", True]] + tail[1:]
         + [["pm-item", x(k), "", "", True] for k in ("重新扫描这个项目", "重命名显示名称…", "从列表中移除…")]],
        ["我的论文", "/r/a\n" + x("切换项目或管理列表"), [["pm-item", "我的论文", "", "/r/a", False], *listed,
         *github_lines("<engine>", x("从 GitHub 获取最新状态"), False), *tail]
         + [["pm-item", x(k), "", "", False] for k in ("重新扫描这个项目", "重命名显示名称…", "从列表中移除…")]],
        ["x", "/r/x\n" + x("切换项目或管理列表"), [["pm-item", "x", x("不在列表里"), "/r/x", False],
         ["pm-item", "我的论文", "", "/r/a", False], *listed,
         *github_lines(x("正在从 GitHub 获取…"), x("正在从 GitHub 获取…"), True), *tail,
         ["pm-item", x("重新扫描这个项目"), "", "", False], ["pm-item", x("重命名显示名称…"), "", "", True],
         ["pm-item", x("从列表中移除…"), "", "", False]]],
        [x("正在从 GitHub 获取…"), x("从 GitHub 获取最新状态"), "没能从 GitHub 获取"],
    ]


@needs_node
@pytest.mark.parametrize("saved", [None, "zh"])
def test_13_8_the_switcher_and_its_menu_in_chinese_are_as_before(saved):
    """13.8 #1: the switcher button, its hover title and every item and flag
    of the project menu with its GitHub lines read exactly as before."""
    assert _menu(saved) == _menu_expected({})


@needs_node
def test_13_7_the_switcher_and_its_menu_in_english():
    """The project line's switcher (its name, its hover title) and every item
    and flag of the menu in English, the GitHub line's own words too; the
    researcher's project names as written (13.1), the engine's sentence as
    the engine sent it (it asks in the page's language), and the failure's
    framing still Chinese (13.7: error sentences are their own increment)."""
    words = {
        "还没有项目": "No project yet", "添加项目": "Add project", "切换项目或管理列表": "Switch project or manage the list",
        "列表里还没有项目": "No projects in the list yet", "不在列表里": "Not in the list", "找不到文件夹": "Folder not found",
        "未初始化": "Not initialized", "选择新位置…": "Choose a new location…", "推送到 GitHub…": "Push to GitHub…",
        "添加项目…": "Add project…", "从公盘加入项目…": "Join a project from the shared folder…",
        "重新扫描这个项目": "Rescan this project", "重命名显示名称…": "Change display name…",
        "从列表中移除…": "Remove from the list…", "GitHub：": "GitHub: ", "正在从 GitHub 获取…": "Fetching from GitHub…",
        "从 GitHub 获取最新状态": "Fetch the latest state from GitHub",
    }
    got = _menu("en")
    assert got == _menu_expected(words)
    shown = json.dumps(got[:3], ensure_ascii=False)
    for theirs in ("我的论文", "旧项目"):
        shown = shown.replace(theirs, "")
    assert not CJK.search(shown)


_ADD_BUTTON_CASES = """
["pi", "member", "none", "single"].map((mode) => {
  shown = els();
  const ctx = window.RODesk.context();
  ctx.mode = mode; ctx.share = null; ctx.me = null; ctx.proposals = { proposals: [] };
  window.RODesk.renderSidebar();
  return shown["new-attempt-btn"].textContent;
})
"""


@needs_node
@pytest.mark.parametrize("saved", [None, "zh", "en"])
def test_13_7_the_top_bar_button_follows_the_language(saved):
    """The top bar's action button (13.7): 新增尝试, and 提议新的尝试 for a
    member (U4), in the screen's language; Chinese exactly as before."""
    got = _sidebar(saved, _ADD_BUTTON_CASES)
    if saved == "en":
        assert got == ["New attempt", "Propose a new attempt", "New attempt", "New attempt"]
    else:
        assert got == ["新增尝试", "提议新的尝试", "新增尝试", "新增尝试"]


def test_13_3_the_menu_wraps_its_english_words_and_keeps_chinese_as_it_was():
    """13.3 in the project menu, which keeps to the sidebar's width: on an
    English screen an action's label wraps and 「选择新位置…」 drops below its
    project; a project's own name keeps its ellipsis; nothing changes on a
    Chinese screen (the rules name <html lang="en">, which /i18n.js sets)."""
    css = re.search(r"<style>(.*?)</style>", APP_HTML.read_text(encoding="utf-8"), re.S).group(1)
    assert ".pm-label { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }" in css
    assert 'html[lang="en"] .pm-item[role="menuitem"] .pm-label { white-space: normal; }' in css
    assert 'html[lang="en"] .pm-row { flex-wrap: wrap; }' in css
    assert 'html[lang="en"] .pm-row .pm-sub { flex-basis: 100%; padding-left: 2.05rem; }' in css
    rules = re.findall(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", css, flags=re.S))
    assert [sel.strip() for sel, _b in rules if "lang=" in sel] == [
        'html[lang="en"] .pm-item[role="menuitem"] .pm-label', 'html[lang="en"] .pm-row', 'html[lang="en"] .pm-row .pm-sub']


_DATE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")


def _github_both(root: Path) -> tuple[str, str]:
    """The state's sentence in Chinese and English (dates masked); asked for
    nothing, it is the Chinese one, and every other field is the same."""
    zh, zh_asked, en = github.state(root), github.state(root, "zh"), github.state(root, "en")
    assert zh == zh_asked
    assert {k: v for k, v in zh.items() if k != "message"} == {k: v for k, v in en.items() if k != "message"}
    return _DATE.sub("<date>", zh["message"]), _DATE.sub("<date>", en["message"])


def test_13_7_the_github_state_in_both_languages(tmp_path, stand_in, linked):
    """The engine's sentence under the menu's GitHub line (11.2), composed in
    the language asked for (13.2, 13.6) with a singular and a plural (13.5):
    Chinese when nothing is asked, as the command line and a push's payload
    ask nothing; the same facts in both."""
    from test_v7_connections import _git

    fresh = tmp_path / "fresh"
    fresh.mkdir()
    _git(fresh, "init", "-q", "-b", "main")
    _git(fresh, "remote", "add", "origin", GITHUB_URL)
    assert _github_both(fresh) == ("还没有从 GitHub 获取过", "Not fetched from GitHub yet")
    assert _github_both(linked) == ("与 GitHub 一致", "In sync with GitHub")
    _git(linked, "commit", "-q", "--allow-empty", "-m", "one")
    assert _github_both(linked) == ("本地领先 1 个提交，落后 0 个（<date> 推送）", "Locally 1 commit ahead, 0 behind (pushed <date>)")
    _git(linked, "commit", "-q", "--allow-empty", "-m", "two")
    assert _github_both(linked) == ("本地领先 2 个提交，落后 0 个（<date> 推送）", "Locally 2 commits ahead, 0 behind (pushed <date>)")
    _colleague_pushes(tmp_path, stand_in)
    assert _DATE.sub("<date>", github.fetch(linked, lang="en")["message"]) == "Locally 2 commits ahead, 1 behind (fetched <date>)"
    assert _DATE.sub("<date>", github.fetch(linked)["message"]) == "本地领先 2 个提交，落后 1 个（<date> 获取）"
    _git(linked, "branch", "--unset-upstream")
    assert _github_both(linked) == ("GitHub 上没有可以对比的分支（<date> 获取）", "No branch on GitHub to compare with (fetched <date>)")

    unborn = tmp_path / "unborn"
    unborn.mkdir()
    _git(unborn, "init", "-q", "-b", "main")
    _git(unborn, "remote", "add", "origin", GITHUB_URL)
    _git(unborn, "fetch", "-q", "origin")
    _git(unborn, "remote", "set-head", "origin", "main")
    assert _github_both(unborn) == ("本地还没有提交（<date> 获取）", "No local commits yet (fetched <date>)")


def test_13_7_api_github_speaks_the_requests_language(tmp_path, stand_in, linked_server):
    """/api/github and /api/github/fetch compose their sentence in the
    language the page's request names; without the header, Chinese."""
    live, _work = linked_server
    asked = [live.raw("GET", "/api/github", h) for h in ({}, {"X-ResearchOS-Lang": "zh"}, {"X-ResearchOS-Lang": "en"})]
    assert [(status, st["message"]) for status, st in asked] == [(200, "与 GitHub 一致"), (200, "与 GitHub 一致"),
                                                                 (200, "In sync with GitHub")]
    _colleague_pushes(tmp_path, stand_in)
    status, st = live.raw("POST", "/api/github/fetch", {"Content-Type": "application/json", "X-ResearchOS-Lang": "en"}, {})
    assert status == 200 and (st["ahead"], st["behind"]) == (0, 1)
    assert _DATE.sub("<date>", st["message"]) == "Locally 0 commits ahead, 1 behind (fetched <date>)"
    status, st = live.raw("GET", "/api/github", {})
    assert status == 200 and _DATE.sub("<date>", st["message"]) == "本地领先 0 个提交，落后 1 个（<date> 获取）"
