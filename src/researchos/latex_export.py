"""Export of a LaTeX draft to PDF (DESIGN.md 22.4, task V17 B2).

Asked by a person only (POST /api/latex/export; no agent endpoint, no compiling on save). The main file is the draft
itself or the `.tex` that reaches it through includes (`latex_edit.main_files`); one `latexmk` runs it, confined by the
operating system: `/usr/bin/sandbox-exec` with a profile written for the run that lets it read only the project, the
TeX installation, the font folders and the system, write only its build folder (outside the project) and the TeX cache,
and reach no network. `-norc` keeps a project `latexmkrc` from running as Perl; shell escape is never on. The run is in
a session of its own, killed whole after 180 seconds, one at a time. The PDF goes to the project's document folder
through the human write path, replacing a PDF there only when it is the last export of that main file, and an
`[[export]]` entry in `papers.toml` records it.
"""

from __future__ import annotations

import contextlib
import glob
import hashlib
import os
import posixpath
import re
import resource
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

from researchos import latex_edit, outputs, paths, settings, workspace
from researchos.records import papers, situation
from researchos.workspace import WorkspaceError

TIMEOUT_S = 180
PDF_LIMIT = 512 * 1024 * 1024
LOG_TAIL = 2 * 1024 * 1024  # of the TeX log, biber's log and latexmk's output: their last part only
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
TEX_SETTING = "tex_path"
ENGINE_SETTING = "latex_export_engines"
MAIN_SETTING = "latex_export_mains"
ENGINES = ("pdflatex", "xelatex", "lualatex")
_ENGINE_FLAG = {"pdflatex": "-pdf", "xelatex": "-xelatex", "lualatex": "-lualatex"}
_UNICODE_RE = re.compile(r"\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{[^}]*\b(?:ctex|xeCJK|fontspec)\b"
                         r"|\\documentclass\s*(?:\[[^\]]*\])?\s*\{ctex[a-z]*\}")
_FILE_REFS_RE = re.compile(r"\\(includegraphics|addbibresource|bibliography)\s*(?:\[[^\]]*\])?\s*\{([^{}]*)\}")
_SHELL_PACKAGES = ("minted", "svg", "pythontex")

_RUN_LOCK = threading.Lock()
_running: dict[str, Any] = {}


def _say(lang: str, zh: str, en: str, **values: Any) -> str:
    return settings.say(zh, en, lang, **values)


def _refuse(code: str, zh: str, en: str, status: int = 409) -> WorkspaceError:
    return WorkspaceError(code, en, status, zh)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# -- before running ---------------------------------------------------------------------------------------------------

def tex_bin() -> Path | None:
    """The TeX programs' folder (22.4): this Mac's setting, else `/Library/TeX/texbin`, else the newest
    `/usr/local/texlive/<year>/bin/universal-darwin`; resolved, and only one that holds `latexmk`."""
    chosen = settings.read_settings().get(TEX_SETTING)
    candidates = [chosen] if isinstance(chosen, str) and chosen.startswith("/") else []
    candidates += ["/Library/TeX/texbin", *sorted(glob.glob("/usr/local/texlive/*/bin/universal-darwin"), reverse=True)]
    for candidate in candidates:
        folder = Path(candidate).resolve()
        if os.access(folder / "latexmk", os.X_OK):
            return folder
    return None


def tex_tree(tex: Path) -> Path:
    """The TeX installation the programs' folder belongs to (`<tree>/bin/<platform>`), else that folder alone: the
    sandbox never opens a folder as wide as `/opt` or `/usr` for a TeX found elsewhere."""
    tree = tex.parent.parent
    return tree if tex.parent.name == "bin" and len(tree.parts) >= 3 and (tree / "texmf-dist").is_dir() else tex


