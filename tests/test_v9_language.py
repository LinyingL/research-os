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

from researchos import github, history, literature, officialness, settings
from researchos.webapp import server

from test_v7_connections import GITHUB_URL, _colleague_pushes, linked, linked_server, stand_in  # noqa: F401 -- fixtures
from test_v8_1213 import GUIDE_EN, GUIDE_EN_CHINESE_LABELS
from test_v8_phase2 import _csv, _map, _project, _scan
from test_v8_u2 import _KEYWORDS_BEFORE, _REGEX_BEFORE, FORBIDDEN, _served_scripts
from test_webapp_macapp import SETTINGS_BYTES
from test_webapp_macapp import _english_table as shell_english_table

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
    ("node.js", None, None),
    ("team.js", "async function loadProposals(", "// One proposal drawn into"),
    ("team.js", "function backBar() {", "\n  }\n"),
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
    # The desk's 「官方版本有更新」 block (13.7: the desk entirely): what 同步 will write and what it
    # did, the difference beside an .official copy -- helpers 成员 shares (13.7: one helper, every page).
    ("team.js", "function packetWords(c) {", "\n  }\n"),
    ("team.js", "function syncWrites(p) {", "\n  }\n"),
    ("team.js", "function differenceBlock(f, open) {", "\n  }\n"),
    ("team.js", "function syncDone(res) {", "\n  }\n"),
    ("team.js", "function syncFlash(res) {", "\n  }\n"),
    ("node.js", "function diffBlock(rows, truncated) {", "\n  }\n"),
    # V10 (DESIGN.md 14.6): the desk's route map, all of it.
    ("routemap.js", None, None),
    # V10 (14.3, 14.6): 来龙去脉, which the desk's side panel draws with the node page's renderer.
    ("node.js", "function renderTraceSection(host, page, opts) {", "\n  }\n"),
    ("node.js", "function viaWord(via) {", "\n  }\n"),
    ("node.js", "function traceItem(item, col, open) {", "\n  }\n"),
    ("node.js", "function gapBox(page, gap, bare) {", "\n  }\n"),
    ("node.js", "function drawLines(grid) {", "\n  }\n"),
    # V10 (14.4, 14.6): 待复核, which the desk's column (and the top bar's count) opens in the side
    # panel -- the judgments, their links and bases in words, the acts, and the variable cards' part.
    ("app.html", 'btn.className = "review-count-btn line-part has";', "line.append(btn);"),
    ("app.html", "function verdictWord(verdict) {", "\n}\n"),
    ("app.html", "function whenText(entry) {", "\n}\n"),
    ("app.html", "function basisLines(basis) {", "\n}\n"),
    ("app.html", "function endName(id, title) {", "\n}\n"),
    ("app.html", "function refEndName(id, title) {", "\n}\n"),
    ("app.html", "function linkText(key) {", "\n}\n"),
    ("app.html", "function historyEntryText(entry) {", "\n}\n"),
    ("app.html", "// -- 待复核 (9.6)", "// -- 记录 (9.2"),
    ("app.html", "function coverageLine(item, kind) {", "\n}\n"),
    ("app.html", "async function variableAction(", "\n}\n"),
    ("app.html", "function cardReviewMember(review, standalone) {", "\n}\n"),
    ("app.html", "function renderCardReviewGroups(host, cardItems) {", "\n}\n"),
    # V10 D3 (14.3, 14.6): a proposal decided in the desk's panel (team.js renderProposalInto, which
    # 提议 draws too) -- its head, answers, what approving does, what it carries, the files, the
    # variable versions it uses, the answers to give -- and a member's draft (composeInto).
    *(("team.js", head, "\n  }\n") for head in (
        "function fileAction(action) {", "function fileState(state) {", "function attestedWord(answer) {",
        "function verifyWord(state) {", "function sizeWords(n) {", "function stateLabel(p) {",
        "function signatureWords(v) {", "function approveSentence(res) {", "function carryWords(kind) {",
        "function sendWrites(draft, me) {", "function failed(el, framing, err) {", "function phrase(host, text, nodes) {",
        "async function renderProposalInto(", "function renderCarried(page, full) {", "function fileList(files, changes) {",
        "function renderWhatApprovingDoes(page, full) {", "function renderUses(page, uses, open) {",
        "function renderDecide(page, full, team, onDone) {", "async function composeInto(",
        "function renderDraft(host, body, draft, status) {", "function removalList(coming) {")),
    # V10 D4 (14.5, 14.6): how an attempt's row moved and where a version came from, which the
    # desk's panel says in its timeline of what changed since the last visit.
    ("node.js", "function verdictMove(prev, cur) {", "\n  }\n"),
    ("node.js", "function sourceWords(v) {", "\n  }\n"),
    # V11 (15.4, 15.5): a Stata .dta file in the panel -- rows by variables, its label, the table.
    ("app.html", "function dtaPreview(info) {", "\n}\n"),
    # V12 (16): 新增尝试's 生成草稿 -- attemptDraftBlock, llmSettingBox (「模型服务」) and their helpers
    # (the language, what became of each cell, the refusals in words, the chevron).
    ("app.html", "// -- 「根据所选文件生成草稿」 (DESIGN.md 16)", "// Builds the form from the (now current) records and config."),
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
    # 13.1: a member's area on the shared drive, 提议/<their e-mail>/, a fixed on-disk name shown as
    # it is (with its meaning in brackets in English); the engine names it, this is its stand-in.
    "提议/你的邮箱/": "13.1: the 提议 folder's fixed name on the shared drive",
    "提议/": "13.1: the 提议 folder's fixed name, before a member's id (team.js sendWrites)",
    "已发布/outcomes/": "13.1: the 已发布 folder's fixed name, where the PI's answers are written",
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


def _t_argument(src: str, start: int, quote: str, raw: str) -> bool | None:
    """Whether the literal at `start` is the first argument of a t() call
    -- `t("…")`, `I.t('…')`, never `at("…")` -- and, if it is, whether it is
    the WHOLE argument (True) or the start of a concatenation (False)."""
    before = src[:start].rstrip()
    if quote == "`" or not before.endswith("t(") or re.search(r"[\w$]t\($", before[-3:]):
        return None
    after = src[start + len(raw) + 2:].lstrip()
    return after[:1] in (")", ",")


def t_calls(src: str) -> list[tuple[str, bool]]:
    """Each string literal passed to t() -- `t("…")`, `I.t('…')` -- with
    whether it is the WHOLE first argument (a `+` after it is a sentence
    built by concatenation, which English word order cannot reuse: 13.6)."""
    found = []
    for start, quote, raw in js_literals(src):
        whole = _t_argument(src, start, quote, raw)
        if whole is not None:
            found.append((_unescape(raw), whole))
    return found


def _paired_with_its_label(src: str, start: int, raw: str, text: str) -> bool:
    """A bare literal immediately followed by `, t(<the same text>)`: the
    code compared with the engine's label, beside its words for the screen
    (desk.js defaultVerdict, 13.1) -- the only bare Chinese a converted
    surface may hold by position."""
    m = re.match(r"\s*,\s*t\(\s*([\"'])((?:\\.|(?!\1).)*)\1\s*\)", src[start + len(raw) + 2:])
    return bool(m) and _unescape(m.group(2)) == text


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


def untranslated_in(where: str, src: str, table: dict[str, Any]) -> list[str]:
    """In one converted stretch, each Chinese literal judged by its own
    position: it is the whole first argument of t() with an English entry,
    or in ALLOWED_CHINESE, or a code paired with its own t() label; else it
    is reported -- not passed to t(), built by concatenation, or a key with
    no entry. (The same text passed to t() elsewhere excuses nothing.)"""
    found = []
    for start, quote, raw in js_literals(src):
        text = _unescape(raw)
        if not CJK.search(text) or text in ALLOWED_CHINESE:
            continue
        whole = _t_argument(src, start, quote, raw)
        if whole is None:
            if not _paired_with_its_label(src, start, raw, text):
                found.append(f"{where}: {text!r} is not passed to t()")
        elif not whole:
            found.append(f"{where}: t({text!r} + ...) is built by concatenation")
        elif text not in table:
            found.append(f"{where}: t({text!r}) has no English entry")
    return found


def untranslated(table: dict[str, Any]) -> list[str]:
    """In each CONVERTED surface: a Chinese literal that is not the whole
    argument of t() (and not in ALLOWED_CHINESE), and a key with no entry."""
    found = []
    for name, start, end in CONVERTED:
        src = _slice((WEBAPP / name).read_text(encoding="utf-8"), start, end)
        found += untranslated_in(f"{name} ({start})", src, table)
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
    # The script goes in on stdin, not as an argument: these scripts carry
    # whole page modules, and Linux caps a single argument at 128 KB.
    out = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, check=True)
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


@pytest.mark.parametrize("name", sorted(SETTINGS_BYTES))
def test_13_2_the_engine_reads_settings_bytes_as_the_shell_does(name):
    """13.2: one source of truth -- the engine and the shell (Foundation)
    accept and refuse the same bytes: a UTF-8 BOM is read, UTF-16 is not; a
    repeated key keeps its first value; NaN, Infinity, a lone surrogate and
    nesting too deep to read make the whole file no choice. The same table
    runs through the compiled shell (test_webapp_macapp)."""
    raw, lang = SETTINGS_BYTES[name]
    path = settings.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    assert (settings.interface_language() or "none") == lang


