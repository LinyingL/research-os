"""Handbacks (交回, DESIGN.md 21.2): which of them the person has not opened yet.

A handback is a run's report, the report's context and its registered outputs, from the moment the run ends with
`researchos_complete_task`. It is listed in 等你处理 and on its attempt's card until the person who started the run
opens it. That an agent run has been handed back is in the project (the task's `report`); that *this person* has
looked at it is not: "opened" is kept per person on this Mac under the Research OS home, never in the project (21.2).

This module is the interface the readers use -- the route map's cards (21.1, `attempt_tasks`) and, later, 项目首页's
「现在在做什么」 (21.4). The store itself, and the writes that mark a handback opened, come with the handback view
(task B1-3); until then every handback counts as opened, so nothing waits and no card says 等你看.
"""

from __future__ import annotations


def unopened(project_id: str | None, person: str | None) -> list[str]:
    """The handbacks `person` has not opened in the project `project_id`: the project paths of their result records
    (`research/results/<name>.md`), oldest first. `person` is the person this Mac belongs to (`proposals.role_of`'s
    `me`; None when none is set), `project_id` the project's identity (None when it has none): neither is a path
    or a name typed on a page. Reads only. Nothing yet -- see the module docstring."""
    return []
