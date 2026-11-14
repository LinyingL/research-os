"""DESIGN.md 22.11 (task V17, the researcher: "需要可以自主创建tex"): 「新建文稿」 on 正文. The engine's `new_tex` -- each
template written through the human write path and, for the two papers, exported to PDF for real; every path it must
refuse, writing nothing; a chapter's \\input put into its main file as a save (LF, CRLF, another folder) or, when it
cannot be, said why; the default folder -- the endpoint (the project echo, a read-only project, the scan after the
answer) and the page's form under node (one primary at a time, a redraw keeps it, 创建 opens the editor). Every test
names its scenario."""

from __future__ import annotations

import dataclasses
import hashlib
import http.client
import json
import os
import shutil
import subprocess
import threading
import time
import unicodedata
from pathlib import Path

import pytest

from researchos import history, latex_edit, latex_export, names, outputs, people, project
from researchos.records.identity import read_identity
from researchos.webapp import server
from test_v17_latex_editor import _DRIVER, NODE, WEBAPP


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    project.init_project(root)
    (root / "paper").mkdir()
    return root


def _put(root: Path, rel: str, data: bytes) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(data)


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink()}


def _versions(root: Path, rel: str) -> list[str]:
    with history.Store.open(read_identity(root).identity.id) as store:
        return [v.sha256 for v in store.versions("file:" + rel)]


def _new(root: Path, **body) -> dict:
    return latex_edit.new_tex(root, {"folder": "paper", **body})


# -- the templates ------------------------------------------------------------------------------------------------------


def test_each_kind_is_written_from_its_template_in_utf8_with_lf_and_a_history_entry(root):
    zh = _new(root, kind="zh_paper", name="main", title="人民币 & 50% 的_路径")
    en = _new(root, kind="en_paper", name="english.tex", title="")
    ch = _new(root, kind="chapter", folder="paper/chapters", name="related_work")
    assert (zh["path"], en["path"], ch["path"]) == ("paper/main.tex", "paper/english.tex", "paper/chapters/related_work.tex")
    assert zh["folder_created"] is None and ch["folder_created"] == "paper/chapters" and zh["input"] is None
    zh_text, en_text, ch_text = ((root / r["path"]).read_bytes() for r in (zh, en, ch))
    for raw in (zh_text, en_text, ch_text):
        assert b"\r" not in raw and raw.decode("utf-8")
    assert zh_text.decode().startswith("\\documentclass[12pt]{ctexart}\n\\usepackage{amsmath}\n\\usepackage{graphicx}\n"
                                       "\\usepackage{booktabs}\n")
    # the title is escaped, so it cannot break the file; the author is left for the person; no bibliography
    assert "\\title{人民币 \\& 50\\% 的\\_路径}\n\\author{}\n" in zh_text.decode()
    assert "\\section{引言}" in zh_text.decode() and "\\begin{abstract}" in zh_text.decode()
    assert en_text.decode().startswith("\\documentclass[12pt]{article}\n\\usepackage[T1]{fontenc}\n")
    assert "\\section{Introduction}" in en_text.decode() and "\\title{}" in en_text.decode()
    for raw in (zh_text, en_text):
        assert b"bibliograph" not in raw and b"biblatex" not in raw and raw.endswith(b"\\end{document}\n")
    assert ch_text == "\\section{related\\_work}\n在这里写这一节。\n".encode()
    # the human write path: one history entry each, the revision the sha256 of what was written, no backup (nothing replaced)
    for r, raw in ((zh, zh_text), (en, en_text), (ch, ch_text)):
        assert r["revision"] == hashlib.sha256(raw).hexdigest() and _versions(root, r["path"]) == [r["revision"]]
    assert not (root / ".researchos/backups/latex").exists()
    # 正文 lists them: the two papers as main files, the chapter (in no main file yet) with the rest
    got = latex_edit.drafts(root)
    assert [g["main"]["path"] for g in got["groups"]] == ["paper/english.tex", "paper/main.tex"]
    assert [r["path"] for r in got["others"]] == ["paper/chapters/related_work.tex"]


def test_a_chapter_gets_its_title_as_its_section_and_the_english_line_for_a_pdflatex_main(root):
    _new(root, kind="en_paper", name="main")
    ch = _new(root, kind="chapter", name="intro", title="Data", main="paper/main.tex")
    assert (root / ch["path"]).read_text() == "\\section{Data}\nWrite this section here.\n"
    en = latex_edit.new_tex(root, {"kind": "chapter", "folder": "", "name": "loose"}, "en")
    assert (root / en["path"]).read_text() == "\\section{loose}\nWrite this section here.\n"


