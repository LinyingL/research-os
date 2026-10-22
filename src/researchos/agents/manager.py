"""The run manager: starts an agent's program in the project folder, follows
it, stops it, checks what it did and undoes it (DESIGN.md 17.3, 17.5, 17.6).

**One run, one thread.** `start` and `reply` validate in the caller's thread
(the HTTP request), write `run.json` (state `preparing`) and hand the rest to
a daemon thread, `researchos-agent-<run id>`, so the page returns at once and
follows the run by polling `runs`. The thread takes the before-picture
(`snapshot.take`, copies into the run's folder), writes the guard of the
hook, builds the instruction, starts the program in a session of its own
(`start_new_session`), reads its standard output line by line into
`events.jsonl` and the stream parser, and at the end takes the after-picture,
compares, records the agent's files in the change history and decides the
run's state. **Every exception in that thread ends the run in a terminal
state with an error recorded** (`_thread_main`); a run is never left
`running` by a bug here. If the engine itself dies, the next `runs()` finds
the stale `running` run and marks it `interrupted` (`_recover`).

**Locks.** `run.json` is only ever written through `run_store.update_run`
(one lock for the whole process), so the thread, the stop caller and the
request threads never tear it. `self._lock` guards the registry (one active
run per project) and is never held across I/O that can wait; each active run
has its own `lock` (the process handle and the stop reason, so a stop that
arrives between "decide to launch" and "launched" is not lost) and a
`kill_lock` (so a stop and the time limit do not run two kill sequences at
once). `stop`, `shutdown` and the time limit may be called from any thread
and any number of times.

**Stopping** (17.3) notes every process the program started *before* it
signals anything (OpenCode runs each shell command in a session of its own,
so the program's process group does not hold them, and once OpenCode dies
they are orphans nothing can find by tree), SIGTERMs the program, waits up
to `KILL_GRACE_S`, and SIGKILLs what of the noted tree is still alive --
deepest first, each checked against its start time so a pid the system has
since reused is never signalled -- and finally the program's own process
group. The tree comes from `ps -A -o pid=,ppid=,stat=,lstart=` (portable
between macOS and Linux).

**Leftovers.** While the program runs the pump samples its descendant tree
(`ps`, every ~2 s, denser at first) and keeps `pid -> start time` stamps. A
run that ends by itself would otherwise leave what the agent started (a
`nohup` download, a training job) running with nobody able to find it by tree;
so once the program has exited -- and reading its output has stopped after
`EXIT_DRAIN_S` however chatty a descendant is -- every recorded descendant
still alive (start time checked) and the program's process group are ended
*before* the after-picture, and the number is kept as `run["leftovers"]`.

**Who changed what.** The program's own writes happen in another process;
the engine's writes happen in this one and are announced by
`records.files.add_write_listener`. A file the engine wrote while the run
went on is the person's (they saved it through Research OS) and is left out
of the run's changes (`human_paths` / `changes.by_person`, 17.5); the set is
read *after* the after-picture is taken, with the listener still registered.
The listener also records the hash of what the engine wrote, so a protected
path the person saved is out of bounds only when it ends with other bytes
(`allowed`), not exempt for the whole run. Files
written by the agent *tools* (the result record, `research/tasks.json`) are
read from `signals.jsonl` (`tool_paths`, `tasks_sha`): they already carry
their own attribution, and `research/tasks.json` ending with a hash the tools
produced is not a violation.

**What the history sees while the run goes** (18.1 item 3). From the
before-picture until the run's attribution is written, the manager tells the
history that the project has a run (`history.watch_run`), and any scan in this
engine -- the watcher's above all -- credits to the run what the run changed:
`_watch` answers for a file from the before-picture (not there, or other
bytes) and from the engine's own writes (the person's saves keep their name).
The attribution after the run then adds nothing for a content that is already
there ("seen during run <id>").

**An out-of-bounds run** (18.1 item 4) is `out_of_bounds`, not what it would
have read (`outcome` keeps that: `done`, `ended`, ...). The task's done mark is
the agent's and is left as it is; the run is what warns.

**Undo** (17.6) restores from the before-picture's copies, never over a later
change (nor over a path the person saved during the run), and takes the task's
done mark back logically rather than restoring `research/tasks.json`. A
created file whose content is a deleted file with no copy is that file renamed:
it is moved back, not away. Directories the move emptied are removed -- unless
the before-picture holds them (`dirs`, 18.1 item 5): a folder that existed
before the run survives, and a picture too cut to say removes none.

**Undo says what it will do, and asks** (18.1 item 5). `revert_plan` runs the
very code of `revert` with `dry=True` -- the same checks in the same order, no
write -- so the list the person sees is what the undo would do at that moment:
`restore` (changed or deleted files, and the task's done mark), `remove` (created
files, moved away, never deleted) and `cannot`, each with the reason of 17.6.
`revert(files=)` then acts on the paths the person left checked and leaves the
rest as they are, listed in the run's `revert.skipped` with the reason `kept`;
without `files` it undoes all that can be undone. A path the plan does not list is
never acted on, whatever `files` says.

**At engine start** (18.1 item 6) `recover_all` recovers the runs of every project
this Mac keeps runs for that were left preparing or running, with the same rules as
`_recover` (a program is ended only when its pid and start time are the recorded
ones), so that nothing waits for someone to open the project's page.

**Who is who after a crash.** `run.json` carries the start time of the
engine process (`engine_started`) and of the program (`program_started`), so
a recycled pid is neither "another live engine" nor "the program" (`_recover`).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import selectors
import shutil
import signal
import subprocess
import threading
import time
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from researchos import history, names, paths, workspace
from researchos.agents import brief, guard, labels, profiles, runners
from researchos.agents import runs as run_store
from researchos.agents import snapshot
from researchos.records import files as record_files
from researchos.records import situation
from researchos.records.lock import ProjectLockError

logger = logging.getLogger(__name__)

#: The project's record folders (never the agent's to change; their writes are not attributed to it).
_RECORD_DIRS = frozenset({names.PROJECT_DIRNAME.casefold(), names.LEGACY_PROJECT_DIRNAME.casefold()})
#: The same and git's folder: not the change history's business (git's own history is git's), and not a folder
#: an undo takes apart.
_RECORD_AND_GIT_DIRS = _RECORD_DIRS | {snapshot.GIT_DIR}

#: How long the program gets to end after SIGTERM before what is left of its tree is killed (17.3).
KILL_GRACE_S = 10.0
#: `run.json` is rewritten at most this often while the program runs.
PERSIST_INTERVAL_S = 2.0
#: After the program exited, how long its standard output is still read: a background process it started
#: may hold the pipe open for ever.
EXIT_DRAIN_S = 2.0
#: How long `stop` waits for the run to reach its final state before returning the state as it is.
STOP_WAIT_S = 30.0
SHUTDOWN_WAIT_S = 60.0
MAX_LINE_BYTES = 4 * 1024 * 1024
STEPS_LIMIT = 500
STDERR_TAIL_BYTES = 4096
REFUSALS_TAIL_BYTES = 64 * 1024
SUMMARY_LIMIT = 2000
MESSAGE_LIMIT = 8000
HUMAN_PATHS_LIMIT = 5000
#: The most paths an undo request may name (a picture holds at most 50,000 files, deletions included).
UNDO_FILES_LIMIT = 200_000
#: Attribution in the change history: at most this many files per run, none larger than this.
HISTORY_FILES_LIMIT = 2000
HISTORY_FILE_SIZE_LIMIT = 1024 * 1024 * 1024
#: A run that has been "active" for longer than the longest time limit plus a margin is stale whoever claims it.
STALE_AFTER = timedelta(minutes=profiles.TIMEOUT_RANGE[1] + 30)
PS_TIMEOUT_S = 10
#: While the program runs, its descendants are sampled at most this far apart (the first samples are denser).
SAMPLE_INTERVAL_S = 2.0
#: Leftover processes get this long after SIGTERM before SIGKILL.
SWEEP_GRACE_S = 1.0
#: At most this many descendants are remembered (those still alive are kept first).
TREE_LIMIT = 10000

REPLY_STATES = frozenset({"needs_input", "done", "ended", "failed", "stopped", "interrupted"})
TASKS_PATH = snapshot.TASKS_PATH

# -- errors ----------------------------------------------------------------------------------------

#: code -> (HTTP status, English message, Chinese message). The Chinese is the product's language (8.8).
_ERRORS: dict[str, tuple[int, str, str]] = {
    "mcp_missing": (409, "Running an AI agent needs the optional mcp package, which is not installed here",
                    "运行 AI 执行者需要 mcp 组件，当前还没有安装"),
    "runner_missing": (409, "The agent's program was not found on this Mac",
                       "这台电脑上找不到这个 AI 执行者的程序"),
    "task_missing": (404, "That task does not exist; reload the page", "找不到这个任务，请刷新页面"),
    "not_agent_task": (400, "The task's owner is not an AI agent", "这个任务的负责人不是 AI 执行者"),
    "task_done": (409, "The task is already done; clear its done mark before running it again",
                  "这个任务已经完成；取消完成标记后才能再次运行"),
    "busy": (409, "Another agent run in this project is still going", "这个项目里还有一次 AI 执行没有结束"),
    "read_only": (403, "This project is read-only for you, so an agent cannot run in it",
                  "当前项目对你是只读的，不能在这里运行 AI 执行者"),
    "run_missing": (404, "That run does not exist", "找不到这次运行"),
    "run_active": (409, "That run is still going", "这次运行还没有结束"),
    "not_latest": (409, "Only the latest run of a task can be continued", "只有任务的最近一次运行才能继续"),
    "no_session": (409, "That run has no conversation to continue", "这次运行没有可以继续的对话"),
    "reverted": (409, "That run was already undone", "这次运行已经撤回"),
    "later_run": (409, "A later run touched the same files; undo that one first",
                  "之后的运行改动了相同的文件，请先撤回那一次"),
    "not_finished": (409, "That run has not finished", "这次运行还没有结束"),
    "not_revertible": (409, "That run can no longer be undone (its copies are gone or it changed nothing)",
                       "这次运行已无法撤回（备份已不在，或它没有改动任何文件）"),
    "invalid": (400, "The request is not valid", "请求的内容不对"),
    "start_failed": (500, "The run could not be started", "无法开始这次运行"),
}


class AgentError(Exception):
    """A refusal the page can show: `code` (the `agent_<code>` state), `message`
    (English), `message_zh`, and the HTTP `status` to answer with."""

    def __init__(self, code: str, message: str | None = None, *, message_zh: str | None = None) -> None:
        status, en, zh = _ERRORS[code]
        super().__init__(message or en)
        self.code = code
        self.message = message or en
        self.message_zh = message_zh or zh
        self.status = status


def _now() -> str:
    return run_store.now_iso()


def _lang(lang: object) -> str:
    return "en" if isinstance(lang, str) and lang.lower().startswith("en") else "zh"


def _key(rel: str) -> str:
    """The form paths are compared in: NFC, case-folded (macOS is case-insensitive and may store names decomposed)."""
    return unicodedata.normalize("NFC", rel).casefold()


def _warn_if_out_of_bounds(state: str, violations: object) -> tuple[str, str | None]:
    """`(state, outcome)` of a run that ended as `state` with these out-of-bounds changes (18.1 item 4): any
    change makes the state `out_of_bounds`, and `outcome` keeps the state it would have read; none leaves both."""
    if violations and state != "out_of_bounds":
        return "out_of_bounds", state
    return state, None


# -- processes -------------------------------------------------------------------------------------


def _ps_table() -> dict[int, tuple[int, str, str]]:
    """`{pid: (ppid, stat, start time)}` for every process. The start time makes
    (pid, start time) identify a process even after the system reuses a pid.
    Empty when `ps` cannot be run."""
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,stat=,lstart="], stdin=subprocess.DEVNULL,
                             capture_output=True, timeout=PS_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError):
        return {}
    table: dict[int, tuple[int, str, str]] = {}
    for line in out.stdout.decode("utf-8", "replace").splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        try:
            table[int(parts[0])] = (int(parts[1]), parts[2], " ".join(parts[3].split()))
        except ValueError:
            continue
    return table


def _descendants(table: dict[int, tuple[int, str, str]], pid: int) -> list[int]:
    """Every process below `pid`, parents before children (breadth first)."""
    children: dict[int, list[int]] = {}
    for child, (parent, _stat, _start) in table.items():
        children.setdefault(parent, []).append(child)
    found: list[int] = []
    seen = {pid}
    queue = [pid]
    while queue:
        for child in children.get(queue.pop(0), ()):
            if child not in seen:
                seen.add(child)
                found.append(child)
                queue.append(child)
    return found


def _is_zombie(pid: int) -> bool:
    try:
        data = Path(f"/proc/{pid}/stat").read_text(errors="replace")
        return data.rsplit(")", 1)[1].split()[0] == "Z"
    except (OSError, IndexError):
        pass
    try:
        out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], stdin=subprocess.DEVNULL, capture_output=True,
                             timeout=PS_TIMEOUT_S, check=False)
        return out.stdout.decode("utf-8", "replace").strip().startswith("Z")
    except (OSError, subprocess.SubprocessError):
        return False


def _pid_alive(pid: object) -> bool:
    """Whether `pid` is a running process (a zombie is not)."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return not _is_zombie(pid)


