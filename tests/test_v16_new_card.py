"""Research OS V16, task B4-3 (DESIGN.md 21.7, 「变量」): 「新建变量卡」 creates the draft card through the code that
`researchos variable new` runs (`cards.new_card`), and the 变量 page tells nobody to use a terminal for it. The route is
compared with the command on two projects, the command's refusals come back as sentences in words in both languages with
the engine's English kept behind 「详情」, and the page's form and its three kinds of project (one person, a member, an
empty page) run under node. Every test names its scenario."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from researchos import cli
from researchos.records import cards
from researchos.records import variables as V
from researchos.webapp import server, variables_api

from test_v5_phase9 import _call, _confirmed, live  # noqa: F401 -- fixture of the card endpoint tests
from test_v10_desk_panel import _PANEL_DOM
from test_v9_language import APP_HTML, CJK, FILL_VARS_LINE, NODE, T_LINE, _app_function, _i18n_source, js_literals, needs_node
from test_variable_cards import _project, _text


def _w(lang: str):
    return lambda zh, en: en if lang == "en" else zh


def _served(root: Path):
    httpd = server.build_server(root, 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread, f"http://127.0.0.1:{httpd.server_address[1]}"


def _files(root: Path) -> dict[str, bytes]:
    """The project's own files, by place and bytes (the engine's index is outside the project)."""
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# -- what it creates: the command and the route compared ------------------------------------------------------------


@pytest.mark.parametrize("fixture", ["a project with no cards", "a project with a confirmed card"])
def test_the_route_creates_exactly_what_the_command_creates(tmp_path, fixture):
    """The same project twice, one card made by `researchos variable new`, the other by 「新建变量卡」: the card's folder holds
    the same one file with the same bytes (the commented template), the card is a draft v1 with an empty log in the
    command's own overview, and nothing else of the project's own files differs."""
    roots = {"command": tmp_path / "command", "route": tmp_path / "route"}
    for root in roots.values():
        _project(root)
        if fixture.endswith("confirmed card"):
            _confirmed(root, "topicshift", _text())
    before = {kind: _files(root) for kind, root in roots.items()}
    assert cli.main(["variable", "new", "rv", "--path", str(roots["command"])]) == 0
    httpd, thread, base = _served(roots["route"])
    try:
        status, made = _call(base, "POST", "/api/variables/new", {"id": "rv"})
        overview = {"route": _call(base, "GET", "/api/variables")[1]}
        card = _call(base, "GET", "/api/variables/card?id=rv")   # what the page opens right after: the new draft, before any rescan
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    assert status == 200 and made["ok"] is True and made["id"] == "rv" and made["version"] == 1
    assert made["file"] == ".researchos/variables/rv/v1.toml"
    added = {kind: sorted(set(_files(root)) - set(before[kind])) for kind, root in roots.items()}
    assert added["command"] == added["route"] == [".researchos/variables/rv/v1.toml"]
    after = {kind: _files(root) for kind, root in roots.items()}
    assert after["command"][made["file"]] == after["route"][made["file"]] == cards.template(1)
    # every other file is as it was in both: the command and the route write one file
    for kind in roots:
        assert {k: v for k, v in after[kind].items() if k != made["file"]} == before[kind], kind
    # the card is a draft with no log in the route's list as it is in the command's own overview
    row = next(r for r in overview["route"]["cards"] if r["id"] == "rv")
    assert (row["draft"], row["in_use"], row["abandoned"], row["questions"]) == (1, None, False, 0)
    assert card[0] == 200 and (card[1]["draft"], card[1]["in_use"], [v["status"] for v in card[1]["versions"]]) == (1, None, ["draft"])
    assert not (V.variables_dir(roots["route"]) / "rv" / "log.toml").exists()
    assert not (V.variables_dir(roots["command"]) / "rv" / "log.toml").exists()


def test_the_page_lists_no_terminal_command_for_an_empty_page(live):  # noqa: F811
    """The list the 变量 page reads carries no command to type: the old `empty_help` block (a sentence and
    `researchos variable new <id>`) is gone from the payload."""
    base, _root = live
    status, data = _call(base, "GET", "/api/variables")
    assert status == 200 and "empty_help" not in data and "researchos variable" not in json.dumps(data, ensure_ascii=False)
    assert not hasattr(variables_api, "EMPTY_HELP")


# -- what it refuses, in words ---------------------------------------------------------------------------------------


def _refuse(base: str, card_id, lang: str):
    return _call(base, "POST", "/api/variables/new", {"id": card_id}, {"X-ResearchOS-Lang": lang})


def test_an_invalid_id_is_refused_with_the_commands_reason_in_words_in_both_languages(live, capsys):  # noqa: F811
    """Each rule of `cards.new_card` (the command's own refusals) as the form says it: length, a first character, a
    character, two dots. 400 `card_invalid`, nothing written; the sentence is in the request's language and the engine's
    English (the command's text) is the error's own message, behind 「详情」."""
    base, root = live
    before = _files(root)
    cases = [
        ("", "编号要有 1 到 64 个字符。", "An id is 1 to 64 characters long."),
        ("x" * 65, "编号要有 1 到 64 个字符。", "An id is 1 to 64 characters long."),
        (".hidden", "编号不能以下划线、点或短横线开头。", "An id may not start with an underscore, a dot or a hyphen."),
        ("_a", "编号不能以下划线、点或短横线开头。", "An id may not start with an underscore, a dot or a hyphen."),
        ("-a", "编号不能以下划线、点或短横线开头。", "An id may not start with an underscore, a dot or a hyphen."),
        ("my var", "编号里不能有空格。", "An id may not hold a space."),
        ("a/b", "编号只能用字母、数字、下划线、短横线和点，不能含「/」。",
         "An id may hold only letters, digits, underscores, hyphens and dots, not “/”."),
        ("a..b", "编号里不能有连续的两个点。", "An id may not hold two dots in a row."),
    ]
    for card_id, zh, en in cases:
        for lang, sentence in (("zh", zh), ("en", en)):
            status, body = _refuse(base, card_id, lang)
            assert status == 400 and body["state"] == "card_invalid", (card_id, lang)
            assert body["message_zh"] == sentence, (card_id, lang)
            assert body["error"].startswith(repr(card_id) + ": an id "), body["error"]
        # the command refuses the same id with the same engine sentence (an empty id cannot be typed after `new`)
        if card_id:
            capsys.readouterr()
            assert cli.main(["variable", "new", "--path", str(root), "--", card_id]) == 1
            assert body["error"] in capsys.readouterr().err
    assert _files(root) == before and not [m for m in CJK.findall(json.dumps(_refuse(base, "a b", "en")[1], ensure_ascii=False))
                                         if m not in "「」"]


def test_a_taken_id_is_refused_up_to_letter_case_with_the_card_named(live):  # noqa: F811
    """`topicshift` exists: `TOPICSHIFT` is refused as the command refuses it (ids compare case-folded), 409
    `card_exists`, naming the card that is there, in both languages; the card is untouched."""
    base, root = live
    before = _files(root)
    status, body = _refuse(base, "TOPICSHIFT", "zh")
    assert status == 409 and body["state"] == "card_exists"
    assert body["message_zh"] == "已经有一张叫「topicshift」的变量卡了（编号不分大小写）。"
    assert "already exists (ids compare case-folded)" in body["error"]
    status, body = _refuse(base, "topicshift", "en")
    assert body["message_zh"] == "A variable card called “topicshift” already exists (ids are not case-sensitive)."
    assert _files(root) == before


def test_an_id_the_index_still_holds_or_that_has_backups_is_refused_in_words(live):  # noqa: F811
    """A card whose folder was removed but whose log the index applied is not free to use (the command says so in
    English); nor is an id with backups of its own. Both are refused with their reason, in both languages."""
    base, root = live
    _confirmed_rv = _text(field="rv").replace("TopicShift（叙事更替）", "RV")
    cards.new_card(root, "rv")
    (V.variables_dir(root) / "rv" / "v1.toml").write_text(_confirmed_rv, encoding="utf-8")
    cards.confirm(root, "rv", attested="yes")
    # the folder is gone but the index applied its log (the command's own case, test_variable_cards)
    shutil.rmtree(V.variables_dir(root) / "rv")
    status, body = _refuse(base, "RV", "zh")
    assert status == 409 and body["state"] == "card_exists"
    assert body["message_zh"] == "索引里还留着变量卡「rv」，请先回答它的问题，再用这个编号。"
    assert "the index still holds a variable card 'rv'" in body["error"]
    assert _refuse(base, "RV", "en")[1]["message_zh"] == \
        "The index still holds a variable card called “rv”. Answer its question first, then use the id."


def test_an_id_with_backups_of_its_own_is_refused_in_words(tmp_path):
    """Backups of a card under `.researchos/backups/variables/<id>` mean the id was used: refused with that said."""
    root = tmp_path / "p"
    _project(root)
    snaps = root / ".researchos" / "backups" / V.SNAPSHOT_SUBDIR / "old"
    snaps.mkdir(parents=True)
    (snaps / "v1.toml.20260101T000000000000Z.toml").write_text("x", encoding="utf-8")
    httpd, thread, base = _served(root)
    try:
        status, body = _refuse(base, "OLD", "zh")
        english = _refuse(base, "old", "en")[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    assert status == 409 and body["state"] == "card_exists"
    assert body["message_zh"] == "「OLD」这个编号以前用过，留有备份，不能再用。"
    assert english["message_zh"] == "The id “old” was used before and its backups remain, so it cannot be used again."
    assert not (V.variables_dir(root) / "old").exists()


def test_a_request_without_a_text_id_is_refused_and_a_read_only_project_writes_nothing(tmp_path):
    """The route needs an id as text; a project opened read-only refuses before anything is written (the same
    guard as the other card actions)."""
    root = tmp_path / "p"
    _project(root)
    httpd, thread, base = _served(root)
    try:
        status, body = _call(base, "POST", "/api/variables/new", {"id": 5})
        assert status == 400 and body["state"] == "card_invalid"
        assert not V.variables_dir(root).exists()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


# -- the page ------------------------------------------------------------------------------------------------------


def test_the_variable_page_has_no_sentence_that_sends_the_person_to_a_terminal():
    """21.7: no string the 变量 view shows (its list, its empty page, its form) names a `researchos ...` command or a
    terminal; its comments may say what the command is."""
    src = APP_HTML.read_text(encoding="utf-8")
    view = src[src.index("const varState = {"):src.index("// -- end of the 变量 view")]
    shown = [raw for _start, _quote, raw in js_literals(view)]
    assert [s for s in shown if "researchos " in s or "终端" in s or "命令" in s] == []
    assert "empty_help" not in view and '"/api/variables/new"' in view and "新建变量卡" in view


# -- the form, on the stand-in DOM ---------------------------------------------------------------------------------

_FORM_PRELUDE = r"""
global.window = {};
%(i18n)s
%(dom)s
global.setStatus = (s, cls, text, err) => { s.className = "notice-status " + cls; s.textContent = text; s.err = err ? (err.messageZh || err.message) : null; };
global.humanRecordsBlocked = () => global.blocked || null;
global.calls = [];
global.refuse = null;
global.apiPost = async (path, body) => { calls.push(["POST", path, body]); if (refuse) throw refuse; return { ok: true, id: body.id, generation: 7 }; };
global.openedCards = [];
global.openVariableCard = (id) => openedCards.push(id);
let lastGeneration = 0;
global.tick = () => new Promise((r) => setTimeout(r, 0));
global.byClass = (host, c) => host.all().filter((k) => k.cls.has(c));
global.text = (e) => e.textContent;
%(block)s
"""


def _form(lang: str, steps: str) -> dict:
    i18n = f"window.ROI18N_SAVED = {json.dumps(lang)};\n" + _i18n_source() + "\n" + FILL_VARS_LINE + "\n" + T_LINE
    block = _app_function("function newCardBlock(")
    script = (_FORM_PRELUDE % {"i18n": i18n, "dom": _PANEL_DOM, "block": block}
              + "\n(async () => {\n const out = {};\n" + steps + "\n process.stdout.write(JSON.stringify(out));\n})();\n")
    done = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout)


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_the_new_card_form_asks_for_the_id_says_the_commands_rules_and_opens_the_new_draft(lang):
    """On the 变量 page 「新建变量卡」 is a button (the primary one when it is the page's only action); it opens a small form --
    the id, the command's rules in words, what happens after, 创建 and 取消 --; the id is sent trimmed to
    POST /api/variables/new and the new draft card opens; the project's generation is taken from the answer."""
    w = _w(lang)
    out = _form(lang, r"""
const only = newCardBlock(true), more = newCardBlock(false);
out.buttons = [byClass(only, "btn")[0].className, byClass(more, "btn")[0].className, text(byClass(only, "btn")[0])];
const form = byClass(only, "var-new-form")[0];
out.hiddenAtFirst = form.cls.has("hidden");
byClass(only, "btn")[0].click();
out.opened = [form.cls.has("hidden"), global.focused === byClass(form, "form-input")[0]];
const id = byClass(form, "form-input")[0];
out.field = [id.getAttribute("aria-label"), id.placeholder, text(byClass(form, "var-new-label")[0])];
out.rules = byClass(form, "var-new-rules").map(text);
out.actions = byClass(form, "var-new-actions")[0].kids.map((b) => [text(b), b.type, b.className]);
id.value = "  topicshift ";
await form.on.submit[0]({ preventDefault() {} });
await tick();
out.posted = calls; out.cards = openedCards; out.generation = lastGeneration;
""")
    assert out["buttons"] == ["btn btn-primary", "btn btn-small", w("新建变量卡", "New variable card")]
    assert out["hiddenAtFirst"] is True and out["opened"] == [False, True]
    assert out["field"] == [w("变量编号", "Variable id"), w("例如 topicshift", "For example topicshift"), w("变量编号", "Variable id")]
    assert out["rules"] == [
        w("编号是这张卡的名字：1 到 64 个字符，只能用字母、数字、下划线、短横线和点，不能以下划线、点或短横线开头，也不能有连续的两个点；不分大小写，不能和已有的卡重名。",
          "The id is the card’s name: 1 to 64 characters, using only letters, digits, underscores, hyphens and dots, not starting with an "
          "underscore, a dot or a hyphen, and without two dots in a row. It is not case-sensitive, and it cannot be the id of a card that exists."),
        w("创建以后会打开这张卡的草稿：它从带说明的模板开始，定义由你在编辑器里写，写好后在卡片页确认。",
          "After you create it, the card’s draft opens. It starts from a commented template; you write the definition in your editor and confirm it on the card’s page.")]
    assert out["actions"] == [[w("创建", "Create"), "submit", "btn btn-primary btn-small"], [w("取消", "Cancel"), "button", "btn btn-small"]]
    assert out["posted"] == [["POST", "/api/variables/new", {"id": "topicshift"}]] and out["cards"] == ["topicshift"] and out["generation"] == 7


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_a_refused_id_is_shown_as_the_engines_sentence_and_the_form_stays_for_another_try(lang):
    """A taken or invalid id: the sentence the engine worded (its `message_zh`, in the request's language) is the page's, the
    buttons are usable again, no card opens; 取消 puts the form away."""
    out = _form(lang, r"""
const box = newCardBlock(false), form = byClass(box, "var-new-form")[0];
const id = byClass(form, "form-input")[0], status = byClass(form, "notice-status")[0];
const [make, cancel] = byClass(form, "var-new-actions")[0].kids;
refuse = Object.assign(new Error("a variable card 'topicshift' already exists"), { messageZh: "SENTENCE", state: "card_exists" });
id.value = "TopicShift";
await form.on.submit[0]({ preventDefault() {} });
await tick();
out.refused = [status.className, status.textContent, status.err, make.disabled, cancel.disabled, openedCards.length];
cancel.click();
out.away = [form.cls.has("hidden"), status.textContent];
""")
    assert out["refused"] == ["notice-status err", _w(lang)("没能新建变量卡", "Could not create the variable card"), "SENTENCE", False, False, 0]
    assert out["away"] == [True, ""]


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_a_members_card_is_made_in_their_working_copy_and_the_form_says_how_it_reaches_the_pi(lang):
    """12.4: a member's new card is the same draft, written in their working copy; the form adds one sentence -- the PI sees
    it as a proposal (提议, 变量定义). A person who works alone, or leads, gets no such sentence."""
    w = _w(lang)
    out = _form(lang, r"""
const say = (box) => byClass(box, "var-new-rules").map(text).filter((s) => /PI/.test(s));
global.ROTeam = window.ROTeam = { mode: () => "member" };
out.member = say(newCardBlock(false));
global.ROTeam = window.ROTeam = { mode: () => "pi" };
out.pi = say(newCardBlock(false));
delete window.ROTeam;
out.alone = say(newCardBlock(false));
""")
    assert out["member"] == [w("你是这个项目的成员：新建的卡先写进你的工作副本。写好定义后，在「提议」里选「变量定义」，把它交给 PI。",
                             "You are a member of this project, so a new card is first written into your working copy. When the definition is written, "
                             "choose “Variable definition” under Proposals to hand it to the PI.")]
    assert out["pi"] == [] and out["alone"] == []


@needs_node
def test_a_project_opened_read_only_offers_no_new_card_and_says_why():
    """The same guard as the card page's other actions: where nothing can be written the button is not offered, the sentence is."""
    out = _form("zh", r"""
global.blocked = "只读打开：不会写入任何东西";
const box = newCardBlock(true);
out.words = [byClass(box, "btn").length, text(box)];
""")
    assert out["words"] == [0, "只读打开：不会写入任何东西"]
