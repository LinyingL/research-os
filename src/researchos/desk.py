"""The desk (工作台, DESIGN.md 12.0, 12.9; task V8 phase U2): what changed
since I last looked, which results lost their evidence, which routes were
abandoned -- read from what Research OS already keeps, never typed by
anyone. No counts for their own sake: every item names a file, an attempt
or a decision the researcher can open.

`desk(conn, root, since)` answers `GET /api/desk?since=…`:

- **changes** since `since` (the page keeps a per-person "last seen" per
  project; without one, the last seven days), newest first, one item per
  node:
  - a file -- from the version store (a project that is not a git
    repository, and files git does not track) and, for a git project, from
    `git log --since` plus the working copy's uncommitted edits: what
    changed (新文件 / lines added and removed / the table sentence), who
    (the member a proposal came from, the PI who approved it, the commit's
    author, or 本机) and the note that came with it;
  - an attempt -- its verdict as it moved (the status words of 12.11, the
    researcher's own words beside them), or a new attempt;
  - a decision -- every approval `decisions.toml` holds;
  - a note written by someone other than me.
  A node's *first* version recorded with the first scan of its kind is the
  baseline, not a change: a project added today does not report every one
  of its files as new.
- **results** needing attention: figures whose trace (12.7) is broken
  (no generating script, a script or data file gone, a script unreadable)
  or runs through a link whose judgment is under review (9.6). "Not yet
  filed under an attempt" alone is not loss of evidence and is left to the
  results section.
- **review**: how many old judgments wait for review because their basis
  changed (9.6; the same count as `/api/summary`'s), so the desk can offer
  the list.
- **abandoned**: the attempts whose verdict is 已放弃, with their words.

`payload` -- what `GET /api/desk` answers -- adds the route map of
DESIGN.md Section 14 to these blocks (the cards, the 「源自」 lines and the
column; `route_map`), reading the shared folder on a thread it waits for
only SHARED_TIMEOUT seconds.

Reads only. Everything here is the researcher's own text or a sentence
the engine composes in the page's language (`lang`, the request's
`X-ResearchOS-Lang`; Chinese without it, so the command line and MCP are
unchanged -- 13.6). Every change carries `what_code`, which the page
compares; `what` is only shown (13.6: the page never compares displayed
text). A verdict's `label` stays the vocabulary that read the researcher's
file (13.1); the page words a default status by its code.
"""

from __future__ import annotations

import difflib
import json
import logging
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from sqlite3 import Connection
from typing import Any

from researchos import db, history, people, settings
from researchos.records import notes as notes_mod
from researchos.records.identity import IdentityState, read_identity

logger = logging.getLogger(__name__)

DEFAULT_DAYS = 7
CHANGE_LIMIT = 80
RESULT_LIMIT = 300
GIT_COMMIT_LIMIT = 300
BASELINE_WINDOW = timedelta(minutes=10)
DIFF_COUNT_LIMIT = 1024 * 1024
ATTENTION_BREAKS = ("no_generating_script", "script_missing", "data_missing", "script_unreadable")
RECORD_PREFIX = ".researchos/"
#: A change's fixed words by its `what_code`, Chinese and English (13.4);
#: the other codes -- "lines", "size", "table" -- are composed sentences.
WHAT_WORDS = {
    "new_file": ("新文件", "New file"),
    "deleted": ("已删除", "Deleted"),
    "changed": ("内容有改动", "Content changed"),
    "new_attempt": ("新增尝试", "New attempt"),
    "verdict_changed": ("判决改了", "Verdict changed"),
    "result_changed": ("结果改了", "Result changed"),
}
#: researchos.trace's words for the breaks the desk shows, and their repairs, in English.
BREAK_WORDS_EN = {
    "no_generating_script": "No script that generates it was found",
    "script_unreadable": "The script cannot be read or parsed right now",
    "script_missing": "The script file is not on disk",
    "data_missing": "The data file is not on disk",
}
REPAIR_WORDS_EN = {"generates": "Add source", "writes": "Add source", "belongs_to": "Link files"}


def _parse(at: Any) -> datetime | None:
    if not isinstance(at, str) or not at:
        return None
    try:
        when = datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo is not None else when.astimezone()


def since_of(given: str | None, now: datetime | None = None) -> tuple[datetime, bool]:
    """(the moment changes are counted from, whether it is the default)."""
    when = _parse(given)
    if when is not None:
        return when, False
    now = now or datetime.now(timezone.utc).astimezone()
    return now - timedelta(days=DEFAULT_DAYS), True


def _iso(when: datetime) -> str:
    return when.isoformat(timespec="seconds")


def _what(code: str, lang: str) -> str:
    """A change's fixed words (WHAT_WORDS) in the page's language."""
    zh, en = WHAT_WORDS[code]
    return settings.say(zh, en, lang)


def _who(by: str | None, team: people.TeamRead | None, *, name: str | None = None,
         unknown: str | None = None, lang: str = "zh") -> str:
    """A person as every view says them (8.8): the member's name; nobody
    recorded is 「最初的研究者」 (12.12), or `unknown` where that is wrong."""
    if name:
        return name
    if not by:
        return unknown if unknown is not None else history.first_researcher_label(lang)
    return people.attribution({"by": by}, team, lang)["label"]


