"""DESIGN.md 22.4 (task V17, B2a): export of a LaTeX draft to PDF, on the engine side. Each test names its scenario.

The exports here run for real: `latexmk` from this Mac's TeX Live, inside `/usr/bin/sandbox-exec` with the profile
`researchos.latex_export` writes. They are marked slow (a few seconds each, more for the first biber and LuaLaTeX run,
which unpack and cache) but run in the full suite; they are skipped only where TeX or the sandbox is not installed.
Every test points `RESEARCHOS_HOME` and `HOME` at throwaway folders shared by this module (so the TeX cache and biber's
unpacked copy are made once), and the last checks that nothing was written under the real `~/.researchos`.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import subprocess
import threading
import time
import zlib
from pathlib import Path

import pytest

from researchos import latex_export, names, project, settings
from researchos.records import papers, situation
from researchos.records.identity import read_identity
from researchos.webapp import server
from researchos.workspace import WorkspaceError

REAL_HOME = Path.home()
REAL_BUILDS = REAL_HOME / ".researchos" / "latex-builds"
REAL_BUILDS_BEFORE = REAL_BUILDS.exists()

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(latex_export.tex_bin() is None or not os.access(latex_export.SANDBOX_EXEC, os.X_OK),
                       reason="needs TeX Live (latexmk) and /usr/bin/sandbox-exec on this Mac"),
]

CTEX_MAIN = r"""\documentclass{ctexart}
\usepackage{graphicx}
\usepackage[backend=biber]{biblatex}
\addbibresource{refs.bib}
\begin{document}
\input{chapters/intro}
\includegraphics[width=2cm]{../figures/fig1.pdf}
\printbibliography
\end{document}
"""
INTRO = "人民币国际化的进展很慢，喆字也在 \\cite{li2020}。\n"
BIB = "@article{li2020, author={Li, A}, title={Currency}, journal={J}, year={2020}}\n"


@pytest.fixture(scope="module")
def shared(tmp_path_factory) -> dict[str, Path]:
    return {"home": tmp_path_factory.mktemp("export-researchos-home"), "user": tmp_path_factory.mktemp("export-user")}


@pytest.fixture
def root(tmp_path: Path, shared, monkeypatch) -> Path:
    monkeypatch.setenv(names.HOME_ENV_VAR, str(shared["home"]))
    monkeypatch.setenv("HOME", str(shared["user"]))
    root = tmp_path / "project"
    root.mkdir()
    project.init_project(root)
    return root


def _put(root: Path, rel: str, data: str | bytes) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)


def _figure(root: Path, rel: str) -> None:
    """A one-page PDF made by pdfLaTeX outside the project (the figure the paper includes)."""
    work = root.parent / "figure-work"
    work.mkdir(exist_ok=True)
    (work / "f.tex").write_text("\\documentclass{standalone}\\begin{document}FIG\\end{document}\n")
    subprocess.run([str(latex_export.tex_bin() / "pdflatex"), "-interaction=nonstopmode", "f.tex"], cwd=work,
                   capture_output=True, check=True, timeout=120)
    _put(root, rel, (work / "f.pdf").read_bytes())


def _listing(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and ".researchos" not in p.relative_to(root).parts}


def _export(root: Path, path: str = "paper/main.tex", **body) -> dict:
    return latex_export.export(root, {"path": path, **body}, "zh")


def _pdf_text(data: bytes) -> bytes:
    """The PDF's bytes with every Flate stream inflated, to look for a font name or a marker."""
    found = data
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
        try:
            found += zlib.decompress(m.group(1))
        except zlib.error:
            pass
    return found


def _simple(root: Path, body: str, rel: str = "paper/main.tex") -> None:
    _put(root, rel, "\\documentclass{article}\n" + body + "\n")


# -- a real paper ---------------------------------------------------------------------------------------------------