def test_the_default_folder_is_the_document_folder_else_paper_else_manuscript(root, tmp_path, monkeypatch):
    assert latex_edit.drafts(root)["new_folder"] == "paper"   # the document folder of 19: the first of reports/manuscript/paper that exists
    empty = tmp_path / "empty"
    empty.mkdir()
    project.init_project(empty)
    assert latex_edit.drafts(empty)["new_folder"] == "reports"

    def unreadable(_root):
        raise outputs._bad("unreadable")  # noqa: SLF001

    monkeypatch.setattr(outputs, "folders", unreadable)
    assert latex_edit.new_folder(root) == "paper" and latex_edit.new_folder(empty) == "manuscript"


# -- what it refuses -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("folder, name, code", [
    ("..", "x", "path"),
    ("paper/../..", "x", "path"),
    (".researchos", "x", "path"),
    (".git", "x", "path"),
    ("paper/.hidden", "x", "path"),
    ("linked", "x", "path"),                 # a symbolic link to a folder outside the project
    ("alias", "x", "path"),                  # and to one inside it
    ("new/deeper", "x", "too_deep"),         # two folder levels that do not exist
    ("paper", "x.TEX", "path"),              # upper-case suffix: refused, not renamed
    ("paper", "x.Tex", "path"),
    ("paper", "a\x00b", "path"),
    ("paper", "a\x07b", "path"),
    ("paper", "a\nb", "path"),
    ("paper", "a/b", "path"),
    ("paper", "a\\b", "path"),
    ("paper", "-x", "path"),
    ("paper", ".tex", "path"),
    ("paper", "", "path"),
    ("paper", "50%", "path"),                # TeX's special characters: \input{50%} ends the paper's compile
    ("paper", "a}b", "path"),
    ("paper", "a{b", "path"),
    ("paper", "a#b", "path"),
    ("paper", "a~b", "path"),
    ("paper", "a$b", "path"),
    ("paper", "a^b", "path"),
    ("paper", "a&b", "path"),
    ("50% done", "x", "path"),               # in a folder too
    ("paper", "a\u2028b", "path"),            # a line separator: the history could not name it
    ("paper", "a\x85b", "path"),              # NEL, a C1 control
    ("paper", "a\u202eb", "path"),            # a right-to-left override: the path would read otherwise
    ("paper", "a\u200bb", "path"),            # a zero-width space
    ("paper", "\udc80", "path"),              # a lone surrogate: it does not encode
    ("paper", "x" * 177, "path"),            # 181 bytes with .tex: 180 leave room for a save's backup and its temporary copy
    ("paper", "引" * 59, "path"),             # 177 bytes, 181 with .tex
    ("x" * 181, "x", "path"),                # a folder too
    ("paper/main.tex", "x", "path"),         # a file is not a folder
    ("cloudy", "x", "cloud"),                # a folder only in the cloud
    ("/etc", "x", "path"),                   # an absolute folder is not read as one inside the project
    ("paper/ chapters", "x.tex", "path"),    # nor a folder spelt with a space at its edge
    ("paper", "main", "exists"),
    ("paper", "MAIN.tex", "exists"),         # another case: that file on a Mac, a clash once synced to one from Linux
    (None, "x", "invalid"),
])
def test_every_path_the_editor_could_not_open_is_refused_and_nothing_is_written(root, tmp_path, folder, name, code):
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, root / "linked")
    (root / "inside").mkdir()
    os.symlink(root / "inside", root / "alias")
    _put(root, "paper/main.tex", b"\\documentclass{article}\n\\begin{document}\n\\end{document}\n")
    (root / ".cloudy.icloud").write_bytes(b"")
    before = _files(root)
    with pytest.raises(latex_edit.WorkspaceError) as caught:
        latex_edit.new_tex(root, {"kind": "chapter", "folder": folder, "name": name})
    assert caught.value.code == code, (caught.value.code, str(caught.value))
    if code == "exists":
        assert caught.value.status == 409 and caught.value.message_zh.startswith("已经有这个文件了：paper/")
    assert _files(root) == before and list(outside.iterdir()) == []


