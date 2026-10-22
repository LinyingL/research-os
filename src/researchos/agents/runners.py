"""The two runners: how each program is started, and how its output is read
(DESIGN.md 17.4). Everything here is a pure function of its arguments --
no process is started, no file written: `Launch` is a description (argv,
environment, standard input, working folder and the small files the run
folder must hold) that the run manager carries out, and the stream parsers
are fed one stdout line at a time.

**Claude Code** is `claude -p --output-format stream-json --verbose`, the
model, a fresh `--session-id` (or `--resume`), `--permission-mode dontAsk`
with the tools it may use in `--allowedTools` and the denials of 17.5 in
`--disallowedTools`, a `PreToolUse` hook and the MCP server from files in the
run's folder (`--settings`, `--mcp-config --strict-mcp-config`), and the rules
in `--append-system-prompt`. `--setting-sources user` keeps the project's own
`.claude/settings*.json` from being loaded (a run could have written one for
the next, 18.1 item 2); the hook runs on the editing tools, Bash and the
reading tools (Read, Glob, Grep, LS). `--allowedTools` and `--disallowedTools` are
variadic: a positional prompt after them would be swallowed as a tool name,
so they come LAST and the instruction goes in on standard input.
The hook's command is `sh -c` around the hook (`hook_command`) so that only
its exit code 0 allows a call and every other outcome (a crash at start, a
missing interpreter) is exit code 2, which Claude Code treats as a refusal;
`hook_canary` runs that exact command on a call that must be refused, for
the manager to do before a run. `--settings` also says `disableAllHooks:
false`, and both Python commands run isolated (`-I`).

**OpenCode** is `opencode run --format json --model <m> --dir <project>`
(`--session <id>` to continue). A message given as an argument reaches the
model re-quoted, so the rules and the instruction go in on standard input.
Its configuration -- permissions and the MCP server -- travels in
`OPENCODE_CONFIG_CONTENT`; a project's own `opencode.json` is switched off
(`OPENCODE_DISABLE_PROJECT_CONFIG`) because it could allow what we deny.
Every permission is `allow` or `deny`, never `ask`: an unattended run rejects
what would ask and stops.

**The environment** is the engine's, less everything that could join a
Claude Code session that started Research OS (`CLAUDE_CODE_REMOTE_SESSION_ID`
makes a child adopt its parent's session) or redirect OpenCode's
configuration, less Research OS's home (`RESEARCHOS_HOME`, `RCE_HOME`: only the
MCP server's own entry names it) and less every variable that looks like a
credential, except what the runner needs to reach its model (`credential_names`,
18.1 item 2; the names removed, never the values, come back in `Launch.removed_env`
for the run log). `PATH` is widened with the usual install places, since an app
opened from the Finder has none of the shell's. The agent's processes carry
`RESEARCHOS_AGENT_RUN`, which makes every Research OS write path refuse
(17.5).

**The streams** (`ClaudeStream`, `OpenCodeStream`) read the program's JSON
lines tolerantly -- an unknown event type, a missing field or a line that is
not JSON is ignored, never an error -- into *steps*
(`{"kind", "text", "tool"}`: start, say, tool, refused, error, end) and keep
the session id, model and cost. `outcome` reads the stream together with the
exit code: Claude Code's `subtype` is not enough (a bad model is
`subtype: "success"` with `is_error: true`), and a SIGTERM leaves no result.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

MCP_KEY = "researchos"
AGENT_TOOLS = ("researchos_complete_task", "researchos_ask", "researchos_save_note",
               "researchos_find", "researchos_trace", "researchos_status")
CLAUDE_ALLOWED = ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "Glob", "Grep", "LS", "Bash",
                  "WebFetch", "WebSearch", "TodoWrite", "Task", "mcp__researchos")
GIT_DENIED = ("push", "commit", "reset", "rebase", "merge", "checkout", "switch", "restore", "stash", "clean",
              "tag", "cherry-pick", "revert")
CLAUDE_DISALLOWED = tuple(f"Bash(git {c}:*)" for c in GIT_DENIED) + ("Bash(researchos:*)", "Bash(rce:*)")
#: Inherited variables that would join a Claude Code session (CLAUDE_CODE_REMOTE_SESSION_ID
#: is the one that makes a child adopt its parent's id), reach a parent's messaging channel, or
#: replace OpenCode's configuration.
SCRUB_ENV_RE = re.compile(
    r"^(CLAUDECODE|CLAUDE_CODE_.*(SESSION|REMOTE|ENTRYPOINT|CHILD|MESSAGING|CONTAINER|WORKER).*"
    r"|CLAUDE_SESSION_.*|CCR_.*|RCE_ENGINE_PORT|OPENCODE_CONFIG_CONTENT|OPENCODE_CONFIG"
    r"|RESEARCHOS_HOME|RCE_HOME)$")  # the home: the agent is not told where Research OS keeps its files (18.1 item 2)
#: Variables that could widen what OpenCode allows beyond the configuration we pass; removed too.
OPENCODE_OVERRIDE_ENV = ("OPENCODE_PERMISSION",)
#: A variable whose name looks like it holds a credential is not handed to the agent program (18.1 item 2): the name
#: contains one of these words, or is one of the exact names, or starts with one of the prefixes.
CREDENTIAL_WORDS = ("TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "PRIVATE", "API_KEY", "ACCESS_KEY", "AUTH",
                    "SESSION", "COOKIE")
CREDENTIAL_NAMES = frozenset({"SSH_AUTH_SOCK", "GPG_AGENT_INFO", "RCE_LLM_API_KEY"})
CREDENTIAL_PREFIXES = ("AWS_", "GITHUB_", "GH_")
#: What each runner keeps although it looks like a credential: it needs it to reach its model. Exact names, prefixes
#: and, for the prefixes in `_KEEP_IF`, the variable that has to be set for the prefix to count.
_KEEP = {
    "claude-code": {"names": frozenset({"CLAUDE_CODE_OAUTH_TOKEN"}), "prefixes": ("ANTHROPIC_",)},
    "opencode": {"names": frozenset({"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY",
                                     "GROQ_API_KEY", "MISTRAL_API_KEY", "DEEPSEEK_API_KEY"}),
                 "prefixes": ("GOOGLE_",)},
}
_KEEP_IF = {"claude-code": (("AWS_", "CLAUDE_CODE_USE_BEDROCK"), ("GOOGLE_", "CLAUDE_CODE_USE_VERTEX"))}

STEP_TEXT_LIMIT = 500
TOOL_SUMMARY_LIMIT = 300
#: Same shape as labels.MODEL_RE, kept here so the argv safety (a value starting with `-`
#: would be read as an option) does not depend on another module.
_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@\[\]+-]{0,119}\Z")
_SESSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
#: Exit codes of a program ended by a signal: -15/143 SIGTERM (Claude Code answers SIGTERM with
#: 143 and no result), -9/137 SIGKILL.
ABORT_EXIT_CODES = frozenset({143, -15, 137, -9})


@dataclass
class Launch:
    """How to start one run: the manager writes `files` (name -> text) into
    the run folder, then starts `argv` in `cwd` with `env` and writes
    `stdin` (utf-8) to the program."""

    argv: list[str]
    env: dict[str, str]
    stdin: str
    cwd: str
    files: dict[str, str] = field(default_factory=dict)
    #: Names (never values) of the environment variables left out as credentials (18.1 item 2); the run log lists them.
    removed_env: list[str] = field(default_factory=list)


# -- starting ---------------------------------------------------------------


def mcp_command(root: Path, run_id: str) -> list[str]:
    """The command that serves Research OS's agent tools for this run (17.4):
    the engine's own interpreter, so the `mcp` extra the engine has is the
    one used. `-I` (isolated mode) keeps the project folder, which both
    programs make the working directory, off `sys.path` and ignores
    `PYTHON*` variables: a project's own `random.py` or `types.py` must not
    replace the standard library under the server."""
    return [sys.executable, "-I", "-m", "researchos.cli", "mcp", "--agent-run", run_id, "--path", str(root)]


def credential_like(name: str) -> bool:
    """Whether a variable's name looks like it holds a credential (`CREDENTIAL_WORDS`, `CREDENTIAL_NAMES`,
    `CREDENTIAL_PREFIXES`; upper or lower case)."""
    upper = name.upper()
    return upper in CREDENTIAL_NAMES or upper.startswith(CREDENTIAL_PREFIXES) or any(w in upper for w in CREDENTIAL_WORDS)


def credential_names(base: Mapping[str, str], runner: str) -> list[str]:
    """The names in `base` that `child_env` removes as credentials for `runner`, sorted: those that look like one
    (`credential_like`) and are not among what the runner keeps to reach its model -- for Claude Code `ANTHROPIC_*`
    and `CLAUDE_CODE_OAUTH_TOKEN`, `AWS_*` only when `CLAUDE_CODE_USE_BEDROCK` is set and `GOOGLE_*` only when
    `CLAUDE_CODE_USE_VERTEX` is; for OpenCode its providers' API keys and `GOOGLE_*`."""
    keep = _KEEP.get(runner, {"names": frozenset(), "prefixes": ()})
    prefixes = tuple(keep["prefixes"]) + tuple(p for p, variable in _KEEP_IF.get(runner, ()) if base.get(variable))
    return sorted(k for k in base if isinstance(k, str) and credential_like(k)
                  and k not in keep["names"] and not k.startswith(prefixes))