def test_13_2_a_settings_file_nested_too_deep_is_read_as_empty_and_the_switch_repairs_it(live):
    """A damaged settings.json nested too deep for the parser is no choice,
    not a failure: /i18n.js still answers (the page would otherwise fall
    back to Chinese without its table), and the switch rewrites the file."""
    path = settings.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[" * 100000 + "]" * 100000, encoding="utf-8")
    status, _h, raw = live.call("GET", "/i18n.js")
    assert status == 200 and raw.decode("utf-8").startswith("window.ROI18N_SAVED = null;\n")
    assert live.post("/api/settings/language", {"language": "en"}) == (200, {"ok": True, "language": "en"})
    assert _settings_file() == {"interface_language": "en"}


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
    expr = "[t('切换界面语言'), t('第 {n} 版', {n: 2}), t('未翻译的 {n}', {n: 2}), uiLang()]"
    alone = _node("global.window = {};\n" + helpers + f"\nprocess.stdout.write(JSON.stringify({expr}));\n")
    assert alone == ["切换界面语言", "第 2 版", "未翻译的 2", "zh"]
    with_en = _with_i18n(f"(function () {{ {helpers} return {expr}; }})()", saved="en")
    assert with_en == ["Switch the interface language", "Version 2", "未翻译的 2", "en"]


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
    # Each literal by its position: the same text passed to t() elsewhere excuses a bare one.
    stretch = ('const a = t("甲"); x = "甲"; y = t("乙" + n); z = t("丙");\n'
               'const pair = ["丁", t("丁")]; const odd = ["戊", t("己")]; const at = at("庚");\n')
    assert untranslated_in("x.js", stretch, {"甲": "A", "乙": "B", "丁": "D", "己": "F"}) == [
        "x.js: '甲' is not passed to t()", "x.js: t('乙' + ...) is built by concatenation",
        "x.js: t('丙') has no English entry", "x.js: '戊' is not passed to t()", "x.js: '庚' is not passed to t()"]
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
#: title, parent and children in order (text as strings); app.html's helpers
#: stubbed. Each rendered desk is one line per element holding words:
#: "class: words".
_DESK_DOM = r"""
function el(tag, cls, text) {
  const adopt = (c) => { if (c && typeof c === "object") c.parentNode = e; return c; };
  const e = { tag, className: cls || "", title: "", dataset: {}, disabled: false, kids: [], parentNode: null, scrollTop: 0,
    get textContent() { return this.kids.map((k) => (typeof k === "string" ? k : k.textContent)).join(""); },
    set textContent(v) { this.kids = v === "" ? [] : [String(v)]; },
    set innerHTML(v) { this.kids = v ? ["<html>" + v] : []; },
    appendChild(c) { this.kids.push(adopt(c)); return c; }, append(...cs) { this.kids.push(...cs.map(adopt)); },
    addEventListener() {}, querySelectorAll() { return []; },
    querySelector(sel) {
      const walk = (n) => { for (const k of n.kids) if (typeof k !== "string") {
        if (k.className.split(" ").includes(sel.slice(1))) return k; const f = walk(k); if (f) return f; } return null; };
      return walk(this);
    }, classList: { toggle() {}, add() {}, remove() {} } };
  if (text != null) e.textContent = text;
  return e;
}
global.mk = (tag, cls, text) => el(tag, cls, text);
global.mkButton = (label, cls, onClick) => Object.assign(el("button", cls || "btn btn-small", label), { onClick });
global.document = { getElementById: () => null, querySelectorAll: () => [] };
global.state = { noProject: false, dataEpoch: 0, currentId: "p-1" };
global.views = {};
global.ownMark = (item) => item && item.officialness && item.officialness.state !== "official"
  ? el("span", "own-mark", item.officialness.label) : null;
global.setStatus = (node, kind, text) => { node.textContent = text; };
global.renderViewFailure = () => {};
global.openReviewPanel = () => {};
global.ROTeam = { syncWrites: () => "[team.js]", openProposal() {}, differenceBlock: () => el("div", "diff") };
global.RONode = { openNode() {} };
function dump(node, out) {
  const own = node.kids.filter((k) => typeof k === "string").join("");
  const all = node.kids.map((k) => (typeof k === "string" ? k : "[" + k.className + "]")).join("");
  if (own || node.title) out.push(node.className + ": " + all + (node.title ? " (title: " + node.title + ")" : ""));
  node.kids.forEach((k) => { if (typeof k !== "string") dump(k, out); });
  return out;
}
async function deskHost(c) {
  const answers = { "/api/me": c.me, "/api/proposals": c.proposals, "/api/share": c.share,
    "/api/share/sync-preview": c.preview || {} };
  global.apiGet = async (path) => (path.startsWith("/api/desk") ? c.desk : answers[path]);
  const host = el("div", "host");
  views.desk = host;
  await window.RODesk.loadDesk(host, false);
  return host;
}
async function renderDesk(c) {
  return dump(await deskHost(c), []);
}
"""

#: The real team.js and node.js after desk.js (13.7: the desk's 「官方版本有更新」 block draws with
#: their syncWrites, syncFlash, differenceBlock and diffBlock), instead of _DESK_DOM's stand-ins.
_TEAM_AND_NODE = (
    "\nwindow.addEventListener = () => {};\n" + (WEBAPP / "node.js").read_text(encoding="utf-8")
    + "\n" + (WEBAPP / "team.js").read_text(encoding="utf-8")
    + "\nglobal.ROTeam = window.ROTeam; global.RONode = window.RONode;\n"
)

#: The researcher's own words in the fixture (13.1: shown as written in either language), a file
#: name of theirs among them, and the member's area on the shared drive, a fixed on-disk name.
_THEIRS = ["新路线", "TopicShift 波动", "伪协整", "成立", "放弃了", "先补稳健性", "这里的缺失值要先处理", "figs/图3.png",
           "figs/图3.official.png", "提议/li@example.com/", "换轴"]


def _desk_cases(lang: str) -> list[dict]:
    """Every part of the desk's column (DESIGN.md 14.4) and every role -- the
    PI busy and with nothing to do; a member behind the official version,
    up to date, before any publish, with a shared folder that cannot be read
    or is not set; someone not in the team; a team file that cannot be read;
    one person -- with /api/desk's column and words as the engine sends them
    in `lang`."""
    from researchos import desk as engine

    def w(zh: str, en: str) -> str:
        return en if lang == "en" else zh

    def st(status: str, label: str, words: str = "") -> dict:
        return {"status": status, "label": label, "words": words}

    a = {n: f"attempt:map.md#{n}" for n in (16, 17, 18, 20)}
    route = {"op": "add", "child": a[18], "parent": a[17], "child_number": "18", "parent_number": "17"}
    proposals = [
        {"id": "p1", "kind": "attempt_new", "state": "pending", "by": "wang@example.com", "at": "2024-03-01T12:00:00",
         "attempt": {"number": "18", "description": "新路线"}, "files": [{"path": "steps/18.py"}],
         "outcomes": [{"act": "discussed", "text": "先补稳健性"}]},
        {"id": "p2", "kind": "verdict_change", "state": "conflict", "by": "wang@example.com", "at": "2024-03-01T11:00:00", "files": [{"path": "a/x.csv"}, {"path": "b/y.csv"}],
         "verdict": {"number": "16", "old_status": st("pending", "待定"), "new_status": st("abandoned", "已放弃"), "abandons": True}},
        {"id": "p3", "kind": "variable_version", "state": "returned", "at": "2024-03-01T10:00:00", "variable": {"card": "log_ret", "version": 2},
         "outcomes": [{"act": "returned", "text": "redo it"}], "sentence": "Redefine log_ret"},
        {"id": "p4", "kind": "files", "state": "approved", "at": "2024-03-01T09:00:00", "files": [{"path": "a/1.py"}, {"path": "a/2.py"}, {"path": "a/3.py"}]},
        {"id": "p5", "kind": "odd", "state": "superseded", "at": "2024-03-01T08:00:00"},
        {"id": "p6", "kind": "verdict_change", "state": "returned", "at": "2024-03-01T07:00:00",
         "verdict": {"number": "#17", "old_status": st("current", "现行"), "new_status": st("labelled", "成立")}},
        {"id": "p7", "kind": "route_link", "state": "pending", "by": "wang@example.com", "at": "2024-03-01T06:00:00",
         "sentence": "", "route": route},
        {"id": "p8", "kind": "route_link", "state": "approved", "at": "2024-03-01T05:00:00", "route": {**route, "op": "remove"}},
    ]
    touches = {"p2": [a[16]], "p6": [a[17]], "p7": [a[18], a[17]], "p8": [a[18], a[17]]}

    def listed(p: dict) -> dict:  # the column's proposal: the listing's summary, as desk.route_map adds to it
        returned = [o["text"] for o in p.get("outcomes", []) if o["act"] == "returned"]
        return {**p, "by_label": "Wang" if p.get("by") else None, "touches": touches.get(p["id"], []),
                "reason": returned[-1] if p["state"] == "returned" and returned else None, "awaiting_sync": p["id"] == "p4"}

    def item(src: str, kind: str, dst: str, reason: str, on: list) -> dict:
        from researchos.records import judgements

        return {"src": src, "dst": dst, "type": kind, "extractor": "python_ast", "outcome": "review", "reason": reason,
                "label": judgements.reason_label(reason, lang), "src_label": src.partition(":")[2],
                "dst_label": dst.partition(":")[2], "attempts": on}

    both = item("script:steps/16-build.py", "reads", "dataset:data/panel.csv", "basis_changed", [a[16], a[17]])
    review = {"count": 1497, "groups": [
        {"id": a[16], "items": [item("script:steps/16-build.py", "writes", "dataset:data/clean.csv", "basis_changed", [a[16]]),
                                both], "officialness": {"state": "official"}},
        {"id": a[17], "items": [both], "officialness": {"state": "mine", "label": w("我的改动，尚未提交", "My change, not yet proposed")}}],
        "none": {"items": [item("script:other.py", "generates", "figure:figs/图3.png", "not_produced", []),
                           item("script:other.py", "odd", "file:x.md", "basis_changed", [])],
                 "cards": [{"key": "script:steps/vars.py", "script": "steps/vars.py", "cards": ["log_ret", "vol"], "counted": True,
                            "words": w("此脚本的改动涉及 2 个变量", "This script’s change touches 2 variables")},
                           {"key": "card:log_ret", "script": None, "cards": ["log_ret", "vol"], "counted": True, "words": None}]}}
    missing = [{"child": a[20], "parent": a[16], "child_number": "20", "parent_number": "16", "missing": ["child"], "note": "换轴",
                "by_label": w("最初的研究者", "Original researcher"), "officialness": {"state": "official"}}]
    attempts = [{"id": a[16], "number": "16", "title": "TopicShift 波动"}, {"id": a[17], "number": "17", "title": "Plot"}]

    def desk(role: str, **column) -> dict:
        shared = column.pop("shared", "ok" if role in ("pi", "member") else "not_needed")
        return {"role": role, "attempts": attempts, "lines": [], "lines_missing": column.pop("missing", []), "column": {
            "role": role, "me": "li@example.com", "notice": w(*engine.ROLE_WORDS[role]) if role in engine.ROLE_WORDS else None,
            "shared": {"state": shared, "message": w(*engine.SHARED_WORDS[shared]) if shared in engine.SHARED_WORDS else None},
            "waiting": [], "mine": [], "review": {"count": 0, "groups": [], "none": {"items": [], "cards": []}}, "sync": None,
            **column}}

    me = {"me": {"id": "li@example.com", "name": "Li"}}
    team = [{"id": "wang@example.com", "name": "Wang"}]
    behind = {"shared": "/x", "reachable": True, "behind": True, "seq": 3,
              "last_snapshot": {"at": "2024-03-01T12:00:00", "seq": 1497, "note": "fixes"}}
    # What 同步 would write (GET /api/share/sync-preview): sending, updating, removing, an .official copy.
    preview = {"from": 3, "to": 1497, "update": ["a.py", "b.py"], "remove": ["c.py"],
               "beside": [{"path": "d.py", "official_copy": "d.official.py"}], "records": [".researchos/notes.toml"],
               "kept": [], "area": "提议/li@example.com/",
               "sending": {"notes": 1, "judgements": 2, "papers": 1, "links_added": 1, "links_removed": 0}}
    pi_list = {"role": "pi", "proposals": proposals}
    member_list = {"role": "member", "proposals": []}
    return [
        {"me": me, "proposals": pi_list, "share": {"shared": "/x", "role": "pi", "team": team},
         "desk": desk("pi", waiting=[listed(p) for p in proposals if p["state"] in ("pending", "conflict")], review=review,
                      missing=missing)},
        {"me": me, "proposals": {"role": "pi", "proposals": []}, "share": {}, "desk": desk("pi")},
        {"me": me, "proposals": {"role": "member", "proposals": proposals}, "share": {}, "preview": preview,
         "desk": desk("member", mine=[listed(p) for p in proposals], sync=behind)},
        {"me": me, "proposals": member_list, "share": {}, "desk": desk("member", sync={**behind, "behind": False, "last_snapshot": {"seq": 3}})},
        {"me": me, "proposals": member_list, "share": {}, "desk": desk("member", sync={**behind, "behind": False, "last_snapshot": None})},
        {"me": me, "proposals": member_list, "share": {}, "desk": desk("member", shared="unreadable", sync={**behind, "reachable": False})},
        {"me": me, "proposals": member_list, "share": {}, "desk": desk("member", shared="not_configured", sync={"shared": None})},
        {"me": me, "proposals": {"role": "none", "proposals": []}, "share": {}, "desk": desk("none")},
        {"me": me, "proposals": {"role": "unknown", "proposals": []}, "share": {}, "desk": desk("unknown")},
        {"me": me, "proposals": None, "share": None, "desk": desk("single", review={**review, "count": 1, "groups": review["groups"][:1],
                                                                               "none": {"items": [], "cards": []}}, missing=missing)},
    ]