def test_each_refusal_says_what_is_wrong_in_english_too(root):
    for folder, name, en, zh in [
        ("paper", "50%", "cannot contain % { } # ~ $ ^ &", "不能含 % { } # ~ $ ^ &"),
        ("paper", "a\u2028b", "an invisible character", "看不见的字符"),
        ("paper", "-x", "cannot start with . or -", "不能以 . 或 - 开头"),
        ("paper", "x.TEX", "lower-case .tex", "小写的 .tex"),
        ("paper", "x" * 300, "at most 180 bytes", "最多 180 字节"),
    ]:
        with pytest.raises(latex_edit.WorkspaceError) as caught:
            latex_edit.new_tex(root, {"kind": "chapter", "folder": folder, "name": name}, "en")
        assert en in str(caught.value) and zh in caught.value.message_zh, str(caught.value)
        assert "cannot be a new draft" not in str(caught.value) and "\\u" not in str(caught.value)


def test_an_existing_folder_typed_in_another_case_or_unicode_form_is_taken_as_it_is_spelt_on_disk(root):
    nfd = unicodedata.normalize("NFD", "résumé")
    (root / nfd).mkdir()
    got = _new(root, kind="chapter", folder=unicodedata.normalize("NFC", "résumé"), name="b")
    assert got["path"] == nfd + "/b.tex" and got["folder_created"] is None and os.listdir(root / nfd) == ["b.tex"]
    got = _new(root, kind="chapter", folder="Paper", name="x")
    assert got["path"] == "paper/x.tex" and (root / "paper/x.tex").is_file() and got["folder_created"] is None
    # and the file itself is still never written over in another case or form
    for name in ("X.tex", "B"):
        with pytest.raises(latex_edit.WorkspaceError) as caught:
            _new(root, kind="chapter", folder="paper" if name == "X.tex" else nfd, name=name)
        assert caught.value.code == "exists"


def test_a_name_with_a_space_and_one_of_180_bytes_are_made_and_saved(root):
    assert _new(root, kind="chapter", name="my chap")["path"] == "paper/my chap.tex"
    long = _new(root, kind="chapter", name="x" * 176)
    assert long["path"] == "paper/" + "x" * 176 + ".tex"
    # a save of it (a temporary copy and a backup beside it) still has room for their names
    saved = latex_edit.save_tex(root, {"path": long["path"], "revision": long["revision"],
                                       "edits": [{"from": 0, "to": 0, "insert": "% x\n"}]})
    assert saved["backup"] and (root / long["path"]).read_text().startswith("% x\n")


def test_a_kind_or_title_that_does_not_hold_and_a_main_for_a_paper_are_refused(root):
    for body in ({"kind": "slides", "name": "x"}, {"kind": "zh_paper", "name": "x", "title": "a\nb"},
                 {"kind": "zh_paper", "name": "x", "title": "x" * 201}, {"kind": "zh_paper", "name": "x", "main": "paper/m.tex"},
                 {"kind": "zh_paper", "name": "x", "title": "a\u2028b"}, {"kind": "zh_paper", "name": "x", "title": "\udc80"},
                 {"kind": "chapter", "name": "x", "main": 3}):
        with pytest.raises(latex_edit.WorkspaceError) as caught:
            _new(root, **body)
        assert caught.value.code == "invalid"
    assert not (root / "paper/x.tex").exists()


def test_a_member_outside_their_working_copy_cannot_create_a_draft(root):
    assert latex_edit.drafts(root)["can_write"] is True
    people.team_path(root).write_text('[[member]]\nid="pi@example.com"\nname="PI"\nrole="pi"\n\n'
                                      '[[member]]\nid="member@example.com"\nname="Member"\nrole="member"\n')
    people.write_me("Member", "member@example.com")
    with pytest.raises(latex_edit.WorkspaceError) as caught:
        _new(root, kind="zh_paper", name="main")
    assert (caught.value.code, caught.value.status) == ("read_only", 403) and not (root / "paper/main.tex").exists()
    assert latex_edit.drafts(root)["can_write"] is False   # so the page does not offer 新建文稿


# -- a chapter into its main file ------------------------------------------------------------------------------------------


MAIN = "\\documentclass{ctexart}\n\\begin{document}\n正文。\n% \\end{document} in a comment\n\\end{document}\n"