def child_env(base: Mapping[str, str], *, run_id: str, program: str, runner: str = "claude-code") -> dict[str, str]:
    """The environment of the agent program: `base` less `SCRUB_ENV_RE` (the home among it) and less
    `credential_names`; `PATH` widened (`profiles.search_path`) with the program's own folder first (a
    `#!/usr/bin/env node` launcher finds its node beside it); `RESEARCHOS_AGENT_RUN` set so every process the agent
    starts is marked. `RESEARCHOS_HOME` is not set: only the MCP server's entry names the home (18.1 item 2)."""
    from researchos.agents import profiles  # lazily: package A, and this module stays importable alone

    drop = set(credential_names(base, runner))
    env = {k: v for k, v in base.items() if isinstance(k, str) and not SCRUB_ENV_RE.match(k) and k not in drop}
    parts = [p for p in profiles.search_path(base.get("PATH")).split(os.pathsep) if p]
    first = os.path.dirname(program) if isinstance(program, str) else ""
    if first:
        parts = [first] + [p for p in parts if p != first]
    env["PATH"] = os.pathsep.join(parts)
    env["RESEARCHOS_AGENT_RUN"] = run_id
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _check_model(model: object) -> str:
    if not isinstance(model, str) or not _MODEL_RE.match(model):
        raise ValueError(f"not a valid model name: {model!r}")
    return model