def _closure(root: Path, main: str, text_of: Callable[[str], str]) -> list[str]:
    """The main file and every project `.tex` it includes, transitively."""
    seen, todo = [main], [main]
    while todo:
        current = todo.pop()
        for command, args in latex_edit.includes(text_of(current)):
            target = latex_edit.resolve_include(command, args, posixpath.dirname(current), posixpath.dirname(main))
            if target and target not in seen and (root / target).is_file():
                seen.append(target)
                todo.append(target)
    return seen


def _in_cloud(root: Path, main: str, sources: list[str], text_of: Callable[[str], str]) -> list[str]:
    """The files the run would read that are only in iCloud: the sources and the figures and bibliographies they
    name (each tried with the suffixes TeX would add)."""
    found = [rel for rel in sources if paths.is_dataless(root / rel)]
    main_dir = posixpath.dirname(main)
    for rel in sources:
        for command, names in _FILE_REFS_RE.findall(latex_edit.uncommented(text_of(rel))):
            for name in names.split(",") if command == "bibliography" else [names]:
                base = posixpath.normpath(posixpath.join(main_dir, name.strip()))
                suffixes = (".bib",) if command == "bibliography" else ("", ".pdf", ".png", ".jpg", ".eps")
                for suffix in ("",) + suffixes:
                    full = root / (base + suffix)
                    if not base.startswith("../") and full.is_file() and paths.is_dataless(full):
                        found.append(base + suffix)
    return sorted(set(found))


def preset_engine(sources: list[str], text_of: Callable[[str], str]) -> str:
    """XeLaTeX when the main file or its inputs load `ctex`, `xeCJK` or `fontspec` or use a `ctex` class, else
    pdfLaTeX. LuaLaTeX is never preset (22.4)."""
    return "xelatex" if any(_UNICODE_RE.search(latex_edit.uncommented(text_of(rel))) for rel in sources) else "pdflatex"


def build_dir(project_id: str, main_abs: Path) -> Path:
    """`<home>/latex-builds/<project id>/<16 hex of the main file's resolved absolute path>/`, mode 0700."""
    folder = paths.researchos_home() / "latex-builds" / project_id / _sha(str(main_abs.resolve()).encode())[:16]
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(folder, 0o700)
    return folder


def cache_dirs(build: Path) -> tuple[Path, Path]:
    """The run's TeX cache (`TEXMFVAR`) and temporary folder, `<build>-cache/`, of this main file alone (22.4 as
    amended): TeX searches `TEXMFVAR` before its installation and biber runs what it unpacks into the temporary
    folder, so a cache one project's run may write must never be read by another's."""
    found = []
    for name in ("texmf-var", "tmp"):
        folder = build.parent / f"{build.name}-cache" / name
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(folder.parent, 0o700)
        found.append(folder)
    return found[0], found[1]


def _quoted(path: Path | str) -> str:
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _developer_tools() -> list[str]:
    """The developer tools `/usr/bin/lipo` runs from (`xcode-select -p`) and the record that their licence was
    accepted: biber unpacks itself with lipo on its first run."""
    found = ["/Library/Developer/CommandLineTools", "/Library/Preferences/com.apple.dt.Xcode.plist"]  # its licence
    try:
        chosen = Path(subprocess.run(["/usr/bin/xcode-select", "-p"], capture_output=True, text=True, timeout=5,
                                     check=True).stdout.strip()).resolve()
    except (OSError, subprocess.SubprocessError):
        return found
    app = next((p for p in (chosen, *chosen.parents) if p.suffix == ".app"), None)
    return found + [str(app or chosen)] if chosen.is_absolute() and chosen != Path("/") else found