@pytest.mark.parametrize("ending", ["\n", "\r\n"])
def test_the_input_goes_on_its_own_line_before_end_document_in_the_main_files_own_line_ending(root, ending):
    original = MAIN.replace("\n", ending).encode()
    _put(root, "paper/main.tex", original)
    got = _new(root, kind="chapter", folder="paper/chapters", name="intro", main="paper/main.tex")
    assert got["input"] == {"main": "paper/main.tex", "line": "\\input{chapters/intro}", "added": True, "message": None}
    after = (root / "paper/main.tex").read_bytes()
    cut = original.rindex(b"\\end{document}")   # the one outside the comment: every other byte stays
    assert after == original[:cut] + b"\\input{chapters/intro}" + ending.encode() + original[cut:]
    # a save of the main file: its backup in its own series, a history entry of what it is now
    series = root / ".researchos/backups" / latex_edit.backup_series("paper/main.tex")
    assert any(p.read_bytes() == original for p in series.iterdir())
    assert _versions(root, "paper/main.tex")[0] == hashlib.sha256(after).hexdigest()   # newest first
    assert [g["files"] for g in latex_edit.drafts(root)["groups"]][0][0]["path"] == "paper/chapters/intro.tex"


def test_the_input_is_relative_to_the_main_files_folder_and_splits_a_line_it_shares(root):
    _put(root, "paper/main.tex", b"\\documentclass{article}\n\\begin{document}\nText. \\end{document}\n")
    got = _new(root, kind="chapter", folder="chapters", name="method", main="paper/main.tex")
    assert got["path"] == "chapters/method.tex" and got["input"]["line"] == "\\input{../chapters/method}"
    assert (root / "paper/main.tex").read_text() == "\\documentclass{article}\n\\begin{document}\nText. \n" \
                                                    "\\input{../chapters/method}\n\\end{document}\n"
    assert (root / "chapters/method.tex").read_text() == "\\section{method}\nWrite this section here.\n"   # a pdfLaTeX main


@pytest.mark.parametrize("body, before", [
    ("\\input{a}\n\\printbibliography\n", "\\printbibliography"),
    ("\\input{a}\n  \\bibliographystyle{plain}\n\\bibliography{refs}\n", "  \\bibliographystyle{plain}"),
    ("\\section{A}\n\\appendix\n\\section{B}\n", "\\appendix"),
    ("\\begin{thebibliography}{9}\n\\bibitem{x} X.\n\\end{thebibliography}\n", "\\begin{thebibliography}{9}"),
    # a style named at the top of the body, a bibliography in a comment: the paper goes on, the line goes at the end
    ("\\bibliographystyle{plain}\n\\section{A}\n\\input{a}\n", "\\end{document}"),
    ("\\input{a}\n% \\printbibliography\n", "\\end{document}"),
])
def test_the_input_goes_before_the_appendix_or_the_bibliography_not_after_the_references(root, body, before):
    _put(root, "paper/main.tex", ("\\documentclass{article}\n\\begin{document}\n" + body + "\\end{document}\n").encode())
    assert _new(root, kind="chapter", folder="paper", name="new", main="paper/main.tex")["input"]["added"]
    lines = (root / "paper/main.tex").read_text().split("\n")
    assert lines[lines.index("\\input{new}") + 1] == before and lines.count("\\input{new}") == 1


@pytest.mark.parametrize("main, why", [
    (b"\\documentclass{article}\n\\begin{document}\nno end\n", "它的 \\begin{document} 之后没有 \\end{document}"),
    # the only one is in a definition in the preamble: not where the body ends
    (b"\\documentclass{article}\n\\newcommand{\\finish}{\\end{document}}\n\\begin{document}\nText.\n\\finish\n",
     "它的 \\begin{document} 之后没有 \\end{document}"),
    (b"\\documentclass{article}\n\\end{document}\n", "它里面没有 \\begin{document}"),
    (b"\\documentclass{article}\r\n\\begin{document}\n\\end{document}\n", latex_edit.READ_ONLY["mixed"][0]),
    (b"\\documentclass{article}\n\\begin{document}\n\\end{document}\n", latex_edit.READ_ONLY["generated"][0]),
])
def test_a_main_file_that_cannot_take_the_line_is_left_alone_and_the_chapter_is_still_made(root, main, why):
    _put(root, "paper/main.tex", main)
    if why == latex_edit.READ_ONLY["generated"][0]:
        (root / "research/results").mkdir(parents=True, exist_ok=True)
        (root / "research/results/abc.outputs.json").write_text(json.dumps(
            {"version": 1, "items": [{"kind": "table", "path": "paper/main.tex"}]}))
    got = _new(root, kind="chapter", name="intro", main="paper/main.tex")
    assert got["path"] == "paper/intro.tex" and (root / "paper/intro.tex").is_file()
    assert got["input"]["added"] is False
    assert got["input"]["message"] == f"没有在 paper/main.tex 里加入 \\input{{intro}}：{why}"
    assert (root / "paper/main.tex").read_bytes() == main
    assert not (root / ".researchos/backups" / latex_edit.backup_series("paper/main.tex")).exists()