def _line_counts(store: history.Store, old_sha: str | None, new_sha: str | None) -> tuple[int, int] | None:
    old = store.read_blob(old_sha) if old_sha else None
    new = store.read_blob(new_sha) if new_sha else None
    if old is None or new is None or len(old) > DIFF_COUNT_LIMIT or len(new) > DIFF_COUNT_LIMIT:
        return None
    try:
        a, b = old.decode("utf-8").splitlines(), new.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return None
    added = removed = 0
    for line in difflib.unified_diff(a, b, lineterm="", n=0):
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


def _file_what(store: history.Store, before: sqlite3.Row | None, latest: sqlite3.Row,
               lang: str = "zh") -> tuple[str, str]:
    """(what_code, what): what changed in a file, in the page's language."""
    if before is None:
        return "new_file", _what("new_file", lang)
    if latest["kind"] == "data" or before["kind"] == "data":
        # 12.13: the engine's word form -- 「行数从 96 增至 97」, never an arrow.
        sentence = history.table_sentence(json.loads(before["summary"] or "{}"), json.loads(latest["summary"] or "{}"),
                                          words=True, lang=lang)
        if sentence:
            return "table", sentence
    if latest["stored"] and before["stored"]:
        counts = _line_counts(store, before["sha256"], latest["sha256"])
        if counts is not None:
            added, removed = counts
            if added or removed:
                parts = []
                if added:
                    parts.append(settings.say("增加 {n} 行", "{n} line added" if added == 1 else "{n} lines added",
                                              lang, n=history.number_text(added, lang)))
                if removed:
                    parts.append(settings.say("删去 {n} 行", "{n} line removed" if removed == 1 else "{n} lines removed",
                                              lang, n=history.number_text(removed, lang)))
                return "lines", settings.say("，", ", ", lang).join(parts)
    if before["size"] is not None and latest["size"] is not None and before["size"] != latest["size"]:
        # 13.5: the English noun follows b ("from 2 to 1 byte", "from 1 to 2 bytes").
        en = "Size changed from {a} to {b} byte" if latest["size"] == 1 else "Size changed from {a} to {b} bytes"
        return "size", settings.say("大小从 {a} 变为 {b} 字节", en, lang,
                                    a=history.number_text(before["size"], lang), b=history.number_text(latest["size"], lang))
    return "changed", _what("changed", lang)


def _attempt_titles(conn: Connection | None) -> dict[str, dict[str, Any]]:
    if conn is None:
        return {}
    out = {}
    for node in db.get_nodes_by_type(conn, "attempt"):
        attrs = node.get("attrs") or {}
        out[node["id"]] = {"number": attrs.get("number"), "title": attrs.get("description") or node.get("title")}
    return out


def _group(row: sqlite3.Row) -> str:
    return "attempt" if row["kind"] == "attempt" else "file"


def _store_versions(store: history.Store) -> tuple[dict[str, list[sqlite3.Row]], dict[str, datetime]]:
    """Every node's versions in order, and when the first scan of each kind
    (attempts, files) recorded its first version."""
    by_node: dict[str, list[sqlite3.Row]] = {}
    baseline: dict[str, datetime] = {}
    for row in store.conn.execute("SELECT * FROM versions ORDER BY seq").fetchall():
        by_node.setdefault(row["node"], []).append(row)
        when = _parse(row["at"])
        if when is not None and (_group(row) not in baseline or when < baseline[_group(row)]):
            baseline[_group(row)] = when
    return by_node, baseline


def _is_baseline(versions: list[sqlite3.Row], baseline: dict[str, datetime]) -> bool:
    """The first version the first scan of its kind recorded is the
    baseline, not a change (module docstring); so is a first version 加入
    installed (`publish`)."""
    first, group = _parse(versions[0]["at"]), _group(versions[-1])
    return (versions[0]["source"] in ("scan", "publish") and first is not None and group in baseline
            and first - baseline[group] < BASELINE_WINDOW)