def test_a_chinese_paper_with_chapters_a_bibliography_and_a_figure_beside_it_exports_to_the_document_folder(root):
    """22.8 acceptance 3: chapters by `\\input`, biblatex with biber, a figure in `../figures`, Chinese via ctex. The
    export is asked from the chapter; XeLaTeX is preset; the PDF lands in the document folder (`paper/`, the first of
    reports/manuscript/paper that exists) and nothing else in the project changes; `[[export]]` records it."""
    _put(root, "paper/main.tex", CTEX_MAIN)
    _put(root, "paper/chapters/intro.tex", INTRO)
    _put(root, "paper/refs.bib", BIB)
    _put(root, "paper/latexmkrc", "system('touch " + str(root / "paper" / "rc-ran") + "');\n")  # -norc: never run
    _figure(root, "figures/fig1.pdf")
    before = _listing(root)
    answer = _export(root, "paper/chapters/intro.tex")
    assert answer["exported"], answer["log"][-3000:]
    assert answer["main"] == "paper/main.tex" and answer["engine"] == "xelatex"
    assert answer["pdf"] == "paper/main.pdf" and answer["message"] == "已导出 paper/main.pdf"
    assert answer["errors"] == [] and answer["missing_characters"] == []
    after = _listing(root)
    assert set(after) - set(before) == {"paper/main.pdf"}
    assert {k: v for k, v in after.items() if k != "paper/main.pdf"} == before
    pdf = (root / "paper/main.pdf").read_bytes()
    assert pdf.startswith(b"%PDF") and b"Songti" in _pdf_text(pdf)        # the system's Songti, 喆 not missing
    (entry,) = papers.exports(root)
    assert entry["id"].startswith("px-") and entry["main"] == "paper/main.tex" and entry["pdf"] == "paper/main.pdf"
    assert entry["pdf_sha256"] == hashlib.sha256(pdf).hexdigest() and entry["engine"] == "xelatex"
    assert entry["tex_sha256"] == hashlib.sha256((root / "paper/main.tex").read_bytes()).hexdigest()
    assert "via = \"app\"" in (root / ".researchos/papers.toml").read_text()
    # The build folder is outside the project, per main file, 0700, and the project's other tables still read.
    pid = read_identity(root).identity.id
    builds = list((Path(os.environ[names.HOME_ENV_VAR]) / "latex-builds").glob("p-*/*"))
    build = next(b for b in builds if b.name == hashlib.sha256(str((root / "paper/main.tex").resolve()).encode())
                 .hexdigest()[:16])
    assert build.stat().st_mode & 0o777 == 0o700 and (build / "main.log").is_file()
    assert build.parent.name == pid
    assert papers.marked(root)["trusted"]


def test_an_english_paper_with_natbib_and_bibtex_is_preset_to_pdflatex_and_goes_to_reports(root):
    (root / "reports").mkdir()
    _put(root, "manuscript/paper.tex", "\\documentclass{article}\n\\usepackage{natbib}\n\\begin{document}\n"
                                       "Hello \\citet{li2020}.\n\\bibliographystyle{plainnat}\n\\bibliography{refs}\n"
                                       "\\end{document}\n")
    _put(root, "manuscript/refs.bib", BIB)
    answer = _export(root, "manuscript/paper.tex")
    assert answer["exported"] and answer["engine"] == "pdflatex", answer["log"][-3000:]
    assert answer["pdf"] == "reports/paper.pdf" and (root / "reports/paper.pdf").read_bytes().startswith(b"%PDF")
    assert not (root / "manuscript/paper.pdf").exists() and not (root / "manuscript/paper.aux").exists()


# -- the sandbox ------------------------------------------------------------------------------------------------------


def test_reading_a_file_outside_the_project_is_refused_and_named(root, tmp_path):
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "s.tex").write_text("SECRETMARKER\n")
    for body in ("\\begin{document}\\input{" + str(secret / "s.tex") + "}\\end{document}",
                 "\\begin{document}\\input{/etc/hosts}\\end{document}"):
        _simple(root, body)
        answer = _export(root)
        assert not answer["exported"] and answer["message"] == "导出没有成功"
        assert any(n.startswith("读取了项目以外的文件 ") and n.endswith("，导出时不允许") for n in answer["notes"]), answer
        assert not (root / "paper/main.pdf").exists()
    # A symbolic link in the project that leads out of it passes TeX's own name check; the sandbox refuses it.
    os.symlink(secret / "s.tex", root / "paper/link.tex")
    _simple(root, "\\begin{document}\\input{link}\\end{document}")
    answer = _export(root)
    assert not answer["exported"] and f"读取了项目以外的文件 {root / 'paper/link.tex'}，导出时不允许" in answer["notes"]
    assert papers.exports(root) == []