def _lipo_cache() -> list[str]:
    """lipo's own cache files in this user's temporary folder (`xcrun_db-*`), as a filter: only those, never the
    folder, which holds other programs' files."""
    try:
        temp = subprocess.run(["/usr/bin/getconf", "DARWIN_USER_TEMP_DIR"], capture_output=True, text=True, timeout=5,
                              check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return []
    temp = os.path.realpath(temp)
    if not re.fullmatch(r"/private/var/folders/[\w.+-]+/[\w.+-]+/T", temp):
        return []
    return ['(regex #"^' + re.sub(r"([.+])", r"\\\1", temp) + '/xcrun_db")']


def sandbox_profile(project: Path, tex_root: Path, writable: list[Path]) -> str:
    """The run's sandbox (22.4): read the project, the TeX installation, the font folders and the system (its
    developer tools included); write only `writable` (the build folder, the TeX cache, the run's temporary folder),
    `/dev` and lipo's cache; no network. Not the system's temporary folders: they hold other programs' files."""
    real = [str(Path(os.path.realpath(p))) for p in (project, tex_root, *writable)]
    reads = ["/System", "/usr/lib", "/usr/bin", "/usr/share", "/usr/libexec", "/bin", "/Library/Fonts",
             "/private/var/db/timezone", "/dev", str(Path.home() / "Library" / "Fonts"),
             str(Path(real[1]).parent / "texmf-local"), *_developer_tools(), *real]
    lipo = _lipo_cache()
    reading = " ".join([f"(subpath {_quoted(p)})" for p in reads] + lipo)
    writing = " ".join([f"(subpath {_quoted(p)})" for p in [*real[2:], "/dev"]] + lipo)
    # Whether a file exists, its size and time: only below what the run reads, and the folders on the way there
    # (with the system's own links: /etc, /var, /tmp and the local time zone).
    parents = {str(parent) for p in [*reads, *real[2:]] for parent in Path(p).parents}
    parents |= {"/etc", "/var", "/tmp", "/private", "/private/etc", "/private/etc/localtime"}
    looking = " ".join(f"(literal {_quoted(p)})" for p in sorted(parents))
    return ("(version 1)\n(deny default)\n"
            "(allow process-exec process-fork signal sysctl-read mach-lookup ipc-posix-shm iokit-open)\n"
            f"(allow file-read-metadata {looking})\n(allow file-read* (literal \"/\") {reading})\n"
            f"(allow file-write* {writing})\n")


def command(tex: Path, engine: str, build: Path, main_name: str, profile: str) -> list[str]:
    """The one run, an argument list (22.4)."""
    return [SANDBOX_EXEC, "-p", profile, str(tex / "latexmk"), "-norc", _ENGINE_FLAG[engine], "-interaction=nonstopmode",
            "-halt-on-error", "-file-line-error", "-no-shell-escape", "-recorder", "-synctex=0", f"-outdir={build}",
            f"-auxdir={build}", main_name]


def environment(tex: Path, build: Path, cache: Path, temp: Path) -> dict[str, str]:
    """An environment built from nothing (22.4); TeX's lines are not wrapped, so a `file:line` reads whole."""
    return {"PATH": f"{tex}:/usr/bin:/bin", "HOME": str(Path.home()), "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8",
            "shell_escape": "f", "openout_any": "p", "TEXMFOUTPUT": str(build), "TEXMFVAR": str(cache),
            "TMPDIR": f"{temp}/", "max_print_line": "10000", "error_line": "254", "half_error_line": "238"}


def _file_limit() -> None:
    """In the child before it starts: no file it writes grows past `PDF_LIMIT` (a log a loop fills stops there)."""
    resource.setrlimit(resource.RLIMIT_FSIZE, (PDF_LIMIT, PDF_LIMIT))


def run(cmd: list[str], cwd: Path, env: dict[str, str], timeout: float) -> tuple[int | None, str, str | None]:
    """`cmd` in a session of its own, stdin closed: its exit code, the last `LOG_TAIL` of its output (kept in a
    file without a name, never in memory), and `timeout` or `stopped` (by `stop`) when it was killed. The whole
    group is killed when it ends either way."""
    with tempfile.TemporaryFile() as output:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=output,
                                stderr=subprocess.STDOUT, start_new_session=True, preexec_fn=_file_limit)
        _running.update(proc=proc, stopped=False)
        try:
            proc.wait(timeout=timeout)
            killed = "stopped" if _running.get("stopped") else None
        except subprocess.TimeoutExpired:
            _kill(proc)
            proc.wait()
            killed = "timeout"
        finally:
            _kill(proc)
            _running.clear()
        output.seek(max(0, os.fstat(output.fileno()).st_size - LOG_TAIL))
        return proc.returncode, output.read(LOG_TAIL).decode("utf-8", errors="replace"), killed