def _store_changes(root: Path, project_id: str, since: datetime, team: people.TeamRead | None,
                   tracked: set[str] | None, labels: dict[str, str], attempts: dict[str, dict[str, Any]],
                   lang: str = "zh") -> list[dict[str, Any]]:
    from researchos.ingest import attempts as attempts_ingest  # noqa: PLC0415

    store = history.Store.existing(project_id)
    if store is None:
        return []
    try:
        by_node, baseline = _store_versions(store)
        out = []
        unknown = history.unknown_who_label(root, lang)
        for node, versions in by_node.items():
            latest = versions[-1]
            group = _group(latest)
            base = _is_baseline(versions, baseline)
            recent = [(i, v) for i, v in enumerate(versions)
                      if (_parse(v["at"]) or since) > since and not (i == 0 and base)]
            if not recent:
                continue
            first_i = recent[0][0]
            before = versions[first_i - 1] if first_i > 0 else None
            window = [v for _, v in recent]
            whos = list(dict.fromkeys(_who(v["who"], team, unknown=unknown, lang=lang) for v in window))
            notes = [v["note"] for v in window if v["note"]]
            approved = [v["approved_by"] for v in window if v["approved_by"]]
            item: dict[str, Any] = {
                "node": node, "at": latest["at"], "who": whos, "notes": list(dict.fromkeys(notes)),
                "approved_by": [_who(a, team, lang=lang) for a in dict.fromkeys(approved)], "versions": len(window),
                "source": latest["source"], "snapshot": latest["snapshot"],
            }
            if group == "attempt":
                new = json.loads(latest["summary"] or "{}")
                old = json.loads(before["summary"] or "{}") if before is not None else None
                info = attempts.get(node, {})
                item.update(kind="attempt", number=info.get("number"), title=info.get("title"),
                            new_status=attempts_ingest.verdict_status(new.get("verdict", ""), labels),
                            old_status=(attempts_ingest.verdict_status(old.get("verdict", ""), labels)
                                        if old is not None else None),
                            result=new.get("result", ""))
                if old is None:
                    item["what_code"] = "new_attempt"
                elif old.get("verdict", "") != new.get("verdict", ""):
                    item["what_code"] = "verdict_changed"
                else:
                    item["what_code"] = "result_changed"
                item["what"] = _what(item["what_code"], lang)
                out.append(item)
                continue
            path = notes_mod.node_path(node)
            if path is None or path.startswith(RECORD_PREFIX) or (tracked is not None and path in tracked):
                continue
            code, what = _file_what(store, before, latest, lang)
            item.update(kind="file", path=path, what_code=code, what=what, exists=(root / path).exists())
            out.append(item)
        return out
    finally:
        store.close()


_GIT_FORMAT = "%x1e%H%x1f%an%x1f%ae%x1f%aI%x1f%s%x1f"


def _git_changes(root: Path, since: datetime, lang: str = "zh") -> tuple[list[dict[str, Any]], set[str] | None]:
    """Files git tracks: commits since `since`, and uncommitted edits."""
    if not (root / ".git").exists():
        return [], None
    tracked_out = history._git(root, ["ls-files", "-z"])
    tracked = set(str(tracked_out).split("\x00")) - {""} if tracked_out is not None else set()
    out = history._git(root, ["log", f"--since={_iso(since)}", f"--max-count={GIT_COMMIT_LIMIT}", "--no-show-signature",
                              "--no-textconv", "--no-ext-diff", "--no-renames", "-z", "--name-status",
                              f"--format={_GIT_FORMAT}"])
    found: dict[str, dict[str, Any]] = {}
    for record in str(out or "").split("\x1e"):
        parts = record.split("\x1f", 5)
        if len(parts) != 6:
            continue
        _sha, name, email, at, subject, rest = parts
        fields = [f for f in rest.strip("\n").split("\x00") if f.strip("\n")]
        for status, path in zip(fields[0::2], fields[1::2]):
            status, path = status.strip("\n"), path.strip("\n")
            if path.startswith(RECORD_PREFIX):
                continue
            item = found.get(path)
            if item is None:  # newest commit first
                item = found[path] = {"kind": "file", "node": notes_mod.normalize_node(path), "path": path, "at": at,
                                      "who": [], "notes": [], "approved_by": [], "versions": 0, "source": "git",
                                      "what_code": "", "exists": (root / path).exists()}
            item["versions"] += 1
            who = name or email or history.local_label(lang)
            if who not in item["who"]:
                item["who"].append(who)
            if subject and subject not in item["notes"]:
                item["notes"].append(subject)
            # The oldest commit in the window decides 新文件 / 已删除.
            item["what_code"] = {"A": "new_file", "D": "deleted"}.get(status[:1], "changed")
    for item in found.values():
        if item["what_code"] == "deleted" and item["exists"]:
            item["what_code"] = "changed"
        item["what"] = _what(item["what_code"], lang)
    status_out = history._git(root, ["status", "--porcelain=v1", "-z", "--untracked-files=no"])
    entries = str(status_out or "").split("\x00")
    skip_next = False
    for entry in entries:
        if skip_next:  # a rename's or copy's original path, which carries no status
            skip_next = False
            continue
        if len(entry) < 4:
            continue
        skip_next = entry[0] in "RC" or entry[1] in "RC"
        path = entry[3:]
        if path.startswith(RECORD_PREFIX):
            continue
        full = root / path
        try:
            mtime = datetime.fromtimestamp(full.stat().st_mtime, timezone.utc).astimezone()
        except OSError:
            continue
        if mtime <= since:
            continue
        item = found.setdefault(path, {"kind": "file", "node": notes_mod.normalize_node(path), "path": path,
                                       "at": _iso(mtime), "who": [history.local_label(lang)], "notes": [],
                                       "approved_by": [], "versions": 0, "source": "git", "what_code": "changed",
                                       "what": _what("changed", lang), "exists": True})
        item["uncommitted"] = True
        item["at"] = max(item["at"], _iso(mtime), key=lambda a: _parse(a) or since)
    return list(found.values()), tracked


