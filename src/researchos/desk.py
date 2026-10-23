"""The desk (工作台, DESIGN.md Section 14, amending 12.0): the route map of
the attempts with the column of what waits beside it -- read from what
Research OS already keeps, never typed by anyone. No counts for their own
sake: every item names an attempt, a proposal, a judgment or a line the
researcher can open.

`payload` answers `GET /api/desk`: the cards, the 「源自」 lines and the
column (`route_map`), reading the shared folder on a thread it waits for
only SHARED_TIMEOUT seconds. What the first desk listed went where 14.5
says: *what changed since I last looked* is each card's `changed_at` (its
row or one of its files; the page compares it with its own last visit for
「有改动」, and the panel's timeline says what), *abandoned routes* are the
quieter cards with their reason, *results that lost their evidence* are on
图表与论断 (12.13). A node's *first* version recorded with the first scan of its
kind is the baseline, not a change: a project added today does not mark
every attempt as changed.

Reads only. Everything here is the researcher's own text or a sentence
the engine composes in the page's language (`lang`, the request's
`X-ResearchOS-Lang`; Chinese without it -- 13.6). A verdict's `label`
stays the vocabulary that read the researcher's file (13.1); the page
words a default status by its code.
"""

from __future__ import annotations

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

GIT_COMMIT_LIMIT = 300
CHANGED_LISTED = 20  # an attempt's newest changes the panel's timeline can say (14.5)
BASELINE_WINDOW = timedelta(minutes=10)
RECORD_PREFIX = ".researchos/"


def _parse(at: Any) -> datetime | None:
    if not isinstance(at, str) or not at:
        return None
    try:
        when = datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo is not None else when.astimezone()


def _iso(when: datetime) -> str:
    return when.isoformat(timespec="seconds")


def _who(by: str | None, team: people.TeamRead | None, *, name: str | None = None,
         unknown: str | None = None, lang: str = "zh") -> str:
    """A person as every view says them (8.8): the member's name; nobody
    recorded is 「最初的研究者」 (12.12), or `unknown` where that is wrong."""
    if name:
        return name
    if not by:
        return unknown if unknown is not None else history.first_researcher_label(lang)
    return people.attribution({"by": by}, team, lang)["label"]


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


def _git_times(root: Path) -> tuple[dict[str, datetime], set[str] | None]:
    """For a git project, when each file git tracks last changed -- its
    newest commit among the last GIT_COMMIT_LIMIT, or an edit not committed
    yet (the file's time) -- and the paths git tracks; ({}, None) for a
    project that is not a git repository."""
    if not (root / ".git").exists():
        return {}, None
    tracked_out = history._git(root, ["ls-files", "-z"])
    tracked = set(str(tracked_out).split("\x00")) - {""} if tracked_out is not None else set()
    times: dict[str, datetime] = {}

    def seen(path: str, when: datetime | None) -> None:
        if when is not None and path and not path.startswith(RECORD_PREFIX):
            try:
                node = notes_mod.normalize_node(path)
            except ValueError:  # a name no node can have (a line break in it): never on the map
                return
            times[node] = max(when, times.get(node, when))

    out = history._git(root, ["log", f"--max-count={GIT_COMMIT_LIMIT}", "--no-show-signature", "--no-textconv",
                              "--no-ext-diff", "--no-renames", "-z", "--name-only", "--format=%x1e%aI%x1f"])
    for record in str(out or "").split("\x1e"):
        at, sep, paths = record.partition("\x1f")
        for path in paths.split("\x00") if sep else []:
            seen(path.strip("\n"), _parse(at))
    status_out = history._git(root, ["status", "--porcelain=v1", "-z", "--untracked-files=no"])
    skip_next = False
    for entry in str(status_out or "").split("\x00"):
        if skip_next:  # a rename's or copy's original path, which carries no status
            skip_next = False
            continue
        if len(entry) < 4:
            continue
        skip_next = entry[0] in "RC" or entry[1] in "RC"
        try:
            mtime = datetime.fromtimestamp((root / entry[3:]).stat().st_mtime, timezone.utc).astimezone()
        except OSError:
            continue
        seen(entry[3:], mtime)
    return times, tracked


