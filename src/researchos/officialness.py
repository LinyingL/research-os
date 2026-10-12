"""A member's own work (DESIGN.md 12.12, "A member's own work"; task V8 U1).

In a member's working copy, what differs from the official snapshot it was
last synced to is that member's own work, not part of the official record.
Every view marks it -- 「我的改动，尚未提交」, 「提议中」 when a pending
proposal of theirs carries exactly it, or 「已批准，待同步」 (12.13) when the
PI approved a proposal that carries exactly it and the member has not
synced since -- and it never appears as official.
Nothing is dropped: a member's new script is still a node of their index,
shown with its state.

`view(root)` reads, once per request:

- **the base** (`researchos.proposals.read_base`): the manifest of the
  snapshot the copy was made from or last synced to -- the last verified
  official version this machine knows;
- **the copy as it is now**: every file's fingerprint (the share module's
  cached walk, `researchos.share.official_files`: a file whose size,
  modification time and inode did not change is not read again);
- **the member's pending proposals** in the shared folder (their own area
  only), with what each carries.

and answers, for a file, an attempt-table row, a variable card or a node
id: `official` (as in the base), `mine` (differs from it: added, changed
or removed), `proposed` (differs, and a pending proposal carries exactly
this) or `approved` (differs, and an approved proposal carries exactly
this: the next sync brings it in as official). The official version itself -- the PI's project, or one person's --
is `official` throughout and nothing is read.

`annotate(view, payload)` adds the field `officialness` to every object of
a payload that names a node (`id`, `attempt_id`, `target`, `node`,
`section`), a file of the project (`path`, `file`) or a hand-drawn link (`src`/`dst`/`type` with the
`mapping` extractor), and `officialness_mode` at the top. Reads only.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from researchos import settings

OFFICIAL, MINE, PROPOSED, APPROVED = "official", "mine", "proposed", "approved"
LABELS = {MINE: "我的改动，尚未提交", PROPOSED: "提议中", APPROVED: "已批准，待同步"}
#: The same, on an English screen (13.4).
LABELS_EN = {MINE: "My change, not yet proposed", PROPOSED: "Proposed", APPROVED: "Approved, awaiting sync"}
FIELD = "officialness"
MODE_FIELD = "officialness_mode"
FILE_PREFIXES = frozenset({"script", "dataset", "figure", "file"})
_NODE_KEYS = ("id", "attempt_id", "target", "node", "section")
_PATH_KEYS = ("path", "file")
#: The cells of an attempt-table row (`researchos.proposals.ROW_FIELDS`).
ROW_CELLS = ("date", "description", "variables", "result", "verdict")


@dataclass
class View:
    """What one request knows about a working copy (module docstring).
    `mode`: `official` (nothing differs by definition) or `working_copy`."""

    mode: str = OFFICIAL
    root: Path | None = None
    seq: int | None = None
    base: dict[str, Mapping[str, Any]] = field(default_factory=dict)
    current: dict[str, str | None] = field(default_factory=dict)
    table: str | None = None
    base_rows: dict[str, dict[str, str]] = field(default_factory=dict)
    rows: dict[str, dict[str, str]] = field(default_factory=dict)
    base_links: set[tuple[str, str, str]] = field(default_factory=set)
    links: set[tuple[str, str, str]] = field(default_factory=set)
    proposed_files: dict[str, tuple[str | None, str]] = field(default_factory=dict)
    proposed_rows: dict[str, tuple[dict[str, str], str]] = field(default_factory=dict)
    proposed_cards: dict[str, tuple[str, str, str]] = field(default_factory=dict)
    #: 「源自」 lines (14.2) a `route_link` -- or a new attempt's `parents` --
    #: carries: (child, parent) as `<table>#<n>` -> (op, proposal).
    proposed_lines: dict[tuple[str, str], tuple[str, str]] = field(default_factory=dict)
    #: The state of each proposal named above: "pending" or "approved".
    proposal_states: dict[str, str] = field(default_factory=dict)
    pending_known: bool = True
    problems: list[str] = field(default_factory=list)

    # -- answers ------------------------------------------------------------------------

    def _sha(self, rel: str, where: Mapping[str, Any]) -> str | None:
        got = where.get(rel)
        return got.get("sha256") if isinstance(got, Mapping) else got

    def _carried_state(self, proposal: str) -> str:
        return APPROVED if self.proposal_states.get(proposal) == "approved" else PROPOSED

    def file_state(self, rel: str | None) -> tuple[str, str | None]:
        """(state, proposal id) of a project-relative file."""
        if self.mode == OFFICIAL or not rel:
            return OFFICIAL, None
        now, then = self.current.get(rel), self._sha(rel, self.base)
        if now == then:
            return OFFICIAL, None
        carried = self.proposed_files.get(rel)
        if carried is not None and carried[0] == now:
            return self._carried_state(carried[1]), carried[1]
        return MINE, None

    def attempt_state(self, table: str, number: str) -> tuple[str, str | None]:
        if self.mode == OFFICIAL or table != self.table:
            return OFFICIAL, None
        now, then = self.rows.get(number), self.base_rows.get(number)
        if now == then:
            return OFFICIAL, None
        carried = self.proposed_rows.get(number)
        if carried is not None and now is not None and self._row_carried(now, then or {}, carried[0]):
            return self._carried_state(carried[1]), carried[1]
        return MINE, None

    @staticmethod
    def _row_carried(now: Mapping[str, str], then: Mapping[str, str], carried: Mapping[str, Any]) -> bool:
        """The row as it is now is exactly what the proposal carries: every
        cell it carries as carried (a verdict change's result may also be
        left as it was), every other cell as in the base -- a later edit of
        a cell no proposal carries is the member's own, unsubmitted."""
        for k in ROW_CELLS:
            if k in carried:
                if now.get(k) != carried[k] and not (k == "result" and now.get(k) == then.get(k)):
                    return False
            elif now.get(k) != then.get(k):
                return False
        return True

    def card_state(self, card: str) -> tuple[str, str | None]:
        if self.mode == OFFICIAL:
            return OFFICIAL, None
        prefix = f".researchos/variables/{card}/"
        rels = {r for r in (*self.base, *self.current) if r.startswith(prefix) and _is_version(r[len(prefix):])}
        changed = sorted(r for r in rels if self.current.get(r) != self._sha(r, self.base))
        if not changed:
            return OFFICIAL, None
        carried = self.proposed_cards.get(card)
        if carried is not None and changed == [carried[0]] and self.current.get(carried[0]) == carried[1]:
            return self._carried_state(carried[2]), carried[2]
        return MINE, None

    def line_state(self, child: str, parent: str) -> tuple[str, str | None]:
        """A 「源自」 line a member's proposal adds or removes: 提议中 (or
        已批准，待同步) until the official version they synced to says so."""
        carried = self.proposed_lines.get((child, parent))
        if self.mode == OFFICIAL or carried is None:
            return OFFICIAL, None
        if ((child, parent, "derives_from") in self.base_links) == (carried[0] == "add"):
            return OFFICIAL, None
        return self._carried_state(carried[1]), carried[1]

    def link_state(self, from_: str, to: str, kind: str) -> tuple[str, str | None]:
        if self.mode == OFFICIAL:
            return OFFICIAL, None
        if kind == "derives_from" and (from_, to) in self.proposed_lines:
            return self.line_state(from_, to)
        key = (from_, to, kind)
        return (OFFICIAL, None) if key in self.base_links or key not in self.links else (MINE, None)

    def node_state(self, node: str) -> tuple[str, str | None]:
        prefix, _sep, rest = node.partition(":")
        if self.mode == OFFICIAL or not rest:
            return OFFICIAL, None
        if prefix in FILE_PREFIXES:
            return self.file_state(rest)
        if prefix == "attempt":
            table, _hash, number = rest.rpartition("#")
            return self.attempt_state(table, number)
        if prefix == "variable":
            return self.card_state(rest)
        # A section or claim of a draft: as its draft file is.
        rel = rest.split("#", 1)[0]
        if rel in self.base or rel in self.current:
            return self.file_state(rel)
        return OFFICIAL, None

    def mode_payload(self, lang: str = "zh") -> dict[str, Any]:
        return {"mode": self.mode, "seq": self.seq, "pending_known": self.pending_known,
                "labels": {state: label(state, lang) for state in LABELS}, "problems": list(self.problems)}


