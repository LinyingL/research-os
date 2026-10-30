"""Read-only lineage report over `researchos.ingest.dataflow`'s graph (task W4).

`researchos lineage` is a query surface, not an extractor: it writes nothing to the
database and re-parses no source file. Everything it reports comes from
`script --reads/writes--> dataset|figure` edges `researchos ingest` already wrote
(DESIGN.md section 4, migration 0003) -- run `researchos ingest` first, same as
`researchos query`/`researchos trace`/`researchos status`.

Four blocks, each answering a distinct provenance question a paper submission
raises:

1. **Orphan inputs** (`orphans`) -- a `dataset` read by >=1 script but written
   by none. Scoped to `dataset` only (not `figure`): the real-world question
   this answers is "where did this input data come from", and a figure is
   normally an output, never an input a script re-reads.
2. **Lineage chains** (`chains`) -- a `dataset` or `figure` with both a writer
   and a reader recorded: the "produced here, consumed there" chain a
   reviewer asks for. `figure` is included here (unlike orphans/duplicates)
   because a generated plot is exactly as traceable a chain link as a
   generated dataset.
3. **Broken links** (`broken_links`) -- any `reads`/`writes` occurrence whose
   evidence carries `missing: true` (`researchos.ingest.dataflow`'s own flag for "the
   script wants to read/write a file that isn't on disk"). Applies to both
   node types; a script can point at a nonexistent target either way.
4. **Duplicate copies** (`duplicates`) -- a read `dataset` whose basename also
   exists at other paths in the project tree, scoped to `dataset` only for
   the same reason as orphans: "which of the 4 copies of theme_counts.csv did
   the script actually read" is a data-provenance question, not a figure one.
   The walk that finds the copies leaves out the work folder and the files
   registered as `work` (DESIGN.md 19.7), as the scan does: a copy an agent
   kept among its process files is not another copy the script could have read.

Two kinds of edge are treated specially, so this report and the canvas
(DESIGN.md section 8.2's clay orphan dot is "the lineage report's own
definition") can never disagree about the same file:

- an edge a human has `rejected` (`rce reject`, the canvas's 标记为错误提取)
  is not a reader or writer at all -- the human said the extraction is
  wrong, and the 血缘 tab listing it as "Written by" anyway while the canvas
  showed the dataset as an orphan was exactly that disagreement
  (adversarial review of the V4 work);
- a human mapping (`.researchos/mappings.toml`, extractor in
  `db.HUMAN_EXTRACTORS`) names its script as the reader/writer, with no
  line and `human: true` -- its evidence points at the mappings file, and
  reporting `.researchos/mappings.toml` as "the script that wrote this dataset"
  was simply wrong.

**A person's own entry answers a broken link** (DESIGN.md 21.5). A script that names a file which is not
there is broken (block 3), unless the researcher has said, with a `reads` or `writes` / `generates` entry of their
own (`.researchos/mappings.toml`), that the same script reads or writes a file of that name elsewhere: that entry
is what the script reads now, and the occurrence that named the missing file is superseded -- not listed as broken,
and the file it named, when nothing else refers to it, not listed as a target at all (`superseded`,
`replaced_targets`). The machine's edge stays in the index; only what the report calls broken changes. One entry
answers one missing path: a script that names the same file name in two folders, neither there, needs an entry for
each (two files of that name); the entry does not say which path it was for, so the paths come in the order of their
names and the last ones are the ones left broken (`Entries.missing_of`).

Never guesses which occurrence, script, or file is "the" answer -- every
finding lists every matching occurrence/path it actually found, sorted for
stable, diffable output, and an empty block is simply the true, honest
answer "none of this pattern exist" rather than a placeholder.
"""

from __future__ import annotations

import os
import posixpath
import time
from pathlib import Path
from sqlite3 import Connection
from typing import Any

from researchos import db, names
from researchos.ingest import files as ingest_files
from researchos.records import judgements