def _desks(saved: str | None, desk_js: str | None = None, *, team: bool = False) -> list[list[str]]:
    """Every desk of the fixture rendered by desk.js (or `desk_js`), with
    /i18n.js in the saved language, or alone (None); with `team`, through
    the real team.js and node.js."""
    i18n = "" if saved is None else f"window.ROI18N_SAVED = {json.dumps(saved)};\n" + _i18n_source()
    cases = json.dumps(_desk_cases(saved or "zh"), ensure_ascii=False)
    return _node("global.window = {};\n" + i18n + _DESK_DOM + (desk_js or (WEBAPP / "desk.js").read_text(encoding="utf-8"))
                 + (_TEAM_AND_NODE if team else "")
                 + f"\n(async () => {{ const out = []; for (const c of {cases}) out.push(await renderDesk(c));"
                 " process.stdout.write(JSON.stringify(out)); })();\n")


#: sha256 of the Chinese desks above as rendered by desk.js (`_desks(None)`): 13.8 #1, a Chinese
#: screen reads byte for byte as designed. Re-baselined on purpose for V10 (DESIGN.md 14.5, 14.7):
#: the desk is the route map with the column beside it, so the first increment's pin (commit
#: 041c510's lists of changes, outputs and abandoned routes) describes a desk that is gone.
#: Moved again by the ruling of 2026-10-13 (DESIGN.md 14.4): a member's returned proposal stays in
#: 我的提议 until 「知道了」, so the member's two returned rows gain that button -- nothing else moved.
#: Moved by the V10 review (finding 22): a 待复核 row is the attempt itself, so its hover reads
#: 「在路线图上标出这条尝试」 (three rows) -- nothing else moved.
_DESK_ZH_SHA256 = "22a04173a0ca4fdae5cb8c5678315cca32470f259f6be2d1c3aa2b3b0e7115dc"


@needs_node
@pytest.mark.parametrize("saved", [None, "zh"])
def test_13_8_the_desk_in_chinese_is_as_before(saved):
    import hashlib

    desks = _desks(saved)
    assert hashlib.sha256(json.dumps(desks, ensure_ascii=False).encode("utf-8")).hexdigest() == _DESK_ZH_SHA256
    assert desks[0][:2] == ["desk-column-title: 待办", "desk-title: 等你批准"]
    assert "desk-sub: 1497 条判断的依据变了，等你复核。[desk-link]" in desks[0]
    assert "desk-link strong: 加上连线：#18 源自 #17 (title: 在路线图上标出它涉及的尝试)" in desks[0]


@needs_node
def test_13_7_the_desk_in_english():
    """Every word the desk's column writes is English (13.4's words and 14.6's,
    13.5's dates, numbers and plurals); the researcher's own words, a
    [verdict_labels] word among them, as written; the engine's sentences
    as it composed them for the request. Drawn with the real team.js and
    node.js: a member's 「官方版本有更新」 says what 同步 will write in
    English too."""
    desks = _desks("en", team=True)
    lines = [line for desk in desks for line in desk]
    left = [line for line in lines if CJK.search(re.sub("|".join(map(re.escape, _THEIRS)), "", line))]
    assert left == [] and not any(FORBIDDEN.search(line) for line in lines)
    for line in [
        "desk-column-title: To handle", "desk-title: Awaiting your approval", "status-label tone-plain: New attempt",
        "desk-link strong: New attempt #18: 新路线, with 1 file", "desk-meta: Wang · 1 Mar 2024", "btn btn-small: View",
        "desk-link strong: #16: change the verdict from “Pending” to “Abandoned”, abandoning the route, with 2 files "
        "(title: Mark the attempts it touches on the route map)",
        "desk-meta: Wang · The approval and return records contradict each other · 1 Mar 2024",
        "status-label tone-plain: Line", "desk-link strong: Add the line: #18 grew out of #17 "
        "(title: Mark the attempts it touches on the route map)",
        "desk-title: Needs review", "desk-sub: The basis of 1,497 judgments changed; they await your review.[desk-link]",
        "desk-link: Review", "desk-link strong: #16 TopicShift 波动 (title: Mark this attempt on the route map)",
        ": [desk-reason]steps/16-build.py writes data/clean.csv", "desk-reason: The basis changed",
        "own-mark: My change, not yet proposed", "desk-head: Not part of any attempt",
        ": [desk-reason]other.py generates figs/图3.png", "desk-reason: The machine no longer finds this link",
        ": [desk-reason]other.py is linked to x.md", ": [mono] This script’s change touches 2 variables", ": log_ret, vol",
        "desk-title: Lines with a missing end", "desk-what: #20 grew out of #16",
        "desk-meta: #20 is not in the table · Original researcher",
    ]:
        assert line in desks[0], line
    # A judgment two attempts hold is under both, and the count says it once (the engine's).
    assert desks[0].count(": [desk-reason]steps/16-build.py reads data/panel.csv") == 2
    assert desks[1] == ["desk-column-title: To handle", "desk-empty: Nothing to handle right now."]
    for line in ["desk-title: My proposals", "desk-meta: Proposed · The PI says: 先补稳健性 · 1 Mar 2024",
                 "desk-note plain: Variable log_ret: definition version 2", "desk-meta: Returned: redo it · 1 Mar 2024",
                 "desk-link strong: 3 files: 1.py, 2.py and others", "desk-meta: Approved, awaiting sync · 1 Mar 2024",
                 "desk-link strong: #17: change the verdict from “Current” to “成立” "
                 "(title: Mark the attempts it touches on the route map)", "desk-meta: Returned · 1 Mar 2024",
                 "desk-meta: Proposed · 1 Mar 2024", "desk-title: Official version updated",
                 "desk-sub: The PI published version 1,497 (1 Mar 2024): fixes", "btn: Sync",
                 "team-line writes: Syncing first writes what you have not sent yet (1 note, 2 linked judgments, "
                 "1 “Set as the paper” mark and 1 link added) into “提议/li@example.com/” (your proposals folder) in the "
                 "shared folder: no approval is needed, and the PI adds it to the official record at the next publish. "
                 "Then it brings the working copy to version 1,497: update 2 files you have not changed; remove 1 file "
                 "the official version deleted; keep your version of 1 file changed on both sides and put the official "
                 "copy beside it (.official); replace each record file of notes, judgments and links with the official "
                 "one where all your entries are in it, and keep yours where they are not yet. It never merges: nothing "
                 "you changed is overwritten."]:
        assert line in desks[2], line
    # Ruled 2026-10-13: each returned proposal stays until its author says “Got it”.
    assert desks[2].count("btn btn-small: Got it") == 2
    # 14.4: what waits -- a proposal superseded, or approved and synced since, is not listed.
    assert not any(line.startswith("desk-link strong: Remove the line") for line in desks[2])
    assert not any("Replaced by your later proposal" in line for line in desks[2])
    for i in (3, 4):  # a member up to date, or before any publish: nothing to handle
        assert desks[i] == desks[1], i
    assert desks[5] == ["desk-column-title: To handle", "desk-alone: The shared folder cannot be opened right now: "
                        "proposals and sync cannot be shown"]  # never 「同步」 while it cannot be opened
    assert "desk-alone: This project is not connected to a shared folder: proposals and sync cannot be shown" in desks[6]
    assert "desk-alone: You are not a member of this project yet: ask the PI to add you to the team, then sync" in desks[7]
    assert "desk-alone: The members file (team.toml) cannot be read right now, so who the PI is cannot be told" in desks[8]
    assert desks[9][1:3] == ["desk-title: Needs review", "desk-sub: The basis of 1 judgment changed; it awaits your review.[desk-link]"]
    assert "item.what ===" not in (WEBAPP / "desk.js").read_text(encoding="utf-8")  # 13.6


