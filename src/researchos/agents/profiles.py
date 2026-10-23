"""This Mac's agents and the programs that run them (DESIGN.md 17.1).

**What is saved.** Four optional keys of `settings.json` (the per-Mac file of
`researchos.settings`), each written only when the person changes it and
read tolerantly (a bad value reads as "not set", never as an error):

- `agents`: `[{"runner", "model"}]`, at most `MAX_AGENTS`, the agents the
  owner control offers;
- `agent_commands`: `{runner: absolute path}`, where the person says a
  program lives (otherwise it is looked up on a widened `PATH`);
- `agent_timeout_minutes`: the run's time limit (default 120, 5-1440);
- `agent_max_budget_usd`: Claude Code's `--max-budget-usd`, when set.

Every setter is read-modify-write and keeps the other keys, and validates
before it writes: a model name ends up on a command line, so it is checked
by `labels.check_model` (never starts with `-`).

**Finding a program.** An app opened from the Finder has none of the shell's
`PATH`, so `search_path` widens it with the usual install folders of both
programs (17.1). Found means an executable regular file that answers
`--version` within `VERSION_TIMEOUT_S`; the version is shown. Research OS
never installs a runner. A version that was answered is remembered in memory
while the program is the same file (18.4 item 7), so the page asking again
does not start the program again.

**The MCP extra.** An agent reports through Research OS's own MCP tools, so
running one needs the optional `mcp` package in this Python (17.1).
`mcp_available` answers without importing it: importing `mcp` costs
hundreds of milliseconds, and nothing under `researchos.agents` imports it.

Every child process here is started in a session of its own and killed as a
group on timeout, so a program that forked cannot keep a pipe open and hang
the engine.
"""

from __future__ import annotations

import importlib.util
import math
import os
import re
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from researchos import memo, settings
from researchos.agents import labels

MAX_AGENTS = 20
DEFAULT_TIMEOUT_MINUTES = 120
TIMEOUT_RANGE = (5, 1440)
MAX_BUDGET_LIMIT_USD = 1000
CLAUDE_MODEL_SUGGESTIONS = ("sonnet", "opus", "haiku", "fable")
EXTRA_PATH_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "~/.local/bin", "~/.claude/local",
                   "~/.opencode/bin", "~/.bun/bin", "~/.npm-global/bin", "/usr/bin", "/bin")
VERSION_TIMEOUT_S = 10
MODELS_TIMEOUT_S = 30

#: The program each runner is.
PROGRAMS = {"claude-code": "claude", "opencode": "opencode"}

AGENTS_SETTING = "agents"
COMMANDS_SETTING = "agent_commands"
TIMEOUT_SETTING = "agent_timeout_minutes"
BUDGET_SETTING = "agent_max_budget_usd"

_VERSION_CAP = 200
_MODEL_LINE_RE = re.compile(r"^[^\s/]+/\S+$")


# -- this Mac's agents -------------------------------------------------------------------


def _agent_of(item: object) -> labels.Agent:
    """One `{runner, model}` entry as an `Agent`; ValueError when it is not valid."""
    if not isinstance(item, Mapping):
        raise ValueError("an agent must be an object with a runner and a model")
    runner = item.get("runner")
    if not isinstance(runner, str) or runner not in labels.RUNNERS:
        raise ValueError(f"the runner must be one of {', '.join(labels.RUNNERS)}, not {runner!r}")
    return labels.Agent(runner, labels.check_model(item.get("model")))


def agents() -> list[labels.Agent]:
    """This Mac's saved agents in saved order: invalid entries dropped, a
    repeated agent kept once, at most `MAX_AGENTS`."""
    saved = settings.read_settings().get(AGENTS_SETTING)
    out: list[labels.Agent] = []
    if not isinstance(saved, list):
        return out
    for item in saved:
        try:
            agent = _agent_of(item)
        except ValueError:
            continue
        if agent not in out:
            out.append(agent)
        if len(out) >= MAX_AGENTS:
            break
    return out