def label(state: str, lang: str = "zh") -> str:
    """A state's words in the request's language (13.6)."""
    return settings.say(LABELS[state], LABELS_EN[state], lang)


def mark(state: tuple[str, str | None], lang: str = "zh") -> dict[str, Any]:
    """The field as a payload carries it, its label in `lang`."""
    out: dict[str, Any] = {"state": state[0]}
    if state[0] in LABELS:
        out["label"] = label(state[0], lang)
    if state[1]:
        out["proposal"] = state[1]
    return out


def _is_version(name: str) -> bool:
    return name.startswith("v") and name.endswith(".toml") and name[1:-5].isdigit()


# -- reading ----------------------------------------------------------------------------------


def _project_id(root: Path) -> str | None:
    from researchos.records.identity import IdentityState, read_identity  # noqa: PLC0415

    got = read_identity(root)
    return got.identity.id if got.state is IdentityState.PRESENT and got.identity is not None else None


def view(project_root: str | Path, *, pending: bool = True) -> View:
    """The officialness of `project_root` as this machine knows it now
    (module docstring). Never raises for what it cannot read: a part that
    cannot be read is said in `problems` and the rest still answers.
    `pending=False` leaves out the one read of the shared folder (a caller
    that bounds it in time reads it with `pending_of`)."""
    from researchos import proposals, share  # noqa: PLC0415

    root = Path(project_root)
    pid = _project_id(root)
    if pid is None:
        return View(root=root)
    try:
        base = proposals.read_base(pid)
    except proposals.ProposalRefused:
        return View(root=root, problems=["这个工作副本所依据的正式版本无法读取"])
    if base is None or Path(base.working_copy) != root.resolve():
        return View(root=root)
    out = View(mode="working_copy", root=root, base=dict(base.files))
    try:
        state = share.read_state(pid)
        if state and Path(str(state.get("working_copy", ""))) == root.resolve():
            out.seq = state.get("seq")
    except share.ShareError:
        pass
    try:
        files, dataless = share.official_files(root, pid)
    except OSError as exc:
        out.problems.append(f"工作副本里的文件无法读取：{exc}")
        files, dataless = {}, []
    out.current = {rel: e["sha256"] for rel, e in files.items()}
    for rel in dataless:  # in the cloud: not read, so not told apart from the base
        out.current[rel] = out._sha(rel, out.base)
    _attempt_rows(root, base, out)
    _links(root, base, out)
    if pending:
        _pending(root, pid, out)
    return out


