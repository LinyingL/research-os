"""Claude Code's PreToolUse hook: `python -m researchos.agents.hook <run dir>`
(DESIGN.md 17.5).

Claude Code runs this before every Edit, Write, MultiEdit, NotebookEdit and
Bash call of an agent run, with the call as JSON on standard input
(`tool_name`, `tool_input`, `cwd`, ...). Exit code 0 lets the call go ahead;
exit code 2 refuses it and Claude Code hands standard error to the model, so
the message says what to do instead. The decision is `guard.check_tool` on
the run's `guard.json`; a refusal is also appended to `refusals.jsonl`,
where the run log shows it.

**A hook that cannot decide refuses.** Unreadable input, input over 10 MB,
a missing or damaged `guard.json`, any exception at all: exit code 2 with a
sentence saying so. Nothing is ever allowed by default because something
went wrong.

Only the standard library and `researchos.agents.guard` / `runs` are
imported, and none of them imports the records package or the database at
load time: this process starts once per tool call.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from researchos.agents import guard

INPUT_LIMIT = 10 * 1024 * 1024
PREFIX = "Research OS: "


def _say(message: str) -> None:
    """Write to standard error as UTF-8 whatever the locale (a path with
    non-ASCII letters must not turn a refusal into a crash)."""
    data = (PREFIX + message + "\n").encode("utf-8", "replace")
    try:
        sys.stderr.buffer.write(data)
        sys.stderr.buffer.flush()
    except (AttributeError, OSError, ValueError):
        sys.stderr.write(data.decode("utf-8", "replace"))


def _target(tool_name: str, tool_input: object) -> str:
    """What the call was about, for the refusal log."""
    if not isinstance(tool_input, dict):
        return ""
    for key in ("file_path", "notebook_path", "command"):
        value = tool_input.get(key)
        if isinstance(value, str):
            return value
    return ""


def _log(run_dir: Path, tool_name: str, target: str, reason: str) -> None:
    try:
        guard.log_refusal(run_dir, tool_name, target, reason)
    except Exception:  # noqa: BLE001 -- the refusal itself must still go out
        pass


def main(argv: list[str] | None = None) -> int:
    """The hook: 0 to allow the call, 2 to refuse it."""
    args = sys.argv[1:] if argv is None else argv
    run_dir = Path(args[0]) if args else None
    tool_name, target = "unknown", ""
    try:
        if run_dir is None:
            raise ValueError("no run folder was given to the hook")
        raw = sys.stdin.buffer.read(INPUT_LIMIT + 1)
        if len(raw) > INPUT_LIMIT:
            raise ValueError("the tool call is larger than 10 MB")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("tool_name"), str):
            raise ValueError("the tool call has no tool_name")
        tool_name = payload["tool_name"]
        tool_input = payload.get("tool_input")
        if tool_input is None:
            tool_input = {}
        target = _target(tool_name, tool_input)
        cwd = payload.get("cwd")
        reason = guard.check_tool(guard.load_guard(run_dir), tool_name, tool_input,  # type: ignore[arg-type]
                                  cwd if isinstance(cwd, str) else None)
    except Exception as exc:  # noqa: BLE001 -- fail closed on anything
        reason = f"this call was refused because Research OS could not check it ({type(exc).__name__}: {exc})."
        if run_dir is not None:
            _log(run_dir, tool_name, target, reason)
        _say(reason)
        return 2
    if reason:
        _log(run_dir, tool_name, target, reason)  # type: ignore[arg-type]
        _say(reason)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