def _command_of(pid: int) -> str:
    try:
        out = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], stdin=subprocess.DEVNULL,
                             capture_output=True, timeout=PS_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.decode("utf-8", "replace").strip()


def _start_of(pid: object) -> str | None:
    """The start time of process `pid` as `ps` prints it (the same form `_ps_table` has), None when it is
    not running or `ps` cannot be run."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], stdin=subprocess.DEVNULL,
                             capture_output=True, timeout=PS_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return " ".join(out.stdout.decode("utf-8", "replace").split()) or None


def _signal(pid: int, sig: int) -> None:
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass
    except OSError as exc:  # pragma: no cover -- nothing else is expected from kill(2)
        logger.warning("could not signal %s: %s", pid, exc)


def _same_alive(table: dict[int, tuple[int, str, str]], pid: int, start: str) -> bool:
    entry = table.get(pid)
    return entry is not None and entry[2] == start and not entry[1].startswith("Z")


def kill_tree(pid: int, *, proc: subprocess.Popen | None = None, grace: float | None = None) -> list[int]:
    """Stop the program `pid` and everything it started (17.3): note the
    descendants, SIGTERM the program, wait up to `grace` seconds for it to
    end (`proc` is its Popen when it is our child; otherwise it is polled),
    then SIGKILL the noted tree that is still alive (deepest first, only
    processes whose start time still matches), the program, and its process
    group. Returns the pids that were SIGKILLed. Safe to call twice."""
    grace = KILL_GRACE_S if grace is None else grace

    def exited() -> bool:
        return proc.poll() is not None if proc is not None else not _pid_alive(pid)

    table = _ps_table()
    tree = _descendants(table, pid)
    stamps = {p: table[p][2] for p in tree}
    leader_start = table[pid][2] if pid in table else None
    _signal(pid, signal.SIGTERM)
    deadline = time.monotonic() + max(0.0, grace)
    while time.monotonic() < deadline and not exited():
        time.sleep(0.05)
    table = _ps_table()
    for p in _descendants(table, pid):  # started while it was ending
        if p not in stamps:
            tree.append(p)
            stamps[p] = table[p][2]
    victims = [p for p in reversed(tree) if _same_alive(table, p, stamps[p])]
    for p in victims:
        _signal(p, signal.SIGKILL)
    if not exited() and (proc is not None or leader_start is None or _same_alive(table, pid, leader_start)):
        _signal(pid, signal.SIGKILL)
    if proc is not None or (leader_start is not None and _same_alive(table, pid, leader_start)):
        # the program's own group (it was started with start_new_session): what it forked without
        # leaving the group; a group id cannot be reused while a member lives. Without our own Popen
        # the leader must be the process noted at the start -- never a group of a reused pid.
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    settle = time.monotonic() + 3.0
    while victims and time.monotonic() < settle:
        table = _ps_table()
        if not any(_same_alive(table, p, stamps[p]) for p in victims):
            break
        time.sleep(0.05)
    return victims


def sweep_tree(stamps: dict[int, str], *, group: int | None = None, grace: float | None = None) -> int:
    """End what a program that has exited left running (17.3): every process of `stamps`
    (`{pid: start time}`) that is still alive with that start time gets SIGTERM, then SIGKILL after
    `grace` seconds (children before parents); then the program's process group `group` gets SIGKILL.
    Returns the number of processes that were alive when this started."""
    grace = SWEEP_GRACE_S if grace is None else grace
    table = _ps_table()
    alive = [p for p, start in stamps.items() if _same_alive(table, p, start)]
    for p in reversed(alive):
        _signal(p, signal.SIGTERM)
    deadline = time.monotonic() + max(0.0, grace)
    while alive and time.monotonic() < deadline:
        time.sleep(0.05)
        table = _ps_table()
        if not any(_same_alive(table, p, stamps[p]) for p in alive):
            break
    table = _ps_table()
    for p in reversed(alive):
        if _same_alive(table, p, stamps[p]):
            _signal(p, signal.SIGKILL)
    if group is not None:
        try:
            os.killpg(group, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    return len(alive)


# -- the summary the page reads --------------------------------------------------------------------


def _change_count(changes: object) -> int:
    if not isinstance(changes, dict):
        return 0
    return sum(len(changes.get(k) or ()) for k in ("created", "modified", "deleted"))


def summary(run: dict, *, latest_of_task: bool, active: bool) -> dict:
    """What the page shows of a run: the identifying and state fields (`state`,
    and `outcome`, how the run ended apart from going out of bounds), counts
    for `violations` and `changes`, the number of files an undo left alone
    (`revert_skipped`, present once an undo was tried) and the three things the
    person may do with it (`can_stop`, `can_reply`, `can_revert`)."""
    state = run.get("state")
    outcome = run_store.outcome_of(run)
    finished = state not in run_store.ACTIVE_STATES
    reverted = bool(run.get("reverted"))
    changed = _change_count(run.get("changes"))
    out = {
        "id": run.get("id"), "task_id": run.get("task_id"), "task_text": run.get("task_text"),
        "owner": run.get("owner"), "label": run.get("label"), "runner": run.get("runner"),
        "model": run.get("model"), "state": state, "outcome": outcome, "reverted": reverted,
        "kind": run.get("kind"),
        "parent": run.get("parent"), "created": run.get("created"), "started": run.get("started"),
        "ended": run.get("ended"), "question": run.get("question"), "report": run.get("report"),
        "error": run.get("error"), "cost_usd": run.get("cost_usd"), "by": run.get("by"),
        "by_name": run.get("by_name"),
        "violations": len(run.get("violations") or ()), "changes": changed,
        "can_stop": bool(active),
        "can_reply": bool(latest_of_task and not active and not reverted and outcome in REPLY_STATES
                          and run.get("session_id")),
        "can_revert": bool(finished and not active and not reverted and run.get("revertible")
                           and (changed > 0 or run.get("report"))),
    }
    revert = run.get("revert")
    if isinstance(revert, dict) and isinstance(revert.get("skipped"), list):
        out["revert_skipped"] = len(revert["skipped"])  # a partial undo: the page says so and lists them
    return out


# -- one active run ----------------------------------------------------------------------------------


class _Active:
    """A run this manager is carrying out."""

    def __init__(self, run: dict, root: Path, program: str, *, task: dict, message: str | None,
                 resume_session: str | None) -> None:
        self.run = run
        self.run_id: str = run["id"]
        self.project_id: str = run["project_id"]
        self.runner: str = run["runner"]
        self.root = root
        self.program = program
        self.task = task
        self.message = message
        self.resume_session = resume_session
        self.lock = threading.Lock()  # proc and stop_reason
        self.kill_lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.stop_reason: str | None = None
        self.thread: threading.Thread | None = None
        self.timer: threading.Timer | None = None
        self.done = threading.Event()
        self.human: set[str] = set()
        #: path -> sha256 of what the engine last wrote there (files up to `snapshot.COPY_LIMIT`)
        self.human_sha: dict[str, str] = {}
        #: the same, by `_key` of the path: key -> sha256 of what the engine last wrote there (None: not known)
        self.human_keys: dict[str, str | None] = {}
        self.human_version = 0
        self.human_lock = threading.Lock()
        self.listener: Callable[[Path], None] | None = None
        #: what the history was told about this run (`_watch`), until `_release`
        self.seen: history.RunSeen | None = None
        #: the program's descendants seen while it ran: pid -> start time; and what `sweep_tree` ended
        self.tree: dict[int, str] = {}
        self.tree_lock = threading.Lock()
        self.drained = False  # the program's output is read to its end (or given up on)
        self.leftovers = 0
        self.timeout_minutes: float = float(profiles.DEFAULT_TIMEOUT_MINUTES)

    @property
    def dir(self) -> Path:
        return run_store.run_dir(self.project_id, self.run_id)

    def human_paths(self) -> list[str]:
        with self.human_lock:
            return sorted(self.human)

    def human_hashes(self) -> dict[str, str]:
        with self.human_lock:
            return dict(self.human_sha)


def _write_json(path: Path, data: object) -> None:
    record_files.durable_write(path, json.dumps(data, ensure_ascii=False).encode("utf-8"))


def _read_json(path: Path) -> Any:
    try:
        if paths.is_dataless(path):
            return None
        return json.loads(path.read_bytes().decode("utf-8-sig"))
    except (OSError, ValueError):
        return None


def _read_text(path: Path, limit: int | None = None, *, tail: bool = False) -> str:
    try:
        if paths.is_dataless(path):
            return ""
        with open(path, "rb") as handle:
            if limit is not None and tail:
                handle.seek(max(0, os.fstat(handle.fileno()).st_size - limit))
                data = handle.read()
            else:
                data = handle.read(limit) if limit is not None else handle.read()
            return data.decode("utf-8", "replace")
    except OSError:
        return ""


def _sha256(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _started_ns(run: dict) -> int:
    """When the run's program started (else when the run was created), in ns since the epoch; 0 when neither
    is a time: then nothing is judged older than the run."""
    for key in ("started", "created"):
        try:
            stamp = datetime.fromisoformat(str(run.get(key)).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            return int(stamp.timestamp() * 1_000_000_000)
        except (ValueError, TypeError, OverflowError, OSError):
            continue
    return 0


def _inside(real_root: str, path: str) -> bool:
    return path == real_root or path.startswith(real_root.rstrip(os.sep) + os.sep)


def _room_for(folder: str) -> bool:
    """Whether a folder could be made at `folder`: it is one already, or the nearest part of it that exists is."""
    while folder and not os.path.lexists(folder):
        above = os.path.dirname(folder)
        if above == folder:
            return False
        folder = above
    return os.path.isdir(folder)


def _folders_before(before: dict) -> set[str] | None:
    """The folders that existed before the run, which undo never removes (18.1 item 5): the ones the picture
    recorded (`dirs`, empty ones included) and the ones its files stand in (all a picture taken before `dirs`
    existed has). None when the picture cannot say -- it was cut at the file or folder limit -- and undo then
    removes no folder at all."""
    if before.get("partial") or before.get("dirs_partial"):
        return None
    folders = {"/".join(p.split("/")[:i]) for p in (before.get("files") or ()) for i in range(1, p.count("/") + 1)}
    folders.update(d for d in before.get("dirs") or () if isinstance(d, str))
    return folders


def _local_time(moment: float | str | None) -> str | None:
    """A time as the person reads it on this Mac: ISO, in local time, to the second (`2026-10-22T10:14:05+02:00`) --
    from a file's modification time (seconds since the epoch) or a run's stored UTC stamp. None when it is none."""
    try:
        if isinstance(moment, str):
            stamp = datetime.fromisoformat(moment.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
        elif isinstance(moment, (int, float)):
            stamp = datetime.fromtimestamp(moment, timezone.utc)
        else:
            return None
        return stamp.astimezone().isoformat(timespec="seconds")
    except (ValueError, OverflowError, OSError):
        return None


class AgentManager:
    """Carries out agent runs for the projects the engine serves. See the
    module docstring. `on_finish(root, run)` is called (from the run's
    thread, after the final state is saved and the project is free again)
    for every run that ends, however it ends; `engine_port` is written into
    the hook's guard so a command that names the engine's address is
    refused (17.5)."""

    def __init__(self, *, on_finish: Callable[[Path, dict], None] | None = None,
                 engine_port: int | None = None) -> None:
        self.on_finish = on_finish
        self.engine_port = engine_port
        self._lock = threading.RLock()
        self._recover_lock = threading.Lock()
        self._active: dict[str, _Active] = {}
        self._reverting: set[str] = set()
        self._closing = False
        self._engine_stamp: str | None = None

    def _engine_started(self) -> str | None:
        """The start time of this very process, read once (`engine_started` in `run.json`)."""
        if self._engine_stamp is None:
            self._engine_stamp = _start_of(os.getpid())
        return self._engine_stamp

    # -- validation helpers ---------------------------------------------------------------------

    @staticmethod
    def _check_ids(project_id: object, run_id: object = None) -> None:
        try:
            run_store.project_runs_dir(project_id)  # type: ignore[arg-type]
            if run_id is not None:
                run_store.run_dir(project_id, run_id)  # type: ignore[arg-type]
        except ValueError as exc:
            raise AgentError("invalid", str(exc)) from exc

    @staticmethod
    def _writable_root(root: Path | str, project_id: str) -> Path:
        """The resolved project folder, after the same write check a task save makes."""
        folder = Path(root)
        try:
            resolved = folder.resolve()
            if not resolved.is_dir():
                raise AgentError("invalid", "The project folder is missing")
            pid = workspace._require_write(resolved)  # noqa: SLF001 -- the one write rule of the desk
        except workspace.WorkspaceError as exc:
            raise AgentError("read_only", str(exc)) from exc
        except OSError as exc:
            raise AgentError("invalid", f"The project folder cannot be read: {exc}") from exc
        if pid != project_id:
            raise AgentError("invalid", "The project folder is not the project this request names")
        return resolved

    @staticmethod
    def _tasks(root: Path) -> tuple[list[dict], str | None]:
        try:
            return workspace.read_tasks(root)
        except workspace.WorkspaceError as exc:
            raise AgentError("invalid", f"The task list cannot be read: {exc}") from exc

    @staticmethod
    def _agent_of(task: dict) -> labels.Agent:
        try:
            agent = labels.parse_owner(task.get("owner"))
        except ValueError:
            agent = None
        if agent is None:
            raise AgentError("not_agent_task")
        return agent

    @staticmethod
    def _check_runner(runner: str) -> profiles.Found:
        if not profiles.mcp_available():
            raise AgentError("mcp_missing")
        found = profiles.find(runner)
        if not found.found or found.path is None:
            raise AgentError("runner_missing")
        return found

    def _load(self, project_id: str, run_id: str) -> dict:
        self._check_ids(project_id, run_id)
        run = run_store.load_run(project_id, run_id)
        if run is None:
            raise AgentError("run_missing")
        return run

    def _is_active(self, project_id: str, run_id: str) -> bool:
        with self._lock:
            act = self._active.get(project_id)
            return act is not None and act.run_id == run_id

    # -- starting -------------------------------------------------------------------------------

    def start(self, root: Path, project_id: str, task_id: str, *, by: str | None, by_name: str | None,
              lang: str) -> dict:
        """Run the agent that owns task `task_id`; returns the new run's summary at once
        (state `preparing`). Raises `AgentError`."""
        self._check_ids(project_id)
        root = self._writable_root(root, project_id)
        tasks, _revision = self._tasks(root)
        task = next((t for t in tasks if t["id"] == task_id), None)
        if task is None:
            raise AgentError("task_missing")
        agent = self._agent_of(task)
        if task.get("done"):
            raise AgentError("task_done")
        found = self._check_runner(agent.runner)
        return self._launch(root, project_id, task, agent, found, by=by, by_name=by_name, lang=_lang(lang),
                            kind="start", parent=None, message=None, session=None)

    def reply(self, root: Path, project_id: str, run_id: str, message: str, *, by: str | None,
              by_name: str | None, lang: str) -> dict:
        """Continue the conversation of the task's latest run: an answer to its question
        (`kind` `answer`) or the task sent back (`send_back`, which first clears a done mark).
        Returns the new run's summary."""
        self._check_ids(project_id, run_id)
        root = self._writable_root(root, project_id)
        self._recover(project_id)
        parent = self._load(project_id, run_id)
        with self._lock:
            act = self._active.get(project_id)
        if act is not None:
            raise AgentError("run_active" if act.run_id == run_id else "busy")
        if parent.get("state") in run_store.ACTIVE_STATES:
            raise AgentError("run_active")
        others = run_store.list_runs(project_id)
        newest = next((r for r in others if r.get("task_id") == parent.get("task_id")), None)
        if newest is None or newest.get("id") != run_id:
            raise AgentError("not_latest")
        if parent.get("reverted"):
            raise AgentError("reverted")
        outcome = run_store.outcome_of(parent)
        if outcome not in REPLY_STATES:
            raise AgentError("not_finished")
        session = parent.get("session_id")
        if not isinstance(session, str) or not session:
            raise AgentError("no_session")
        text = message.strip() if isinstance(message, str) else ""
        if not text or len(text) > MESSAGE_LIMIT:
            raise AgentError("invalid", f"The message must be 1 to {MESSAGE_LIMIT} characters",
                             message_zh=f"内容需要 1 到 {MESSAGE_LIMIT} 个字符")
        tasks, revision = self._tasks(root)
        task = next((t for t in tasks if t["id"] == parent.get("task_id")), None)
        if task is None:
            raise AgentError("task_missing")
        agent = self._agent_of(task)
        if agent.owner != parent.get("owner"):
            raise AgentError("not_agent_task", "The task now belongs to a different agent; start a new run instead",
                             message_zh="这个任务现在由另一个 AI 执行者负责，请重新运行")
        found = self._check_runner(agent.runner)
        kind = "answer" if outcome == "needs_input" else "send_back"
        cleared = False
        if kind == "send_back" and task.get("done"):
            # 17.3: 打回 on a done task clears its done mark first (a human write through the desk; `report` stays)
            reopened = [dict(t, done=False) if t["id"] == task["id"] else t for t in tasks]
            try:
                workspace.save_tasks(root, {"tasks": reopened, "revision": revision})
            except workspace.WorkspaceError as exc:
                raise AgentError("start_failed", f"The task's done mark could not be cleared: {exc}") from exc
            except ProjectLockError as exc:  # the project is being written by something else: try again shortly
                raise AgentError("busy", f"The project is being written by something else: {exc}") from exc
            except situation.WriteRefused as exc:  # read-only, moved, waiting for migration: nothing written
                raise AgentError("read_only", str(exc)) from exc
            task = dict(task, done=False)
            cleared = True
        try:
            return self._launch(root, project_id, task, agent, found, by=by, by_name=by_name, lang=_lang(lang),
                                kind=kind, parent=run_id, message=text, session=session)
        except BaseException:
            if cleared:  # the run did not start: give the person's task its done mark back
                with contextlib.suppress(Exception):
                    latest, rev = workspace.read_tasks(root)
                    workspace.save_tasks(root, {"tasks": [dict(t, done=True) if t["id"] == task["id"] else t
                                                          for t in latest], "revision": rev})
            raise

    def _launch(self, root: Path, project_id: str, task: dict, agent: labels.Agent, found: profiles.Found, *,
                by: str | None, by_name: str | None, lang: str, kind: str, parent: str | None,
                message: str | None, session: str | None) -> dict:
        assert found.path is not None
        self._recover(project_id)
        engine_started = self._engine_started()
        run: dict[str, Any] = {
            "id": None, "project_id": project_id, "root": str(root), "task_id": task["id"],
            "task_text": task["text"], "task_due": task.get("due") or None,
            "runner": agent.runner, "model": agent.model, "owner": agent.owner, "label": agent.label,
            "by": by if isinstance(by, str) and by else None,
            "by_name": by_name if isinstance(by_name, str) and by_name else None,
            "lang": lang, "kind": kind, "parent": parent, "message": message,
            "state": "preparing", "outcome": None, "reverted": False, "reverted_at": None, "reverted_by": None,
            "created": _now(), "started": None, "ended": None, "session_id": session, "pid": None,
            "engine_pid": os.getpid(), "engine_started": engine_started, "program_started": None,
            "leftovers": 0, "exit_code": None, "error": None, "question": None, "report": None,
            "summary": None, "cost_usd": None, "model_answered": None,
            "changes": {"created": [], "modified": [], "deleted": [], "by_person": []},
            "violations": [], "snapshot": None, "revertible": False,
            "task_done_before": bool(task.get("done")), "task_report_before": task.get("report") or None,
            "tool_paths": [], "human_paths": [], "human_sha": {}, "program": found.path,
        }
        with self._lock:
            if self._closing:
                raise AgentError("start_failed", "Research OS is shutting down", message_zh="Research OS 正在退出")
            if project_id in self._active or project_id in self._reverting or self._foreign_active(project_id):
                raise AgentError("busy")
            run["id"] = run_store.new_run_id()
            try:
                run_store.create_run(project_id, run)
            except (OSError, ValueError) as exc:
                raise AgentError("start_failed", f"The run could not be recorded: {exc}") from exc
            act = _Active(run, root, found.path, task=task, message=message, resume_session=session)
            self._active[project_id] = act
            try:
                act.thread = threading.Thread(target=self._thread_main, args=(act,), daemon=True,
                                              name=f"researchos-agent-{run['id']}")
                act.thread.start()
            except BaseException as exc:
                del self._active[project_id]
                self._fail_record(project_id, run["id"], "start_failed", f"The run's thread did not start: {exc}")
                raise AgentError("start_failed", f"The run could not be started: {exc}") from exc
        return summary(run, latest_of_task=True, active=True)

    # -- the run's thread -----------------------------------------------------------------------

    def _thread_main(self, act: _Active) -> None:
        try:
            self._execute(act)
        except BaseException as exc:  # noqa: BLE001 -- documented: a run never stays active because of a bug
            logger.exception("agent run %s failed unexpectedly", act.run_id)
            self._abort(act, exc)
        finally:
            self._cleanup(act)

    def _abort(self, act: _Active, exc: BaseException) -> None:
        """The thread crashed: make sure the program is gone and the run is terminal."""
        with act.lock:
            proc = act.proc
        if proc is not None and proc.poll() is None:
            with contextlib.suppress(Exception), act.kill_lock:
                kill_tree(proc.pid, proc=proc)
        self._fail_record(act.project_id, act.run_id, "internal", f"{type(exc).__name__}: {exc}")

    @staticmethod
    def _fail_record(project_id: str, run_id: str, code: str, detail: str) -> None:
        """Mark a run failed unless it already reached a terminal state."""
        try:
            run_store.update_run_if_active(project_id, run_id, state="failed", ended=_now(),
                                           error={"code": code, "detail": detail[:2000]})
        except Exception:  # noqa: BLE001 -- nothing more can be done; the next listing marks it interrupted
            logger.exception("could not record the failure of agent run %s", run_id)

    def _cleanup(self, act: _Active) -> None:
        if act.timer is not None:
            act.timer.cancel()
        if act.listener is not None:
            record_files.remove_write_listener(act.listener)
        self._release(act)
        try:
            run_store.prune(act.project_id)
        except Exception:  # noqa: BLE001
            logger.exception("could not prune agent runs of %s", act.project_id)
        with self._lock:
            if self._active.get(act.project_id) is act:
                del self._active[act.project_id]
        act.done.set()
        if self.on_finish is not None:
            try:
                run = run_store.load_run(act.project_id, act.run_id)
                if run is not None:
                    self.on_finish(act.root, run)
            except Exception:  # noqa: BLE001
                logger.exception("on_finish failed for agent run %s", act.run_id)

    def _update(self, act: _Active, **changes: Any) -> None:
        try:
            run_store.update_run(act.project_id, act.run_id, **changes)
        except (KeyError, OSError, ValueError):
            logger.exception("could not update agent run %s", act.run_id)

    def _register_listener(self, act: _Active) -> None:
        real_root = os.path.realpath(act.root)

        def on_write(path: Path) -> None:
            try:
                head, tail = os.path.split(os.fspath(path))
                full = os.path.join(os.path.realpath(head), tail)
            except (OSError, ValueError, TypeError):
                return
            if not _inside(real_root, full) or full == real_root:
                return
            rel = os.path.relpath(full, real_root).replace(os.sep, "/")
            sha = None
            try:  # what the engine wrote: a protected path the person saved may end with exactly this
                if os.path.getsize(full) <= snapshot.COPY_LIMIT and os.path.isfile(full):
                    sha = _sha256(Path(full))
            except OSError:
                sha = None
            with act.human_lock:
                if len(act.human) < HUMAN_PATHS_LIMIT or rel in act.human:
                    act.human.add(rel)
                    act.human_keys[_key(rel)] = sha
                    if sha is not None:
                        act.human_sha[rel] = sha
                    else:
                        act.human_sha.pop(rel, None)
                    act.human_version += 1

        act.listener = on_write
        record_files.add_write_listener(on_write)

    def _watch(self, act: _Active, before: dict) -> None:
        """Tell the history the run is going on (module docstring): from here the scans of this engine
        credit to it what it changed. Not when the owner is no agent that can be named."""
        try:
            agent = labels.parse_owner(act.run.get("owner"))
        except ValueError:
            agent = None
        if agent is None:
            return
        files = before.get("files") or {}
        partial = bool(before.get("partial"))
        began = time.time_ns()

        def owns(rel: str, entry: Any) -> bool:
            """Whether the content now at `rel` is the run's: not what the person last saved through Research OS
            there, and not what the before-picture holds (a cut picture knows only what is older than the run)."""
            with act.human_lock:
                known = _key(rel) in act.human_keys
                sha = act.human_keys.get(_key(rel))
            if known and (sha is None or entry.get("sha256") == sha):
                return False
            was = files.get(rel)
            if was is None:
                return not partial or int(entry.get("mtime_ns") or 0) >= began
            return not snapshot.same(was, entry)

        act.seen = history.RunSeen(who=labels.who_value(agent, act.run.get("by")), run=act.run_id, owns=owns)
        history.watch_run(act.root, act.seen)

    @staticmethod
    def _release(act: _Active) -> None:
        """The history is no longer told (idempotent)."""
        if act.seen is not None:
            history.release_run(act.root, act.seen)
            act.seen = None

    def _documents(self, root: Path, lang: str) -> list[dict]:
        try:
            data = workspace.overview(root, lang)
        except Exception:  # noqa: BLE001 -- the instruction is useful without the list
            return []
        return [{"path": d["path"], "kind": d["kind"], "title": d.get("title") or ""}
                for d in data.get("documents", ())
                if isinstance(d, dict) and d.get("kind") in ("note", "result") and not d.get("error")]

    def _overview(self, root: Path, lang: str) -> str | None:
        try:
            content = workspace.document(root, workspace.OVERVIEW_PATH, lang).get("content")
        except Exception:  # noqa: BLE001
            return None
        return content if isinstance(content, str) and content.strip() else None

    def _execute(self, act: _Active) -> None:
        run = act.run
        root, rd = act.root, act.dir
        runner = act.runner
        lang = run["lang"]
        self._register_listener(act)
        if act.stop_reason is not None:  # stopped before anything was taken
            self._finish(act, before=None, stream=None, exit_code=None, launch_error=None)
            return

        before = snapshot.take(root, copy_to=rd / "before")
        prot = snapshot.protected(root, before)
        before["protected"] = prot
        _write_json(rd / "before.json", before)
        self._watch(act, before)
        guard.write_guard_file(rd, root=root, prot=prot, engine_port=self.engine_port)
        if runner == "claude-code":
            # 17.5: Claude Code lets a call through when its hook cannot run; the hook is tried on a call
            # it must refuse first, and the program is not started when it does not
            refused = runners.hook_canary(rd)
            if refused is not None:
                logger.warning("the hook of agent run %s did not refuse its probe: %s", act.run_id, refused)
                self._finish(act, before=before, stream=None, exit_code=None,
                             launch_error={"code": "guard_failed", "detail": refused[:2000]})
                return

        rules = brief.rules(lang=lang, runner=runner, prot=prot)
        if run["kind"] == "start":
            text = brief.instruction(lang=lang, task={"text": run["task_text"], "due": run.get("task_due")},
                                     project_name=root.name, overview=self._overview(root, lang),
                                     documents=self._documents(root, lang))
        else:
            text = brief.continuation(lang=lang, kind=run["kind"], message=act.message or "")
        combined = rules + "\n\n" + text
        record_files.durable_write(rd / "instruction.txt", combined.encode("utf-8"))

        home = str(paths.researchos_home())
        timeout_minutes = float(profiles.timeout_minutes())
        act.timeout_minutes = timeout_minutes
        if runner == "claude-code":
            resume = act.resume_session is not None
            session = act.resume_session or str(uuid.uuid4())
            launch = runners.claude_launch(
                program=act.program, model=run["model"], root=root, run_dir=rd, run_id=act.run_id,
                session_id=session, resume=resume, prompt=text, rules=rules,
                max_budget_usd=profiles.max_budget_usd(), home=home, base_env=os.environ)
            stream: runners.ClaudeStream | runners.OpenCodeStream = runners.ClaudeStream()
        else:
            launch = runners.opencode_launch(
                program=act.program, model=run["model"], root=root, run_dir=rd, run_id=act.run_id,
                session_id=act.resume_session, prompt=combined, home=home, base_env=os.environ)
            stream = runners.OpenCodeStream()
        for name, content in launch.files.items():
            (rd / name).write_text(content, encoding="utf-8")
        self._update(act, env_removed=launch.removed_env)  # the names, never the values (18.1 item 2): the run log lists them

        launch_error: dict | None = None
        proc: subprocess.Popen | None = None
        started_at = _now()  # before the program exists: a partial picture judges "created" by this time
        with act.lock:
            if act.stop_reason is None:
                try:
                    with open(rd / "stderr.log", "ab") as errlog:
                        proc = subprocess.Popen(launch.argv, cwd=launch.cwd, env=launch.env, stdin=subprocess.PIPE,
                                                stdout=subprocess.PIPE, stderr=errlog, bufsize=0,
                                                start_new_session=True)
                except (OSError, ValueError) as exc:
                    launch_error = {"code": "start_failed", "detail": f"{type(exc).__name__}: {exc}"[:2000]}
                else:
                    act.proc = proc
        exit_code: int | None = None
        if proc is not None:
            self._update(act, state="running", pid=proc.pid, started=started_at,
                         program_started=_start_of(proc.pid))
            threading.Thread(target=self._feed_stdin, args=(proc, launch.stdin.encode("utf-8")), daemon=True,
                             name=f"researchos-agent-stdin-{act.run_id}").start()
            act.timer = threading.Timer(max(0.1, timeout_minutes * 60.0), self._terminate, args=(act, "timeout"))
            act.timer.daemon = True
            act.timer.start()
            try:
                self._pump(act, proc, stream, rd / "events.jsonl")
            except Exception as exc:  # noqa: BLE001 -- the program must not outlive a bug in reading it
                logger.exception("reading the output of agent run %s failed", act.run_id)
                launch_error = {"code": "internal", "detail": f"{type(exc).__name__}: {exc}"[:2000]}
                with act.kill_lock:
                    kill_tree(proc.pid, proc=proc)
            finally:
                exit_code = proc.wait()
                with act.lock:
                    act.drained = True
                # what the program left running goes before the after-picture (17.3): nothing it still
                # writes can then pass for someone else's change
                with contextlib.suppress(Exception), act.kill_lock:
                    act.leftovers += self._sweep(act, proc)
                with contextlib.suppress(OSError):
                    if proc.stdout is not None:
                        proc.stdout.close()
        self._finish(act, before=before, stream=stream, exit_code=exit_code, launch_error=launch_error)

    @staticmethod
    def _feed_stdin(proc: subprocess.Popen, data: bytes) -> None:
        """Write the prompt and close; on its own thread so a program that does not read
        its input cannot block the run, and one that exits early is not an error."""
        stdin = proc.stdin
        if stdin is None:
            return
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(stdin.fileno(), view):]
        except (OSError, ValueError):
            pass
        finally:
            with contextlib.suppress(OSError, ValueError):
                stdin.close()

    @staticmethod
    def _sample(act: _Active, proc: subprocess.Popen) -> None:
        """Note the program's descendants (pid and start time) for `_sweep`: those still alive from earlier
        samples stay even when their parent has gone since (they are orphans of the program then)."""
        table = _ps_table()
        if not table:
            return
        current = {p: table[p][2] for p in _descendants(table, proc.pid) if not table[p][1].startswith("Z")}
        with act.tree_lock:
            kept = {p: start for p, start in act.tree.items() if _same_alive(table, p, start)}
            kept.update(current)
            if len(kept) > TREE_LIMIT:
                kept = dict(list(kept.items())[-TREE_LIMIT:])
            act.tree = kept

    @staticmethod
    def _sweep(act: _Active, proc: subprocess.Popen) -> int:
        """End the noted descendants of the (exited) program that are still alive, and its process group.
        Returns how many were alive. The caller holds `act.kill_lock`."""
        with act.tree_lock:
            stamps = dict(act.tree)
        return sweep_tree(stamps, group=proc.pid)

    def _pump(self, act: _Active, proc: subprocess.Popen, stream: Any, events_path: Path) -> None:
        """Read the program's standard output until it ends: lines go to `events.jsonl` (while
        under `EVENTS_LIMIT`) and to the stream parser; `run.json` follows at most every
        `PERSIST_INTERVAL_S` (at once when the session id becomes known)."""
        assert proc.stdout is not None
        fd = proc.stdout.fileno()
        try:
            events = open(events_path, "ab", buffering=0)  # noqa: SIM115 -- closed in the finally below
        except OSError:
            events = None
            logger.warning("cannot write %s", events_path)
        written = 0
        truncated = False
        sent: tuple = (act.run.get("session_id"), None, None, 0)
        last_persist = time.monotonic()
        buffer = b""
        discarding = False
        exited_at: float | None = None

        def handle(raw: bytes) -> None:
            nonlocal written, truncated
            raw = raw.rstrip(b"\r\n")
            if not raw.strip():
                return
            if events is not None and not truncated:
                if written + len(raw) + 1 <= run_store.EVENTS_LIMIT:
                    try:
                        events.write(raw + b"\n")
                        written += len(raw) + 1
                    except OSError:
                        logger.warning("cannot write %s", events_path)
                else:
                    truncated = True
            try:
                stream.feed(raw.decode("utf-8", "replace"))
            except Exception:  # noqa: BLE001 -- a stream parser never stops a run
                logger.debug("stream parser failed on a line", exc_info=True)

        def persist(force: bool = False) -> None:
            nonlocal sent, last_persist
            with act.human_lock:
                count = act.human_version
            now = (stream.session_id or sent[0], stream.model, stream.cost_usd, count)
            if now == sent:
                return
            fresh_session = now[0] != sent[0]
            if not (force or fresh_session or time.monotonic() - last_persist >= PERSIST_INTERVAL_S):
                return
            self._update(act, session_id=now[0], model_answered=now[1], cost_usd=now[2],
                         human_paths=act.human_paths(), human_sha=act.human_hashes())
            sent, last_persist = now, time.monotonic()

        selector = selectors.DefaultSelector()  # not select(): the engine may hold descriptors above 1023
        selector.register(fd, selectors.EVENT_READ)
        began = time.monotonic()
        next_sample = began
        try:
            while True:
                now = time.monotonic()
                # whether the program has exited is looked at on EVERY turn, however much arrives: a
                # descendant that holds the pipe and writes often must not keep the run going (17.3)
                if exited_at is None and proc.poll() is not None:
                    exited_at = now
                if exited_at is not None and now - exited_at > EXIT_DRAIN_S:
                    break
                if exited_at is None and now >= next_sample:
                    with contextlib.suppress(Exception):
                        self._sample(act, proc)
                    next_sample = time.monotonic() + min(SAMPLE_INTERVAL_S, 0.25 + (now - began) / 4)
                if selector.select(0.25):
                    try:
                        chunk = os.read(fd, 65536)
                    except OSError:
                        break
                    if not chunk:
                        break
                    buffer += chunk
                    while True:
                        cut = buffer.find(b"\n")
                        if cut < 0:
                            break
                        line, buffer = buffer[:cut], buffer[cut + 1:]
                        if discarding:
                            discarding = False
                        elif len(line) <= MAX_LINE_BYTES:
                            handle(line)
                    if len(buffer) > MAX_LINE_BYTES:  # a line without an end: drop it, keep reading
                        buffer, discarding = b"", True
                persist()
            if buffer and not discarding and len(buffer) <= MAX_LINE_BYTES:
                handle(buffer)
        finally:
            selector.close()
            if events is not None:
                events.close()
            persist(force=True)
            if truncated:
                self._update(act, events_truncated=True)

    def _terminate(self, act: _Active, reason: str) -> bool:
        """Stop the program of `act` for `reason` (`stop`, `timeout`, `shutdown`): the reason is
        marked first, then the program and everything it started are ended (`kill_tree`). A program
        that has exited while its output is still being read (a descendant holds the pipe) is
        stopped the same way: the reason is marked and the noted tree is swept. False when there
        was nothing to stop (the program ended on its own and its output was read to the end).
        Idempotent."""
        with act.lock:
            proc = act.proc
            if proc is not None and proc.poll() is not None and act.drained:
                return False
            if act.stop_reason is None:
                act.stop_reason = reason
        if proc is None:
            return True  # not started yet; the thread sees the reason before it would launch
        with act.kill_lock:
            if proc.poll() is None:
                kill_tree(proc.pid, proc=proc)
            else:
                act.leftovers += self._sweep(act, proc)
        return True

    # -- the end of a run -----------------------------------------------------------------------

    def _signals(self, project_id: str, run_id: str) -> tuple[list[dict], dict[str, Any]]:
        """The agent tools' signals and what is read from them."""
        signals = run_store.read_signals(project_id, run_id)
        tool_paths: set[str] = set()
        shas: set[str] = set()
        for sig in signals:
            if isinstance(sig.get("path"), str) and sig.get("kind") in ("complete", "note"):
                tool_paths.add(sig["path"])
            for p in sig.get("paths") or ():
                if isinstance(p, str):
                    tool_paths.add(p)
            if sig.get("kind") == "complete" and isinstance(sig.get("tasks_sha"), str):
                shas.add(sig["tasks_sha"])
        decisive = [s for s in signals if s.get("kind") in ("ask", "complete")]
        complete = [s for s in signals if s.get("kind") == "complete"]
        asks = [s for s in signals if s.get("kind") == "ask"]
        return signals, {
            "tool_paths": sorted(tool_paths), "tasks_shas": shas,
            "last": decisive[-1]["kind"] if decisive else None,
            "question": asks[-1].get("question") if asks and isinstance(asks[-1].get("question"), str) else None,
            "report": complete[-1].get("path") if complete and isinstance(complete[-1].get("path"), str) else None,
        }

    def _assess(self, root: Path, project_id: str, run_id: str, run: dict, before: dict,
                human: Callable[[], Iterable[str]], human_sha: Callable[[], dict[str, str]] | None = None
                ) -> dict[str, Any]:
        """The after-picture and what it shows against `before` (17.5): the run's changes (the
        person's own saves left out), the out-of-bounds ones, and the paths the agent tools wrote.
        `human` (and `human_sha`, the hashes of what the engine wrote) are read once the after-picture
        is taken, so a save made while the project was being walked is the person's too. The
        out-of-bounds check does not leave the person's paths out: a protected path is allowed to end
        with the bytes the engine wrote there, and with nothing else."""
        rd = run_store.run_dir(project_id, run_id)
        after = snapshot.take(root)
        _write_json(rd / "after.json", after)
        human_list = sorted(human())
        shas = {_key(p): s for p, s in (human_sha() if human_sha is not None else {}).items()}
        old, new = before.get("files") or {}, after["files"]
        human_keys = {_key(p) for p in human_list}
        exclude = {p for p in (*old, *new) if _key(p) in human_keys}
        everything = snapshot.compare(before, after)
        changes = snapshot.compare(before, after, exclude=exclude)
        partial = bool(before.get("partial") or after.get("partial"))
        if partial:  # a cut picture lists files it never saw: a deletion only when the file is really gone,
            # a creation only when the file is newer than the run (17.5)
            started_ns = _started_ns(run)
            gone = lambda p: not os.path.lexists(os.path.join(root, *p.split("/")))  # noqa: E731
            for found in (changes, everything):
                found["deleted"] = [p for p in found["deleted"] if gone(p)]
                found["created"] = snapshot.created_since(root, found["created"], started_ns)
        by_person = sorted({p for kind in everything.values() for p in kind if p in exclude})
        _signals, info = self._signals(project_id, run_id)
        before_task = old.get(TASKS_PATH) or {}
        tasks_ok = set(info["tasks_shas"])
        if before_task.get("sha256"):
            tasks_ok.add(before_task["sha256"])
        allowed: dict[str, set[str]] = {TASKS_PATH: tasks_ok}
        for kind in everything.values():
            for rel in kind:
                if _key(rel) in shas:
                    allowed.setdefault(rel, set()).add(shas[_key(rel)])
        prot = before.get("protected") or snapshot.protected(root, before)
        data_before, data_after = before.get("data"), after.get("data")
        backups_before, backups_after = before.get("backups"), after.get("backups")
        violations = snapshot.violations(
            everything, prot, git_before=before.get("git"), git_after=after.get("git"), allowed=allowed,
            after=after, data_before=data_before if isinstance(data_before, dict) else None,
            data_after=data_after if isinstance(data_after, dict) else None,
            backups_before=backups_before if isinstance(backups_before, dict) else None,
            backups_after=backups_after if isinstance(backups_after, dict) else None)
        lost_backups = [v["path"] for v in violations if v["reason"] == "backup_deleted"]
        if lost_backups:  # the picture holds no copy of a backup: the run lists it, and undo says it cannot
            changes["deleted"] = sorted({*changes["deleted"], *lost_backups})
        return {
            "changes": {**changes, "by_person": by_person}, "violations": violations,
            "snapshot": {"files": len(old), "partial": partial,
                         "copied_bytes": before.get("copied_bytes", 0), "not_copied": before.get("not_copied", 0)},
            "tool_paths": info["tool_paths"], "revertible": True, "human_paths": human_list,
            "signal_info": info,
        }

    def _attribute(self, act_root: Path, project_id: str, run: dict, changes: dict, tool_paths: list[str]) -> None:
        """Record the files the run created or changed in the change history as the agent's (17.5), unless a
        scan that ran meanwhile already holds that very content as the run's (18.1 item 3). Git's own files
        (`.git/hooks`, `.git/config`) are the out-of-bounds list's business, not the history's."""
        try:
            agent = labels.parse_owner(run.get("owner"))
        except ValueError:
            agent = None
        if agent is None:
            return
        who = labels.who_value(agent, run.get("by"))
        skip = set(tool_paths) | {TASKS_PATH}
        candidates = [rel for rel in [*changes.get("created", ()), *changes.get("modified", ())]
                      if rel not in skip and rel.split("/", 1)[0].casefold() not in _RECORD_AND_GIT_DIRS]
        tracked: set[str] = set()
        if candidates:  # 17.5: a git-tracked file's history is git's; one lookup for the whole run
            try:
                tracked = history._tracked(act_root) or set()  # noqa: SLF001 -- the history's own list of git files
            except Exception:  # noqa: BLE001 -- unknown means "not tracked"; the history never fails a run
                tracked = set()
        wanted = [rel for rel in candidates if rel not in tracked]
        if len(wanted) > HISTORY_FILES_LIMIT:
            logger.warning("agent run %s changed %d files; recording the first %d in the history", run["id"],
                           len(wanted), HISTORY_FILES_LIMIT)
            wanted = wanted[:HISTORY_FILES_LIMIT]
        if not wanted:
            return
        try:
            with history.Store.open(project_id) as store:
                for rel in wanted:
                    full = act_root / rel
                    try:
                        if paths.is_dataless(full) or full.stat().st_size > HISTORY_FILE_SIZE_LIMIT:
                            continue
                        # what a scan saw while the run went on is already there under this note (18.1 item 3)
                        history.record_file(store, act_root, rel, source="agent", who=who, note=run["id"],
                                            unless_noted=f"{history.SEEN_NOTE}{run['id']}")
                    except Exception:  # noqa: BLE001 -- one file's trouble does not stop the others
                        logger.warning("could not record %s of agent run %s in the history", rel, run["id"],
                                       exc_info=True)
        except Exception:  # noqa: BLE001 -- history never fails a run
            logger.warning("could not record agent run %s in the history", run["id"], exc_info=True)

    def _decide(self, act: _Active, stream: Any, exit_code: int | None, launch_error: dict | None,
                info: dict[str, Any]) -> tuple[str, dict | None]:
        if launch_error:
            return "failed", launch_error
        reason = act.stop_reason
        if reason == "stop":
            return "stopped", None
        if reason == "shutdown":
            return "interrupted", {"code": "shutdown",
                                   "detail": "Research OS was closed while the run was going"}
        if reason == "timeout":
            minutes = act.timeout_minutes
            shown = f"{minutes:g}"
            return "failed", {"code": "timeout", "detail": f"The run passed its time limit of {shown} minutes"}
        if stream is None:
            return "failed", {"code": "start_failed", "detail": "The program was not started"}
        status, error = runners.outcome(act.runner, exit_code, stream)
        if status != "ok":
            return "failed", error
        if info["last"] == "ask":
            return "needs_input", None
        if info["last"] == "complete":
            return "done", None
        return "ended", None

    def _finish(self, act: _Active, *, before: dict | None, stream: Any, exit_code: int | None,
                launch_error: dict | None) -> None:
        """Take the after-picture, compare, attribute, decide the state and save it (17.3, 17.5)."""
        project_id, run_id, root = act.project_id, act.run_id, act.root
        run = run_store.load_run(project_id, run_id) or act.run
        extra: dict[str, Any] = {}
        info: dict[str, Any] = {"last": None, "question": None, "report": None, "tasks_shas": set(),
                                "tool_paths": []}
        check_error: dict | None = None
        if before is not None:
            try:
                assessed = self._assess(root, project_id, run_id, run, before, act.human_paths, act.human_hashes)
                info = assessed.pop("signal_info")
                extra.update(assessed)
                self._attribute(root, project_id, run, assessed["changes"], assessed["tool_paths"])
            except Exception as exc:  # noqa: BLE001 -- the state is still decided; the person is told
                logger.exception("the after-run check of agent run %s failed", run_id)
                check_error = {"code": "check_failed", "detail": f"{type(exc).__name__}: {exc}"}
                _sigs, info = self._signals(project_id, run_id)
            finally:
                self._release(act)  # the history stops crediting scans to the run (18.1 item 3)
        extra.setdefault("human_paths", act.human_paths())
        state, error = self._decide(act, stream, exit_code, launch_error, info)
        if error is None and check_error is not None:
            error = check_error
        state, outcome = _warn_if_out_of_bounds(state, extra.get("violations"))
        text = (getattr(stream, "result_text", None) or "").strip() if stream is not None else ""
        self._update(
            act, state=state, outcome=outcome, ended=_now(), exit_code=exit_code, error=error,
            question=info["question"], report=info["report"], summary=text[:SUMMARY_LIMIT] or None,
            session_id=(getattr(stream, "session_id", None) or run.get("session_id")) if stream is not None
            else run.get("session_id"),
            model_answered=getattr(stream, "model", None) if stream is not None else None,
            cost_usd=getattr(stream, "cost_usd", None) if stream is not None else None,
            human_sha=act.human_hashes(), leftovers=act.leftovers, **extra)

    # -- stopping -------------------------------------------------------------------------------

    def stop(self, project_id: str, run_id: str) -> dict:
        """Stop the run and everything it started (17.3). Returns the run's summary as it is
        when the run has reached its final state or `STOP_WAIT_S` has passed. Idempotent; a run
        that is not going is returned unchanged."""
        run = self._load(project_id, run_id)
        with self._lock:
            act = self._active.get(project_id)
        if act is not None and act.run_id == run_id:
            self._terminate(act, "stop")
            act.done.wait(STOP_WAIT_S)
        else:
            self._recover(project_id)
        return self._summary_of(project_id, run_store.load_run(project_id, run_id) or run)

    def shutdown(self) -> None:
        """Stop every active run (the app is quitting): each ends `interrupted`, after its
        after-run check. Idempotent; later starts are refused."""
        with self._lock:
            self._closing = True
            actives = list(self._active.values())
        killers = [threading.Thread(target=self._terminate, args=(act, "shutdown"), daemon=True) for act in actives]
        for t in killers:
            t.start()
        for t in killers:
            t.join()
        deadline = time.monotonic() + SHUTDOWN_WAIT_S
        for act in actives:
            act.done.wait(max(0.0, deadline - time.monotonic()))

    # -- listing --------------------------------------------------------------------------------

    def active_for(self, project_id: str) -> str | None:
        """The id of the run this manager is carrying out for the project, if any."""
        with self._lock:
            act = self._active.get(project_id)
            return act.run_id if act is not None else None

    def _summary_of(self, project_id: str, run: dict) -> dict:
        latest = next((r for r in run_store.list_runs(project_id) if r.get("task_id") == run.get("task_id")), None)
        return summary(run, latest_of_task=latest is not None and latest.get("id") == run.get("id"),
                       active=self._is_active(project_id, run["id"]))

    def runs(self, project_id: str, *, recover: bool = True) -> dict:
        """`{"runs": [summary...], "active": run id | None}`, newest first. Runs left active by an
        engine that is gone are marked `interrupted` first (17.3) -- unless `recover` is False
        (a window without the engine's session may read the list but not change a run, 18.1):
        they are then listed as they are stored."""
        self._check_ids(project_id)
        foreign = self._recover(project_id) if recover else self._foreign_active(project_id)
        with self._lock:
            act = self._active.get(project_id)
            active_id = act.run_id if act is not None else None
        out = []
        seen: set[Any] = set()
        for run in run_store.list_runs(project_id):
            latest = run.get("task_id") not in seen
            seen.add(run.get("task_id"))
            out.append(summary(run, latest_of_task=latest, active=run["id"] == active_id))
        return {"runs": out, "active": active_id or (foreign[0] if foreign else None)}

    def detail(self, project_id: str, run_id: str) -> dict:
        """One run with its steps (read back from `events.jsonl`), changes, out-of-bounds
        changes, the instruction sent, the hook's refusals, the tail of stderr and its error."""
        run = self._load(project_id, run_id)
        rd = run_store.run_dir(project_id, run_id)
        out = self._summary_of(project_id, run)
        steps, total = self._steps(rd / "events.jsonl", run)
        out.update(
            steps=steps, steps_total=total, changes=run.get("changes") or {}, violations=run.get("violations") or [],
            instruction=_read_text(rd / "instruction.txt"),
            refusals=run_store.read_lines(rd / "refusals.jsonl", tail_bytes=REFUSALS_TAIL_BYTES),
            stderr_tail=_read_text(rd / "stderr.log", STDERR_TAIL_BYTES, tail=True),
            error=run.get("error"), message=run.get("message"), summary=run.get("summary"),
            exit_code=run.get("exit_code"), model_answered=run.get("model_answered"),
            snapshot=run.get("snapshot"), lang=run.get("lang"), revert=run.get("revert"),
            leftovers=run.get("leftovers") or 0, env_removed=run.get("env_removed") or [],
            events_truncated=bool(run.get("events_truncated")))
        return out

    @staticmethod
    def _steps(events_path: Path, run: dict) -> tuple[list[dict], int]:
        stream: runners.ClaudeStream | runners.OpenCodeStream = (
            runners.ClaudeStream() if run.get("runner") == "claude-code" else runners.OpenCodeStream())
        raw = _read_text(events_path, run_store.EVENTS_LIMIT)
        for line in raw.splitlines():
            stream.feed(line)
        steps = list(stream.steps)
        # Paths inside the project read as the project names them (`analysis/vol.py`), not from `/`.
        prefixes = sorted({os.path.join(str(p), "") for p in (run.get("root"), os.path.realpath(str(run.get("root"))))
                           if p and p != "None"}, key=len, reverse=True)
        if prefixes:
            for step in steps:
                for prefix in prefixes:
                    step["text"] = step["text"].replace(prefix, "")
        state = run.get("state")
        outcome = run_store.outcome_of(run)
        if state not in run_store.ACTIVE_STATES and not (steps and steps[-1]["kind"] == "end"):
            en = _lang(run.get("lang")) == "en"
            error = run.get("error") or {}
            if outcome == "stopped":
                text = "Stopped by the person." if en else "已被你停止。"
            elif outcome == "interrupted":
                text = ("Interrupted: Research OS was closed while the run was going." if en
                        else "已中断：运行期间 Research OS 被关闭了。")
            elif error:
                text = str(error.get("detail") or error.get("code") or "")
            else:
                text = (run.get("summary") or "").strip() or ("Ended." if en else "已结束。")
            steps.append({"kind": "end", "text": " ".join(text.split())[:runners.STEP_TEXT_LIMIT], "tool": None})
        total = len(steps)
        return steps[-STEPS_LIMIT:], total

    # -- recovery -------------------------------------------------------------------------------

    def _engine_alive(self, run: dict) -> bool:
        """Whether another live process still carries `run` out."""
        pid = run.get("engine_pid")
        if not _pid_alive(pid) or pid == os.getpid():
            return False
        stamp = run.get("engine_started")
        if isinstance(stamp, str) and stamp:
            current = _start_of(pid)
            if current is not None and current != stamp:
                return False  # the pid belongs to another process now: the engine of the run is gone
        try:
            created = datetime.fromisoformat(str(run.get("created")).replace("Z", "+00:00"))
            if datetime.now(timezone.utc) - created > STALE_AFTER:
                return False
        except ValueError:
            return False
        return True

    def _foreign_active(self, project_id: str) -> list[str]:
        """Runs recorded as active that a different live engine is carrying out."""
        with self._lock:
            act = self._active.get(project_id)
            mine = act.run_id if act is not None else None
        return [r["id"] for r in run_store.list_runs(project_id)
                if r.get("state") in run_store.ACTIVE_STATES and r["id"] != mine and self._engine_alive(r)]

    def _recover(self, project_id: str) -> list[str]:
        """Mark the runs an engine left active `interrupted` (`engine_gone`), end their program if
        it is still alive, and work out their changes so they can be undone (17.3). Returns the
        ids of active runs that belong to another live engine and were left alone."""
        foreign: list[str] = []
        with self._recover_lock:
            listing = run_store.list_runs(project_id)
            owing = [r for r in listing if r.get("check_pending") and r.get("state") == "interrupted"]
            for listed in listing:
                if listed.get("state") not in run_store.ACTIVE_STATES:
                    continue
                with self._lock:
                    # the listing may be older than the run's end: read it again, with the registry held
                    # (the run's thread leaves the registry after its final state is saved)
                    run = run_store.load_run(project_id, listed["id"])
                    act = self._active.get(project_id)
                    if run is None or run.get("state") not in run_store.ACTIVE_STATES:
                        continue
                    if act is not None and act.run_id == run["id"]:
                        continue
                if self._engine_alive(run):
                    foreign.append(run["id"])
                    continue
                try:
                    self._interrupt_stale(project_id, run)
                except Exception:  # noqa: BLE001 -- recovery of one run never blocks the listing
                    logger.exception("could not recover agent run %s", run.get("id"))
                    self._fail_record(project_id, run["id"], "engine_gone",
                                      "Research OS stopped while the run was going")
            if owing and not foreign:
                self._check_pending(project_id, owing)
        return foreign

    def recover_all(self) -> list[str]:
        """At engine start (18.1 item 6): recover the runs left preparing or running of every project this Mac
        keeps runs for, at once, instead of whenever that project's runs are next listed. Each is handled as
        `_recover` handles it -- marked `interrupted`, its program ended only when the recorded pid has the
        recorded start time, a run of another live engine left alone. One project's trouble does not stop the
        others, and nothing more is begun once the manager is closing. Returns the ids of the runs recovered."""
        recovered: list[str] = []

        def going(project_id: str) -> set[str]:
            return {r["id"] for r in run_store.list_runs(project_id) if r.get("state") in run_store.ACTIVE_STATES}

        def owed(project_id: str) -> bool:  # an interrupted run whose changes were never compared (folder unreadable then)
            return any(r.get("check_pending") for r in run_store.list_runs(project_id))

        for project_id in run_store.project_ids():
            if self._closing:
                break
            left = going(project_id)
            if not left and not owed(project_id):
                continue
            try:
                self._recover(project_id)
            except Exception:  # noqa: BLE001 -- recovery of one project never blocks the others
                logger.exception("could not recover the agent runs of project %s", project_id)
                continue
            recovered.extend(sorted(left - going(project_id)))
        return recovered

    def _interrupt_stale(self, project_id: str, run: dict) -> None:
        run_id = run["id"]
        root = Path(run.get("root") or "")
        pid = run.get("pid")
        started = run.get("program_started")
        # only the process recorded at launch (pid AND start time) is the run's program: a recycled pid
        # is some other process, whatever its command line says (and no process group is signalled)
        if _pid_alive(pid) and isinstance(started, str) and started and _start_of(pid) == started:
            kill_tree(pid)
        update: dict[str, Any] = {"state": "interrupted", "ended": _now(),
                                  "error": {"code": "engine_gone",
                                            "detail": "Research OS stopped while the run was going"}}
        rd = run_store.run_dir(project_id, run_id)
        before = _read_json(rd / "before.json")
        _signals, info = self._signals(project_id, run_id)
        update.update(question=info["question"], report=info["report"])
        compared = isinstance(before, dict) and isinstance(before.get("files"), dict)
        if compared and root.is_dir():
            try:
                update.update(self._compare_interrupted(root, project_id, run, before))
            except Exception:  # noqa: BLE001
                logger.exception("could not check the changes of interrupted agent run %s", run_id)
        elif compared:
            # The folder cannot be read now (a drive that is not attached, a folder it may not enter). The program
            # is ended, but what it changed is not known: the run says so, and a later recovery compares it once
            # the folder is back (`_check_pending`) -- undo is not lost for good, nor an out-of-bounds change unseen.
            update["check_pending"] = True
        # only while it is still going: a run that ended meanwhile keeps the state it reached
        run_store.update_run_if_active(project_id, run_id, **update)

    def _compare_interrupted(self, root: Path, project_id: str, run: dict, before: dict) -> dict[str, Any]:
        """The fields a recovered run gets from comparing its folder with its before-picture (17.3, 18.1 item 4)."""
        assessed = self._assess(root, project_id, run["id"], run, before, lambda: list(run.get("human_paths") or ()),
                                lambda: dict(run.get("human_sha") or {}))
        assessed.pop("signal_info")
        state, outcome = _warn_if_out_of_bounds("interrupted", assessed["violations"])
        return {**assessed, "state": state, "outcome": outcome}

    def _check_pending(self, project_id: str, owing: list[dict]) -> None:
        """Compare the interrupted runs `owing` (`check_pending`: their folder could not be read when they were
        recovered) whose folder can be read now. Nothing is compared while a run of the project is going (its
        changes would be read as theirs). A run without a usable before-picture has nothing to compare and is let
        go; a comparison that fails stays pending for the next recovery."""
        with self._lock:
            if project_id in self._active:
                return
        for run in owing:
            if not run.get("root") or not Path(run["root"]).is_dir():
                continue
            before = _read_json(run_store.run_dir(project_id, run["id"]) / "before.json")
            try:
                update = (self._compare_interrupted(Path(run["root"]), project_id, run, before)
                          if isinstance(before, dict) and isinstance(before.get("files"), dict) else {})
            except Exception:  # noqa: BLE001 -- recovery never blocks a listing; the run stays pending
                logger.exception("could not check the changes of interrupted agent run %s", run["id"])
                continue
            run_store.update_run(project_id, run["id"], check_pending=False, **update)

    # -- undoing ----------------------------------------------------------------------------------

    @staticmethod
    def _chosen(files: object) -> set[str] | None:
        """The paths the person left checked in the undo list, in comparing form (`_key`); None when the
        request names none (all that can be undone is then undone). An empty list is a mistake, not an undo."""
        if files is None:
            return None
        if (not isinstance(files, (list, tuple)) or len(files) > UNDO_FILES_LIMIT
                or not all(isinstance(f, str) and f for f in files)):
            raise AgentError("invalid", "files must be a list of project paths",
                             message_zh="files 必须是项目文件路径的列表")
        if not files:
            raise AgentError("invalid", "Choose at least one file to undo", message_zh="请至少选择一个要撤回的文件")
        return {_key(f) for f in files}

    def _check_undoable(self, project_id: str, run_id: str, run: dict, foreign: list[str]) -> None:
        """What stops a run from being undone (or planned) right now; the registry lock is held by the caller."""
        act = self._active.get(project_id)
        if act is not None:
            raise AgentError("run_active" if act.run_id == run_id else "busy")
        if project_id in self._reverting:
            raise AgentError("busy")
        if run.get("state") in run_store.ACTIVE_STATES:
            raise AgentError("run_active" if self._engine_alive(run) else "not_finished")
        if foreign:  # a run of another Research OS process is going: its files are not ours to move
            raise AgentError("busy")
        if run.get("reverted"):
            raise AgentError("reverted")

    def revert(self, root: Path, project_id: str, run_id: str, *, by: str | None,
               files: list[str] | None = None) -> dict:
        """Undo a finished run (17.6): `{"run": summary, "restored": [rel], "removed": [rel],
        "skipped": [{"path", "reason"}]}`. `removed` are files the run created, moved into the
        run's `reverted/` folder; `restored` come back from the before-picture's copies. A file
        that changed after the run, or that has no copy, is left alone and listed in `skipped`
        (`changed_since`, `no_copy`, `in_cloud`, `protected`). With `files` only those paths are
        undone (the checked ones of `revert_plan`, 18.1 item 5); a path that could have been
        undone and was not named is left as it is and listed as `kept`. The run is marked undone
        unless nothing could be; what was left alone is kept as `run["revert"]` and counted in the
        summary (`revert_skipped`)."""
        self._check_ids(project_id, run_id)
        chosen = self._chosen(files)
        root = self._writable_root(root, project_id)
        foreign = self._recover(project_id)
        run = self._load(project_id, run_id)
        with self._lock:
            self._check_undoable(project_id, run_id, run, foreign)
            self._reverting.add(project_id)
        try:
            return self._revert(root, project_id, run, by, chosen)
        finally:
            with self._lock:
                self._reverting.discard(project_id)

    def revert_plan(self, root: Path, project_id: str, run_id: str) -> dict:
        """What 撤回本次运行 would do now, without doing it (18.1 item 5): `{"run": summary, "restore":
        [{"path", "time"}], "remove": [{"path", "time"}], "cannot": [{"path", "reason"}]}`. `restore` is the
        files put back from the before-picture (changed or deleted ones; a created file that is a deleted one
        renamed is listed under the old name, with `from` the new; the task's done mark is listed as
        `research/tasks.json` with `task`), `remove` the files moved away, `cannot` the files left alone with
        the reason (`changed_since`, `no_copy`, `in_cloud`, `protected`). `time` is a file's modification time
        on this Mac, in local time, where it is there; for a file that is gone, and for the done mark, when the
        run ended. Refuses as `revert` does (`reverted`, `later_run`, `not_revertible`, `run_active`, `busy`).
        It only reads: it does not recover the runs a gone engine left active (listing the runs does, and
        a run is offered for undo only after that), so a window without the engine's session may ask."""
        self._check_ids(project_id, run_id)
        root = self._readable_root(root)
        foreign = self._foreign_active(project_id)
        run = self._load(project_id, run_id)
        with self._lock:
            self._check_undoable(project_id, run_id, run, foreign)
        rd, before, after, changes, info = self._undo_inputs(project_id, run)
        restored: list[str] = []
        removed: list[str] = []
        skipped: list[dict[str, str]] = []
        origin: dict[str, str] = {}
        self._undo_files(root, os.path.realpath(root), rd, before["files"], after["files"], changes, run_id,
                         restored, removed, skipped, project_id, person=self._person_paths(run, changes),
                         dry=True, origin=origin)
        self._undo_task(root, run, info, restored, skipped, dry=True)
        ended = _local_time(run.get("ended"))

        def item(rel: str) -> dict[str, Any]:
            entry: dict[str, Any] = {"path": rel}
            source = origin.get(rel, rel)
            moment = None
            if rel == TASKS_PATH:
                entry["task"] = run.get("task_text") or ""
            else:
                with contextlib.suppress(OSError):
                    moment = _local_time(os.lstat(os.path.join(root, *source.split("/"))).st_mtime)
            entry["time"] = moment or ended
            if rel in origin:
                entry["from"] = origin[rel]
            return entry

        return {"run": self._summary_of(project_id, run),
                "restore": [item(rel) for rel in sorted(restored)], "remove": [item(rel) for rel in sorted(removed)],
                "cannot": sorted(skipped, key=lambda s: s["path"])}

    @staticmethod
    def _readable_root(root: Path | str) -> Path:
        """The resolved project folder, for a request that only reads."""
        try:
            resolved = Path(root).resolve()
            if not resolved.is_dir():
                raise AgentError("invalid", "The project folder is missing")
        except OSError as exc:
            raise AgentError("invalid", f"The project folder cannot be read: {exc}") from exc
        return resolved

    @staticmethod
    def _person_paths(run: dict, changes: dict) -> set[str]:
        """The paths the person saved through Research OS while the run went on (`_key` form)."""
        return {_key(p) for p in (*(run.get("human_paths") or ()), *(changes.get("by_person") or ()))}

    def _undo_inputs(self, project_id: str, run: dict) -> tuple[Path, dict, dict, dict, dict[str, Any]]:
        """What undoing (or planning the undo of) `run` works from: the run's folder, its two pictures, its
        changes and what its tools signalled. Refuses `not_revertible` and `later_run` (17.6)."""
        run_id = run["id"]
        rd = run_store.run_dir(project_id, run_id)
        before, after = _read_json(rd / "before.json"), _read_json(rd / "after.json")
        if (not run.get("revertible") or not isinstance(before, dict) or not isinstance(after, dict)
                or not isinstance(before.get("files"), dict)
                or not isinstance(after.get("files"), dict)):
            raise AgentError("not_revertible")
        changes = run.get("changes") or {}
        _signals, info = self._signals(project_id, run_id)
        if _change_count(changes) == 0 and not info["report"]:
            raise AgentError("not_revertible")
        mine = {_key(p) for kind in ("created", "modified", "deleted") for p in changes.get(kind, ())} - {
            _key(TASKS_PATH)}
        for other in run_store.list_runs(project_id):
            if other["id"] <= run_id or other.get("reverted"):
                continue
            theirs = {_key(p) for kind in ("created", "modified", "deleted")
                      for p in (other.get("changes") or {}).get(kind, ())}
            if mine & theirs:
                raise AgentError("later_run")
        return rd, before, after, changes, info

    def _revert(self, root: Path, project_id: str, run: dict, by: str | None, chosen: set[str] | None) -> dict:
        run_id = run["id"]
        rd, before, after, changes, info = self._undo_inputs(project_id, run)
        restored: list[str] = []
        removed: list[str] = []
        skipped: list[dict[str, str]] = []
        real_root = os.path.realpath(root)
        try:
            with situation.write_guard(root, project_id, human=True, timeout=10.0):
                self._undo_files(root, real_root, rd, before["files"], after["files"], changes, run_id,
                                 restored, removed, skipped, project_id, person=self._person_paths(run, changes),
                                 folders=_folders_before(before), chosen=chosen)
                self._undo_task(root, run, info, restored, skipped, chosen=chosen)
        except ProjectLockError as exc:
            raise AgentError("busy", f"The project is being written by something else: {exc}") from exc
        except situation.WriteRefused as exc:
            raise AgentError("read_only", str(exc)) from exc
        result = {"restored": sorted(restored), "removed": sorted(removed), "skipped": skipped}
        done_something = bool(restored or removed)
        if done_something or not skipped:
            run = run_store.update_run(project_id, run_id, reverted=True, reverted_at=_now(),
                                       reverted_by=by if isinstance(by, str) and by else None, revert=result)
        else:
            run = run_store.update_run(project_id, run_id, revert=result)
        return {"run": self._summary_of(project_id, run), **result}

    def _undo_files(self, root: Path, real_root: str, rd: Path, old: dict, new: dict, changes: dict, run_id: str,
                    restored: list[str], removed: list[str], skipped: list[dict[str, str]],
                    project_id: str, person: set[str] | None = None, *, folders: set[str] | None = None,
                    chosen: set[str] | None = None, dry: bool = False,
                    origin: dict[str, str] | None = None) -> None:
        """Undo (or, with `dry`, work out the undo of) the files of a run (17.6), filling `restored`, `removed`
        and `skipped`. `chosen` (18.1 item 5) are the paths, in `_key` form, the person left checked: a path
        that would be undone and is not among them is left as it is and listed as `kept`; None undoes all.
        `folders` are the folders that existed before the run, which are not removed when a move empties them
        (`_folders_before`; None removes none). `dry` runs every check of the real thing and writes nothing:
        no file or folder is made, moved or removed, nothing is recorded; `origin` then maps a path put back
        by moving a created file to the created file's name."""
        who = history.scan_who(root)
        recorded: list[str] = []
        person = person or set()
        origin = origin if origin is not None else {}
        handled: set[str] = set()  # deleted paths a moved-back file has restored
        moved: list[str] = []  # files that left their folder: the folders they emptied are cleaned up

        def skip(rel: str, reason: str) -> None:
            skipped.append({"path": rel, "reason": reason})

        def kept(rel: str) -> bool:
            """Whether the person left `rel` out of this undo: it stays as it is, and the run's log says so."""
            if chosen is not None and _key(rel) not in chosen:
                skip(rel, "kept")
                return True
            return False

        def path_of(rel: str) -> str:
            return os.path.join(root, *rel.split("/"))

        def copy_away(rel: str, full: str) -> bool:
            dest = rd / "reverted" / Path(*rel.split("/"))
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(full, dest)
                return True
            except OSError:
                return False

        def free(rel: str, probe: bool = False) -> bool:
            """Whether nothing stands at `rel`: an empty folder (what a replaced file leaves behind) counts as
            nothing, and is removed unless this only `probe`s."""
            target = path_of(rel)
            if not os.path.lexists(target):
                return True
            if os.path.isdir(target) and not os.path.islink(target):
                try:
                    if probe or dry:
                        return not os.listdir(target)
                    os.rmdir(target)  # fails unless empty
                except OSError:
                    return False
                return True
            return False

        def blocked(rel: str) -> str | None:
            """Why `restore(rel)` would leave `rel` alone, found without writing anything: the file has no usable
            copy (`no_copy`) or its folder cannot be made inside the project (`changed_since`). Asked before the
            person's choice is read, so that a file the undo could not have put back is never listed as one they
            chose to keep (the plan lists it as it is, 18.1 item 5)."""
            src = rd / "before" / Path(*rel.split("/"))
            entry = old.get(rel) or {}
            if not entry.get("copied") or not src.is_file():
                return "no_copy"
            if entry.get("sha256") and _sha256(src) != entry["sha256"]:
                return "no_copy"  # the copy is not what was recorded: do not write it back
            parent = os.path.dirname(path_of(rel))
            if not _room_for(parent) or not _inside(real_root, os.path.realpath(parent)):
                return "changed_since"
            return None

        def restore(rel: str, *, checked: bool = False) -> bool:
            """Put the before-picture's copy of `rel` back, atomically and inside the project. `checked`: the
            caller has asked `blocked` already."""
            why = None if checked else blocked(rel)
            if why is not None:
                skip(rel, why)
                return False
            src = rd / "before" / Path(*rel.split("/"))
            target = path_of(rel)
            parent = os.path.dirname(target)
            if dry:
                restored.append(rel)
                return True
            try:
                os.makedirs(parent, exist_ok=True)
                if not _inside(real_root, os.path.realpath(parent)):
                    skip(rel, "changed_since")
                    return False
                record_files.durable_write(Path(target), src.read_bytes())
                with contextlib.suppress(OSError):
                    shutil.copymode(src, target)
            except (OSError, record_files.RecordFileError):
                logger.warning("could not restore %s of agent run %s", rel, run_id, exc_info=True)
                skip(rel, "changed_since")
                return False
            restored.append(rel)
            recorded.append(rel)
            return True

        # A deleted file with no copy plus a created file of the same content is a rename or a move: the
        # created file is the only holder of that content, so it goes back to the old name (never away).
        lost: dict[str, list[str]] = {}
        for rel in changes.get("deleted", ()):
            entry = old.get(rel) or {}
            if rel != TASKS_PATH and _key(rel) not in person and entry.get("sha256") and not entry.get("copied"):
                lost.setdefault(entry["sha256"], []).append(rel)

        def move_back(rel: str, current: dict) -> bool:
            """Handle the created file `rel` that is a deleted file renamed: True when it was (moved back, or
            left where it is because the person kept the old name's return), False when it is a plain creation."""
            sha = current.get("sha256") or (new.get(rel) or {}).get("sha256")
            for target_rel in list(lost.get(sha, ())) if sha else ():
                target = path_of(target_rel)
                occupied = os.path.lexists(target) and not (os.path.exists(target)
                                                            and os.path.samefile(target, path_of(rel)))
                if occupied and not free(target_rel, probe=True):
                    continue
                parent = os.path.dirname(target)
                if not _room_for(parent) or not _inside(real_root, os.path.realpath(parent)):
                    continue
                if kept(target_rel):  # the file keeps its new name; the old name stays empty
                    lost[sha].remove(target_rel)
                    handled.add(target_rel)
                    return True
                if not dry:
                    try:
                        if occupied and not free(target_rel):
                            continue
                        os.makedirs(parent, exist_ok=True)
                        if not _inside(real_root, os.path.realpath(parent)):
                            continue
                        os.rename(path_of(rel), target)
                    except OSError:
                        continue
                    recorded.append(target_rel)
                    moved.append(rel)
                origin[target_rel] = rel
                lost[sha].remove(target_rel)
                handled.add(target_rel)
                restored.append(target_rel)
                return True
            return False

        for rel in changes.get("created", ()):
            if rel == TASKS_PATH:
                continue
            if _key(rel) in person:  # saved by the person during the run: never ours to move
                skip(rel, "changed_since")
                continue
            if rel.split("/", 1)[0].casefold() in _RECORD_DIRS or rel == snapshot.GIT_CONFIG:
                skip(rel, "protected")  # never move the project's own records, nor the config of a repository
                continue
            current = snapshot.fingerprint(root, rel)
            if current is None or not snapshot.same(current, new.get(rel)):
                skip(rel, "changed_since")
                continue
            if current.get("dataless"):  # moving it could download it, under the project lock
                skip(rel, "in_cloud")
                continue
            if move_back(rel, current):
                continue
            if kept(rel):
                continue
            if dry:
                removed.append(rel)
                continue
            dest = rd / "reverted" / Path(*rel.split("/"))
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(path_of(rel), dest)
            except OSError:
                logger.warning("could not move %s of agent run %s away", rel, run_id, exc_info=True)
                skip(rel, "changed_since")
                continue
            removed.append(rel)
            moved.append(rel)
        # the folders the moves emptied, deepest first, unless they were there before the run
        emptied = {"/".join(rel.split("/")[:i]) for rel in moved for i in range(1, rel.count("/") + 1)}
        for folder in sorted(emptied, key=lambda f: f.count("/"), reverse=True):
            if folders is None or folder in folders or folder.split("/", 1)[0].casefold() in _RECORD_AND_GIT_DIRS:
                continue
            with contextlib.suppress(OSError):  # not empty: it stays
                if not os.path.islink(path_of(folder)):
                    os.rmdir(path_of(folder))
        for rel in changes.get("modified", ()):
            if rel == TASKS_PATH:
                continue
            if _key(rel) in person:
                skip(rel, "changed_since")
                continue
            current = snapshot.fingerprint(root, rel)
            if current is None or not snapshot.same(current, new.get(rel)):
                skip(rel, "changed_since")
                continue
            if current.get("dataless"):  # copying it away would download it, under the project lock
                skip(rel, "in_cloud")
                continue
            why = blocked(rel)
            if why is not None:
                skip(rel, why)
                continue
            if kept(rel):
                continue
            if not dry and not copy_away(rel, path_of(rel)):
                skip(rel, "changed_since")
                continue
            restore(rel, checked=True)
        for rel in changes.get("deleted", ()):
            if rel == TASKS_PATH or rel in handled:
                continue
            if _key(rel) in person or not free(rel, probe=True):
                skip(rel, "changed_since")
                continue
            why = blocked(rel)  # before the choice: a file with no copy is not one the person kept
            if why is not None:
                skip(rel, why)
                continue
            if kept(rel):
                continue
            if not dry and not free(rel):
                skip(rel, "changed_since")
                continue
            restore(rel, checked=True)
        # git's own files and the project's records are not the change history's business (as in `_attribute`)
        recorded = [rel for rel in recorded if rel.split("/", 1)[0].casefold() not in _RECORD_AND_GIT_DIRS]
        if recorded:
            try:
                with history.Store.open(project_id) as store:
                    for rel in recorded:
                        history.record_file(store, root, rel, who=who, note=f"undo {run_id}")
            except Exception:  # noqa: BLE001 -- the files are back; the history is best effort
                logger.warning("could not record the undo of agent run %s in the history", run_id, exc_info=True)

    def _undo_task(self, root: Path, run: dict, info: dict[str, Any], restored: list[str],
                   skipped: list[dict[str, str]], *, chosen: set[str] | None = None, dry: bool = False) -> None:
        """Take back the done mark and the `report` link the run's complete tool set -- logically,
        on the tasks as they are now, never by restoring `research/tasks.json` (17.6). It is listed
        (and can be left out) as `research/tasks.json`; with `dry` it only says whether it would."""
        report = info.get("report")
        if not report:
            return
        try:
            tasks, revision = workspace.read_tasks(root)
            target = next((t for t in tasks if t["id"] == run.get("task_id")), None)
            if target is None:
                return
            changed = dict(target)
            changed["done"] = bool(run.get("task_done_before"))
            if target.get("report") == report:
                changed.pop("report", None)
                if run.get("task_report_before"):
                    changed["report"] = run["task_report_before"]
            if changed == target:
                return
            if chosen is not None and _key(TASKS_PATH) not in chosen:
                skipped.append({"path": TASKS_PATH, "reason": "kept"})
                return
            if not dry:
                workspace.save_tasks(root, {"tasks": [changed if t["id"] == target["id"] else t for t in tasks],
                                            "revision": revision})
        except workspace.WorkspaceError:
            logger.warning("could not take back the done mark of agent run %s", run.get("id"), exc_info=True)
            skipped.append({"path": TASKS_PATH, "reason": "changed_since"})
            return
        restored.append(TASKS_PATH)