def _decision_changes(root: Path, since: datetime, team: people.TeamRead | None,
                      labels: dict[str, str], attempts: dict[str, dict[str, Any]],
                      lang: str = "zh") -> list[dict[str, Any]]:
    from researchos.ingest import attempts as attempts_ingest  # noqa: PLC0415
    from researchos.records import decisions  # noqa: PLC0415

    out = []
    for entry in decisions.entries(root):
        when = _parse(entry.get("at"))
        if when is None or when <= since:
            continue
        attempt = entry.get("attempt")
        info = attempts.get(attempt, {}) if isinstance(attempt, str) else {}
        out.append({
            "kind": "decision", "node": attempt, "at": entry.get("at"), "act": entry.get("act"),
            "proposal_kind": entry.get("kind"), "number": entry.get("number") or info.get("number"),
            "title": info.get("title"), "who": [_who(entry.get("by"), team, lang=lang)],
            "approved_by": [_who(entry.get("approved_by"), team, lang=lang)] if entry.get("approved_by") else [],
            "verdict_status": (attempts_ingest.verdict_status(entry.get("verdict"), labels)
                               if entry.get("verdict") else None),
            "reason": entry.get("reason"), "notes": [entry["note"]] if entry.get("note") else [],
            "missing_from_file": bool(entry.get("missing_from_file")),
        })
    return out


def _note_changes(root: Path, conn: Connection | None, since: datetime, me: str | None) -> list[dict[str, Any]]:
    try:
        timeline = notes_mod.notes_for(root, None, conn)["notes"]
    except Exception:  # noqa: BLE001 -- a desk never fails on a ledger the notes view explains
        return []
    out = []
    for note in timeline:
        when = _parse(note.get("at"))
        if when is None or when <= since or note.get("withdrawn"):
            continue
        by = note.get("by")
        if by is None or (me is not None and by == me):
            continue  # my own notes, or the first researcher's before names existed
        node = note.get("node")
        item = {"kind": "note", "node": node, "path": notes_mod.node_path(node) if isinstance(node, str) else None,
                "at": note.get("at"), "who": [note.get("label") or by], "text": note.get("text"),
                "id": note.get("id")}
        # 8.8: what the note is on, as a reader names it -- an attempt's
        # number and title, a section's heading -- never the node id.
        if item["path"] is None and isinstance(node, str) and conn is not None:
            from researchos import nodepage  # noqa: PLC0415

            try:
                item["label"] = nodepage._label(conn, node)
            except Exception:  # noqa: BLE001 -- the page falls back to a plain word
                item["label"] = None
        out.append(item)
    return out


def _break_words(b: dict[str, Any], lang: str) -> dict[str, Any]:
    """A break as the desk shows it: its kind, the trace's words for it and
    for its repair in the page's language."""
    repair = b.get("repair") or {}
    return {"kind": b["kind"], "label": settings.say(b["label"], BREAK_WORDS_EN.get(b["kind"], b["label"]), lang),
            "path": b.get("path"),
            "repair": (settings.say(repair["label"], REPAIR_WORDS_EN.get(repair.get("type"), repair["label"]), lang)
                       if repair.get("label") else None)}


def _results(conn: Connection | None, root: Path, lang: str = "zh") -> tuple[list[dict[str, Any]], int]:
    """Figures whose trace lost its evidence or runs through a link under
    review; (the list, how many figures were not looked at)."""
    from researchos import trace as trace_mod  # noqa: PLC0415

    if conn is None:
        return [], 0
    figures = sorted(n["id"] for n in db.get_nodes_by_type(conn, "figure"))
    out = []
    for node in figures[:RESULT_LIMIT]:
        try:
            chain = trace_mod.chain(conn, root, node)
        except Exception:  # noqa: BLE001 -- one figure never hides the others
            continue
        breaks = [b for b in chain.get("breaks", []) if b.get("kind") in ATTENTION_BREAKS]
        review = [link for link in chain.get("links", []) if link.get("review") or link.get("conflict")]
        if not breaks and not review:
            continue
        out.append({
            "node": node, "path": chain["node"].get("path"), "exists": chain["node"].get("exists"),
            "breaks": [_break_words(b, lang) for b in breaks],
            "review": len(review),
            "drafts": sorted({d["file"] for d in chain.get("drafts", []) if d.get("file")}),
        })
    return out, max(0, len(figures) - RESULT_LIMIT)


def _review_count(conn: Connection | None) -> int:
    """9.6's count -- the same number as /api/summary's `review`: old
    judgments (and variable cards) whose basis changed. 0 for an index
    from before V5, which keeps no review."""
    from researchos.records import implementation, judgements  # noqa: PLC0415

    if conn is None or not db._has_machine_status(conn):
        return 0
    try:
        return judgements.review_count(conn) + implementation.review_groups(conn)["count"]
    except Exception:  # noqa: BLE001 -- the review list itself says what is wrong
        return 0