@needs_node
@pytest.mark.parametrize("saved", [None, "zh", "en"])
def test_13_7_people_named_in_a_desk_row_read_as_a_list(saved):
    """A row of the desk's column names its people as the engine named them
    in the request's language -- who proposed, who drew a line (13.4's
    “Original researcher”) -- and its names as each language lists them: in
    English with a comma, in Chinese with 「、」. A proposal's line says what
    approving it would do, not what is done. (V10, 14.5: the rows of changes
    that named several people are gone; what changed is 「有改动」 on a card.)"""
    zh = saved != "en"
    first = "最初的研究者" if zh else "Original researcher"
    verdict = {"id": "p1", "kind": "verdict_change", "state": "pending", "by": "wang@example.com", "by_label": "Wang",
               "at": "2024-03-01T11:00:00", "touches": ["attempt:map.md#16"],
               "verdict": {"number": "16", "old_status": {"status": "pending", "label": "待定"},
                           "new_status": {"status": "current", "label": "现行"}}}
    line = {"child": "attempt:map.md#20", "parent": "attempt:map.md#16", "child_number": "20", "parent_number": "16",
            "missing": ["child", "parent"], "by_label": first}
    cards = {"key": "card:x", "script": None, "cards": ["log_ret", "vol", "ret"], "counted": True, "words": None}
    review = {"count": 1, "groups": [], "none": {"items": [], "cards": [cards]}}
    case = {"me": {"me": {"id": "li@example.com", "name": "Li"}}, "proposals": {"role": "pi", "proposals": [verdict]},
            "share": {}, "desk": {"role": "pi", "attempts": [], "lines_missing": [line], "column": {
                "role": "pi", "shared": {"state": "ok", "message": None}, "waiting": [verdict], "review": review}}}
    i18n = "" if saved is None else f"window.ROI18N_SAVED = {json.dumps(saved)};\n" + _i18n_source()
    desk = _node("global.window = {};\n" + i18n + _DESK_DOM + (WEBAPP / "desk.js").read_text(encoding="utf-8")
                 + f"\n(async () => process.stdout.write(JSON.stringify(await renderDesk({json.dumps(case)}))))();\n")
    meta = [line for line in desk if line.startswith(("desk-meta", "desk-link strong", ": log_ret"))]
    if zh:
        assert meta == ["desk-link strong: #16 的判决从「待定」改为「现行」 (title: 在路线图上标出它涉及的尝试)",
                        "desk-meta: Wang · 2024年3月1日", ": log_ret、vol、ret", "desk-meta: 年表里没有 #20 · 年表里没有 #16 · 最初的研究者"]
    else:
        assert meta == ["desk-link strong: #16: change the verdict from “Pending” to “Current” "
                        "(title: Mark the attempts it touches on the route map)", "desk-meta: Wang · 1 Mar 2024",
                        ": log_ret, vol, ret",
                        "desk-meta: #20 is not in the table · #16 is not in the table · Original researcher"]


def _sync_result(lang: str) -> dict[str, Any]:
    """What POST /api/share/sync answers, its reasons and the table's
    sentence as the engine composes them in `lang`."""
    def w(zh: str, en: str) -> str:
        return en if lang == "en" else zh

    diff = "--- yours\n+++ official\n@@ -1,24 +1,25 @@\n" + "".join(f"-{i},0.5\n+{i},0.7\n" for i in range(12))
    return {"ok": True, "from": 3, "seq": 1497, "updated": ["a.py", "b.py"], "removed": ["c.py"], "entries_sent": 3,
            "notes_waiting": [".researchos/notes.toml"],
            "conflicts": [{"path": "data/panel.csv", "official_copy": "data/panel.official.csv",
                           "reason": w("你和正式版本都改了这个文件：留着你的，正式版本的放在旁边",
                                       "you and the official version both changed this file: yours is kept, and the "
                                       "official one is put beside it"),
                           "summary": w("行数从 96 增至 97，新增列 log_ret", "Rows from 96 to 97; column added: log_ret"),
                           "table": True, "table_small": True, "diff": diff, "truncated": True},
                          {"path": "figs/图3.png", "official_copy": "figs/图3.official.png", "reason": "x"}],
            "kept_yours": [{"path": ".researchos/canvas.json",
                            "reason": w("关系图上的摆放是每个人自己的，不随同步改变",
                                        "the arrangement on the relation map is each person’s own and does not change "
                                        "with a sync")}]}


def _desk_synced(saved: str | None) -> list[str]:
    """A member's desk, behind the official version, after 「同步」 is
    clicked on it: the sentence of what it did and each .official copy
    with its difference, drawn by the real team.js and node.js; then the
    difference block's other two forms (a large table, a file that is not
    text), which 成员's list of copies beside shows."""
    lang = saved or "zh"
    i18n = "" if saved is None else f"window.ROI18N_SAVED = {json.dumps(saved)};\n" + _i18n_source()
    case = json.dumps(_desk_cases(lang)[2], ensure_ascii=False)
    res = json.dumps(_sync_result(lang), ensure_ascii=False)
    return _node("global.window = {};\n" + i18n + _DESK_DOM + (WEBAPP / "desk.js").read_text(encoding="utf-8")
                 + _TEAM_AND_NODE
                 + f"global.apiPost = async (path) => (path === '/api/share/sync' ? {res} : null);\n"
                 "const find = (n) => { for (const k of n.kids) if (typeof k !== 'string') {"
                 " if (k.className === 'btn' && k.onClick) return k; const f = find(k); if (f) return f; } return null; };\n"
                 f"(async () => {{ const host = await deskHost({case}); await find(host).onClick();"
                 " const shown = []; dump(host.querySelector('.desk-sync-done'), shown);"
                 " dump(ROTeam.differenceBlock({ summary: 'S', table: true }, false), shown);"
                 " dump(ROTeam.differenceBlock({}, false), shown);"
                 " process.stdout.write(JSON.stringify(shown)); })();\n")


@needs_node
@pytest.mark.parametrize("saved", [None, "zh", "en"])
def test_13_7_a_members_desk_after_sync_in_the_screens_language(saved):
    """13.7 (the desk entirely) and 13.8 #2: after 「同步」 on a member's
    desk, what it did, each .official copy and its difference read in the
    screen's language -- the engine's reason and table sentence as it
    composed them for the request -- with nothing Chinese in English but
    the researcher's file names; in Chinese exactly the sentences 成员
    showed before (one helper, every page)."""
    shown = _desk_synced(saved)
    if saved == "en":
        assert shown == [
            "team-done: Synced to version 1,497; sent your 3 changes to notes, judgments or links to the shared folder; "
            "updated 2 files; removed 1 file the official version deleted; kept your versions of 2 files changed on both "
            "sides, with the official copies beside them; kept your version of 1 file (the arrangement on the relation "
            "map is each person’s own and does not change with a sync); your notes and links still wait for the PI to "
            "publish, so the record files stay yours for now.",
            "team-done: The official data/panel.csv is beside yours as data/panel.official.csv; yours was not changed.",
            ": How yours differs from the official one", "desk-note plain: Rows from 96 to 97; column added: log_ret",
            *[line for line in shown if line.startswith("dl-row")],
            "btn btn-small diff-more: Show the remaining 5 lines",
            "node-sub: The change is too long; only its beginning was compared.",
            "team-done: The official figs/图3.png is beside yours as figs/图3.official.png; yours was not changed.",
            ": How yours differs from the official one", "desk-note plain: S",
            "desk-note plain: A large table: only its rows and columns are compared, not line by line.",
            ": How yours differs from the official one",
            "desk-note plain: The two files differ (not text, so not compared line by line)",
        ]
        assert len([line for line in shown if line.startswith("dl-row")]) == 20
        left = [line for line in shown if CJK.search(re.sub("|".join(map(re.escape, _THEIRS)), "", line))]
        assert left == [] and not any(FORBIDDEN.search(line) for line in shown)
    else:
        assert [line for line in shown if not line.startswith("dl-row")] == [
            "team-done: 已同步到第 1497 版；已把你的 3 条备注、判断或连线改动发送到公盘；更新了 2 个文件；删除了 1 个正式版本已删除的文件；"
            "2 个两边都改过的文件留着你的，官方的一份放在旁边；1 个文件留着你的（关系图上的摆放是每个人自己的，不随同步改变）；"
            "你的备注和连线还在等 PI 发布，记录文件暂时留着你的。",
            "team-done: 官方的 data/panel.csv 放在了旁边：data/panel.official.csv；你的没有动。",
            ": 你的和官方的有什么不同", "desk-note plain: 行数从 96 增至 97，新增列 log_ret",
            "btn btn-small diff-more: 显示其余 5 行", "node-sub: 改动太长，只比较了开头的一部分。",
            "team-done: 官方的 figs/图3.png 放在了旁边：figs/图3.official.png；你的没有动。",
            ": 你的和官方的有什么不同", "desk-note plain: S", "desk-note plain: 表格较大：只比较行和列，不逐行对比。",
            ": 你的和官方的有什么不同", "desk-note plain: 两个文件不同（不是文本，没有逐行比较）",
        ]


@needs_node
@pytest.mark.parametrize("saved", [None, "zh"])
def test_13_8_a_members_sync_preview_in_chinese_is_as_before(saved):
    """13.8 #1: the desk's 「官方版本有更新」, drawn with the real team.js,
    says what 同步 will write in the words 成员 always used."""
    desks = _desks(saved, team=True)
    assert ("team-line writes: 同步会先把你还没发送的 1 条备注、2 条关联判断、1 处「设为论文正文」的标记、增加 1 条连线写进公盘的"
            "「提议/li@example.com/」：不需要批准，PI 下次发布时加入正式记录。然后把工作副本带到第 1497 版：更新 2 个你没改过的文件；"
            "删除 1 个正式版本已删除的文件；1 个两边都改过的文件留着你的，把官方的一份放在旁边（.official）；备注、判断和连线的记录文件："
            "你的条目都已进入正式版本的，换成正式的那一份；还没进入的，留着你的。从不合并，你改过的内容不会被覆盖。") in desks[2]


@pytest.fixture
def drive(tmp_path, monkeypatch):
    """A PI and a member on one machine (tests/test_v8_phase4.Drive)."""
    from researchos import signing
    from test_v8_phase4 import Drive

    if signing.tool() is None:
        pytest.skip("ssh-keygen is not installed here")
    return Drive(tmp_path, monkeypatch)


