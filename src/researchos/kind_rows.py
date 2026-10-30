"""What a row on a kind page says besides its state (DESIGN.md 21.5; task V16 B3-2): which attempt its file belongs
to, the title an agent or a person registered it with, and whether it has a thumbnail.

The kind pages (图表与论断, 数据与代码) list by kind. An agent's outputs are seen in its handback (21.2), not in a block
of their own at the top of these pages: they are rows like any other file, with their registered title, and each row
says its attempt as a quiet field. `Filed` reads, once for an answer, what the rows need:

- **the registered outputs** (`outputs.catalog`, the catalogs an agent's declaration and a person's registration go to),
  by path -- a registered file that is not a node of the index is a row too, so that nothing an agent produced drops out
  of the list for want of a link;
- **the attempt a row belongs to**, in this order. The task whose output the file is: the attempt of that task
  (`now_doing.placed_reports`, the one notion of which attempt a report -- and so its outputs -- belongs to, 21.4: the
  task's own attempt, else the first attempt its research context links). Else the attempts the file was filed under by
  the researcher (`trace.attempt_members`, 14.3's one rule: the step files an attempt's description names and the files
  attached with 「归入尝试」), or, for a figure or table, the attempts its trace found (`found`: the same ones its own page
  says it belongs to). A row with none says nothing: lacking an attempt is a warning only for what the paper includes,
  and that is `provenance`'s.

Reads only. A task list, a catalog or an attempt table that cannot be read leaves that part out, never the page.
"""

from __future__ import annotations

import os
from pathlib import Path
from sqlite3 import Connection
from typing import Any, Iterable

from researchos import now_doing, outputs, workspace
from researchos import trace as trace_mod
from researchos.ingest import attempts as attempts_ingest
from researchos.ingest import files as ingest_files
from researchos.records import notes as notes_mod

#: What `/api/workspace/asset` serves, and the size it refuses past: a thumbnail of anything else would be a broken image.
THUMBNAIL_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp")
THUMBNAIL_LIMIT = 8 * 1024 * 1024
_READS = (OSError, ValueError, workspace.WorkspaceError)


def thumbnail(root: Path, rel: str | None) -> bool:
    """Whether the page can show `rel` as a small picture: an image the asset route serves, there and not too large."""
    if not rel or not rel.lower().endswith(THUMBNAIL_SUFFIXES):
        return False
    try:
        return os.stat(root / rel).st_size <= THUMBNAIL_LIMIT
    except OSError:
        return False


class Filed:
    """The registered outputs of `kinds` and the attempts of the files of one answer, each read on first use."""

    def __init__(self, conn: Connection, root: Path, shared: trace_mod.Shared, kinds: Iterable[str]) -> None:
        self.conn, self.root, self.shared = conn, root, shared
        bundle = outputs.catalog(root, set(kinds), preview=False)
        #: The sentence and the catalogs a read that was not whole is about (`outputs.catalog`'s own), else None.
        self.error: str | None = bundle.get("error")
        self.broken: list[str] = list(bundle.get("broken") or [])
        #: The registered files by path as the scan compares paths (`ingest_files.fold`), newest registration first
        #: (`outputs.catalog` keeps one row per path).
        self.registered: dict[str, dict[str, Any]] = {ingest_files.fold(item["path"]): item for item in bundle["items"]}
        self._placed: dict[str, str] | None = None
        self._linked: dict[str, list[str]] | None = None

    def title(self, path: str | None) -> str | None:
        """The title the file was registered with, as written; None when it was not registered or has none."""
        entry = self.registered.get(ingest_files.fold(path or ""))
        return (entry.get("title") or None) if entry else None

    def unlisted(self, known: Iterable[str]) -> list[dict[str, Any]]:
        """The registered files whose path is not in `known` (the rows the index gave), in the catalog's order."""
        have = {ingest_files.fold(path) for path in known}
        return [entry for key, entry in self.registered.items() if key not in have]

    def _reports(self) -> dict[str, str]:
        """`{report path: attempt id}` (`now_doing.placed_reports`), read when the first registered row asks."""
        if self._placed is None:
            try:
                tasks = workspace.read_tasks(self.root)[0]
            except _READS:
                tasks = []
            self._placed = now_doing.placed_reports(self.root, set(self.shared.by_id), tasks)
        return self._placed

    def _filed(self) -> dict[str, list[str]]:
        """`{path: [attempt id]}` of the files the researcher filed under an attempt (`trace.attempt_members`)."""
        if self._linked is None:
            found: dict[str, list[str]] = {}
            try:
                members = trace_mod.attempt_members(self.conn, self.root, self.shared.attempts)
            except (OSError, ValueError):
                members = {}
            for attempt, files in members.items():
                for node in files:
                    path = notes_mod.node_path(node)
                    if path:
                        found.setdefault(path, []).append(attempt)
            self._linked = found
        return self._linked

    def attempts_of(self, path: str | None, found: Iterable[str] | None = None) -> list[str]:
        """The attempts a file belongs to, as ids in the attempt table's order of numbers (module docstring): the
        attempt of the task whose output it is; else `found` when the caller traced the file, else the files filed
        under an attempt. Ids the index does not hold are left out."""
        entry = self.registered.get(ingest_files.fold(path or ""))
        if entry is not None:
            placed = self._reports().get(str(entry.get("report") or ""))
            if placed:
                return [placed]
        ids = list(found) if found is not None else self._filed().get(path or "", [])
        by_id = self.shared.by_id
        known = {i for i in ids if i in by_id}

        def order(attempt: str) -> tuple[Any, ...]:
            return (*attempts_ingest.attempt_sort_key(str(by_id[attempt]["attrs"].get("number") or "")), attempt)

        return sorted(known, key=order)