def test_a_bib_file_outside_the_project_and_a_pdf_special_of_one_read_nothing(root, tmp_path):
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "s.bib").write_text("@article{z, author={S}, title={SECRETMARKER}, journal={J}, year={1}}\n")
    (secret / "s.txt").write_text("SECRETMARKER\n")
    _simple(root, "\\usepackage[backend=biber]{biblatex}\\addbibresource{" + str(secret / "s.bib") + "}\n"
                  "\\begin{document}\\nocite{*}\\printbibliography\\end{document}")
    answer = _export(root)
    assert not answer["exported"]
    assert f"读取了项目以外的文件 {secret / 's.bib'}，导出时不允许" in answer["notes"]
    _simple(root, "\\begin{document}x\\special{pdf:fstream @s (" + str(secret / "s.txt") + ")}"
                  "\\special{pdf:put @resources << /X @s >>}\\end{document}")
    answer = _export(root, engine="xelatex")
    assert answer["exported"] and f"读取了项目以外的文件 {secret / 's.txt'}，导出时不允许" in answer["notes"], (
        answer["notes"], answer["log"][-2000:])
    assert b"SECRETMARKER" not in _pdf_text((root / "paper/main.pdf").read_bytes())


def test_shell_escape_does_not_run_and_an_openout_outside_the_build_is_refused(root):
    _simple(root, "\\begin{document}\\immediate\\write18{touch " + str(root / "paper/pwned") + "}x\\end{document}")
    answer = _export(root)
    assert answer["exported"] and "文稿要求运行外部程序，导出时没有运行。" in answer["notes"]
    assert not (root / "paper/pwned").exists()
    _simple(root, "\\newwrite\\f\\begin{document}\\immediate\\openout\\f=" + str(root / "paper/out.tex")
                  + "\\immediate\\write\\f{x}\\immediate\\closeout\\f x\\end{document}")
    answer = _export(root)
    assert not answer["exported"] and not (root / "paper/out.tex").exists()
    assert answer["errors"][0]["path"] == "paper/main.tex" and "I can't write on file" in answer["errors"][0]["text"]
    _simple(root, "\\usepackage{minted}\\begin{document}\\begin{minted}{python}\nx = 1\n\\end{minted}\\end{document}")
    answer = _export(root)
    assert not answer["exported"] and "这个宏包需要运行外部程序，导出时不允许：minted" in answer["notes"]


def test_lualatex_is_never_preset_and_its_lua_can_neither_write_outside_the_build_nor_read_outside_the_project(
        root, tmp_path):
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "s.txt").write_text("SECRETMARKER\n")
    lua = ("local f = io.open('" + str(root / "paper/w.txt") + "', 'w') "
           "texio.write_nl('LUAWRITE:' .. (f and 'yes' or 'no')) "
           "local g = io.open('" + str(secret / "s.txt") + "') "
           "texio.write_nl('LUAREAD:' .. (g and 'yes' or 'no'))")
    _simple(root, "\\usepackage{fontspec}\\begin{document}x\\directlua{" + lua + "}\\end{document}")
    assert _export(root, pdf=None)["engine"] == "xelatex"                 # fontspec: XeLaTeX, never LuaLaTeX
    answer = _export(root, engine="lualatex")
    assert answer["exported"] and answer["engine"] == "lualatex", answer["log"][-3000:]
    assert "LUAWRITE:no" in answer["log"] and "LUAREAD:no" in answer["log"]
    assert not (root / "paper/w.txt").exists()
    # The person's choice is kept in this Mac's settings for that main file, and used next time.
    assert _export(root)["engine"] == "lualatex"
    kept = settings.read_settings()[latex_export.ENGINE_SETTING]
    assert kept[f"{read_identity(root).identity.id}/paper/main.tex"] == "lualatex"


def test_the_time_limit_kills_the_whole_group(root):
    _simple(root, "\\begin{document}\\def\\x{\\x}\\x\\end{document}")
    started = time.monotonic()
    answer = latex_export.export(root, {"path": "paper/main.tex"}, "en", timeout=3)
    assert time.monotonic() - started < 30
    assert not answer["exported"] and answer["stopped"] == "timeout"
    assert answer["message"] == "The export took longer than 3 seconds and was stopped."
    build = latex_export.build_dir(read_identity(root).identity.id, root / "paper/main.tex")
    assert (build / "main.log").is_file()                            # TeX did start
    left = subprocess.run(["/usr/bin/pgrep", "-f", str(build)], capture_output=True, text=True)
    assert left.stdout.strip() == "" and latex_export.stop() is False
    assert not (root / "paper/main.pdf").exists()


