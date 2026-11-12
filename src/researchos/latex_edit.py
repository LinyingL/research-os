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

import hashlib
import os
import re
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from typing import Any

from researchos import paths, workspace
from researchos.ingest import files as files_ingest
from researchos.records import files as record_files
from researchos.records import notes, papers, situation
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


def _generated(root: Path, rel: str) -> bool:
    """Whether a script writes `rel` (a `writes`/`generates` link into it that is not rejected, the scan's or a
    person's) or a task's output record registers it (19.5)."""
    key = files_ingest.fold(rel)
    if any(key in {files_ingest.fold(r) for r in rels} for rels in files_ingest.registered(root).values()):
        return True
    try:
        graph = paths.graph_db_path(root)
        if not graph.is_file() or paths.is_dataless(graph):
            return False
        with closing(sqlite3.connect(graph.as_uri() + "?mode=ro", uri=True)) as conn:
            dsts = conn.execute("SELECT DISTINCT dst FROM edges WHERE type IN ('writes', 'generates') "
                                "AND status != 'rejected'").fetchall()
        # Compared folded, as the output records are: the index may spell the path otherwise than the disk.
        return any(prefix in notes.FILE_PREFIXES and files_ingest.fold(path) == key
                   for prefix, _, path in (str(dst).partition(":") for (dst,) in dsts))
    except (OSError, sqlite3.Error, ValueError, paths.IdentityUnavailableError):
        return False


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
    reason = "generated" if _generated(root, rel) else None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
        reason = reason or "encoding"
    ending, odd = _ending(text)
    reason = reason or ("nul" if "\0" in text else None) or odd
    return {"text": text, "line_ending": ending, "read_only": reason is not None, "read_only_reason": reason}


def _revision(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _payload(rel: str, raw: bytes, opened: dict[str, Any], lang: str) -> dict[str, Any]:
    reason = opened["read_only_reason"]
    message = None if reason is None else READ_ONLY[reason][1 if lang == "en" else 0]
    return {"path": rel, **opened, "read_only_message": message, "revision": _revision(raw)}


def read_tex(root: str | Path, rel: Any, lang: str = "zh") -> dict[str, Any]:
    """The editor's view of a `.tex`: its text, revision, line ending and whether, and why, it is read-only.
    Writes nothing."""
    root = workspace._root(root)  # noqa: SLF001
    rel = checked_path(root, rel)
    raw = _read(root, rel)
    return _payload(rel, raw, _opened(root, rel, raw), lang)


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