def test_the_reason_is_said_in_english_on_an_english_screen(root):
    _put(root, "paper/main.tex", b"\\documentclass{article}\n\\begin{document}\n")
    got = latex_edit.new_tex(root, {"kind": "chapter", "folder": "paper", "name": "intro", "main": "paper/main.tex"}, "en")
    assert got["input"]["message"] == ("\\input{intro} was not added to paper/main.tex: "
                                       "it has no \\end{document} after \\begin{document}")


# Where TeX does not end on the first \end{document} outside a comment: the line is not added, and the editor says why.
SEVERAL_ENDS = {
    "verb": "\\documentclass{article}\n\\begin{document}\nUse \\verb|\\end{document}| to end.\nText.\n\\end{document}\n",
    "verbatim": "\\documentclass{article}\n\\begin{document}\n\\begin{verbatim}\n\\end{document}\n\\end{verbatim}\nText.\n"
                "\\end{document}\n",
    "iffalse": "\\documentclass{article}\n\\begin{document}\nText.\n\\iffalse\n\\end{document}\n\\fi\nMore.\n\\end{document}\n",
}


@pytest.mark.parametrize("case", sorted(SEVERAL_ENDS))
def test_a_main_with_an_end_document_in_verb_verbatim_or_iffalse_is_left_alone_and_says_why(root, case):
    _put(root, "paper/main.tex", SEVERAL_ENDS[case].encode())
    got = _new(root, kind="chapter", name="ch", main="paper/main.tex")
    assert got["input"]["added"] is False and "不止一处 \\end{document}" in got["input"]["message"]
    assert (root / "paper/main.tex").read_text() == SEVERAL_ENDS[case] and (root / "paper/ch.tex").is_file()


def test_a_main_file_outside_the_project_is_named_as_typed_never_through_the_engines_folder(root):
    for main, lang, words in (("../x.tex", "zh", "这个章节没有加入主文件 ../x.tex："),
                              ("../x.tex", "en", "The chapter was not added to ../x.tex: ")):
        got = latex_edit.new_tex(root, {"kind": "chapter", "folder": "paper", "name": "q" + lang, "main": main}, lang)
        assert got["input"]["added"] is False and got["input"]["line"] is None
        assert got["input"]["message"].startswith(words) and "\\input" not in got["input"]["message"]
        assert os.path.basename(os.getcwd()) not in got["input"]["message"]


def test_a_title_pdflatex_cannot_set_is_kept_in_a_comment_and_a_neutral_heading_written(root):
    _new(root, kind="en_paper", name="main")
    ch = _new(root, kind="chapter", name="引言", main="paper/main.tex")
    assert (root / ch["path"]).read_text() == ("% 标题：引言（pdfLaTeX 排不出其中的字符，请改写成英文）\n"
                                               "\\section{Section}\nWrite this section here.\n")
    typed = latex_edit.new_tex(root, {"kind": "chapter", "folder": "paper", "name": "data", "title": "数据",
                                      "main": "paper/main.tex"}, "en")
    assert (root / typed["path"]).read_text().startswith(
        "% Title: 数据 (pdfLaTeX cannot set its characters; write it in English)\n\\section{Section}\n")
    en = _new(root, kind="en_paper", name="english", title="人民币 & Trade")
    assert "% 标题：人民币 & Trade（pdfLaTeX 排不出其中的字符，请改写成英文）\n\\title{}\n" in (root / en["path"]).read_text()
    # Latin-1 is set by T1; a XeLaTeX paper takes any title
    assert "\\title{Café}" in (root / _new(root, kind="en_paper", name="cafe", title="Café")["path"]).read_text()
    assert "\\title{人民币}" in (root / _new(root, kind="zh_paper", name="zh", title="人民币")["path"]).read_text()


PDFLATEX = shutil.which("pdflatex") or ("/Library/TeX/texbin/pdflatex" if os.access("/Library/TeX/texbin/pdflatex", os.X_OK)
                                        else None)