def _check_session(session_id: object) -> str:
    if not isinstance(session_id, str) or not _SESSION_RE.match(session_id):
        raise ValueError(f"not a valid session id: {session_id!r}")
    return session_id


def _json_file(data: object) -> str:
    return json.dumps(data, indent=2, ensure_ascii=True) + "\n"


def _budget(value: float) -> str:
    """A positive finite number as a plain decimal (never `1e-05`)."""
    number = Decimal(str(value))
    if not number.is_finite() or number <= 0:
        raise ValueError(f"not a valid budget: {value!r}")
    return format(number, "f")


#: The hook runs under `sh -c` so that ONLY the hook's own exit code 0 allows a call (17.5). Claude
#: Code blocks on exit code 2 alone and lets any other non-zero code through; a hook that cannot
#: start (a project file shadowing a standard-library module, an interpreter that was moved, exit
#: 1 or 127) would otherwise leave the whole guard off. `-I` keeps the project folder (the hook's
#: working directory) off `sys.path` and ignores `PYTHON*` variables.
HOOK_SHELL = '"$0" -I -m researchos.agents.hook "$1"; s=$?; [ "$s" -eq 0 ] && exit 0; exit 2'


def hook_command(run_dir: Path, python: str | None = None) -> str:
    """The `PreToolUse` hook (a shell string; Claude Code runs it through a
    shell): `sh -c HOOK_SHELL <python> <run dir>`, so it exits 0 or 2 whatever
    the interpreter does. `python` is the engine's own interpreter unless
    given."""
    return shlex.join(["sh", "-c", HOOK_SHELL, python or sys.executable, str(run_dir)])