def test_13_7_sync_and_its_preview_speak_the_requests_language(drive):
    """GET /api/share/sync-preview and POST /api/share/sync compose their
    reasons and a table's sentence in the language the page's request names
    (13.2, 13.6): English with X-ResearchOS-Lang: en, Chinese without the
    header (the command line's and MCP's case) or with zh."""
    from researchos import share
    from test_v8_phase2 import _call
    from test_v8_phase3 import _serve

    def official_changes(rows: int, deleted: str) -> None:
        with drive.pi():
            (drive.official / "data" / "panel.csv").write_text(_csv(rows))
            (drive.official / deleted).unlink()
            share.publish(drive.official)

    def mine(changed: str) -> None:
        (drive.work / "data" / "panel.csv").write_text(_csv(96, ("id", "x", "log_ret")))
        (drive.work / changed).write_text("# the member's own change\n")

    official_changes(97, "steps/17-plot.py")
    with drive.member():
        mine("steps/17-plot.py")
        with _serve(drive.work) as base:
            asked = [_call(f"{base}/api/share/sync-preview", headers=h)
                     for h in ({}, {"X-ResearchOS-Lang": "zh"}, {"X-ResearchOS-Lang": "en"})]
            assert [status for status, _p in asked] == [200, 200, 200] and asked[0][1] == asked[1][1]
            zh, en = asked[1][1], asked[2][1]
            assert zh["kept"] == [{"path": "steps/17-plot.py", "reason": "正式版本删除了这个文件；你改过它，所以留着你的"}]
            assert en["kept"] == [{"path": "steps/17-plot.py",
                                   "reason": "the official version deleted this file; you changed it, so yours is kept"}]
            assert {k: v for k, v in zh.items() if k != "kept"} == {k: v for k, v in en.items() if k != "kept"}
            status, done = _call(f"{base}/api/share/sync", "POST", {}, headers={"X-ResearchOS-Lang": "en"})
    assert status == 200
    [conflict] = done["conflicts"]
    assert conflict["reason"] == ("you and the official version both changed this file: yours is kept, and the "
                                  "official one is put beside it")
    assert conflict["summary"].startswith("Rows from 96 to 97; ") and not CJK.search(conflict["summary"])
    assert conflict["diff"] and done["kept_yours"] == [
        {"path": "steps/17-plot.py", "reason": "the official version deleted this file; you changed it, so yours is kept"}]

    official_changes(98, "steps/16-build.py")
    with drive.member():
        mine("steps/16-build.py")
        with _serve(drive.work) as base:
            status, done = _call(f"{base}/api/share/sync", "POST", {})
    assert status == 200
    [conflict] = done["conflicts"]
    assert conflict["reason"] == "你和正式版本都改了这个文件：留着你的，正式版本的放在旁边"
    assert conflict["summary"].startswith("行数从 96 增至 98") and CJK.search(conflict["summary"])
    assert done["kept_yours"] == [{"path": "steps/16-build.py", "reason": "正式版本删除了这个文件；你改过它，所以留着你的"}]


def _marks(value: Any) -> set[str]:
    """Every officialness label in a payload."""
    found: set[str] = set()
    if isinstance(value, dict):
        mark = value.get(officialness.FIELD)
        if isinstance(mark, dict) and "label" in mark:
            found.add(mark["label"])
        for child in value.values():
            found |= _marks(child)
    elif isinstance(value, list):
        for child in value:
            found |= _marks(child)
    return found


def test_13_7_officialness_and_people_words_follow_the_language_on_every_page(drive):
    """13.7 as amended: officialness states and people words are shared
    words -- the same English on Research routes, the node page, its history,
    the tree, Data & code and Variables (their marks and the mode's labels)
    as on the desk, for one request's language; Chinese without the header,
    exactly as with zh."""
    from researchos import proposals
    from test_v8_phase2 import _call
    from test_v8_phase3 import _serve
    from test_v8_phase4 import MEMBER, PI

    with drive.member():
        (drive.work / "map.md").write_text(_map("☠️ 伪协整"))  # row 16 re-judged: my change, not yet proposed
        (drive.work / "steps" / "17-plot.py").write_text("# the member's own change\n")
        proposals.write_proposal(drive.work, proposals.make_proposal(drive.work, "files", "画图改了",
                                                                     only=["steps/17-plot.py"]))
        paths = ("/api/routes", "/api/node?node=script%3Asteps%2F17-plot.py",
                 "/api/history?node=script%3Asteps%2F17-plot.py", "/api/tree", "/api/data-code", "/api/variables")
        with _serve(drive.work) as base:
            got = [{path: _call(f"{base}{path}", headers=h) for path in paths}
                   for h in ({}, {"X-ResearchOS-Lang": "zh"}, {"X-ResearchOS-Lang": "en"})]
    plain, zh, en = got
    assert plain == zh and all(status == 200 for status, _b in zh.values())
    marks = [{path: _marks(body) for path, (_s, body) in lang.items()} for lang in (zh, en)]
    assert marks[0]["/api/routes"] == {"我的改动，尚未提交"} and marks[0][paths[2]] == {"提议中"}
    assert set().union(*marks[0].values()) == {"我的改动，尚未提交", "提议中"}
    to_en = {officialness.LABELS[state]: officialness.LABELS_EN[state] for state in officialness.LABELS}
    assert marks[1] == {path: {to_en[m] for m in got} for path, got in marks[0].items()}
    assert all(body["officialness_mode"]["labels"] == officialness.LABELS_EN for _s, body in en.values())
    # The history names this Mac's own versions in the member's copy as 13.4 does.
    who = [{v["who_label"] for v in lang[paths[2]][1]["versions"]} for lang in (zh, en)]
    assert "本机" in who[0] and "This Mac" in who[1] and not any(CJK.search(w) for w in who[1] - {MEMBER[0], PI[0]})


def test_13_7_the_original_researcher_is_named_in_the_requests_language(tmp_path):
    """A version, a note and a card entry with no `by` are the original
    researcher's (12.12) -- “Original researcher” on an English screen on
    the node page's history, its notes and a variable card; Chinese without
    the header (the command line's and MCP's case) or with zh."""
    from researchos import nodepage
    from researchos.records import cards
    from researchos.records import notes as notes_mod

    root = _project(tmp_path / "proj")
    _scan(root)
    notes_mod.add_note(root, "figs/fig2.png", "单位", via="cli")
    cards.new_card(root, "panelx")
    (root / ".researchos" / "variables" / "panelx" / "v1.toml").write_text(
        'name = "Panel X"\nmeaning = \'\'\'x\'\'\'\nunit = "1"\ngranularity = "月"\n\n'
        '[[input]]\ndataset = "data/raw.csv"\n\n[construction]\nformula = \'\'\'x\'\'\'\n\n'
        '[implementation]\nscript = "steps/16-build.py"\noutput = "data/panel.csv"\nfield = "x"\n\n'
        "[decision]\nwhy = '''because'''\n", encoding="utf-8")
    cards.confirm(root, "panelx", attested="yes")
    httpd, thread = _serving(root)
    try:
        def ask(headers: dict[str, str]) -> list[Any]:
            def get(path: str) -> Any:
                status, _h, raw = _Live(httpd).call("GET", path, headers=headers)
                assert status == 200, raw
                return json.loads(raw)
            return [sorted({v["who_label"] for v in get("/api/history?node=script%3Asteps%2F16-build.py")["versions"]}),
                    [n["label"] for n in get("/api/notes?node=figure%3Afigs%2Ffig2.png")["notes"]],
                    [e["by_label"] for e in get("/api/variables/card?id=panelx")["history"]],
                    sorted({v["who_label"] for v in get("/api/history?node=variable%3Apanelx")["versions"]})]
        plain, zh, en = ask({}), ask({"X-ResearchOS-Lang": "zh"}), ask({"X-ResearchOS-Lang": "en"})
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    assert plain == zh == [["最初的研究者"]] * 4
    assert en == [["Original researcher"]] * 4
    # A decision recorded before names existed (no `by`), as Research routes and 「当时的依据」 name it.
    entry = {"id": "d-1", "act": "abandoned", "attempt": "attempt:map.md#16"}
    assert nodepage._decision_payload(entry, None, {}, "en")["by"] == {"id": None, "label": "Original researcher"}
    assert nodepage._decision_payload(entry, None, {})["by"] == {"id": None, "label": "最初的研究者"}


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
    left but the researcher's text and the verdict vocabulary (13.1).
    Re-baselined for V10 (14.5): the desk is the route map -- what changed
    is a card's `changed_at`, a time, the same in both languages (the
    timeline says what), so the first desk's sentences are gone."""
    from researchos.ingest import attempts as attempts_ingest
    from test_v8_u2 import _backdate

    root = _project(tmp_path / "proj")
    _backdate(root)
    (root / "steps" / "16-build.py").write_text((root / "steps" / "16-build.py").read_text() + "df = df.dropna()\n")
    (root / "data" / "panel.csv").write_text(_csv(97, ("id", "x", "log_ret")))
    (root / "figs" / "fig3.png").write_bytes(b"\x89PNG fig3")
    (root / "map.md").write_text(_map("☠️ 伪协整"))
    _scan(root)
    httpd, thread = _serving(root)
    try:
        got = [json.loads(_Live(httpd).call("GET", "/api/desk", headers=h)[2])
               for h in ({}, {"X-ResearchOS-Lang": "zh"}, {"X-ResearchOS-Lang": "en"})]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    zh, zh_asked, en = got
    assert zh == zh_asked
    cards = [{a["id"]: a for a in payload["attempts"]}["attempt:map.md#16"] for payload in (zh, en)]
    assert cards[0]["changed_at"] is not None and cards[0]["changed_at"] == cards[1]["changed_at"]
    assert [(c["route_status"]["status"], c["tone"], c["abandoned"], c["reason"]) for c in cards] == [
        ("abandoned", "gray", True, None)] * 2
    assert en["officialness_mode"]["labels"] == {"mine": "My change, not yet proposed", "proposed": "Proposed",
                                                 "approved": "Approved, awaiting sync"}
    vocabulary = set(attempts_ingest.DEFAULT_VERDICT_LABELS.values()) | {attempts_ingest.OTHER_VERDICT_LABEL}
    left = [(k, s) for k, s in _strings(en) if CJK.search(s) and k not in _THEIR_KEYS and not (k == "label" and s in vocabulary)]
    assert left == []


