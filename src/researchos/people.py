"""People: who I am on this machine, and who is in a project (DESIGN.md
12.1, task V8 phase 2).

Who I am: `~/.researchos/me.toml`
--------------------------------

::

    # 这台电脑上的使用者。由 Research OS 写入（researchos me set）。
    id    = "li@example.com"
    name  = "李"
    email = "Li@Example.com"

Set once per machine with the name and the e-mail of the shared-drive
account. The id is the e-mail, lower-cased -- the same convention as
`contributor:<lowercased-email>` (Section 4) -- so the same person gets the
same id on every machine without anything to coordinate. From the moment
the file exists, every entry Research OS appends to any ledger carries
`by = "<id>"` (`researchos.records.ledger.append_many` asks
`current_member_id`). An entry without `by` was written before V8, or
before the file existed, and belongs to the project's first researcher
(`attribution`).

A `me.toml` that exists but cannot be read is not "nobody": writing an
entry without `by` would silently hand someone's act to the first
researcher. `current_member_id` raises `PeopleError` then, and the ledger
refuses the write with the reason.

Who is in a project: `.researchos/team.toml`
------------------------------------------

::

    [[member]]
    id   = "li@example.com"
    name = "李"
    role = "pi"                 # pi | member
    key  = "ssh-ed25519 AAAA…"  # 12.5; may be absent until keys exist

Written only by the PI's Research OS (a later phase); read and validated
here. A project without the file -- or whose only member is its PI -- is a
single-person project: no approval step anywhere (12.1). A team file that
cannot be read or is invalid is neither: `team_mode` says "unknown", and
nothing may treat such a project as single-person by default.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from researchos import paths, settings
from researchos.records import files as record_files
from researchos.records.files import RecordState

ME_FILENAME = "me.toml"
TEAM_FILENAME = "team.toml"
ROLES = ("pi", "member")
FIRST_RESEARCHER_LABEL = "最初的研究者"  # 12.12: a missing `by`
FIRST_RESEARCHER_LABEL_EN = "Original researcher"  # 13.4

_ME_HEADER = "# 这台电脑上的使用者。由 Research OS 写入（researchos me set），请用这个命令修改。\n"
_ME_KEYS = ("id", "name", "email")
_MEMBER_KEYS = ("id", "name", "role", "key")
# An e-mail address as an id: no whitespace, no control characters, one
# "@", a dotted domain. Deliberately plain -- it is an identifier, not a
# mail validator -- and lower-case only once normalised.
_ID_RE = re.compile(r"^[a-z0-9!#$%&'*+/=?^_`{|}~.-]+@[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$")
_MAX_ID = 254
_MAX_NAME = 200


class PeopleError(Exception):
    """A person could not be read or written as asked. `code` is
    machine-readable (`invalid_email`, `invalid_name`, `unreadable`,
    `invalid`); `message_zh` the app's sentence."""

    def __init__(self, code: str, message: str, message_zh: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message_zh = message_zh or {
            "invalid_email": "邮箱格式不对",
            "invalid_name": "名字不能为空，也不能含换行",
            "unreadable": "本机的使用者文件（me.toml）无法读取，请先修复它",
            "invalid": "本机的使用者文件（me.toml）格式不对，请先修复它",
        }.get(code, "无法读取使用者信息")


@dataclass(frozen=True)
class Me:
    id: str
    name: str
    email: str

    def payload(self) -> dict[str, str]:
        return {"id": self.id, "name": self.name, "email": self.email}


@dataclass(frozen=True)
class MeRead:
    """`state`: absent | present | dataless | unreadable | invalid."""

    state: str
    path: Path
    me: Me | None = None
    error: str | None = None


@dataclass(frozen=True)
class Member:
    id: str
    name: str
    role: str
    key: str | None = None

    def payload(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "role": self.role, "key": self.key}


@dataclass(frozen=True)
class TeamRead:
    """`state`: absent | present | dataless | unreadable | invalid |
    conflict_copy. `members` only when present."""

    state: str
    path: Path
    members: tuple[Member, ...] = ()
    error: str | None = None
    line: int | None = None
    conflict_copies: tuple[str, ...] = field(default_factory=tuple)

    @property
    def pi(self) -> Member | None:
        return next((m for m in self.members if m.role == "pi"), None)


# -- ids ------------------------------------------------------------------------


def member_id_for(email: str) -> str:
    """The member id of an e-mail address: stripped, lower-cased, validated.
    Raises `PeopleError("invalid_email")`."""
    if not isinstance(email, str):
        raise PeopleError("invalid_email", f"an e-mail address is text, got {type(email).__name__}")
    candidate = email.strip().lower()
    if not candidate or len(candidate) > _MAX_ID or not _ID_RE.match(candidate):
        raise PeopleError("invalid_email", f"not an e-mail address: {email!r}")
    return candidate


def is_member_id(value: Any) -> bool:
    return isinstance(value, str) and len(value) <= _MAX_ID and bool(_ID_RE.match(value))


def _clean_name(name: Any) -> str:
    if not isinstance(name, str) or not name.strip() or len(name) > _MAX_NAME:
        raise PeopleError("invalid_name", f"a name is non-empty text of at most {_MAX_NAME} characters, got {name!r}")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F or ch in "\x85  " for ch in name):
        raise PeopleError("invalid_name", f"a name is one line without control characters, got {name!r}")
    return name.strip()


