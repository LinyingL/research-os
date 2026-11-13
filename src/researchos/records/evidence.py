"""Passages of a `.tex` draft tied to their supporting files: `.researchos/evidence.toml` (DESIGN.md 22.2, task V17).

An append-only ledger on the engine of `researchos.records.ledger`, keyed by `link` (made at the first entry and kept
through every later one)::

    # 正文段落和它的依据文件（人工声明）。Research OS 只追加、不改写；重新定位、撤回关联、撤销也是追加一条记录。
    [[passage_evidence]]
    id    = "pe-…"
    seq   = 1
    at    = "2026-11-13T10:02:11+01:00"
    by    = "li@example.com"
    act   = "link"                 # reanchor | withdraw | undone
    link  = "pl-…"
    draft = "paper/main.tex"
    file  = "figure:figures/f1.png"
    note  = "…"
    via   = "app"

    [passage_evidence.anchor]
    quote  = "…"                   # the passage's source text, every line ending written as \\n
    prefix = "…"                   # up to 32 characters before it, and after it
    suffix = "…"
    offset = 1234                  # code points into the \\n-normalised text: a hint, never authority

`reanchor` (「是这段」, 「仍然成立」) carries a new anchor; `withdraw` is 撤回关联; `undone` takes back the link's last act,
the engine's rule. A link stands while its current act is `link` or `reanchor`.

**Trust** is 9.3's, as for `papers.toml` (no index copy): a file missing though a snapshot of it exists, in the cloud,
unreadable, invalid, or with a sync conflict copy beside it refuses every write, and readers list no link and say why.

**Who writes** (22.5): the PI and a single researcher, in the official version; a member, in their own working copy
only. A member's entries are 待批准 there (`unapproved`: theirs, and not in the official version they synced to) and
back nothing until approved: at 同步 they go, with the revision of each draft, into one proposal of the kind
`passage_evidence` (`researchos.share`). Approving appends them to the official ledger with their own ids and
`approved_by` (`settle`); returning appends them followed by the PI's acts that undo their effect -- a `withdraw` of a
link they made -- so either way every entry of the member's is held by the official ledger, and their copy is replaced
by it at the next sync with nothing left beside it.

`resolve` finds a passage again in the draft's `\\n`-normalised text (22.2). It is a pure function: reading never
writes the ledger, and nothing here ever writes the `.tex`.
"""

from __future__ import annotations

import bisect
import os
import re
import secrets
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from researchos import paths, settings
from researchos.records import files as record_files
from researchos.records import ledger as ledger_mod
from researchos.records import notes, papers
from researchos.records.files import RecordState
from researchos.records.identity import IdentityState, read_identity
from researchos.records.ledger import LedgerLoad, LedgerSchema, LedgerWriteRefused
from researchos.records.situation import READ_NOW, write_guard
from researchos.records.trust import TrustDecision, assess_ledger

EVIDENCE_FILENAME = "evidence.toml"
LINK, REANCHOR, WITHDRAW, UNDONE = "link", "reanchor", "withdraw", "undone"
ACTS = frozenset({LINK, REANCHOR, WITHDRAW, UNDONE})
VIAS = frozenset({"app", "cli", "recovered"})
CONTEXT = 32
MAX_QUOTE = 20_000
MAX_NOTE = 2_000
CANDIDATES = 3
_LINK_RE = re.compile(r"pl-[A-Za-z0-9_-]{1,64}\Z")
_HEADER = "# 正文段落和它的依据文件（人工声明）。Research OS 只追加、不改写；重新定位、撤回关联、撤销也是追加一条记录。\n"