@pytest.mark.parametrize("before, after, en, zh", [
    (["id"], ["id", "x"], "Column added: x", "新增列 x"), (["id"], ["id", "x", "y"], "Columns added: x, y", "新增列 x、y"),
    (["id", "x"], ["id"], "Column removed: x", "删除列 x"), (["id", "x", "y"], ["id"], "Columns removed: x, y", "删除列 x、y"),
])
def test_13_5_a_tables_column_change_has_a_singular_and_a_plural(before, after, en, zh):
    """13.5: every counted noun has a singular and a plural -- the Chinese
    has one form, as before. Re-baselined for V10 (14.5): the first desk's
    sentence for a file's size left with its list of changes; a change is
    now worded by the timeline and the sync preview, whose table sentence
    counts columns (`history.table_sentence`, in the request's language)."""
    a, b = {"rows": 2, "columns": before}, {"rows": 2, "columns": after}
    assert history.table_sentence(a, b, words=True, lang="en") == en
    assert history.table_sentence(a, b, words=True) == zh


def test_13_7_a_git_projects_desk_and_the_officialness_words_in_english(tmp_path):
    """Edits git has not seen yet: the desk marks the attempt holding them
    (a time, the same in both languages); who made them is the timeline's,
    in the page's language -- the original researcher in a project of one,
    「本机」 "This Mac" in a member's copy; a member's own work is marked in
    the page's language (13.4). Re-baselined for V10 (14.5): the first
    desk's change rows are gone."""
    from datetime import datetime, timezone

    from researchos import desk, officialness
    from test_v8_phase2 import _conn, _git

    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q")
    _project(root)
    _git(root, "add", "steps", "data", "figs", "paper.md", "map.md")
    _git(root, "commit", "-q", "-m", "first", date="2026-01-01T10:00:00+00:00")
    plot = root / "steps" / "17-plot.py"
    plot.write_text(plot.read_text() + "# mine\n")
    conn = _conn(root)
    try:
        out = [desk.payload(conn, root, lang=lang) for lang in ("zh", "en")]
    finally:
        conn.close()
    edited = desk._iso(datetime.fromtimestamp(plot.stat().st_mtime, timezone.utc).astimezone())
    assert out[0]["attempts"][0]["changed_at"] == out[1]["attempts"][0]["changed_at"] == edited
    newest = [history.versions(root, "steps/17-plot.py", lang)["versions"][0] for lang in ("zh", "en")]
    assert [(v["version"], v["who_label"]) for v in newest] == [(history.WORKTREE, "最初的研究者"),
                                                                 (history.WORKTREE, "Original researcher")]
    assert (history.local_label(), history.local_label("en")) == ("本机", "This Mac")
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
    # Without an upstream the engine compares with the remote's default
    # branch; git 2.48+ records origin/HEAD on fetch, older git does not.
    _git(linked, "remote", "set-head", "origin", "-d")
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


def test_13_7_api_github_push_answers_with_the_state_in_the_requests_language(stand_in, linked_server):
    """POST /api/github/push answers with the state the page puts under the
    project menu's GitHub line at once (13.2): in the request's language, as
    /api/github does -- after a push and when there was nothing to push;
    Chinese without the header or with zh. The push's own words stay
    Chinese (the push dialog is a later increment)."""
    from test_v7_push import _commit

    live, work = linked_server
    said = []
    for n, headers in enumerate(({"X-ResearchOS-Lang": "en"}, {}, {"X-ResearchOS-Lang": "zh"})):
        _commit(work, f"step{n}.py", "x\n", f"step {n}")
        for _round in ("pushed", "nothing to push"):
            status, plan = live.raw("GET", "/api/github/push-plan", {})
            assert status == 200
            status, res = live.raw("POST", "/api/github/push", {"Content-Type": "application/json", **headers},
                                   {"token": plan["token"]})
            assert status == 200 and CJK.search(res["message"])
            said.append((res["pushed"], res["state"]["message"]))
    assert said == [(True, "In sync with GitHub"), (False, "In sync with GitHub"),
                    (True, "与 GitHub 一致"), (False, "与 GitHub 一致"), (True, "与 GitHub 一致"), (False, "与 GitHub 一致")]


# -- the English install guide against the real strings (13.7, 12.13) ----------------------------------------

#: The English labels the guide quotes, in bold, from surfaces now in English (13.7): each is an
#: English entry of /i18n.js (either form of a plural) or the switch's own name.
GUIDE_EN_ENGLISH_LABELS = (
    "English", "Members", "Proposals", "Add project…", "Join a project from the shared folder…",
    # V10 D3 (14.6): a proposal as the PI decides it and a member's draft.
    "Review again with the ticked files", "Send proposal", "Proposed", "Approved", "Returned", "Approve",
    "Return with reason", "Discuss", "Changed on both sides", "Link files", "Add a source",
)
#: English sentences the guide quotes, in bold, by their first words: each begins an English entry.
GUIDE_EN_ENGLISH_SENTENCE_STARTS = ("Already the latest official version",)
#: The officialness marks the guide quotes in English: the engine's words on an English screen
#: (13.7: shared words follow the interface language on every page).
GUIDE_EN_OFFICIALNESS_LABELS = ("Approved, awaiting sync",)
#: The streamlined guide uses page buttons, not shell menu shortcuts.
GUIDE_EN_SHELL_ITEMS = ()
#: Bold that is emphasis, not a label.
GUIDE_EN_EMPHASIS = ("0.2.0",)
#: Chinese labels quoted for the Chinese screen before the switch (its neighbours), and words a
#: still-Chinese page shares with an English surface: the meaning in brackets is the English the
#: reader sees where the same word is in English.
_GUIDE_EN_SAME_WORDS = {
    "记录": "page", "停止服务": "page", "同步": "page", "发布": "shell",
}
#: A message quoted by its first words (the rest of the line varies).
_GUIDE_EN_SENTENCE_STARTS = ("指纹：",)


def _still_chinese_texts() -> set[str]:
    """Every whole text a surface not yet converted shows: the string
    literals of the served scripts (not /i18n.js, whose Chinese is the
    table's keys) and of app.html's inline script, and the text of
    app.html's unmarked markup, each with its CONVERTED stretches removed.
    (The engine's officialness words follow the request's language on every
    page, 13.7, so none of them is still Chinese.)"""
    texts: set[str] = set()
    for path in [APP_HTML, *(p for p in _served_scripts() if p != I18N_JS)]:
        text = path.read_text(encoding="utf-8")
        for name, start, end in CONVERTED:
            if name == path.name:
                text = text.replace(_slice((WEBAPP / name).read_text(encoding="utf-8"), start, end), "\0")
        if path.suffix == ".html":
            texts |= {_unescape(raw) for _p, _q, raw in js_literals("\n".join(re.findall(r"<script>(.*?)</script>", text, re.S)))}
            body = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", re.sub(r"<!--.*?-->", "", text, flags=re.S), flags=re.S)
            for m in re.finditer(r"<([a-zA-Z][\w-]*)\b([^>]*)>([^<]*)", body):
                if not re.search(r"\bdata-i18n\b(?!-)", m.group(2)):
                    texts.add(html_mod.unescape(m.group(3)).strip())
        else:
            texts |= {_unescape(raw) for _p, _q, raw in js_literals(text)}
    return texts


@needs_node
def test_13_7_the_english_guide_quotes_the_labels_the_screen_shows():
    """docs/install-and-join.en.md against the strings now on screen: every
    bold English label is an English entry of /i18n.js (or the switch's own
    name), every shell menu item it names is in the shell's English table,
    in its menu; every Chinese label it quotes for a page still in Chinese
    is a whole text such a page shows (a CONVERTED stretch no longer counts);
    and where a word it quotes in Chinese is English elsewhere, its meaning
    is that English. No other bold but listed emphasis."""
    text = GUIDE_EN.read_text(encoding="utf-8")
    table = english_table()
    names = _node("global.window = {};\n" + I18N_JS.read_text(encoding="utf-8")
                  + "\nprocess.stdout.write(JSON.stringify(window.ROI18N.names));\n")
    english = {form for value in table.values() for form in (value.values() if isinstance(value, dict) else [value])}
    shell = shell_english_table()
    bold = re.findall(r"\*\*(.+?)\*\*", text)
    known = set(GUIDE_EN_ENGLISH_LABELS + GUIDE_EN_ENGLISH_SENTENCE_STARTS + GUIDE_EN_OFFICIALNESS_LABELS
                + GUIDE_EN_SHELL_ITEMS + GUIDE_EN_EMPHASIS)
    assert [b for b in bold if not CJK.search(b) and b not in known] == []
    assert [b for b in bold if CJK.search(b) and b not in GUIDE_EN_CHINESE_LABELS + ("中文",)] == []
    # English from the page.
    assert names["en"] == "English"
    assert [label for label in GUIDE_EN_ENGLISH_LABELS if label not in english | {names["en"]}] == []
    assert [label for label in GUIDE_EN_ENGLISH_LABELS + GUIDE_EN_ENGLISH_SENTENCE_STARTS + GUIDE_EN_SHELL_ITEMS
            if f"**{label}**" not in text] == []
    assert [s for s in GUIDE_EN_ENGLISH_SENTENCE_STARTS if not any(form.startswith(s) for form in english)] == []
    # English from the engine: the officialness marks as /api/... say them with X-ResearchOS-Lang: en.
    assert [label for label in GUIDE_EN_OFFICIALNESS_LABELS
            if label not in officialness.LABELS_EN.values() or f"**{label}**" not in text] == []
    # English from the shell: its items, each in the menu that holds it; any of its menus named holds a named item.
    assert [item for item in GUIDE_EN_SHELL_ITEMS if item not in shell.values()] == []
    menus = {shell[zh] for zh in ("文件", "编辑", "视图", "窗口", "帮助")}
    named = re.findall(r"(\*\*([^*]+)\*\*|\S+) from the (\w+) menu", text)
    assert {item for _all, item, menu in named if menu in menus} == set(GUIDE_EN_SHELL_ITEMS)
    assert all(item and menu == shell["文件"] for _all, item, menu in named if menu in menus)
    # Chinese labels: what a page still in Chinese shows, whole (a message by its first words).
    still = _still_chinese_texts()
    for label in GUIDE_EN_CHINESE_LABELS:
        if label in ("记录", "停止服务"):
            continue  # the switch's neighbours, quoted for the Chinese screen; their meaning is checked below
        if label in _GUIDE_EN_SENTENCE_STARTS:
            assert any(s.startswith(label) for s in still), label
        else:
            assert label in still, label
    # A word quoted in Chinese that is English elsewhere means that English.
    by_state = {zh: officialness.LABELS_EN[state] for state, zh in officialness.LABELS.items()}
    source = {"page": table, "officialness": by_state, "shell": shell}
    for label, where in _GUIDE_EN_SAME_WORDS.items():
        meanings = set(re.findall(r"\*\*" + re.escape(label) + r"\*\* \(([^)]*)\)", text))
        assert meanings == {source[where][label]}, (label, meanings)