# Directories never worth walking for a duplicate-basename scan: version
# control internals, this tool's own state, and common tooling caches --
# mirrors researchos.ingest.files._NOISE_DIRS (not imported from there: that list
# exists to build a *categorized source inventory*; this one wants literally
# every real file on disk, so the two lists serve different callers even
# though today's values happen to coincide). Every dot-prefixed directory is
# *also* skipped below regardless of this set, same rationale as
# researchos.ingest.files: a project's own tool caches are not enumerable by a fixed
# list.
_NOISE_DIRS = frozenset(
    {".git", names.PROJECT_DIRNAME, names.LEGACY_PROJECT_DIRNAME, "__pycache__", ".venv", "node_modules",
     ".Rproj.user", "_cache", ".ipynb_checkpoints"}
)


def _occurrences(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """An edge's evidence, unwrapped to its list of occurrence dicts.

    Every `reads`/`writes` edge is written via `db.upsert_edge` (never
    `set_edge_semantic_review`), so in practice this is always the
    `{"occurrences": [...]}` shape -- but the defensive fallback matches
    every other evidence reader in this codebase (see
    `researchos.cli._format_evidence_summary`) for the same pre-T10 legacy-row
    reason, cheap insurance rather than a real expected case here.
    """
    occurrences = evidence.get("occurrences")
    if not isinstance(occurrences, list):
        occurrences = [evidence]
    return occurrences


def _target_path_and_type(conn: Connection, target_id: str) -> tuple[str, str | None]:
    """A target node's repo-relative path (its `title`, set by
    `ingest_dataflow_repo` to exactly this) and its node type ("dataset" or
    "figure"). Falls back to stripping the id's own `<type>:` prefix only if
    the node is somehow missing (defensive -- every `reads`/`writes` edge's
    dst is upserted in the same call that writes the edge, so this should
    not happen in practice)."""
    node = db.get_node(conn, target_id)
    if node is not None:
        return node["title"] or target_id, node["type"]
    _, _, path = target_id.partition(":")
    return path or target_id, None


def _flag_occurrences(occurrences: list[dict[str, Any]], edge: dict[str, Any], flags: Any) -> list[dict[str, Any]]:
    """DESIGN.md 9.6 "Where it shows": every occurrence of a link whose
    judgment is under review or in conflict carries the mark (copies; the
    evidence itself is never touched)."""
    if flags is None:
        return occurrences
    marks = flags.for_key((edge["src"], edge["dst"], edge["type"], edge["extractor"]))
    if not (marks["review"] or marks["conflict"]):
        return occurrences
    keep = {k: marks[k] for k in ("review", "conflict", "judgement")}
    # Which link the mark is about, so the app's 「待复核」 tag can open the
    # review list at that item.
    keep["link"] = {k: edge[k] for k in ("src", "dst", "type", "extractor")}
    return [{**occ, **keep} if isinstance(occ, dict) else occ for occ in occurrences]


def _edge_occurrences(conn: Connection, edge: dict[str, Any], flags: Any = None) -> list[dict[str, Any]]:
    """One edge's occurrences as this report lists them. A human mapping's
    evidence is `{file: .researchos/mappings.toml, source: human}` -- naming the
    mappings file, not a script -- so its single occurrence is rebuilt from
    the edge's own script endpoint (`src`: every `reads`/`writes` edge is
    stored `script --> target`), with no line and `human: True`."""
    if edge["extractor"] in db.HUMAN_EXTRACTORS:
        script_path, _ = _target_path_and_type(conn, edge["src"])
        return [{"file": script_path, "line": None, "callee": None, "human": True}]
    return _flag_occurrences(_occurrences(edge["evidence"]), edge, flags)


def _live_edges(conn: Connection, edge_type: str) -> list[dict[str, Any]]:
    """Every `edge_type` edge a human has not rejected (module docstring)."""
    return [e for e in db.query_edges(conn, type=edge_type) if e["status"] != "rejected"]


#: The two things a script does to a file, for matching a person's entry to what the script names: a `generates`
#: entry (script to figure) is a write like `writes`.
FAMILY = {"reads": "reads", "writes": "writes", "generates": "writes"}


class Entries(dict):
    """`{(script path, family): {file paths}}` (`person_entries`), which can also say which files a script names that
    are not there (`missing_of`), read from the index when first asked and only for a script that has an entry."""

    def __init__(self, conn: Connection) -> None:
        super().__init__()
        self.conn = conn
        self._missing: dict[tuple[str, str], list[str]] = {}

    def missing_of(self, script: str, family: str) -> list[str]:
        """The files `script` reads (or writes, by `family`) that a machine occurrence says are not on disk, sorted."""
        key = (script, family)
        if key not in self._missing:
            found: set[str] = set()
            for edge in _live_edges_of(self.conn, f"script:{script}"):
                if edge["extractor"] in db.HUMAN_EXTRACTORS or edge["type"] not in ("reads", "writes") or FAMILY[edge["type"]] != family:
                    continue
                if any(occ.get("missing") and occ.get("file") == script for occ in _edge_occurrences(self.conn, edge)):
                    found.add(_target_path_and_type(self.conn, edge["dst"])[0])
            self._missing[key] = sorted(found)
        return self._missing[key]


def _live_edges_of(conn: Connection, script_id: str) -> list[dict[str, Any]]:
    return [e for e in db.query_edges(conn, src=script_id) if e["status"] != "rejected"]


def person_entries(conn: Connection) -> Entries:
    """The files the researcher's own entries say a script reads or writes (`FAMILY`): `{(script path, family): {file
    paths}}`, from the live edges of a human extractor. Reads the index only."""
    out = Entries(conn)
    for edge_type, family in FAMILY.items():
        for edge in _live_edges(conn, edge_type):
            if edge["extractor"] not in db.HUMAN_EXTRACTORS:
                continue
            script_path, _ = _target_path_and_type(conn, edge["src"])
            target_path, _ = _target_path_and_type(conn, edge["dst"])
            out.setdefault((script_path, family), set()).add(target_path)
    return out


def superseded(entries: dict[tuple[str, str], set[str]], script: str | None, kind: str, target: str) -> bool:
    """Whether the researcher's own entry answers a script's naming of the missing file `target`: they said `script`
    reads (`kind` "reads") or writes (`kind` "writes") a different file of the same name. An entry for the very path
    the script names does not answer it -- that file is still not there. One entry answers one path: when the script
    names that file name in several folders, none of them there (`Entries.missing_of`), as many of them are answered as
    there are entries of the name, the first in the order of the paths."""
    script, family, name = script or "", FAMILY.get(kind, kind), posixpath.basename(target)
    answers = [p for p in entries.get((script, family), ()) if p != target and posixpath.basename(p) == name]
    if not answers:
        return False
    missing_of = getattr(entries, "missing_of", None)
    if missing_of is None:
        return True
    rivals = sorted({p for p in missing_of(script, family) if posixpath.basename(p) == name} | {target})
    return rivals.index(target) < len(answers)


def broken_in(conn: Connection, edges: list[dict[str, Any]],
              entries: dict[tuple[str, str], set[str]] | None = None) -> list[dict[str, Any]]:
    """The broken links among `edges` (live `reads` / `writes` edges): every machine occurrence whose evidence says
    the file it names is not on disk, except those a person's own entry supersedes. Sorted by script and line. The
    person's entries are read only when some occurrence is missing."""
    missing: list[tuple[str, str, dict[str, Any]]] = []
    for edge in edges:
        if edge["type"] not in ("reads", "writes"):
            continue
        for occ in _edge_occurrences(conn, edge):
            if occ.get("missing"):
                missing.append((edge["type"], _target_path_and_type(conn, edge["dst"])[0], occ))
    if not missing:
        return []
    entries = person_entries(conn) if entries is None else entries
    found = [{"script": occ.get("file"), "line": occ.get("line"), "callee": occ.get("callee"), "kind": kind, "target": target}
             for kind, target, occ in missing if not superseded(entries, occ.get("file"), kind, target)]
    found.sort(key=lambda b: (b["script"] or "", b["line"] or 0))
    return found


def answered(conn: Connection, entries: dict[tuple[str, str], set[str]], edge: dict[str, Any]) -> bool:
    """Whether a script's link `edge` (a live `reads` / `writes`) is a machine link every occurrence of which names
    a file that is not there and that a person's own entry answers (`superseded`)."""
    if not entries or edge["extractor"] in db.HUMAN_EXTRACTORS or edge["type"] not in ("reads", "writes"):
        return False
    target_path, _ = _target_path_and_type(conn, edge["dst"])
    found = _edge_occurrences(conn, edge)
    return bool(found) and all(occ.get("missing") and superseded(entries, occ.get("file"), edge["type"], target_path) for occ in found)


def replaced_targets(conn: Connection, entries: dict[tuple[str, str], set[str]] | None = None) -> set[str]:
    """The ids of the files that exist only because a script named them and that a person's own entry has since
    answered (module docstring): every link that refers to the file is an answered one (`answered`)."""
    entries = person_entries(conn) if entries is None else entries
    if not entries:
        return set()
    gone: dict[str, bool] = {}
    for edge_type in ("reads", "writes"):
        for edge in _live_edges(conn, edge_type):
            gone[edge["dst"]] = gone.get(edge["dst"], True) and answered(conn, entries, edge)
    return {target for target, ok in gone.items() if ok}


#: The walks of pages that ask for a `stamp`, by project: `(stamp, when, files by base name)`.
_WALKS: dict[str, tuple[Any, float, dict[str, list[str]]]] = {}
#: How long a walk is kept for the same `stamp` (seconds): a node page with a broken link opens often, the project is big.
WALK_KEPT = 20.0


class Names:
    """The project's files by base name (`_scan_basenames`), walked once and only when something asks. With a `stamp`
    (the index's last scan, which says the project was read as it was then) a walk is kept for `WALK_KEPT` seconds for
    the next page that asks with the same stamp: a node page offers candidates for a broken link, and walking thousands
    of files for each opening is what makes it slow."""

    def __init__(self, project_root: str | Path, stamp: Any = None) -> None:
        self.root = Path(project_root)
        self.stamp = stamp
        self._found: dict[str, list[str]] | None = None

    def get(self, name: str) -> list[str]:
        if self._found is None:
            key = str(self.root)
            kept = _WALKS.get(key) if self.stamp is not None else None
            if kept is not None and kept[0] == self.stamp and time.monotonic() - kept[1] < WALK_KEPT:
                self._found = kept[2]
            else:
                self._found = _scan_basenames(self.root)
                if self.stamp is not None:
                    _WALKS[key] = (self.stamp, time.monotonic(), self._found)
        return self._found.get(name, [])


def _collect_by_target(conn: Connection, edges: list[dict[str, Any]], flags: Any = None) -> dict[str, list[dict[str, Any]]]:
    """Every `(file, line, callee, ...)` occurrence across a list of edges,
    grouped by the edge's `dst` (the target node id) -- one edge per distinct
    script, so a target read/written by several scripts spans several edges
    here, each possibly itself carrying several occurrences (several call
    sites in that one script)."""
    by_target: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        by_target.setdefault(edge["dst"], []).extend(_edge_occurrences(conn, edge, flags))
    return by_target


def _sorted_occurrences(occurrences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(occurrences, key=lambda occ: (occ.get("file") or "", occ.get("line") or 0))


def _reader_entry(occ: dict[str, Any]) -> dict[str, Any]:
    entry = {"script": occ.get("file"), "line": occ.get("line"), "callee": occ.get("callee")}
    if occ.get("human"):
        entry["human"] = True  # a human mapping: no line, the researcher's own assertion
    for mark in ("review", "conflict", "judgement", "link"):
        if occ.get(mark):
            entry[mark] = occ[mark]
    return entry


def is_orphan_input(node_type: str | None, has_readers: bool, has_writers: bool) -> bool:
    """THE orphan-input definition (block 1 below): a `dataset` that some
    script reads and no script writes. Public because the canvas (DESIGN.md
    section 8.2, "the lineage report's own definition") flags the same
    files with a clay dot and must never drift from this report; scoped to
    `dataset` for the reason given in the module docstring."""
    return node_type == "dataset" and has_readers and not has_writers


def _scan_basenames(project_root: Path) -> dict[str, list[str]]:
    """Every real (non-symlink) file under `project_root`, grouped by
    basename -> sorted repo-relative paths -- the input to duplicate-copy
    detection (block 4). Skips the same noise/hidden directories
    `researchos.ingest.files.list_source_files` skips (see `_NOISE_DIRS` above), and
    never follows a symlink, matching that module's own guard against
    reporting a path that doesn't lead to a distinct real file. It leaves out
    what the scan leaves out of a Research OS project (`ingest.files.scan_skips`,
    DESIGN.md 19.7): the work folder, and the files registered as `work` -- a
    copy of a dataset an agent kept among its process files is not "another copy
    of the dataset" the script could have read.
    """
    by_basename: dict[str, list[str]] = {}
    skipped = ingest_files.scan_skips(project_root)
    for dirpath, dirnames, filenames in os.walk(project_root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in _NOISE_DIRS and not d.startswith("."))
        for filename in sorted(filenames):
            if filename.startswith("."):
                continue
            full_path = Path(dirpath) / filename
            if full_path.is_symlink():
                continue
            rel = full_path.relative_to(project_root).as_posix()
            if not skipped(rel):
                by_basename.setdefault(filename, []).append(rel)
    return by_basename


def build_lineage_report(conn: Connection, project_root: str | Path, *, names: Names | None = None,
                         replaced: set[str] | None = None, entries: dict[tuple[str, str], set[str]] | None = None) -> dict[str, Any]:
    """The full four-block lineage report (task W4), as a plain JSON-ready
    dict:

        {
          "scanned": {"scripts": int, "reads_edges": int, "writes_edges": int,
                      "targets": int},
          "orphans": [{"target": id, "path": str, "readers": [
                          {"script": str, "line": int, "callee": str}, ...]}],
          "chains": [{"target": id, "path": str,
                      "writers": [...], "readers": [...]}],
          "broken_links": [{"script": str, "line": int, "callee": str,
                             "kind": "reads"|"writes", "target": str}],
          "duplicates": [{"target": id, "path": str,
                           "other_copies": [str, ...]}],
        }

    Every list is sorted by path (then script/line within a target) so two
    runs over an unchanged graph produce byte-identical output. `scanned`
    always reflects what this run actually looked at, even when every block
    below it is empty -- the caller (`researchos.cli`) uses it to explain a truly
    empty result rather than printing a bare success line (DESIGN.md
    section 0: a missing finding is a normal outcome, but it must still be
    stated, not silently indistinguishable from "nothing was checked").
    """
    project_root = Path(project_root)
    reads_edges = _live_edges(conn, "reads")
    writes_edges = _live_edges(conn, "writes")
    flags = judgements.link_flags(conn)
    readers_by_target = _collect_by_target(conn, reads_edges, flags)
    writers_by_target = _collect_by_target(conn, writes_edges, flags)
    entries = person_entries(conn) if entries is None else entries  # a caller that has read them says so
    # What a person's own entry has answered (module docstring) is no target of this report: the script reads
    # the file they named now.
    gone = replaced_targets(conn, entries) if replaced is None else replaced
    all_targets = sorted((set(readers_by_target) | set(writers_by_target)) - gone)

    orphans: list[dict[str, Any]] = []
    chains: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    names = Names(project_root) if names is None else names  # walks the project only if a candidate exists

    for target_id in all_targets:
        readers = _sorted_occurrences(readers_by_target.get(target_id, []))
        writers = _sorted_occurrences(writers_by_target.get(target_id, []))
        path, node_type = _target_path_and_type(conn, target_id)

        if readers and writers:
            chains.append({
                "target": target_id, "path": path,
                "writers": [_reader_entry(o) for o in writers],
                "readers": [_reader_entry(o) for o in readers],
            })
        elif is_orphan_input(node_type, bool(readers), bool(writers)):
            # Orphan inputs are scoped to `dataset` only -- a `figure` read
            # back by a script but never written by one is not the
            # "unexplained input data" finding this block exists for; see
            # module docstring.
            orphans.append({
                "target": target_id, "path": path,
                "readers": [_reader_entry(o) for o in readers],
            })

        if readers and node_type == "dataset":
            # Duplicate-copy detection (block 4), scoped to `dataset` like
            # orphans -- also only for a target that is actually *read*
            # somewhere, matching the real question ("which copy did the
            # script that reads it actually see").
            others = sorted(p for p in names.get(posixpath.basename(path)) if p != path)
            if others:
                duplicates.append({"target": target_id, "path": path, "other_copies": others})

    broken_links = broken_in(conn, [*reads_edges, *writes_edges], entries)

    return {
        "scanned": {
            "scripts": len(db.get_nodes_by_type(conn, "script")),
            "reads_edges": len(reads_edges),
            "writes_edges": len(writes_edges),
            "targets": len(all_targets),
        },
        "orphans": orphans,
        "chains": chains,
        "broken_links": broken_links,
        "duplicates": duplicates,
    }