def _abandoned(conn: Connection | None, labels: dict[str, str], root: Path | None = None,
               team: people.TeamRead | None = None, lang: str = "zh") -> list[dict[str, Any]]:
    """Every abandoned route with its words (12.0: "which routes were
    abandoned and why"): the result and the reason -- from the decision
    that recorded it, else from the table's own 「放弃理由：…」 -- in the
    table's order; that `decision` as 研究路线 shows it (who proposed and
    who approved, or, with no proposal, who abandoned it directly: 14.3)."""
    from researchos import nodepage, proposals  # noqa: PLC0415
    from researchos.ingest import attempts as attempts_ingest  # noqa: PLC0415

    if conn is None:
        return []
    markers = proposals.abandon_markers(root) if root is not None else [proposals.DEFAULT_ABANDON_MARKER]
    decided: dict[str, dict[str, Any]] = {}
    if root is not None:
        for entry in nodepage._decisions(root)[0]:
            if entry.get("act") == "abandoned" and isinstance(entry.get("attempt"), str) \
                    and str(entry.get("reason") or "").strip():
                decided[entry["attempt"]] = entry
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
            entry = decided.get(node["id"])
            out.append({"id": node["id"], "number": attrs.get("number"), "title": attrs.get("description"),
                        "verdict_status": status, "result": result,
                        "reason": str(entry["reason"]).strip() if entry else reason,
                        "decision": nodepage._decision_payload(entry, team, labels, lang) if entry else None})
    return out


# -- the route map (DESIGN.md Section 14) --------------------------------------------------------

#: A verdict's colour word on the map, by its status (desk.js's VERDICT_TONES); any other is "plain".
TONES = {"current": "olive", "abandoned": "gray", "pending": "ochre", "adverse": "clay"}
#: How long the desk waits for the shared folder before the map comes back without it (14.4).
SHARED_TIMEOUT = 4.0
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
    "stale": ("公盘现在读得太慢：提议和同步显示的是上次读到的情况",
              "The shared folder is slow to read right now: proposals and sync are shown as last read"),
}
_JOBS: dict[str, dict[str, Any]] = {}
#: The last read of each key that finished well, kept when a later one is slow (14.4): (when, result).
_LAST: dict[str, tuple[datetime, Any]] = {}
_JOBS_LOCK = threading.Lock()