def pending_of(v: View) -> View:
    """What `view` reads from the shared folder for a working copy -- the
    member's pending and approved proposals -- into a View of its own, which
    `with_pending` merges (the desk reads it on a thread it waits for only
    so long, 14.4)."""
    got = View(mode=v.mode, root=v.root, table=v.table)
    pid = _project_id(v.root) if v.mode != OFFICIAL and v.root is not None else None
    if pid is not None:
        _pending(v.root, pid, got)
    return got


def with_pending(v: View, got: View | None) -> View:
    """`v` with what `pending_of` read; without it (the shared folder could
    not be read in time), the pending proposals are not known, said so."""
    if v.mode == OFFICIAL:
        return v
    if got is None:
        v.pending_known = False
        return v
    for name in ("proposed_files", "proposed_rows", "proposed_cards", "proposed_lines", "proposal_states"):
        setattr(v, name, getattr(got, name))
    v.pending_known = got.pending_known
    return v


def _attempt_rows(root: Path, base, out: View) -> None:
    from researchos import proposals  # noqa: PLC0415

    config = proposals._attempts_config(root)
    if config is None:
        return
    out.table = config.file
    out.base_rows = proposals._rows(base.text(config.file), config) or {}
    entry = out.current.get(config.file)
    text = proposals._text_of(root / config.file, (root / config.file).stat().st_size) if entry else None
    out.rows = proposals._rows(text, config) or {}