def _pdflatex(folder: Path, main: str) -> subprocess.CompletedProcess:
    return subprocess.run([PDFLATEX, "-interaction=nonstopmode", "-halt-on-error", "-no-shell-escape", main], cwd=folder,
                          capture_output=True, text=True, timeout=120, env={**os.environ, "HOME": str(folder)})


@pytest.mark.skipif(PDFLATEX is None, reason="needs pdfLaTeX (TeX Live)")
def test_a_main_file_compiles_with_the_input_lines_just_added_to_it(root, tmp_path):
    _new(root, kind="en_paper", name="main", title="Currency & Trade")
    for name, title in (("my chap", ""), ("引言", ""), ("data", "数据"), ("résumé", "")):
        assert _new(root, kind="chapter", name=name, title=title, main="paper/main.tex")["input"]["added"]
    text = (root / "paper/main.tex").read_text()
    assert "\\input{my chap}\n\\input{引言}\n\\input{data}\n\\input{résumé}\n\\end{document}\n" in text
    build = tmp_path / "build"   # a copy: TeX writes nothing in the project
    shutil.copytree(root / "paper", build)
    done = _pdflatex(build, "main.tex")
    assert done.returncode == 0, done.stdout[-3000:]
    log = (build / "main.log").read_text(errors="replace")
    for chapter in ("my chap.tex", "data.tex"):
        assert chapter in log.replace("\n", "")


# -- the two papers export for real ---------------------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.skipif(latex_export.tex_bin() is None or not os.access(latex_export.SANDBOX_EXEC, os.X_OK),
                    reason="needs TeX Live (latexmk) and /usr/bin/sandbox-exec on this Mac")
def test_both_paper_templates_and_a_chapter_brought_in_export_to_pdf(tmp_path, monkeypatch):
    monkeypatch.setenv(names.HOME_ENV_VAR, str(tmp_path / "rhome"))
    monkeypatch.setenv("HOME", str(tmp_path / "uhome"))
    root = tmp_path / "exported"   # made under these homes, as the export tests make theirs
    root.mkdir()
    project.init_project(root)
    _new(root, kind="zh_paper", name="main", title="人民币国际化")
    _new(root, kind="chapter", folder="paper/chapters", name="intro", title="数据", main="paper/main.tex")
    (root / "english").mkdir()
    _new(root, kind="en_paper", folder="english", name="main", title="Currency & Trade")
    zh = latex_export.export(root, {"path": "paper/chapters/intro.tex"}, "zh")
    assert zh["exported"] and zh["engine"] == "xelatex" and zh["main"] == "paper/main.tex", zh["log"][-3000:]
    en = latex_export.export(root, {"path": "english/main.tex"}, "zh")
    assert en["exported"] and en["engine"] == "pdflatex", en["log"][-3000:]
    for answer in (zh, en):
        assert answer["errors"] == [] and (root / answer["pdf"]).read_bytes().startswith(b"%PDF")


# -- the endpoint -----------------------------------------------------------------------------------------------------------


@pytest.fixture
def served(root):
    httpd = server.build_server(root, 0)
    scans: list[Path] = []
    httpd.start_scan = lambda target: scans.append(target.root)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd, root, scans
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _post(port: int, body: dict, lang: str = "zh"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        conn.request("POST", "/api/latex/new", body=json.dumps(body).encode(),
                     headers={"Content-Type": "application/json", "X-ResearchOS-Lang": lang})
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read())
    finally:
        conn.close()


def _get(port: int, path: str) -> dict:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        assert resp.status == 200
        return json.loads(resp.read())
    finally:
        conn.close()