def _abandoned(conn: Connection | None, labels: dict[str, str], root: Path | None = None) -> list[dict[str, Any]]:
    """Every abandoned route with its words (12.0: "which routes were
    abandoned and why"): the result and the reason -- from the approval
    that recorded it, else from the table's own 「放弃理由：…」 -- in the
    table's order."""
    from researchos import nodepage, proposals  # noqa: PLC0415
    from researchos.ingest import attempts as attempts_ingest  # noqa: PLC0415

    if conn is None:
        return []
    markers = proposals.abandon_markers(root) if root is not None else [proposals.DEFAULT_ABANDON_MARKER]
    reasons: dict[str, str] = {}
    if root is not None:
        for entry in nodepage._decisions(root)[0]:
            if entry.get("act") == "abandoned" and isinstance(entry.get("attempt"), str) \
                    and str(entry.get("reason") or "").strip():
                reasons[entry["attempt"]] = str(entry["reason"]).strip()
    out = []
    nodes = sorted(db.get_nodes_by_type(conn, "attempt"),
                   key=lambda n: ((n.get("attrs") or {}).get("source_file", ""),
                                  attempts_ingest.attempt_sort_key(str((n.get("attrs") or {}).get("number") or ""))))
    for node in nodes:
        human = node.get("human_fields") or {}
        verdict = human.get("verdict", "")
        status = attempts_ingest.route_status(verdict, labels, markers)
        if status["status"] == "abandoned":
            attrs = node.get("attrs") or {}
            result, reason = nodepage._reason_in(str(human.get("result") or ""))
            out.append({"id": node["id"], "number": attrs.get("number"), "title": attrs.get("description"),
                        "verdict_status": status, "result": result,
                        "reason": reasons.get(node["id"]) or reason})
    return out


def desk(conn: Connection | None, project_root: str | Path, since: str | None = None, *,
         now: datetime | None = None, lang: str = "zh") -> dict[str, Any]:
    """`GET /api/desk` (module docstring); its sentences in `lang` (13.6)."""
    from researchos.ingest import attempts as attempts_ingest  # noqa: PLC0415

    root = Path(project_root)
    when, default = since_of(since, now)
    got = read_identity(root)
    project_id = got.identity.id if got.state is IdentityState.PRESENT and got.identity is not None else None
    team = people.read_team(root)
    team_known = team if team.state == "present" else None
    try:
        me = people.current_member_id()
    except people.PeopleError:
        me = None
    labels = attempts_ingest.verdict_labels(root)
    attempts = _attempt_titles(conn)
    git_items, tracked = _git_changes(root, when, lang)
    changes = list(git_items)
    if project_id is not None:
        changes += _store_changes(root, project_id, when, team_known, tracked, labels, attempts, lang)
    changes += _decision_changes(root, when, team_known, labels, attempts, lang)
    changes += _note_changes(root, conn, when, me)
    changes.sort(key=lambda c: _parse(c.get("at")) or when, reverse=True)
    results, unlooked = _results(conn, root, lang)
    return {
        "since": _iso(when), "since_default": default, "default_days": DEFAULT_DAYS,
        "changes": changes[:CHANGE_LIMIT], "more_changes": max(0, len(changes) - CHANGE_LIMIT),
        "results": results, "results_not_checked": unlooked, "review": _review_count(conn),
        "abandoned": _abandoned(conn, labels, root),
    }


# -- the route map (DESIGN.md Section 14) --------------------------------------------------------

#: A verdict's colour word on the map, by its status (desk.js's VERDICT_TONES); any other is "plain".
TONES = {"current": "olive", "abandoned": "gray", "pending": "ochre", "adverse": "clay"}
#: How long the desk waits for the shared folder before the map comes back without it (14.4).
SHARED_TIMEOUT = 4.0
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
WAITING_WORDS = ("等你批准", "Awaiting your approval")
REVIEW_MARK = ("待复核 {n}", "Needs review: {n}")
ROLE_WORDS = {
    "none": ("你还不是这个项目的成员：请 PI 把你加入团队，然后同步",
             "You are not a member of this project yet: ask the PI to add you to the team, then sync"),
    "unknown": ("成员文件（team.toml）当前无法读取，无法判断谁是 PI",
                "The members file (team.toml) cannot be read right now, so who the PI is cannot be told"),
}
SHARED_WORDS = {
    "not_configured": ("这个项目还没有连上公盘：提议和同步的情况看不到",
                       "This project is not connected to a shared folder: proposals and sync cannot be shown"),
    "unreadable": ("公盘现在打不开：提议和同步的情况暂时看不到",
                   "The shared folder cannot be opened right now: proposals and sync cannot be shown"),
    "slow": ("公盘现在读得太慢：提议和同步的情况稍后再看",
             "The shared folder is slow to read right now: proposals and sync will show later"),
}
_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()


def _start(key: str, fn: Callable[[], Any]) -> dict[str, Any]:
    """`fn` on a thread of its own -- one per `key` at a time: a request
    that finds one running waits on it, so a folder that hangs holds one
    thread, not one per visit to the desk."""
    with _JOBS_LOCK:
        job = _JOBS.get(key)
        if job is None:
            job = _JOBS[key] = {"done": threading.Event(), "result": ("slow", None)}

            def run() -> None:
                try:
                    job["result"] = ("ok", fn())
                except Exception as exc:  # noqa: BLE001 -- said in the column
                    job["result"] = ("error", exc)
                finally:
                    with _JOBS_LOCK:
                        _JOBS.pop(key, None)
                    job["done"].set()

            threading.Thread(target=run, name="desk-shared-folder", daemon=True).start()
    return job