# -- 16: 生成草稿 on 新增尝试, run under node (DESIGN.md 16) -----------------------------------------------------

_DRAFT_BLOCK = ("// -- 「根据所选文件生成草稿」 (DESIGN.md 16)", "// Builds the form from the (now current) records and config.")


def _draft_run(saved: str | None, body: str) -> Any:
    """`body` (a script that ends by writing JSON) after the draft block's
    functions, in the screen's language `saved` (None: no /i18n.js)."""
    i18n = "" if saved is None else f"window.ROI18N_SAVED = {json.dumps(saved)};\n" + _i18n_source()
    return _node("global.window = {};\n" + i18n
                 + _app_block("// -- The interface language (DESIGN.md 13.6)", "// -- Small shared state")
                 + _app_block(*_DRAFT_BLOCK) + "\n" + body)


def _rows(*cells: tuple[str, str]) -> list[dict[str, Any]]:
    return [{"attrs": {"description": d}, "result": r} for d, r in cells]


@needs_node
def test_16_2_the_draft_language_is_read_from_the_tables_last_five_rows():
    """Ruled 2026-10-17: of the last 5 rows, those with a description or a
    result; at least half with Chinese asks for Chinese, none for English;
    a mix below half, or no such row, says nothing (the model follows the
    files)."""
    cases = {
        "no rows": [],
        "only empty rows": _rows(("", ""), ("", "")),
        "half Chinese": _rows(("OLS on the panel", "beta 0.3"), ("面板固定效应", ""), ("", "系数为正"), ("IV", "F = 12")),
        "a third Chinese": _rows(("OLS", "beta 0.3"), ("", "系数为正"), ("IV", "F = 12")),
        "no Chinese": _rows(("OLS", "beta 0.3"), ("", "")),
        "older Chinese rows do not count": _rows(("面板", "系数"), *[("OLS", "beta")] * 5),
        "the result alone counts": _rows(("IV", "第一阶段 F = 12")),
    }
    got = _draft_run(None, f"const cases = {json.dumps(cases, ensure_ascii=False)};\n"
                     "const out = {}; for (const k in cases) out[k] = draftLanguage(cases[k]);\n"
                     "process.stdout.write(JSON.stringify(out));\n")
    assert got == {"no rows": None, "only empty rows": None, "half Chinese": "zh", "a third Chinese": None,
                   "no Chinese": "en", "older Chinese rows do not count": "en", "the result alone counts": "zh"}


_OUTCOMES = [
    ({"description": "filled", "result": "unsupported_number"}, True),
    ({"description": "kept", "result": "kept"}, False),
    ({"description": "no_source", "result": "unknown_source"}, False),
    ({"description": "empty", "result": "filled"}, False),
]


@needs_node
@pytest.mark.parametrize("saved", [None, "en"])
def test_16_1_the_status_says_what_became_of_each_cell_in_whole_sentences(saved):
    """Each cell's outcome is one whole sentence (13.6), named by the form's
    own column label, joined with a space: filled, kept (the researcher's
    text, untouched -- never 'the files show nothing'), or the engine's
    reason for leaving it empty; then the cut."""
    names = {"description": "做了什么", "result": "结果"} if saved is None else {"description": "What", "result": "Result"}
    got = _draft_run(saved, f"const cases = {json.dumps(_OUTCOMES, ensure_ascii=False)};\n"
                     f"const names = {json.dumps(names, ensure_ascii=False)};\n"
                     "process.stdout.write(JSON.stringify(cases.map(([o, cut]) => draftOutcomeText(o, names, cut))));\n")
    if saved is None:
        assert got == [
            "已填入「做了什么」。 「结果」留空：草稿里有所选文件中找不到的数字。 较长的文件只读了开头。",
            "「做了什么」原本有内容，没有改。 「结果」原本有内容，没有改。",
            "「做了什么」留空：模型没有指出依据的文件。 「结果」留空：模型引了没有选的文件。",
            "文件里看不出「做了什么」，留空。 已填入「结果」。",
        ]
    else:
        assert got == [
            "Filled in “What”. “Result” is left empty: the draft had a number that is not in the chosen files. "
            "Only the beginning of longer files was read.",
            "“What” already had text and was left as it was. “Result” already had text and was left as it was.",
            "“What” is left empty: the model named no file it rests on. “Result” is left empty: the model cited a file you did not choose.",
            "“What” is left empty: the files do not show it. Filled in “Result”.",
        ]
        assert not CJK.search(json.dumps(got, ensure_ascii=False))


@needs_node
@pytest.mark.parametrize("saved", [None, "en"])
def test_16_2_a_refused_draft_names_the_file_or_the_remedy(saved):
    """The LM Studio remedy only when the model failed; the context remedy
    for an overflow; each file refusal names the file as typed; anything
    else is the plain sentence (project_blocked keeps its own through
    renderBlockingError)."""
    states = ["draft_model_failed", "draft_too_long", "draft_file_missing", "draft_file_not_text",
              "draft_file_cloud_only", "draft_file_outside", "draft_too_many_files", "draft_no_files", "draft_whatever", None]
    got = _draft_run(saved, f"const states = {json.dumps(states)};\n"
                     "process.stdout.write(JSON.stringify(states.map((s) => draftErrorText(Object.assign(new Error('x'),"
                     " s ? { state: s, file: 'data/panel.dta' } : {})))));\n")
    if saved is None:
        assert got == [
            "没能从模型得到草稿：请确认 LM Studio 的本地服务已启动、模型已载入",
            "所选文件超出了模型一次能读的长度：在 LM Studio 里用更大的上下文长度重新载入模型，或少选几个文件",
            "项目里找不到 data/panel.dta", "data/panel.dta 不是文本文件，模型读不了",
            "data/panel.dta 还在云端，没有下载到这台 Mac", "data/panel.dta 不在这个项目里",
            "一次最多选 5 个文件", "先选至少一个文件", "没能生成草稿", "没能生成草稿",
        ]
    else:
        assert got[2:6] == ["data/panel.dta cannot be found in the project", "data/panel.dta is not a text file, so the model cannot read it",
                            "data/panel.dta is still in the cloud and not downloaded to this Mac", "data/panel.dta is not in this project"]
        assert got[1].startswith("The chosen files are longer than the model can read at once")
        assert "LM Studio" in got[0] and all("LM Studio" not in s for s in got[2:])
        assert not CJK.search(json.dumps(got, ensure_ascii=False))


#: Just enough DOM for attemptDraftBlock and llmSettingBox: elements with children, values, datasets,
#: listeners one can fire, `after` and `remove`; the page's mk/mkButton/formFieldEl/setFormStatus stood in.
_DRAFT_DOM = r"""
function el(tag, cls, text) {
  return { tag, className: cls || "", _text: text == null ? "" : String(text), children: [], dataset: {}, listeners: {},
    disabled: false, value: "", placeholder: "", title: "", parent: null, open: false,
    get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); },
    set textContent(v) { this._text = String(v); this.children = []; },
    set innerHTML(v) { this._text = ""; this.children = []; },
    get childElementCount() { return this.children.length; },
    append(...cs) { cs.forEach((c) => { c.parent = this; this.children.push(c); }); },
    appendChild(c) { this.append(c); return c; },
    setAttribute(k, v) { this[k] = String(v); },
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
    fire(type, ev) { return Promise.all((this.listeners[type] || []).map((fn) => fn(ev || {}))); },
    after(n) { const p = this.parent; p.children.splice(p.children.indexOf(this) + 1, 0, n); n.parent = p; },
    remove() { const p = this.parent; if (p) p.children.splice(p.children.indexOf(this), 1); this.parent = null; },
  };
}
global.document = { createElement: (tag) => tag === "template"
  ? { set innerHTML(v) { this.content = { firstChild: el("svg", "res-chevron") }; } } : el(tag) };
global.mk = el;
global.mkButton = (label, cls, onClick) => { const b = el("button", cls || "btn btn-small", label); if (onClick) b.addEventListener("click", onClick); return b; };
global.formFieldEl = (label, input) => { const w = el("div", "form-field"); w.append(el("label", "", label), input); return w; };
global.setFormStatus = (s, cls, text, err) => { s.className = "panel-status " + cls; s.textContent = text; s.err = err ? err.state || err.message : null; };
const all = (root, pred, out = []) => { if (pred(root)) out.push(root); root.children.forEach((c) => all(c, pred, out)); return out; };
const find = (root, pred) => all(root, pred)[0];
const tick = () => new Promise((r) => setTimeout(r, 0));
const key = (k, composing) => ({ key: k, isComposing: !!composing, keyCode: composing ? 229 : 13, prevented: false,
  preventDefault() { this.prevented = true; } });
"""