def test_a_missing_package_and_a_missing_character_are_named_and_a_file_line_error_links_into_the_editor(root):
    _simple(root, "\\usepackage{nosuchpackagexyz}\\begin{document}x\\end{document}")
    answer = _export(root)
    assert not answer["exported"] and "缺少宏包 nosuchpackagexyz" in answer["notes"]
    _put(root, "paper/main.tex", "\\documentclass{article}\n\\usepackage{fontspec}\n\\begin{document}\n喆 x\n"
                                 "\\end{document}\n")
    answer = _export(root)
    assert answer["exported"] and answer["missing_characters"] == ["喆"]
    assert answer["missing_message"] == "有 1 个字符字体里没有，PDF 里缺了：喆"
    _put(root, "paper/main.tex", "\\documentclass{article}\n\\begin{document}\n\\input{chapters/a}\n\\end{document}\n")
    _put(root, "paper/chapters/a.tex", "one\n\\undefinedmacro\n")
    answer = _export(root, engine="pdflatex")
    assert not answer["exported"]
    first = answer["errors"][0]
    assert (first["path"], first["line"]) == ("paper/chapters/a.tex", 2) and "Undefined control sequence" in first["text"]


# -- refusals and choices ---------------------------------------------------------------------------------------------


def test_without_the_sandbox_or_tex_nothing_runs_and_one_export_at_a_time(root, monkeypatch):
    _simple(root, "\\begin{document}x\\end{document}")
    sandbox, tex_bin = latex_export.SANDBOX_EXEC, latex_export.tex_bin
    monkeypatch.setattr(latex_export, "SANDBOX_EXEC", str(root / "no-sandbox-exec"))
    with pytest.raises(WorkspaceError) as caught:
        _export(root)
    assert caught.value.code == "no_sandbox" and caught.value.message_zh == "这台 Mac 上没法隔离导出，所以没有运行。"
    monkeypatch.setattr(latex_export, "SANDBOX_EXEC", sandbox)
    monkeypatch.setattr(latex_export, "tex_bin", lambda: None)
    with pytest.raises(WorkspaceError) as caught:
        _export(root)
    assert caught.value.code == "no_tex" and caught.value.message_zh == "这台 Mac 上没有找到 TeX（TeX Live 或 MacTeX）。"
    monkeypatch.setattr(latex_export, "tex_bin", tex_bin)
    assert latex_export._RUN_LOCK.acquire(blocking=False)  # noqa: SLF001 -- another export running
    try:
        with pytest.raises(WorkspaceError) as caught:
            _export(root)
        assert caught.value.code == "busy" and caught.value.status == 409
    finally:
        latex_export._RUN_LOCK.release()  # noqa: SLF001
    monkeypatch.setenv(situation.AGENT_RUN_ENV, "1")
    with pytest.raises(WorkspaceError) as caught:
        _export(root)
    assert caught.value.code == "agent"
    assert not (root / "paper/main.pdf").exists() and papers.exports(root) == []


def test_the_tex_folder_is_this_macs_setting_first(root, tmp_path):
    fake = tmp_path / "tex" / "bin"
    fake.mkdir(parents=True)
    (fake / "latexmk").write_text("#!/bin/sh\n")
    (fake / "latexmk").chmod(0o755)
    settings.update_setting(latex_export.TEX_SETTING, str(fake))
    assert latex_export.tex_bin() == fake.resolve()
    settings.update_setting(latex_export.TEX_SETTING, str(tmp_path / "none"))
    assert latex_export.tex_bin() == Path("/Library/TeX/texbin").resolve()
    # The sandbox reads the installation a bin folder belongs to, never a folder as wide as /usr or /opt.
    assert latex_export.tex_tree(Path("/usr/local/texlive/2023/bin/universal-darwin")) == Path("/usr/local/texlive/2023")
    assert latex_export.tex_tree(Path("/usr/bin")) == Path("/usr/bin")
    assert latex_export.tex_tree(Path("/opt/bin/x")) == Path("/opt/bin/x")


