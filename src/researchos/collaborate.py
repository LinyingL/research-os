"""Working with a team from the command line (DESIGN.md 12.1-12.5; task V8
phase 5): what `researchos status` says about people and the shared folder,
and the guided steps of `researchos collaborate`.

Read only. Nothing here writes: `overview` gathers what the person at this
computer needs to know about a project -- who they are, their role, the
shared folder (last snapshot, whether it verified, how far a member's copy
is behind), the proposals waiting, the notes and the history kept -- and
`overview_lines` words it plainly, in English (the command line is a
developer surface, 8.8). `plan` lists the steps of setting up, in order, for
the PI or for a member, each with whether it is done and the exact command
that does it; `researchos collaborate STEP --yes` runs that command, and
nothing else (`researchos.cli.cmd_collaborate`).

The steps:

- the PI: me (who I am) -> init (the project has an identity) -> team
  (team.toml with me as the PI and my key) -> share (the shared folder,
  set on this computer) -> publish (the first signed snapshot) -> member
  (a member added with the key they sent, and published).
- a member: me -> key (the public key to send the PI) -> join (a working
  copy from the shared folder's latest verified snapshot) -> added (the PI
  has added me and I have synced the team that says so).

A step is refused when an earlier one is not done, with the reason and the
command of the earlier one. Whatever cannot be read is said, never guessed.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from researchos import exchange as ex_mod
from researchos import history, people, signing
from researchos.records.identity import IdentityState, read_identity

COMMAND = "researchos"

PI_STEPS = ("me", "init", "team", "share", "publish", "member")
MEMBER_STEPS = ("me", "key", "join", "added")
#: Steps that may be run again once done (another snapshot, another member,
#: the key printed again, another sync).
REPEATABLE = frozenset({"publish", "member", "key", "added"})
ROLES = ("pi", "member")

#: A verified-or-not state of the last good snapshot, plainly.
VERIFICATION_WORDS = {
    "verified": "signature verified with the PI's key",
    "no_key": "not signed: the PI has no key in team.toml, so only the shared folder's permissions protect it",
    "no_tool": "signature not checked: ssh-keygen is missing on this computer, so only the shared folder's "
               "permissions protect it",
}
#: Why a snapshot was refused (`share.verify_chain`'s states), plainly.
REFUSED_WORDS = {
    "wrong_project": "it belongs to another project",
    "not_pi": "it was not written by the PI",
    "bad": "its signature is not the PI's key, or it was changed after it was signed",
    "unsigned": "it carries no signature",
    "chain": "it does not follow the last trusted snapshot",
    "pi_changed": "its team file names another PI, or cannot be read",
    "pi_key_changed": "its team file carries another key for the PI than the one trusted when joining",
}
NO_TOOL_WORDS = ("ssh-keygen is missing on this computer: snapshots and proposals cannot be signed or verified "
                 "here; only the shared folder's permissions protect them")


def command_line(argv: list[str]) -> str:
    """`argv` as one line to copy into a terminal."""
    return shlex.join([COMMAND, *argv])


# -- what status says ------------------------------------------------------------------------------


def _identity(root: Path) -> str | None:
    got = read_identity(root)
    if got.state is IdentityState.PRESENT and got.identity is not None:
        return got.identity.id
    return None


def overview(root: str | Path) -> dict[str, Any]:
    """Everything `researchos status` says about people, the shared folder,
    proposals, notes and history. Every part is read on its own; one that
    cannot be read carries `error` and the others are still given."""
    from researchos import proposals, share  # noqa: PLC0415
    from researchos.records import decisions as decisions_mod  # noqa: PLC0415
    from researchos.records import notes as notes_mod  # noqa: PLC0415

    root = Path(root)
    out: dict[str, Any] = {"root": str(root), "project": _identity(root)}
    got = people.read_me()
    out["me"] = {"state": got.state, "path": str(got.path), "error": got.error,
                 **(got.me.payload() if got.me is not None else {})}
    role, me, team = proposals.role_of(root)
    out["role"] = role
    out["team"] = {"state": team.state, "error": team.error, "pi": team.pi.id if team.pi else None,
                   "members": [{"id": m.id, "name": m.name, "role": m.role} for m in team.members]}
    out["ssh_keygen"] = signing.tool() is not None
    if out["project"] is not None:
        try:
            out["share"] = share.status(root)
        except Exception as exc:  # noqa: BLE001 -- a status never fails on one part
            out["share"] = {"error": str(exc)}
        try:
            joined = (out["share"].get("joined") or {})
            if joined.get("seq") is not None and out["share"].get("behind"):
                seen = share.available(root)
                out["share"]["behind_by"] = sum(1 for s in seen["chain"].good if s.seq > joined["seq"])
        except Exception as exc:  # noqa: BLE001
            out["share"]["behind_error"] = str(exc)
        if role == "single" and out["share"].get("joined"):
            # A copy joined from a shared folder whose team does not list me
            # yet is not a project of one: its official version is the PI's.
            out["role"] = role = "none"
        out["proposals"] = _proposals(root, role)
        out["history"] = _history(root, out["project"])
    for name, reader in (("notes", notes_mod.summary), ("decisions", decisions_mod.summary)):
        try:
            out[name] = reader(root)
        except Exception as exc:  # noqa: BLE001
            out[name] = {"state": "error", "error": str(exc)}
    return out


def _proposals(root: Path, role: str) -> dict[str, Any]:
    from researchos import proposals  # noqa: PLC0415

    if role not in ("pi", "member"):
        return {"role": role}
    try:
        listed = proposals.listing(root)
    except Exception as exc:  # noqa: BLE001
        return {"role": role, "error": str(exc)}
    counts: dict[str, int] = {}
    for item in listed["proposals"]:
        counts[item["state"]] = counts.get(item["state"], 0) + 1
    returned = [{"id": p["id"], "sentence": p.get("sentence"),
                 "reason": next((o.get("text") for o in reversed(p["outcomes"]) if o.get("act") == "returned"), None)}
                for p in listed["proposals"] if p["state"] == "returned"]
    unverified = sum(1 for p in listed["proposals"]
                     if p["state"] == "pending" and not ((p.get("verification") or {}).get("trusted", True)))
    return {"role": role, "counts": counts, "returned": returned, "unverified": unverified,
            "problems": len(listed["problems"])}


def _history(root: Path, project_id: str) -> dict[str, Any]:
    try:
        store = history.Store.existing(project_id)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    if store is None:
        return {"versions": 0, "nodes": 0, "git": history._is_git(root)}
    with store:
        versions, nodes = store.counts()
    return {"versions": versions, "nodes": nodes, "git": history._is_git(root)}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _me_lines(ov: dict[str, Any]) -> list[str]:
    me = ov["me"]
    if me["state"] == "present":
        return [f"Who I am: {me['name']} <{me['email']}> (id {me['id']})"]
    if me["state"] == "absent":
        return ["Who I am: not set on this computer -- what you record carries no name; set it with: "
                + command_line(["me", "set", "--name", "NAME", "--email", "EMAIL"])]
    return [f"Who I am: {me['path']} cannot be read ({me['state']}: {me['error']}) -- every record you would write "
            f"is refused until it is fixed with: " + command_line(["me", "set", "--name", "NAME", "--email", "EMAIL"])]


def _role_lines(ov: dict[str, Any]) -> list[str]:
    role, team = ov["role"], ov["team"]
    members = [m for m in team["members"] if m["role"] == "member"]
    if role == "single":
        return ["My role: one person -- no approval step: your changes are official when you make them"]
    if role == "unknown":
        return [f"My role: unknown -- .researchos/team.toml cannot be read ({team['state']}: {team['error']}); "
                f"nothing is proposed or approved until it is fixed"]
    if role == "pi":
        return [f"My role: the PI -- you approve proposals and publish the official version "
                f"(team: you and {_plural(len(members), 'member')})"]
    if role == "member":
        return [f"My role: a member -- your changes reach the official version as proposals the PI "
                f"({team['pi']}) approves"]
    if ov["me"]["state"] != "present":
        return [f"My role: not known until you say who you are (the PI is {team['pi']})"]
    return [f"My role: not in this project's team yet (team.toml lists {len(team['members'])}; the PI is "
            f"{team['pi']}) -- send the PI your key (researchos me key --out FILE); once they add you and "
            f"publish: researchos share sync"]


def _share_lines(ov: dict[str, Any]) -> list[str]:
    s = ov.get("share")
    if s is None:
        return []
    if s.get("error"):
        return [f"Shared folder: cannot be read now ({s['error']})"]
    role = ov["role"]
    root = ov["root"]
    if not s.get("shared"):
        if role in ("single",) and not s.get("joined"):
            return []
        return ["Shared folder: not set on this computer -- " + (
            command_line(["share", "set", "DIR", root]) if role == "pi"
            else "a member's working copy is made with: " + command_line(["share", "join", "SHARED", "FOLDER"]))]
    lines = [f"Shared folder: {s['shared']}" + ("" if s.get("reachable") else
                                                " -- cannot be reached now (is the sync app running and the folder "
                                                "downloaded to this computer?)")]
    if not ov["ssh_keygen"]:
        lines.append(f"  Warning: {NO_TOOL_WORDS}")
    last = s.get("last_snapshot")
    if s.get("reachable"):
        if last:
            check = VERIFICATION_WORDS.get(s.get("verification") or "", "signature not checked")
            lines.append(f"  Last snapshot: {last['seq']}, published {last['at']} by {last['by']}; {check}")
        elif role == "pi":
            lines.append("  Last snapshot: none published yet -- " + command_line(["share", "publish", root]))
        else:
            lines.append("  Last snapshot: none that verifies")
    joined = s.get("joined")
    if joined:
        if s.get("behind"):
            n = s.get("behind_by")
            how_far = f"{_plural(n, 'snapshot')} behind" if isinstance(n, int) and n > 0 else "behind"
            lines.append(f"  Your copy: snapshot {joined['seq']} -- {how_far} the latest ({last['seq']}): "
                         + command_line(["share", "sync", root]))
        elif s.get("reachable"):
            lines.append(f"  Your copy: snapshot {joined['seq']} -- up to date")
        else:
            lines.append(f"  Your copy: snapshot {joined['seq']} (the shared folder cannot be checked now)")
        copies = joined.get("official_copies") or []
        if copies:
            lines.append(f"  Official versions beside yours (both sides changed; compare, keep what is right, then "
                         f"delete the .official file): {', '.join(copies)}")
    elif role == "member":
        lines.append("  Your copy: this folder was not made with 'researchos share join' on this computer")
    pending = s.get("pending_publish")
    if pending and pending.get("count"):
        lines.append(f"  Not yet published: {_plural(pending['count'], 'file')} of the official version changed since "
                     f"the last snapshot -- " + command_line(["share", "publish", root]))
    if pending and pending.get("dataless"):
        lines.append(f"  Only in the cloud (download them before publishing): {', '.join(pending['dataless'][:10])}")
    for item in s.get("refused") or []:
        why = REFUSED_WORDS.get(item.get("state"), item.get("state"))
        lines.append(f"  Refused snapshot {item['seq']}: {why} -- not used; the last good one is kept")
    if s.get("problems"):
        lines.append(f"  Not read in the shared folder: {_plural(len(s['problems']), 'item')} (half-written, or a "
                     f"sync conflict copy): researchos share show {root}")
    return lines


def _proposal_lines(ov: dict[str, Any]) -> list[str]:
    p = ov.get("proposals")
    if not p or p["role"] not in ("pi", "member"):
        return []
    root = ov["root"]
    if p.get("error"):
        return [f"Proposals: cannot be read now ({p['error']})"]
    counts = p["counts"]
    if p["role"] == "pi":
        waiting = counts.get("pending", 0)
        line = (f"Proposals: {waiting} waiting for your decision -- " + command_line(["inbox", root])
                if waiting else "Proposals: none waiting for your decision")
        lines = [line]
        if p["unverified"]:
            lines.append(f"  {p['unverified']} of them cannot be approved: the signature does not verify")
    else:
        order = ("pending", "returned", "approved", "superseded", "conflict")
        parts = [f"{counts[s]} {s}" for s in order if counts.get(s)]
        lines = ["My proposals: " + (", ".join(parts) if parts else "none")]
        for item in p["returned"][-3:]:
            lines.append(f"  returned {item['id']}: {item['reason'] or '(no reason given)'}")
    if p["problems"]:
        lines.append(f"  Not read: {_plural(p['problems'], 'item')} in the shared folder (half-written, or a sync "
                     f"conflict copy)")
    return lines


def _record_lines(ov: dict[str, Any]) -> list[str]:
    lines = []
    n = ov.get("notes") or {}
    if n.get("state") == "present":
        lines.append(f"Notes: {_plural(n['notes'], 'note')} on {_plural(n['nodes'], 'node')}"
                     + (f" ({n['withdrawn']} withdrawn)" if n.get("withdrawn") else ""))
    elif n.get("state") == "absent":
        lines.append("Notes: none yet")
    elif n:
        lines.append(f"Notes: .researchos/notes.toml cannot be read ({n.get('state')}: {n.get('error')})")
    h = ov.get("history")
    if h is not None:
        if h.get("error"):
            lines.append(f"History: cannot be read now ({h['error']})")
        else:
            lines.append(f"History: {_plural(h['versions'], 'version')} of {_plural(h['nodes'], 'node')} kept by "
                         f"Research OS" + ("; files git tracks keep theirs in git" if h.get("git") else ""))
    d = ov.get("decisions") or {}
    if d.get("state") == "present" and d.get("entries"):
        lines.append(f"Decisions: {d['approved']} approved" + (f", {d['abandoned']} abandoning a route"
                                                               if d.get("abandoned") else "")
                     + " (.researchos/decisions.toml)")
    elif d.get("state") not in (None, "present", "absent"):
        lines.append(f"Decisions: .researchos/decisions.toml cannot be read ({d.get('state')}: {d.get('error')})")
    return lines


def overview_lines(ov: dict[str, Any]) -> list[str]:
    """`overview`, worded for the terminal."""
    return _me_lines(ov) + _role_lines(ov) + _share_lines(ov) + _proposal_lines(ov) + _record_lines(ov)


# -- the guided steps --------------------------------------------------------------------------------


@dataclass
class Step:
    name: str
    title: str
    done: bool
    detail: str
    argv: list[str] | None = None  # the command that does it, when this person can run it here
    missing: list[str] = field(default_factory=list)  # options `collaborate` needs to fill the command
    after: str | None = None  # what to do once it ran (send a file, wait for the PI)

    def payload(self) -> dict[str, Any]:
        return {"name": self.name, "title": self.title, "done": self.done, "detail": self.detail,
                "command": command_line(self.argv) if self.argv else None, "missing": self.missing,
                "after": self.after}


class StepRefused(Exception):
    """A step that cannot be taken now, with the reason; nothing was run."""


@dataclass
class Values:
    """What the person gave `collaborate` to fill the commands."""

    name: str | None = None
    email: str | None = None
    shared: str | None = None
    folder: str | None = None
    member: str | None = None
    member_name: str | None = None
    key: str | None = None
    out: str | None = None
    note: str | None = None


def infer_role(root: Path | None) -> str | None:
    """"member" when this project's team names me as a member or this
    folder was joined from a shared folder on this computer; "pi" when the
    team names me as its PI, or when this is a project that was not joined
    (a project of one's own is the official version, and its keeper is its
    PI); None when `root` is no project."""
    from researchos import share  # noqa: PLC0415

    if root is None or not root.is_dir():
        return None
    pid = _identity(root)
    if pid is None:
        return None
    team = people.read_team(root)
    try:
        me = people.current_member_id()
    except people.PeopleError:
        me = None
    if team.state == "present" and me is not None:
        mine = next((m for m in team.members if m.id == me), None)
        if mine is not None:
            return mine.role
    try:
        state = share.read_state(pid)
    except Exception:  # noqa: BLE001
        state = None
    if state and Path(state.get("working_copy", "")) == root.resolve():
        return "member"
    return "pi" if team.state != "present" or team.pi is None or me is None or team.pi.id == me else None


def _me_step(v: Values) -> Step:
    got = people.read_me()
    argv = ["me", "set", "--name", v.name or "NAME", "--email", v.email or "EMAIL"]
    missing = [o for o, given in (("--name", v.name), ("--email", v.email)) if not given]
    if got.me is not None:
        return Step("me", "Say who you are on this computer", True,
                    f"{got.me.name} <{got.me.email}> (id {got.me.id})", argv, missing)
    detail = ("not set yet" if got.state == "absent"
              else f"{got.path} cannot be read ({got.state}); setting it again replaces it")
    return Step("me", "Say who you are on this computer", False, detail, argv, missing)


def plan(role: str, root: Path | None, values: Values | None = None) -> list[Step]:
    """The steps for `role`, each with whether it is done. Raises
    `StepRefused` when the facts contradict the role asked for."""
    if role not in ROLES:
        raise StepRefused(f"say which you are: --as pi (you lead the project and keep the official version) or "
                          f"--as member (you join from the shared folder)")
    v = values or Values()
    return _pi_plan(root, v) if role == "pi" else _member_plan(root, v)


def _pi_plan(root: Path | None, v: Values) -> list[Step]:
    from researchos import share  # noqa: PLC0415

    where = str(root) if root is not None else "PROJECT"
    steps = [_me_step(v)]
    me = steps[0].done and people.read_me().me
    pid = _identity(root) if root is not None and root.is_dir() else None
    steps.append(Step("init", "Make the folder a Research OS project (the official version)", pid is not None,
                      f"project {pid}" if pid else f"{where} has no project identity yet", ["init", where]))
    team = people.read_team(root) if pid else None
    if team is not None and team.state == "present" and team.pi is not None and me and team.pi.id != me.id:
        raise StepRefused(f"{where}'s PI is {team.pi.id}, not you ({me.id}): only the PI keeps the official "
                          f"version; a member joins from the shared folder (--as member)")
    if team is not None and team.state not in ("present", "absent"):
        steps.append(Step("team", "Write the team file with you as the PI", False,
                          f".researchos/team.toml cannot be read ({team.state}: {team.error}); fix it first", None))
    else:
        has_team = team is not None and team.state == "present"
        key_note = "" if signing.tool() else " (ssh-keygen is missing: no key, snapshots will not be signed)"
        steps.append(Step("team", "Write the team file with you as the PI and your public key", has_team,
                          ("written: " + ", ".join(f"{m.id} ({m.role})" for m in team.members)) if has_team
                          else "not written yet" + key_note, ["team", "init", where]))
    shared = ex_mod.configured(pid) if pid else None
    reachable = bool(shared and shared.is_dir())
    steps.append(Step("share", "Choose the shared folder (a OneDrive folder shared with the team)",
                      reachable, (f"{shared}" if reachable else f"set to {shared}, which cannot be reached now"
                                  if shared else "not set on this computer"),
                      ["share", "set", v.shared or "SHARED_FOLDER", where], [] if v.shared else ["--shared"]))
    status: dict[str, Any] = {}
    if reachable and pid and team is not None and team.state == "present":
        try:
            status = share.status(root)
        except Exception as exc:  # noqa: BLE001
            status = {"error": str(exc)}
    last = status.get("last_snapshot")
    pending = (status.get("pending_publish") or {})
    publish_argv = ["share", "publish", where] + (["--note", v.note] if v.note else [])
    if last:
        detail = f"snapshot {last['seq']} published {last['at']}"
        if pending.get("count"):
            detail += f"; {_plural(pending['count'], 'file')} changed since -- publish again"
    else:
        detail = status.get("error") or "nothing published yet"
    steps.append(Step("publish", "Publish the official version as the first signed snapshot", bool(last), detail,
                      publish_argv))
    members = [m for m in team.members if m.role == "member"] if team is not None and team.state == "present" else []
    team_rel = people.team_path(root).relative_to(root).as_posix() if root is not None else ""
    team_published = bool(last) and team_rel not in (pending.get("files") or [])
    if members and not team_published:
        steps.append(Step("member", "Add a member and publish, so their copy carries the team", False,
                          f"{', '.join(m.id for m in members)} added but not published yet", publish_argv))
    else:
        argv = ["team", "add", v.member or "EMAIL", "--name", v.member_name or "NAME", "--key", v.key or "KEYFILE", where]
        missing = [o for o, given in (("--member", v.member), ("--member-name", v.member_name), ("--key", v.key))
                   if not given]
        steps.append(Step("member", "Add a member with the key they sent you, then publish", bool(members),
                          (f"in the team and published: {', '.join(m.id for m in members)}" if members
                           else "no member yet: ask them to run 'researchos me key --out FILE' and send you the file "
                                "and their e-mail"), argv, missing,
                          after="then publish, so their copy carries the team: " + command_line(publish_argv)))
    return steps


def _member_plan(root: Path | None, v: Values) -> list[Step]:
    from researchos import share  # noqa: PLC0415

    steps = [_me_step(v)]
    me = people.read_me().me
    tool = signing.tool() is not None
    local = signing.local_public_key() if tool else None
    out = v.out or (f"researchos-key-{me.id}.pub" if me else "KEYFILE")
    if not tool:
        steps.append(Step("key", "Make your public key and send it to the PI", True,
                          "skipped: ssh-keygen is missing on this computer -- your proposals will not be signed",
                          None))
    else:
        steps.append(Step("key", "Make your public key and send it to the PI", local is not None,
                          f"made: {signing.fingerprint(local)}" if local else "not made yet",
                          ["me", "key", "--out", out],
                          after=f"send {out} and your e-mail to the PI"))
    pid = _identity(root) if root is not None and root.is_dir() else None
    state = None
    if pid:
        try:
            state = share.read_state(pid)
        except Exception:  # noqa: BLE001
            state = None
    joined = bool(state and Path(state.get("working_copy", "")) == root.resolve())
    team = people.read_team(root) if pid else None
    if pid and not joined and team is not None and me and team.pi is not None and team.pi.id == me.id:
        raise StepRefused(f"you are the PI of {root}: the PI keeps the official version (--as pi); members join from "
                          f"the shared folder")
    steps.append(Step("join", "Make your working copy from the shared folder", joined,
                      (f"{root}, at snapshot {state.get('seq')}; the PI {state.get('pi')} trusted with key "
                       f"{state.get('pi_fingerprint') or '(none)'}") if joined
                      else "not joined yet: the PI shares the folder with you first (view only for 已发布, "
                           "edit for 提议/<your e-mail>)",
                      ["share", "join", v.shared or "SHARED_FOLDER", v.folder or "NEW_FOLDER"],
                      [o for o, given in (("--shared", v.shared), ("--folder", v.folder)) if not given],
                      after="compare the PI key fingerprint it prints with the PI's own before relying on it"))
    added = bool(joined and me and team is not None and team.state == "present"
                 and any(m.id == me.id and m.role == "member" for m in team.members))
    where = str(root) if root is not None else "FOLDER"
    steps.append(Step("added", "Be added to the team by the PI, then sync", added,
                      f"in the team as {me.id}" if added and me else
                      "not in the team your copy carries yet: the PI runs 'researchos team add <your e-mail> --name "
                      "<your name> --key <your key file>' and publishes; then sync", ["share", "sync", where]))
    return steps


def next_step(steps: list[Step]) -> Step | None:
    return next((s for s in steps if not s.done), None)


def check_order(steps: list[Step], name: str) -> Step:
    """The step called `name`, refused (`StepRefused`) when an earlier one
    is not done yet."""
    names = [s.name for s in steps]
    if name not in names:
        raise StepRefused(f"there is no step '{name}' here; the steps are: {', '.join(names)}")
    index = names.index(name)
    for earlier in steps[:index]:
        if not earlier.done:
            how = command_line(earlier.argv) if earlier.argv else "see 'researchos collaborate'"
            raise StepRefused(f"step '{name}' comes after step {names.index(earlier.name) + 1} '{earlier.name}', which "
                              f"is not done yet ({earlier.detail}); first: {how}")
    return steps[index]


def daily_loop(role: str, root: Path | None) -> list[str]:
    where = str(root) if root is not None else "PATH"
    if role == "pi":
        return [
            "see what waits: " + command_line(["inbox", where]),
            "read one: " + command_line(["proposals", "--show", "ID", where]),
            "decide: " + command_line(["approve", "ID", "--note", "NOTE", where]) + "  |  "
            + command_line(["return", "ID", "--reason", "REASON", where]) + "  |  "
            + command_line(["discuss", "ID", "--text", "TEXT", where]),
            "after changing the official version yourself: " + command_line(["share", "publish", where]),
        ]
    return [
        "start from the latest official version: " + command_line(["share", "sync", where]),
        "work as usual; then look at what you would propose: "
        + command_line(["propose", "attempt_new|verdict_change|variable_version|files", "--sentence", "SENTENCE", where]),
        "send it: the same command with --yes",
        "see the answers: " + command_line(["proposals", where]),
    ]
