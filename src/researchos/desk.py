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

Reads only. Everything here is the researcher's own text or the engine's
Chinese; no English reaches the page except inside 「详情」.
"""

from __future__ import annotations

import difflib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from sqlite3 import Connection
from typing import Any

from researchos import db, history, people
from researchos.records import notes as notes_mod
from researchos.records.identity import IdentityState, read_identity

DEFAULT_DAYS = 7
CHANGE_LIMIT = 80
RESULT_LIMIT = 300
GIT_COMMIT_LIMIT = 300
BASELINE_WINDOW = timedelta(minutes=10)
DIFF_COUNT_LIMIT = 1024 * 1024
ATTENTION_BREAKS = ("no_generating_script", "script_missing", "data_missing", "script_unreadable")
RECORD_PREFIX = ".researchos/"


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


def _who(by: str | None, team: people.TeamRead | None, *, name: str | None = None,
         unknown: str = people.FIRST_RESEARCHER_LABEL) -> str:
    """A person as every view says them (8.8): the member's name; nobody
    recorded is 「最初的研究者」 (12.12), or `unknown` where that is wrong."""
    if name:
        return name
    if not by:
        return unknown
    return people.attribution({"by": by}, team)["label"]


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


def _words(sentence: str | None) -> str | None:
    """12.11: an engine sentence shown on the desk says its relation in
    words -- 「行数 96 改为 97」, never an arrow."""
    return None if sentence is None else sentence.replace(" → ", " 改为 ").replace("→", "改为")


def _file_what(store: history.Store, before: sqlite3.Row | None, latest: sqlite3.Row) -> str:
    if before is None:
        return "新文件"
    if latest["kind"] == "data" or before["kind"] == "data":
        sentence = history.table_sentence(json.loads(before["summary"] or "{}"), json.loads(latest["summary"] or "{}"))
        if sentence:
            return _words(sentence) or "内容有改动"
    if latest["stored"] and before["stored"]:
        counts = _line_counts(store, before["sha256"], latest["sha256"])
        if counts is not None:
            added, removed = counts
            if added or removed:
                parts = ([f"增加 {added} 行"] if added else []) + ([f"删去 {removed} 行"] if removed else [])
                return "，".join(parts)
    if before["size"] is not None and latest["size"] is not None and before["size"] != latest["size"]:
        return f"大小 {before['size']} 字节改为 {latest['size']} 字节"
    return "内容有改动"


def _attempt_titles(conn: Connection | None) -> dict[str, dict[str, Any]]:
    if conn is None:
        return {}
    out = {}
    for node in db.get_nodes_by_type(conn, "attempt"):
        attrs = node.get("attrs") or {}
        out[node["id"]] = {"number": attrs.get("number"), "title": attrs.get("description") or node.get("title")}
    return out


def _store_changes(root: Path, project_id: str, since: datetime, team: people.TeamRead | None,
                   tracked: set[str] | None, labels: dict[str, str], attempts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    from researchos.ingest import attempts as attempts_ingest  # noqa: PLC0415

    store = history.Store.existing(project_id)
    if store is None:
        return []
    try:
        rows = store.conn.execute("SELECT * FROM versions ORDER BY seq").fetchall()
        by_node: dict[str, list[sqlite3.Row]] = {}
        baseline: dict[str, datetime] = {}
        for row in rows:
            by_node.setdefault(row["node"], []).append(row)
            group = "attempt" if row["kind"] == "attempt" else "file"
            when = _parse(row["at"])
            if when is not None and (group not in baseline or when < baseline[group]):
                baseline[group] = when
        out = []
        unknown = history.unknown_who_label(root)
        for node, versions in by_node.items():
            latest = versions[-1]
            group = "attempt" if latest["kind"] == "attempt" else "file"
            first_seen = _parse(versions[0]["at"])
            # The first version the first scan of its kind recorded is the
            # baseline, not a change (module docstring).
            base = (versions[0]["source"] == "scan" and first_seen is not None and group in baseline
                    and first_seen - baseline[group] < BASELINE_WINDOW)
            recent = [(i, v) for i, v in enumerate(versions)
                      if (_parse(v["at"]) or since) > since and not (i == 0 and base)]
            if not recent:
                continue
            first_i = recent[0][0]
            before = versions[first_i - 1] if first_i > 0 else None
            window = [v for _, v in recent]
            whos = list(dict.fromkeys(_who(v["who"], team, unknown=unknown) for v in window))
            notes = [v["note"] for v in window if v["note"]]
            approved = [v["approved_by"] for v in window if v["approved_by"]]
            item: dict[str, Any] = {
                "node": node, "at": latest["at"], "who": whos, "notes": list(dict.fromkeys(notes)),
                "approved_by": [_who(a, team) for a in dict.fromkeys(approved)], "versions": len(window),
                "source": latest["source"],
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
                    item["what"] = "新增尝试"
                elif old.get("verdict", "") != new.get("verdict", ""):
                    item["what"] = "判决改了"
                else:
                    item["what"] = "结果改了"
                out.append(item)
                continue
            path = notes_mod.node_path(node)
            if path is None or path.startswith(RECORD_PREFIX) or (tracked is not None and path in tracked):
                continue
            item.update(kind="file", path=path, what=_file_what(store, before, latest),
                        exists=(root / path).exists())
            out.append(item)
        return out
    finally:
        store.close()


_GIT_FORMAT = "%x1e%H%x1f%an%x1f%ae%x1f%aI%x1f%s%x1f"


def _git_changes(root: Path, since: datetime) -> tuple[list[dict[str, Any]], set[str] | None]:
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
                                      "what": "", "exists": (root / path).exists()}
            item["versions"] += 1
            who = name or email or history.LOCAL_LABEL
            if who not in item["who"]:
                item["who"].append(who)
            if subject and subject not in item["notes"]:
                item["notes"].append(subject)
            # The oldest commit in the window decides 新文件 / 已删除.
            item["what"] = {"A": "新文件", "D": "已删除"}.get(status[:1], "内容有改动")
    for item in found.values():
        if item["what"] == "已删除" and item["exists"]:
            item["what"] = "内容有改动"
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
                                       "at": _iso(mtime), "who": [history.LOCAL_LABEL], "notes": [], "approved_by": [],
                                       "versions": 0, "source": "git", "what": "内容有改动", "exists": True})
        item["uncommitted"] = True
        item["at"] = max(item["at"], _iso(mtime), key=lambda a: _parse(a) or since)
    return list(found.values()), tracked


def _decision_changes(root: Path, since: datetime, team: people.TeamRead | None,
                      labels: dict[str, str], attempts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
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
            "title": info.get("title"), "who": [_who(entry.get("by"), team)],
            "approved_by": [_who(entry.get("approved_by"), team)] if entry.get("approved_by") else [],
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


def _results(conn: Connection | None, root: Path) -> tuple[list[dict[str, Any]], int]:
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
            "breaks": [{"kind": b["kind"], "label": b["label"], "path": b.get("path"),
                        "repair": (b.get("repair") or {}).get("label")} for b in breaks],
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
         now: datetime | None = None) -> dict[str, Any]:
    """`GET /api/desk` (module docstring)."""
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
    git_items, tracked = _git_changes(root, when)
    changes = list(git_items)
    if project_id is not None:
        changes += _store_changes(root, project_id, when, team_known, tracked, labels, attempts)
    changes += _decision_changes(root, when, team_known, labels, attempts)
    changes += _note_changes(root, conn, when, me)
    changes.sort(key=lambda c: _parse(c.get("at")) or when, reverse=True)
    results, unlooked = _results(conn, root)
    return {
        "since": _iso(when), "since_default": default, "default_days": DEFAULT_DAYS,
        "changes": changes[:CHANGE_LIMIT], "more_changes": max(0, len(changes) - CHANGE_LIMIT),
        "results": results, "results_not_checked": unlooked, "review": _review_count(conn),
        "abandoned": _abandoned(conn, labels, root),
    }
