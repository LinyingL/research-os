"""Agent names, owner values and history attributions (DESIGN.md 17.1, 17.2, 17.5).

An **agent** is a runner plus a model. It travels in three spellings, all
produced and read only here so that no other module parses them by hand:

- the task `owner` in `research/tasks.json`: `agent:<runner>:<model>`, e.g.
  `agent:claude-code:sonnet`. It names *which* agent, never a path or an
  address: the file travels to the team, the program lives on one Mac;
- the label people read: `Claude Code · sonnet` (a middle dot with spaces);
- the change history's `who`: `agent:<runner>:<model>#<person id>`, the
  person who started the run after the `#` (omitted when unknown).

The model is a command-line value, so it is validated hard: 1-120 characters
of letters, digits and `. _ : / @ [ ] + -`, starting with a letter or digit
(never `-`). Import-light on purpose (`re` and dataclasses only): the
workspace, the history and the agent package all use it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

RUNNERS = ("claude-code", "opencode", "codex", "deepseek-harness")
RUNNER_LABELS = {"claude-code": "Claude Code", "opencode": "OpenCode", "codex": "Codex",
                 "deepseek-harness": "DeepSeek Harness"}
OWNER_PREFIX = "agent:"
MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@\[\]+-]{0,119}\Z")


@dataclass(frozen=True)
class Agent:
    runner: str
    model: str

    @property
    def owner(self) -> str:
        return f"{OWNER_PREFIX}{self.runner}:{self.model}"

    @property
    def label(self) -> str:
        return f"{RUNNER_LABELS.get(self.runner, self.runner)} · {self.model}"

    def payload(self) -> dict[str, str]:
        return {"runner": self.runner, "model": self.model, "owner": self.owner, "label": self.label}


def check_model(model: object) -> str:
    """The model name, stripped; ValueError when it is not a valid one."""
    if not isinstance(model, str) or not MODEL_RE.match(model.strip()):
        raise ValueError("A model name is 1 to 120 characters of letters, digits and . _ : / @ [ ] + -, "
                         "starting with a letter or digit")
    return model.strip()


def harness_route(model: object) -> tuple[str, str]:
    """The explicit official Harness authentication route and model ID."""
    provider, _, name = check_model(model).partition("/")
    if provider not in ("deepseek-account", "deepseek-official") or not name:
        raise ValueError("Harness model must be deepseek-account/<model> or deepseek-official/<model>")
    return provider, name


def parse_owner(owner: object) -> Agent | None:
    """The agent an `owner` value names; None when it is not an agent owner
    (not text, or not starting with `agent:`); ValueError when it starts
    with `agent:` but names no known runner or no valid model."""
    if not isinstance(owner, str) or not owner.startswith(OWNER_PREFIX):
        return None
    runner, _, model = owner[len(OWNER_PREFIX):].partition(":")
    if runner not in RUNNERS:
        raise ValueError("An agent owner must name a runner and a model")
    if not MODEL_RE.match(model):  # strict: an owner value is canonical, never stripped
        raise ValueError("An agent owner must name a runner and a model")
    if runner == "deepseek-harness":
        harness_route(model)
    return Agent(runner, model)


def who_value(agent: Agent, person: str | None) -> str:
    """The history `who` for a change `agent` made for `person`."""
    return f"{agent.owner}#{person}" if person else agent.owner


def parse_who(who: object) -> tuple[Agent, str | None] | None:
    """Inverse of `who_value`; None when `who` is not an agent's."""
    if not isinstance(who, str) or not who.startswith(OWNER_PREFIX):
        return None
    owner, _, person = who.partition("#")
    try:
        agent = parse_owner(owner)
    except ValueError:
        return None
    return (agent, person or None) if agent is not None else None
