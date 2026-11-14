"""The LaTeX editor's reading and saving of a `.tex` draft (DESIGN.md 22.1, task V17).

One validator (`checked_path`) decides every path the editor reads or saves. A file is read as bytes and decoded as
UTF-8; its line ending is LF or CRLF, and one that is not UTF-8, holds a NUL, mixes endings, has a lone CR or is written
by a script or registered by a result record opens read-only, with one sentence why. Nothing is normalised.

A save sends `{path, revision, edits}`: `revision` the sha256 of the bytes the editor opened, `edits` the changed ranges
`{from, to, insert}`, in code points of the decoded text with a CRLF counted as two (Python's own string indices). The
edits are spliced into the decoded text, so every byte outside an edited range is unchanged, and written through
`workspace._save` (the human write path: the daily snapshot, a backup in a series of its own per path, a durable atomic
write, a history entry, the recheck). A save never writes the evidence ledger (22.2). Undoing a save writes the bytes
before it back, as a second history entry, only while the file is still what that save wrote and that save is the
path's last (its backup the newest of the series).
"""

from __future__ import annotations

import bisect
import hashlib
import itertools
import os
import posixpath
import re
import sqlite3
import stat
import unicodedata
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from researchos import paths, people, settings, workspace
from researchos.ingest import files as files_ingest
from researchos.records import files as record_files
from researchos.records import evidence, notes, papers, situation
from researchos.workspace import WorkspaceError

#: The largest `.tex` the editor opens or a save may leave (22.1), and the largest save request (refused unread).
SIZE_LIMIT = 2 * 1024 * 1024
BODY_LIMIT = 4 * 1024 * 1024
EDIT_LIMIT = 2000
_REVISION_RE = re.compile(r"[0-9a-f]{64}\Z")

#: Why a file opens read-only, one sentence each (22.1), Chinese and English.
READ_ONLY = {
    "generated": ("这个文件是脚本生成的，在这里改了下次运行会被覆盖。",
                  "A script writes this file: a change made here would be overwritten the next time it runs."),
    "encoding": ("这个文件不是 UTF-8 编码，只能查看。", "This file is not UTF-8 text, so it can only be viewed."),
    "nul": ("这个文件里有空字符（NUL），只能查看。", "This file contains a NUL character, so it can only be viewed."),
    "lone_cr": ("这个文件里有单独的回车符（CR），只能查看。",
                "This file has a carriage return without a line feed, so it can only be viewed."),
    "mixed": ("这个文件混用了两种换行方式，只能查看。", "This file mixes two kinds of line ending, so it can only be viewed."),
}


def _refuse(code: str, en: str, zh: str, status: int = 400) -> WorkspaceError:
    return WorkspaceError(code, en, status, zh)


def _path_refused(rel: Any) -> WorkspaceError:
    return _refuse("path", f"{rel!r} is not a .tex draft the editor can open",
                   "只能在编辑器里打开项目里的 .tex 文稿", 400)


def checked_path(root: Path, rel: Any) -> str:
    """`rel` when the editor may open it (22.1 "Which files"), else a refusal: a project-relative path that
    `papers.clean_draft` accepts as it is written, no component starting with `.`, no symbolic link anywhere on it,
    not only in the cloud (409 `cloud`), an existing regular file, suffix `.tex` in lower case, and every component
    spelt as its folder lists it: a Mac volume ignores case and Unicode normalisation, so `Tables/T2.tex` would open
    `tables/t2.tex` past the read-only rule and the per-path backup series."""
    try:
        if papers.clean_draft(rel) != rel:
            raise _path_refused(rel)
    except ValueError as exc:
        raise _path_refused(rel) from exc
    if any(part.startswith(".") or not part for part in rel.split("/")) or not rel.endswith(".tex"):
        raise _path_refused(rel)
    full = workspace._confined(root, rel)  # noqa: SLF001 -- symbolic links and the cloud, as every workspace path
    try:
        info = os.lstat(full)
    except FileNotFoundError as exc:
        raise _refuse("not_found", f"{rel} does not exist", "这个文件已不存在，请刷新后重试", 404) from exc
    except OSError as exc:
        raise _refuse("unreadable", f"Cannot read {rel}: {exc}", "文件无法读取，请先检查项目文件", 409) from exc
    if not stat.S_ISREG(info.st_mode):
        raise _path_refused(rel)
    folder = root
    for part in rel.split("/"):
        try:
            names = os.listdir(folder)
        except OSError as exc:
            raise _refuse("unreadable", f"Cannot read {rel}: {exc}", "文件无法读取，请先检查项目文件", 409) from exc
        if part not in names:
            raise _refuse("path", f"{rel!r} is not spelt as the file is named on disk",
                          "路径的大小写或字符写法和磁盘上的文件名不一致，请从项目文件里打开", 400)
        folder = folder / part
    return rel


def _generated_keys(root: Path) -> set[str]:
    """The files a script writes (a `writes`/`generates` link into it that is not rejected, the scan's or a person's)
    or a task's output record registers (19.5), folded as `files_ingest.fold` compares paths."""
    keys = {files_ingest.fold(r) for rels in files_ingest.registered(root).values() for r in rels}
    try:
        graph = paths.graph_db_path(root)
        if not graph.is_file() or paths.is_dataless(graph):
            return keys
        with closing(sqlite3.connect(graph.as_uri() + "?mode=ro", uri=True)) as conn:
            dsts = conn.execute("SELECT DISTINCT dst FROM edges WHERE type IN ('writes', 'generates') "
                                "AND status != 'rejected'").fetchall()
        # Compared folded, as the output records are: the index may spell the path otherwise than the disk.
        keys |= {files_ingest.fold(path) for prefix, _, path in (str(dst).partition(":") for (dst,) in dsts)
                 if prefix in notes.FILE_PREFIXES}
    except (OSError, sqlite3.Error, ValueError, paths.IdentityUnavailableError):
        pass
    return keys


def _generated(root: Path, rel: str) -> bool:
    """Whether a script writes `rel` or a task's output record registers it (`_generated_keys`)."""
    return files_ingest.fold(rel) in _generated_keys(root)


def _ending(text: str) -> tuple[str, str | None]:
    """The file's line ending (`lf` or `crlf`) and, when it is not one of them, why: `lone_cr` or `mixed`."""
    crlf = text.count("\r\n")
    if text.count("\r") != crlf:
        return ("crlf" if crlf else "lf"), "lone_cr"
    if crlf and text.count("\n") != crlf:
        return "crlf", "mixed"
    return ("crlf" if crlf else "lf"), None


def _read(root: Path, rel: str) -> bytes:
    try:
        raw = workspace._read(root, rel, limit=SIZE_LIMIT, text=False)  # noqa: SLF001
    except WorkspaceError as exc:
        if exc.code == "too_large":
            raise _refuse("too_large", str(exc), "这个文件超过 2 MB，编辑器打不开", 413) from exc
        raise
    if raw is None:
        raise _refuse("not_found", f"{rel} does not exist", "这个文件已不存在，请刷新后重试", 404)
    return raw