def set_agents(items: object) -> list[labels.Agent]:
    """Replace this Mac's agent list with `items` (`[{runner, model}]`).
    ValueError, nothing written, when `items` is not a list, an entry is not
    valid, or more than `MAX_AGENTS` remain after dropping repeats. An empty
    list removes the key (17.1: written only when the person changes it)."""
    if not isinstance(items, (list, tuple)):
        raise ValueError("agents must be a list of {runner, model}")
    chosen: list[labels.Agent] = []
    for item in items:
        agent = _agent_of(item)
        if agent not in chosen:
            chosen.append(agent)
    if len(chosen) > MAX_AGENTS:
        raise ValueError(f"at most {MAX_AGENTS} agents can be saved")
    data = settings.read_settings()
    if chosen:
        data[AGENTS_SETTING] = [{"runner": a.runner, "model": a.model} for a in chosen]
    else:
        data.pop(AGENTS_SETTING, None)
    settings.write_json(settings.settings_path(), data)
    return chosen


# -- where the programs are --------------------------------------------------------------


def _valid_command(value: object) -> bool:
    return (isinstance(value, str) and bool(value) and os.path.isabs(value)
            and "\0" not in value and "\n" not in value and "\r" not in value)


def commands() -> dict[str, str]:
    """The saved program locations that are usable as settings: known
    runners with an absolute path. Whether the file exists is `find`'s
    business, not this function's."""
    saved = settings.read_settings().get(COMMANDS_SETTING)
    if not isinstance(saved, dict):
        return {}
    return {runner: saved[runner] for runner in labels.RUNNERS if _valid_command(saved.get(runner))}


def set_commands(mapping: object) -> dict[str, str]:
    """Save program locations: `{runner: absolute path}`; a runner mapped to
    `""` (or None) is removed; runners not named keep what they have.
    ValueError, nothing written, for an unknown runner or a path that is not
    absolute / has a NUL or a line break. The file need not exist (it may be
    on a volume that is not mounted yet)."""
    if not isinstance(mapping, Mapping):
        raise ValueError("commands must be an object mapping a runner to a path")
    updates: dict[str, str | None] = {}
    for runner, value in mapping.items():
        if not isinstance(runner, str) or runner not in labels.RUNNERS:
            raise ValueError(f"unknown runner {runner!r}; expected one of {', '.join(labels.RUNNERS)}")
        if value is None or (isinstance(value, str) and not value.strip()):
            updates[runner] = None
            continue
        if not isinstance(value, str):
            raise ValueError(f"the path for {runner} must be text")
        value = value.strip()
        if not _valid_command(value):
            raise ValueError(f"the path for {runner} must be an absolute path on one line, not {value!r}")
        updates[runner] = value
    merged = commands()
    for runner, value in updates.items():
        if value is None:
            merged.pop(runner, None)
        else:
            merged[runner] = value
    data = settings.read_settings()
    if merged:
        data[COMMANDS_SETTING] = merged
    else:
        data.pop(COMMANDS_SETTING, None)
    settings.write_json(settings.settings_path(), data)
    _VERSIONS.clear()  # a saved location is asked about afresh (18.4 item 7)
    return commands()


def search_path(base: str | None = None) -> str:
    """`base` (default: this process's `PATH`) widened with `EXTRA_PATH_DIRS`
    (`~` expanded), repeats dropped, order kept. Empty entries are dropped:
    in a `PATH` they mean the current directory."""
    if base is None:
        base = os.environ.get("PATH", "")
    seen: set[str] = set()
    out: list[str] = []
    for entry in [*base.split(os.pathsep), *(os.path.expanduser(d) for d in EXTRA_PATH_DIRS)]:
        if entry and entry not in seen:
            seen.add(entry)
            out.append(entry)
    return os.pathsep.join(out)