def test_the_endpoint_creates_answers_then_scans_and_refuses_another_project_an_existing_file_and_a_read_only_one(served):
    httpd, root, scans = served
    port = httpd.server_address[1]
    status, got = _post(port, {"project": str(root), "kind": "zh_paper", "folder": "paper", "name": "main"})
    assert status == 200 and got["path"] == "paper/main.tex" and isinstance(got["generation"], int)
    for _ in range(200):   # the scan starts once the answer is sent
        if scans:
            break
        time.sleep(0.01)
    assert scans == [root]
    status, refused = _post(port, {"project": str(root), "kind": "zh_paper", "folder": "paper", "name": "main"}, "en")
    assert (status, refused["state"], refused["message"]) == (409, "workspace_exists", "paper/main.tex already exists")
    # a chapter brought into that main file: the save of the main file takes the same (re-entrant) project lock
    status, got = _post(port, {"project": str(root), "kind": "chapter", "folder": "paper", "name": "intro", "main": "paper/main.tex"})
    assert status == 200 and got["input"]["added"] and "\\input{intro}\n\\end{document}" in (root / "paper/main.tex").read_text()
    status, refused = _post(port, {"project": "/elsewhere", "kind": "zh_paper", "folder": "paper", "name": "other"})
    assert (status, refused["state"]) == (409, "workspace_project_changed")
    # a name the history could not record, or one that does not encode, is refused before anything is written
    for name in ("a\u2028b", "\udc80", "x" * 300):
        status, refused = _post(port, {"project": str(root), "kind": "chapter", "folder": "paper", "name": name}, "en")
        assert (status, refused["state"]) == (400, "workspace_path"), refused
    assert sorted(os.listdir(root / "paper")) == ["intro.tex", "main.tex"]
    assert _get(port, "/api/latex/drafts")["can_write"] is True
    httpd.set_served(dataclasses.replace(httpd.get_served(), read_only=True))
    assert _get(port, "/api/latex/drafts")["can_write"] is False   # 新建文稿 is not offered
    status, refused = _post(port, {"project": str(root), "kind": "zh_paper", "folder": "paper", "name": "other"})
    assert (status, refused["state"]) == (409, "read_only")
    assert not (root / "paper/other.tex").exists() and len(scans) == 2


# -- the page's form --------------------------------------------------------------------------------------------------------