MESSAGES = {
    "no_identity": ("项目身份文件无法读取", "The project's identity file cannot be read"),
    "migrating": ("项目正在迁移，迁移完成前不能写入你的记录", "The project is being migrated; no record is written until it ends"),
    "missing": ("依据文件记录（evidence.toml）当前无法读取，请先恢复它",
                "The record of supporting files (evidence.toml) cannot be read now; restore it first"),
    "dataless": ("记录文件正在从云端下载…", "The record file is downloading from the cloud…"),
    "invalid": ("依据文件记录（evidence.toml）格式不对，请先修复它",
                "The record of supporting files (evidence.toml) is not in the right form; fix it first"),
    "conflict_copy": ("依据文件记录旁有同步冲突副本，请先处理",
                      "A sync conflict copy sits beside the record of supporting files; deal with it first"),
    "member": ("成员只能在自己的工作副本里关联依据文件，PI 批准后才进入正式版本",
               "A member links supporting files in their own working copy only; they reach the official version "
               "when the PI approves them"),
    "not_in_team": ("你还不是这个项目的成员，不能关联依据文件",
                    "You are not in this project's team yet, so you cannot link a supporting file"),
    "team_unreadable": ("成员名单（team.toml）当前无法读取，无法判断谁是 PI",
                        "The member list (team.toml) cannot be read now, so who the PI is cannot be told"),
    "no_such_file": ("项目里找不到这个文件", "There is no such file in the project"),
    "no_such_link": ("找不到这条关联", "This link cannot be found"),
    "already_linked": ("这段已经关联了这个文件", "This passage is already linked to this file"),
    "withdrawn": ("这条关联已经撤回", "This link has been withdrawn"),
    "invalid_request": ("无法识别这次关联", "This link cannot be read"),
    "not_written": ("关联没有写入", "The link was not written"),
    "note_too_long": (f"备注最多 {MAX_NOTE} 个字符", f"A note is at most {MAX_NOTE:,} characters"),
    "line_separator": ("这段文字里有分页符或特殊的换行字符，无法记录；请选一段不含它们的文字，或先在正文里删掉它们",
                       "This passage contains a page break or a special line separator, which cannot be recorded; "
                       "select text without one, or remove it from the draft first"),
}
#: The characters besides \n that the ledger engine refuses in every string (`webapp.mapedit`'s line boundaries).
LINE_SEPARATORS = "\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"
_ROLE_REFUSAL = {"member": "member", "none": "not_in_team", "unknown": "team_unreadable"}
_STATUS = {"member": 403, "not_in_team": 403, "team_unreadable": 409, "untrusted": 409, "no_such_file": 404,
           "no_such_link": 404, "withdrawn": 409, "already_linked": 409, "not_written": 409}