def _read_shared(root: Path, role: str, seen: Any) -> dict[str, Any]:
    """Everything the desk reads from the shared folder: the proposals (the
    PI's inbox, a member's own), and a member's sync state and the
    officialness of their proposals. Each part fails on its own."""
    from researchos import officialness, proposals, share  # noqa: PLC0415

    got: dict[str, Any] = {}
    parts = [("listing", lambda: proposals.listing(root))]
    if role == "member":
        parts += [("status", lambda: share.status(root))]
        if seen is not None:
            parts += [("pending", lambda: officialness.pending_of(seen))]
    for name, fn in parts:
        try:
            got[name] = fn()
        except Exception as exc:  # noqa: BLE001 -- said in the column
            got.setdefault("errors", {})[name] = exc
    return got


def _change_times(root: Path, project_id: str | None) -> dict[str, datetime]:
    """When each node last changed (14.5's 「有改动」): a file git tracks by
    its newest commit or uncommitted edit (the last GIT_COMMIT_LIMIT
    commits), anything else -- an attempt's row, a file git does not track
    -- by its newest version in the version store, the baseline aside."""
    git_items, tracked = _git_changes(root, _EPOCH)
    out = {item["node"]: when for item in git_items if (when := _parse(item["at"])) is not None}
    store = history.Store.existing(project_id) if project_id is not None else None
    if store is None:
        return out
    try:
        by_node, baseline = _store_versions(store)
    finally:
        store.close()
    for node, versions in by_node.items():
        if tracked is not None and notes_mod.node_path(node) in tracked:
            continue
        times = [w for v in versions[1 if _is_baseline(versions, baseline) else 0:] if (w := _parse(v["at"]))]
        if times:
            out[node] = max(times)
    return out


def _date_key(raw: Any, date_year: int | None) -> str | None:
    """The table's free-text date as a day, where `date_year` makes it
    readable (`consistency._parse_attempt_date`: never guessed); else None."""
    from researchos import consistency  # noqa: PLC0415

    got = consistency._parse_attempt_date(str(raw or ""), date_year)
    return got.isoformat() if got is not None else None


def _touches(p: dict[str, Any], table: str | None, holders: dict[str, list[str]], role: str) -> list[str]:
    """The attempts a proposal touches: the row it changes, the ends of its
    line, the parents a new attempt names (and, for its author, the new
    row), and the attempts holding a file it carries."""
    out: list[str] = []
    row = (lambda n: f"attempt:{table}#{n}" if table and n else None)
    found = [row((p.get("verdict") or {}).get("number"))]
    if p.get("kind") == "attempt_new":
        attempt = p.get("attempt") or {}
        found += ([row(attempt.get("number"))] if role == "member" else []) + list(attempt.get("parents") or [])
    found += [(p.get("route") or {}).get(k) for k in ("child", "parent")]
    for f in p.get("files") or []:
        try:
            found += holders.get(notes_mod.normalize_node(str(f.get("path"))), [])
        except ValueError:
            continue
    for node in found:
        if isinstance(node, str) and node and node not in out:
            out.append(node)
    return out


def _review(conn: Connection, holders: dict[str, list[str]], order: list[str], lang: str) -> dict[str, Any]:
    """待复核 (14.4): each judgment under every attempt holding an end of its
    link (14.3's membership rule), else under none -- with the variable
    cards, which belong to no attempt."""
    from researchos import nodepage  # noqa: PLC0415
    from researchos.records import implementation, judgements  # noqa: PLC0415

    out: dict[str, Any] = {"count": 0, "groups": [], "none": {"items": [], "cards": []}}
    if not db._has_machine_status(conn):
        return out
    items = judgements.review_items(conn)["review"]
    cards = implementation.review_groups(conn)
    under: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        shown = {k: item.get(k) for k in ("src", "dst", "type", "extractor", "outcome", "reason", "verdict", "at", "note")}
        shown.update(label=judgements.reason_label(item.get("reason"), lang), src_label=nodepage._label(conn, item["src"]),
                     dst_label=nodepage._label(conn, item["dst"]))
        ends = (item["src"], item["dst"])
        shown["attempts"] = [a for a in order if a in ends or any(a in holders.get(e, ()) for e in ends)]
        for a in shown["attempts"]:
            under.setdefault(a, []).append(shown)
        if not shown["attempts"]:
            out["none"]["items"].append(shown)
    out["groups"] = [{"id": a, "items": under[a]} for a in order if a in under]
    for g in cards["groups"]:
        n = len(g["cards"])
        out["none"]["cards"].append({
            "key": g["key"], "script": g["script"], "cards": [c.get("card") for c in g["cards"]], "counted": g["counted"],
            "words": settings.say(implementation.GROUP_MESSAGE, "This script’s change touches {n} variable" if n == 1
                                  else "This script’s change touches {n} variables", lang, n=n) if g["script"] else None})
    out["count"] = len(items) + cards["count"]
    return out