def test_the_main_file_is_asked_when_there_are_several_or_none_and_the_choice_is_kept(root):
    _put(root, "paper/shared.tex", "Shared text.\n")
    for name in ("a", "b"):
        _put(root, f"paper/{name}.tex", "\\documentclass{article}\n\\begin{document}\n\\input{shared}\n\\end{document}\n")
    answer = _export(root, "paper/shared.tex")
    assert answer == {"exported": False, "ask": "main", "mains": ["paper/a.tex", "paper/b.tex"], "message": None}
    answer = _export(root, "paper/shared.tex", main="paper/b.tex")
    assert answer["exported"] and answer["main"] == "paper/b.tex" and answer["pdf"] == "paper/b.pdf"
    assert _export(root, "paper/shared.tex")["main"] == "paper/b.tex"   # kept for this draft on this Mac
    _put(root, "paper/alone.tex", "Not included anywhere.\n")
    answer = _export(root, "paper/alone.tex")
    assert answer["ask"] == "main" and answer["mains"] == ["paper/a.tex", "paper/b.tex"]
    assert answer["message"] == "找不到包含这个文件的主文件（带 \\documentclass 的 .tex）。"
    with pytest.raises(WorkspaceError):
        _export(root, "paper/alone.tex", main="paper/shared.tex")


def test_two_main_files_with_one_name_get_the_folder_in_the_pdf_name(root):
    (root / "reports").mkdir()
    for folder in ("paper", "slides"):
        _put(root, f"{folder}/main.tex", "\\documentclass{article}\n\\begin{document}\nx\n\\end{document}\n")
    assert _export(root, "slides/main.tex")["pdf"] == "reports/slides-main.pdf"


# -- the PDF already there ---------------------------------------------------------------------------------------------


def test_a_pdf_that_was_not_exported_is_not_replaced_without_asking(root):
    _simple(root, "\\begin{document}first\\end{document}")
    first = _export(root)
    assert first["exported"]
    second = _export(root)                                           # the last export: replaced without asking
    assert second["exported"] and second["pdf"] == "paper/main.pdf" and len(papers.exports(root)) == 2
    _put(root, "paper/main.pdf", b"%PDF-1.4 made elsewhere\n")
    asked = _export(root)
    assert asked == {"exported": False, "ask": "replace", "file": "paper/main.pdf", "save_as": "paper/main-导出.pdf",
                     "main": "paper/main.tex", "message": "paper/main.pdf 不是上次导出的文件，要替换它吗？"}
    assert (root / "paper/main.pdf").read_bytes() == b"%PDF-1.4 made elsewhere\n" and len(papers.exports(root)) == 2
    saved = _export(root, pdf="save_as")
    assert saved["exported"] and saved["pdf"] == "paper/main-导出.pdf"
    assert (root / "paper/main.pdf").read_bytes() == b"%PDF-1.4 made elsewhere\n"
    again = _export(root, pdf="save_as")                             # its own last export: replaced in place
    assert again["pdf"] == "paper/main-导出.pdf"
    replaced = _export(root, pdf="replace")
    assert replaced["exported"] and replaced["pdf"] == "paper/main.pdf"
    assert (root / "paper/main.pdf").read_bytes().startswith(b"%PDF") and len(papers.exports(root)) == 5
    backups = list((root / ".researchos/backups").rglob("main*.pdf"))
    assert any(b.read_bytes() == b"%PDF-1.4 made elsewhere\n" for b in backups)  # the replaced PDF is kept
    assert [e["pdf"] for e in papers.exports(root)] == ["paper/main.pdf", "paper/main.pdf", "paper/main-导出.pdf",
                                                        "paper/main-导出.pdf", "paper/main.pdf"]


def test_a_failed_run_leaves_the_project_as_it_was(root):
    _simple(root, "\\begin{document}\\undefinedmacro\\end{document}")
    before = _listing(root)
    answer = _export(root)
    assert not answer["exported"] and answer["message"] == "导出没有成功" and "Undefined control sequence" in answer["log"]
    assert _listing(root) == before and papers.exports(root) == []


# -- the endpoint -----------------------------------------------------------------------------------------------------