# -- me.toml --------------------------------------------------------------------


def me_path() -> Path:
    return paths.researchos_home() / ME_FILENAME


def _parse_me(text: str) -> Me:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise PeopleError("invalid", f"{ME_FILENAME} is not valid TOML: {exc}") from exc
    unknown = [k for k in data if k not in _ME_KEYS]
    if unknown:
        raise PeopleError("invalid", f"{ME_FILENAME}: unknown key {unknown[0]!r}")
    email = data.get("email")
    member = member_id_for(email) if isinstance(email, str) else None
    if member is None:
        raise PeopleError("invalid", f"{ME_FILENAME} has no 'email'")
    if data.get("id") != member:
        raise PeopleError("invalid", f"{ME_FILENAME}: 'id' must be the e-mail lower-cased ({member!r}), got {data.get('id')!r}")
    return Me(id=member, name=_clean_name(data.get("name")), email=email.strip())


def read_me() -> MeRead:
    """Read `me.toml` without ever blocking on the cloud."""
    path = me_path()
    got = record_files.read_record(path)
    if got.state is RecordState.ABSENT:
        return MeRead("absent", path)
    if got.state is RecordState.DATALESS:
        return MeRead("dataless", path, error=got.error)
    if got.state is not RecordState.PRESENT:
        return MeRead("unreadable", path, error=got.error)
    try:
        return MeRead("present", path, me=_parse_me(got.text or ""))
    except PeopleError as exc:
        return MeRead("invalid", path, error=str(exc))


def current_member_id() -> str | None:
    """The id to write as `by`: None when `me.toml` does not exist (entries
    then carry no `by`, as before V8). Raises `PeopleError` when it exists
    but cannot be read -- never a silent "nobody" (module docstring)."""
    got = read_me()
    if got.state == "absent":
        return None
    if got.me is None:
        raise PeopleError(
            "invalid" if got.state == "invalid" else "unreadable",
            f"{got.path} cannot be read ({got.state}: {got.error}); fix it or run 'researchos me set' -- "
            f"Research OS will not write an entry without saying who made it",
        )
    return got.me.id


def emit_me(me: Me) -> bytes:
    from researchos.records.ledger import toml_string  # noqa: PLC0415 -- one emitter

    text = (
        _ME_HEADER
        + f"id    = {toml_string(me.id)}\n"
        + f"name  = {toml_string(me.name)}\n"
        + f"email = {toml_string(me.email)}\n"
    )
    data = text.encode("utf-8")
    if _parse_me(text) != me:  # pragma: no cover -- emitter guard
        raise PeopleError("invalid", "me.toml would not read back as written; nothing written")
    return data


def write_me(name: str, email: str) -> Me:
    """Write `~/.researchos/me.toml` (replacing it whole, atomically). The
    home is created if needed -- it is the machine's own folder, never a
    project."""
    email_clean = email.strip() if isinstance(email, str) else email
    me = Me(id=member_id_for(email_clean), name=_clean_name(name), email=email_clean)
    path = me_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    record_files.durable_write(path, emit_me(me))
    back = read_me()
    if back.me != me:
        raise PeopleError("invalid", f"{path} does not read back as written")
    return me


# -- team.toml ------------------------------------------------------------------


def team_path(project_root: str | Path) -> Path:
    return paths.project_dir(project_root) / TEAM_FILENAME


def _member_lines(text: str) -> list[int]:
    pattern = re.compile(r"^\s*\[\[\s*member\s*\]\]\s*(?:#.*)?$")
    return [n for n, line in enumerate(text.split("\n"), start=1) if pattern.match(line)]