class EvidenceRefused(Exception):
    """Nothing was written. `code` a key of `MESSAGES` (`untrusted` uses the trust reason's sentence)."""

    def __init__(self, code: str, message: str, words: tuple[str, str] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.words = words or MESSAGES.get(code, MESSAGES["invalid_request"])
        self.status = _STATUS.get(code, 400)

    def say(self, lang: str) -> str:
        return settings.say(*self.words, lang)


def clean_tex_draft(value: Any) -> str:
    """A draft as the record names it: a path `papers.clean_draft` accepts as written, no hidden part, `.tex`."""
    rel = papers.clean_draft(value)
    if rel != value or any(part.startswith(".") for part in rel.split("/")) or not rel.endswith(".tex"):
        raise ValueError(f"{value!r} is not a .tex draft of the project")
    return rel


def _validate_anchor(anchor: Any) -> None:
    if not isinstance(anchor, Mapping):
        raise ValueError("a link needs its passage, as [passage_evidence.anchor]")
    quote, offset = anchor.get("quote"), anchor.get("offset")
    if not isinstance(quote, str) or not quote.strip() or len(quote) > MAX_QUOTE:
        raise ValueError(f"the anchor's 'quote' is the passage's text, 1 to {MAX_QUOTE} characters")
    for name in ("prefix", "suffix"):
        value = anchor.get(name)
        if not isinstance(value, str) or len(value) > CONTEXT:
            raise ValueError(f"the anchor's {name!r} is at most {CONTEXT} characters of text")
    if any("\r" in anchor[name] for name in ("quote", "prefix", "suffix")):
        raise ValueError("an anchor's line endings are written as \\n")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError(f"the anchor's 'offset' is a count of characters, got {offset!r}")


def _validate(entry: Mapping[str, Any]) -> None:
    link, act = entry.get("link"), entry.get("act")
    if not isinstance(link, str) or not _LINK_RE.match(link):
        raise ValueError(f"'link' names a link as pl-…, got {link!r}")
    if act == LINK:
        clean_tex_draft(entry.get("draft"))
        file = entry.get("file")
        if not isinstance(file, str) or notes.normalize_node(file) != file:
            raise ValueError(f"'file' names the supporting file as a node id, got {file!r}")
        note = entry.get("note")
        if note is not None and (not isinstance(note, str) or len(note) > MAX_NOTE):
            raise ValueError(f"'note' is at most {MAX_NOTE} characters")
    if act in (LINK, REANCHOR):
        _validate_anchor(entry.get("anchor"))
    approved_by = entry.get("approved_by")
    if approved_by is not None and (not isinstance(approved_by, str) or "@" not in approved_by):
        raise ValueError(f"'approved_by' names the PI who approved a member's act, got {approved_by!r}")
    via = entry.get("via")
    if via is not None and via not in VIAS:
        raise ValueError(f"'via' must be one of {', '.join(sorted(VIAS))}, got {via!r}")


def _validate_new(entry: Mapping[str, Any]) -> None:
    if entry.get("via") not in VIAS:
        raise ValueError(f"'via' must be one of {', '.join(sorted(VIAS))}, got {entry.get('via')!r}")


SCHEMA = LedgerSchema(
    table="passage_evidence",
    id_prefix="pe-",
    act_field="act",
    acts=ACTS,
    undo_act=UNDONE,
    key_fields=("link",),
    field_order=("id", "seq", "at", "by", "approved_by", "act", "link", "draft", "file", "note", "via", "undoes"),
    header=_HEADER,
    validate=_validate,
    validate_new=_validate_new,
    multiline=frozenset({f"passage_evidence.anchor.{name}" for name in ("quote", "prefix", "suffix")}),
)


def evidence_path(project_root: str | Path) -> Path:
    return paths.project_dir(project_root) / EVIDENCE_FILENAME


def load(project_root: str | Path) -> LedgerLoad:
    """The ledger, plus its one rule across entries: a link's first entry is its `link` act, and it has only one."""
    loaded = ledger_mod.load_ledger(evidence_path(project_root), SCHEMA)
    if loaded.ledger is not None:
        for key in loaded.ledger.keys():
            history = loaded.ledger.history(key)
            made = [e for e in history if e.get("act") == LINK]
            if history[0].get("act") != LINK or len(made) != 1:
                bad = history[0] if history[0].get("act") != LINK else made[1]
                where = f"line {bad.line}: " if bad.line else ""
                return LedgerLoad(RecordState.INVALID, loaded.path, data=loaded.data, line=bad.line,
                                  error=f"{where}link {key[0]!r} must start with exactly one 'link' entry",
                                  conflict_copies=loaded.conflict_copies)
    return loaded


def expected(project_root: str | Path) -> bool:
    return bool(record_files.snapshots(project_root, evidence_path(project_root)))


def assess(project_root: str | Path) -> tuple[LedgerLoad, TrustDecision]:
    root = Path(project_root)
    got = read_identity(root)
    identity = got.identity if got.state is IdentityState.PRESENT else None
    loaded = load(root)
    return loaded, assess_ledger(identity, loaded, {}, expected=expected(root))


def message_of(decision: TrustDecision) -> tuple[str, str] | None:
    return None if decision.reason is None else MESSAGES.get(decision.reason, MESSAGES["missing"])


def links_from(loaded: LedgerLoad, waiting: frozenset[str] | set[str] = frozenset()) -> dict[str, dict[str, Any]]:
    """Every link that stands (its current act `link` or `reanchor`; a key in conflict counts, marked, until a new act
    settles it): `{link, draft, file, note, anchor, entry (its link act), last (the act 撤销 would take back), conflict,
    pending}` -- `pending` (待批准, 22.5) when an act of its history is one of `waiting` (`unapproved`)."""
    if loaded.ledger is None:
        return {}
    ledger, out = loaded.ledger, {}
    for key in ledger.keys():
        state = ledger.state(key)
        live = [e for e in state.history if e.get("act") != UNDONE and not ledger.is_cancelled(e)]
        made = next((e for e in live if e.get("act") == LINK), None)
        if made is None or not live or (state.conflict is None and state.entry is None):
            continue
        current = state.entry if state.conflict is None else live[-1]
        if current.get("act") == WITHDRAW:
            continue
        anchor = next(e for e in reversed(live) if e.get("act") in (LINK, REANCHOR)).get("anchor")
        out[key[0]] = {"link": key[0], "draft": made.get("draft"), "file": made.get("file"), "note": made.get("note"),
                       "anchor": dict(anchor), "entry": made, "last": current.id, "conflict": state.conflict is not None,
                       "pending": any(e.id in waiting for e in state.history)}
    return out


def links_now(project_root: str | Path) -> tuple[dict[str, dict[str, Any]], TrustDecision]:
    """The standing links, or none when the file cannot be trusted now (the decision says why). Reads only."""
    loaded, decision = assess(project_root)
    return (links_from(loaded, unapproved(project_root, loaded)) if decision.may_apply else {}), decision


def _official_ids(project_root: str | Path) -> set[str] | None:
    """The entry ids of the official ledger a member's working copy was synced to (its base), or None when this is no
    member's working copy."""
    from researchos import proposals  # noqa: PLC0415 -- proposals imports the records

    root = Path(project_root)
    try:
        if proposals.role_of(root)[0] != "member":
            return None
        base = proposals.read_base(proposals._project_id(root))  # noqa: SLF001
        if base is None or Path(base.working_copy).resolve() != root.resolve():
            return None
        return ledger_mod.parse_ledger((base.text(f"{paths.PROJECT_DIRNAME}/{EVIDENCE_FILENAME}") or "")
                                       .removeprefix("\ufeff"), SCHEMA).ids
    except (proposals.ProposalRefused, ledger_mod.LedgerInvalidError, OSError, ValueError, TypeError):
        return None


def unapproved(project_root: str | Path, loaded: LedgerLoad) -> set[str]:
    """待批准 (22.5): in a member's working copy, the ids of their own entries the official version does not hold yet.
    Empty anywhere else."""
    from researchos import people  # noqa: PLC0415

    official = _official_ids(project_root) if loaded.ledger is not None else None
    if official is None:
        return set()
    try:
        me = people.current_member_id()
    except people.PeopleError:
        return set()
    return {e.id for e in loaded.ledger.entries if e.get("by") == me and e.id not in official}


# -- finding a passage again (22.2) -------------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[㐀-鿿豈-﫿]|[^\W㐀-鿿豈-﫿]+")
_PARAGRAPH_GAP = re.compile(r"\n[ \t]*\n")


def _tokens(text: str) -> Counter:
    """Words, and each Chinese character on its own (22.2: shared words; for Chinese, shared characters)."""
    return Counter(_TOKEN_RE.findall(text.lower()))


class Paragraphs:
    """The text's paragraph-sized stretches with their tokens, worked out once for all the links of one draft."""

    def __init__(self, text: str) -> None:
        self.text = text
        self._spans: list[tuple[int, int, Counter]] | None = None

    def spans(self) -> list[tuple[int, int, Counter]]:
        if self._spans is None:
            self._spans, start = [], 0
            for gap in [*_PARAGRAPH_GAP.finditer(self.text), None]:
                end = len(self.text) if gap is None else gap.start()
                chunk = self.text[start:end]
                lead = len(chunk) - len(chunk.lstrip())
                body = chunk.strip()
                if body:
                    self._spans.append((start + lead, start + lead + len(body), _tokens(body)))
                start = end if gap is None else gap.end()
        return self._spans


def _occurrences(text: str, quote: str) -> list[int]:
    found, at = [], text.find(quote)
    while at != -1:
        found.append(at)
        at = text.find(quote, at + 1)
    return found


def _between(text: str, prefix: str, suffix: str, offset: int) -> tuple[int, int] | None:
    """The stretch between the anchor's prefix and suffix, when both are still there (an empty prefix is the start of
    the text, an empty suffix its end); of several, the one nearest `offset`. The suffix's places are listed once, so
    a prefix that recurs thousands of times (a generated table) costs a search each, not a scan of the text each."""
    starts = [0] if not prefix else [i + len(prefix) for i in _occurrences(text, prefix)]
    ends = None if not suffix else _occurrences(text, suffix)
    best = None
    for start in starts:
        if ends is None:
            end = len(text)
        else:
            k = bisect.bisect_left(ends, start)
            if k == len(ends):
                continue
            end = ends[k]
        if best is None or abs(start - offset) < abs(best[0] - offset):
            best = (start, end)
    return best


_BLANK = re.compile(r"\s*")


def _deleted(text: str, prefix: str, suffix: str, offset: int) -> tuple[int, int] | None:
    """Where the passage was, when every character of it is gone: the prefix (less its trailing blank space) followed,
    with nothing but blank space between, by the suffix (less its leading blank space), nearest `offset`. Deleting a
    paragraph with one of its blank lines, or a line with its line ending, takes blank space of the context with it."""
    head, tail = prefix.rstrip(), suffix.lstrip()
    if not head and not tail:
        return None
    best = None
    for start in [0] if not head else [i + len(head) for i in _occurrences(text, head)]:
        gap = _BLANK.match(text, start).end()
        if (text.startswith(tail, gap) if tail else gap == len(text)) and (
                best is None or abs(start - offset) < abs(best[0] - offset)):
            best = (start, gap)
    return best


def resolve(anchor: Mapping[str, Any], text: str, paragraphs: Paragraphs | None = None) -> dict[str, Any]:
    """Where the anchor's passage is in `text` (`\\n`-normalised), as `{state, start, end, candidates}`:

    - `found` -- the quote occurs once, or of several the one whose prefix and suffix match (then the nearest
      `offset`);
    - `deleted` -- it does not occur, and nothing but blank space is left between its prefix and suffix, each less
      its blank space next to the passage (「段落已被删掉」);
    - `changed` -- it does not occur (待复核 「依据已变化」), with up to three candidates `{start, end, text}`, best
      first: the text between the same prefix and suffix, then the most similar paragraphs; none longer than a
      passage may be (`MAX_QUOTE`), since 「是这段」 on it would be refused.

    Pure: it reads nothing and writes nothing."""
    quote, prefix, suffix, offset = anchor["quote"], anchor["prefix"], anchor["suffix"], anchor["offset"]
    hits = _occurrences(text, quote)
    if hits:
        fitting = [i for i in hits if text[max(0, i - len(prefix)):i] == prefix
                   and text[i + len(quote):i + len(quote) + len(suffix)] == suffix]
        at = min(fitting or hits, key=lambda i: abs(i - offset))
        return {"state": "found", "start": at, "end": at + len(quote), "candidates": []}
    gone = _deleted(text, prefix, suffix, offset)
    if gone is not None:
        return {"state": "deleted", "start": gone[0], "end": gone[1], "candidates": []}
    candidates: list[dict[str, Any]] = []
    between = _between(text, prefix, suffix, offset) if (prefix or suffix) else None
    if between is not None:
        start, end = between
        stretch = text[start:end]
        lead = len(stretch) - len(stretch.lstrip())
        if len(stretch.strip()) <= MAX_QUOTE:
            candidates.append({"start": start + lead, "end": start + lead + len(stretch.strip()),
                               "text": stretch.strip()})
    wanted = _tokens(quote)
    size = sum(wanted.values())
    scored = []
    for start, end, tokens in (paragraphs or Paragraphs(text)).spans():
        if end - start > MAX_QUOTE:
            continue
        shared = sum((wanted & tokens).values())
        if shared and size:
            scored.append((2 * shared / (size + sum(tokens.values())), -abs(start - offset), start, end))
    for _score, _near, start, end in sorted(scored, reverse=True):
        if len(candidates) >= CANDIDATES:
            break
        if not any(c["start"] == start and c["end"] == end for c in candidates):
            candidates.append({"start": start, "end": end, "text": text[start:end]})
    return {"state": "changed", "start": None, "end": None, "candidates": candidates}


# -- writes ------------------------------------------------------------------------------------------------------


def _may_write(root: Path) -> None:
    from researchos import proposals  # noqa: PLC0415 -- proposals imports the records

    role = proposals.role_of(root)[0]
    if role == "member" and _official_ids(root) is None:
        raise EvidenceRefused("member", "a member links in their own working copy only: nothing written")
    if role not in ("pi", "single", "member"):
        code = _ROLE_REFUSAL.get(role, "team_unreadable")
        raise EvidenceRefused(code, f"{code}: nothing written")


def _file_of_project(root: Path, node: str) -> bool:
    """Whether the file node names a file of the project, every component spelt as its folder lists it (a Mac volume
    ignores case and Unicode normalisation, so `Figures/F1.PNG` would pass as another name of `figures/f1.png`, as
    `latex_edit.checked_path` refuses for drafts). A node that is not a file is not judged here."""
    rel = notes.node_path(node)
    if rel is None:
        return True
    if any(part.startswith(".") for part in rel.split("/")):
        return False
    target = root / rel
    try:
        if not (target.resolve().is_relative_to(root.resolve()) and target.is_file()):
            return False
        folder = root
        for part in rel.split("/"):
            if part not in os.listdir(folder):
                return False
            folder = folder / part
        return True
    except OSError:
        return False


def _check_anchor(anchor: Mapping[str, Any]) -> None:
    _validate_anchor(anchor)
    if any(ch in LINE_SEPARATORS for ch in anchor["quote"] + anchor["prefix"] + anchor["suffix"]):
        raise EvidenceRefused("line_separator", "the passage holds a line separator the ledger cannot store")


def _write(root: Path, plan: Callable[[dict[str, dict[str, Any]], LedgerLoad], dict[str, Any]], *, expected_id: Any,
           timeout: float | None, now: Callable[[], datetime] | None) -> dict[str, Any]:
    _may_write(root)
    with write_guard(root, expected_id, human=True, timeout=timeout) as held:
        loaded, decision = assess(root)
        if not decision.may_write:
            raise EvidenceRefused("untrusted", f"{EVIDENCE_FILENAME} may not be written now ({decision.reason})",
                                  message_of(decision) or MESSAGES["missing"])
        fields = plan(links_from(loaded), loaded)
        try:
            entry = ledger_mod.append(evidence_path(root), SCHEMA, fields, lock=held, project_root=root,
                                      create=decision.may_create, now=now)
        except LedgerWriteRefused as exc:
            raise EvidenceRefused("not_written", str(exc)) from exc
        if loaded.state is RecordState.ABSENT:
            # From now on the file is expected: a later read that finds it gone refuses rather than founds (9.3).
            record_files.snapshot_if_first_change_today(root, evidence_path(root), now=now)
        return {"entry": entry.id, "act": entry.get("act"), "link": entry.get("link")}


def _standing(links: dict[str, dict[str, Any]], loaded: LedgerLoad, link: Any) -> dict[str, Any]:
    if isinstance(link, str) and link in links:
        return links[link]
    known = isinstance(link, str) and loaded.ledger is not None and loaded.ledger.history((link,))
    raise EvidenceRefused("withdrawn" if known else "no_such_link", f"no standing link {link!r}")


def add_link(project_root: str | Path, draft: str, file: str, anchor: Mapping[str, Any], note: str | None = None, *,
             via: str, expected_id: Any = READ_NOW, timeout: float | None = None,
             now: Callable[[], datetime] | None = None) -> dict[str, Any]:
    """关联依据文件: a new link (`pl-…`) from the passage `anchor` of `draft` to `file`, a file of the project named by
    its path or a file node id, and stored under one name only: the path as spelt on disk, with the prefix
    `notes.normalize_node` classifies it under (so `file:x.png` and `x.png` are one supporting file). A node that is not
    a file (a claim, an attempt) is refused: nothing here can tell yet whether the index holds it."""
    root = Path(project_root)
    try:
        draft, node = clean_tex_draft(draft), notes.normalize_node(file)
        _validate_anchor(anchor)
        if note is not None and not isinstance(note, str):
            raise ValueError("a note is text")
        if note is not None:
            note.encode("utf-8")
    except (ValueError, UnicodeEncodeError) as exc:
        raise EvidenceRefused("invalid_request", str(exc)) from exc
    _check_anchor(anchor)
    note = " ".join(note.split()) or None if note is not None else None
    if note is not None and len(note) > MAX_NOTE:
        raise EvidenceRefused("note_too_long", f"a note is at most {MAX_NOTE} characters")
    rel = notes.node_path(node)
    if rel is None or not _file_of_project(root, node):
        raise EvidenceRefused("no_such_file", f"{node} is not a file of the project")
    node = notes.normalize_node(rel)
    fields = {"act": LINK, "link": "pl-" + secrets.token_hex(16), "draft": draft, "file": node, "note": note,
              "via": via, "anchor": dict(anchor)}
    same = ("quote", "prefix", "suffix")

    def plan(links: dict[str, dict[str, Any]], _loaded: LedgerLoad) -> dict[str, Any]:
        # The same passage to the same file twice (a retried panel, a second click) would be two links of one thing.
        if any(item["draft"] == draft and item["file"] == node and all(item["anchor"].get(k) == anchor.get(k) for k in same)
               for item in links.values()):
            raise EvidenceRefused("already_linked", f"this passage is already linked to {node}")
        return fields

    return _write(root, plan, expected_id=expected_id, timeout=timeout, now=now)


def reanchor(project_root: str | Path, link: str, draft: str, anchor: Mapping[str, Any], *, via: str,
             expected_id: Any = READ_NOW, timeout: float | None = None,
             now: Callable[[], datetime] | None = None) -> dict[str, Any]:
    """「是这段」 / 「仍然成立」: the standing link's passage is now `anchor`, in the same draft."""
    try:
        _validate_anchor(anchor)
    except ValueError as exc:
        raise EvidenceRefused("invalid_request", str(exc)) from exc
    _check_anchor(anchor)

    def plan(links: dict[str, dict[str, Any]], loaded: LedgerLoad) -> dict[str, Any]:
        if _standing(links, loaded, link)["draft"] != draft:
            raise EvidenceRefused("invalid_request", f"link {link} is about another draft")
        return {"act": REANCHOR, "link": link, "via": via, "anchor": dict(anchor)}

    return _write(Path(project_root), plan, expected_id=expected_id, timeout=timeout, now=now)


def withdraw(project_root: str | Path, link: str, *, via: str, expected_id: Any = READ_NOW,
             timeout: float | None = None, now: Callable[[], datetime] | None = None) -> dict[str, Any]:
    """撤回关联: the link no longer stands (an entry; nothing is removed)."""

    def plan(links: dict[str, dict[str, Any]], loaded: LedgerLoad) -> dict[str, Any]:
        _standing(links, loaded, link)
        return {"act": WITHDRAW, "link": link, "via": via}

    return _write(Path(project_root), plan, expected_id=expected_id, timeout=timeout, now=now)


def undo(project_root: str | Path, link: str, undoes: str, *, via: str, expected_id: Any = READ_NOW,
         timeout: float | None = None, now: Callable[[], datetime] | None = None) -> dict[str, Any]:
    """撤销: takes back the link's last act, which must be `undoes` (the engine refuses any other, and a link in
    conflict)."""
    if not isinstance(link, str) or not isinstance(undoes, str) or not undoes:
        raise EvidenceRefused("invalid_request", "an undo names the link and the act it takes back")

    def plan(_links: dict[str, dict[str, Any]], loaded: LedgerLoad) -> dict[str, Any]:
        if loaded.ledger is None or not loaded.ledger.history((link,)):
            raise EvidenceRefused("no_such_link", f"no link {link!r}")
        return {"act": UNDONE, "link": link, "undoes": undoes, "via": via}

    return _write(Path(project_root), plan, expected_id=expected_id, timeout=timeout, now=now)


# -- a member's acts as a proposal, and the PI's answer (22.5) ----------------------------------------------------

_ITEM_FIELDS = ("id", "at", "act", "link", "note", "via", "undoes")
_ANCHOR_FIELDS = ("quote", "prefix", "suffix", "offset")


def _current_of(entries: Any) -> str | None:
    """The act that stands after `entries` (one link's, in file order), by the engine's undo rule."""
    entries = list(entries)
    cancelled = {e.get("undoes") for e in entries if e.get("act") == UNDONE}
    return next((e.id for e in reversed(entries) if e.get("act") != UNDONE and e.id not in cancelled), None)


def proposal_items(project_root: str | Path, loaded: LedgerLoad, skip: Any = ()) -> list[dict[str, Any]]:
    """A member's 待批准 entries not in `skip` (already proposed), in file order, as a proposal's flat `[[evidence]]`:
    the entry's fields and its anchor's, the link's `draft` and `file` (for the reader), and on the first entry of a link
    made before, `follows` -- the act it stood on then, which approving requires to be the official one still ("" for a
    link they make, which approving requires the official ledger not to hold)."""
    waiting = unapproved(project_root, loaded) - set(skip)
    out: list[dict[str, Any]] = []
    seen: set[Any] = set()
    for e in loaded.ledger.entries if loaded.ledger is not None else ():
        if e.id not in waiting:
            continue
        history = loaded.ledger.history(e.key)
        made = next((x for x in history if x.get("act") == LINK), None)
        item = {k: e.get(k) for k in _ITEM_FIELDS if e.get(k) is not None}
        item.update(at=e.at, draft=made.get("draft") if made else None, file=made.get("file") if made else None,
                    **{k: (e.get("anchor") or {}).get(k) for k in _ANCHOR_FIELDS})
        if e.key not in seen:
            seen.add(e.key)
            item["follows"] = _current_of(x for x in history if x.index < e.index) or ""
        out.append({k: v for k, v in item.items() if v is not None})
    return out


def entry_of(item: Mapping[str, Any], by: str, approved_by: str | None = None) -> dict[str, Any]:
    """The ledger entry a proposal's item stands for, made by `by` (the member), approved by `approved_by`."""
    entry = {k: item[k] for k in _ITEM_FIELDS if item.get(k) is not None}
    entry.update(by=by, approved_by=approved_by)
    if item.get("act") == LINK:
        entry.update(draft=item.get("draft"), file=item.get("file"))
    if item.get("act") in (LINK, REANCHOR):
        entry["anchor"] = {k: item.get(k) for k in _ANCHOR_FIELDS}
    return {k: v for k, v in entry.items() if v is not None}


def check_item(item: Any, by: str) -> None:
    """A proposal's item as read from the exchange: an entry the ledger would take. Raises ValueError."""
    if not isinstance(item, Mapping) or not str(item.get("id") or "").startswith(SCHEMA.id_prefix):
        raise ValueError("each [[evidence]] is a member's entry, with its pe-… id")
    entry = entry_of(item, by)
    ledger_mod._validate_engine_fields(SCHEMA, entry)  # noqa: SLF001
    SCHEMA.validate(entry)
    SCHEMA.validate_new(entry)
    if item.get("follows") is not None and not isinstance(item["follows"], str):
        raise ValueError("'follows' names the act the link stood on")


def review(project_root: str | Path, items: list[Mapping[str, Any]]) -> dict[str, Any]:
    """What approving `items` would do to the official ledger (reads only): `state` `apply`, `same` (held already: an
    approval taken up again), `conflict` (`code` `link_changed`: a link the member acted on is no longer as they saw
    it, or one they made exists already) or `problem` (`code` `untrusted`)."""
    loaded, decision = assess(project_root)
    if not decision.may_write:
        return {"state": "problem", "code": "untrusted", "words": message_of(decision) or MESSAGES["missing"]}
    ledger = loaded.ledger or ledger_mod.parse_ledger("", SCHEMA)
    if all(item["id"] in ledger.ids for item in items):
        return {"state": "same"}
    for item in (i for i in items if "follows" in i):   # the first entry of each link
        state = ledger.state((item["link"],))
        now = state.entry.id if state.entry is not None else ""
        made_here = item["follows"] == "" and item.get("act") == LINK
        if state.conflict is not None or (bool(state.history) if made_here else now != item["follows"]):
            return {"state": "conflict", "code": "link_changed", "link": item["link"]}
    return {"state": "apply"}


def _as_ledger(datas: list[Mapping[str, Any]]) -> ledger_mod.Ledger:
    return ledger_mod.Ledger(SCHEMA, tuple(ledger_mod.LedgerEntry(data=d, index=i, line=None, key=SCHEMA.key_of(d))
                                           for i, d in enumerate(datas)), ())


def _closing(ledger: ledger_mod.Ledger, entries: list[dict[str, Any]], via: str) -> list[dict[str, Any]]:
    """The PI's acts that take back what `entries` did to each link when a proposal is returned: a link they made is
    withdrawn; one that stood before stands again on its official passage (their withdraw undone, its anchor put back);
    one withdrawn before is withdrawn again."""
    close: list[dict[str, Any]] = []
    for key in dict.fromkeys(SCHEMA.key_of(e) for e in entries):
        state = ledger.state(key).entry
        want = dict(state.get("anchor")) if state is not None and state.get("act") in (LINK, REANCHOR) else None
        for _ in range(2):
            after = _as_ledger([*ledger.entry_dicts(), *entries,
                                *({**c, "id": f"pe-closing-{n}"} for n, c in enumerate(close))]).state(key).entry
            act = after.get("act") if after is not None else None
            if want is None and act in (LINK, REANCHOR):
                close.append({"act": WITHDRAW, "link": key[0], "via": via})
            elif want is not None and act == WITHDRAW:
                close.append({"act": UNDONE, "link": key[0], "undoes": after.id, "via": via})
                continue
            elif want is not None and act in (LINK, REANCHOR) and dict(after.get("anchor")) != want:
                close.append({"act": REANCHOR, "link": key[0], "anchor": want, "via": via})
            break
    return close


def settle(project_root: str | Path, items: list[Mapping[str, Any]], member: str, *, approved_by: str | None, lock: Any,
           via: str, now: Callable[[], datetime] | None = None) -> list[str]:
    """The PI's answer written (under the held project lock): approved (`approved_by` the PI), the member's entries
    with their own ids; returned (`approved_by` None), the same entries followed by the PI's `_closing` acts. Nothing
    when the ledger holds them already. Returns the ids written."""
    root = Path(project_root)
    loaded, decision = assess(root)
    if not decision.may_write:
        raise EvidenceRefused("untrusted", f"{EVIDENCE_FILENAME} may not be written now ({decision.reason})",
                              message_of(decision) or MESSAGES["missing"])
    ledger = loaded.ledger or ledger_mod.parse_ledger("", SCHEMA)
    if all(item["id"] in ledger.ids for item in items):
        return []
    entries = [entry_of(item, member, approved_by) for item in items]
    if approved_by is None:
        entries += _closing(ledger, entries, via)
    try:
        written = ledger_mod.append_many(evidence_path(root), SCHEMA, entries, lock=lock, project_root=root,
                                         create=decision.may_create, now=now, adopt=True)
    except LedgerWriteRefused as exc:
        raise EvidenceRefused("not_written", str(exc)) from exc
    if loaded.state is RecordState.ABSENT:
        record_files.snapshot_if_first_change_today(root, evidence_path(root), now=now)
    return [e.id for e in written]