def hook_canary(run_dir: Path) -> str | None:
    """Run the exact hook command string (`hook_command`) through `/bin/sh`,
    in the project folder as Claude Code does, with a call that must be
    refused (a Write to a protected file named in `guard.json`, else to
    `.researchos/x`). None when the hook answers exit code 2 and its own
    refusal text is on standard error; otherwise a sentence saying what
    happened, and the run must not start (a hook that does not refuse
    leaves the agent unguarded). The refusal this probe makes is taken back
    out of `refusals.jsonl`. Never raises."""
    run_dir = Path(run_dir)
    log = run_dir / "refusals.jsonl"
    try:
        guard = json.loads((run_dir / "guard.json").read_bytes().decode("utf-8"))
        root = guard["root"]
        files = [f for f in guard.get("files", ()) if isinstance(f, str) and f]
        target = os.path.join(root, *files[0].split("/")) if files else os.path.join(root, ".researchos", "x")
        payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": target, "content": "x"},
                              "cwd": root, "hook_event_name": "PreToolUse"}).encode("utf-8")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return f"the hook probe could not read guard.json ({type(exc).__name__}: {exc})"
    try:
        size = log.stat().st_size
    except OSError:
        size = None
    try:
        done = subprocess.run(["/bin/sh", "-c", hook_command(run_dir)], input=payload, capture_output=True,
                              timeout=60, cwd=root, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"the hook probe could not run ({type(exc).__name__}: {exc})"
    finally:
        try:  # the probe's own refusal is not the agent's
            if size is None:
                log.unlink()
            else:
                os.truncate(log, size)
        except OSError:
            pass
    if done.returncode != 2:
        return f"the hook probe was not refused (exit code {done.returncode}, expected 2)"
    if b"Research OS:" not in done.stderr:
        return "the hook probe was refused, but not by the Research OS hook: " + done.stderr.decode(
            "utf-8", "replace").strip()[:300]
    return None


def claude_launch(*, program: str, model: str, root: Path, run_dir: Path, run_id: str, session_id: str,
                  resume: bool, prompt: str, rules: str, max_budget_usd: float | None, home: str,
                  base_env: Mapping[str, str]) -> Launch:
    """Claude Code: see the module docstring. The variadic tool lists come
    last; `prompt` goes to standard input and is never in `argv`."""
    argv = [program, "-p", "--output-format", "stream-json", "--verbose",
            "--model", _check_model(model),
            "--resume" if resume else "--session-id", _check_session(session_id),
            "--permission-mode", "dontAsk",
            "--setting-sources", "user",  # not the project's .claude/settings*.json (18.1 item 2)
            "--settings", str(run_dir / "claude-settings.json"),
            "--mcp-config", str(run_dir / "mcp.json"),
            "--strict-mcp-config",
            "--append-system-prompt", rules]
    if max_budget_usd is not None:
        argv += ["--max-budget-usd", _budget(max_budget_usd)]
    argv += ["--allowedTools", *CLAUDE_ALLOWED, "--disallowedTools", *CLAUDE_DISALLOWED]
    settings = {"disableAllHooks": False,  # a user-level `disableAllHooks: true` must not switch the guard off
                "hooks": {"PreToolUse": [{
                    "matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash|Read|Glob|Grep|LS",
                    "hooks": [{"type": "command", "command": hook_command(run_dir)}]}]}}
    mcp = {"mcpServers": {MCP_KEY: {
        "command": sys.executable, "args": mcp_command(root, run_id)[1:],
        "env": {"RESEARCHOS_HOME": home, "RESEARCHOS_AGENT_RUN": run_id}}}}
    return Launch(argv=argv,
                  env=child_env(base_env, run_id=run_id, program=program, runner="claude-code"),
                  stdin=prompt, cwd=str(root),
                  files={"claude-settings.json": _json_file(settings), "mcp.json": _json_file(mcp)},
                  removed_env=credential_names(base_env, "claude-code"))


def opencode_config(root: Path, run_id: str, home: str) -> dict[str, Any]:
    """OpenCode's configuration for a run: the MCP server and permissions.
    In `permission.bash` the later rule wins, so `*` comes first."""
    bash: dict[str, str] = {"*": "allow"}
    for command in GIT_DENIED:
        bash[f"git {command}*"] = "deny"
    for command in GIT_DENIED:  # git's own options may come first: `git -C dir push`, `git -c a=b commit -m x`
        bash[f"git * {command}"] = "deny"
        bash[f"git * {command} *"] = "deny"
    bash["researchos*"] = "deny"
    bash["rce *"] = "deny"
    bash["rce"] = "deny"
    return {
        "$schema": "https://opencode.ai/config.json",
        "mcp": {MCP_KEY: {"type": "local", "command": mcp_command(root, run_id), "enabled": True,
                          "environment": {"RESEARCHOS_HOME": home, "RESEARCHOS_AGENT_RUN": run_id}}},
        "permission": {"edit": "allow", "webfetch": "allow", "external_directory": "deny",
                       "doom_loop": "deny",  # OpenCode's default is "ask"; an unattended run would stop on it
                       "read": {"*": "allow", "*.env": "deny", "*.env.*": "deny"},  # its default asks for .env files
                       "bash": bash},
    }


def opencode_launch(*, program: str, model: str, root: Path, run_dir: Path, run_id: str,
                    session_id: str | None, prompt: str, home: str,
                    base_env: Mapping[str, str]) -> Launch:
    """OpenCode: see the module docstring. `prompt` (the rules and the
    instruction, joined by the manager) goes to standard input."""
    argv = [program, "run", "--format", "json", "--model", _check_model(model), "--dir", str(root)]
    if session_id:
        argv += ["--session", _check_session(session_id)]
    env = child_env(base_env, run_id=run_id, program=program, runner="opencode")
    for name in OPENCODE_OVERRIDE_ENV:
        env.pop(name, None)
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(opencode_config(root, run_id, home), ensure_ascii=True)
    env["OPENCODE_DISABLE_PROJECT_CONFIG"] = "1"
    env["OPENCODE_DISABLE_AUTOUPDATE"] = "1"
    return Launch(argv=argv, env=env, stdin=prompt, cwd=str(root), files={},
                  removed_env=credential_names(base_env, "opencode"))


# -- reading the streams ----------------------------------------------------

_SPACE_RE = re.compile(r"\s+")


def _flat(text: object, limit: int) -> str:
    """Whitespace (newlines included) collapsed to single spaces, cut to `limit` characters."""
    out = _SPACE_RE.sub(" ", text if isinstance(text, str) else "" if text is None else str(text)).strip()
    return out if len(out) <= limit else out[: max(0, limit - 3)].rstrip() + "..."


def _step(kind: str, text: object, tool: str | None = None) -> dict[str, Any]:
    return {"kind": kind, "text": _flat(text, STEP_TEXT_LIMIT), "tool": tool}


#: Display name of a built-in tool (Claude Code spells them capitalised, OpenCode lowercase) and
#: the input fields its summary is read from, in order.
_TOOLS: dict[str, tuple[str, tuple[str, ...]]] = {
    "bash": ("Bash", ("command",)),
    "edit": ("Edit", ("file_path", "filePath", "path")),
    "write": ("Write", ("file_path", "filePath", "path")),
    "multiedit": ("MultiEdit", ("file_path", "filePath", "path")),
    "notebookedit": ("NotebookEdit", ("notebook_path", "file_path", "filePath", "path")),
    "read": ("Read", ("file_path", "filePath", "path")),
    "ls": ("LS", ("path", "file_path", "filePath")),
    "list": ("LS", ("path", "file_path", "filePath")),
    "glob": ("Glob", ("pattern", "path")),
    "grep": ("Grep", ("pattern", "path")),
    "webfetch": ("WebFetch", ("url",)),
    "websearch": ("WebSearch", ("query",)),
    "todowrite": ("TodoWrite", ()),
    "task": ("Task", ("description", "prompt")),
    "skill": ("Skill", ("name",)),
}


def _short_name(name: str) -> str:
    """A tool's own name: `mcp__researchos__researchos_ask` and OpenCode's
    `researchos_researchos_ask` both read `researchos_ask`."""
    if name.startswith("mcp__"):
        parts = name.split("__", 2)
        if len(parts) == 3 and parts[2]:
            return parts[2]
    double = f"{MCP_KEY}_{MCP_KEY}_"
    if name.startswith(double):
        return name[len(MCP_KEY) + 1:]
    return name


def summarize_tool(name: str, tool_input: dict) -> str:
    """One line for a tool call: `Bash: <command>`, `Edit: <path>`, `Read:
    <path>`, `Grep: <pattern>`, `WebFetch: <url>`, `WebSearch: <query>`; any
    other tool by its own name (an MCP tool without the server prefix, e.g.
    `researchos_complete_task`). At most `TOOL_SUMMARY_LIMIT` characters,
    newlines collapsed. Works for both programs' spellings and field names."""
    name = _short_name(name if isinstance(name, str) else str(name))
    known = _TOOLS.get(name.lower())
    if known is None:
        return _flat(name, TOOL_SUMMARY_LIMIT)
    display, keys = known
    data = tool_input if isinstance(tool_input, dict) else {}
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return _flat(f"{display}: {value}", TOOL_SUMMARY_LIMIT)
    return display


def _tool_label(name: str) -> str:
    """The `tool` field of a step: the display name of a built-in, else the short name."""
    short = _short_name(name)
    known = _TOOLS.get(short.lower())
    return known[0] if known else short


def _text_of(content: object) -> str:
    """A tool_result's content (a string, or a list of text blocks) as text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b["text"] for b in content
                         if isinstance(b, dict) and isinstance(b.get("text"), str))
    return ""


def _loads(line: str) -> dict | None:
    line = line.strip() if isinstance(line, str) else ""
    if not line.startswith("{"):
        return None
    try:
        event = json.loads(line)
    except (ValueError, RecursionError):  # nested too deeply to read is not an event either
        return None
    return event if isinstance(event, dict) else None


class ClaudeStream:
    """Reads Claude Code's `stream-json` lines (one event each; thinking,
    tool_use and text blocks arrive as separate `assistant` events)."""

    def __init__(self) -> None:
        self.session_id: str | None = None
        self.model: str | None = None
        self.cost_usd: float | None = None
        self.result: dict | None = None
        self.is_error = False
        self.result_text: str | None = None
        self.steps: list[dict] = []

    def feed(self, line: str) -> list[dict]:
        """The steps this line adds (also appended to `steps`); a line that
        is not a JSON object, or an event of no interest, adds none."""
        event = _loads(line)
        if event is None:
            return []
        if self.session_id is None and isinstance(event.get("session_id"), str) and event["session_id"]:
            self.session_id = event["session_id"]
        kind = event.get("type")
        steps: list[dict] = []
        try:
            if kind == "system":
                steps = self._system(event)
            elif kind == "assistant":
                steps = self._assistant(event)
            elif kind == "user":
                steps = self._user(event)
            elif kind == "result":
                steps = self._result(event)
        except (TypeError, AttributeError, KeyError):  # a shape we do not know: ignore, never raise
            steps = []
        self.steps.extend(steps)
        return steps

    def _system(self, event: dict) -> list[dict]:
        subtype = event.get("subtype")
        if subtype == "init":
            if isinstance(event.get("session_id"), str) and event["session_id"]:
                self.session_id = event["session_id"]
            model = event.get("model")
            if isinstance(model, str) and model:
                self.model = model
            steps = [_step("start", model if isinstance(model, str) else "")]  # the model's own name, no words of ours
            for server in event.get("mcp_servers") or ():
                if isinstance(server, dict) and server.get("name") == MCP_KEY and server.get("status") == "failed":
                    steps.append(_step("error", "The Research OS tools did not start (status: failed)."))
            return steps
        if subtype == "permission_denied":
            tool = event.get("tool_name")
            return [_step("refused", event.get("message") or f"{tool} was denied",
                          _tool_label(tool) if isinstance(tool, str) else None)]
        return []

    def _assistant(self, event: dict) -> list[dict]:
        content = (event.get("message") or {}).get("content")
        steps: list[dict] = []
        for block in content if isinstance(content, list) else ():
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str) and block["text"].strip():
                steps.append(_step("say", block["text"]))
            elif block.get("type") == "tool_use" and isinstance(block.get("name"), str):
                if block["name"] in _INTERNAL_TOOLS:  # Claude Code loading its own tools: not a step of the work
                    continue
                steps.append(_step("tool", summarize_tool(block["name"], block.get("input")),
                                   _tool_label(block["name"])))
        return steps

    def _user(self, event: dict) -> list[dict]:
        content = (event.get("message") or {}).get("content")
        steps: list[dict] = []
        for block in content if isinstance(content, list) else ():
            if not (isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error")):
                continue
            text = _text_of(block.get("content"))
            if not text.startswith("PreToolUse:"):  # a hook refusal; other errors are the tool's own
                continue
            tool = text[len("PreToolUse:"):].split(" ", 1)[0] or None
            marker = text.find("Research OS:")  # our hook's message, without Claude Code's wrapper
            steps.append(_step("refused", text[marker:] if marker >= 0 else text, tool))
        return steps

    def _result(self, event: dict) -> list[dict]:
        self.result = event
        self.is_error = bool(event.get("is_error"))
        cost = event.get("total_cost_usd")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            self.cost_usd = float(cost)
        if isinstance(event.get("session_id"), str) and event["session_id"]:
            self.session_id = event["session_id"]
        text = event.get("result")
        self.result_text = text if isinstance(text, str) else None
        errors = [e if isinstance(e, str) else json.dumps(e, ensure_ascii=False)
                  for e in event.get("errors") or () if e]
        shown = (self.result_text or "").strip() or "; ".join(errors) or str(event.get("subtype") or "")
        said = next((st["text"] for st in reversed(self.steps) if st["kind"] == "say"), None)
        if said is not None and _flat(shown, STEP_TEXT_LIMIT) == said:
            shown = ""  # the final answer was just shown as what it said: the end needs no second copy
        return [_step("end", shown)]


#: Claude Code's own bookkeeping calls (loading deferred tools), left out of the steps.
_INTERNAL_TOOLS = frozenset({"ToolSearch"})

#: OpenCode's error text when a permission rule, or the person's rejection, stopped a tool call.
_REFUSED_RE = re.compile(r"specified a rule which prevents|rejected permission|unavailable tool", re.I)


class OpenCodeStream:
    """Reads OpenCode's `run --format json` lines. There is no terminal
    event: the manager adds the "end" step when the process exits. `tool_use`
    arrives once, when the call completes or errors; `step_finish` carries
    the cost of one step, summed here."""

    def __init__(self) -> None:
        self.session_id: str | None = None
        self.model: str | None = None
        self.cost_usd: float | None = None
        self.result: dict | None = None
        self.is_error = False
        self.result_text: str | None = None
        self.error_message: str | None = None
        self.steps: list[dict] = []
        self._started = False

    def feed(self, line: str) -> list[dict]:
        event = _loads(line)
        if event is None:
            return []
        if isinstance(event.get("sessionID"), str) and event["sessionID"]:
            self.session_id = event["sessionID"]
        kind = event.get("type")
        steps: list[dict] = []
        try:
            if kind == "step_start" and not self._started:
                self._started = True
                steps = [_step("start", "")]
            elif kind == "tool_use":
                steps = self._tool(event.get("part") or {})
            elif kind == "text":
                text = (event.get("part") or {}).get("text")
                if isinstance(text, str) and text.strip():
                    self.result_text = text
                    steps = [_step("say", text)]
            elif kind == "step_finish":
                cost = (event.get("part") or {}).get("cost")
                if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                    self.cost_usd = (self.cost_usd or 0.0) + float(cost)
            elif kind == "error":
                steps = self._error(event.get("error") or {})
        except (TypeError, AttributeError, KeyError):
            steps = []
        self.steps.extend(steps)
        return steps

    def _tool(self, part: dict) -> list[dict]:
        name, state = part.get("tool"), part.get("state") or {}
        if not isinstance(name, str) or not isinstance(state, dict):
            return []
        status = state.get("status")
        tool_input = state.get("input") if isinstance(state.get("input"), dict) else {}
        label = _tool_label(name)
        if name == "invalid":  # a call to a tool that was removed by a deny rule, or with bad arguments
            output = state.get("output") if isinstance(state.get("output"), str) else ""
            return [_step("refused" if "unavailable tool" in output else "error", output or "invalid tool call", label)]
        summary = summarize_tool(name, tool_input)
        if status == "completed":
            return [_step("tool", summary, label)]
        if status == "error":
            error = state.get("error") if isinstance(state.get("error"), str) else ""
            reason = error.split(" Here are", 1)[0]  # the rule list that follows is long and no help
            return [_step("refused" if _REFUSED_RE.search(error) else "error",
                          f"{summary}: {reason}" if reason else summary, label)]
        return []

    def _error(self, error: dict) -> list[dict]:
        data = error.get("data") if isinstance(error.get("data"), dict) else {}
        message = data.get("message") if isinstance(data.get("message"), str) and data["message"] else None
        message = message or (error.get("message") if isinstance(error.get("message"), str) else None) \
            or (error.get("name") if isinstance(error.get("name"), str) else None) or "error"
        self.is_error = True
        self.error_message = message
        return [_step("error", message)]


# -- how a run ended ----------------------------------------------------------


def _failed(code: str, detail: object) -> tuple[str, dict]:
    return "failed", {"code": code, "detail": _flat(detail, 2000)}


def outcome(runner: str, exit_code: int | None, stream: ClaudeStream | OpenCodeStream) -> tuple[str, dict | None]:
    """`("ok", None)` or `("failed", {"code", "detail"})` from the exit code and the stream.

    Claude Code: ok needs exit 0 and a result that is not an error. A
    SIGTERM (143/-15, or a kill) or a stream without a result is `aborted`;
    a result with `is_error` (whatever its `subtype`) fails with the
    subtype as code and the errors or result text as detail; any other
    non-zero exit is `exit`. OpenCode: an `error` event is `model_error`
    with its message; else a non-zero exit is `exit`; else ok."""
    if runner == "claude-code":
        result = stream.result
        if exit_code in ABORT_EXIT_CODES:
            return _failed("aborted", f"the program was ended by a signal (exit code {exit_code})")
        if result is None:
            return _failed("aborted", "the program ended without a result"
                           + (f" (exit code {exit_code})" if exit_code not in (None, 0) else ""))
        if stream.is_error:
            subtype = result.get("subtype")
            code = subtype if isinstance(subtype, str) and subtype and subtype != "success" else "error"
            errors = [e if isinstance(e, str) else json.dumps(e, ensure_ascii=False)
                      for e in result.get("errors") or () if e]
            detail = "; ".join(errors) or (stream.result_text or "").strip() or code
            return _failed(code, detail)
        if exit_code not in (None, 0):
            return _failed("exit", f"exit code {exit_code}")
        return "ok", None
    if runner == "opencode":
        if stream.is_error:
            return _failed("model_error", getattr(stream, "error_message", None) or "error")
        if exit_code not in (None, 0):
            signal_note = " (ended by a signal)" if exit_code in ABORT_EXIT_CODES else ""
            return _failed("exit", f"exit code {exit_code}{signal_note}")
        return "ok", None
    raise ValueError(f"unknown runner: {runner!r}")