@pytest.fixture
def served(root):
    httpd = server.build_server(root, 0)
    scans: list[Path] = []
    httpd.start_scan = lambda target: scans.append(target.root)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1], root, scans
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _post(port: int, path: str, body: dict, lang: str = "zh") -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=200)
    try:
        conn.request("POST", path, body=json.dumps(body).encode(),
                     headers={"Content-Type": "application/json", "X-ResearchOS-Lang": lang})
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read())
    finally:
        conn.close()


def test_the_endpoint_exports_asks_and_refuses_another_project(served):
    port, root, scans = served
    _simple(root, "\\begin{document}x\\end{document}")
    status, refused = _post(port, "/api/latex/export", {"project": "/elsewhere", "path": "paper/main.tex"})
    assert status == 409 and refused["state"] == "workspace_project_changed" and scans == []
    status, answer = _post(port, "/api/latex/export", {"project": str(root), "path": "paper/main.tex"}, "en")
    assert status == 200 and answer["exported"] and answer["message"] == "Exported paper/main.pdf"
    assert isinstance(answer["generation"], int) and scans == [root]
    _put(root, "paper/main.pdf", b"%PDF other\n")
    status, answer = _post(port, "/api/latex/export", {"project": str(root), "path": "paper/main.tex"}, "en")
    assert status == 200 and answer["ask"] == "replace"
    assert answer["message"] == "paper/main.pdf is not the last export. Replace it?" and len(scans) == 1
    status, refused = _post(port, "/api/latex/export", {"project": str(root), "path": "../x.tex"})
    assert status == 400 and refused["state"] == "workspace_path"
    status, stopped = _post(port, "/api/latex/export/stop", {})
    assert status == 200 and stopped == {"stopped": False}


def test_nothing_was_written_under_the_real_research_os_home():
    assert REAL_BUILDS.exists() == REAL_BUILDS_BEFORE


# -- what the review of B2a found ---------------------------------------------------------------------------------------


def _lua_at_end(root: Path, lua: str) -> None:
    """A LuaLaTeX draft whose Lua runs at the very end of the run (`wrapup_run`), inside the sandbox."""
    _simple(root, "\\directlua{luatexbase.add_to_callback('wrapup_run', function() " + lua + " end, 'x')}"
                  "\\begin{document}hello\\end{document}")


def test_a_link_the_run_leaves_for_the_pdf_or_a_log_is_never_followed(root, tmp_path):
    secret = tmp_path / "secret-key"
    secret.write_text("PRIVATE KEY MATERIAL\n")
    out = "os.getenv('TEXMFOUTPUT') .. '/main"
    _lua_at_end(root, f"os.remove({out}.pdf') lfs.link('{secret}', {out}.pdf', true) "
                      f"lfs.link('{secret}', {out}.blg', true)")
    answer = _export(root, engine="lualatex")
    assert not answer["exported"] and answer["message"] == "导出没有成功"
    assert "PRIVATE KEY" not in json.dumps(answer, ensure_ascii=False)
    assert not (root / "paper/main.pdf").exists() and papers.exports(root) == []
    # A hard link, a FIFO, a file over the limit: nothing is read from any of them.
    build = latex_export.build_dir(read_identity(root).identity.id, root / "paper/main.tex")
    (build / "plain").write_bytes(b"%PDF-1.4 x")
    assert latex_export._build_file(build / "plain", 100) == b"%PDF-1.4 x"  # noqa: SLF001
    assert latex_export._build_file(build / "plain", 4) == b""  # noqa: SLF001
    assert latex_export._build_file(build / "plain", 4, tail=True) == b".4 x"  # noqa: SLF001
    os.link(build / "plain", build / "twice")
    assert latex_export._build_file(build / "twice", 100) == b""  # noqa: SLF001
    os.mkfifo(build / "fifo")
    assert latex_export._build_file(build / "fifo", 100) == b""  # noqa: SLF001


def test_a_link_the_run_leaves_for_its_marker_is_not_written_through(root, tmp_path):
    victim = tmp_path / "thesis-final.docx"
    victim.write_text("the person's thesis")
    _lua_at_end(root, f"lfs.link('{victim}', os.getenv('TEXMFOUTPUT') .. '/last-good-run', true)")
    answer = _export(root, engine="lualatex")
    assert answer["exported"], answer["log"][-2000:]
    assert victim.read_text() == "the person's thesis"
    marker = latex_export.build_dir(read_identity(root).identity.id, root / "paper/main.tex") / "last-good-run"
    assert not marker.is_symlink() and marker.read_text() == "lualatex"