_FORM = r"""
const host = mk('section'); host.dataset = {};
const row = (path) => ({path, modified: null, cloud: false, supported: 0, waiting: 0});
const answer = {groups: [{main: row('paper/main.tex'), files: [row('paper/chapters/intro.tex')]}], others: [row('Notes/old.tex')],
  generated: 0, truncated: false, limit: 2000, message: null, new_folder: 'paper'};
const show = async () => { const p = L.loadDrafts(host); await tick(); gets.shift().res(answer); await p; await tick(); };
const visible = (el) => { const seen = []; const walk = (e) => { if (!e || typeof e === 'string' || e.removed || /\bhidden\b/.test(e.className)) return; seen.push(e); e.children.forEach(walk); }; walk(el); return seen; };
const primaries = () => visible(host).filter((e) => /btn-primary/.test(e.className)).map((e) => e.textContent);
const inputs = () => visible(host).filter((e) => e.tag === 'input' && e.type === 'text');
const radio = (v) => all.filter((e) => e.tag === 'input' && e.type === 'radio' && e.value === v).pop();
currentView = 'drafts';
await show();
out.before = primaries();
visible(host).find((e) => e.textContent === '新建文稿').fire('click');
out.open = primaries();
out.fields = inputs().map((e) => e.value);
out.mainShown = visible(host).some((e) => e.tag === 'select');
out.folders = all.filter((e) => e.tag === 'option' && !e.textContent).map((e) => e.value);
radio('chapter').checked = true; radio('zh_paper').checked = false; radio('chapter').fire('change');
out.chapter = {fields: inputs().map((e) => e.value), main: visible(host).find((e) => e.tag === 'select').value};
inputs()[2].value = '数据';
// a redraw (a generation) keeps the form and what is typed in it, and the page's 新建文稿 stays hidden
await show();
out.redrawn = {fields: inputs().map((e) => e.value), primaries: primaries()};
// 取消 closes it; 新建文稿 is back
visible(host).find((e) => e.textContent === '取消').fire('click');
out.cancelled = {primaries: primaries(), inputs: inputs().length};
// again, as a paper: 创建 posts, a refusal is said in the form
visible(host).find((e) => e.textContent === '新建文稿').fire('click');
out.again = inputs().map((e) => e.value);
visible(host).find((e) => e.textContent === '创建').fire('click'); await tick();
out.firstPost = posts[0]; posts.shift().rej({state: 'workspace_exists', message: '已经有这个文件了：paper/main-2.tex'}); await tick();
out.refusal = all.filter((e) => /notice-status err/.test(e.className)).map((e) => e.textContent).pop();
radio('chapter').checked = true; radio('zh_paper').checked = false; radio('chapter').fire('change');
visible(host).find((e) => e.textContent === '创建').fire('click'); await tick();
out.secondPost = posts[0];
posts.shift().res({path: 'paper/intro-2.tex', input: {added: false, message: '没有在 paper/main.tex 里加入 \\input{intro-2}：它里面没有 \\end{document}'}});
await tick();
out.opened = gets[0].url; out.section = L.section();
gets.shift().res({path: 'paper/intro-2.tex', text: '\\section{intro-2}\n', revision: 'r1', line_ending: 'lf', read_only: false, links: [], mains: []});
await tick(); await tick();
out.note = byClass('latex-new-note').textContent;
out.noteHidden = byClass('latex-new-note').classList.contains('hidden');
// where the person cannot write, 新建文稿 is disabled and an open form is not kept
await show();
visible(host).find((e) => e.textContent === '新建文稿').fire('click');
answer.can_write = false;
await show();
out.readOnly = {disabled: visible(host).find((e) => e.textContent === '新建文稿').disabled === true, inputs: inputs().length,
  primaries: primaries()};
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def form() -> dict:
    if NODE is None:
        pytest.skip("node is not installed")
    prelude = _DRIVER.split("const out = {};")[0] % {"cm": json.dumps(str(WEBAPP / "vendor" / "codemirror.js")),
                                                     "latex": json.dumps(str(WEBAPP / "latex.js"))}
    script = (prelude + "const out = {};\n(async () => {\n" + _FORM
              + "\n})().catch((err) => { console.log(JSON.stringify({error: String(err && err.stack || err)})); });")
    got = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, check=True, timeout=60).stdout
    out = json.loads(got.strip().splitlines()[-1])
    assert "error" not in out, out.get("error")
    return out


def test_new_draft_is_the_pages_one_primary_and_the_forms_create_takes_over_while_it_is_open(form):
    assert form["before"] == ["新建文稿"] and form["open"] == ["创建"]
    assert form["redrawn"]["primaries"] == ["创建"] and form["cancelled"] == {"primaries": ["新建文稿"], "inputs": 0}


def test_the_form_starts_in_the_document_folder_with_main_tex_taken_names_numbered_and_folders_offered(form):
    assert form["fields"] == ["paper", "main-2.tex", ""] and form["mainShown"] is False
    assert form["folders"] == ["paper", "paper/chapters", "Notes"]   # as on disk, never folded
    assert form["chapter"] == {"fields": ["paper", "intro.tex", ""], "main": "paper/main.tex"}
    assert form["redrawn"]["fields"] == ["paper", "intro.tex", "数据"]
    assert form["again"] == ["paper", "main-2.tex", ""]


def test_create_posts_the_form_says_a_refusal_and_opens_the_new_file_saying_when_the_input_was_not_added(form):
    assert form["firstPost"]["url"] == "/api/latex/new"
    assert form["firstPost"]["body"] == {"project": "/p", "kind": "zh_paper", "folder": "paper", "name": "main-2.tex", "title": ""}
    assert form["refusal"].startswith("没能新建文稿")
    assert form["secondPost"]["body"] == {"project": "/p", "kind": "chapter", "folder": "paper", "name": "intro.tex",
                                          "title": "", "main": "paper/main.tex"}
    assert form["opened"] == "/api/latex/file?path=paper%2Fintro-2.tex" and form["section"] == "drafts"
    assert form["note"].startswith("没有在 paper/main.tex 里加入") and form["noteHidden"] is False


def test_new_draft_is_disabled_where_the_person_cannot_write(form):
    assert form["readOnly"] == {"disabled": True, "inputs": 0, "primaries": ["新建文稿"]}


def test_every_word_of_the_form_has_its_english():
    i18n = (WEBAPP / "i18n.js").read_text(encoding="utf-8")
    for zh, en in [("新建文稿", "New draft"), ("中文论文", "Chinese paper"), ("英文论文", "English paper"),
                   ("章节文件", "Chapter file"), ("文件夹", "Folder"), ("文件名", "File name"),
                   ("标题（可选）", "Title (optional)"), ("加入主文件", "Add to main file"), ("不加入", "Do not add"),
                   ("正在新建…", "Creating…"), ("没能新建文稿", "Could not create the draft"), ("创建", "Create"),
                   ("取消", "Cancel"), ("类型", "Type"),
                   ("这个项目里还没有 .tex 文稿。点「新建文稿」开始写，或把 .tex 文件放进项目文件夹。", "Click “New draft”")]:
        at = i18n.index(json.dumps(zh, ensure_ascii=False) + ":")
        assert en in i18n[at:at + 300], zh