def _lines(root: Path, cards: set[str], team: people.TeamRead | None, lang: str) -> tuple[list, list, str | None]:
    """The 「源自」 lines `mappings.toml` holds (14.2): those between two
    attempts of the map, and those with an end the map does not have (a
    renumbered row), which wait in the file; a line that would close a
    circle is not among them (a mapping problem)."""
    from researchos.ingest import mappings as mappings_ingest  # noqa: PLC0415

    try:
        loaded = mappings_ingest.load_mappings(root)
    except mappings_ingest.MappingsFileError as exc:
        return [], [], str(exc)
    lines, missing = [], []
    for m in loaded.mappings:
        if m.type != mappings_ingest.DERIVES_FROM:
            continue
        item = {"child": m.from_id, "parent": m.to_id, "note": m.note, "date": m.date, "by": m.by,
                "by_label": _who(m.by, team, lang=lang), "pending": None}
        gone = [end for end, node in (("child", m.from_id), ("parent", m.to_id)) if node not in cards]
        if gone:
            item.update(missing=gone, child_number=m.from_path.rpartition("#")[2],
                        parent_number=m.to_path.rpartition("#")[2])
        (missing if gone else lines).append(item)
    return lines, missing, None


def route_map(conn: Connection, project_root: str | Path, *, lang: str = "zh", seen: Any = None,
              timeout: float | None = None) -> dict[str, Any]:
    """The desk's route map and its column (14.1, 14.3-14.5), in one pass;
    see `payload`. `seen`: the officialness view, whose pending proposals
    are read with the shared folder and merged in."""
    from researchos import officialness, proposals  # noqa: PLC0415
    from researchos import trace as trace_mod  # noqa: PLC0415
    from researchos.ingest import attempts as attempts_ingest  # noqa: PLC0415

    root = Path(project_root)
    role, me, team = proposals.role_of(root)
    team_known = team if team.state == "present" else None
    # The shared folder first, on its own thread: the map never waits on it longer than `timeout`.
    job = _start(f"{root.resolve()}|{role}|{me}", lambda: _read_shared(root, role, seen)) \
        if role in ("pi", "member") else None
    started = datetime.now(timezone.utc)

    config = trace_mod._attempts_config(root)
    table = config.file if config is not None else None
    labels = attempts_ingest.verdict_labels(root)
    markers = proposals.abandon_markers(root)
    nodes = db.get_nodes_by_type(conn, "attempt")
    members = trace_mod.attempt_members(conn, root, nodes)
    holders: dict[str, list[str]] = {}
    for attempt_id, files in members.items():
        for f in files:
            holders.setdefault(f, []).append(attempt_id)
    got = read_identity(root)
    times = _change_times(root, got.identity.id if got.state is IdentityState.PRESENT and got.identity else None)
    abandoned = {a["id"]: a for a in _abandoned(conn, labels, root)}
    attempts = []
    for n in nodes:
        attrs, human = n.get("attrs") or {}, n.get("human_fields") or {}
        status = attempts_ingest.route_status(human.get("verdict", ""), labels, markers)
        gone = abandoned.get(n["id"])
        changed = [times[i] for i in (n["id"], *members.get(n["id"], {})) if i in times]
        attempts.append({
            "id": n["id"], "number": str(attrs.get("number") or ""),
            "title": attrs.get("description") or n.get("title") or "", "date": str(attrs.get("date") or ""),
            "date_key": _date_key(attrs.get("date"), config.date_year if config is not None else None),
            "route_status": status, "tone": TONES.get(status["status"], "plain"), "abandoned": gone is not None,
            "result": gone["result"] if gone else str(human.get("result") or ""),
            "reason": gone["reason"] if gone else None, "changed_at": _iso(max(changed)) if changed else None,
            "marks": {"proposals": [], "review": None}, "_source": attrs.get("source_file", ""),
        })
    attempts.sort(key=lambda a: (a["date_key"] is None, a["date_key"] or "",
                                 attempts_ingest.attempt_sort_key(a["number"]), a.pop("_source")))
    by_id = {a["id"]: a for a in attempts}
    for i, a in enumerate(attempts):
        a["order"] = i
    lines, missing, lines_problem = _lines(root, set(by_id), team_known, lang)
    review = _review(conn, holders, list(by_id), lang)
    for group in review["groups"]:
        by_id[group["id"]]["marks"]["review"] = {"count": len(group["items"]),
                                                 "label": settings.say(*REVIEW_MARK, lang, n=len(group["items"]))}

    column: dict[str, Any] = {"role": role, "me": me, "notice": settings.say(*ROLE_WORDS[role], lang)
                              if role in ROLE_WORDS else None, "shared": {"state": "not_needed", "message": None},
                              "waiting": [], "mine": [], "review": review, "sync": None}
    if job is not None:
        left = (timeout if timeout is not None else SHARED_TIMEOUT) - (datetime.now(timezone.utc) - started).total_seconds()
        job["done"].wait(max(0.0, left))
        state, shared = job["result"] if job["done"].is_set() else ("slow", None)
        errors = (shared or {}).get("errors", {}) if state == "ok" else {"listing": shared}
        listing = (shared or {}).get("listing") if state == "ok" else None
        if state == "slow":
            column["shared"]["state"] = "slow"
        elif listing is None:
            column["shared"]["state"] = "not_configured" \
                if getattr(errors.get("listing"), "code", None) == "not_configured" else "unreadable"
        else:
            column["shared"]["state"] = "ok"
        column["shared"]["message"] = settings.say(*SHARED_WORDS[column["shared"]["state"]], lang) \
            if column["shared"]["state"] in SHARED_WORDS else None
        if seen is not None:
            officialness.with_pending(seen, (shared or {}).get("pending") if state == "ok" else None)
        status = (shared or {}).get("status") if state == "ok" else None
        if role == "member" and isinstance(status, dict):
            column["sync"] = {k: status.get(k) for k in ("shared", "reachable", "behind", "last_snapshot")}
            column["sync"]["seq"] = (status.get("joined") or {}).get("seq")
        for p in sorted((listing or {}).get("proposals") or [], key=lambda p: str(p.get("at") or ""), reverse=True):
            p = dict(p, by_label=_who(p.get("by"), team_known, lang=lang), touches=_touches(p, table, holders, role))
            returned = [o for o in p.get("outcomes") or [] if o.get("act") == "returned"]
            p["reason"] = returned[-1].get("text") if p.get("state") == "returned" and returned else None
            marked = p["state"] in ("pending", "conflict") if role == "pi" else p["state"] == "pending"
            if role == "pi" and marked:
                column["waiting"].append(p)
            elif role == "member":
                column["mine"].append(p)
            label = settings.say(*WAITING_WORDS, lang) if role == "pi" else officialness.label(officialness.PROPOSED, lang)
            for node in p["touches"] if marked else []:
                if node in by_id:
                    by_id[node]["marks"]["proposals"].append({"id": p["id"], "by": p.get("by"), "kind": p.get("kind"),
                                                              "state": p["state"], "label": label})
            route = p.get("route") or {}
            if role == "pi" and marked and p.get("kind") == "route_link" and route.get("op") == "remove":
                for line in lines:
                    if (line["child"], line["parent"]) == (route.get("child"), route.get("parent")):
                        line["pending"] = {"proposal": p["id"], "op": "remove", "by": p.get("by"),
                                           "by_label": p["by_label"], "state": p["state"]}
    elif seen is not None:
        officialness.with_pending(seen, None)  # not read: not known
    if role == "member" and seen is not None:
        notes = {(r.get("child"), r.get("parent")): r.get("note") for p in column["mine"]
                 if (r := p.get("route") or {})}
        for drawn in officialness.proposed_lines(seen, lang):
            mark = drawn[officialness.FIELD]
            pending = {"proposal": mark.get("proposal"), "op": drawn["op"], "by": me,
                       "by_label": _who(me, team_known, lang=lang), "state": mark["state"]}
            line = next((x for x in lines if (x["child"], x["parent"]) == (drawn["src"], drawn["dst"])), None)
            if line is None and drawn["op"] == "add":
                line = {"child": drawn["src"], "parent": drawn["dst"], "note": notes.get((drawn["src"], drawn["dst"])),
                        "date": None, "by": me, "by_label": pending["by_label"]}
                lines.append(line)
            if line is not None:
                line.update(pending=pending, **{officialness.FIELD: mark})
    column["empty"] = not (column["waiting"] or column["mine"] or review["count"] or missing
                           or (column["sync"] or {}).get("behind"))
    return {"role": role, "attempts": attempts, "lines": lines, "lines_missing": missing,
            "lines_problem": lines_problem, "column": column}


