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
from pathlib import Path
from typing import Any

import pytest

from researchos import literature, settings
from researchos.webapp import server

from test_v8_phase2 import _project, _scan
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
]
#: Chinese a converted surface may keep outside t(), with the reason (13.1:
#: fixed on-disk names, the researcher's vocabulary; 13.3: a language's own
#: name). Keys are the exact literal.
ALLOWED_CHINESE: dict[str, str] = {}

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