def _start(key: str, fn: Callable[[], Any], keep: Callable[[Any], bool] = lambda result: True) -> dict[str, Any]:
    """`fn` on a thread of its own -- one per `key` at a time: a request
    that finds one running waits on it, so a folder that hangs holds one
    thread, not one per visit to the desk. A read that finishes -- even
    after its request stopped waiting -- is kept as the key's last
    (`_LAST`) when `keep` says it read well, errors never: a folder always
    slower than the wait still shows what it last said."""
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
                        if job["result"][0] == "ok" and keep(job["result"][1]):
                            _LAST[key] = (datetime.now(timezone.utc), job["result"][1])
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
    out, tracked = _git_times(root)
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
    key = f"{root.resolve()}|{role}|{me}"
    job = _start(key, lambda: _read_shared(root, role, seen), keep=lambda got: "listing" in got) \
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
    abandoned = {a["id"]: a for a in _abandoned(conn, labels, root, team_known, lang)}
    attempts = []
    for n in nodes:
        attrs, human = n.get("attrs") or {}, n.get("human_fields") or {}
        status = attempts_ingest.route_status(human.get("verdict", ""), labels, markers)
        gone = abandoned.get(n["id"])
        changed = sorted(((times[i], i) for i in (n["id"], *members.get(n["id"], {})) if i in times), reverse=True)
        attempts.append({
            "id": n["id"], "number": str(attrs.get("number") or ""),
            "title": attrs.get("description") or n.get("title") or "", "date": str(attrs.get("date") or ""),
            "date_key": _date_key(attrs.get("date"), config.date_year if config is not None else None),
            "route_status": status, "tone": TONES.get(status["status"], "plain"), "abandoned": gone is not None,
            "result": gone["result"] if gone else str(human.get("result") or ""),
            "reason": gone["reason"] if gone else None, "decision": gone["decision"] if gone else None,
            "changed_at": _iso(changed[0][0]) if changed else None,
            "changed": [{"node": i, "path": notes_mod.node_path(i), "at": _iso(when)} for when, i in changed[:CHANGED_LISTED]],
            "changed_more": max(0, len(changed) - CHANGED_LISTED),
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
        last = _LAST.get(key) if state == "slow" else None
        if last is not None:  # still reading: what it last read, said as such (a decided proposal refuses its act)
            state, shared = "ok", last[1]
        errors = (shared or {}).get("errors", {}) if state == "ok" else {"listing": shared}
        listing = (shared or {}).get("listing") if state == "ok" else None
        if last is not None:
            column["shared"].update(state="stale", at=_iso(last[0].astimezone()))
        elif state == "slow":
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
            last = column["sync"]["last_snapshot"]
            approved = proposals.approval_note(last.get("note")) if isinstance(last, dict) else None
            if approved:  # published on approving a proposal: said by its code, never Research OS's Chinese note (13.6)
                column["sync"]["last_snapshot"] = {**last, "note_code": "approved", "proposal": approved}
        for p in sorted((listing or {}).get("proposals") or [], key=lambda p: str(p.get("at") or ""), reverse=True):
            p = dict(p, by_label=_who(p.get("by"), team_known, lang=lang), touches=_touches(p, table, holders, role))
            returned = [o for o in p.get("outcomes") or [] if o.get("act") == "returned"]
            p["reason"] = returned[-1].get("text") if p.get("state") == "returned" and returned else None
            p["awaiting_sync"] = bool(role == "member" and seen is not None and seen.awaiting_sync(p["id"]))
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
    # 14.4: a member's proposal waits while pending, returned (its reason stays) or approved until synced.
    listed = [p for p in column["mine"] if p["state"] in ("pending", "conflict", "returned") or p["awaiting_sync"]]
    column["empty"] = not (column["waiting"] or listed or review["count"] or missing
                           or (column["sync"] or {}).get("behind"))
    return {"role": role, "attempts": attempts, "lines": lines, "lines_missing": missing,
            "lines_problem": lines_problem, "column": column}


def payload(conn: Connection, project_root: str | Path, *, lang: str = "zh",
            timeout: float | None = None) -> dict[str, Any]:
    """`GET /api/desk` (DESIGN.md 14.1, 14.3-14.5), the route map:

    - **attempts** -- the cards, in the map's order (`order`: by the date
      where `date_year` makes it readable -- `date_key` -- then by the
      table's number): number, title, the date as written, `route_status`
      and its `tone`, `abandoned` with the `reason` and the `decision`
      that recorded it (who abandoned it: 14.3), `changed_at` (the newest
      change of its row or of any of its files, 14.3's membership rule;
      the page compares it with its own last visit for 「有改动」) and
      `changed`, what changed, newest first -- the row (`node` the
      attempt, `path` None) or a file -- for the panel's timeline (at most
      CHANGED_LISTED; `changed_more` counts the earlier ones left out),
      and `marks`: the proposals touching it (等你批准 for the PI, 提议中
      for its author) and its 待复核 count;
    - **lines** -- the 「源自」 lines, child and parent, note, who drew them
      and when, `pending` when a proposal adds or removes one (a member's
      own proposed line is drawn, marked); **lines_missing** -- the lines
      with an end the map does not have;
    - **column** -- the role (from the team file, never the shared folder),
      the PI's `waiting` proposals of every kind or a member's own (`mine`,
      each with the attempts it `touches`, a returned one's `reason`, and
      `awaiting_sync`: approved, something it carries not synced yet),
      待复核 grouped by attempt (`review`), a member's `sync` state, and
      `shared`: whether the shared folder could be read -- the map never
      waits on it longer than SHARED_TIMEOUT.

    Every attempt, line and review group carries `officialness` (12.12);
    every sentence composed here is in `lang` (13.6)."""
    from researchos import officialness  # noqa: PLC0415

    root = Path(project_root)
    try:
        seen = officialness.view(root, pending=False)
    except Exception:  # noqa: BLE001 -- contained, as every view's officialness is
        logger.exception("officialness not added to the desk")
        seen = None
    out = route_map(conn, root, lang=lang, seen=seen, timeout=timeout)
    if seen is not None:
        try:
            officialness.annotate_desk(seen, out, lang)
        except Exception:  # noqa: BLE001
            logger.exception("officialness not added to the desk")
    return out