def payload(conn: Connection, project_root: str | Path, since: str | None = None, *, now: datetime | None = None,
            lang: str = "zh", timeout: float | None = None) -> dict[str, Any]:
    """`GET /api/desk` (DESIGN.md 14.1, 14.3-14.5): `desk`'s blocks (kept for
    the page until the route map replaces it) and the route map:

    - **attempts** -- the cards, in the map's order (`order`: by the date
      where `date_year` makes it readable -- `date_key` -- then by the
      table's number): number, title, the date as written, `route_status`
      and its `tone`, `abandoned` with the `reason`, `changed_at` (the
      newest change of its row or of any of its files, 14.3's membership
      rule; the page compares it with its own last visit for 「有改动」),
      and `marks`: the proposals touching it (等你批准 for the PI, 提议中
      for its author) and its 待复核 count;
    - **lines** -- the 「源自」 lines, child and parent, note, who drew them
      and when, `pending` when a proposal adds or removes one (a member's
      own proposed line is drawn, marked); **lines_missing** -- the lines
      with an end the map does not have;
    - **column** -- the role (from the team file, never the shared folder),
      the PI's `waiting` proposals of every kind or a member's own (`mine`,
      each with the attempts it `touches` and a returned one's `reason`),
      待复核 grouped by attempt (`review`), a member's `sync` state, and
      `shared`: whether the shared folder could be read -- the map never
      waits on it longer than SHARED_TIMEOUT.

    Every attempt, line and review group carries `officialness` (12.12);
    every sentence composed here is in `lang` (13.6)."""
    from researchos import officialness  # noqa: PLC0415

    root = Path(project_root)
    out = desk(conn, root, since, now=now, lang=lang)
    try:
        seen = officialness.view(root, pending=False)
    except Exception:  # noqa: BLE001 -- contained, as every view's officialness is
        logger.exception("officialness not added to the desk")
        seen = None
    out.update(route_map(conn, root, lang=lang, seen=seen, timeout=timeout))
    if seen is not None:
        try:
            officialness.annotate_desk(seen, out, lang)
        except Exception:  # noqa: BLE001
            logger.exception("officialness not added to the desk")
    return out