def _kill(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def stop() -> bool:
    """「停止」: the running export's whole group killed. Whether one was running."""
    proc = _running.get("proc")
    if proc is None:
        return False
    _running["stopped"] = True
    _kill(proc)
    return True


# -- reading the log ----------------------------------------------------------------------------------------------------

_LINE_ERROR_RE = re.compile(r"^(?P<file>[^\s:<][^:\n]*?):(?P<line>\d+): (?P<text>[^\n]+)$", re.MULTILINE)
_NOT_FOUND_RE = re.compile(r"(?:File|file) [`'](?P<name>[^'\n]+)' not found|I can't find file [`'](?P<other>[^'\n]+)'"
                           r"|I couldn't open (?:database|style) file (?P<bib>[^\s]+)"
                           r"|Couldn't open (?P<denied>/[^\n:]+): Operation not permitted"
                           r"|I WON'T RUN '[^\n]*' because I don't find the following files:\n\s+(?P<unseen>/[^\n]+)"
                           r"|xdvipdfmx:warning: File \"(?P<special>[^\"\n]+)\" not found")
_MISSING_CHAR_RE = re.compile(r"Missing character: There is no (?P<char>.+?) (?:\(U\+[0-9A-F]+\) )?in font")


def _outside(root: Path, main_dir: Path, name: str) -> str | None:
    """`name` as TeX was asked to read it, when it is a file outside the project (directly or by a link)."""
    full = Path(os.path.normpath(main_dir / name))
    for candidate in (full, Path(str(full) + ".tex"), Path(str(full).removesuffix(".tex"))):
        if os.path.lexists(candidate) and not Path(os.path.realpath(candidate)).is_relative_to(root):
            return str(candidate)
    return None


def read_log(root: Path, main: str, log: str, lang: str, more: str = "") -> dict[str, Any]:
    """The run's TeX log in plain words (22.4): its first `file:line` errors, each linked into the editor only when
    its path passes 22.1's validator; and, from the log with `more` (latexmk's output, biber's log), missing packages,
    packages that need to run programs, files outside the project the sandbox refused, characters the fonts lack."""
    main_dir = (root / main).parent
    errors: list[dict[str, Any]] = []
    for m in _LINE_ERROR_RE.finditer(log or more):
        if any((e["file"], e["line"], e["text"]) == (m.group("file"), int(m.group("line")), m.group("text").strip())
               for e in errors):
            continue
        name = m.group("file").removeprefix("./")
        resolved = os.path.normpath(main_dir / name)
        rel = os.path.relpath(resolved, root) if Path(resolved).is_relative_to(root) else None
        try:
            link = latex_edit.checked_path(root, rel) if rel else None
        except WorkspaceError:
            link = None
        errors.append({"file": m.group("file"), "line": int(m.group("line")), "text": m.group("text").strip(),
                       "path": link})
        if len(errors) == 5:
            break
    log += "\n" + more
    notes = []
    for m in _NOT_FOUND_RE.finditer(log):
        name = next(found for found in m.groups() if found)
        if name.endswith((".sty", ".cls")):
            notes.append(_say(lang, "缺少宏包 {name}", "Missing package {name}", name=name.rsplit(".", 1)[0]))
        elif (outside := _outside(root, main_dir, name)) is not None:
            notes.append(_say(lang, "读取了项目以外的文件 {path}，导出时不允许",
                              "The export read {path}, a file outside the project, which is not allowed", path=outside))
    for package in _SHELL_PACKAGES:
        if re.search(rf"Package {package} (?:Error|Warning)[^\n]*(?:shell.escape|write18)", log, re.IGNORECASE):
            notes.append(_say(lang, "这个宏包需要运行外部程序，导出时不允许：{name}",
                              "This package needs to run programs, which an export does not allow: {name}",
                              name=package))
    if re.search(r"runsystem\(.*\)\.\.\.disabled", log):
        notes.append(_say(lang, "文稿要求运行外部程序，导出时没有运行。",
                          "The draft asked to run a program; the export did not run it."))
    chars = list(dict.fromkeys(m.group("char") for m in _MISSING_CHAR_RE.finditer(log)))
    missing = None
    if chars:
        missing = _say(lang, "有 {n} 个字符字体里没有，PDF 里缺了：{chars}",
                       "1 character is not in the font and is missing from the PDF: {chars}" if len(chars) == 1 else
                       "{n} characters are not in the font and are missing from the PDF: {chars}",
                       n=len(chars), chars=" ".join(chars))
    return {"errors": errors, "notes": list(dict.fromkeys(notes)), "missing_characters": chars,
            "missing_message": missing}


# -- the PDF ------------------------------------------------------------------------------------------------------------

def pdf_name(root: Path, main: str, folder: str) -> str:
    """`<folder>/<name>.pdf`, or `<folder>/<main's folders>-<name>.pdf` (its whole folder path, `/` as `-`) when two
    main files share a name."""
    stem = posixpath.splitext(posixpath.basename(main))[0]
    twins = [m for m in latex_edit.every_main(root) if m != main and posixpath.splitext(posixpath.basename(m))[0] == stem]
    parent = posixpath.dirname(main).replace("/", "-")
    name = f"{parent}-{stem}.pdf" if twins and parent else f"{stem}.pdf"
    return posixpath.join(folder, name) if folder else name


def _current(root: Path, rel: str) -> bytes | None:
    return workspace._read(root, rel, limit=PDF_LIMIT, text=False)  # noqa: SLF001


def _exported(root: Path, main: str, rel: str, data: bytes | None) -> bool:
    """Whether `data`, the file at `rel`, is absent or the last export of `main` there."""
    if data is None:
        return True
    last = [e for e in papers.exports(root) if e["main"] == main and e["pdf"] == rel]
    return bool(last) and last[-1]["pdf_sha256"] == _sha(data)


def target(root: Path, main: str, choice: Any) -> dict[str, Any]:
    """Where the PDF goes: `{pdf}`, or `{ask: rel, save_as}` when a PDF there is not the last export and the person
    has not chosen 替换 (`replace`) or 另存为 (`save_as`, `{name}-导出.pdf`, numbered when that is taken too)."""
    rel = pdf_name(root, main, outputs.folders(root)["folders"]["document"])
    if choice == "replace" or _exported(root, main, rel, _current(root, rel)):
        return {"pdf": rel}
    for n in range(1, 1000):
        save_as = f"{rel[:-4]}-导出{'' if n == 1 else f'-{n}'}.pdf"
        if _exported(root, main, save_as, _current(root, save_as)):
            break
    return {"pdf": save_as} if choice == "save_as" else {"ask": rel, "save_as": save_as}


def _ask(place: dict[str, Any], main: str, lang: str) -> dict[str, Any]:
    return {"exported": False, "ask": "replace", "file": place["ask"], "save_as": place["save_as"], "main": main,
            "message": _say(lang, "{file} 不是上次导出的文件，要替换它吗？", "{file} is not the last export. Replace it?",
                            file=place["ask"])}


def _place(root: Path, main: str, place: str, pdf: bytes, tex_sha: str, engine: str) -> str:
    """The PDF written at `place` through the human write path and its `[[export]]` appended: the id."""
    pid = workspace._require_write(root)  # noqa: SLF001
    _drafts, decision = papers.assess(root)
    if not decision.may_write:  # refused before the PDF is written, not after
        raise _refuse("record", papers.message_of(decision) or "论文正文记录没有写入", "papers.toml may not be written now")
    before = _current(root, place)
    workspace._save(root, place, before, pdf, pid, limit=PDF_LIMIT, text=False)  # noqa: SLF001
    try:
        return papers.record_export(root, {"main": main, "pdf": place, "pdf_sha256": _sha(pdf), "tex_sha256": tex_sha,
                                           "engine": engine}, via="app")
    except papers.PaperRefused as exc:
        raise _refuse("record", exc.message_zh or str(exc), str(exc)) from exc


# -- the export -----------------------------------------------------------------------------------------------------------

def _chosen(key: str, item: str, value: Any, allowed: list[str] | tuple[str, ...]) -> str | None:
    """A choice the person made (kept in this Mac's settings) or, without one, the one kept before."""
    kept = settings.read_settings().get(key)
    kept = kept if isinstance(kept, dict) else {}
    if value is not None:
        if value not in allowed:
            raise _refuse("invalid", "内容格式不对，请检查后重试", f"{value!r} is not a choice here", 400)
        settings.update_setting(key, {**kept, item: value})
        return value
    return kept.get(item) if kept.get(item) in allowed else None


def export(root: str | Path, body: Any, lang: str = "zh", *, guard: Callable[[], Any] | None = None,
           timeout: float = TIMEOUT_S) -> dict[str, Any]:
    """「导出 PDF」 (22.4) for `{path, main?, engine?, pdf?}`: an answer that exports, asks (`ask`: `main` with the
    main files to choose from, or `replace` with the PDF's name and the 另存为 name), or says why it did not
    succeed (`exported` false with `errors`, `notes`, `log`). `guard` takes the write guard for the placing."""
    root = workspace._root(root)  # noqa: SLF001
    if not isinstance(body, dict):
        raise _refuse("invalid", "内容格式不对，请检查后重试", "An export must be an object", 400)
    if situation._in_agent_run():  # noqa: SLF001 -- 17.5: no agent runs TeX
        raise _refuse("agent", "AI 助手不能导出 PDF。", "An agent's run cannot export a PDF", 403)
    rel = latex_edit.checked_path(root, body.get("path"))
    pid = workspace._require_write(root)  # noqa: SLF001
    mains = latex_edit.main_files(root, rel)
    choices = mains or latex_edit.every_main(root)
    main = _chosen(MAIN_SETTING, f"{pid}/{rel}", body.get("main"), choices) or (mains[0] if len(mains) == 1 else None)
    if main is None:
        words = None if mains else _say(lang, "找不到包含这个文件的主文件（带 \\documentclass 的 .tex）。",
                                        "No main file (a .tex with \\documentclass) includes this file.")
        return {"exported": False, "ask": "main", "mains": choices, "message": words}
    _known, text_of = latex_edit.tex_reader(root)
    sources = _closure(root, latex_edit.checked_path(root, main), text_of)
    engine = _chosen(ENGINE_SETTING, f"{pid}/{main}", body.get("engine"), ENGINES) or preset_engine(sources, text_of)
    tex = tex_bin()
    if tex is None:
        raise _refuse("no_tex", "这台 Mac 上没有找到 TeX（TeX Live 或 MacTeX）。",
                      "No TeX (TeX Live or MacTeX) was found on this Mac.")
    if not os.access(SANDBOX_EXEC, os.X_OK):
        raise _refuse("no_sandbox", "这台 Mac 上没法隔离导出，所以没有运行。",
                      "This Mac cannot confine the export, so it did not run.")
    cloud = _in_cloud(root, main, sources, text_of)
    if cloud:
        raise _refuse("cloud", f"有文件还在 iCloud 里没下载：{'、'.join(cloud)}",
                      f"Some files are only in iCloud and not downloaded: {', '.join(cloud)}")
    place = target(root, main, body.get("pdf"))
    if "ask" in place:
        return _ask(place, main, lang)
    if not _RUN_LOCK.acquire(blocking=False):
        raise _refuse("busy", "正在导出另一份文稿，请等它结束。", "Another export is running; wait for it to finish.")
    try:
        main_abs = root / main
        tex_sha = _sha(main_abs.read_bytes())
        build = build_dir(pid, main_abs)
        cache, temp = cache_dirs(build)
        produced, marker = build / f"{main_abs.stem}.pdf", build / "last-good-run"
        if _text(marker) != engine:  # after a failed run or with another engine, latexmk starts from nothing
            for leftover in build.iterdir():
                if leftover.is_dir() and not leftover.is_symlink():
                    shutil.rmtree(leftover)
                else:
                    leftover.unlink()
        produced.unlink(missing_ok=True)  # a PDF of an earlier run is never taken for this one's
        marker.unlink(missing_ok=True)
        profile = sandbox_profile(root, tex_tree(tex), [build, cache, temp])
        code, out, killed = run(command(tex, engine, build, main_abs.name, profile), main_abs.parent,
                                environment(tex, build, cache, temp), timeout)
        if out.startswith("sandbox-exec:"):  # it refused the profile: nothing ran
            raise _refuse("no_sandbox", "这台 Mac 上没法隔离导出，所以没有运行。",
                          "This Mac cannot confine the export, so it did not run.")
        log, more = (_text(build / f"{main_abs.stem}{suffix}") for suffix in (".log", ".blg"))
        pdf = _build_file(produced, PDF_LIMIT) if not killed and code == 0 else b""
        pdf = pdf if pdf.startswith(b"%PDF-") else b""
        if pdf:
            _mark(marker, engine)
    finally:
        _RUN_LOCK.release()
    answer = {"main": main, "engine": engine, **read_log(root, main, log, lang, out + "\n" + more),
              "log": (log or out)[-20000:]}
    if not pdf:
        why = {"timeout": ("导出超过 {s} 秒，已经停止。", "The export took longer than {s} seconds and was stopped."),
               "stopped": ("已停止导出。", "The export was stopped.")}.get(killed, ("导出没有成功", "The export did not succeed"))
        return {**answer, "exported": False, "stopped": killed, "message": _say(lang, *why, s=int(timeout))}
    with guard() if guard else contextlib.nullcontext(), situation.write_guard(root, human=True):
        place = target(root, main, body.get("pdf"))
        if "ask" in place:
            return {**answer, **_ask(place, main, lang)}
        export_id = _place(root, main, place["pdf"], pdf, tex_sha, engine)
    return {**answer, "exported": True, "pdf": place["pdf"], "export": export_id,
            "message": _say(lang, "已导出 {pdf}", "Exported {pdf}", pdf=place["pdf"])}


def _build_file(path: Path, limit: int, *, tail: bool = False) -> bytes:
    """A file the run left in its build folder, read by Research OS outside the sandbox: never through a link, only
    a regular file with one name and at most `limit` bytes (or, with `tail`, its last `limit`); else nothing."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return b""
    with os.fdopen(fd, "rb") as handle:
        found = os.fstat(fd)
        if not stat.S_ISREG(found.st_mode) or found.st_nlink != 1 or (found.st_size > limit and not tail):
            return b""
        handle.seek(max(0, found.st_size - limit))
        return handle.read(limit)


def _mark(marker: Path, engine: str) -> None:
    """`last-good-run` written afresh, never through a link or a file the run left there."""
    try:
        marker.unlink(missing_ok=True)
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except OSError:
        return  # without it the next export starts from nothing
    with os.fdopen(fd, "w") as handle:
        handle.write(engine)


def _text(path: Path) -> str:
    return _build_file(path, LOG_TAIL, tail=True).decode("utf-8", errors="replace")