def _links(root: Path, base, out: View) -> None:
    from researchos.ingest import mappings as mappings_ingest  # noqa: PLC0415

    rel = mappings_ingest.MAPPINGS_RELATIVE_PATH
    try:
        mine = (root / rel).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        mine = None
    out.base_links = {k for k in mappings_ingest._entries_by_key(root, base.text(rel))}
    out.links = {k for k in mappings_ingest._entries_by_key(root, mine)}


def _pending(root: Path, pid: str, out: View) -> None:
    """What the member's pending proposals carry (their own area only) --
    and their approved ones (12.13: 「已批准，待同步」 until the member's
    next sync makes it official); a pending one wins where both carry the
    same thing."""
    from researchos import exchange as exchange_mod  # noqa: PLC0415
    from researchos import people, proposals  # noqa: PLC0415

    try:
        me = people.current_member_id()
        if me is None:
            return
        ex = exchange_mod.for_project(pid, root)
        found = ex.list_proposals(me).proposals
    except Exception:  # noqa: BLE001 -- no shared folder now: pending proposals are not known, said so
        out.pending_known = False
        return
    carrying = []
    for stored in found:
        if stored.data.get("project") != pid:
            continue
        try:
            state = proposals.state_of(ex, stored, found)["state"]
        except Exception:  # noqa: BLE001
            continue
        if state in ("pending", "approved"):
            carrying.append((0 if state == "approved" else 1, str(stored.data.get("at") or ""), state, stored))
    for _order, _at, state, stored in sorted(carrying, key=lambda c: c[:2]):
        data = stored.data
        out.proposal_states[stored.id] = state
        for item in data.get("file") or []:
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                out.proposed_files[item["path"]] = (item.get("sha256") if item.get("action") != "removed" else None,
                                                    stored.id)
        attempt, verdict, variable = data.get("attempt"), data.get("verdict"), data.get("variable")
        if isinstance(attempt, dict) and isinstance(attempt.get("number"), str):
            out.proposed_rows[attempt["number"]] = ({k: attempt.get(k) for k in proposals.ROW_FIELDS}, stored.id)
            for parent in attempt.get("parents") or []:
                out.proposed_lines[(f"{out.table}#{attempt['number']}", str(parent).removeprefix("attempt:"))] = \
                    ("add", stored.id)
        route = data.get("route")
        if isinstance(route, dict) and route.get("op") in proposals.ROUTE_OPS:
            ends = tuple(str(route.get(k, "")).removeprefix("attempt:") for k in ("child", "parent"))
            out.proposed_lines[ends] = (route["op"], stored.id)
        if isinstance(verdict, dict) and isinstance(verdict.get("number"), str):
            out.proposed_rows[verdict["number"]] = ({"verdict": verdict.get("new"), "result": verdict.get("result")},
                                                    stored.id)
        if isinstance(variable, dict) and isinstance(variable.get("card"), str):
            out.proposed_cards[variable["card"]] = (str(variable.get("file")), str(variable.get("sha256")), stored.id)


def proposed_lines(v: View, lang: str = "zh") -> list[dict[str, Any]]:
    """The 「源自」 lines a member's map draws from their own proposals
    (14.2): each being added or removed, marked 提议中 or 已批准，待同步 in
    `lang`. A returned proposal's line is not among them: it disappears."""
    out = []
    for (child, parent), (op, _proposal) in sorted(v.proposed_lines.items()):
        state = v.line_state(child, parent)
        if state[0] != OFFICIAL:
            out.append({"src": "attempt:" + child, "dst": "attempt:" + parent, "type": "derives_from", "op": op,
                        FIELD: mark(state, lang)})
    return out


# -- payloads ---------------------------------------------------------------------------------


def _relative(value: Any) -> str | None:
    if not isinstance(value, str) or not value or value.startswith(("/", "~")) or "\x00" in value:
        return None
    norm = posixpath.normpath(value)
    return None if norm.startswith("..") or norm == "." else norm