_DRAFT_FORM = r"""
const calls = [];
let answer = null, release = null, settingFails = false, canvasFailures = 1;
const elsewhere = { base_url: "http://studio.local:1234/v1", model: "qwen", saved: { base_url: "http://studio.local:1234/v1", model: null },
  where: "elsewhere", host: "studio.local" };
global.apiGet = async (path) => {
  calls.push(["GET", path]);
  if (path === "/api/settings/llm") { if (settingFails) throw new Error("HTTP 500"); return elsewhere; }
  if (canvasFailures-- > 0) throw new Error("HTTP 409");
  return { nodes: [{ path: "out/t.csv" }, { path: "code/run.py" }, { path: "code/run.py" }, { path: "figs/f.png", type: "figure" },
    { path: "data/auto.dta", type: "dataset" }, { path: "gone.py", missing: true }, { path: "maybe.py", ghost: true }] };
};
global.apiPost = async (path, body) => {
  calls.push(["POST", path, body]);
  if (path === "/api/settings/llm") return { base_url: "http://localhost:1234/v1", model: body.model || "dflt",
    saved: { base_url: body.base_url || null, model: body.model || null }, where: "this_mac", host: "localhost" };
  return new Promise((r) => { release = () => r(answer); });
};
(async () => {
  const out = {};
  const form = el("form"), desc = el("textarea"), result = el("textarea");
  form.append(desc, result);
  const block = attemptDraftBlock({ description: desc, result: result }, [{ attrs: { description: "面板回归" }, result: "系数 0.3" }],
    false, { description: "做了什么" });
  const go = find(block.el, (n) => n.tag === "button" && n.textContent === "生成草稿");
  const where = block.el.children[1], status = block.el.children[3];
  const details = find(block.el, (n) => n.tag === "details");
  const [url, model] = all(details, (n) => n.tag === "input");
  const save = find(details, (n) => n.tag === "button" && n.textContent === "保存");
  const path = all(block.el, (n) => n.tag === "input")[0];
  const addBtn = find(block.el, (n) => n.tag === "button" && n.textContent === "加入");
  out.goBeforeTheSetting = go.disabled;
  await tick();
  out.where = [where.className, where.textContent, go.disabled];
  out.summary = details.children[0].className + " " + details.children[0].children.map((c) => c.className || c.textContent).join("|");
  out.setting = [url.value, url.placeholder, model.value, model.placeholder, url.dataset.initial, model.dataset.initial];
  const datalist = find(block.el, (n) => n.tag === "datalist");
  const asked = () => [datalist.children.map((o) => o.value), calls.filter((c) => c[1] === "/api/canvas?scope=all").length];
  out.suggestions = [];
  for (let i = 0; i < 3; i++) { await path.fire("focus"); out.suggestions.push(asked()); }
  out.help = find(block.el, (n) => n.className === "team-help").textContent;
  path.value = "./code/run.py";
  let e = key("Enter", true);
  await path.fire("keydown", e);
  out.composing = [path.value, e.prevented];
  e = key("Enter");
  await path.fire("keydown", e);
  const chips = find(block.el, (n) => n.className === "verdict-chips");
  out.added = [path.value, e.prevented, chips.children.map((c) => c.textContent)];
  // The researcher's own description stays; the result is drafted and counts as typed.
  desc.value = "我自己写的";
  answer = { fields: { description: { text: "OLS", sources: ["code/run.py"] },
    result: { text: "beta = 0.12", sources: ["code/run.py", "out/t.csv"] } }, dropped: {}, model: "qwen-27b", cut: ["code/run.py"] };
  path.value = "out/t.csv"; // picked from the suggestions, never 加入'd: chosen all the same
  let pending = go.fire("click");
  await tick();
  out.busy = [go.disabled, status.textContent];
  release();
  await pending;
  out.sent = calls.filter((c) => c[1] === "/api/attempts/draft").map((c) => c[2]);
  out.cells = [desc.value, result.value, desc.dataset.typed || null, result.dataset.typed || null];
  out.form = form.children.map((c) => c.tag === "div" ? c.textContent : c.tag);
  out.status = [status.className, status.textContent, go.disabled];
  await result.fire("input");
  out.afterAnEdit = form.children.map((c) => c.tag);
  // A draft that arrives once the form froze the block (its preview being made or standing) fills nothing.
  desc.value = "";
  result.value = "";
  answer = { fields: { description: { text: "late", sources: ["code/run.py"] }, result: { text: "", sources: [] } },
    dropped: { result: "empty" }, model: "qwen-27b", cut: [] };
  pending = go.fire("click");
  await tick();
  block.setFrozen(true);
  out.frozen = [go.disabled, path.disabled, addBtn.disabled, chips.children.map((c) => c.disabled), url.disabled, model.disabled, save.disabled];
  release();
  await pending;
  out.late = [desc.value, status.className, status.textContent, go.disabled];
  block.setFrozen(false);
  out.unfrozen = [go.disabled, path.disabled, save.disabled];
  // Enter in 模型 saves both fields (never previews the row); the line above 生成草稿 follows.
  model.value = "m2";
  model.dataset.typed = "1";
  e = key("Enter");
  await model.fire("keydown", e);
  await tick();
  out.saved = [e.prevented, calls.filter((c) => c[0] === "POST" && c[1] === "/api/settings/llm").map((c) => c[2]),
    model.dataset.typed || null, model.dataset.initial, where.className, where.textContent];
  // A refusal names the file (apiError carries it from the body); the engine's text goes behind 「详情」.
  global.apiPost = async (path) => { throw apiError(path, { status: 404 },
    { error: "no such file: code/rnu.py", state: "draft_file_missing", file: "code/rnu.py" }); };
  await go.fire("click");
  out.refused = [status.textContent, status.err, go.disabled];
  // A member's row written while a draft runs: the late draft fills nothing and says the row is written.
  global.apiPost = async () => new Promise((r) => { release = () => r(answer); });
  desc.value = "";
  answer = { fields: { description: { text: "late", sources: ["code/run.py"] }, result: { text: "", sources: [] } },
    dropped: { result: "empty" }, model: "qwen-27b", cut: [] };
  pending = go.fire("click");
  await tick();
  block.markWritten();
  release();
  await pending;
  out.written = [desc.value, status.className, status.textContent, go.disabled, path.disabled];
  // The setting cannot be read: 生成草稿 stays off; opening 模型服务 asks again.
  settingFails = true;
  const other = attemptDraftBlock({ description: el("textarea"), result: el("textarea") }, [], true, {});
  const go2 = find(other.el, (n) => n.tag === "button" && n.textContent === "生成草稿");
  const details2 = find(other.el, (n) => n.tag === "details");
  await tick();
  out.unread = [other.el.children[1].textContent, go2.disabled];
  settingFails = false;
  details2.open = true;
  await details2.fire("toggle");
  await tick();
  out.reread = [other.el.children[1].textContent, go2.disabled];
  process.stdout.write(JSON.stringify(out));
})();
"""


@needs_node
def test_16_the_draft_block_fills_only_empty_cells_and_never_a_frozen_form():
    """attemptDraftBlock and 「模型服务」 under node: where a draft goes is said
    before anything is sent (and 生成草稿 waits for it); suggestions are asked
    until one answer; Enter while composing adds nothing; the request carries
    the files and the table's language, never example rows; a typed cell is
    kept, an empty one filled, marked typed for the leave guard, with the
    model's own claim beside it until edited; a draft that lands while the
    preview stands fills nothing; the frozen form freezes the block; Enter in
    the setting saves both fields; a refusal names the file; an unread
    setting keeps 生成草稿 off until 模型服务 reads it."""
    out = _draft_run(None, _DRAFT_DOM + _app_function("function apiError(") + _DRAFT_FORM)
    assert out["goBeforeTheSetting"] is True
    assert out["where"] == ["attempt-draft-where warn", "起草会把所选文件的内容发到 studio.local，它不在这台 Mac 上；模型 qwen", False]
    assert out["summary"] == "res-draft-summary res-chevron|模型服务"
    assert out["setting"] == ["http://studio.local:1234/v1", "http://studio.local:1234/v1", "", "qwen",
                              "http://studio.local:1234/v1", ""]
    # Asked again after a failed answer, never after a good one; only files a draft can read.
    assert out["suggestions"] == [[[], 1], [["code/run.py", "out/t.csv"], 2], [["code/run.py", "out/t.csv"], 2]]
    assert out["help"].startswith("模型只起草「做了什么」和「结果」，")
    assert out["composing"] == ["./code/run.py", False]
    assert out["added"] == ["", True, ["code/run.py 去掉"]]
    assert out["busy"] == [True, "正在请模型起草…可能要等一两分钟"]
    assert out["sent"] == [{"files": ["code/run.py", "out/t.csv"], "language": "zh"}]
    assert out["cells"] == ["我自己写的", "beta = 0.12", None, "1"]
    assert out["form"] == ["textarea", "textarea", "草稿（qwen-27b）。模型说它依据：code/run.py、out/t.csv。写入前请核对。"]
    assert out["status"] == ["panel-status ok", "「做了什么」原本有内容，没有改。 已填入「结果」。 较长的文件只读了开头。", False]
    assert out["afterAnEdit"] == ["textarea", "textarea"]
    assert out["frozen"] == [True, True, True, [True, True], True, True, True]
    assert out["late"] == ["", "panel-status err", "预览已经生成，草稿没有填入：先返回修改，再生成草稿", True]
    assert out["unfrozen"] == [False, False, False]
    assert out["saved"] == [True, [{"base_url": "http://studio.local:1234/v1", "model": "m2"}], None, "m2",
                            "attempt-draft-where", "起草用这台 Mac 上的模型 m2"]
    assert out["refused"] == ["项目里找不到 code/rnu.py", "draft_file_missing", False]
    assert out["written"] == ["", "panel-status ", "这一行已经写进工作副本，草稿没有用上", True, True]
    assert out["unread"] == ["没能读到模型设置，先不能起草", True]
    assert out["reread"] == ["起草会把所选文件的内容发到 studio.local，它不在这台 Mac 上；模型 qwen", False]


def test_16_1_the_form_freezes_the_draft_block_and_builds_it_for_new_rows_only():
    """The wiring the node run above cannot reach: setFormDisabled freezes
    the block; the block is frozen before the preview is asked for (and
    thawed on each way back without one); a member's written row marks it;
    a row being updated has no block."""
    form = _app_function("function renderAttemptForm(")
    assert 'const draftBlock = mode === "append" ? attemptDraftBlock(' in form
    disable = form[form.index("function setFormDisabled("):]
    assert "if (draftBlock) draftBlock.setFrozen(disabled);" in disable[: disable.index("\n  }\n")]
    submit = form[form.index('form.addEventListener("submit"'):]
    frozen = submit[submit.index("if (draftBlock) draftBlock.setFrozen(true);"):]
    assert frozen.index("setFrozen(true)") < frozen.index('apiPost("/api/attempts/preview"')
    assert frozen.count("return;") == frozen.count("unfreezeDraft();") == 3  # every way back before renderPreview
    assert "if (draftBlock) draftBlock.markWritten();" in form


def test_16_the_served_draft_block_sends_no_example_rows_and_names_no_address():
    """The page sends no `examples` (16.2: they leaked into drafts) and holds
    no address literal (the placeholder comes from the setting at run time)."""
    block = _app_block(*_DRAFT_BLOCK)
    assert "examples" not in block and "slice(-3)" not in block
    assert re.search(r"https?://", block) is None
    assert ".local" not in block and "LM Studio" in block  # the remedy names LM Studio; nothing decides locality here