def test_a_tex_cache_one_project_writes_is_never_read_by_another(root, tmp_path):
    planted = ("local d = os.getenv('TEXMFVAR') .. '/tex' lfs.mkdir(d) lfs.mkdir(d .. '/latex') "
               "local f = io.open(d .. '/latex/size10.clo', 'w') "
               "f:write('\\string\\\\typeout{PLANTED-BY-OTHER-PROJECT}\\string\\\\endinput') f:close() "
               "local t = io.open(os.getenv('TMPDIR') .. 'planted-tmp', 'w') t:write('x') t:close()")
    _simple(root, "\\directlua{" + planted + "}\\begin{document}x\\end{document}")
    _export(root, engine="lualatex")                                   # its own run reads what it planted
    other = tmp_path / "other"
    other.mkdir()
    project.init_project(other)
    _simple(other, "\\begin{document}hello\\end{document}")
    answer = _export(other)
    assert answer["exported"] and "PLANTED-BY-OTHER-PROJECT" not in answer["log"]
    mine = latex_export.cache_dirs(latex_export.build_dir(read_identity(root).identity.id, root / "paper/main.tex"))
    theirs = latex_export.cache_dirs(latex_export.build_dir(read_identity(other).identity.id, other / "paper/main.tex"))
    assert (mine[1] / "planted-tmp").exists() and not (theirs[1] / "planted-tmp").exists()
    assert set(mine).isdisjoint(theirs)


def test_a_refused_sandbox_profile_says_the_export_did_not_run(root, monkeypatch):
    _simple(root, "\\begin{document}x\\end{document}")
    monkeypatch.setattr(latex_export, "sandbox_profile", lambda *_args: "(version 1)(bogus)")
    with pytest.raises(WorkspaceError) as caught:
        _export(root)
    assert caught.value.code == "no_sandbox" and caught.value.message_zh == "这台 Mac 上没法隔离导出，所以没有运行。"
    assert not latex_export._RUN_LOCK.locked() and papers.exports(root) == []  # noqa: SLF001


def test_an_endless_log_stops_at_the_file_limit_and_only_its_end_is_read(root, monkeypatch):
    monkeypatch.setattr(latex_export, "PDF_LIMIT", 4 * 1024 * 1024)
    _simple(root, "\\begin{document}\\loop\\message{" + "x" * 900 + "}\\iftrue\\repeat\\end{document}")
    started = time.monotonic()
    answer = latex_export.export(root, {"path": "paper/main.tex"}, "en", timeout=120)
    assert time.monotonic() - started < 60 and answer["stopped"] is None and not answer["exported"]
    build = latex_export.build_dir(read_identity(root).identity.id, root / "paper/main.tex")
    assert (build / "main.log").stat().st_size <= 4 * 1024 * 1024 and len(answer["log"]) <= 20000


def test_the_run_cannot_see_whether_a_file_outside_what_it_reads_exists(root, tmp_path):
    (tmp_path / "secret.txt").write_text("x" * 1234)
    lua = (f"texio.write_nl('META-OUT:' .. tostring(lfs.attributes('{tmp_path / 'secret.txt'}', 'size'))) "
           f"texio.write_nl('META-IN:' .. tostring(lfs.attributes('{root / 'paper/main.tex'}', 'mode')))")
    _simple(root, "\\begin{document}x\\directlua{" + lua + "}\\end{document}")
    answer = _export(root, engine="lualatex")
    assert answer["exported"] and "META-OUT:nil" in answer["log"] and "META-IN:file" in answer["log"]


def test_twin_main_files_in_folders_of_one_name_get_their_whole_folder_path(root):
    for folder in ("a/paper", "b/paper"):
        _put(root, f"{folder}/main.tex", "\\documentclass{article}\n\\begin{document}\nx\n\\end{document}\n")
    assert latex_export.pdf_name(root, "a/paper/main.tex", "reports") == "reports/a-paper-main.pdf"
    assert latex_export.pdf_name(root, "b/paper/main.tex", "reports") == "reports/b-paper-main.pdf"