def parse_team(text: str) -> tuple[Member, ...]:
    """Validate a team file's text. Raises `PeopleError("invalid")` whose
    message names the member's line where it can."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise PeopleError("invalid", f"{TEAM_FILENAME} is not valid TOML: {exc}") from exc
    unknown = [k for k in data if k != "member"]
    if unknown:
        raise PeopleError("invalid", f"{TEAM_FILENAME}: unknown key {unknown[0]!r}")
    raw = data.get("member", [])
    if not isinstance(raw, list) or not all(isinstance(m, dict) for m in raw):
        raise PeopleError("invalid", f"{TEAM_FILENAME}: members are written as [[member]] tables")
    lines = _member_lines(text)
    members: list[Member] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        where = f"line {lines[i]}" if len(lines) == len(raw) else f"member #{i + 1}"
        bad = [k for k in item if k not in _MEMBER_KEYS]
        if bad:
            raise PeopleError("invalid", f"{TEAM_FILENAME} {where}: unknown key {bad[0]!r}")
        member = item.get("id")
        if not is_member_id(member):
            raise PeopleError("invalid", f"{TEAM_FILENAME} {where}: 'id' must be a lower-case e-mail address, got {member!r}")
        if member in seen:
            raise PeopleError("invalid", f"{TEAM_FILENAME} {where}: member {member!r} is listed twice")
        seen.add(member)
        role = item.get("role")
        if role not in ROLES:
            raise PeopleError("invalid", f"{TEAM_FILENAME} {where}: 'role' must be pi or member, got {role!r}")
        key = item.get("key")
        if key is not None and (not isinstance(key, str) or not key.strip() or "\n" in key):
            raise PeopleError("invalid", f"{TEAM_FILENAME} {where}: 'key' must be one line of text")
        try:
            name = _clean_name(item.get("name"))
        except PeopleError as exc:
            raise PeopleError("invalid", f"{TEAM_FILENAME} {where}: {exc}") from exc
        members.append(Member(id=member, name=name, role=role, key=key))
    if members and sum(1 for m in members if m.role == "pi") != 1:
        raise PeopleError("invalid", f"{TEAM_FILENAME}: a team has exactly one pi")
    return tuple(members)


def read_team(project_root: str | Path) -> TeamRead:
    path = team_path(project_root)
    copies = tuple(p.name for p in record_files.conflict_copies(path))
    got = record_files.read_record(path)
    if got.state is RecordState.ABSENT:
        return TeamRead("absent", path, conflict_copies=copies)
    if copies:
        return TeamRead("conflict_copy", path, error=", ".join(copies), conflict_copies=copies)
    if got.state is RecordState.DATALESS:
        return TeamRead("dataless", path, error=got.error)
    if got.state is not RecordState.PRESENT:
        return TeamRead("unreadable", path, error=got.error)
    try:
        return TeamRead("present", path, members=parse_team(got.text or ""))
    except PeopleError as exc:
        return TeamRead("invalid", path, error=str(exc))


def team_mode(team: TeamRead) -> str:
    """"single" (no team file, an empty one, or only its PI), "team", or
    "unknown" (a team file that cannot be read -- never assumed single)."""
    if team.state == "absent":
        return "single"
    if team.state != "present":
        return "unknown"
    if not team.members or (len(team.members) == 1 and team.members[0].role == "pi"):
        return "single"
    return "team"


def is_single_person(project_root: str | Path) -> bool:
    return team_mode(read_team(project_root)) == "single"


# -- readers ----------------------------------------------------------------------


def attribution(entry: Mapping[str, Any], team: TeamRead | None = None, lang: str = "zh") -> dict[str, Any]:
    """Who made an entry, as a reader shows it: `by` and the member's name
    when the team file knows it; an entry without `by` is the project's
    first researcher's (12.1), labelled in `lang` -- people words follow the
    interface language on every page (13.7); Chinese by default."""
    by = entry.get("by")
    if not isinstance(by, str) or not by:
        return {"by": None, "first_researcher": True, "name": None,
                "label": settings.say(FIRST_RESEARCHER_LABEL, FIRST_RESEARCHER_LABEL_EN, lang)}
    name = None
    if team is not None:
        name = next((m.name for m in team.members if m.id == by), None)
    if name is None:
        got = read_me()
        if got.me is not None and got.me.id == by:
            name = got.me.name
    return {"by": by, "first_researcher": False, "name": name, "label": name or by}