def _subject(v: View, item: Mapping[str, Any]) -> tuple[str, str | None] | None:
    if isinstance(item.get("src"), str) and isinstance(item.get("dst"), str) and isinstance(item.get("type"), str):
        if item.get("extractor") != "mapping":
            return None
        src, dst, kind = item["src"], item["dst"], item["type"]
        # The file writes a link in the direction it was drawn (`reads` reversed).
        a, b = (dst, src) if kind == "reads" else (src, dst)
        return v.link_state(a.partition(":")[2], b.partition(":")[2], kind)
    for key in _NODE_KEYS:
        value = item.get(key)
        if isinstance(value, str) and ":" in value and value.partition(":")[0].isidentifier():
            return v.node_state(value)
    for key in _PATH_KEYS:
        rel = _relative(item.get(key))
        # Only a file of the project (on disk, or in the official version):
        # `file` and `path` also name things that are not (a library PDF).
        if rel is not None and (rel in v.base or rel in v.current or (v.root is not None and _exists(v.root, rel))):
            return v.file_state(rel)
    return None


def _exists(root: Path, rel: str) -> bool:
    try:
        target = root / rel
        return target.is_file() and target.resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


def annotate(v: View, payload: Any, lang: str = "zh") -> Any:
    """`payload` with `officialness` on every object that names a node, a
    file or a hand-drawn link, and `officialness_mode` at the top (when it
    is an object), the labels in `lang` -- officialness states follow the
    interface language on every page (13.7). Changes `payload` in place and
    returns it."""

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if FIELD not in value:
                got = _subject(v, value)
                if got is not None:
                    value[FIELD] = mark(got, lang)
            for child in list(value.values()):
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    if isinstance(payload, dict):
        payload[MODE_FIELD] = v.mode_payload(lang)
    return payload


def annotate_desk(v: View, payload: Any, lang: str = "zh") -> Any:
    """The desk's payload (`researchos.desk`): a file's or an attempt's
    change, a result and an abandoned route carry the state of what they
    name; a decision (official by definition: the PI's approval) and a
    note (it travels without approval, 12.4) do not take the state of the
    node they are about. On the route map (14.1) every attempt, every
    「源自」 line (one a proposal carries keeps the mark it came with) and
    every 待复核 group's attempt carries its state. The labels in the page's
    language (13.7: the desk entirely)."""
    if isinstance(payload, dict):
        for item in payload.get("changes") or []:
            if isinstance(item, dict) and item.get("kind") in ("file", "attempt"):
                got = _subject(v, item)
                if got is not None:
                    item[FIELD] = mark(got, lang)
        review = (payload.get("column") or {}).get("review") or {}
        for items in (payload.get("results"), payload.get("abandoned"), payload.get("attempts"), review.get("groups")):
            for item in items or []:
                if isinstance(item, dict):
                    got = _subject(v, item)
                    if got is not None:
                        item[FIELD] = mark(got, lang)
        for key in ("lines", "lines_missing"):
            for item in payload.get(key) or []:
                if isinstance(item, dict) and FIELD not in item:
                    ends = [str(item.get(k, "")).partition(":")[2] for k in ("child", "parent")]
                    item[FIELD] = mark(v.link_state(ends[0], ends[1], "derives_from"), lang)
        payload[MODE_FIELD] = v.mode_payload(lang)
    return payload


def annotate_cards(v: View, payload: Any, lang: str = "zh") -> Any:
    """The 「变量」 view's payloads: a card is named by its id, not a node
    id -- each card row (and a single card) carries its card's state, its
    label in `lang`."""
    if isinstance(payload, dict):
        rows = payload.get("cards")
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and isinstance(row.get("id"), str):
                    row[FIELD] = mark(v.card_state(row["id"]), lang)
        elif isinstance(payload.get("id"), str) and ":" not in payload["id"]:
            payload[FIELD] = mark(v.card_state(payload["id"]), lang)
    return annotate(v, payload, lang)