def _opened(root: Path, rel: str, raw: bytes) -> dict[str, Any]:
    # A reason of the text itself comes before "generated": a generated file is still linked to (22.2), which needs
    # its text read as the file it is, one line break per ending.
    reason = None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
        reason = "encoding"
    ending, odd = _ending(text)
    reason = reason or ("nul" if "\0" in text else None) or odd or ("generated" if _generated(root, rel) else None)
    return {"text": text, "line_ending": ending, "read_only": reason is not None, "read_only_reason": reason}


def _revision(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _payload(rel: str, raw: bytes, opened: dict[str, Any], lang: str) -> dict[str, Any]:
    reason = opened["read_only_reason"]
    message = None if reason is None else READ_ONLY[reason][1 if lang == "en" else 0]
    return {"path": rel, **opened, "read_only_message": message, "revision": _revision(raw)}


def read_tex(root: str | Path, rel: Any, lang: str = "zh", links: bool = True) -> dict[str, Any]:
    """The editor's view of a `.tex`: its text, revision, line ending, whether, and why, it is read-only, and the files
    its includes open (`include_links`; left out with `links=False`, the editor's revision check on focus, which
    would otherwise walk the project and read its `.tex` files each time). Writes nothing."""
    root = workspace._root(root)  # noqa: SLF001
    rel = checked_path(root, rel)
    raw = _read(root, rel)
    opened = _opened(root, rel, raw)
    payload = _payload(rel, raw, opened, lang)
    return {**payload, **include_links(root, rel, opened["text"])} if links else payload


# -- includes and the main file (22.1 "One file at a time", 22.4 "Which file") --------------------------------------

_INCLUDE_RE = re.compile(r"\\(input|include|subfile)\s*\{([^{}]*)\}|\\(input)\s+([^\s{}\\%]+)"
                         r"|\\(import|subimport)\*?\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
#: The most `.tex` files of a project main-file finding reads.
TEX_FILE_LIMIT = 2000


def uncommented(text: str) -> str:
    """`text` with each line cut at its first `%` that is not escaped (an odd run of backslashes before it)."""
    lines = []
    for line in text.splitlines():
        at = 0
        while (at := line.find("%", at)) >= 0:
            run = len(line[:at]) - len(line[:at].rstrip("\\"))
            if run % 2 == 0:
                line = line[:at]
                break
            at += 1
        lines.append(line)
    return "\n".join(lines)


def includes(text: str) -> list[tuple[str, tuple[str, ...]]]:
    """Each `\\input` (with or without braces), `\\include`, `\\subfile`, `\\import` and `\\subimport` of `text`
    outside comments: the command and its arguments as written."""
    found = []
    for m in _INCLUDE_RE.finditer(uncommented(text)):
        if m.group(1):
            found.append((m.group(1), (m.group(2),)))
        elif m.group(3):
            found.append(("input", (m.group(4),)))
        else:
            found.append((m.group(5), (m.group(6), m.group(7))))
    return found


def resolve_include(command: str, args: tuple[str, ...], own_dir: str, main_dir: str) -> str | None:
    """The project-relative path an include names, as TeX finds it: `\\input`, `\\include` and `\\import` from the
    main file's folder, `\\subfile` and `\\subimport` from the including file's own; `.tex` added to a name without
    it. None for a name no project path can be (empty, absolute, a backslash, a `..` out of the project)."""
    parts = [part.strip() for part in args]
    if not parts[-1] or any("\\" in part or part.startswith(("/", "~")) for part in parts):
        return None
    path = posixpath.normpath(posixpath.join(own_dir if command in ("subfile", "subimport") else main_dir, *parts))
    if path == ".." or path.startswith(("../", "/")):
        return None
    return path if path.endswith(".tex") else path + ".tex"


def _is_main(text: str) -> bool:
    code = uncommented(text)
    head = re.search(r"\\documentclass\b", code)
    return bool(head) and code.find("\\begin{document}", head.end()) >= 0


def tex_paths(root: Path, limit: int = TEX_FILE_LIMIT) -> list[str]:
    """The project's `.tex` files in the walk's order (dot folders pruned, symbolic links skipped), at most `limit`:
    the walk stops there, so a huge project cannot hold it up."""
    return list(itertools.islice((r for r, _ in files_ingest.iter_files(root) if r.endswith(".tex")), limit))


def tex_reader(root: Path, known: list[str] | None = None) -> tuple[list[str], Any]:
    """The project's `.tex` files (at most `TEX_FILE_LIMIT`; `known` when given) and a reader of their text, each read
    once: "" for one that is not a regular file, is only in the cloud or is over 2 MB. Reads files, runs nothing."""
    known = tex_paths(root) if known is None else known
    texts: dict[str, str] = {}

    def text_of(path: str) -> str:
        if path not in texts:
            texts[path] = ""
            full = root / path
            try:
                # 22.1's one validator: only a regular file, never followed through a link; opened without blocking,
                # so a FIFO or a device named `.tex` is skipped rather than waited on.
                if not stat.S_ISREG(os.lstat(full).st_mode) or paths.is_dataless(full):
                    return ""
                fd = os.open(full, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, "rb") as handle:
                    info = os.fstat(handle.fileno())
                    if stat.S_ISREG(info.st_mode) and info.st_size <= SIZE_LIMIT:
                        texts[path] = handle.read(SIZE_LIMIT + 1)[:SIZE_LIMIT].decode("utf-8", errors="replace")
            except OSError:
                pass
        return texts[path]

    return known, text_of


def every_main(root: str | Path) -> list[str]:
    """Every `.tex` of the project with `\\documentclass` before `\\begin{document}` (22.4 "None")."""
    known, text_of = tex_reader(workspace._root(root))  # noqa: SLF001
    return [path for path in known if _is_main(text_of(path))]


def main_files(root: str | Path, rel: str) -> list[str]:
    """The main files of the draft `rel` (22.4): `rel` itself when it has `\\documentclass` before
    `\\begin{document}`; else the project's `.tex` files that do and reach `rel` through includes, followed
    transitively, commented lines skipped. Reads files, runs nothing; a file only in the cloud is not read."""
    known, text_of = tex_reader(workspace._root(root))  # noqa: SLF001

    if _is_main(text_of(rel)):
        return [rel]
    tex, found = set(known), []
    for main in known:
        if main == rel or not _is_main(text_of(main)):
            continue
        seen, todo = {main}, [main]
        while todo:
            current = todo.pop()
            for command, args in includes(text_of(current)):
                target = resolve_include(command, args, posixpath.dirname(current), posixpath.dirname(main))
                if target in tex and target not in seen:
                    seen.add(target)
                    todo.append(target)
        if rel in seen:
            found.append(main)
    return found


def _closure(main: str, tex: set[str], text_of: Any) -> list[str]:
    """The `.tex` files `main` brings in through includes, followed transitively in document order, without it."""
    seen, order, todo = {main}, [], [main]
    while todo:
        current = todo.pop()
        if current != main:
            order.append(current)
        found = [resolve_include(c, a, posixpath.dirname(current), posixpath.dirname(main)) for c, a in includes(text_of(current))]
        fresh = [f for f in dict.fromkeys(found) if f in tex and f not in seen]
        seen.update(fresh)
        todo.extend(reversed(fresh))
    return order


def drafts(root: str | Path, lang: str = "zh") -> dict[str, Any]:
    """正文's list (22.10): each main file with the files it brings in, then the `.tex` files no main file includes;
    a `.tex` a script writes or a result record registers is left out and counted (`generated`). Each row: path, when
    it last changed (UTC, ISO 8601), `cloud` when it is only in the cloud (not readable, not openable), and its
    passages with a supporting file (`supported`) and of them 待复核 (`waiting`), a member's 待批准 not counted, as
    `ROLatex.paperCounts` counts them. At most `TEX_FILE_LIMIT` files are read (`truncated`). `new_folder` and
    `can_write` are for 新建文稿 (22.11). Writes nothing."""
    root = workspace._root(root)  # noqa: SLF001
    walked = tex_paths(root, TEX_FILE_LIMIT + 1)
    generated = _generated_keys(root)
    known = [p for p in walked[:TEX_FILE_LIMIT] if files_ingest.fold(p) not in generated]
    _known, text_of = tex_reader(root, known)
    links, decision = evidence.links_now(root)
    by_draft: dict[str, list[dict[str, Any]]] = {}
    for item in links.values():
        if not item["pending"]:
            by_draft.setdefault(item["draft"], []).append(item)

    def row(path: str) -> dict[str, Any]:
        full, cloud, modified = root / path, False, None
        try:
            cloud = paths.is_dataless(full)
            modified = datetime.fromtimestamp(os.lstat(full).st_mtime, timezone.utc).isoformat()
        except OSError:
            pass
        supported: set[Any] = set()
        waiting: set[Any] = set()
        if by_draft.get(path) and not cloud:
            norm = text_of(path).replace("\r\n", "\n")
            paragraphs = evidence.Paragraphs(norm)
            for item in by_draft[path]:
                found = evidence.resolve(item["anchor"], norm, paragraphs)
                key = (found["start"], found["end"]) if found["state"] == "found" else item["link"]
                supported.add(key)
                if found["state"] != "found":
                    waiting.add(key)
        return {"path": path, "modified": modified, "cloud": cloud, "supported": len(supported), "waiting": len(waiting)}

    tex = set(known)
    mains = [p for p in known if _is_main(text_of(p))]
    closures = {m: _closure(m, tex, text_of) for m in mains}
    # A main file another main file brings in (a subfiles chapter, `\documentclass[main.tex]{subfiles}`) is that
    # file's chapter, listed under it only; two that bring in each other both stay groups.
    within = {m for m in mains for n in mains if n != m and m in closures[n] and n not in closures[m]}
    groups = [{"main": row(m), "files": [row(p) for p in closures[m]]} for m in mains if m not in within]
    mains = [m for m in mains if m not in within]
    included = {f["path"] for g in groups for f in g["files"]}
    others = [row(p) for p in known if p not in included and p not in mains]
    return {"groups": groups, "others": others,
            "generated": sum(files_ingest.fold(p) in generated for p in walked[:TEX_FILE_LIMIT]),
            "truncated": len(walked) > TEX_FILE_LIMIT, "limit": TEX_FILE_LIMIT, "new_folder": new_folder(root),
            "can_write": _can_write(root),
            "message": None if decision.may_apply else _record_state(decision, lang)["message"]}


def _can_write(root: Path) -> bool:
    """Whether this person may write in the project here (`workspace._require_write`): 新建文稿 is offered only then."""
    try:
        workspace._require_write(root)  # noqa: SLF001
    except WorkspaceError:
        return False
    return True


def new_folder(root: Path) -> str:
    """Where 「新建文稿」 puts a draft unless the person says otherwise (22.11): the document folder of 19, else
    `paper` when it exists, else `manuscript`."""
    from researchos import outputs   # it imports workspace, which this module imports

    try:
        return outputs.folders(root)["folders"]["document"]
    except (WorkspaceError, OSError, ValueError):
        return "paper" if (root / "paper").is_dir() else "manuscript"


def include_links(root: Path, rel: str, text: str) -> dict[str, Any]:
    """The editor's include links (22.1): for each include of `text`, the file it opens when that path passes
    `checked_path`, else None (shown as text). `\\input`, `\\include` and `\\import` resolve from the main file's
    folder; with no main file, or main files in several folders, from the draft's own, and `own_folder` says so."""
    mains = main_files(root, rel)
    folders = {posixpath.dirname(main) for main in mains}
    own, single = posixpath.dirname(rel), len(folders) == 1
    main_dir = next(iter(folders)) if single else own
    links, seen = [], set()
    for command, args in includes(text):
        if (command, args) in seen:
            continue
        seen.add((command, args))
        target = resolve_include(command, args, own, main_dir)
        try:
            path = checked_path(root, target) if target else None
        except WorkspaceError:
            path = None
        links.append({"command": command, "args": list(args), "path": path,
                      "own_folder": not single and command not in ("subfile", "subimport")})
    return {"mains": mains, "links": links}


def backup_series(rel: str) -> str:
    """The folder of `.researchos/backups/` a draft's backups go in: one per path, so `paper/main.tex` and
    `slides/main.tex` never prune each other (22.1)."""
    return "latex/" + hashlib.sha256(rel.encode("utf-8")).hexdigest()[:16]


def _revision_of(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not _REVISION_RE.fullmatch(value):
        raise _refuse("invalid", f"'{key}' must be a SHA-256 hash", "内容格式不对，请检查后重试")
    return value


def _edits(value: Any) -> list[tuple[int, int, str]]:
    """The edits' shape, before the file is read: a list of at most `EDIT_LIMIT` `{from, to, insert}`, the offsets
    integers, the inserted text valid Unicode without NUL."""
    if not isinstance(value, list):
        raise _refuse("invalid", "'edits' must be a list", "内容格式不对，请检查后重试")
    if len(value) > EDIT_LIMIT:
        raise _refuse("too_many_edits", f"A save may carry at most {EDIT_LIMIT} edits",
                      f"一次保存最多 {EDIT_LIMIT} 处改动，请分几次保存", 413)
    edits = []
    for edit in value:
        if not isinstance(edit, dict) or set(edit) != {"from", "to", "insert"}:
            raise _refuse("invalid", "An edit is {from, to, insert}", "内容格式不对，请检查后重试")
        start, end, insert = edit["from"], edit["to"], edit["insert"]
        if (type(start) is not int or type(end) is not int or not isinstance(insert, str)):  # noqa: E721 -- not bool
            raise _refuse("invalid", "An edit's offsets are integers and its text a string", "内容格式不对，请检查后重试")
        try:
            insert.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise _refuse("invalid", "An edit holds a lone surrogate", "改动里有无效的字符") from exc
        if "\0" in insert:
            raise _refuse("invalid", "An edit holds a NUL character", "改动里有空字符（NUL）")
        edits.append((start, end, insert))
    return edits


def _splice(text: str, edits: list[tuple[int, int, str]], ending: str) -> str:
    """`text` with `edits` applied: each inside the text, sorted and not overlapping; the result keeps the file's
    line ending throughout (an edit that brings another, or splits a CRLF, is refused)."""
    pieces, at = [], 0
    for start, end, insert in edits:
        if not at <= start <= end <= len(text):
            raise _refuse("invalid", "The edits must be sorted, not overlapping and inside the text",
                          "改动的位置不对，请重新载入文件后再改")
        pieces += [text[at:start], insert]
        at = end
    pieces.append(text[at:])
    result = "".join(pieces)
    crlf = result.count("\r\n")
    if (result.count("\r") != (0 if ending == "lf" else crlf)
            or (ending == "crlf" and result.count("\n") != crlf)):
        raise _refuse("invalid", f"An edit brings a line ending other than the file's ({ending})",
                      "改动里的换行方式和文件不一致")
    return result


def save_tex(root: str | Path, body: Any) -> dict[str, Any]:
    """A person's save of a `.tex` (22.1): refused when the file is no longer `revision` (409 `conflict`), is
    read-only (409 `read_only_file`) or the edits do not hold; else written through the human write path. The answer
    carries the new `revision` and `previous`, the revision it replaced (what 「撤销这次保存」 sends back)."""
    root = workspace._root(root)  # noqa: SLF001
    if not isinstance(body, dict):
        raise _refuse("invalid", "A save must be an object", "内容格式不对，请检查后重试")
    revision = _revision_of(body, "revision")
    edits = _edits(body.get("edits"))
    with situation.write_guard(root, human=True):
        pid = workspace._require_write(root)  # noqa: SLF001
        rel = checked_path(root, body.get("path"))
        before = _read(root, rel)
        if _revision(before) != revision:
            raise _refuse("conflict", "The file has changed since it was opened", "这个文件在别处被改过。", 409)
        opened = _opened(root, rel, before)
        if opened["read_only"]:
            raise _refuse("read_only_file", READ_ONLY[opened["read_only_reason"]][1],
                          READ_ONLY[opened["read_only_reason"]][0], 409)
        after = _splice(opened["text"], edits, opened["line_ending"]).encode("utf-8")
        if len(after) > SIZE_LIMIT:
            raise _refuse("too_large", "The saved file would be larger than 2 MB", "保存后的文件会超过 2 MB", 413)
        backup, history_error = workspace._save(root, rel, before, after, pid,  # noqa: SLF001
                                                limit=SIZE_LIMIT, text=False, backup_subdir=backup_series(rel))
    changed = before != after
    return {"path": rel, "revision": _revision(after), "previous": _revision(before) if changed else None,
            "changed": changed, "backup": backup, "history_error": history_error}


def _saved_before(root: Path, rel: str, previous: str) -> bytes:
    """The bytes the last save replaced: the newest copy of the path's series, taken by the save that wrote the file
    as it still is. Only when its sha256 is `previous`; any older copy would roll back more than that one save."""
    series = record_files._backups_dir(root, backup_series(rel))  # noqa: SLF001
    copies = record_files._snapshots_of(series, Path(rel).name, ".tex", writes=True)  # noqa: SLF001
    data = None
    if copies:
        try:
            workspace._confined(root, copies[-1][1].relative_to(root).as_posix())  # noqa: SLF001
            data = copies[-1][1].read_bytes()
        except (OSError, ValueError, WorkspaceError):
            data = None
    if data is None:
        raise _refuse("gone", "The copy taken before this save is no longer kept",
                      "这次保存之前的版本已经不在备份里了，不能撤销。", 409)
    if _revision(data) != previous:
        raise _refuse("conflict", "This is not the file's last save, so it cannot be undone",
                      "这不是这个文件最近的一次保存，不能撤销。", 409)
    return data


def undo_save(root: str | Path, body: Any, lang: str = "zh") -> dict[str, Any]:
    """「撤销这次保存」 (22.1): `{path, revision, previous}`, the revision a save wrote and the one it replaced. Writes
    the replaced bytes back, as a second history entry, only while the file is still `revision` (else 409
    `conflict`); 409 `gone` when the backup is no longer kept. Answers with the file as the editor reopens it."""
    root = workspace._root(root)  # noqa: SLF001
    if not isinstance(body, dict):
        raise _refuse("invalid", "An undo must be an object", "内容格式不对，请检查后重试")
    revision, previous = _revision_of(body, "revision"), _revision_of(body, "previous")
    with situation.write_guard(root, human=True):
        pid = workspace._require_write(root)  # noqa: SLF001
        rel = checked_path(root, body.get("path"))
        current = _read(root, rel)
        if _revision(current) != revision:
            raise _refuse("conflict", "The file has changed since this save",
                          "这个文件在这次保存之后又被改过，不能撤销这次保存。", 409)
        opened = _opened(root, rel, current)
        if opened["read_only"]:
            raise _refuse("read_only_file", READ_ONLY[opened["read_only_reason"]][1],
                          READ_ONLY[opened["read_only_reason"]][0], 409)
        restored = _saved_before(root, rel, previous)
        backup, history_error = workspace._save(root, rel, current, restored, pid,  # noqa: SLF001
                                                limit=SIZE_LIMIT, text=False, backup_subdir=backup_series(rel))
    return {**_payload(rel, restored, _opened(root, rel, restored), lang), "backup": backup,
            "history_error": history_error}


# -- 新建文稿, a new draft (22.11) -------------------------------------------------------------------------------------

_PAPER = ("\\documentclass[12pt]{%s}\n%s\\usepackage{amsmath}\n\\usepackage{graphicx}\n\\usepackage{booktabs}\n\n"
          "\\title{<TITLE>}\n\\author{}\n\\date{\\today}\n\n\\begin{document}\n\\maketitle\n\n"
          "\\begin{abstract}\n%s\n\\end{abstract}\n\n\\section{%s}\n%s\n\n\\end{document}\n")
#: What each kind starts as (22.11): minimal and compilable, no bibliography; `<TITLE>` is the title, escaped.
TEMPLATES = {
    "zh_paper": _PAPER % ("ctexart", "", "在这里写摘要。", "引言", "在这里开始写正文。"),
    "en_paper": _PAPER % ("article", "\\usepackage[T1]{fontenc}\n", "Write the abstract here.", "Introduction",
                          "Start writing here."),
    "chapter": "\\section{<TITLE>}\n<BODY>\n",
}
_TEX_SPECIAL = {"\\": "\\textbackslash{}", "{": "\\{", "}": "\\}", "$": "\\$", "&": "\\&", "#": "\\#", "_": "\\_",
                "%": "\\%", "^": "\\^{}", "~": "\\~{}"}
# A component of a new draft's path holds no TeX special character (`\input{50%}` would end the paper's compile, `a}b`
# close the line early) and no character one cannot see: control, format (U+202E reverses what follows), line and
# paragraph separators, a lone surrogate. At most 180 bytes: the volume takes 255, and the temporary copy a save makes of
# its backup (`.<name>.<stamp>Z.tex.<pid>.<hex>.researchos-tmp`) adds some 70.
_TEX_UNSAFE = frozenset("%{}#~$^&")
_UNSEEN = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})
_PART_BYTES = 180


def _new_refused(zh: str, en: str) -> WorkspaceError:
    return _refuse("path", en, zh)


def _part_refusal(part: str) -> WorkspaceError | None:
    """Why `part` cannot be a folder or the file name of a new draft (22.11), or None."""
    if not part or part != part.strip():
        return _new_refused("文件夹或文件名不能是空的，也不能以空格开头或结尾",
                            "A folder or file name cannot be empty or start or end with a space")
    if part.startswith((".", "-")):
        return _new_refused("文件夹或文件名不能以 . 或 - 开头", "A folder or file name cannot start with . or -")
    if "\\" in part or "/" in part or any(unicodedata.category(c) in _UNSEEN for c in part):
        return _new_refused("文件夹或文件名不能含斜杠、反斜杠或看不见的字符（控制字符、换行符、文字方向符）",
                            "A folder or file name cannot contain a slash, a backslash or an invisible character "
                            "(a control character, a line separator, a direction mark)")
    if _TEX_UNSAFE.intersection(part):
        return _new_refused("文件夹或文件名不能含 % { } # ~ $ ^ &：LaTeX 在 \\input{…} 里会把它们当成命令",
                            "A folder or file name cannot contain % { } # ~ $ ^ &: LaTeX reads them as commands in "
                            "\\input{…}")
    if len(part.encode("utf-8")) > _PART_BYTES:   # it encodes: a lone surrogate is refused above
        return _new_refused(f"文件夹或文件名太长（最多 {_PART_BYTES} 字节，约 60 个汉字）",
                            f"A folder or file name is at most {_PART_BYTES} bytes")
    return None


def _new_path(root: Path, folder: Any, name: Any) -> tuple[str, str | None]:
    """The new draft's path and the folder it creates, if any (22.11): 22.1's rules but existence -- inside the project,
    no component starting with `.` or `-`, none with a TeX special character, a slash or backslash or a character one
    cannot see, none over 180 bytes, no symbolic link, not only in the cloud, lower-case `.tex` (added when the name has
    none) --, an existing folder typed in another case or Unicode form taken as it is spelt on disk, at most one new
    folder level, and never a file that exists (409 `exists`). Nothing is written."""
    if not isinstance(folder, str) or not isinstance(name, str):
        raise _refuse("invalid", "A new draft needs a folder and a name", "请填写文件夹和文件名")
    name, folder = name.strip(), folder.strip().rstrip("/")   # a leading "/" is an empty component, refused below
    if not name.endswith(".tex"):
        if name.lower().endswith(".tex"):
            raise _new_refused("文件名要以小写的 .tex 结尾", "The file name must end in a lower-case .tex")
        name += ".tex"
    parts = (folder.split("/") if folder else []) + [name]
    for part in parts:
        if (why := _part_refusal(part)) is not None:
            raise why
    current, created = root, None
    try:
        for at, part in enumerate(parts[:-1]):
            on_disk = os.listdir(current)
            if part not in on_disk:
                same = [n for n in on_disk if files_ingest.fold(n) == files_ingest.fold(part)]
                if len(same) > 1:
                    raise _new_refused(f"有几个文件夹只差大小写或字符写法：{'、'.join(sorted(same))}，请照磁盘上的写法填写",
                                       f"Several folders differ only in case or character form: {', '.join(sorted(same))}; "
                                       "type the one meant as it is spelt on disk")
                if same:
                    part = parts[at] = same[0]   # the folder on disk, spelt as it is there
                elif os.path.lexists(current / part):
                    raise _new_refused("文件夹的大小写或字符写法和磁盘上的不一致",
                                       "The folder is spelt otherwise than on disk")
            if part not in on_disk:
                if at != len(parts) - 2:
                    raise _refuse("too_deep", "At most one new folder level", "最多只能新建一层文件夹")
                created = "/".join(parts[:at + 1])
            elif os.path.islink(current / part):
                raise _new_refused("文稿只能建在项目文件夹里，路径上不能有替身（符号链接）",
                                   "A draft is made only inside the project, with no symbolic link on its path")
            elif not stat.S_ISDIR(os.lstat(current / part).st_mode):
                raise _new_refused(f"{part} 不是文件夹", f"{part} is not a folder")
            current = current / part
    except OSError as exc:
        raise _refuse("unreadable", f"Cannot read the folders of {'/'.join(parts)}: {exc}",
                      "文件夹无法读取，请先检查项目文件", 409) from exc
    rel = "/".join(parts)
    try:
        full = workspace._confined(root, rel)  # noqa: SLF001 -- symbolic links, the cloud, inside the project
        notes.normalize_node(rel)               # the history names it so: refused here, before anything is written
        exists = os.path.lexists(full)
    except WorkspaceError as exc:
        if exc.code == "cloud":
            raise
        raise _new_refused("文稿只能建在项目文件夹里，路径上不能有替身（符号链接）",
                           "A draft is made only inside the project, with no symbolic link on its path") from exc
    except (ValueError, OSError) as exc:
        raise _new_refused("这个路径不能用作文稿", f"This path cannot be a draft: {exc}") from exc
    if exists:
        raise _refuse("exists", f"{rel} already exists", f"已经有这个文件了：{rel}", 409)
    return rel, created


_END_DOCUMENT_RE = re.compile(re.escape("\\end{document}"))


def _end_documents(text: str) -> tuple[bool, list[int]]:
    """Whether `text` has a `\\begin{document}` outside a comment, and where each `\\end{document}` outside a comment
    after the first starts. More than one means one is in `\\verb`, verbatim, `\\iffalse` or a definition, which this
    reading cannot tell from the one TeX ends on."""
    at, begun, ends = 0, False, []
    for line in text.splitlines(keepends=True):
        code = uncommented(line)
        start = 0
        if not begun and (hit := code.find("\\begin{document}")) >= 0:
            begun, start = True, hit
        if begun:
            ends.extend(at + m.start() for m in _END_DOCUMENT_RE.finditer(code, start))
        at += len(line)
    return begun, ends


_BACK_MATTER_RE = re.compile(r"\s*\\(appendix\b|printbibliography\b|bibliography\s*\{|bibliographystyle\s*\{"
                             r"|begin\s*\{thebibliography\})")
_BODY_RE = re.compile(r"\s*\\(input|include|subfile|part|chapter|section)\b")


def _back_matter(text: str, end: int) -> int | None:
    """Where the back matter (`\\appendix`, or a bibliography with no section or `\\input` after it) starts, when it
    begins its own line, outside a comment, after `\\begin{document}` and before `end`: a new chapter goes before it,
    not after the references."""
    at, begun, found, appendix = 0, False, None, False
    for line in text.splitlines(keepends=True):
        if at >= end:
            break
        code = uncommented(line)
        if begun and found is None and (m := _BACK_MATTER_RE.match(code)):
            found, appendix = at, m.group(1) == "appendix"
        elif begun and found is not None and not appendix and _BODY_RE.match(code):
            found = None    # a \bibliographystyle at the top of the body, say: the paper goes on after it
        begun = begun or "\\begin{document}" in code
        at += len(line)
    return found


def _add_input(root: Path, main: str, chapter: str, lang: str) -> dict[str, Any]:
    """`\\input{<chapter from the main's folder, no .tex>}` on a line of its own just before `main`'s back matter
    (`_back_matter`) or else its one `\\end{document}` after its `\\begin{document}` (outside comments), written as a save is (`save_tex`); when it
    cannot be, the sentence why."""
    line = None
    try:
        main = checked_path(root, main)
        line = "\\input{" + posixpath.relpath(chapter[:-4], posixpath.dirname(main) or ".") + "}"
        raw = _read(root, main)
        opened = _opened(root, main, raw)
        if opened["read_only"]:
            raise _refuse("read_only_file", *reversed(READ_ONLY[opened["read_only_reason"]]))
        text = opened["text"]
        begun, ends = _end_documents(text)
        if not begun:
            raise _refuse("no_begin", "it has no \\begin{document}", "它里面没有 \\begin{document}")
        if not ends:
            raise _refuse("no_end", "it has no \\end{document} after \\begin{document}",
                          "它的 \\begin{document} 之后没有 \\end{document}")
        if len(ends) > 1:
            raise _refuse("several_ends", "it has more than one \\end{document} (in \\verb, verbatim or \\iffalse, "
                          "say), so where the line goes is not certain; add it by hand",
                          "它里面有不止一处 \\end{document}（比如写在 \\verb、verbatim 或 \\iffalse 里），"
                          "不能确定加在哪里，请自己加这一行")
        back = _back_matter(text, ends[0])
        at = ends[0] if back is None else back
        sep = "\r\n" if opened["line_ending"] == "crlf" else "\n"
        start = text.rfind("\n", 0, at) + 1
        own_line = not text[start:at].strip()
        save_tex(root, {"path": main, "revision": _revision(raw), "edits": [
            {"from": start if own_line else at, "to": start if own_line else at,
             "insert": line + sep if own_line else sep + line + sep}]})
    except WorkspaceError as exc:
        why = exc.message_zh if lang != "en" else str(exc)
        message = (settings.say("没有在 {main} 里加入 {line}：{why}", "{line} was not added to {main}: {why}",
                                lang, main=main, line=line, why=why) if line is not None else
                   settings.say("这个章节没有加入主文件 {main}：{why}", "The chapter was not added to {main}: {why}",
                                lang, main=main, why=why))
        return {"main": main, "line": line, "added": False, "message": message}
    return {"main": main, "line": line, "added": True, "message": None}


def new_tex(root: str | Path, body: Any, lang: str = "zh") -> dict[str, Any]:
    """「新建文稿」 (22.11): `{kind, folder, name, title?, main?}`, kind one of `TEMPLATES`. The file is written from its
    template through the human write path (`write_guard(human=True)`, exclusive creation, a history entry), UTF-8 with
    LF; a chapter with `main` is then brought into it by `_add_input`, whose outcome is `input`."""
    root = workspace._root(root)  # noqa: SLF001
    if not isinstance(body, dict) or body.get("kind") not in TEMPLATES:
        raise _refuse("invalid", "A new draft is a Chinese paper, an English paper or a chapter", "请选择文稿的类型")
    title, main = body.get("title") or "", body.get("main") or None
    if (not isinstance(title, str) or len(title) > 200
            or any(unicodedata.category(c) in ("Cc", "Cs", "Zl", "Zp") for c in title)):   # one line, and it encodes
        raise _refuse("invalid", "A title is at most 200 characters on one line", "标题最多 200 个字符，只能写一行")
    if main is not None and (body["kind"] != "chapter" or not isinstance(main, str)):
        raise _refuse("invalid", "Only a chapter is brought into a main file", "只有章节文件能加入主文件")
    with situation.write_guard(root, human=True):
        pid = workspace._require_write(root)  # noqa: SLF001
        rel, created = _new_path(root, body.get("folder"), body.get("name"))
        text = TEMPLATES[body["kind"]]
        if body["kind"] == "chapter":
            title = title.strip() or posixpath.basename(rel)[:-4]
            # a chapter of a pdfLaTeX paper gets the English line: pdfLaTeX cannot set Chinese
            from researchos import latex_export   # it imports this module

            try:
                main_text = _read(root, checked_path(root, main)).decode("utf-8", "replace") if main else ""
            except WorkspaceError:
                main_text = ""
            pdflatex = main is not None and not latex_export._UNICODE_RE.search(uncommented(main_text))  # noqa: SLF001
            text = text.replace("<BODY>", "在这里写这一节。" if lang != "en" and not pdflatex else "Write this section here.")
        else:
            pdflatex = body["kind"] == "en_paper"
        title = title.strip()
        if pdflatex and any(ord(c) > 0xFF for c in title):
            # pdfLaTeX (T1) cannot set it: a neutral heading, the title kept in a comment above it, which TeX skips
            kept = settings.say("% 标题：{title}（pdfLaTeX 排不出其中的字符，请改写成英文）",
                                "% Title: {title} (pdfLaTeX cannot set its characters; write it in English)",
                                lang, title=title) + "\n"
            title = "Section" if body["kind"] == "chapter" else ""
            text = kept + text if body["kind"] == "chapter" else text.replace("\\title{", kept + "\\title{", 1)
        data = text.replace("<TITLE>", "".join(_TEX_SPECIAL.get(c, c) for c in title)).encode("utf-8")
        _backup, history_error = workspace._save(root, rel, None, data, pid,  # noqa: SLF001
                                                 limit=SIZE_LIMIT, text=False, backup_subdir=backup_series(rel))
    return {"path": rel, "revision": _revision(data), "folder_created": created, "history_error": history_error,
            "input": _add_input(root, main, rel, lang) if main is not None else None}


# -- passages and their supporting files (22.2) ---------------------------------------------------------------------
# The editor's positions count a CRLF as two; the ledger's anchors and `evidence.resolve` use the text with every line
# ending written as \n. Converted here, both ways, so neither side ever guesses.

_HEADING_RE = re.compile(r"\\(?:chapter|section|subsection|subsubsection)\*?\s*(?:\[[^\]]*\])?\s*\{([^{}]*)\}")


def _normalised(text: str) -> tuple[str, list[int], list[int]]:
    """The `\\n`-normalised text, where each CRLF starts in the file's text, and where its \\n is in the normalised."""
    crlfs = [m.start() for m in re.finditer("\r\n", text)]
    return text.replace("\r\n", "\n"), crlfs, [i - k for k, i in enumerate(crlfs)]


def _to_file(crlfs_n: list[int], k: int | None) -> int | None:
    return None if k is None else k + bisect.bisect_left(crlfs_n, k)


def _anchor_of(text: str, start: Any, end: Any) -> dict[str, Any]:
    """The anchor of the passage `[start, end)` (the editor's positions) in `text`: its quote, prefix and suffix
    `\\n`-normalised and its offset in that text. Refused when the range is not a passage of words."""
    norm, crlfs, _n = _normalised(text)
    if any(isinstance(v, bool) or not isinstance(v, int) for v in (start, end)) or not 0 <= start < end <= len(text):
        raise _refuse("invalid", "A passage is a range inside the text", "请先选中正文里的一段文字")
    at = []
    for pos in (start, end):
        k = bisect.bisect_left(crlfs, pos)
        if k and crlfs[k - 1] == pos - 1:
            raise _refuse("invalid", "A passage cannot start or end inside a line ending", "请先选中正文里的一段文字")
        at.append(pos - k)
    quote = norm[at[0]:at[1]]
    if not quote.strip() or len(quote) > evidence.MAX_QUOTE:
        raise _refuse("invalid", f"A passage is 1 to {evidence.MAX_QUOTE} characters of text",
                      f"一段正文最多 {evidence.MAX_QUOTE} 个字符，不能是空白")
    # The context stops at a page break or another line separator the ledger cannot store (text pasted from a PDF);
    # one inside the passage itself is refused by `evidence`, with a sentence saying so.
    prefix = re.split(f"[{evidence.LINE_SEPARATORS}]", norm[max(0, at[0] - evidence.CONTEXT):at[0]])[-1]
    suffix = re.split(f"[{evidence.LINE_SEPARATORS}]", norm[at[1]:at[1] + evidence.CONTEXT])[0]
    return {"quote": quote, "prefix": prefix, "suffix": suffix, "offset": at[0]}


def _evidence_call(fn: Any) -> dict[str, Any]:
    try:
        return fn()
    except evidence.EvidenceRefused as exc:
        raise _refuse(exc.code, exc.words[1], exc.words[0], exc.status) from exc


def evidence_act(root: str | Path, action: str, body: Any, *, timeout: float | None = None) -> dict[str, Any]:
    """关联依据文件 (`link`), 「是这段」/「仍然成立」 (`reanchor`), 撤回关联 (`withdraw`) or 撤销 (`undo`): one entry in
    `.researchos/evidence.toml`. A passage is named by the editor's range in the saved file `{path, revision, from, to}`,
    and its anchor is taken from the file as it is on disk (409 `conflict` when it is no longer that revision). The
    `.tex` is only read."""
    root = workspace._root(root)  # noqa: SLF001
    if not isinstance(body, dict) or action not in ("link", "reanchor", "withdraw", "undo"):
        raise _refuse("invalid", "Not a link request", "无法识别这次关联")
    link = body.get("link")
    if action in ("link", "reanchor"):
        rel = checked_path(root, body.get("path"))
        raw = _read(root, rel)
        if _revision(raw) != _revision_of(body, "revision"):
            raise _refuse("conflict", "The file has changed since it was opened", "这个文件在别处被改过。", 409)
        opened = _opened(root, rel, raw)
        if opened["read_only_reason"] not in (None, "generated"):
            raise _refuse("read_only_file", READ_ONLY[opened["read_only_reason"]][1],
                          READ_ONLY[opened["read_only_reason"]][0], 409)
        anchor = _anchor_of(opened["text"], body.get("from"), body.get("to"))
        if action == "link":
            return _evidence_call(lambda: evidence.add_link(root, rel, body.get("file"), anchor, body.get("note"),
                                                            via="app", timeout=timeout))
        return _evidence_call(lambda: evidence.reanchor(root, link, rel, anchor, via="app", timeout=timeout))
    if action == "withdraw":
        return _evidence_call(lambda: evidence.withdraw(root, link, via="app", timeout=timeout))
    return _evidence_call(lambda: evidence.undo(root, link, body.get("undoes"), via="app", timeout=timeout))


def _record_state(decision: Any, lang: str) -> dict[str, Any]:
    words = evidence.message_of(decision)
    return {"trusted": decision.may_apply, "message": None if words is None else settings.say(*words, lang)}


def _missing(root: Path, node: str) -> bool:
    rel = notes.node_path(node)
    return rel is not None and not evidence._file_of_project(root, node)  # noqa: SLF001


def evidence_of_draft(root: str | Path, rel: Any, lang: str = "zh") -> dict[str, Any]:
    """The draft's standing links, each found again in its current text (22.2), in the editor's positions: `state`
    `found` / `changed` (待复核 「依据已变化」, with `candidates`) / `deleted` (「段落已被删掉」); `file_missing` when the
    supporting file has gone from the project (it is not withdrawn); `pending` (待批准) for a member's link the PI has
    not approved yet (22.5). Writes nothing."""
    root = workspace._root(root)  # noqa: SLF001
    rel = checked_path(root, rel)
    raw = _read(root, rel)
    norm, _crlfs, crlfs_n = _normalised(_opened(root, rel, raw)["text"])
    links, decision = evidence.links_now(root)
    team = people.read_team(root)
    team = team if team.state == "present" else None
    paragraphs, out = evidence.Paragraphs(norm), []
    for item in links.values():
        if item["draft"] != rel:
            continue
        found = evidence.resolve(item["anchor"], norm, paragraphs)
        out.append({
            "link": item["link"], "file": item["file"], "file_missing": _missing(root, item["file"]),
            "note": item["note"], "quote": item["anchor"]["quote"], "state": found["state"],
            "from": _to_file(crlfs_n, found["start"]), "to": _to_file(crlfs_n, found["end"]),
            "candidates": [{"from": _to_file(crlfs_n, c["start"]), "to": _to_file(crlfs_n, c["end"]), "text": c["text"]}
                           for c in found["candidates"]],
            "conflict": item["conflict"], "last": item["last"], "at": item["entry"].at, "pending": item["pending"],
            **people.attribution(item["entry"].data, team, lang),
        })
    return {"path": rel, "revision": _revision(raw), "links": out, **_record_state(decision, lang)}


def passages_supported(root: str | Path, file: Any, lang: str = "zh") -> dict[str, Any]:
    """以它为依据的段落: every standing link whose supporting file is `file` (a path or a node id), with its draft, the
    section the passage is in, its first 80 characters and its state (`draft_missing` when the draft cannot be read
    now). A file is matched by its path as a Mac volume compares it, whatever prefix a link names it with. Writes
    nothing."""
    root = workspace._root(root)  # noqa: SLF001
    try:
        node = notes.normalize_node(file)
        node = notes.normalize_node(notes.node_path(node) or node)
    except ValueError as exc:
        raise _refuse("invalid", str(exc), "无法识别这个文件") from exc

    def key(name: Any) -> Any:
        rel = notes.node_path(name) if isinstance(name, str) else None
        return name if rel is None else ("file", files_ingest.fold(rel))

    links, decision = evidence.links_now(root)
    texts: dict[str, tuple[str, evidence.Paragraphs] | None] = {}
    out = []
    for item in links.values():
        if key(item["file"]) != key(node):
            continue
        draft = item["draft"]
        if draft not in texts:
            try:
                norm = _normalised(_opened(root, draft, _read(root, checked_path(root, draft)))["text"])[0]
                texts[draft] = (norm, evidence.Paragraphs(norm))
            except WorkspaceError:
                texts[draft] = None
        got, anchor = texts[draft], item["anchor"]
        found = evidence.resolve(anchor, *got) if got is not None else {"state": "draft_missing", "start": None}
        where = found["start"] if found["start"] is not None else anchor["offset"]
        headings = list(_HEADING_RE.finditer(got[0][:where])) if got is not None else []
        shown = got[0][found["start"]:found["end"]] if found["state"] == "found" else anchor["quote"]
        out.append({"link": item["link"], "draft": draft, "state": found["state"], "excerpt": " ".join(shown.split())[:80],
                    "section": headings[-1].group(1).strip() if headings else None, "conflict": item["conflict"],
                    "pending": item["pending"]})
    out.sort(key=lambda p: (p["draft"], p["section"] or ""))
    return {"file": node, "passages": out, **_record_state(decision, lang)}


def declared_by_claim(root: str | Path, rel: str) -> dict[str, list[dict[str, Any]]]:
    """人工声明的依据文件 of each claim of the draft `rel` (22.3): claim id to `{file, missing}` for the supporting file of
    every found passage its sentence lies in (`missing` when the file has gone from the project), by position -- the
    claims reader's span tested against the passage's found range, both in the editor's positions, on the one text read now. Nothing when the draft cannot be read as a draft or the
    ledger cannot be trusted. It is shown beside a claim's state and never changes it. Writes nothing."""
    from researchos.ingest import claims as claims_ingest  # noqa: PLC0415 -- the scan's reader, only when asked

    try:
        root = workspace._root(root)  # noqa: SLF001
        rel = checked_path(root, rel)
        text = _opened(root, rel, _read(root, rel))["text"]
    except WorkspaceError:
        return {}
    # 22.5: a link 待批准 backs nothing
    mine = [item for item in evidence.links_now(root)[0].values() if item["draft"] == rel and not item["pending"]]
    if not mine:
        return {}
    norm, _crlfs, crlfs_n = _normalised(text)
    paragraphs, ranges = evidence.Paragraphs(norm), []
    for item in mine:
        found = evidence.resolve(item["anchor"], norm, paragraphs)
        if found["state"] == "found":
            ranges.append((_to_file(crlfs_n, found["start"]), _to_file(crlfs_n, found["end"]), item["file"]))
    try:
        claims = claims_ingest.parse_tex_claims(root, rel, text) if ranges else []
    except OSError:   # its sections are read from the file again; gone or in the cloud since
        return {}
    got: dict[str, set[str]] = {}
    for claim in claims:   # one sentence printed twice in a section is one id: the files of both are kept
        got.setdefault(claim.id, set()).update(f for start, end, f in ranges
                                               if claim.span is not None and start <= claim.span[0] and claim.span[1] <= end)
    return {cid: [{"file": notes.node_path(f) or f, "missing": _missing(root, f)}
                  for f in sorted(files, key=lambda f: notes.node_path(f) or f)]
            for cid, files in got.items() if files}


#: The order the link panel lists a project's files in (22.2): figures and tables, then data, scripts, documents.
_FILE_GROUPS = ("figure", "data", "script", "document")
_TABLE_RE = re.compile(r"(?:^|/)(?:tables?|tabs?)/|(?:^|/)tab(?:le)?s?[_\-.\d]", re.IGNORECASE)
_FILE_WALK_LIMIT = 20000


def _own_paper(root: Path, draft: str) -> set[str]:
    """What cannot support a passage of `draft` (folded): the draft itself, the other drafts of its paper (each `.tex`
    its main files include, transitively, but a table or a file a script writes) and the PDFs made from those main
    files -- each export recorded in papers.toml and the `<main>.pdf` beside it."""
    from researchos import latex_export   # it imports this module

    mains = main_files(root, draft)
    own = {draft}
    _known, text_of = tex_reader(root)
    for main in mains:
        own.update(rel for rel in latex_export._closure(root, main, text_of)  # noqa: SLF001
                   if rel == main or not (_TABLE_RE.search(rel) or _generated(root, rel)))
        own.add(posixpath.splitext(main)[0] + ".pdf")
    own.update(e["pdf"] for e in papers.exports(root) if e["main"] in mains and isinstance(e["pdf"], str))
    return {files_ingest.fold(rel) for rel in own}


def supporting_files(root: str | Path, query: Any, limit: int = 50, draft: Any = None) -> dict[str, Any]:
    """The project's files the link panel offers as a passage's supporting file (22.2): every file the scan's walk
    lists (no hidden folder, no symlink), its path containing `query` as a Mac volume compares names, figures and
    tables first, then data, scripts and documents; with `draft`, the draft being edited, never that draft, the other
    drafts of its paper or the PDF exported from it (`_own_paper`); a `draft` the editor may no longer open (deleted
    or renamed on disk while open, not a `.tex`) or whose paper cannot be read leaves out only that path, so the list
    never fails for it. `group` is one of `_FILE_GROUPS`. Reads directory listings, and with `draft` the paper's
    `.tex` files and papers.toml."""
    root = workspace._root(root)  # noqa: SLF001
    wanted = files_ingest.fold(query.strip()) if isinstance(query, str) else ""
    own: set[str] = set()
    if draft:
        try:
            own = _own_paper(root, checked_path(root, draft))
        except (WorkspaceError, OSError, ValueError):
            own = {files_ingest.fold(draft)} if isinstance(draft, str) else set()
    found: list[tuple[int, str, str]] = []
    walked = 0
    for rel, _category in files_ingest.iter_files(root):
        walked += 1
        if walked > _FILE_WALK_LIMIT:
            break
        if (wanted and wanted not in files_ingest.fold(rel)) or (own and files_ingest.fold(rel) in own):
            continue
        kind = notes.normalize_node(rel).split(":", 1)[0]
        table = kind != "script" and _TABLE_RE.search(rel)   # tab.py is a script, tables/t.tex a table
        group = ("figure" if kind == "figure" or table else "data" if kind == "dataset"
                 else "script" if kind == "script" else "document")
        found.append((_FILE_GROUPS.index(group), rel, group))
    found.sort()
    return {"files": [{"path": rel, "group": group} for _rank, rel, group in found[:limit]],
            "more": len(found) > limit}