def _run(argv: list[str], *, env: Mapping[str, str], timeout: float) -> tuple[int, str, str]:
    """Run `argv` (stdin closed, own session) and return (exit code, stdout,
    stderr). A timeout kills the whole group and raises TimeoutError; a
    program that cannot start raises OSError."""
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=dict(env), start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
        try:
            proc.communicate(timeout=2)
        except Exception:  # noqa: BLE001 -- the group is dead; pipes are being abandoned
            pass
        raise TimeoutError(f"{argv[0]} did not answer within {timeout:g} seconds") from None
    return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def _program_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = search_path(env.get("PATH", ""))
    env["OPENCODE_DISABLE_AUTOUPDATE"] = "1"
    return env


@dataclass(frozen=True)
class Found:
    """What `find` learned about one runner's program."""

    runner: str
    path: str | None
    version: str | None
    #: "missing" | "not_executable" | "no_version" | None
    problem: str | None

    @property
    def found(self) -> bool:
        return self.path is not None and self.problem in (None, "no_version")

    def payload(self) -> dict[str, Any]:
        return {"runner": self.runner, "label": labels.RUNNER_LABELS[self.runner], "path": self.path,
                "version": self.version, "problem": self.problem, "found": self.found}


def _is_executable_file(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def _version_of(path: str) -> str | None:
    """The first non-empty stdout line of `path --version`, or None when the
    program fails, prints nothing or does not answer in time."""
    try:
        code, out, _err = _run([path, "--version"], env=_program_env(), timeout=VERSION_TIMEOUT_S)
    except (OSError, TimeoutError):
        return None
    if code != 0:
        return None
    for line in out.splitlines():
        line = line.strip()
        if line:
            return line[:_VERSION_CAP]
    return None


#: What `--version` answered, kept while the program is the same file (18.4 item 7): by the path as `find`
#: resolved it, the file's device, inode, size and modification time, and the `PATH` the program is started
#: with. Only a version is kept: a program that failed, said nothing or timed out gave no answer, and is asked
#: again. A file whose modification time is less than `memo.RACY_NS` old is not kept either.
_VERSIONS = memo.Memo(32)


def _remembered_version(path: str) -> str | None:
    """`_version_of(path)`, asked again only when something it depends on has moved: the file (its stamp, looked at
    before and after the program ran, so a file replaced while `--version` was running is not taken for the old
    one), its path, or the `PATH` it runs with. A file that cannot be looked at is asked as it always was."""
    began = time.time_ns()
    try:
        before = os.stat(path)
    except OSError:
        return _version_of(path)
    key = (path, memo.stamp(before), _program_env().get("PATH", ""))
    kept = _VERSIONS.get(key)
    if kept is not None:
        return kept
    version = _version_of(path)
    if version is not None and memo.settled(before, began):
        try:
            if memo.stamp(os.stat(path)) == key[1]:
                _VERSIONS.put(key, version)
        except OSError:
            pass
    return version


def find(runner: str, *, check_version: bool = True) -> Found:
    """Where `runner`'s program is and whether it answers (17.1). The
    person's saved path wins and is never second-guessed by a `PATH`
    search: a saved path that is not an executable file is reported as
    such. `check_version=False` skips the `--version` call (the answer then
    has no version and no "no_version" problem)."""
    if runner not in PROGRAMS:
        raise ValueError(f"unknown runner {runner!r}")
    saved = commands().get(runner)
    if saved is not None:
        if not os.path.lexists(saved):
            return Found(runner, None, None, "missing")
        if not _is_executable_file(saved):
            return Found(runner, saved, None, "not_executable")
        path = saved
    else:
        found = shutil.which(PROGRAMS[runner], path=search_path())
        if found is None:
            return Found(runner, None, None, "missing")
        path = found
    if not check_version:
        return Found(runner, path, None, None)
    version = _remembered_version(path)
    return Found(runner, path, version, None if version else "no_version")


# -- settings: limits ----------------------------------------------------------------------


def _blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _number(value: object, what: str) -> float:
    """`value` as a finite number (a number or its text); ValueError otherwise."""
    if isinstance(value, bool):
        raise ValueError(f"{what} must be a number")
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            raise ValueError(f"{what} must be a number, not {value!r}") from None
    else:
        raise ValueError(f"{what} must be a number")
    if not math.isfinite(number):
        raise ValueError(f"{what} must be a finite number")
    return number


def _valid_timeout(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if TIMEOUT_RANGE[0] <= value <= TIMEOUT_RANGE[1] else None


def timeout_minutes() -> int:
    """The run's time limit in minutes: the saved one, else the default."""
    saved = _valid_timeout(settings.read_settings().get(TIMEOUT_SETTING))
    return saved if saved is not None else DEFAULT_TIMEOUT_MINUTES


def set_timeout_minutes(value: object) -> int:
    """Save the time limit (a whole number of minutes in `TIMEOUT_RANGE`);
    None or "" removes the setting. Returns the limit now in force."""
    data = settings.read_settings()
    if _blank(value):
        data.pop(TIMEOUT_SETTING, None)
    else:
        number = _number(value, "the time limit")
        if number != int(number) or not TIMEOUT_RANGE[0] <= number <= TIMEOUT_RANGE[1]:
            raise ValueError(f"the time limit must be a whole number of minutes from {TIMEOUT_RANGE[0]} "
                             f"to {TIMEOUT_RANGE[1]}")
        data[TIMEOUT_SETTING] = int(number)
    settings.write_json(settings.settings_path(), data)
    return timeout_minutes()


def _valid_budget(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and 0 < number <= MAX_BUDGET_LIMIT_USD else None


def max_budget_usd() -> float | None:
    """The saved spending cap for one Claude Code run, or None."""
    return _valid_budget(settings.read_settings().get(BUDGET_SETTING))


def set_max_budget_usd(value: object) -> float | None:
    """Save the spending cap (more than 0, at most 1000 US dollars); None or
    "" removes it. Returns the cap now in force."""
    data = settings.read_settings()
    if _blank(value):
        data.pop(BUDGET_SETTING, None)
    else:
        number = _number(value, "the budget")
        if not 0 < number <= MAX_BUDGET_LIMIT_USD:
            raise ValueError(f"the budget must be more than 0 and at most {MAX_BUDGET_LIMIT_USD} US dollars")
        data[BUDGET_SETTING] = number
    settings.write_json(settings.settings_path(), data)
    return max_budget_usd()


# -- the MCP extra and OpenCode's models -----------------------------------------------------


def mcp_available() -> bool:
    """Whether `mcp.server.fastmcp` can be imported in this Python (17.1),
    decided without importing anything of `mcp`: the top-level spec is
    located and the submodule looked for beside it."""
    try:
        spec = importlib.util.find_spec("mcp")
        if spec is None:
            return False
        for location in spec.submodule_search_locations or ():
            base = Path(location) / "server"
            if (base / "fastmcp.py").is_file() or (base / "fastmcp" / "__init__.py").is_file():
                return True
        return False
    except (ImportError, ValueError, AttributeError, OSError):
        return False


def opencode_models(path: str) -> list[str]:
    """`opencode models` as `provider/model` names, in the program's order,
    repeats dropped. RuntimeError(message) when the program cannot be run,
    fails or does not answer in `MODELS_TIMEOUT_S` seconds."""
    try:
        code, out, err = _run([path, "models"], env=_program_env(), timeout=MODELS_TIMEOUT_S)
    except TimeoutError as exc:
        raise RuntimeError(str(exc)) from None
    except OSError as exc:
        raise RuntimeError(f"could not run {path}: {exc}") from None
    if code != 0:
        detail = next((line.strip() for line in (err or out).splitlines() if line.strip()), "")
        raise RuntimeError(f"opencode models failed (exit {code})" + (f": {detail[:300]}" if detail else ""))
    models: list[str] = []
    for line in out.splitlines():
        line = line.strip()
        if _MODEL_LINE_RE.match(line) and line not in models:
            models.append(line)
    return models