def test_one_missing_character_is_said_in_the_singular():
    log = "Missing character: There is no 喆 (U+5586) in font [lmroman10-regular]:+tlig;!\n"
    read = latex_export.read_log(Path("/nonexistent"), "main.tex", log, "en")
    assert read["missing_message"] == "1 character is not in the font and is missing from the PDF: 喆"
    read = latex_export.read_log(Path("/nonexistent"), "main.tex", log + log.replace("喆", "龘"), "en")
    assert read["missing_message"] == "2 characters are not in the font and are missing from the PDF: 喆 龘"


def test_a_tex_folder_outside_a_tex_installation_is_read_alone(tmp_path):
    home_bin = tmp_path / "home" / "bin" / "tex"
    home_bin.mkdir(parents=True)
    assert latex_export.tex_tree(home_bin) == home_bin                  # never the whole home folder
    (tmp_path / "home" / "texmf-dist").mkdir()
    assert latex_export.tex_tree(home_bin) == tmp_path / "home"


def test_a_project_switched_during_the_run_gets_no_pdf(root, tmp_path, monkeypatch):
    httpd = server.build_server(root, 0)
    httpd.start_scan = lambda target: None
    other = tmp_path / "other"
    other.mkdir()
    project.init_project(other)
    real_run = latex_export.run

    def run_then_switch(*args, **kwargs):
        done = real_run(*args, **kwargs)
        httpd.set_served(server.served_for(other))                     # another window opened another project
        return done

    monkeypatch.setattr(latex_export, "run", run_then_switch)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        _simple(root, "\\begin{document}x\\end{document}")
        status, refused = _post(httpd.server_address[1], "/api/latex/export", {"project": str(root),
                                                                              "path": "paper/main.tex"})
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    assert status == 409 and refused["state"] == "workspace_project_changed"
    assert not (root / "paper/main.pdf").exists() and papers.exports(root) == []


# -- the page's settings line (B2b) -------------------------------------------------------------------------------------


def test_the_settings_line_reads_the_main_file_the_engine_and_the_last_pdf_and_writes_nothing(root):
    """22.4: what 「导出 PDF」 would use is shown before it runs: the main file (none while the person has to choose), the
    engine kept or preset (XeLaTeX for ctex), and 「导出的 PDF」 once there is one; reading it changes no
    setting and no file."""
    _put(root, "paper/shared.tex", "Shared text.\n")
    for name, cls in (("a", "ctexart"), ("b", "article")):
        _put(root, f"paper/{name}.tex", f"\\documentclass{{{cls}}}\n\\begin{{document}}\n\\input{{shared}}\n\\end{{document}}\n")
    before, kept = _listing(root), settings.read_settings()
    seen = latex_export.choices(root, "paper/shared.tex")
    assert seen == {"main": None, "mains": ["paper/a.tex", "paper/b.tex"], "engine": None,
                    "engines": ["pdflatex", "xelatex", "lualatex"], "last": None}
    seen = latex_export.choices(root, "paper/a.tex")
    assert seen["main"] == "paper/a.tex" and seen["engine"] == "xelatex" and seen["last"] is None
    assert _listing(root) == before and settings.read_settings() == kept
    assert _export(root, "paper/shared.tex", main="paper/b.tex", engine="xelatex")["exported"]
    seen = latex_export.choices(root, "paper/shared.tex")
    assert seen["main"] == "paper/b.tex" and seen["engine"] == "xelatex" and seen["last"]["pdf"] == "paper/b.pdf"
    (root / "paper/b.pdf").unlink()
    assert latex_export.choices(root, "paper/shared.tex")["last"] is None   # a PDF no longer there is not listed
    with pytest.raises(WorkspaceError):
        latex_export.choices(root, "../x.tex")


def test_the_settings_line_endpoint_answers_for_the_open_project_only(served):
    port, root, _scans = served
    _simple(root, "\\begin{document}x\\end{document}")
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        conn.request("GET", "/api/latex/export?path=paper/main.tex")
        resp = conn.getresponse()
        answer = json.loads(resp.read())
        conn.request("GET", "/api/latex/export?path=paper/main.tex&project=/elsewhere")
        other = conn.getresponse()
        other.read()
    finally:
        conn.close()
    assert resp.status == 200 and answer["main"] == "paper/main.tex" and answer["engine"] == "pdflatex"
    assert other.status == 409
