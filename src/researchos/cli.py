"""Research OS command-line interface (T4): `researchos init` / `ingest` / `status` / `query` /
`trace` / `confirm` (F3) / `judge` (S2, optional semantic layer) / `lineage`
(W4, read-only four-block report over task W2's dataflow graph) / `serve`
(V1, local read-only web view over the graph -- see researchos.webapp.server).

stdlib argparse only (DESIGN.md section 0, Occam rule 1). Orchestrates
the existing extractors (researchos.ingest.git/latex/dataflow/pyfig/mlflow/wandb/
mdpaper/claims) and researchos.db (section 7 Phase A order: git -> latex/.bib ->
dataflow (task W2, data lineage) -> pyfig -> mlflow -> wandb -> mdpaper
(task W3, Markdown paper support) -> claims); writes only via
db.upsert_node/upsert_edge, no new graph mutation logic here. mdpaper runs
after mlflow/wandb (same experiment-nodes-must-exist-first requirement the
tex claims step has) and before the tex claims step, since both need
section/experiment state already in place before generating their own
backed_by candidates.

W1: `cmd_ingest` catches `git_ingest.NotAGitRepositoryError` specifically
(a project root that is not a git repository at all -- the common case for
a researcher's working directory that was never `git init`ed) and degrades
rather than aborting: it prints one explanatory line, skips commit/
contributor ingestion (nothing to read), and gets the file inventory from
`researchos.ingest.files.list_source_files` (a plain filesystem walk) instead of
`git_ingest.list_source_files`. Every other extractor still runs. A git
repository's behavior is unchanged -- this only branches on the specific
"not a repository" failure, not `GitIngestError` in general, which still
aborts the whole ingest as before (missing git binary, permission error,
a corrupt repo, ...).
`trace` reuses researchos.query.trace() directly -- multi-hop traversal logic lives
in exactly one place. `lineage` (W4) reuses researchos.lineage.build_lineage_report()
the same way -- it is a pure read over edges `researchos ingest` already wrote (task
W2's dataflow extractor), never a re-parse of source files and never a write
path; see researchos.lineage's own module docstring for the four blocks it reports.

A project is "initialized" once its graph exists (`researchos init`); every other
command requires that file and errors clearly if absent (no guessing, per
the constitution). Since DESIGN.md section 8.10 rule 1 the graph is NOT in
the project -- it lives at `~/.researchos/graphs/<id>/graph.db`, out of reach of
iCloud/Dropbox file providers, and `researchos.paths` is the one module that says
where. `_require_db` below resolves through `researchos.paths.resolve_graph_db`,
which also performs the one-time migration of a legacy in-project
`.researchos/graph.db` on first touch by any subcommand.

Positioning ruling 2026-07-22 (Owner): Research OS is a local-first standalone tool;
MCP is one optional exit among several, not a requirement. Concretely: (1)
`researchos.mcp_server` is imported lazily, only inside the `mcp` subcommand branch
of main() below, so every other subcommand works with the optional 'mcp'
extra uninstalled (see pyproject.toml); (2) `trace` exists here so multi-hop
provenance is a full CLI feature, not something only reachable through an AI
client's MCP tool calls.

V5 phase 5 (DESIGN.md 9.5, 9.8): `researchos rebuild` (`researchos.rebuild`: a fresh
index beside the current one, compared per link before the swap), `researchos
records [--verify|--clean [--yes]]` (`researchos.inventory`) and `researchos migrate
[--list|--yes|--not-mine|--from DIR]` (`researchos.migration`: pre-V5 judgments
moved into the record, an explicit act). `researchos status` reports pre-V5
indexes waiting for the folder.

V5 phase 6 (DESIGN.md 9.12): every subcommand that addresses a project
takes it as a positional path or as `--path` (`add_project_path`; both at
once is refused); `researchos project adopt|restore` answer a lost identity file;
`researchos records --answer ... --missing IDS` binds the shrink answer to the
entries the question showed.

F3 (Blocker C): `status --pending`/`confirm` give the zero-dependency
baseline its own human-confirmation path -- previously the sole writer of
`edges.status` was the optional `mcp` extra's `researchos_confirm_edge`,
contradicting DESIGN.md section 2 now that ingest writes real `pending`
edges. stdlib argparse only.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from contextlib import contextmanager, nullcontext
from pathlib import Path
from sqlite3 import Connection
from typing import Any

from researchos import addproject, consistency, db, github, githubpush, inventory, lineage, literature, migration, names, paths, query, rename
from researchos import collaborate as collaborate_mod
from researchos import history as history_mod
from researchos import exchange as exchange_mod
from researchos import people
from researchos import proposals as proposals_mod
from researchos import share as share_mod
from researchos import signing
from researchos import trace as trace_mod
from researchos import project as project_identity
from researchos import rebuild as rebuild_mod
from researchos.ingest import attempts as attempts_ingest
from researchos.ingest import citations as citations_ingest
from researchos.ingest import files as files_ingest
# `git_ingest` stays importable as `researchos.cli.git_ingest` (tests patch it);
# the scan itself is `researchos.ingest.pipeline`.
from researchos.ingest import git as git_ingest  # noqa: F401
from researchos.ingest import mappings as mappings_ingest
from researchos.ingest import pipeline as ingest_pipeline
from researchos.records import cards as variable_cards
from researchos.records import decisions as decisions_mod
from researchos.records import implementation as card_implementation
from researchos.records import files as record_files
from researchos.records import judgements
from researchos.records import ledger as ledger_mod
from researchos.records import lock as records_lock
from researchos.records import notes as notes_mod
from researchos.records import situation as records_situation
from researchos.records import variables as variables_mod
# S2: `researchos judge`, the optional semantic layer. Unlike researchos.mcp_server
# (behind a lazy import because it needs the third-party 'mcp' extra),
# researchos.semantic.{backend,judge} use only stdlib urllib -- importing them
# here eagerly costs zero-dependency installs nothing, so `judge` gets a
# normal top-level subparser like every other subcommand, not the `mcp`
# subcommand's pass-through special case.
from researchos.semantic import backend as semantic_backend
from researchos.semantic import judge as semantic_judge
# V1: `researchos serve`, the local read-only web view (DESIGN.md section 7,
# "Later"). stdlib http.server only (see researchos.webapp.server's module
# docstring) -- like researchos.semantic above, and unlike researchos.mcp_server, this
# needs no optional extra, so it gets a normal top-level subparser.
# V3 phase 1: researchos.webapp.registry is the machine-managed project registry
# (~/.researchos/projects.json) that lets `researchos serve` resume the most recently
# served project when no path is given.
# V3 phase 4: researchos.webapp.macapp generates the double-clickable Research OS.app
# launcher bundle (`researchos app`). Generation is pure stdlib file writing,
# platform-independent -- only cmd_app's *default* install location is
# macOS-gated -- so this too is a plain eager import.
from researchos.webapp import canvas as canvas_mod
from researchos.webapp import macapp
from researchos.webapp import registry as project_registry
from researchos.webapp import server as webapp_server

# Kept as module attributes (error messages and tests quote them), but the
# definitions live in researchos.paths now -- no module computes the graph's
# location itself anymore (DESIGN.md section 8.10 rule 1).
PROJECT_DIRNAME = paths.PROJECT_DIRNAME
DB_FILENAME = paths.DB_FILENAME


class CliError(Exception):
    """User-facing error; caught once in main() -> "Error: <msg>" on stderr, exit 1."""


# -- one path convention (DESIGN.md 9.12, "The command line") --------------------
#
# Every subcommand that addresses a project takes it either as a positional
# path or as `--path` -- both spellings everywhere, and the help says so.
# Giving both is refused (it was already, for `attempts`); nothing that
# worked before stops working.

POSITIONAL_PATH_HELP = "project root (default: {default}); or give it as --path"
FLAG_PATH_HELP = "project root, the same as the positional path (give one or the other, not both)"


def add_project_path(parser: argparse.ArgumentParser, *, default: str | None = ".", help: str | None = None) -> None:
    """Add the positional `path` and `--path` to a subcommand. `default`
    is used when neither is given (None: the subcommand decides)."""
    shown = f"'{default}'" if default is not None else "see below"
    parser.add_argument("path", nargs="?", default=None, help=help or POSITIONAL_PATH_HELP.format(default=shown))
    parser.add_argument("--path", dest="path_flag", default=None, metavar="PATH", help=FLAG_PATH_HELP)
    parser.set_defaults(path_default=default)


class PathParser(argparse.ArgumentParser):
    """The parser of every `researchos` command, so that the trailing project path
    is read the same way on every supported Python (9.12: "Research OS runs on
    Python 3.11 ... as it says it does").

    An optional positional (`path`, `confirm`'s link args) followed by an
    option and then a token: older argparse (3.11, early 3.12) matches the
    optional positionals at the first positional run, with nothing if the
    run is short (`variable abandon ID --note X PATH` gives `path` nothing
    while reading ID), so PATH is left over as "unrecognized"; later
    versions keep them open and PATH fills `path`. Here the leftover is
    placed the later way on every version: when everything left over is
    plain tokens (no option among them), and there are no more of them
    than optional positionals still unfilled (None, their default; no
    `type`/`choices`), they fill those, in declaration order. A leftover
    that starts with the `--` separator (older argparse leaves it there,
    later versions consume it) is read as later argparse reads it: the
    separator is dropped and every token after it is plain, even one that
    starts with "-". Anything else is refused exactly as before. Nothing is
    accepted that the later argparse refuses, and nothing it accepts is
    refused."""

    def parse_known_args(self, args=None, namespace=None):  # type: ignore[override]
        namespace, extras = super().parse_known_args(args, namespace)
        tokens = extras[1:] if extras and extras[0] == "--" else extras
        if not tokens or (tokens is extras and any(token.startswith("-") for token in tokens)):
            return namespace, extras
        open_slots = [
            action.dest for action in self._actions
            if not action.option_strings and action.nargs == argparse.OPTIONAL
            and action.default is None and action.type is None and action.choices is None
            and getattr(namespace, action.dest, None) is None
        ]
        if len(tokens) > len(open_slots):
            return namespace, extras
        for dest, token in zip(open_slots, tokens):
            setattr(namespace, dest, token)
        return namespace, []


def settle_project_path(positional: str | None, flag: str | None, default: str | None) -> str | None:
    """The one project path a command line gave (`add_project_path`)."""
    if positional is not None and flag is not None:
        raise CliError("give the project root either positionally or via --path, not both")
    if flag is not None:
        return flag
    return positional if positional is not None else default


def _settle_path(args: argparse.Namespace) -> None:
    """After parsing: `args.path` is the project path whichever way it was
    given; `args.path_flag` is consumed. `researchos confirm --index N <path>`:
    with --index there is no src/dst/type/extractor, so the one positional
    given is the project path."""
    if not hasattr(args, "path_flag"):
        return
    positional = args.path
    if getattr(args, "func", None) is cmd_confirm and args.index is not None and positional is None:
        given = [v for v in (args.src, args.dst, args.type, args.extractor) if v is not None]
        if len(given) == 1 and args.src is not None:
            positional, args.src = args.src, None
    if (getattr(args, "func", None) is cmd_share and getattr(args, "share_command", None) == "set" and positional is None
            and args.directory is not None and (args.published or args.mine or args.area)):
        # 'share set --published P --mine M PROJECT' / 'share set --area M=D PROJECT': the one positional is the project.
        positional, args.directory = args.directory, None
    args.path = settle_project_path(positional, args.path_flag, args.path_default)
    args.path_flag = None


def _open(path_str: str, *, register: bool = False) -> project_identity.Opened:
    """THE identity check (DESIGN.md 9.4), first thing in every subcommand
    that names a project: before anything is written. A moved project is
    adopted, a missing index is built empty; a copy, a home that cannot be
    checked, a lost or unreadable identity stops here with the reason and
    the exact commands, and nothing is written (not even the registry)."""
    root = Path(path_str).resolve()
    if not root.is_dir():
        raise CliError(f"{root} is not a directory")
    try:
        return project_identity.open_project(root, register=register)
    except project_identity.ProjectBlocked as exc:
        raise CliError(str(exc)) from exc
    except project_identity.AnswerRefused as exc:
        raise CliError(str(exc)) from exc


def _resolve_project_root(path_str: str) -> Path:
    return _open(path_str).root


def _refuse_frozen(opened: project_identity.Opened, command: str) -> None:
    """DESIGN.md 9.12 (acceptance, 2026-10-05): a pre-V5 project -- or one
    whose migration has not finished -- is frozen until it is migrated,
    scans included. Its old index is at an older schema, and nothing new
    may land in it before the migration reads its own count. Reading
    works; `researchos ingest`, `researchos attempts` and `researchos mappings` refuse."""
    if not opened.needs_migration:
        return
    why = (
        "its migration has not finished"
        if opened.situation is not records_situation.Situation.LEGACY
        else "it was indexed before V5 and its old index may hold judgments"
    )
    raise CliError(
        f"'{command}' refused: {opened.root} is frozen until it is migrated ({why}); nothing new may land in "
        f"the old index before the migration counts it -- migrate first: researchos migrate {opened.root}  "
        f"(reading -- researchos status, researchos query, researchos records -- still works). Nothing written."
    )


@contextmanager
def _write_guard(opened: project_identity.Opened, *, human: bool):
    """Hold the project lock and re-check identity for a write (9.4, 9.7):
    an index write (a scan) or, with `human=True`, a human record -- which
    a pre-V5 project refuses until it is migrated."""
    try:
        with records_situation.write_guard(opened.root, opened.project_id, human=human):
            yield
    except records_situation.WriteRefused as exc:
        raise CliError(str(exc)) from exc
    except records_lock.ProjectLockError as exc:
        raise CliError(f"could not take the project lock: {exc}") from exc


def _require_db(project_root: Path) -> Path:
    """The graph for `project_root`, or a clear CliError. Resolved through
    `researchos.paths.resolve_graph_db` -- which is `graph_db_path` plus the
    one-time migration of a legacy in-project `.researchos/graph.db` -- so this
    is one of the "first touch by any subcommand" sites section 8.10
    rule 1 names. A migration that could not be verified is fatal for the
    run: the legacy graph is still there and untouched, and continuing
    would mean silently building a second, empty one."""
    try:
        path = paths.resolve_graph_db(project_root)
    except paths.GraphMigrationError as exc:
        raise CliError(str(exc)) from exc
    if not path.exists():
        raise CliError(
            f"no Research OS project at {project_root} (missing its graph at {path}); "
            f"run 'researchos init {project_root}' first"
        )
    return path


def _format_counts(counts: dict[str, int]) -> str:
    return " ".join(f"{k}={v}" for k, v in counts.items())


def _print_graph_counts(conn: Connection) -> None:
    """Whole-graph counts by type, shared by `status` and `ingest`'s closing
    summary -- via db.get_nodes_by_type/query_edges/pending_edges only, no
    raw SQL here (db.py's module contract)."""
    node_counts = {t: len(db.get_nodes_by_type(conn, t)) for t in sorted(db.NODE_TYPES)}
    edge_counts = {t: 0 for t in sorted(db.EDGE_TYPES)}
    for edge in db.query_edges(conn):
        edge_counts[edge["type"]] += 1
    print(f"  Nodes: {_format_counts(node_counts)}")
    print(f"  Edges: {_format_counts(edge_counts)}")
    print(f"  Pending confirmation queue: {len(db.pending_edges(conn))}")
    waiting = judgements.review_count(conn)
    if waiting:
        # 9.6: old judgments waiting for the researcher, never in the queue above.
        print(f"  Judgments under review: {waiting} (see 'researchos review')")


def _ordered_edges(edges: list[dict]) -> list[dict]:
    """Deterministic order shared by `status --pending` and `confirm
    --index`, so the Nth edge one prints is the Nth edge the other resolves."""
    return sorted(edges, key=lambda e: (e["src"], e["dst"], e["type"], e["extractor"]))


def _format_semantic_review_suffix(evidence: dict[str, Any]) -> str:
    """Second line for a pending edge that already carries a `semantic_review`
    annotation (written by `researchos judge`, S2) -- lets a human skim the queue
    and see which candidates a model already flagged as likely coincidental,
    without opening `researchos query` on each one. `[FLAGGED]` on `related=False`
    is the "see this one first" signal the task asked for; it is purely
    display -- the edge's `status` is untouched by judge and stays whatever
    it already was (pending, per the constitution).

    Includes `metric=` (bug fix), omitted entirely for an older edge whose
    `semantic_review` predates this attribution field rather than printing
    a misleading `metric=None`: a `backed_by` edge can carry several
    (experiment, metric) candidate pairs (see `candidate_count`), but
    `researchos.semantic.judge` only ever reviews one occurrence per edge and
    records which one in `semantic_review["metric"]`/`["metric_value"]`
    (see researchos.semantic.judge.review_pending_backed_by). Without printing it
    here, a human reading this line had no way to tell which of possibly
    several matched metrics the model's `related`/`reason` verdict was
    actually about.
    """
    review = evidence.get("semantic_review") if isinstance(evidence, dict) else None
    if not isinstance(review, dict):
        return ""
    flag = "[FLAGGED: model says likely unrelated] " if review.get("related") is False else ""
    metric_note = f"metric={review['metric']!r} " if "metric" in review else ""
    better = review.get("better_match")
    better_note = f" better_match={better!r}" if better else ""
    return (
        f"\n      semantic_review: {flag}{metric_note}"
        f"related={review.get('related')!r} reason={review.get('reason')!r}"
        f"{better_note} (model={review.get('model')!r})"
    )


def _display_line(location: dict[str, Any] | None) -> int | None:
    """Pull the plain `line` int out of `query.claim_source_location`'s
    `{"file", "line"}` result, for `_format_occurrence`'s `display_line`
    parameter (which only ever wants the int, not the file)."""
    return location["line"] if location else None


def _format_pending_line(index: int, edge: dict, conn: Connection) -> str:
    location = query.claim_source_location(conn, edge["src"]) if edge["type"] == "backed_by" else None
    return (
        f"  [{index}] {edge['src']} --{edge['type']}--> {edge['dst']} "
        f"extractor={edge['extractor']} confidence={edge['confidence']:.2f} "
        f"evidence={_format_evidence_summary(edge['evidence'], display_line=_display_line(location))}"
        f"{_format_semantic_review_suffix(edge['evidence'])}"
    )


def _print_pending_queue(conn: Connection, limit: int | None) -> None:
    """Every pending edge, detailed enough to act on via `confirm`. `limit`
    (no invented default -- unset prints all) truncates display only, and
    only ever with an explicit notice, never silently."""
    queue = _ordered_edges(db.pending_edges(conn))
    print(f"Pending confirmation queue ({len(queue)}):")
    if not queue:
        print("  (empty)")
        return
    shown = queue if limit is None else queue[:limit]
    for i, edge in enumerate(shown, start=1):
        print(_format_pending_line(i, edge, conn))
    if limit is not None and len(queue) > limit:
        print(
            f"  ... truncated: showing {limit} of {len(queue)} pending edge(s) -- pass a larger "
            f"--limit to see more. Indices are only valid for this run's own listing."
        )


def cmd_init(args: argparse.Namespace) -> int:
    """Create the project's identity, `.researchos/project.toml` (DESIGN.md 9.4,
    created exclusively, never overwritten), and its index under the id
    at `~/.researchos/graphs/<id>/` -- outside the project (8.10 rule 1) -- plus
    the one-line `.researchos/README` signpost, and print where the index is.

    Idempotent on a project that already has an id. A folder in a
    situation that must be answered first (a copy, ...) is refused with
    the commands that answer it; a pre-V5 project is refused too: its
    judgments sit in the old index, and giving it an id is what `researchos
    migrate` does once they have been moved into the record (9.5)."""
    project_root = Path(args.path).resolve()
    if not project_root.is_dir():
        raise CliError(f"{project_root} is not a directory")
    try:
        result = project_identity.init_project(project_root)
    except project_identity.ProjectBlocked as exc:
        raise CliError(str(exc)) from exc
    except (project_identity.AnswerRefused, records_situation.WriteRefused) as exc:
        raise CliError(str(exc)) from exc
    node = project_identity.project_node_id(result.identity.id)
    print(f"Initialized Research OS project at {project_root} (project node: {node})")
    if result.created_identity:
        print(f"Project id: {result.identity.id} (written to {PROJECT_DIRNAME}/project.toml)")
    print(f"Graph: {result.db_path}")
    if result.applied:
        print(f"Applied migrations: {result.applied}")
    # Still a nudge only -- Research OS never edits the user's own files (DESIGN.md
    # section 2, "零习惯改变"): `.researchos/` holds only the project's identity
    # and files the researcher owns, so whether it goes into git is theirs.
    print(
        f"Note: '{PROJECT_DIRNAME}/' in your project now holds only your own files "
        f"(project.toml, judgements.toml, canvas.json, attempts.toml, mappings.toml, backups/) -- "
        f"commit or .gitignore it as you "
        f"prefer. The derived graph is outside the project; see {result.readme}."
    )
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    """Scan every source into the index (`researchos.ingest.pipeline`), under the
    project lock with the identity re-checked (9.7, 9.4): a scan is an
    index write like any other."""
    opened = _open(args.path)
    _refuse_frozen(opened, "researchos ingest")
    project_root = opened.root
    db_path = _require_db(project_root)
    with _write_guard(opened, human=False):
        conn = db.connect(db_path)
        try:
            print(f"Ingesting {project_root}")
            try:
                skipped = ingest_pipeline.ingest_sources(
                    conn, project_root, mlruns=args.mlruns, wandb=args.wandb, echo=print,
                )
            except ingest_pipeline.IngestFailed as exc:
                raise CliError(str(exc)) from exc
            print("Ingest summary (whole graph):")
            _print_graph_counts(conn)
            print(f"  Skipped/unresolved during this run (see logs): {skipped}")
        finally:
            conn.close()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    project_root = _resolve_project_root(args.path)
    db_path = _require_db(project_root)
    conn = db.connect(db_path)
    try:
        print(f"Project: {project_root}")
        # Section 8.10 rule 1: "`researchos status` and `/api/summary` report the
        # graph's actual location so nothing is hidden" -- the graph is no
        # longer where a user would think to look for it.
        print(f"Graph: {db_path}")
        _print_graph_counts(conn)
        _print_migration_waiting(project_root)
        if args.pending:  # purely additive -- omitting it reproduces the prior output exactly
            _print_pending_queue(conn, args.limit)
    finally:
        conn.close()
    _print_overview(project_root)
    return 0


def _print_overview(project_root: Path) -> None:
    """V8 phase 5 (DESIGN.md 12.1-12.6): who I am, my role, the shared
    folder and how far this copy is behind, the proposals, the notes and
    the history -- after the counts, read only, and never a reason for
    `status` to fail."""
    try:
        lines = collaborate_mod.overview_lines(collaborate_mod.overview(project_root))
    except Exception as exc:  # noqa: BLE001 -- status reads; one unreadable part is said, not raised
        lines = [f"People and the shared folder: cannot be read now ({exc})"]
    for line in lines:
        print(line)


def _print_migration_waiting(project_root: Path) -> None:
    """9.5 "What is looked for": checked on every open, with or without an
    id -- pre-V5 indexes that may hold this folder's judgments."""
    waiting = migration.waiting_payload(project_root)
    if waiting["migrating_from"]:
        print(f"  Migration from {waiting['migrating_from']} not finished: human records are read-only until 'researchos migrate' resumes it")
    elif waiting["waiting"]:
        where = ", ".join(w["db_path"] for w in waiting["waiting"])
        print(f"  Legacy records waiting: a pre-V5 index may hold this project's judgments ({where}); see 'researchos migrate'")


def _print_attempts_listing(conn: Connection, config: attempts_ingest.AttemptsConfig) -> None:
    """`researchos attempts` with no --check: what's registered, not a judgement --
    editor/date/verdict/link count, in the attempt's own `#` order
    (`researchos.consistency.attempts_for_file` already sorts by
    `researchos.ingest.attempts.attempt_sort_key`, the same order `--check`'s
    findings now use, so both walk a project's attempts identically)."""
    nodes = consistency.attempts_for_file(conn, config.file)
    print(f"Registered attempts in {config.file} ({len(nodes)}):")
    for node in nodes:
        number = node["attrs"].get("number", "?")
        raw_date = node["attrs"].get("date", "")
        verdict = node["human_fields"].get("verdict", "")
        step_files = len(node["attrs"].get("step_files") or [])
        print(f"  #{number:<5} {raw_date:<14} {verdict:<24} linked_step_files={step_files}")


def _format_finding(check_name: str, finding: dict[str, Any]) -> str:
    """One finding line per check type (task A3) -- each check's dict shape
    is its own, so this is a dispatch, not a generic key=value dump."""
    if check_name == "broken_references":
        neighbors = finding["neighbors"]
        return (
            f"{finding['attempt']}: references step {finding['missing_step']}, no matching file -- "
            f"nearest existing steps: prev={neighbors['prev_available']} next={neighbors['next_available']}"
        )
    if check_name == "stale_verdicts":
        return (
            f"{finding['attempt']}: verdict dated {finding['attempt_date']!r} but {finding['script']} "
            f"last touched {finding['script_last_touched']} (basis={finding['basis']})"
        )
    if check_name == "revived_dead_variables":
        return (
            f"{finding['attempt']}: dead variable {finding['dead_variable']!r} found in "
            f"{finding['field']}: {finding['excerpt']!r}"
        )
    return str(finding)  # defensive only -- every real check name is handled above


def _format_coverage(result: consistency.CheckResult) -> str:
    """`N/M attempts checked` plus, when some were skipped, why -- shared by
    every non-skipped branch of `_print_consistency_report` so the coverage
    figure is never left out of a clean-looking report (B3: a check that
    examined zero attempts must never read the same as a check that
    examined all of them and found nothing)."""
    note = f"{result.checked}/{result.total} attempts checked"
    skipped_count = result.total - result.checked
    if skipped_count:
        note += f" ({skipped_count} skipped: {result.items_skipped_reason})"
    return note


def _print_consistency_report(results: list[consistency.CheckResult]) -> None:
    print("Consistency checks:")
    for result in results:
        if result.skipped:
            print(f"  [{result.name}] skipped: {result.skip_reason}")
            continue
        if result.total == 0:
            # Nothing to check at all (e.g. an empty attempt timeline) --
            # a genuinely different case from "checked some/none of N and
            # found nothing", so this is the one case still allowed to say
            # a plain OK.
            print(f"  [{result.name}] OK: no attempts to check")
            continue
        coverage = _format_coverage(result)
        if not result.findings:
            if result.checked == 0:
                print(f"  [{result.name}] {coverage} -- coverage is zero, this is NOT a clean bill of health")
            elif result.checked < result.total:
                print(f"  [{result.name}] {coverage}, no issues found among those checked")
            else:
                print(f"  [{result.name}] OK: no issues found ({coverage})")
            continue
        print(f"  [{result.name}] {coverage}, {len(result.findings)} issue(s):")
        for finding in result.findings:
            print(f"    - {_format_finding(result.name, finding)}")


def _resolve_attempts_path(args: argparse.Namespace) -> str:
    """`researchos attempts` accepts the project root either positionally (like
    `init`/`ingest`) or via `--path` (its own original convention, kept for
    compatibility) -- but not both at once."""
    if args.path is not None and args.path_flag is not None:
        raise CliError("give the project root either positionally or via --path, not both")
    if args.path_flag is not None:
        return args.path_flag
    if args.path is not None:
        return args.path
    return "."


def cmd_attempts(args: argparse.Namespace) -> int:
    """`researchos attempts` (task A2 ingest, task A3 listing/`--check`):
    config-driven ingest of a hand-maintained attempt timeline (see
    researchos.ingest.attempts), always run first so what follows reflects the
    current source file. Config-gated on purpose (DESIGN.md section 0,
    "never guess") -- with no .researchos/attempts.toml this prints a
    copy-pasteable template and exits 1 instead of guessing which table in
    the project is the attempt timeline. The same clean-message-plus-exit-1
    handling also covers every other way `.researchos/attempts.toml` can drift out
    of sync with the project: a configured `[columns]` name no longer
    present in the table's actual header (e.g. a renamed column), or the
    configured heading/table no longer locatable at all
    (`attempts_ingest.AttemptsTableNotFoundError`, DESIGN.md's "Attempt
    orphans" section) -- both are raised as `AttemptsConfigError` (the
    latter via that subclass) from inside `ingest_attempts_repo`, not just
    from `load_config`, so both must be caught here too; letting either
    propagate out of this function would print a raw Python traceback
    instead of the clean message this docstring promises.

    Without `--check`: a plain listing of what is registered (researchos.consistency
    .attempts_for_file), no judgement involved. With `--check`: the three
    deterministic consistency checks (researchos.consistency.run_checks), grouped
    in a report; exits 1 if any check reports a finding (skipped checks
    never affect the exit code -- a missing config declaration is not
    itself "a problem found"), so this composes into a caller's own CI/
    pre-commit pipeline.
    """
    opened = _open(_resolve_attempts_path(args))
    _refuse_frozen(opened, "researchos attempts")
    project_root = opened.root
    db_path = _require_db(project_root)
    with _write_guard(opened, human=False):
        conn = db.connect(db_path)
        try:
            try:
                config = attempts_ingest.load_config(project_root)
                counts = attempts_ingest.ingest_attempts_repo(conn, project_root, config)
            except attempts_ingest.AttemptsConfigError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            ingest_pipeline.reingest_attempt_links(conn, project_root)  # 12.7: links waiting on attempts
            print(f"Attempts ({config.file}): {_format_counts(counts)}")

            if args.check:
                results = consistency.run_checks(conn, project_root, config)
                judgements.apply_after_scan(conn, project_root, print)  # 9.1: the end of a scan
                _print_consistency_report(results)
                return 1 if any(r.findings for r in results) else 0
            judgements.apply_after_scan(conn, project_root, print)
            _print_attempts_listing(conn, config)
        finally:
            conn.close()
    return 0


def cmd_mappings(args: argparse.Namespace) -> int:
    """`researchos mappings` (DESIGN.md section 8.5): ingest the human mappings
    file `.researchos/mappings.toml` -- the same `ingest_mappings` the web app's
    watcher runs when the file changes -- and print the counts plus every
    refused entry with its line (or entry index). Refused entries do not
    fail the run (the good ones were ingested; the file is the researcher's
    to fix), so the exit code is 0; a file that cannot be read or parsed at
    all exits 1 with the graph untouched (failing to read the source is not
    evidence its entries were deleted). A missing file is reported, not an
    error: there is simply nothing to ingest, and existing mapping edges
    are left as they are."""
    opened = _open(args.path)
    _refuse_frozen(opened, "researchos mappings")
    project_root = opened.root
    db_path = _require_db(project_root)
    with _write_guard(opened, human=False):
        conn = db.connect(db_path)
        try:
            try:
                report = mappings_ingest.ingest_mappings(conn, project_root)
            except mappings_ingest.MappingsFileError as exc:
                print(f"Error: {exc} -- graph left untouched", file=sys.stderr)
                return 1
            judgements.apply_after_scan(conn, project_root, print)  # 9.1: the end of a scan
        finally:
            conn.close()
    if not report.file_present:
        print(
            f"Mappings: no {mappings_ingest.MAPPINGS_RELATIVE_PATH} -- nothing to ingest "
            "(existing mapping edges left untouched)"
        )
        return 0
    print(f"Mappings ({mappings_ingest.MAPPINGS_RELATIVE_PATH}): {_format_counts(report.counts)}")
    for problem in report.problems:
        print(f"  {'waiting' if problem.waiting else 'refused'} {problem.location()}: {problem.message}")
    return 0


def cmd_confirm(args: argparse.Namespace) -> int:
    """A human act on one machine link, written to the judgment ledger
    first and only then reflected in the index (DESIGN.md 9.1, 9.8):
    `researchos.records.judgements.judge`, the one write path every surface uses.
    Verdicts: confirmed, rejected, withdrawn (the machine's status again),
    undone (takes back the last act). Identifies the link by its 4
    identity columns, or by `--index` into a freshly re-queried queue. A
    hand-drawn link is refused (its truth is .researchos/mappings.toml), and so
    is a pre-V5 project until it is migrated (9.10)."""
    opened = _open(args.path)
    db_path = _require_db(opened.root)
    conn = db.connect(db_path)
    try:
        src, dst, edge_type, extractor = _confirm_target(args, conn)
        matches = [
            e for e in db.query_edges(conn, src=src, dst=dst, type=edge_type) if e["extractor"] == extractor
        ]
        old_status = matches[0]["status"] if matches else None
    finally:
        conn.close()
    try:
        judged = judgements.judge(
            opened.root, (src, dst, edge_type, extractor), args.status,
            via="cli", note=args.note, expected_id=opened.project_id,
        )
    except judgements.JudgementRefused as exc:
        raise CliError(f"not written: {exc}") from exc
    except records_situation.WriteRefused as exc:
        raise CliError(str(exc)) from exc
    except records_lock.ProjectLockError as exc:
        raise CliError(f"could not take the project lock: {exc}") from exc
    entry = judged.entry
    print(
        f"Edge {src} --{edge_type}--> {dst} (extractor={extractor}): {old_status} -> {judged.status} "
        f"[recorded {entry.get('verdict')} as {entry.id}, seq {entry.seq}, in .researchos/judgements.toml]"
    )
    state = judged.state
    if state is not None and state["outcome"] != "applied":
        reason = state.get("reason") or state["outcome"]
        print(f"  not applied: {reason} ({judgements.REASON_LABELS.get(reason, '')}) -- see 'researchos review'")
    return 0


def _confirm_target(args: argparse.Namespace, conn: Connection) -> tuple[str, str, str, str]:
    positional = (args.src, args.dst, args.type, args.extractor)
    if args.index is not None:
        if any(v is not None for v in positional):
            raise CliError("--index cannot be combined with the src/dst/type/extractor positional args")
        # The same queue `status --pending` prints: a link whose old
        # judgment is under review is not in 待确认 (9.6).
        candidates = db.pending_edges(conn) if args.from_status == "pending" else db.query_edges(conn, status=args.from_status)
        queue = _ordered_edges(candidates)
        if not 1 <= args.index <= len(queue):
            raise CliError(
                f"--index {args.index} out of range: the {args.from_status!r} queue has "
                f"{len(queue)} edge(s) right now -- indices are 1-based and re-sorted on "
                f"every run, so re-check with 'researchos status --pending' immediately before use"
            )
        edge = queue[args.index - 1]
        return edge["src"], edge["dst"], edge["type"], edge["extractor"]
    if any(v is None for v in positional):
        raise CliError(
            "confirm requires either all four positional args (src dst type extractor) "
            "or --index (with --from-status)"
        )
    if args.extractor == "mapping":
        raise CliError(
            "this is a hand-drawn link: its one authority is .researchos/mappings.toml -- edit or delete "
            "the mapping there (or in the app) instead"
        )
    return positional  # type: ignore[return-value]


def _print_card_reviews(card_items: dict[str, Any]) -> None:
    """9.11 stage (b) in `researchos review`: one item per changed script, naming
    every card on it; Research OS cannot tell whether a definition changed."""
    groups = card_items["groups"]
    if not groups:
        return
    waiting = sum(1 for g in groups if not g["counted"])
    print(f"Variable cards whose implementation moved: {card_items['count']} under review"
          + (f", {waiting} more waiting on a draft already opened (not counted)" if waiting else "")
          + " (Research OS cannot tell whether a definition changed; answer in the app's 变量 view, "
          "or open the next draft with 'researchos variable revise <id>')")
    for group in groups:
        head = f"{group['script']}: {len(group['cards'])} card(s)" if group["script"] else "input data"
        print(f"  {head}")
        for member in group["cards"]:
            reasons = ", ".join(r["code"] for r in member["reasons"])
            waiting = f" -- draft v{member['draft']} is open" if member.get("draft") is not None else ""
            print(f"    {member['card']} v{member['version']}: {reasons}{waiting}")


def cmd_review(args: argparse.Namespace) -> int:
    """`researchos review` (DESIGN.md 9.6, 9.8): the judgments not applied --
    under review (with the reason, the old verdict, its date and note, the
    basis then and now, and candidate links), in conflict, held because
    their source could not be read, or whose link the index does not hold.
    Settle one with `researchos confirm ...`: the same verdict again (仍然成立,
    recorded on the basis as it is now), the opposite one, or withdrawn."""
    opened = _open(args.path)
    db_path = _require_db(opened.root)
    # The ledger may have changed since anything last applied it (a hand
    # edit, a sync, no engine running): apply it first -- an index write,
    # so under the project lock like a scan. A project frozen until it is
    # migrated (9.12) is only read: nothing lands in its old index.
    frozen = opened.needs_migration
    with nullcontext() if frozen else _write_guard(opened, human=False):
        conn = db.connect(db_path)
        try:
            if frozen and not db._has_machine_status(conn):
                print(f"{opened.root} is read from its pre-V5 index, which keeps no review: its judgments move "
                      f"into the record when it is migrated (researchos migrate {opened.root}). Under review: 0")
                return 0
            if not frozen:
                judgements.apply_ledger(conn, opened.root)
            items = judgements.review_items(conn)
            card_items = card_implementation.review_groups(conn)
        finally:
            conn.close()
    if args.json:
        print(json.dumps({**items, "cards": card_items}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    ledger_state = items["ledger"]
    if ledger_state and ledger_state.get("state") != "ok":
        print(
            f"Judgment ledger: {ledger_state['state']} -- {ledger_state.get('reason')} "
            f"({ledger_state.get('detail') or ''}); the index keeps what it had and nothing is written to the ledger"
        )
        if ledger_state.get("state") == "shrunk":
            print("  see the question with: researchos records (it prints the answers bound to the missing entries)")
    print(f"Under review: {items['count']}")
    for item in items["review"]:
        _print_review_item(item)
    if items["source_unreadable"]:
        print(f"Source not readable (judgment kept as it was): {len(items['source_unreadable'])}")
        for item in items["source_unreadable"]:
            print(f"  {_item_label(item)} [{item['source_status']}]")
    if items["not_in_index"]:
        print(f"Judged links the index does not hold: {len(items['not_in_index'])}")
        for item in items["not_in_index"]:
            print(f"  {_item_label(item)} ({item['verdict']} {_when(item)})")
    _print_card_reviews(card_items)
    return 0


MIGRATED_WHEN = "migrated from the old index; original judgment time unknown"


def _when(item: dict[str, Any]) -> str:
    """When a judgment was made, as far as the record knows: a migrated
    entry's `at` is the migration's (9.12), never shown as the judgment's."""
    if item.get("migrated"):
        return f"({MIGRATED_WHEN})"
    return f"at {item.get('at')}"


def _item_label(item: dict[str, Any]) -> str:
    return f"{item['src']} --{item['type']}--> {item['dst']} (extractor={item['extractor']})"


def _print_review_item(item: dict[str, Any]) -> None:
    print(f"  {_item_label(item)}")
    print(f"    reason: {item['reason']} ({item['label']})")
    if item["outcome"] == "conflict":
        for n, branch in enumerate(item["detail"].get("branches", []), start=1):
            acts = ", ".join(f"{e.get('verdict')} seq {e.get('seq')} {_when(e)}" for e in branch) or "-"
            print(f"    history {n}: {acts}")
        print("    settle it with a new judgment: researchos confirm ... --status confirmed|rejected|withdrawn")
        return
    note = f", note: {item['note']}" if item.get("note") else ""
    print(f"    was: {item['verdict']} {_when(item)}{note}")
    print(f"    basis then: {json.dumps(item['basis'], ensure_ascii=False, sort_keys=True)}")
    print(f"    basis now:  {json.dumps(item['basis_now'], ensure_ascii=False, sort_keys=True)}")
    for cand in item["candidates"]:
        print(f"    candidate ({judgements.CANDIDATE_HINT}): {_item_label(cand)}")
    print(
        f"    settle: researchos confirm {item['src']} {item['dst']} {item['type']} {item['extractor']} "
        f"--status {item['verdict']}|{'confirmed' if item['verdict'] == 'rejected' else 'rejected'}|withdrawn"
    )


def cmd_records(args: argparse.Namespace) -> int:
    """`researchos records` (DESIGN.md 9.2, 9.8): where each kind of human labor
    lives for this project, how many, the newest snapshot, and anything
    that stands between Research OS and trusting it (`researchos.inventory`). `--verify`
    checks, per link, that the index's human state is what the record
    implies -- judgments, hand-drawn links, attempt verdicts -- and exits 1
    with the differences if not. `--clean` lists copies nothing refers to
    and, with `--yes`, removes them. `--answer file|restore` answers 9.3's
    question when the judgment ledger has fewer entries than the index
    applied."""
    opened = _open(args.path)
    root = opened.root
    if args.missing is not None and not args.answer:
        raise CliError("--missing goes with --answer")
    if args.answer and args.file == "decisions":
        shown = None if args.missing is None else [i.strip() for i in args.missing.split(",") if i.strip()]
        try:
            with records_situation.write_guard(root, opened.project_id, human=True) as held:
                missing, appended = decisions_mod.answer_shrunk(root, args.answer, lock=held, expected_missing=shown)
        except decisions_mod.DecisionRefused as exc:
            raise CliError(f"not answered: {exc}") from exc
        except records_situation.WriteRefused as exc:
            raise CliError(str(exc)) from exc
        if args.answer == decisions_mod.ANSWER_FILE:
            print(f"Took decisions.toml as it is: {missing} entr(y/ies) dropped from this machine's copy")
        else:
            print(f"Appended {appended} missing decision(s) to .researchos/decisions.toml (via = recovered)")
    elif args.answer and args.file == "notes":
        shown = None if args.missing is None else [i.strip() for i in args.missing.split(",") if i.strip()]
        try:
            missing, appended = notes_mod.answer_shrunk(root, args.answer, expected_missing=shown, expected_id=opened.project_id)
        except notes_mod.NoteRefused as exc:
            raise CliError(f"not answered: {exc}") from exc
        except records_situation.WriteRefused as exc:
            raise CliError(str(exc)) from exc
        if args.answer == notes_mod.ANSWER_FILE:
            print(f"Took the notes file as it is: {missing} entr(y/ies) dropped from the index's copy")
        else:
            print(f"Appended {appended} missing entr(y/ies) to .researchos/notes.toml (via = recovered)")
    elif args.answer:
        shown = None if args.missing is None else [i.strip() for i in args.missing.split(",") if i.strip()]
        try:
            answered = judgements.answer_shrunk(root, args.answer, expected_missing=shown, expected_id=opened.project_id)
        except judgements.JudgementRefused as exc:
            raise CliError(f"not answered: {exc}") from exc
        except records_situation.WriteRefused as exc:
            raise CliError(str(exc)) from exc
        if args.answer == judgements.ANSWER_FILE:
            print(f"Took the file as it is: {len(answered.missing)} entr(y/ies) dropped from the index's copy")
        else:
            print(f"Appended {len(answered.appended)} missing entr(y/ies) to .researchos/judgements.toml (via = recovered)")
    if args.clean:
        return _records_clean(opened, apply=args.yes)
    db_path = paths.graph_db_path(root)
    conn = db.connect(db_path) if db_path.exists() else None
    try:
        for line in records_inventory_lines(conn, root):
            print(line)
        if not args.verify:
            return 0
        if conn is None:
            raise CliError(f"there is no index to verify ({db_path}); open the project to build it")
        problems = inventory.verify(conn, root)
    finally:
        if conn is not None:
            conn.close()
    if problems:
        print(f"Verify: {len(problems)} mismatch(es)")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print("Verify: the index's human state is what the record implies")
    return 0


def _records_clean(opened: project_identity.Opened, *, apply: bool) -> int:
    try:
        report = inventory.clean(opened.root, apply=apply, expected_id=opened.project_id)
    except records_situation.WriteRefused as exc:
        raise CliError(str(exc)) from exc
    except records_lock.ProjectLockError as exc:
        raise CliError(f"could not take the project lock: {exc}") from exc
    for directory in report.undecidable:
        print(f"  left alone: {directory} (its references cannot all be read)")
    if apply:
        print(f"Removed {len(report.removed)} copy/copies nothing refers to")
        for path in report.removed:
            print(f"  removed {path}")
        return 0
    print(f"Copies nothing refers to: {len(report.removable)}" + (" (dry run; --yes removes them)" if report.removable else ""))
    for path in report.removable:
        print(f"  {path}")
    return 0


def records_inventory_lines(conn: Connection | None, root: Path) -> list[str]:
    """The 9.2 inventory (`researchos.inventory.inventory`), one line per kind of
    human labor -- each kind exactly once -- plus its problems, and, when
    the ledger has shrunk, the exact command that answers the question
    shown (with the ids of the missing entries, 9.12)."""
    lines = [f"Records of {root}:"]
    seen: set[str] = set()
    for row in inventory.inventory(conn, root):
        if row.kind in seen:
            continue
        seen.add(row.kind)
        snap = f"; newest snapshot {row.snapshot}" if row.snapshot else ""
        lines.append(f"  {row.kind}: {row.path} -- {row.count}{snap}")
        for problem in dict.fromkeys(row.problems):
            lines.append(f"    ! {problem}")
    if conn is not None:
        lines += _shrunk_question_lines(conn, root)
    return lines


def _shrunk_question_lines(conn: Connection, root: Path) -> list[str]:
    """9.3's question, as the CLI shows it: the missing entries, and the
    answers bound to them (`--missing`)."""
    got = _identity_or_none(root)
    loaded, decision = judgements.assess(conn, root, got, for_migration=True)
    out = _notes_question_lines(conn, root, got)
    if decision.reason != "shrunk" or not decision.missing:
        return out
    ids = ",".join(str(m["id"]) for m in decision.missing)
    out += [f"  The judgment ledger has {len(decision.missing)} fewer judgment(s) than the index applied:"]
    for m in decision.missing:
        out.append(f"    {m.get('id')}: {m.get('verdict')} {m.get('src')} --{m.get('type')}--> {m.get('dst')}")
    out.append(f"  answer: researchos records --answer file --missing {ids} {root}     (take the file as it is)")
    out.append(f"      or: researchos records --answer restore --missing {ids} {root}  (append the missing ones back)")
    return out


def _notes_question_lines(conn: Connection, root: Path, identity: Any) -> list[str]:
    """The same question for the notes ledger (12.6)."""
    _loaded, decision = notes_mod.assess(conn, root, identity, for_migration=True)
    if decision.reason != "shrunk" or not decision.missing:
        return []
    ids = ",".join(str(m["id"]) for m in decision.missing)
    out = [f"  The notes file has {len(decision.missing)} fewer entr(y/ies) than the index applied:"]
    for m in decision.missing:
        out.append(f"    {m.get('id')}: {m.get('act')} on {m.get('node')}")
    out.append(f"  answer: researchos records --file notes --answer file --missing {ids} {root}     (take the file as it is)")
    out.append(f"      or: researchos records --file notes --answer restore --missing {ids} {root}  (append the missing ones back)")
    return out


def _identity_or_none(root: Path):
    from researchos.records.identity import IdentityState, read_identity  # noqa: PLC0415

    got = read_identity(root)
    return got.identity if got.state is IdentityState.PRESENT else None


def cmd_rebuild(args: argparse.Namespace) -> int:
    """`researchos rebuild` (DESIGN.md 9.8): a fresh index beside the current one,
    the record applied, human state compared per link; swapped only when
    the comparison is clean and every source could be read. The previous
    index is kept one generation (`graph.db.prev`)."""
    opened = _open(args.path)
    print(f"Rebuilding the index of {opened.root}")
    try:
        result = rebuild_mod.rebuild(opened.root, expected_id=opened.project_id, echo=print)
    except rebuild_mod.RebuildRefused as exc:
        raise CliError(str(exc)) from exc
    except rebuild_mod.SwapRefused as exc:
        raise CliError(str(exc)) from exc
    except records_situation.WriteRefused as exc:
        raise CliError(str(exc)) from exc
    except records_lock.ProjectLockError as exc:
        raise CliError(f"could not take the project lock: {exc}") from exc
    tally = result.tally
    if tally:
        print(
            f"Per-link comparison: {tally.get('applied_before', 0)} judgment(s) applied before, "
            f"{tally.get('applied_after', 0)} still applied after, {tally.get('changed_source', 0)} on a changed source"
        )
    for item in result.blocked:
        print(f"  blocked: {item}")
    for item in result.failures:
        print(f"  failure: {item}")
    if not result.swapped:
        print("Not swapped: the current index is unchanged and keeps serving")
        return 1
    print(f"Swapped in the new index at {result.db_path}" + (f"; the previous one is kept at {result.previous}" if result.previous else ""))
    return 0


def _print_preview(view: migration.Preview) -> None:
    index = view.index
    print(f"  {index.db_path}")
    if index.error:
        print(f"    cannot be read: {index.error}")
        return
    came = index.recorded_path or "(not recorded)"
    exists = "" if index.path_exists is None else (" (exists)" if index.path_exists else " (no longer exists)")
    print(f"    came from: {came}{exists}")
    print(
        f"    holds: confirmed {index.confirmed}, rejected {index.rejected} "
        f"({index.remembered} remembering a prior status), arranged views {index.arranged_views}, "
        f"hand-drawn links {index.mapping_links} (skipped: their truth is mappings.toml)"
    )
    if index.judged:
        print(f"    match: a scan of {view.root} produces both ends of {view.produced} of {index.judged} judged link(s)")
        if view.produced < index.judged and view.outside:
            print(f"    {view.outside} script(s) in this folder read or write absolute paths outside it -- a low match "
                  f"here is what a moved or copied project looks like (those paths cannot be mapped into this folder)")
    if view.scan_error:
        print(f"    (the scan of this folder failed: {view.scan_error})")


def cmd_migrate(args: argparse.Namespace) -> int:
    """`researchos migrate` (DESIGN.md 9.5): an explicit act, never a side effect
    of opening. `--list`: every un-retired pre-V5 index on this machine.
    Without `--yes`: what waits for this folder and how well it matches,
    nothing written. `--yes`: migrate (export, basis, identity, rebuild
    and verify against the old index's own count, retire). `--not-mine`:
    「这不是这个项目的」 -- the old index is left alone and not offered to
    this folder again. `--from DIR`: a stranded index. An unfinished
    migration resumes without being asked again."""
    if args.list:
        found = migration.list_old_indexes()
        print(f"Un-retired pre-V5 indexes under {paths.researchos_home() / paths.GRAPHS_DIRNAME}: {len(found)}")
        for index in found:
            exists = "?" if index.path_exists is None else ("exists" if index.path_exists else "missing")
            print(
                f"  {index.db_path.parent} -- {index.recorded_path or '(no project path recorded)'} [{exists}]; "
                f"confirmed {index.confirmed}, rejected {index.rejected}, remembered prior status {index.remembered}, "
                f"arranged views {index.arranged_views}" + (f"; cannot be read: {index.error}" if index.error else "")
            )
        return 0
    root = Path(args.path).resolve()
    if not root.is_dir():
        raise CliError(f"{root} is not a directory")
    try:
        project_identity.open_project(root)
    except (project_identity.ProjectBlocked, project_identity.AnswerRefused) as exc:
        raise CliError(str(exc)) from exc
    try:
        if args.not_mine:
            declined = migration.decline(root, from_dir=args.from_dir)
            print(f"Not this project's: {', '.join(declined) or 'nothing waiting'} -- left untouched, and not offered to {root} again")
            return 0
        results = migration.migrate(root, yes=args.yes, from_dir=args.from_dir, echo=print if args.verbose else (lambda _l: None))
    except migration.MigrationRefused as exc:
        raise CliError(str(exc)) from exc
    except records_lock.ProjectLockError as exc:
        raise CliError(f"could not take the project lock: {exc}") from exc
    if results and results[0].previews:
        print(f"Pre-V5 index(es) that may hold the judgments of {root}:")
        for view in results[0].previews:
            _print_preview(view)
        print("Nothing was written. To move these judgments into .researchos/judgements.toml: researchos migrate --yes; "
              "if this is not this project's index: researchos migrate --not-mine")
        return 1
    status = 0
    for result in results:
        print(f"Migration of {result.key}" + (" (resumed)" if result.resumed else "") + ":")
        if result.tally is not None:
            for line in result.tally.lines():
                print(f"  {line}")
        if result.exported is not None:
            print(f"  ledger entries appended: {result.exported.appended}"
                  + ("; arrangement copied to .researchos/canvas.json" if result.exported.copied_arrangement else ""))
        for note in result.notes:
            print(f"  note: {note}")
        if result.ok:
            if result.retired_to is not None:
                print(f"  the old index was retired to {result.retired_to}")
            print(f"  project id: {result.identity.id if result.identity else '?'}")
        else:
            print(f"  stopped: {result.stopped}")
            status = 1
    return status


def cmd_judge(args: argparse.Namespace) -> int:
    """`researchos judge` (S2): the optional semantic layer. Reviews every pending
    `backed_by` candidate and annotates it with a model's opinion --
    written to `evidence.semantic_review` via `db.set_edge_semantic_review`
    only (see researchos.semantic.judge's module docstring for why: the machine
    write path may only ever produce `status` in {auto, pending}, so this
    command never calls `db.set_edge_status`/`db.upsert_edge` and cannot
    move an edge to confirmed/rejected no matter what the model says).

    Zero-dependency baseline (task requirement 6): a backend that is not
    reachable fails this one command clearly and exits non-zero -- it never
    touches ingest/status/query/trace/confirm, none of which import
    anything from researchos.semantic to begin with.
    """
    opened = _open(args.path)
    _refuse_frozen(opened, "researchos judge")
    db_path = _require_db(opened.root)
    with _write_guard(opened, human=False):
        return _judge(args, db.connect(db_path))


def _judge(args: argparse.Namespace, conn: Connection) -> int:
    try:
        llm = semantic_backend.LlmBackend()
        try:
            llm.probe()
        except semantic_backend.LlmError as exc:
            raise CliError(
                f"semantic backend unavailable: {exc} -- 'researchos judge' is the only affected "
                "command; ingest/status/query/trace/confirm work with no model running at all"
            ) from exc

        result = semantic_judge.review_pending_backed_by(
            conn, llm, limit=args.limit, dry_run=args.dry_run,
        )
        mode = "dry run -- no writes" if args.dry_run else "writing semantic_review annotations"
        print(f"Judging pending backed_by candidates ({mode}), backend model={llm.model!r}:")
        print(f"  pending backed_by edges total: {result.total_pending}")
        for outcome in result.reviewed:
            if outcome.error:
                print(f"  [error] {outcome.src} --backed_by--> {outcome.dst}: {outcome.error}")
                continue
            flag = " [hallucinated better_match dropped]" if outcome.hallucination_dropped else ""
            print(
                f"  {outcome.src} --backed_by--> {outcome.dst}: related={outcome.related} "
                f"better_match={outcome.better_match!r}{flag} reason={outcome.reason!r}"
            )
        errors = sum(1 for o in result.reviewed if o.error)
        written = sum(1 for o in result.reviewed if o.written)
        print(f"  reviewed={len(result.reviewed)} written={written} errors={errors}")
    finally:
        conn.close()
    return 0


def _print_edge(edge: dict, other_side: str, direction: str, conn=None) -> None:
    evidence = json.dumps(edge["evidence"], sort_keys=True)
    marker = judgements.review_marker(judgements.link_flags(conn).for_key(judgements.key_of(edge))) if conn is not None else ""
    # A claim's line lives on the claim node, not in the edge evidence, so resolve
    # it here the same way `status --pending` and `trace` do -- otherwise this is
    # the one consumer that cannot tell the reader where in the paper to look.
    location = ""
    if conn is not None and edge["type"] == "backed_by":
        loc = query.claim_source_location(conn, edge["src"]) or query.claim_source_location(
            conn, edge["dst"]
        )
        if loc and loc.get("line") is not None:
            location = f" source_location={loc.get('file')}:{loc['line']}"
    print(
        f"  {direction} {edge[other_side]} [{edge['type']}] extractor={edge['extractor']} "
        f"confidence={edge['confidence']:.2f} status={edge['status']}{marker} evidence={evidence}{location}"
    )


def cmd_query(args: argparse.Namespace) -> int:
    project_root = _resolve_project_root(args.path)
    conn = db.connect(_require_db(project_root))
    try:
        node = db.get_node(conn, args.node_id)
        if node is None:
            print(f"No such node: {args.node_id}", file=sys.stderr)
            return 1

        print(f"Node: {node['id']} ({node['type']})")
        if node["title"]:
            print(f"  title: {node['title']}")
        print(f"  attrs: {json.dumps(node['attrs'], sort_keys=True)}")
        if node["human_fields"]:
            print(f"  human_fields: {json.dumps(node['human_fields'], sort_keys=True)}")

        outgoing = db.query_edges(conn, src=args.node_id)
        incoming = db.query_edges(conn, dst=args.node_id)
        print(f"Outgoing edges ({len(outgoing)}):")
        for edge in outgoing:
            _print_edge(edge, "dst", "->", conn)
        if not outgoing:
            print("  (none)")
        print(f"Incoming edges ({len(incoming)}):")
        for edge in incoming:
            _print_edge(edge, "src", "<-", conn)
        if not incoming:
            print("  (none)")
    finally:
        conn.close()
    return 0


def _format_occurrence(occurrence: dict[str, Any], display_line: int | None = None) -> str:
    """Render one evidence occurrence dict as a readable string.

    Special-cases the common {"file": ..., "line": ...} shape (latex/pyfig
    extractors) as "file:line"; every other key (sha, run_id, artifact_path,
    callee, ...) falls back to sorted "key=value" pairs. This only reformats
    keys that are actually present -- it never invents fields an extractor
    didn't record.

    `display_line` fills in a missing "line" for display only (never
    written back to the occurrence dict in the database) -- `backed_by`
    occurrences from researchos.ingest.claims no longer carry one (see
    researchos.query.claim_source_location), so the caller passes the claim
    node's current line here to keep the "file:line" rendering instead of
    falling back to a bare "file=...".
    """
    remaining = dict(occurrence)
    if display_line is not None and "file" in remaining and "line" not in remaining:
        remaining["line"] = display_line
    parts: list[str] = []
    if "file" in remaining and "line" in remaining:
        parts.append(f"{remaining.pop('file')}:{remaining.pop('line')}")
    parts.extend(f"{key}={remaining[key]}" for key in sorted(remaining))
    return ", ".join(parts) if parts else "(no detail)"


def _format_evidence_summary(evidence: dict[str, Any], display_line: int | None = None) -> str:
    """Expand an edge's evidence into a readable summary.

    db.upsert_edge stores evidence as {"occurrences": [dict, ...]} (T10); a
    pre-T10 bare-evidence dict (see db._merge_edge_evidence's docstring) is
    treated as its own single occurrence rather than requiring a migration.

    `display_line` (see `_format_occurrence`) is passed through to every
    occurrence -- harmless for occurrence shapes that already carry their
    own "line" (it is only ever used to fill a *missing* one).
    """
    occurrences = evidence.get("occurrences")
    if not isinstance(occurrences, list):
        occurrences = [evidence]
    return "; ".join(_format_occurrence(occ, display_line) for occ in occurrences)


def _format_trace_human(node_id: str, max_hops: int, result: dict[str, Any]) -> str:
    """Indented, evidence-expanded text for `researchos trace` (no --json).

    `hop["source_location"]` (query.trace's uniform, query-time-resolved
    claim line -- see researchos.query.claim_source_location) is unwrapped to its
    plain `line` int and threaded through as `_format_evidence_summary`'s
    `display_line`, exactly like `_format_pending_line` does for `status
    --pending` -- the same value, read through the same function, so the
    two display paths can never drift the way they did when only one of
    them was patched to backfill it.
    """
    if not result["hops"]:
        return f"Node {node_id} exists but has no provenance edges recorded."
    lines = [f"Provenance trace for {node_id} (max_hops={max_hops}):"]
    for hop in result["hops"]:
        indent = "  " * hop["depth"]
        lines.append(f"{indent}[depth {hop['depth']}] {hop['src']} --{hop['type']}--> {hop['dst']}")
        lines.append(
            f"{indent}    extractor={hop['extractor']} confidence={hop['confidence']:.2f} "
            f"status={hop['status']}{judgements.review_marker(hop)}"
        )
        display_line = _display_line(hop.get("source_location"))
        lines.append(f"{indent}    evidence: {_format_evidence_summary(hop['evidence'], display_line)}")
    return "\n".join(lines)


def cmd_trace(args: argparse.Namespace) -> int:
    project_root = _resolve_project_root(args.path)
    if args.chain:
        return _trace_chain(project_root, args.node_id, as_json=args.json)
    conn = db.connect(_require_db(project_root))
    try:
        result = query.trace(conn, args.node_id, max_hops=args.hops)
    finally:
        conn.close()

    if not result["found"]:
        print(f"No such node: {args.node_id}", file=sys.stderr)
        return 1
    if args.json:
        # max_hops is echoed back explicitly (T-blocker fix, 2026-07-26) so a
        # scripted consumer can tell how far this trace was allowed to walk,
        # rather than inferring it from an argv it may not have access to.
        print(json.dumps({**result, "max_hops": args.hops}, sort_keys=True))
    else:
        print(_format_trace_human(args.node_id, args.hops, result))
    return 0


def _trace_chain(project_root: Path, node: str, *, as_json: bool) -> int:
    """`researchos trace --chain` (DESIGN.md 12.7): data -> scripts -> the
    node -> the drafts, with its attempts, the variable versions and every
    break with its repair."""
    conn = db.connect(_require_db(project_root))
    try:
        try:
            result = trace_mod.chain(conn, project_root, node)
        except ValueError as exc:
            raise CliError(str(exc)) from exc
    finally:
        conn.close()
    if not result["found"]:
        print(f"No such node or file: {result['node']['id']}", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    for line in trace_mod.summary_lines(result):
        print(line)
    for item in result["breaks"]:
        repair = item.get("repair")
        if repair and repair["type"] in ("generates", "writes"):
            print(f"  repair: researchos link <script> {item['path']} --type {repair['type']} {project_root}")
        elif repair:
            print(f"  repair: researchos link {item['path'] or item['node']} attempt:<table>#<n> --type belongs_to {project_root}")
    return 0


def cmd_link(args: argparse.Namespace) -> int:
    """`researchos link FROM TO --type T` (DESIGN.md 8.5, 12.7): a hand-drawn
    mapping appended to .researchos/mappings.toml -- 「补上来源」 (`generates`,
    `writes`), 「关联文件」 (`belongs_to`, TO = attempt:<table>#<n>) and
    「源自」 (`derives_from`, DESIGN.md 14.2: FROM grew out of TO, both
    attempt:<table>#<n>, both in the index) -- then the mappings read
    again. `--remove` deletes the entry instead."""
    opened = _open(args.path)
    _refuse_frozen(opened, "researchos link")
    root = opened.root
    db_path = _require_db(root)
    try:
        if args.remove:
            result = mappings_ingest.delete_mapping(root, args.from_path, args.to_path, args.type)
        else:
            result = mappings_ingest.add_mapping(root, args.from_path, args.to_path, args.type, note=args.note)
    except mappings_ingest.MappingsWriteError as exc:
        raise CliError(f"not written ({exc.code}): {exc}") from exc
    except records_situation.WriteRefused as exc:
        raise CliError(str(exc)) from exc
    with _write_guard(opened, human=False):
        conn = db.connect(db_path)
        try:
            report = mappings_ingest.ingest_mappings(conn, root)
            judgements.apply_after_scan(conn, root)
        finally:
            conn.close()
    verb = "Removed" if args.remove else "Added"
    print(f"{verb} {args.from_path} -> {args.to_path} ({args.type}) in {result['file']}")
    for problem in report.problems:
        print(f"  {'waiting' if problem.waiting else 'refused'} {problem.location()}: {problem.message}")
    return 0


def cmd_me(args: argparse.Namespace) -> int:
    """`researchos me set|show` (DESIGN.md 12.1): who I am on this machine,
    in ~/.researchos/me.toml. From then on every entry Research OS appends
    to a ledger carries `by` = this id."""
    if args.me_command == "key":
        try:
            got = share_mod.my_key()
        except share_mod.ShareError as exc:
            raise CliError(str(exc)) from exc
        if args.out:
            target = Path(args.out).expanduser()
            try:
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            except FileExistsError as exc:
                raise CliError(f"{target} already exists; not overwritten") from exc
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(got["key"] + "\n")
            print(f"Public key written to {target} ({got['fingerprint']}); send it to the PI for "
                  f"'researchos team add <your id> --name <name> --key {target.name}'", file=sys.stderr)
            return 0
        print(got["key"])
        print(f"fingerprint {got['fingerprint']} ({got['path']})", file=sys.stderr)
        return 0
    if args.me_command == "set":
        try:
            me = people.write_me(args.name, args.email)
        except people.PeopleError as exc:
            raise CliError(str(exc)) from exc
        print(f"Me: {me.name} <{me.email}> (id {me.id}) -- written to {people.me_path()}")
        print("  every entry Research OS appends from now on carries by = " + me.id)
        return 0
    got = people.read_me()
    if args.json:
        print(json.dumps({"state": got.state, "path": str(got.path), "me": got.me.payload() if got.me else None,
                          "error": got.error}, ensure_ascii=False, indent=2))
        return 0 if got.state in ("present", "absent") else 1
    if got.state == "absent":
        print(f"Me: not set ({got.path} does not exist) -- entries carry no 'by'; set it with "
              "researchos me set --name NAME --email EMAIL")
        return 0
    if got.me is None:
        print(f"Me: {got.path} cannot be read ({got.state}: {got.error}); every ledger write is refused until "
              f"it is fixed (researchos me set ...)")
        return 1
    print(f"Me: {got.me.name} <{got.me.email}> (id {got.me.id})")
    return 0


def _note_error(exc: Exception) -> CliError:
    return CliError(f"not written: {exc}")


def cmd_note(args: argparse.Namespace) -> int:
    """`researchos note add|list|withdraw` (DESIGN.md 12.6): notes on any
    node, in .researchos/notes.toml -- append-only; a note is withdrawn,
    never edited."""
    if args.note_command == "list" and args.path is None:
        # `note list PATH`: one positional that names a folder is the
        # project, not a node (a node is a file or an id, never a folder).
        if args.node is not None and Path(args.node).expanduser().is_dir():
            args.path, args.node = args.node, None
        else:
            args.path = "."
    opened = _open(args.path)
    root = opened.root
    if args.note_command in ("add", "withdraw"):
        try:
            if args.note_command == "add":
                entry = notes_mod.add_note(root, args.node, args.text, via="cli", expected_id=opened.project_id)
                print(f"Note {entry.id} on {entry.get('node')} appended to .researchos/notes.toml"
                      + (f" (by {entry.get('by')})" if entry.get("by") else ""))
            else:
                entry = notes_mod.withdraw_note(root, args.note_id, via="cli", expected_id=opened.project_id)
                print(f"Note {args.note_id} withdrawn (entry {entry.id})")
        except notes_mod.NoteRefused as exc:
            raise _note_error(exc) from exc
        except (records_situation.WriteRefused, records_lock.ProjectLockError) as exc:
            raise _note_error(exc) from exc
        return 0
    db_path = paths.graph_db_path(root)
    conn = db.connect(db_path) if db_path.exists() else None
    try:
        try:
            listed = notes_mod.notes_for(root, args.node, conn)
        except ValueError as exc:
            raise CliError(str(exc)) from exc
    finally:
        if conn is not None:
            conn.close()
    if args.json:
        print(json.dumps(listed, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if not listed["trusted"]:
        print(f"Notes file: {listed['state']} -- {listed['reason']} ({listed['detail'] or ''}); showing what the "
              f"index last applied; nothing can be written until it is resolved")
    shown = [n for n in listed["notes"] if args.all or not n["withdrawn"]]
    print(f"Notes{' on ' + listed['node'] if listed['node'] else ''}: {len(shown)}")
    for item in shown:
        who = item["by"] or "the project's first researcher"
        mark = f" [withdrawn {item.get('withdrawn_at')}]" if item["withdrawn"] else ""
        where = "" if listed["node"] else f" {item['node']}"
        print(f"  {item['id']} {item['at']} {who}{where}{mark}")
        for line in str(item["text"]).splitlines():
            print(f"    {line}")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    """`researchos history NODE` (DESIGN.md 12.6): every version of a node,
    newest first -- git's commits for a file git tracks, the version store
    otherwise; an attempt's verdict as it moved; a variable's log."""
    root = _resolve_project_root(args.path)
    try:
        listed = history_mod.versions(root, args.node)
    except history_mod.HistoryError as exc:
        raise CliError(str(exc)) from exc
    if args.json:
        print(json.dumps(listed, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    print(f"History of {listed['node']} ({listed.get('origin') or 'none'}): {len(listed['versions'])} version(s)")
    if listed.get("reason"):
        print(f"  {listed['reason']}")
    for v in listed["versions"]:
        who = v.get("who") or "this machine"
        if v.get("approved_by"):
            who += f" (approved by {v['approved_by']})"
        extra = ""
        if listed["kind"] == "attempt":
            extra = f"  verdict={v.get('verdict') or '-'} result={v.get('result') or '-'}"
        elif v.get("summary") and v["summary"].get("format"):
            s = v["summary"]
            extra = (f"  rows={s.get('rows', s.get('sheet_rows'))} columns="
                     f"{','.join(s['columns']) if s.get('columns') else s.get('sheet_columns')}")
        size = f" {v['size']}B" if v.get("size") is not None else ""
        note = f"  {v['note']}" if v.get("note") else ""
        print(f"  {v['version'][:12]:<12} {v.get('at')} {v.get('source')} {who}{size}{extra}{note}")
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    """`researchos diff NODE [A B] [PATH]` (DESIGN.md 12.6): what changed
    between two versions (default: the newest and the one before it) -- a
    unified diff for text, a sentence for tables, the verdict for an
    attempt. The project is the trailing positional or --path."""
    rest = list(args.rest)
    if len(rest) in (1, 3):
        if args.diff_path is not None:
            raise CliError("give the project root either positionally or via --path, not both")
        path = rest.pop()
    else:
        path = args.diff_path or "."
    if len(rest) not in (0, 2):
        raise CliError("name both versions to compare, or neither")
    a, b = (rest + [None, None])[:2]
    root = _resolve_project_root(path)
    try:
        result = history_mod.diff(root, args.node, a, b)
    except history_mod.HistoryError as exc:
        raise CliError(str(exc)) from exc
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    print(f"{result['node']}: {result['a']['version']} -> {result['b']['version']}")
    if result["summary"]:
        print(f"  {result['summary']}")
    if result["diff"]:
        sys.stdout.write(result["diff"])
    if result["truncated"]:
        print("  (diff truncated)")
    return 0


# -- proposals and approval (DESIGN.md 12.3, 12.4) --------------------------------------------


def _proposal_error(exc: Exception) -> CliError:
    if isinstance(exc, proposals_mod.ProposalRefused) and exc.details.get("conflicts"):
        lines = [f"  both sides changed: {c['path']} ({c['reason']})" for c in exc.details["conflicts"]]
        return CliError(f"{exc}\n" + "\n".join(lines))
    return CliError(f"nothing written: {exc}")


_PROPOSAL_ERRORS = (proposals_mod.ProposalRefused, exchange_mod.ExchangeError, records_situation.WriteRefused,
                    records_lock.ProjectLockError)


def _exchange_arg(args: argparse.Namespace, root: Path, project_id: str | None):
    if getattr(args, "exchange", None) is None:
        return None
    return exchange_mod.for_project(project_id or "", root, override=args.exchange)


def _print_files(files: list[dict[str, Any]], *, diffs: bool = True) -> None:
    for item in files:
        state = f" [{item['state']}]" if item.get("state") else ""
        size = f" {item['size']}B" if item.get("size") is not None else ""
        print(f"  {item['action']:<8} {item['path']}{size}{state}"
              + (f" ({item['store']})" if item.get("store") else ""))
        if item.get("reason"):
            print(f"    {item['reason']}")
        if item.get("sentence"):
            print(f"    {item['sentence']}")
        if diffs and item.get("diff"):
            for line in item["diff"].splitlines()[:200]:
                print(f"    {line}")


def cmd_propose(args: argparse.Namespace) -> int:
    """`researchos propose KIND --sentence S` (DESIGN.md 12.3): what the working
    copy would propose -- a dry run unless --yes, which writes it to the
    exchange (once; ready.toml last)."""
    opened = _open(args.path)
    root = opened.root
    route = parents = None
    if args.kind == "route_link":
        # 14.2: a 「源自」 line, --child grew out of the one --parent.
        if len(args.parent or []) > 1:
            raise CliError("a route_link names one --parent: the attempt the --child grew out of")
        route = {"op": args.op, "child": args.child, "parent": (args.parent or [None])[0], "note": args.note}
    else:
        if args.op or args.child or args.note:
            raise CliError("--op, --child and --note name a route_link's line; a new attempt names --parent only")
        parents = args.parent
    try:
        draft = proposals_mod.make_proposal(
            root, args.kind, args.sentence or "", number=args.number, result=args.result, reason=args.reason,
            card=args.card, attested=args.attested, only=args.only, supersedes=args.supersedes,
            add=args.add, leave=args.leave, route=route, parents=parents,
        )
        if args.json and not args.yes:
            print(json.dumps({"proposal": draft.data, "preview": draft.preview, "fingerprint": draft.fingerprint},
                             ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        data = draft.data
        print(f"Proposal ({data['kind']}) by {data['by']} against {data['base'][:19]}: {data['sentence']}")
        if data.get("attempt"):
            row = data["attempt"]
            print(f"  new attempt row #{row['number']}: {row['description']} | {row['verdict']} | {row['result']}"
                  + (f"; abandoned, reason: {row['reason']}" if row.get("abandons") == "yes" else ""))
            for line in proposals_mod.parent_words(row, "en"):
                print(f"  {line}")
        if data.get("route"):
            print(f"  {_route_words(data['route'])}")
        if data.get("verdict"):
            v = data["verdict"]
            print(f"  attempt #{v['number']}: verdict {v['old']} -> {v['new']}; result {v['old_result']!r} -> {v['result']!r}"
                  + (f"; reason: {v['reason']}" if v.get("reason") else ""))
        if data.get("variable"):
            v = data["variable"]
            print(f"  variable {v['card']}: {v['file']} (attested={v['attested']})")
        print(f"Files: {len(data.get('file', []))}")
        _print_files(draft.preview.get("files", []))
        for item in data.get("not_carried", []):
            print(f"  not carried: {item['path']} -- {item['reason']}")
        for item in draft.preview.get("candidates", []):
            if item.get("returned"):
                print(f"  {item['path']} was part of a returned proposal ({', '.join(item['returned'])})")
        if any(not item["chosen"] for item in draft.preview.get("candidates", [])):
            print("  (add a file with --add FILE, leave one out with --leave FILE)")
        if not args.yes:
            print("Dry run: nothing written. Add --yes to write this proposal to the exchange.")
            return 0
        stored = proposals_mod.write_proposal(root, draft, exchange=_exchange_arg(args, root, data["project"]))
    except _PROPOSAL_ERRORS as exc:
        raise _proposal_error(exc) from exc
    print(f"Proposal {stored.id} written to {stored.directory}")
    return 0


def _route_words(route: dict) -> str:
    """A route_link's line in the command line's English (8.8)."""
    words = proposals_mod._route_summary(route) or {}
    said = ("Adds" if route.get("op") == "add" else "Removes") + \
        f" the line: #{words.get('child_number')} grew out of #{words.get('parent_number')}"
    return said + (f" ({route['note']})" if route.get("note") else "")


def cmd_proposals(args: argparse.Namespace) -> int:
    """`researchos proposals` (12.3): the PI's inbox, or a member's own
    proposals; `--show ID` for one, with -- for the PI -- exactly what
    approving would do."""
    opened = _open(args.path)
    root = opened.root
    try:
        ex = _exchange_arg(args, root, opened.project_id)
        if args.show:
            shown = proposals_mod.open_for(root, args.show, exchange=ex, member=args.from_member, lang="en")
            if args.json:
                print(json.dumps(shown, ensure_ascii=False, indent=2, sort_keys=True, default=str))
                return 0
            print(f"Proposal {shown['id']} ({shown['kind']}) by {shown['by']}: {shown['state']}")
            print(f"  {shown['sentence']}")
            for o in shown["outcomes"]:
                print(f"  {o.get('at')} {o.get('act')} by {o.get('by')}" + (f": {o['text']}" if o.get("text") else ""))
            if shown.get("route") and shown.get("changes") is None:  # the member's own line
                print(f"  {_route_words(shown['route'])}")
            for line in proposals_mod.parent_words(shown.get("attempt") or {}, "en") if shown.get("changes") is None else []:
                print(f"  {line}")
            changes = shown.get("changes")
            if changes is not None:
                attempt = changes.get("attempt")
                if attempt:
                    print(f"  attempt table ({attempt['file'] if 'file' in attempt else ''}) [{attempt['state']}]"
                          + (f": {attempt['reason']}" if attempt.get("reason") else ""))
                    for line in (attempt.get("diff") or "").splitlines():
                        print(f"    {line}")
                    for line in attempt.get("parent_words") or []:
                        print(f"  {line}")
                route = changes.get("route")
                if route:
                    print(f"  {route['words']} [{route['state']}]" + (f": {route['reason']}" if route.get("reason") else ""))
                variable = changes.get("variable")
                if variable:
                    print(f"  variable {variable['card']} -> v{variable.get('number')} [{variable['state']}]"
                          + (f": {variable['reason']}" if variable.get("reason") else ""))
                _print_files(changes["files"])
                for c in changes["conflicts"]:
                    print(f"  CONFLICT {c['path']}: {c['reason']}")
                for p in changes["problems"]:
                    print(f"  problem: {p}")
                print("  approvable" if changes["approvable"] else "  NOT approvable")
            return 0
        listed = proposals_mod.listing(root, exchange=ex)
    except _PROPOSAL_ERRORS as exc:
        raise _proposal_error(exc) from exc
    if args.json:
        print(json.dumps(listed, ensure_ascii=False, indent=2, sort_keys=True, default=str))
        return 0
    title = {"pi": "Inbox", "member": "My proposals"}.get(listed["role"])
    if title is None:
        print(f"No proposals to show ({listed['role']}: only the PI and a proposal's author see proposals)")
        return 0
    print(f"{title}: {len(listed['proposals'])}")
    for item in listed["proposals"]:
        print(f"  {item['id']} {item['state']:<10} {item['kind']:<16} {item['by']} {item.get('at')}  {item['sentence']}")
        last = item["outcomes"][-1] if item["outcomes"] else None
        if last is not None and last.get("text"):
            print(f"    {last.get('act')}: {last['text']}")
    for problem in listed["problems"]:
        print(f"  not read: {problem['path']} ({problem['kind']}: {problem['detail']})")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    """`researchos approve ID [--note N]` (12.3): only the PI's Research OS."""
    opened = _open(args.path)
    try:
        done = proposals_mod.approve(opened.root, args.proposal_id, note=args.note, via="cli",
                                     exchange=_exchange_arg(args, opened.root, opened.project_id),
                                     expected_id=opened.project_id, member=args.from_member)
    except _PROPOSAL_ERRORS as exc:
        raise _proposal_error(exc) from exc
    applied = done["applied"]
    print(f"Approved {done['proposal']}: decision {done['decision']['id']} in .researchos/decisions.toml "
          f"(by {done['decision'].get('by')}, approved_by {done['decision'].get('approved_by')})")
    for item in applied["files"]:
        print(f"  {item['action']:<8} {item['path']}")
    if applied["attempt"]:
        print(f"  attempt {applied['attempt']['node']} ({applied['attempt']['op']})")
    if applied["variable"]:
        print(f"  variable {applied['variable']['reference']} confirmed")
    if applied["evidence"] is not None:
        print(f"  evidence frozen: {len(applied['evidence'])} file(s)")
    _print_published_after(done.get("published"), opened.root)
    return 0


def _print_published_after(published: dict[str, Any] | None, root: Path) -> None:
    """12.3: approving publishes a snapshot; the approval stands whatever
    publishing said, so a failure is printed with the command to publish
    again -- the member sees nothing until a snapshot carries it."""
    if not published:
        return
    if published.get("published"):
        print(f"Published snapshot {published['seq']} for the members: " + ("signed" if published.get("signed") else "NOT signed"))
    elif published.get("reason") == "unchanged":
        print(f"Snapshot {published.get('seq')} already carries the official version")
    else:
        print(f"NOT published ({published.get('error')}: {published.get('detail')}); the approval is recorded -- "
              f"publish it with: researchos share publish {root}")


def cmd_return(args: argparse.Namespace) -> int:
    """`researchos return ID --reason R` (12.3): nothing is applied, ever."""
    opened = _open(args.path)
    try:
        outcome = proposals_mod.return_(opened.root, args.proposal_id, args.reason,
                                        exchange=_exchange_arg(args, opened.root, opened.project_id),
                                        expected_id=opened.project_id, member=args.from_member)
    except _PROPOSAL_ERRORS as exc:
        raise _proposal_error(exc) from exc
    print(f"Returned {args.proposal_id} ({outcome['id']}): {outcome['text']}")
    return 0


def cmd_discuss(args: argparse.Namespace) -> int:
    """`researchos discuss ID --text T` (12.3): a note to the member; the
    proposal stays open."""
    opened = _open(args.path)
    try:
        outcome = proposals_mod.discuss(opened.root, args.proposal_id, args.text,
                                        exchange=_exchange_arg(args, opened.root, opened.project_id),
                                        expected_id=opened.project_id, member=args.from_member)
    except _PROPOSAL_ERRORS as exc:
        raise _proposal_error(exc) from exc
    print(f"Discussion on {args.proposal_id} ({outcome['id']}) written; the proposal stays open")
    return 0


def cmd_exchange(args: argparse.Namespace) -> int:
    """`researchos exchange set DIR | show` (12.2): where this machine's
    proposals and outcomes travel for this project."""
    opened = _open(args.path)
    if opened.project_id is None:
        raise CliError(f"{opened.root} has no project identity")
    try:
        if args.exchange_command == "set":
            where = exchange_mod.configure(opened.project_id, args.directory, opened.root)
            print(f"Exchange for {opened.project_id}: {where}")
            return 0
        where = exchange_mod.configured(opened.project_id)
    except exchange_mod.ExchangeError as exc:
        raise CliError(str(exc)) from exc
    print(f"Exchange for {opened.project_id}: {where if where is not None else 'not set'}")
    return 0


def cmd_working_copy(args: argparse.Namespace) -> int:
    """`researchos working-copy OFFICIAL DEST` -- a member's working copy made
    from the official folder on this machine, with its base recorded (the
    single-machine stand-in for joining from a published snapshot)."""
    try:
        base = proposals_mod.start_working_copy(Path(args.official).resolve(), Path(args.dest), exchange=args.exchange)
    except _PROPOSAL_ERRORS as exc:
        raise _proposal_error(exc) from exc
    print(f"Working copy {base.working_copy} made from {base.snapshot[:19]} ({len(base.files)} file(s))")
    return 0


# -- the shared drive and the team (DESIGN.md 12.2, 12.3, 12.5) ------------------------------------


_SHARE_ERRORS = (share_mod.ShareError, exchange_mod.ExchangeError, records_situation.WriteRefused,
                 records_lock.ProjectLockError, signing.SigningError)


def _share_error(exc: Exception) -> CliError:
    details = getattr(exc, "details", None) or {}
    lines = [f"nothing written: {exc}"]
    for item in details.get("refused", []):
        lines.append(f"  refused snapshot {item['seq']}: {item['state']} -- "
                     f"{collaborate_mod.REFUSED_WORDS.get(item['state'], item['reason'])}")
    for item in details.get("files", []):
        lines.append(f"  {item}")
    return CliError("\n".join(lines))


def _print_refused(result: dict[str, Any]) -> None:
    for item in result.get("refused") or []:
        why = collaborate_mod.REFUSED_WORDS.get(item["state"], item["reason"])
        print(f"  REFUSED snapshot {item['seq']} ({item['state']}): {why} -- not used; the last good one is kept")
    for problem in result.get("problems") or []:
        print(f"  not read: {problem['path']} ({problem['kind']}: {problem['detail']})")
    for warning in result.get("warnings") or []:
        print(f"  warning: {warning}")


def cmd_team(args: argparse.Namespace) -> int:
    """`researchos team init | add | show` (12.1, 12.5): the project's members,
    in .researchos/team.toml -- written only by the PI's Research OS, and only
    ever appended to."""
    opened = _open(args.path)
    root = opened.root
    try:
        if args.team_command == "init":
            done = share_mod.team_init(root, expected_id=opened.project_id)
            print(f"Team file {done['path']} written with you ({done['pi']}) as the PI")
            if done["fingerprint"]:
                print(f"  your key: {done['fingerprint']} -- members compare this with what 'researchos share join' shows")
            for warning in done["warnings"]:
                print(f"  warning: {warning}")
            return 0
        if args.team_command == "add":
            try:
                key_text = Path(args.key).expanduser().read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise CliError(f"cannot read the key file {args.key}: {exc}") from exc
            done = share_mod.team_add(root, args.member_id, args.name, key_text, expected_id=opened.project_id)
            print(f"Added {done['member']} ({done['name']}) as a member; key {done['fingerprint']}")
            print(f"  {done['sentence_en']}")
            print(f"  {done['sentence']}")
            print("  publish so the member's copy carries the new team: researchos share publish")
            return 0
    except _SHARE_ERRORS as exc:
        raise _share_error(exc) from exc
    team = people.read_team(root)
    if args.json:
        print(json.dumps({"state": team.state, "members": [
            {**m.payload(), "fingerprint": signing.fingerprint(m.key) if m.key else None} for m in team.members]},
            ensure_ascii=False, indent=2))
        return 0
    if team.state != "present":
        print(f"Team: {team.state}" + (f" ({team.error})" if team.error else ""))
        return 0 if team.state == "absent" else 1
    for m in team.members:
        print(f"  {m.role:<6} {m.id:<30} {m.name}  " + (signing.fingerprint(m.key) if m.key else "(no key)"))
    return 0


def cmd_share(args: argparse.Namespace) -> int:
    """`researchos share set | show | publish | sync | join` (12.2, 12.3):
    the shared folder (公盘) -- the PI publishes signed snapshots, a member
    joins and syncs from them."""
    command = args.share_command
    try:
        if command == "join":
            done = share_mod.join(args.shared, args.folder, project=args.project, mine=args.mine)
            if args.json:
                print(json.dumps(done, ensure_ascii=False, indent=2, sort_keys=True))
                return 0
            print(f"Joined: working copy {done['working_copy']} at snapshot {done['seq']} ({done['files']} file(s))")
            if done["pi_fingerprint"]:
                print(f"  trusting the PI {done['pi']} with key {done['pi_fingerprint']} -- trusted on first use: "
                      f"compare this fingerprint with the PI's own (researchos me key) before relying on it")
            else:
                print(f"  the PI {done['pi']} has no key in team.toml: snapshots cannot be verified")
            if done["role"] != "member":
                print(f"  you are not a member in team.toml yet ({done['role']}); the PI adds you with 'researchos team add'")
            if done.get("mine"):
                print(f"  your own proposals folder: {done['mine']} (checked: you can write into it)")
            _print_refused(done)
            return 0
        if command == "set" and args.published is None and args.mine is None and args.area is None and args.directory is None:
            raise CliError("name the shared folder (researchos share set DIR), or a member's two folders "
                           "(--published DIR --mine DIR)")
        opened = _open(args.path)
        root = opened.root
        if command == "set":
            if opened.project_id is None:
                raise CliError(f"{root} has no project identity")
            if args.published is not None or args.mine is not None:
                if args.published is None or args.mine is None or args.directory is not None:
                    raise CliError("a member sets both folders: --published DIR (the 已发布 folder the PI shared) and "
                                   "--mine DIR (your own proposals folder)")
                done = share_mod.set_member_folders(root, args.published, args.mine)
                print(f"Published folder for {opened.project_id} on this machine: {done['published']} (snapshot "
                      f"{done['seq']} verified)")
                print(f"Your own proposals folder: {done['mine']} (checked: you can write into it)")
                return 0
            if args.directory is not None:
                where = exchange_mod.configure(opened.project_id, args.directory, root)
                print(f"Shared folder for {opened.project_id} on this machine: {where}")
            for given in args.area or []:
                member, sep, folder = given.partition("=")
                if not sep or not member or not folder:
                    raise CliError(f"--area takes MEMBER=DIR, got {given!r}")
                done = share_mod.set_member_area(root, member.strip(), folder.strip())
                print(f"{done['member']}'s proposal area on this machine: {done['area']}")
            return 0
        if command == "publish":
            done = share_mod.publish(root, note=args.note, expected_id=opened.project_id)
            if args.json:
                print(json.dumps(done, ensure_ascii=False, indent=2, sort_keys=True))
                return 0
            for area in done.get("areas") or []:
                if area.get("sentence_en"):
                    print(area["sentence_en"])
                    print(area["sentence"])
            if done.get("restored"):
                print(f"Wrote again {len(done['restored'])} damaged blob(s) removed by 'researchos share repair'")
            if not done["published"]:
                print(f"Nothing to publish: snapshot {done['seq']} is the official version as it is")
            else:
                print(f"Published snapshot {done['seq']}: {done['files']} file(s), {len(done['carried'])} carried as text, "
                      f"{len(done['blobs'])} as blobs; " + ("signed" if done["signed"] else "NOT signed"))
            _print_refused(done)
            return 0
        if command == "prune":
            done = share_mod.prune_blobs(root, args.before, apply=args.yes)
            if args.json:
                print(json.dumps(done, ensure_ascii=False, indent=2, sort_keys=True))
                return 0
            verb = "Removed" if args.yes else "Would remove"
            print(f"{verb} {len(done['prunable'])} blob(s), {done['bytes']} byte(s), that no snapshot from "
                  f"{done['before']} on references ({done['kept']} kept)")
            if not args.yes and done["prunable"]:
                print("Dry run: nothing removed. Add --yes to remove them.")
            return 0
        if command == "repair":
            done = share_mod.repair_blobs(root, apply=args.yes)
            if args.json:
                print(json.dumps(done, ensure_ascii=False, indent=2, sort_keys=True))
                return 0
            verb = "Removed" if args.yes else "Would remove"
            print(f"{verb} {len(done['damaged'])} damaged blob(s) from the shared folder's published blobs "
                  f"(nothing else is touched)")
            for item in done["damaged"]:
                seqs = ", ".join(str(n) for n in item["snapshots"]) or "none"
                again = ("the next publish writes it again" if item["restorable"]
                         else "the official version no longer holds this content: it cannot be written again")
                print(f"  {item['sha256']} (named by snapshot(s) {seqs}) -- {again}")
            for name in done["unchecked"]:
                print(f"  not checked (still in the cloud): {name}")
            if not args.yes and done["damaged"]:
                print("Dry run: nothing removed. Add --yes to remove them, then: researchos share publish")
            return 0
        if command == "sync":
            done = share_mod.sync(root)
            if args.json:
                print(json.dumps(done, ensure_ascii=False, indent=2, sort_keys=True))
                return 0
            if done.get("up_to_date"):
                print(f"Up to date at snapshot {done['seq']}")
            else:
                print(f"Synced snapshot {done['from']} -> {done['seq']}: {len(done['updated'])} updated, "
                      f"{len(done['removed'])} removed")
            for item in done["conflicts"]:
                print(f"  BOTH CHANGED {item['path']}: yours kept, the official one is {item['official_copy']}")
                for line in (item.get("diff") or "").splitlines()[:200]:
                    print(f"    {line}")
            for item in done["kept_yours"]:
                print(f"  kept yours: {item['path']} ({item['reason']})")
            if done.get("entries_sent"):
                print(f"  sent {done['entries_sent']} note(s)/link judgment(s) to your area for the PI (no approval needed)")
            for rel in done.get("notes_waiting") or []:
                print(f"  kept yours: {rel} (your entries have not reached the official version yet; it is updated "
                      f"once they have)")
            _print_refused(done)
            if not done.get("up_to_date"):
                _scan_after_sync(opened)
            return 0
        got = share_mod.status(root)
    except _SHARE_ERRORS as exc:
        raise _share_error(exc) from exc
    except _PROPOSAL_ERRORS as exc:
        raise _proposal_error(exc) from exc
    if args.json:
        print(json.dumps(got, ensure_ascii=False, indent=2, sort_keys=True, default=str))
        return 0
    print(f"Shared folder: {got['shared'] or 'not set'}" + ("" if got["reachable"] or not got["shared"] else " (cannot be reached now)"))
    if got.get("two_folders"):
        print(f"  two folders: published {got['layout']['published']}; yours "
              f"{(got.get('mine') or {}).get('path') or 'not set (researchos share set --published DIR --mine DIR)'}")
    for area in got.get("areas") or []:
        if area.get("set_apart"):
            print(f"  {area['member']}'s proposal area: {area['area']}" + ("" if area["reachable"] else " (cannot be reached now)"))
    print(f"  role: {got['role']}; ssh-keygen: {'yes' if got['ssh_keygen'] else 'MISSING'}")
    last = got.get("last_snapshot")
    if last:
        print(f"  last good snapshot: {last['seq']} ({last['at']}, {'signed' if last['signed'] else 'unsigned'}; "
              f"verification: {got.get('verification') or '-'})")
    if "pending_publish" in got:
        print(f"  not yet published: {got['pending_publish']['count']} file(s)")
    if "inbox" in got:
        print(f"  inbox: {got['inbox']} pending proposal(s)")
    if "joined" in got:
        joined = got["joined"]
        print(f"  joined at snapshot {joined['seq']}; PI {joined['pi']} {joined.get('pi_fingerprint') or '(no key)'}"
              + ("; a newer snapshot is available: researchos share sync" if got.get("behind") else ""))
    _print_refused(got)
    return 0


def cmd_collaborate(args: argparse.Namespace) -> int:
    """`researchos collaborate [STEP] [--as pi|member] [--yes] [PATH]` (V8
    phase 5): the steps of working with a team, in order -- the PI: me,
    init, team, share, publish, member; a member: me, key, join, added --
    each with whether it is done and the exact command that does it. With
    STEP, that one step: refused (with the reason) while an earlier one is
    not done; a dry run unless --yes, which runs exactly the command shown
    (`researchos.collaborate`)."""
    step, path = args.step, args.path
    known = set(collaborate_mod.PI_STEPS) | set(collaborate_mod.MEMBER_STEPS)
    if step is not None and step not in known:
        if path is not None:
            raise CliError(f"there is no step '{step}'; the PI's steps are {', '.join(collaborate_mod.PI_STEPS)} and "
                           f"a member's {', '.join(collaborate_mod.MEMBER_STEPS)}")
        path, step = step, None
    root = Path(path).expanduser().resolve() if path is not None else Path.cwd()
    inferred = collaborate_mod.infer_role(root)
    if args.role and inferred and args.role != inferred:
        raise CliError(f"{root}'s team file makes you {'the PI' if inferred == 'pi' else 'a member'} here, not "
                       f"{'the PI' if args.role == 'pi' else 'a member'}; nothing run")
    role = args.role or inferred
    if role is None:
        raise CliError("say which you are: --as pi (you lead the project and keep the official version) or --as member "
                       "(you join it from the shared folder)")
    values = collaborate_mod.Values(name=args.name, email=args.email, shared=args.shared, folder=args.folder,
                                    member=args.member, member_name=args.member_name, key=args.key, out=args.out,
                                    note=args.note)
    who = "the PI" if role == "pi" else "a member"
    flag = ["--as", role] if args.role else []
    try:
        steps = collaborate_mod.plan(role, root, values)
        if step is None:
            return _print_plan(steps, role, root, who, flag, as_json=args.json)
        chosen = collaborate_mod.check_order(steps, step)
    except collaborate_mod.StepRefused as exc:
        raise CliError(f"refused: {exc}") from exc
    number = [s.name for s in steps].index(step) + 1
    if chosen.done and step not in collaborate_mod.REPEATABLE:
        print(f"Step {number} ({step}) is done already: {chosen.detail}")
        return 0
    if chosen.argv is None:
        raise CliError(f"step '{step}' cannot be run here: {chosen.detail}")
    shown = collaborate_mod.command_line(chosen.argv)
    if args.yes and chosen.missing:
        raise CliError(f"step '{step}' needs {', '.join(chosen.missing)} to run: {shown}")
    print(f"Step {number} ({step}), as {who}: {chosen.title}")
    if not args.yes:
        print(f"Would run: {shown}")
        print("Dry run: nothing written. Add --yes to run it"
              + (f" (and give {', '.join(chosen.missing)})" if chosen.missing else "") + ".")
        return 0
    print(f"Running: {shown}", flush=True)
    code = main(chosen.argv)
    if code == 0:
        if chosen.after:
            print(f"Then: {chosen.after}")
        here = Path(values.folder).expanduser().resolve() if step == "join" and values.folder else root
        print("Where you are now: " + collaborate_mod.command_line(["collaborate", *flag, str(here)]))
    return code


def _print_plan(steps: list[collaborate_mod.Step], role: str, root: Path, who: str, flag: list[str], *,
                as_json: bool) -> int:
    upcoming = collaborate_mod.next_step(steps)
    daily = collaborate_mod.daily_loop(role, root)
    if as_json:
        print(json.dumps({"role": role, "root": str(root), "steps": [s.payload() for s in steps],
                          "next": upcoming.name if upcoming else None, "daily": daily},
                         ensure_ascii=False, indent=2))
        return 0
    print(f"Working with a team, as {who}, in {root}")
    for number, item in enumerate(steps, start=1):
        print(f"  [{'done' if item.done else '    '}] {number} {item.name:<8} {item.title}: {item.detail}")
        if not item.done and item.argv:
            print(f"             {collaborate_mod.command_line(item.argv)}")
    if upcoming is None:
        print("Set up. The daily loop:")
        for line in daily:
            print(f"  {line}")
        return 0
    if upcoming.argv is None:
        print(f"Next: {upcoming.title} -- {upcoming.detail}")
        return 0
    print(f"Next: {collaborate_mod.command_line(upcoming.argv)}")
    if upcoming.missing:
        print(f"  (fill in {', '.join(upcoming.missing)}; or give them to collaborate)")
    print("  or let collaborate run it: " + collaborate_mod.command_line(
        ["collaborate", upcoming.name, *flag, "--yes", str(root)]))
    return 0


def _scan_after_sync(opened: project_identity.Opened) -> None:
    """After a sync brought new files, the working copy's own index is
    scanned, so `status`, `query` and `trace` see them at once (the app's
    watcher does this by itself). A scan that cannot run is said, with the
    command; the sync stands."""
    try:
        with _write_guard(opened, human=False):
            conn = db.connect(_require_db(opened.root))
            try:
                ingest_pipeline.ingest_sources(conn, opened.root)
                ingest_pipeline.ingest_records(conn, opened.root)
            finally:
                conn.close()
    except Exception as exc:  # noqa: BLE001 -- contained: the files are synced either way
        print(f"  synced, but not scanned ({exc}): researchos ingest {opened.root}")


def cmd_inbox(args: argparse.Namespace) -> int:
    """`researchos inbox` (12.3): the PI's inbox from the members' areas of
    the shared folder, each proposal with its signature check -- an
    unverifiable one is shown, and cannot be approved."""
    opened = _open(args.path)
    try:
        listed = proposals_mod.listing(opened.root)
    except _PROPOSAL_ERRORS as exc:
        raise _proposal_error(exc) from exc
    if args.json:
        print(json.dumps(listed, ensure_ascii=False, indent=2, sort_keys=True, default=str))
        return 0
    if listed["role"] != "pi":
        print(f"No inbox: only the PI has one (you are {listed['role']}); 'researchos proposals' lists your own")
        return 0
    pending = [p for p in listed["proposals"] if p["state"] == "pending"]
    print(f"Inbox: {len(pending)} pending, {len(listed['proposals'])} in all")
    for item in listed["proposals"]:
        check = item.get("verification") or {}
        mark = {"verified": "signed", "no_key": "unsigned (no key listed)", "no_tool": "unverified (no ssh-keygen)",
                "unsigned": "UNSIGNED -- cannot be approved", "bad": "BAD SIGNATURE -- cannot be approved"}.get(check.get("state"), "-")
        print(f"  {item['id']} {item['state']:<10} {item['kind']:<16} {item['by']} [{mark}]  {item['sentence']}")
    for problem in listed["problems"]:
        print(f"  not read: {problem['path']} ({problem['kind']}: {problem['detail']})")
    return 0


def _format_lineage_entry(entry: dict[str, Any]) -> str:
    if entry.get("human"):
        # A human mapping (researchos.lineage): no call site to cite, and the
        # assertion's source is the mappings file the researcher wrote.
        return f"{entry['script']} (human mapping, .researchos/mappings.toml)"
    return f"{entry['script']}:{entry['line']} ({entry['callee']}){judgements.review_marker(entry)}"


def _format_lineage_human(report: dict[str, Any], orphans_only: bool) -> str:
    """Human-readable rendering of `researchos.lineage.build_lineage_report`'s four
    blocks. Per-block rule: a block is only headed and listed when it
    actually has content -- an empty block is not padded out with a "none
    found" line of its own, matching the task's own spec ("每块有内容才打").
    The one exception is when there is truly nothing to say at all (every
    block empty, or -- in --orphans mode -- the one block being shown is
    empty): that case is never left silent or rendered as a bare "OK". It
    states what was actually scanned and why the result is empty instead
    (DESIGN.md section 0: a missing finding is a normal outcome, but it must
    still be stated, never indistinguishable from "nothing was checked").
    """
    scanned = report["scanned"]
    scan_note = (
        f"scanned {scanned['scripts']} script node(s) -> {scanned['reads_edges']} reads "
        f"edge(s), {scanned['writes_edges']} writes edge(s) across {scanned['targets']} "
        f"dataset/figure target(s)"
    )
    lines = [f"Lineage report ({scan_note}):"]

    orphans = report["orphans"]
    if orphans:
        lines.append(f"\nOrphan inputs -- read but never written ({len(orphans)}):")
        for o in orphans:
            lines.append(f"  {o['path']}")
            for r in o["readers"]:
                lines.append(f"    <- read by {_format_lineage_entry(r)}")
    elif orphans_only:
        lines.append(
            f"\nNo orphan inputs found ({scan_note}): every dataset read here has at "
            "least one writer, or nothing here reads a dataset at all."
        )

    if orphans_only:
        return "\n".join(lines)

    chains = report["chains"]
    if chains:
        lines.append(f"\nLineage chains ({len(chains)}):")
        for c in chains:
            lines.append(f"  {c['path']}")
            for w in c["writers"]:
                lines.append(f"    <- written by {_format_lineage_entry(w)}")
            for r in c["readers"]:
                lines.append(f"    -> read by {_format_lineage_entry(r)}")

    broken = report["broken_links"]
    if broken:
        lines.append(f"\nBroken links -- evidence.missing=true ({len(broken)}):")
        for b in broken:
            lines.append(f"  {b['script']}:{b['line']} {b['kind']} {b['target']} (not found on disk)")

    duplicates = report["duplicates"]
    if duplicates:
        lines.append(f"\nDuplicate copies ({len(duplicates)}):")
        for d in duplicates:
            lines.append(f"  {d['path']} (this is the copy actually read)")
            for other in d["other_copies"]:
                lines.append(f"    also exists at: {other}")

    if not (orphans or chains or broken or duplicates):
        lines.append(
            f"\nNothing to report: {scan_note}, and none of it matched an orphan-input, "
            "lineage-chain, broken-link, or duplicate-copy pattern."
        )
    return "\n".join(lines)


def cmd_lineage(args: argparse.Namespace) -> int:
    """`researchos lineage` (task W4): the one new user-facing exit this round --
    a read-only, four-block report over task W2's dataflow graph
    (`script --reads/writes--> dataset|figure`). Requires `researchos ingest` to
    have already run, exactly like `query`/`trace`/`status` -- this command
    writes nothing and re-parses no source file (see researchos.lineage's module
    docstring for the four blocks and their scoping rules).

    `--orphans` narrows the human/`--json` output to block 1 alone. The
    exit code rule is the same either way: 1 if orphan inputs or broken
    links were found (the two blocks that represent an actual gap, not
    merely a fact worth knowing), 0 otherwise -- computed from the full
    report regardless of which blocks `--orphans` chooses to print, so the
    exit code never depends on which flag was used to ask.
    """
    project_root = _resolve_project_root(args.path)
    conn = db.connect(_require_db(project_root))
    try:
        report = lineage.build_lineage_report(conn, project_root)
    finally:
        conn.close()

    if args.json:
        payload = report if not args.orphans else {
            "scanned": report["scanned"], "orphans": report["orphans"],
        }
        print(json.dumps(payload, sort_keys=True))
    else:
        print(_format_lineage_human(report, args.orphans))

    return 1 if (report["orphans"] or report["broken_links"]) else 0


def cmd_serve(args: argparse.Namespace) -> int:
    """`researchos serve` (task V1): starts `researchos.webapp.server`'s local read-only
    web view over the graph, bound to 127.0.0.1 only. Requires `researchos init`
    (and normally `researchos ingest`/`researchos attempts`) to have already run, exactly
    like every other read-only subcommand -- `researchos.webapp.server.serve`
    raises `ProjectNotInitializedError` with the same message shape
    `_require_db` above gives, re-raised here as `CliError` so it prints and
    exits the same way any other missing-project error does rather than a
    raw traceback.

    V3 phase 1: the path is now optional. Given one, the project is
    recorded in the machine-managed registry (~/.researchos/projects.json,
    researchos.webapp.registry) as most recently served -- but only when it is
    actually an initialized project, so a typo'd or never-`researchos init`ed path
    fails with the usual clean error without leaving a junk registry entry
    behind. Given no path, the most recently served registry entry is
    served instead -- NOT the current directory: an implicit "." would be
    exactly the kind of guess DESIGN.md section 0 rules out, since a bare
    `researchos serve` is most naturally "reopen what I had open", not "serve
    wherever my shell happens to be". An empty registry starts the engine
    in the no-project state (DESIGN.md 10.1, 10.5), where the app offers
    「添加项目…」 -- on a new machine Research OS.app opens onto that, not an error.
    """
    if args.path is not None:
        project_root = Path(args.path).resolve()
        if not project_root.is_dir():
            raise CliError(f"{project_root} is not a directory")
        # The identity check first (DESIGN.md 9.4). A project that opens is
        # registered as most recently served; one in a situation to answer
        # is served in its blocked state, and nothing is written.
        served = webapp_server.served_for(project_root, register=True)
    else:
        entries = project_registry.load()
        if not entries:
            print(
                "No project yet: the project registry "
                f"(~/{names.HOME_DIRNAME}/{project_registry.REGISTRY_FILENAME}) is empty. "
                "Starting with no project -- add one in the app, or with 'researchos projects add <path>' "
                "(or 'researchos serve <path>').",
                file=sys.stderr,
            )
            try:
                webapp_server.serve(None, args.port, open_browser=not args.no_browser,
                                    served=webapp_server.no_project_served())
            except webapp_server.ApiError as exc:
                raise CliError(str(exc)) from exc
            return 0
        entry = entries[0]
        # A bare `researchos serve` (how Research OS.app starts the engine) whose most
        # recent entry is gone or no longer this project STARTS ANYWAY and
        # serves that entry's "missing" state, so the app can offer
        # 「选择新位置…」 instead of failing to start (9.4).
        served = webapp_server.served_for(
            Path(entry["path"]), expected_id=entry.get("id"), label=entry["label"], register=True,
        )
        project_root = served.root
    try:
        webapp_server.serve(project_root, args.port, open_browser=not args.no_browser, served=served)
    except webapp_server.ApiError as exc:
        raise CliError(str(exc)) from exc
    return 0


def _project_state_note(entry: dict[str, str]) -> str:
    """The one thing a listing must say about an entry beyond its path:
    whether it is usable, and if not, which of the two ways it is not.
    Same two questions `GET /api/projects` answers with its `available`/
    `initialized` pair (DESIGN.md section 8.10 rule 3) -- a directory that
    is gone is a dead entry worth removing; one that was merely never
    `researchos init`ed is fine and just needs initializing."""
    state = webapp_server.entry_state(entry)
    if state["missing"]:
        if entry.get("id") and Path(entry["path"]).is_dir():
            return (
                "  (this folder no longer carries the project -- it was moved; open it at its new "
                "path, or 'researchos projects remove' to drop this entry)"
            )
        return "  (directory missing -- 'researchos projects remove' to drop this entry)"
    if not state["initialized"]:
        return f"  (not initialized -- run 'researchos init {entry['path']}')"
    return ""


def cmd_projects_list(args: argparse.Namespace) -> int:
    """`researchos projects list`: the registry the app's project switcher shows,
    on the command line (DESIGN.md section 8.10 rule 3 asks for CLI
    parity, so the researcher never has to hand-edit
    ~/.researchos/projects.json). Most-recently-served first, exactly the order
    `GET /api/projects` returns."""
    entries = project_registry.load()
    if not entries:
        print(
            f"No registered projects yet ({project_registry.registry_path()} is empty or absent). "
            "'researchos serve <path>' registers one."
        )
        return 0
    print(f"Registered projects ({len(entries)}, most recently served first):")
    for entry in entries:
        project_id = f"  [{entry['id']}]" if entry.get("id") else "  [pre-V5]"
        print(f"  {entry['label']}  {entry['path']}{project_id}{_project_state_note(entry)}")
    return 0


def _print_add_preview(insp: addproject.Inspection) -> None:
    """`researchos projects add` without --yes: what the folder is and what adding
    it would do (DESIGN.md 10.2), in English."""
    print(f"Folder: {insp.root}")
    if insp.kind == addproject.NEW_FOLDER and insp.preview is not None:
        pv = insp.preview
        counts = pv.counts
        if pv.source == "git":
            print(f"A git repository: Research OS reads the files git tracks ({pv.tracked} tracked).")
            if pv.untracked:
                more = "+" if pv.untracked_truncated else ""
                print(f"  {pv.untracked}{more} more file(s) in the folder are not tracked by git and will not be scanned.")
            if pv.git_error:
                print(f"  git could not list the folder: {pv.git_error}")
        else:
            print("Not a git repository: Research OS reads the folder's files (hidden folders and caches skipped).")
        print(
            f"  To scan: {pv.to_scan} file(s) -- scripts (.py/.R/.Rmd/.do) {counts['scripts']}, data {counts['data']}, "
            f"drafts (.md/.tex/.bib) {counts['drafts']}, images {counts['images']}; other files (not read) {counts['other']}"
        )
        if pv.cloud and pv.cloud.get("blocked"):
            name = pv.cloud["name"]
            print(
                f"  {pv.dataless} of them are only in the {name} cloud and the {name} client is not running: "
                f"Research OS will not read them until {name} is opened and signed in."
            )
        elif pv.dataless:
            print(f"  {pv.dataless} of them are still in the cloud and are read once downloaded.")
        if pv.truncated:
            print(f"  Counting stopped at {addproject.ENTRY_CAP:,} entries; the folder holds more.")
        if pv.large:
            print(f"  This is a large folder (more than {addproject.LARGE_THRESHOLD:,} files to scan).")
        print(
            f"Adding writes .researchos/ inside the folder (project.toml, its snapshot in .researchos/backups/, a short README) and an index under "
            f"{paths.researchos_home() / paths.GRAPHS_DIRNAME}; nothing else in the folder is created or changed. "
            f"Then the first full scan runs."
        )
    elif insp.kind == addproject.PRE_V5:
        print("An Research OS project from before V5: adding registers and opens it; record judgments after 'researchos migrate'.")
        if insp.moves_graph:
            print(f"  Opening it moves its in-project .researchos/{paths.DB_FILENAME} to {paths.legacy_graph_dir(insp.root)} "
                  f"(copied and verified first) and writes .researchos/README saying where it went.")
    elif insp.kind == addproject.RESEARCHOS_PROJECT and insp.classification is not None:
        c = insp.classification
        if insp.can_add:
            print(f"An Research OS project (id {c.project_id}, {c.situation.value}): adding registers and opens it.")
        else:
            print(project_identity.describe_blocked(c))


def cmd_projects_add(args: argparse.Namespace) -> int:
    """`researchos projects add <path> [--label L] [--yes]` (DESIGN.md 10.5): the
    app's 「添加项目…」 on the command line. Without --yes it prints what the
    folder is and what adding would do, and writes nothing; with --yes it
    adds the folder and, for a folder Research OS has not seen, runs the first full
    scan in the foreground."""
    if args.path is None:
        raise CliError("name the folder to add: researchos projects add <path>")
    try:
        insp = addproject.inspect(args.path, deadline=None)
    except OSError as exc:
        raise CliError(f"could not look at {args.path}: {exc}") from exc
    if insp.kind == addproject.REFUSED:
        assert insp.refusal is not None
        raise CliError(f"{insp.refusal.detail} -- not added (refused: {insp.refusal.code}); nothing written")
    if insp.kind == addproject.ALREADY_REGISTERED:
        print(f"{insp.root} is already in the project list as {(insp.entry or {}).get('label')!r}; nothing written.")
        return 0
    _print_add_preview(insp)
    if insp.kind == addproject.RESEARCHOS_PROJECT and not insp.can_add:
        print("Nothing written; answer the question above first.", file=sys.stderr)
        return 1
    if not args.yes:
        print("Nothing written. Run again with --yes to add it.")
        return 0
    try:
        added = addproject.add(args.path, label=args.label, inspected=insp.token, deadline=None)
    except addproject.AddRefused as exc:
        raise CliError(f"not added: {exc.detail} ({exc.code}); nothing written") from exc
    except project_registry.LabelError as exc:
        raise CliError(f"not added: {exc}; nothing written") from exc
    print(f"Added {added.root} as {added.label!r}" + (f" (project {added.project_id})." if added.project_id else "."))
    if not added.needs_scan:
        return 0
    print("First full scan:")
    try:
        report = addproject.rescan(
            added.root, expected_id=added.project_id,
            progress=lambda step, n, m: print(f"[{n}/{m}] {addproject.STEP_NAMES.get(step, step)}"),
            echo=print,
        )
    except addproject.ScanRefused as exc:
        raise CliError(f"the project was added, but its scan did not run: {exc.detail}; run 'researchos ingest {added.root}'") from exc
    if report.unreadable_sources:
        print(f"Could not be read ({len(report.unreadable_sources)}): " + ", ".join(report.unreadable_sources))
    for note in report.cloud:
        print(
            f"  {len(note['files'])} of them are only in the {note['name']} cloud and the {note['name']} client "
            f"is not running; open it and sign in, then rescan."
        )
    if not report.ok:
        print(f"Warning: the scan did not finish ({report.error}); run 'researchos ingest {added.root}'.", file=sys.stderr)
        return 1
    print("Scan finished.")
    return 0


def cmd_projects_rename(args: argparse.Namespace) -> int:
    """`researchos projects rename <path|id> <label>` (DESIGN.md 10.4, 10.5): the
    registry label only -- never the folder. An id names its entry; a path
    is matched as stored first and resolved second, as `remove` does."""
    key = args.path
    if key is None:
        raise CliError("name the registered project (its path or id) to rename")
    try:
        entry = project_registry.rename(key, args.label)
        if entry is None and not records_lock.PROJECT_ID_RE.match(key):
            resolved = str(Path(key).expanduser().resolve())
            if resolved != key:
                entry = project_registry.rename(resolved, args.label)
    except project_registry.LabelError as exc:
        raise CliError(f"{exc}; nothing renamed") from exc
    if entry is None:
        raise CliError(
            f"{key!r} is not in the project registry ({project_registry.registry_path()}); 'researchos projects list' shows what is"
        )
    print(f"Renamed {entry['path']} to {entry['label']!r} (the folder itself is unchanged).")
    return 0


def _print_answered(result: project_identity.Answered) -> int:
    if result.answer == "fork":
        print(f"{result.root} is now project {result.identity.id}, forked from {result.previous_id}.")
        if result.git_tracked_identity:
            print(
                f"Note: {PROJECT_DIRNAME}/project.toml is tracked by git -- committing it carries the new "
                f"identity into whatever branch it is merged to."
            )
    elif result.answer == "claim":
        print(f"{result.root} is now the home of project {result.identity.id}; its index was rebuilt from this folder.")
        if result.replaced_index is not None:
            print(f"The previous index was kept at {result.replaced_index}.")
        print("The other folder carrying this id will be asked how to continue when it is next opened.")
    elif result.answer == "adopt":
        print(f"{result.root} keeps its records under a new identity: project {result.identity.id}.")
        print("A fresh index was built from its sources and records.")
    elif result.answer == "restore":
        print(f"Restored {PROJECT_DIRNAME}/project.toml from {result.restored_from}: project {result.identity.id}.")
        if result.blocked_after:
            print(f"The folder now has a question of its own:\n{result.blocked_after}")
            return 1
        print(f"Opened: {result.situation_after}.")
    else:
        print(f"{result.root} is now an independent project {result.identity.id}.")
        if result.moved_aside:
            print(f"Copied records moved into {PROJECT_DIRNAME}/backups/: {', '.join(result.moved_aside)}")
    print(f"Graph: {paths.index_dir(result.identity.id) / paths.DB_FILENAME}")
    if result.build_error:
        print(f"Warning: the scan of this folder did not finish ({result.build_error}); run 'researchos ingest'.",
              file=sys.stderr)
        return 1
    return 0


def _answer(args: argparse.Namespace, fn) -> int:
    root = Path(args.path).resolve()
    if not root.is_dir():
        raise CliError(f"{root} is not a directory")
    try:
        result = fn(root, echo=print)
    except (project_identity.AnswerRefused, project_identity.ProjectBlocked) as exc:
        raise CliError(str(exc)) from exc
    except (records_situation.WriteRefused, records_lock.ProjectLockError) as exc:
        raise CliError(str(exc)) from exc
    return _print_answered(result)


def cmd_project_fork(args: argparse.Namespace) -> int:
    """「作为独立分支继续」 (DESIGN.md 9.4): this copy becomes its own project
    (new id, `forked_from` the original), with its own index."""
    return _answer(args, project_identity.fork)


def cmd_project_claim(args: argparse.Namespace) -> int:
    """「这里才是原项目」 (9.4): this folder becomes the home of the id, and
    the index is rebuilt from it."""
    return _answer(args, project_identity.claim)


def cmd_project_adopt(args: argparse.Namespace) -> int:
    """「沿用这些记录，建立新身份」 (DESIGN.md 9.12): the identity file was
    lost; a new id, every record kept where it is, a fresh index built."""
    return _answer(args, project_identity.adopt)


def cmd_project_restore(args: argparse.Namespace) -> int:
    """Restore `.researchos/project.toml` from its newest snapshot (9.12), then the
    identity check as on any open."""
    return _answer(args, project_identity.restore)


def cmd_project_other(args: argparse.Namespace) -> int:
    """「这是另一个项目」 (9.4): a new id with no `forked_from`; copied record
    files are moved into `.researchos/backups/`."""
    return _answer(args, project_identity.other)


# -- variable definition cards (DESIGN.md 9.11) ---------------------------------------


@contextmanager
def _card_errors():
    try:
        yield
    except variables_mod.VariableError as exc:
        raise CliError(f"not written: {exc}") from exc
    except records_situation.WriteRefused as exc:
        raise CliError(str(exc)) from exc
    except records_lock.ProjectLockError as exc:
        raise CliError(f"could not take the project lock: {exc}") from exc


def _card_overview(root: Path, card_id: str | None = None) -> list[dict[str, Any]]:
    db_path = paths.graph_db_path(root)
    conn = db.connect(db_path) if db_path.exists() else None
    try:
        found = variable_cards.overview(conn, root)
    finally:
        if conn is not None:
            conn.close()
    if card_id is None:
        return found
    key = variables_mod.card_key(card_id)
    match = [c for c in found if variables_mod.card_key(c["id"]) == key]
    if not match:
        raise CliError(f"there is no variable card {card_id!r} in {variables_mod.variables_dir(root)}")
    return match[:1]


def _card_status_word(card: dict[str, Any]) -> str:
    trust = card.get("trust") or {}
    if card["state"] == "unreadable":
        return f"UNREADABLE ({card['reason']}: {card['detail']})"
    if trust.get("state") not in (None, "ok"):
        missing = len(trust.get("missing") or [])
        what = f"{missing} entr(y/ies) fewer than the index applied" if trust["state"] == "shrunk" else (trust.get("detail") or "")
        return f"FROZEN -- {trust.get('reason')}: {what}; writes to this card are refused"
    if card["abandoned"]:
        return f"abandoned at {card['abandoned'].get('at')} ({card['abandoned'].get('note')})"
    if card["in_use"] is not None:
        return f"v{card['in_use']} in use"
    return "no confirmed version"


def _print_card_line(card: dict[str, Any]) -> None:
    draft = f", draft v{card['draft']} open" if card["draft"] is not None else ""
    print(f"  {card['id']}: {_card_status_word(card)}{draft}")
    for q in card["questions"]:
        kind = q.get("kind")
        what = {"absent": "is gone", "unreadable": "can no longer be read"}.get(kind, "was changed")
        print(f"    ! v{q['version']}.toml {what} after it was confirmed ({q['message']}): "
              f"researchos variable answer {card['id']} {'|'.join(q['answers'])} --version {q['version']}")
        if kind == "absent":
            print("      new = put it back from its frozen copy, byte for byte (there is no edited text to keep)")
        elif kind == "unreadable":
            print("      new = keep these bytes, as they are, as the next draft and put the frozen text back")
    for problem in card["problems"]:
        print(f"    ! {problem}")
    for flag in card["dead_flags"]:
        print(f"    ! dead-variable list disagrees: {flag['direction']} ({flag['message']})")


def cmd_variable_list(args: argparse.Namespace) -> int:
    """`researchos variable list`: every card, the version in use, its draft, and
    what stands between Research OS and trusting it."""
    root = _open(args.path).root
    found = _card_overview(root)
    if args.json:
        print(json.dumps(found, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    print(f"Variable cards of {root}: {len(found)}")
    for card in found:
        _print_card_line(card)
    return 0


def _print_check(name: str, item: dict[str, Any]) -> None:
    extra = ", ".join(f"{k}={v}" for k, v in item.items() if k not in ("result", "dataset"))
    print(f"      {name}: {item.get('result', '')} {extra}".rstrip())


def cmd_variable_show(args: argparse.Namespace) -> int:
    """`researchos variable show <id>`: one card -- each version with its status,
    checks and observations, the history from the log, references."""
    root = _open(args.path).root
    card = _card_overview(root, args.id)[0]
    if args.json:
        print(json.dumps(card, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    _print_card_line(card)
    for v in card["versions"]:
        label = f" [{v['label']}]" if v["label"] else ""
        print(f"  v{v['version']} ({v['file']}): {v['status']}{label}, file {v['file_state']}")
        if v["error"]:
            print(f"    ! {v['error']}")
        if v.get("entry"):
            print(f"    confirmed at {v['confirmed_at']}; reference {v['reference']['label']}")
            print(f"    attested at that confirmation (the output then on disk was built with the text confirmed, "
                  f"in your words): {v['attested']}")
            if v["superseded_by"]:
                print(f"    superseded by v{v['superseded_by']['version']} from {v['superseded_by']['at']}")
            moment = "confirmation" if v.get("checked_act") != "corrected" else "correction"
            if moment == "correction":
                print(f"    the text in force is the correction made at {v['checked_at']}; the checks and observations "
                      f"below were taken then (your attestation above was not given about them)")
            checked = v.get("checked") or {}
            for name in ("script", "writes", "field"):
                if name in checked:
                    _print_check(name, checked[name])
            for item in checked.get("reads") or []:
                _print_check(f"reads {item.get('dataset')}", item)
            observed = v.get("observed") or {}
            if observed.get("output"):
                print(f"      observed output (on disk at the {moment} at {v['checked_at']}; not a claim it was built by "
                      f"this version): {json.dumps(observed['output'], ensure_ascii=False, sort_keys=True)}")
            if v.get("frozen_missing"):
                print(f"    ! {v['copy_missing']}: the frozen copy is not there; this version cannot be restored from it")
            if v.get("code_missing"):
                print(f"    ! {v['copy_missing']}: the code copy is not there; the script as it was cannot be shown")
        for note in v["upstream_notes"]:
            print(f"    note: {note}")
    _print_dispute(card)
    print("  history:")
    for e in card["history"]:
        what = f" v{e['version']}" if "version" in e else ""
        note = f" -- {e['note']}" if e.get("note") else ""
        kept = f" keeps {', '.join(e['keeps'])}" if e.get("keeps") else ""
        off = "" if e.get("in_force", True) else " [not in force: left out by a settlement]"
        print(f"    seq {e.get('seq')} {e.get('act')}{what} at {e.get('at')} via {e.get('via')} ({e.get('id')}){kept}{note}{off}")
    trust = card.get("trust") or {}
    if trust.get("state") == "shrunk":
        ids = ",".join(str(m["id"]) for m in trust["missing"])
        print(f"  log.toml has {len(trust['missing'])} fewer entr(y/ies) than the index applied:")
        for m in trust["missing"]:
            print(f"    {m.get('id')}: {m.get('act')}{' v' + str(m['version']) if 'version' in m else ''}")
        print(f"  answer: researchos variable answer {card['id']} file --missing {ids} {root}     (take the file as it is)")
        print(f"      or: researchos variable answer {card['id']} restore --missing {ids} {root}  (append the missing ones back)")
    return 0


def cmd_variable_new(args: argparse.Namespace) -> int:
    """`researchos variable new <id>`: a card directory, created exclusively, with
    v1.toml from the commented template. Research OS never writes the definition."""
    opened = _open(args.path)
    with _card_errors():
        path = variable_cards.new_card(opened.root, args.id, expected_id=opened.project_id)
    print(f"Created {path.relative_to(opened.root)} from the template. Write the definition in it, then: "
          f"researchos variable confirm {args.id}")
    return 0


def cmd_variable_revise(args: argparse.Namespace) -> int:
    """`researchos variable revise <id>`: the current confirmed version copied byte
    for byte to the next number, as the draft."""
    opened = _open(args.path)
    with _card_errors():
        path = variable_cards.revise(opened.root, args.id, expected_id=opened.project_id)
    print(f"Opened draft {path.relative_to(opened.root)} (a copy of the version in use). Edit it, then confirm it.")
    return 0


ATTEST_QUESTION = "Was the output file, as it stands now, built with THIS definition? [yes/no/unknown]: "


def _ask_attestation(root: Path, card_id: str) -> tuple[str, Any]:
    """9.11: the confirmation asks, once, whether the output as it stands
    was built with this definition; the answer is recorded as the
    researcher's. Returns (answer, the draft's content hash the answer was
    given about -- the confirmation is refused if the draft changes before
    it is written)."""
    expected: Any = variable_cards.ANY_CONTENT
    try:
        card = variables_mod.open_card(root, card_id)
    except variables_mod.VariableError:
        card = None
    if card is not None and card.readable and card.draft is not None:
        view = card.versions[card.draft]
        expected = view.content_hash
        output = ((view.content or {}).get("implementation") or {}).get("output") or "(no output file named)"
        print(f"Confirming {card.id} v{view.number}; output: {output}")
    if not sys.stdin.isatty():
        raise CliError(
            "the confirmation asks whether the output file as it stands was built with this definition, and "
            "records your answer as yours -- pass --attest yes|no|unknown (Research OS never answers it for you)"
        )
    while True:
        try:
            answer = input(ATTEST_QUESTION).strip().lower()
        except EOFError as exc:
            raise CliError("no answer was given; nothing confirmed") from exc
        if answer in variables_mod.ATTESTED:
            return answer, expected
        print("Please answer yes, no or unknown.")


def cmd_variable_confirm(args: argparse.Namespace) -> int:
    """`researchos variable confirm <id> [--attest yes|no|unknown]`: the draft
    becomes a definition results may rely on (snapshot first, entry last).
    Without --attest the attestation question is asked at the terminal
    (and the confirmation is tied to the draft it was asked about); off a
    terminal, --attest is required."""
    opened = _open(args.path)
    expected: Any = variable_cards.ANY_CONTENT
    attested = args.attest
    if attested is None:
        attested, expected = _ask_attestation(opened.root, args.id)
    with _card_errors():
        done = variable_cards.confirm(opened.root, args.id, attested=attested, expected_content=expected,
                                      expected_id=opened.project_id)
    entry = done.entry
    print(f"Confirmed {done.reference.variable} v{entry.get('version')} as {entry.id} (seq {entry.seq}); "
          f"reference {done.reference.label}")
    print(f"  attested: {entry.get('attested')} (your answer to: was the output as it stands built with this definition?)")
    checked = entry.get("checked") or {}
    for name in ("script", "writes", "field"):
        if name in checked:
            _print_check(name, dict(checked[name]))
    for item in checked.get("reads") or []:
        _print_check(f"reads {item.get('dataset')}", dict(item))
    return 0


def cmd_variable_answer(args: argparse.Namespace) -> int:
    """`researchos variable answer <id> new|correct [--version N]`: the question
    「v<n> 的定义在确认后被改动了」. `file|restore [--missing ids]`: the 9.3
    question when the card's log has fewer entries than the index applied."""
    opened = _open(args.path)
    with _card_errors():
        if args.answer in (variables_mod.ANSWER_NEW, variables_mod.ANSWER_CORRECT):
            if args.missing is not None:
                raise CliError("--missing goes with the answers file|restore")
            done = variable_cards.answer_edited(opened.root, args.id, args.answer, version=args.version,
                                                expected_id=opened.project_id)
            if done.answer == variables_mod.ANSWER_NEW and done.new_version is None:
                print(f"Put v{done.version}.toml back from its frozen copy, byte for byte (the file had vanished; "
                      f"there was no edited text to keep).")
            elif done.answer == variables_mod.ANSWER_NEW:
                print(f"Kept the file's bytes as they were as draft v{done.new_version}; v{done.version}.toml was put "
                      f"back from its frozen copy, byte for byte.")
            else:
                print(f"Recorded a correction of v{done.version} as {done.entry.id}: both wordings stay readable.")
            return 0
        shown = None if args.missing is None else [i.strip() for i in args.missing.split(",") if i.strip()]
        done = variable_cards.answer_shrunk(opened.root, args.id, args.answer, expected_missing=shown,
                                            expected_id=opened.project_id)
    if done.answer == "file":
        print(f"Took log.toml as it is: {len(done.missing)} entr(y/ies) dropped from the index's copy"
              + (f"; {len(done.appended)} version number(s) marked removed" if done.appended else ""))
    else:
        print(f"Appended {len(done.appended)} missing entr(y/ies) to log.toml (via = recovered)"
              + (f"; put back {len(done.restored_copies)} frozen cop(y/ies)" if done.restored_copies else ""))
    return 0


def _entry_line(e: dict[str, Any]) -> str:
    what = f" v{e['version']}" if "version" in e else ""
    content = f" content {str(e['content'])[7:15]}" if e.get("content") else ""
    attested = f" attested {e['attested']}" if e.get("attested") else ""
    return f"{e.get('id')}  seq {e.get('seq')} {e.get('act')}{what} at {e.get('at')} via {e.get('via')}{content}{attested}"


def _print_dispute(card: dict[str, Any]) -> None:
    """9.12: two histories in a card's log -- both, side by side in order,
    each disputed confirmation with its id, and the command that settles."""
    d = card.get("dispute")
    if not d:
        return
    print("  two histories of log.toml were merged; nothing is in force until you name what stands:")
    for e in d["common"]:
        print(f"    both:        {_entry_line(e)}")
    for i, branch in enumerate(d["branches"], 1):
        for e in branch:
            print(f"    history {i}:   {_entry_line(e)}")
        if not branch:
            print(f"    history {i}:   (no entry about this card)")
    for v in d["versions"]:
        print(f"    v{v['version']} is in dispute; the confirmations that may stand: {', '.join(v['candidates'])}")
    keeps = " ".join(f"--keep <v{v['version']} entry>" for v in d["versions"])
    print(f"  settle: researchos variable settle {card['id']} {keeps}   (the others stay in the history, not in force)")


def cmd_variable_settle(args: argparse.Namespace) -> int:
    """`researchos variable settle <id> --keep <entry id>...` (DESIGN.md 9.12): a
    card whose log holds two merged histories is settled by naming, for
    each version number in dispute, the one confirmation that stands. One
    `settled` entry is appended; the other entries stay in the history, not
    in force. Nothing is chosen by position or time."""
    opened = _open(args.path)
    if not args.keep:
        card = _card_overview(opened.root, args.id)[0]
        if card.get("dispute"):
            _print_dispute(card)
            raise CliError("name, with --keep, the confirmation that stands for each version in dispute")
    with _card_errors():
        done = variable_cards.settle(opened.root, args.id, args.keep, note=args.note, expected_id=opened.project_id)
    print(f"Settled {done.card.id}'s two histories as {done.entry.id} (seq {done.entry.seq}). What stands:")
    by_id = {e.id: e for e in done.card.entries}
    for entry_id in done.keeps:
        print(f"  v{by_id[entry_id].get('version')}: {entry_id}")
    if done.not_in_force:
        print(f"Kept in the history, not in force: {', '.join(done.not_in_force)}")
    for n in done.card.questions:
        print(f"  ! v{n}.toml does not hold the text that stands for v{n}: researchos variable answer {done.card.id} "
              f"new|correct --version {n}")
    return 0


def cmd_variable_abandon(args: argparse.Namespace) -> int:
    """`researchos variable abandon <id> --note "..."`: why the variable died."""
    opened = _open(args.path)
    with _card_errors():
        entry = variable_cards.abandon(opened.root, args.id, note=args.note, expected_id=opened.project_id)
    print(f"Recorded: {args.id} abandoned at {entry.at} ({entry.id}).")
    return 0


def cmd_variable_revive(args: argparse.Namespace) -> int:
    """`researchos variable revive <id> --note "..."`."""
    opened = _open(args.path)
    with _card_errors():
        entry = variable_cards.revive(opened.root, args.id, note=args.note, expected_id=opened.project_id)
    print(f"Recorded: {args.id} revived at {entry.at} ({entry.id}).")
    return 0


def cmd_projects_remove(args: argparse.Namespace) -> int:
    """`researchos projects remove <path>`: drop one registry entry. Removes a
    bookmark, never a project -- nothing on disk is touched and nothing is
    ingested again, which is why this needs no confirmation prompt.

    The stored paths are absolute and resolved (`registry.register`), so a
    literal match is tried first and the resolved spelling second: a user
    typing `researchos projects remove .` means the directory they are standing
    in. That convenience is deliberately NOT extended to
    `POST /api/projects/remove`, whose caller may be a web page and which
    therefore matches by string equality alone."""
    if args.path is None:
        raise CliError("name the registered project path to drop (as 'researchos projects list' prints it)")
    if not project_registry.remove(args.path):
        resolved = str(Path(args.path).expanduser().resolve())
        if resolved == args.path or not project_registry.remove(resolved):
            raise CliError(
                f"{args.path!r} is not in the project registry "
                f"({project_registry.registry_path()}); 'researchos projects list' shows what is"
            )
        print(f"Removed {resolved} from the project registry (nothing on disk was deleted).")
        return 0
    print(f"Removed {args.path} from the project registry (nothing on disk was deleted).")
    return 0


def cmd_app(args: argparse.Namespace) -> int:
    """`researchos app` (task V4 phase 3, DESIGN.md 8.9; first shipped in V3
    phase 4): build Research OS.app with researchos.webapp.macapp.build_app -- the native
    shell compiled by the system swiftc, or, when that toolchain is
    missing, the V3 launcher-script bundle plus a one-line notice saying
    so. Either replaces an existing Research OS.app in place. See that module's
    docstring for the bundle's exact shape and why the engine path is a
    runtime sidecar rather than baked into source.

    `--dir` builds into any directory on any platform (the tests use it,
    and it is how a user targets /Applications instead). Only the
    *default* location, ~/Applications, is macOS-gated: on another
    platform there is no `open`, no .app double-click, and no
    ~/Applications convention, so defaulting there would generate a
    bundle nothing can launch -- the error says to pass --dir instead of
    guessing at a per-platform equivalent. The hidden `--port` (default
    7357) exists for tests and trial builds only, so a trial app never
    probes or stops the researcher's own engine."""
    if args.dir is not None:
        target_dir = Path(args.dir).expanduser().resolve()
    else:
        if not macapp.is_macos():
            raise CliError(
                "the default install location (~/Applications) is only meaningful on "
                "macOS; on this platform pass an explicit --dir to choose where the "
                "bundle is generated"
            )
        target_dir = Path.home() / "Applications"
    try:
        result = macapp.build_app(target_dir, port=args.port, force=args.force)
    except macapp.MacAppError as exc:
        raise CliError(str(exc)) from exc
    for notice in result.notices:
        print(notice)
    if result.removed_legacy is not None:
        print(f"Removed the old {result.removed_legacy} (built by this tool before the rename to Research OS).")
    if result.up_to_date:
        # 9.12: an unchanged shell is not rebuilt -- a rebuilt, re-signed
        # app is a new application to macOS, which asks for Documents again.
        print(f"Research OS.app is up to date: {result.bundle} (built from these same sources; left untouched, so macOS "
              f"keeps the permissions it gave it). 'researchos app --force' rebuilds it anyway.")
        return 0
    print(f"Research OS.app written to {result.bundle}")
    if result.native:
        # 12.8: a new bundle identifier is a new application to macOS.
        print("macOS will ask once more whether Research OS may open your Documents folder: answer Allow.")
    if result.native:
        print("双击 Research OS.app 即可打开研究地图；引擎没在运行时会自动启动。")
    else:
        print("双击 Research OS.app 即可打开研究地图；服务已在运行时会直接打开页面。")
    return 0


def _tcp_port(value: str) -> int:
    """argparse `type=` for `researchos app --port`: an integer in 1..65535."""
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a port number: {value!r}") from None
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"port must be between 1 and 65535, got {port}")
    return port


def _import_mcp_server():
    """Lazy import for the optional 'mcp' extra (positioning ruling
    2026-07-22): researchos.mcp_server does `from mcp.server.fastmcp import
    FastMCP` at module scope, so importing it eagerly at this module's top
    would make every `researchos` subcommand require the mcp package. Only the
    `mcp` subcommand needs it. Raises ImportError if the extra isn't
    installed; the caller turns that into a clear, actionable message.
    """
    from researchos import mcp_server

    return mcp_server


def _positive_hops(value: str) -> int:
    """argparse `type=` for `--hops`: must be an integer >= 1.

    T-blocker fix (2026-07-26): query.trace()'s BFS loop is `range(1,
    max_hops + 1)`, so max_hops <= 0 makes it not execute at all -- the
    traversal never even looks at the start node's own directly-incident
    edges. The result is hops=[], which _format_trace_human then reports as
    "Node X exists but has no provenance edges recorded", a false statement
    for any node that actually has edges (confirmed via `researchos query` showing
    incoming/outgoing edges the same run). Rejecting <= 0 here, before the
    traversal ever runs, is preferred over rewording the empty-result
    message: with --hops >= 1 guaranteed, depth 1 always inspects every edge
    touching the start node regardless of the hop budget (db.EDGE_TYPES ==
    query.TRACE_EDGE_TYPES, so no edge type is untraceable), so an empty
    result is then always a truthful "zero edges touch this node".
    """
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid int value: {value!r}") from None
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"--hops must be >= 1, got {parsed}")
    return parsed

# -- researchos github (DESIGN.md 11.2, 11.3) ------------------------------------------------

_BLOCKERS_EN = {
    "not_git": "this folder is not a git repository; Research OS does not turn it into one",
    "detached": "HEAD is detached (not on a branch); check out a branch first",
    "merge": "a merge is in progress; finish or abort it first",
    "rebase": "a rebase is in progress; finish or abort it first",
    "no_branch": "the repository has no commit yet, so there is no branch to push",
    "not_fast_forward": "GitHub has commits this project does not have; sort it out in a terminal (Research OS never force-pushes)",
    "too_large": "the commits to push contain files over 100 MB, which GitHub refuses",
    "remote_not_github": "the remote is not on GitHub; Research OS pushes only to GitHub",
    "remote_ambiguous": "there are several GitHub remotes and Research OS does not choose; push from a terminal",
    "upstream_elsewhere": "the branch tracks a differently named branch; push from a terminal",
    "unsafe_config": "the repository's own git config names a program a push would run; push from a terminal",
    "gh_missing": "gh (GitHub's command-line tool) is not installed; install it, then run: gh auth login",
    "gh_logged_out": "gh is not logged in; run: gh auth login",
    "gh_unknown": "could not tell whether gh is logged in; run: gh auth status",
}


def _github_root(args: argparse.Namespace) -> tuple[Path, str | None]:
    root = Path(args.path).resolve()
    if not root.is_dir():
        raise CliError(f"{root} is not a directory")
    try:
        pid = paths._project_id_of(root)
    except paths.IdentityUnavailableError:
        pid = None
    return root, pid


def _print_github_state(st: dict[str, Any]) -> None:
    if not st.get("linked"):
        print("Not linked to GitHub" + ("" if st.get("git") else " (not a git repository)") + ".")
        return
    print(f"GitHub: {st['owner']}/{st['repo']} (remote {st['remote']}, {st['url']})")
    if st.get("ahead") is not None:
        print(f"  {st['ahead']} ahead, {st['behind']} behind {st.get('base')}"
              + (f" (fetched {st['fetched_at']})" if st.get("fetched_at") else " (never fetched)"))
    elif st.get("fetched_at") is None:
        print("  never fetched from GitHub")


def cmd_github_status(args: argparse.Namespace) -> int:
    """`researchos github status [path]`: the linked repository from local refs."""
    root, _ = _github_root(args)
    _print_github_state(github.state(root))
    return 0


def cmd_github_fetch(args: argparse.Namespace) -> int:
    """`researchos github fetch [path]`: `git fetch <remote>` -- never a pull."""
    root, pid = _github_root(args)
    try:
        st = github.fetch(root, pid)
    except github.GitHubError as exc:
        raise CliError(f"not fetched ({exc.code}): {exc.detail}") from exc
    _print_github_state(st)
    return 0


def _print_push_plan(plan: dict[str, Any]) -> None:
    print(f"Project: {plan['root']}")
    if not plan["git"]:
        ng = plan["not_git"]
        print("Not a git repository; Research OS does not turn it into one.")
        if ng["synced"]:
            print(f"  It is inside a folder {ng['synced']} syncs: sync services and git's internal files do not mix, "
                  "and a repository there can be damaged by the sync.")
            print("  Move the project to a folder that is not synced (Research OS recognises the move), then run 'git init' there.")
        else:
            print("  Run 'git init' in it and make a first commit, then push.")
        if ng["large_files"]:
            print(f"  GitHub refuses files over 100 MB; this folder has {len(ng['large_files'])}:")
            for f in ng["large_files"]:
                print(f"    {f['path']} ({f['size_text']})")
        return
    remote = plan["remote"]
    if plan["needs_repo"]:
        print(f"Branch: {plan['branch'] or '(none)'}; no remote yet -- a PRIVATE GitHub repository would be created "
              f"(suggested name: {plan['suggested_name']}) and added as 'origin'.")
    elif remote:
        upstream = " (its upstream will be set)" if plan["set_upstream"] else ""
        print(f"Branch: {plan['branch'] or '(none)'} -> {plan['target'] or remote['remote']} ({remote['url']}){upstream}")
    else:
        print(f"Branch: {plan['branch'] or '(none)'}")
    print(f"Commits GitHub does not have yet: {plan['commit_count']}")
    for c in plan["commits"]:
        print(f"  {c['short']} {c['subject']}")
    if plan["commits_truncated"]:
        print(f"  ... and {plan['commit_count'] - len(plan['commits'])} more")
    if plan["uncommitted"]:
        print(f"{plan['uncommitted']} other file(s) have changes that are not committed; a push does not carry them.")
    rec = plan["records"]
    if rec["changed"]:
        print(f"Human records (.researchos/): {len(rec['changed'])} file(s) not committed (--commit-records commits exactly these).")
    elif rec["ignored"]:
        print("Human records (.researchos/): ignored by git; a push does not carry them.")
    elif rec["committed"]:
        print("Human records (.researchos/): committed.")
    for b in plan["blockers"]:
        line = _BLOCKERS_EN.get(b["code"], b["code"])
        if b.get("files"):
            line += ": " + ", ".join(f"{f['path']} ({f['size_text']})" for f in b["files"])
        print(f"Refused ({b['code']}): {line}")
    if plan["nothing_to_push"] and not rec["changed"]:
        print("GitHub already has every commit; nothing to push.")


def cmd_github_push(args: argparse.Namespace) -> int:
    """`researchos github push [path] [--commit-records] [--create-repo NAME]
    [--yes]` (DESIGN.md 11.3): without --yes it prints the plan and writes
    nothing; with --yes it commits the records (if asked), creates the
    private repository (if asked and there is no remote), and pushes --
    never forced."""
    root, pid = _github_root(args)
    try:
        plan = githubpush.plan(root)
        _print_push_plan(plan)
        if plan["blockers"]:
            print("Nothing pushed; nothing written.", file=sys.stderr)
            return 1
        if plan["needs_repo"] and not args.create_repo:
            print(f"Nothing written. Run again with --create-repo {plan['suggested_name']} --yes "
                  "to create a private GitHub repository and push.")
            return 0 if not args.yes else 1
        if args.create_repo and not plan["needs_repo"]:
            raise CliError("--create-repo: this project already has a remote; nothing written")
        if args.create_repo and not githubpush.valid_repo_name(args.create_repo):
            raise CliError(f"--create-repo: {args.create_repo!r} is not a repository name "
                           "(ASCII letters, digits, '.', '_', '-'; at most 100); nothing written")
        if not args.yes:
            print("Nothing written. Run again with --yes to " + ("create the repository and " if plan["needs_repo"] else "")
                  + ("commit the records and " if args.commit_records else "") + "push.")
            return 0
        if plan["needs_repo"]:
            created = githubpush.create_repo(root, args.create_repo, plan["token"], pid)
            print(f"Created the private repository {created['repo']['owner']}/{created['repo']['repo']} ({created['repo']['url']}).")
            plan = created["plan"]
        if args.commit_records and not plan["records"]["changed"]:
            print("The human records are already committed.")
        result = githubpush.push(root, plan["token"], pid, commit_records=args.commit_records)
    except githubpush.PushError as exc:
        raise CliError(f"not pushed ({exc.code}): {exc.detail}") from exc
    if result.get("records") and result["records"].get("committed"):
        print(f"Committed {len(result['records']['files'])} record file(s) as 'Research OS：人工记录'.")
    if result["pushed"]:
        print(f"Pushed {result['count']} commit(s) to {result['plan']['target']}.")
    else:
        print("GitHub already has every commit; nothing pushed.")
    return 0


def _citation_statuses(root: Path) -> dict[tuple[str, str], str]:
    """{(src, dst): status} of the index's `citations` links, read-only;
    empty when the folder has no index here."""
    try:
        db_path = paths.graph_db_path(root)
    except paths.GraphMigrationError:
        return {}
    if not db_path.exists():
        return {}
    import sqlite3  # noqa: PLC0415 -- leaf use

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = conn.execute("SELECT src, dst, status FROM edges WHERE extractor = 'citations'").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return {}
    return {(src, dst): status for src, dst, status in rows}


def cmd_citations(args: argparse.Namespace) -> int:
    """`researchos citations [path] [--json]` (DESIGN.md 11.4): per draft, what it
    cites and how each citation resolves -- read only, nothing written,
    the network not asked (online answers only from the cache).
    `researchos citations lookup [--on | --off]`: the machine setting for DOI
    lookups online (off by default; only the DOI is sent)."""
    if args.path == "lookup":
        if args.on or args.off:
            literature.set_doi_lookup(bool(args.on))
        state = "on" if literature.doi_lookup_enabled() else "off"
        print(f"DOI lookup online: {state} (only the DOI is sent, to api.crossref.org; nothing from the drafts). "
              f"Setting: {literature.settings_path()}")
        return 0
    if args.on or args.off:
        raise CliError("--on/--off belong to 'researchos citations lookup'")
    root = Path(args.path).resolve()
    if not root.is_dir():
        raise CliError(f"{root} is not a directory")
    try:
        files = git_ingest.list_source_files(root)
    except git_ingest.NotAGitRepositoryError:
        files = files_ingest.list_source_files(root)
    except git_ingest.GitIngestError as exc:
        raise CliError(f"cannot list the project's files: {exc}") from exc
    rep = citations_ingest.report(root, citations_ingest.drafts_of(files), statuses=_citation_statuses(root))
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    z = rep["zotero"]
    print(f"Zotero library: {z['status']} ({z['data_dir']}, {z['items']} item(s))" + (f" -- {z['reason']}" if z["reason"] else ""))
    print(f"DOI lookup online: {'on' if rep['lookup']['enabled'] else 'off'}")
    t = rep["totals"]
    print(f"{t['citations']} citation(s): {t['resolved']} resolved, {t['pending']} with candidates to confirm, "
          f"{t['unresolved']} not found"
          + (f", {t['unanchored']} before the first heading (not linked)" if t["unanchored"] else "")
          + (f"; {t['unreadable']} draft(s) unreadable" if t["unreadable"] else ""))
    for draft in rep["drafts"]:
        if not draft["readable"]:
            print(f"  {draft['file']}: not read")
            continue
        c = draft["counts"]
        if not c["citations"]:
            continue
        print(f"  {draft['file']}: {c['citations']} citation(s), {c['resolved']} resolved, {c['pending']} pending, "
              f"{c['unresolved']} not found" + (f", {c['unanchored']} not linked" if c["unanchored"] else "")
              + (f"; reference list: {draft['entries']} entries" if draft["reference_list"] or draft["entries"] else ""))
        for item in draft["citations"]:
            if item["state"] == "unresolved":
                print(f"      line {item['line']}: {item['text']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = PathParser(prog=names.COMMAND, description=f"{names.PRODUCT_NAME} command line.")
    # Global, precedes the subcommand (e.g. `researchos -v attempts --check`): every
    # extractor logs its skip/orphan/fallback reasons at INFO via the
    # standard `logging` module, but main() never called `basicConfig`, so
    # none of that was ever visible in normal use -- only this flag turns it
    # on, and only for this one invocation.
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="enable INFO-level diagnostic logging (skip reasons, orphan preservation, etc.)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "init",
        help=(
            "Initialize an Research OS project at a path: creates its identity file .researchos/project.toml, "
            "its graph under ~/.researchos/graphs/<id>/ (outside the project) and a .researchos/README saying so"
        ),
    )
    add_project_path(p)
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("ingest", help="Ingest git + LaTeX/.bib + MLflow sources into the graph")
    add_project_path(p)
    p.add_argument(
        "--mlruns", default=None,
        help="MLflow local FileStore dir (default: <path>/mlruns if present)",
    )
    p.add_argument(
        "--wandb", default=None, metavar="ENTITY/PROJECT",
        help=(
            "W&B entity/project to ingest, e.g. 'acme/my-project' "
            "(requires the WANDB_API_KEY env var; see researchos.ingest.wandb)"
        ),
    )
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("status", help="Show node/edge counts and the pending confirmation queue")
    add_project_path(p)
    p.add_argument(
        "--pending", action="store_true",
        help="also list each pending edge (src/dst/type/extractor/confidence/evidence) for 'researchos confirm'",
    )
    p.add_argument(
        "--limit", type=int, default=None, metavar="N",
        help="cap how many pending edges --pending prints (default: no cap); truncation is always stated, never silent",
    )
    p.set_defaults(func=cmd_status)

    p = sub.add_parser(
        "query",
        help=(
            "Show a node and its immediate (single-hop) incoming/outgoing edges with "
            "evidence; use 'trace' for multi-hop provenance"
        ),
    )
    p.add_argument("node_id", help="node id, e.g. figure:overview.png")
    add_project_path(p)
    p.set_defaults(func=cmd_query)

    p = sub.add_parser(
        "trace",
        help="Walk the multi-hop provenance chain from a node (see 'query' for single-hop)",
    )
    p.add_argument("node_id", help="node id, e.g. figure:overview.png")
    add_project_path(p)
    p.add_argument(
        "--hops", type=_positive_hops, default=4, help="max traversal depth (default: 4, must be >= 1)"
    )
    p.add_argument(
        "--json", action="store_true", help="output structured JSON instead of human-readable text"
    )
    p.add_argument(
        "--chain", action="store_true",
        help="the result's whole chain (DESIGN.md 12.7): data -> scripts -> node -> drafts, its attempts, the "
             "variable versions it used, and every break with its repair; NODE may be a project-relative path",
    )
    p.set_defaults(func=cmd_trace)

    p = sub.add_parser(
        "link",
        help="Append a hand-drawn mapping to .researchos/mappings.toml (repair a broken trace): "
             "'generates'/'writes' (补上来源), 'belongs_to' with TO = attempt:<table>#<n> (关联文件), or "
             "'derives_from' with FROM and TO both attempt:<table>#<n>: FROM grew out of TO (源自)",
    )
    p.add_argument("from_path", metavar="FROM",
                   help="project-relative path (script, data file or figure), or attempt:<table file>#<number> "
                        "for derives_from")
    p.add_argument("to_path", metavar="TO",
                   help="project-relative path, or attempt:<table file>#<number> for belongs_to and derives_from")
    p.add_argument("--type", required=True, choices=list(mappings_ingest.MAPPING_TYPES))
    p.add_argument("--note", default=None, help="optional note, in your words")
    p.add_argument("--remove", action="store_true", help="remove this mapping instead")
    add_project_path(p)
    p.set_defaults(func=cmd_link)

    p = sub.add_parser("me", help="Who I am on this machine (~/.researchos/me.toml): 'researchos me set|show'")
    me_sub = p.add_subparsers(dest="me_command", required=True)
    q = me_sub.add_parser("set", help="set your name and the e-mail of your shared-drive account (the id is the e-mail, lower-cased)")
    q.add_argument("--name", required=True)
    q.add_argument("--email", required=True)
    q.set_defaults(func=cmd_me)
    q = me_sub.add_parser("show", help="show who this machine's entries are written by")
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_me)
    q = me_sub.add_parser("key", help="print this machine's public signing key (made on first use) for the PI's 'team add'")
    q.add_argument("--out", default=None, metavar="FILE", help="write it to a new file instead")
    q.set_defaults(func=cmd_me)

    p = sub.add_parser("note", help="Notes on any node, in .researchos/notes.toml: 'researchos note add|list|withdraw'")
    note_sub = p.add_subparsers(dest="note_command", required=True)
    q = note_sub.add_parser("add", help="append a note on a node (a node id, or a project-relative path)")
    q.add_argument("node")
    q.add_argument("text")
    add_project_path(q)
    q.set_defaults(func=cmd_note)
    q = note_sub.add_parser("list", help="the notes on a node (every node's when NODE is omitted)")
    q.add_argument("node", nargs="?", default=None, help="a node id or a project-relative path (then the project as PATH or --path)")
    q.add_argument("--all", action="store_true", help="include withdrawn notes")
    q.add_argument("--json", action="store_true")
    add_project_path(q, default=None, help="project root (default: '.'); a lone folder given here is the project")
    q.set_defaults(func=cmd_note)
    q = note_sub.add_parser("withdraw", help="withdraw a note (appends a 'withdrawn' entry; the note itself is kept)")
    q.add_argument("note_id")
    add_project_path(q)
    q.set_defaults(func=cmd_note)

    p = sub.add_parser("history", help="Every version of a node, newest first (git commits, or the version store)")
    p.add_argument("node", help="a node id or a project-relative path; attempt:<table>#<n>; variable:<id>")
    p.add_argument("--json", action="store_true")
    add_project_path(p)
    p.set_defaults(func=cmd_history)

    p = sub.add_parser(
        "diff",
        help="What changed between two versions of a node: 'researchos diff NODE [A B] [PATH]' "
             "(default: the newest and the one before it)",
    )
    p.add_argument("node")
    p.add_argument("rest", nargs="*", metavar="A B PATH", help="two versions from 'researchos history', and/or the project root")
    p.add_argument("--path", dest="diff_path", default=None, metavar="PATH", help=FLAG_PATH_HELP)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser(
        "propose",
        help="Propose what this working copy changed (DESIGN.md 12.3): a dry run unless --yes",
    )
    p.add_argument("kind", choices=list(proposals_mod.KINDS))
    p.add_argument("--sentence", default=None,
                   help="what this proposal is, in a sentence of your own (a route_link may leave it out)")
    p.add_argument("--number", default=None, help="the attempt row, when more than one differs")
    p.add_argument("--result", default=None, help="verdict_change: the result (default: the row's result cell)")
    p.add_argument("--reason", default=None,
                   help="verdict_change, or attempt_new whose row is abandoned: why -- required when abandoning a route")
    p.add_argument("--card", default=None, help="variable_version: the card id")
    p.add_argument("--attested", default=None, choices=["yes", "no", "unknown"],
                   help="variable_version: was the output as it stands built with this definition")
    p.add_argument("--only", action="append", default=None, metavar="FILE", help="carry only these files (repeatable)")
    p.add_argument("--add", action="append", default=None, metavar="FILE",
                   help="also carry this file (repeatable; a verdict change carries its row only, a new attempt the "
                        "files linked to it)")
    p.add_argument("--leave", action="append", default=None, metavar="FILE", help="do not carry this file (repeatable)")
    p.add_argument("--parent", action="append", default=None, metavar="N",
                   help="attempt_new: an official attempt it grew out of (repeatable); route_link: the line's parent")
    p.add_argument("--op", default=None, choices=list(proposals_mod.ROUTE_OPS),
                   help="route_link: add or remove the line --child grew out of --parent")
    p.add_argument("--child", default=None, metavar="N", help="route_link: the attempt that grew out of --parent")
    p.add_argument("--note", default=None, help="route_link: a note on the line")
    p.add_argument("--supersedes", default=None, metavar="ID", help="a proposal of yours this one replaces")
    p.add_argument("--exchange", default=None, metavar="DIR", help="the exchange directory (default: the one set)")
    p.add_argument("--yes", action="store_true", help="write the proposal (otherwise a dry run)")
    p.add_argument("--json", action="store_true")
    add_project_path(p)
    p.set_defaults(func=cmd_propose)

    p = sub.add_parser("proposals", help="The PI's inbox, or a member's own proposals (12.3); --show ID for one")
    p.add_argument("--show", default=None, metavar="ID", help="one proposal; for the PI, exactly what approving would do")
    p.add_argument("--from", dest="from_member", default=None, metavar="MEMBER",
                   help="whose proposal, when the same id is in more than one member's area")
    p.add_argument("--exchange", default=None, metavar="DIR")
    p.add_argument("--json", action="store_true")
    add_project_path(p)
    p.set_defaults(func=cmd_proposals)

    p = sub.add_parser("approve", help="Approve a proposal into the official version (the PI only, 12.3)")
    p.add_argument("proposal_id", metavar="ID")
    p.add_argument("--note", default=None, help="your note, recorded with the decision")
    p.add_argument("--from", dest="from_member", default=None, metavar="MEMBER",
                   help="whose proposal, when the same id is in more than one member's area")
    p.add_argument("--exchange", default=None, metavar="DIR")
    add_project_path(p)
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("return", help="Return a proposal with the reason (the PI only); nothing is applied")
    p.add_argument("proposal_id", metavar="ID")
    p.add_argument("--reason", required=True)
    p.add_argument("--from", dest="from_member", default=None, metavar="MEMBER",
                   help="whose proposal, when the same id is in more than one member's area")
    p.add_argument("--exchange", default=None, metavar="DIR")
    add_project_path(p)
    p.set_defaults(func=cmd_return)

    p = sub.add_parser("discuss", help="A note to the member on a proposal (the PI only); it stays open")
    p.add_argument("proposal_id", metavar="ID")
    p.add_argument("--text", required=True)
    p.add_argument("--from", dest="from_member", default=None, metavar="MEMBER",
                   help="whose proposal, when the same id is in more than one member's area")
    p.add_argument("--exchange", default=None, metavar="DIR")
    add_project_path(p)
    p.set_defaults(func=cmd_discuss)

    p = sub.add_parser("exchange", help="Where this project's proposals travel on this machine: 'researchos exchange set DIR|show'")
    ex_sub = p.add_subparsers(dest="exchange_command", required=True)
    q = ex_sub.add_parser("set", help="set the exchange directory (never inside the project)")
    q.add_argument("directory", metavar="DIR")
    add_project_path(q)
    q.set_defaults(func=cmd_exchange)
    q = ex_sub.add_parser("show", help="show the exchange directory")
    add_project_path(q)
    q.set_defaults(func=cmd_exchange)

    p = sub.add_parser(
        "working-copy",
        help="Make a member's working copy from the official folder on this machine and record its base "
             "(single-machine stand-in for joining from a published snapshot)",
    )
    p.add_argument("official", metavar="OFFICIAL")
    p.add_argument("dest", metavar="DEST")
    p.add_argument("--exchange", default=None, metavar="DIR")
    p.set_defaults(func=cmd_working_copy)

    p = sub.add_parser("team", help="The project's members (.researchos/team.toml), kept by the PI: 'researchos team init|add|show'")
    team_sub = p.add_subparsers(dest="team_command", required=True)
    q = team_sub.add_parser("init", help="write team.toml with you as the PI and your public key (made on first use)")
    add_project_path(q)
    q.set_defaults(func=cmd_team)
    q = team_sub.add_parser("add", help="append a member with the public key they exported ('researchos me key')")
    q.add_argument("member_id", metavar="ID", help="the member's e-mail (their id)")
    q.add_argument("--name", required=True)
    q.add_argument("--key", required=True, metavar="FILE", help="the member's public key file")
    add_project_path(q)
    q.set_defaults(func=cmd_team)
    q = team_sub.add_parser("show", help="the members, with their key fingerprints")
    q.add_argument("--json", action="store_true")
    add_project_path(q)
    q.set_defaults(func=cmd_team)

    p = sub.add_parser("share", help="The shared folder (12.2): 'researchos share set|show|publish|sync|join|prune|repair'")
    share_sub = p.add_subparsers(dest="share_command", required=True)
    q = share_sub.add_parser("set", help="set this project's shared folder on this machine (never inside a project); "
                                         "a member on a personal OneDrive sets two: --published DIR --mine DIR")
    q.add_argument("directory", metavar="DIR", nargs="?", default=None)
    q.add_argument("--published", default=None, metavar="DIR",
                   help="a member: the 已发布 folder the PI shared with you (view only)")
    q.add_argument("--mine", default=None, metavar="DIR",
                   help="a member: your own proposals folder the PI shared with you (edit)")
    q.add_argument("--area", action="append", default=None, metavar="MEMBER=DIR",
                   help="the PI: where a member's proposal area is on this machine, when it is not under 提议/")
    add_project_path(q)
    q.set_defaults(func=cmd_share)
    q = share_sub.add_parser("show", help="role, shared folder, last snapshot, verification, what is pending")
    q.add_argument("--json", action="store_true")
    add_project_path(q)
    q.set_defaults(func=cmd_share)
    q = share_sub.add_parser("publish", help="publish the official version as a new signed snapshot (the PI)")
    q.add_argument("--note", default=None)
    q.add_argument("--json", action="store_true")
    add_project_path(q)
    q.set_defaults(func=cmd_share)
    q = share_sub.add_parser("sync", help="bring this working copy to the latest verified snapshot (never a merge)")
    q.add_argument("--json", action="store_true")
    add_project_path(q)
    q.set_defaults(func=cmd_share)
    q = share_sub.add_parser("join", help="make a working copy in a new folder from the shared folder's latest verified snapshot")
    q.add_argument("shared", metavar="SHARED")
    q.add_argument("folder", metavar="FOLDER")
    q.add_argument("--project", default=None, metavar="ID",
                   help="which project, when the shared folder holds snapshots of more than one")
    q.add_argument("--mine", default=None, metavar="DIR",
                   help="your own proposals folder, when it is a separate folder (SHARED is then the 已发布 folder)")
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_share)
    q = share_sub.add_parser("repair", help="list (and with --yes remove) published blobs whose content is damaged; "
                                            "the next publish writes them again (the PI, 12.12)")
    q.add_argument("--yes", action="store_true", help="remove them (without it: a dry run)")
    q.add_argument("--json", action="store_true")
    add_project_path(q)
    q.set_defaults(func=cmd_share)
    q = share_sub.add_parser("prune", help="list (and with --yes remove) published blobs no snapshot from DATE on "
                                           "references (the PI, 12.2)")
    q.add_argument("--before", required=True, metavar="YYYY-MM-DD")
    q.add_argument("--yes", action="store_true", help="remove them (without it: a dry run)")
    q.add_argument("--json", action="store_true")
    add_project_path(q)
    q.set_defaults(func=cmd_share)

    p = sub.add_parser(
        "collaborate",
        help="Set up working with a team, step by step: each step, whether it is done and the exact next command "
             "(the PI: me, init, team, share, publish, member; a member: me, key, join, added); STEP --yes runs one",
    )
    p.add_argument("step", nargs="?", default=None, metavar="STEP", help="one step to take (a dry run unless --yes)")
    p.add_argument("--as", dest="role", default=None, choices=list(collaborate_mod.ROLES),
                   help="pi or member (default: what this project's team file says you are)")
    p.add_argument("--name", default=None, help="me: your name")
    p.add_argument("--email", default=None, help="me: the e-mail of your shared-drive account")
    p.add_argument("--shared", default=None, metavar="DIR", help="share / join: the shared folder")
    p.add_argument("--folder", default=None, metavar="DIR", help="join: the new, empty folder for your working copy")
    p.add_argument("--member", default=None, metavar="EMAIL", help="member: the member's e-mail")
    p.add_argument("--member-name", dest="member_name", default=None, metavar="NAME", help="member: the member's name")
    p.add_argument("--key", default=None, metavar="FILE", help="member: the public key file the member sent")
    p.add_argument("--out", default=None, metavar="FILE", help="key: where to write your public key")
    p.add_argument("--note", default=None, help="publish: a note on the snapshot")
    p.add_argument("--yes", action="store_true", help="run the step (otherwise a dry run)")
    p.add_argument("--json", action="store_true")
    add_project_path(p, default=None, help="the project (the PI) or the working copy (a member); default: '.'")
    p.set_defaults(func=cmd_collaborate)

    p = sub.add_parser("inbox", help="The PI's inbox from the shared folder, with each proposal's signature check")
    p.add_argument("--json", action="store_true")
    add_project_path(p)
    p.set_defaults(func=cmd_inbox)

    p = sub.add_parser(
        "lineage",
        help=(
            "Read-only four-block report over the W2 dataflow graph: orphan inputs, "
            "lineage chains, broken (missing-on-disk) links, and duplicate-named copies"
        ),
    )
    add_project_path(p)
    p.add_argument(
        "--orphans", action="store_true",
        help="print only block 1 (data files read by a script but written by none)",
    )
    p.add_argument(
        "--json", action="store_true", help="output structured JSON instead of human-readable text"
    )
    p.set_defaults(func=cmd_lineage)

    p = sub.add_parser(
        "serve",
        help=(
            "Start a local read-only web view over the graph, bound to 127.0.0.1 only "
            "(task V1); prints the URL and opens a browser tab unless --no-browser is given"
        ),
    )
    add_project_path(p, default=None, help=(
        "project root, or give it as --path; also registered in ~/.researchos/projects.json as most "
        "recently served. Omit both to reopen the most recently served project instead"
    ))
    p.add_argument(
        "--port", type=int, default=8317, help="TCP port to bind on 127.0.0.1 (default: 8317)"
    )
    p.add_argument(
        "--no-browser", action="store_true", help="do not automatically open a browser tab"
    )
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser(
        "projects",
        help=(
            "The project list the app's project menu reads (~/.researchos/projects.json): "
            "'researchos projects list | add <path> | rename <path|id> <label> | remove <path>'"
        ),
    )
    projects_sub = p.add_subparsers(dest="projects_command", required=True)
    q = projects_sub.add_parser(
        "list", help="List registered projects, most recently served first, flagging dead entries",
    )
    q.set_defaults(func=cmd_projects_list)
    q = projects_sub.add_parser(
        "remove", help="Remove one entry from the registry (a bookmark only -- deletes nothing)",
    )
    add_project_path(q, default=None, help=(
        "the registered project path to drop (as 'researchos projects list' prints it); or give it as --path"
    ))
    q.set_defaults(func=cmd_projects_remove)
    q = projects_sub.add_parser(
        "add",
        help="Add a folder as a project: without --yes, show what Research OS would read and write (writes nothing)",
    )
    add_project_path(q, default=None, help="the folder to add; or give it as --path")
    q.add_argument("--label", default=None, help="display name (default: the folder's name)")
    q.add_argument("--yes", action="store_true", help="add it, then run the first full scan")
    q.set_defaults(func=cmd_projects_add)
    q = projects_sub.add_parser(
        "rename", help="Change a registered project's display name (the folder is never renamed)",
    )
    add_project_path(q, default=None, help="the registered project's path (as 'researchos projects list' prints it) or id; or give it as --path")
    q.add_argument("label", help="the new display name")
    q.set_defaults(func=cmd_projects_rename)

    p = sub.add_parser(
        "project",
        help=(
            "Answer what Research OS asks when a project folder is a copy, its original cannot be "
            "checked, or its identity file was lost (DESIGN.md 9.4, 9.12): "
            "'researchos project fork|claim|other|adopt|restore [path]'"
        ),
    )
    project_sub = p.add_subparsers(dest="project_command", required=True)
    for name, func, text in (
        ("fork", cmd_project_fork, "continue this copy as an independent branch: new id, forked_from the original"),
        ("claim", cmd_project_claim, "this folder is the original: it becomes the id's home and the index is rebuilt from it"),
        ("other", cmd_project_other, "this is another project that received a copy of .researchos/: new id, copied records moved to .researchos/backups/"),
        ("adopt", cmd_project_adopt, "the identity file was lost: keep every record here under a new id and build a fresh index from them"),
        ("restore", cmd_project_restore, "the identity file was lost: put .researchos/project.toml back from its newest snapshot in .researchos/backups/"),
    ):
        q = project_sub.add_parser(name, help=text)
        add_project_path(q)
        q.set_defaults(func=func)

    p = sub.add_parser(
        "github",
        help=(
            "The project's GitHub repository (DESIGN.md 11.2, 11.3): "
            "'researchos github status | fetch | push [--commit-records] [--create-repo NAME] [--yes]'"
        ),
    )
    github_sub = p.add_subparsers(dest="github_command", required=True)
    q = github_sub.add_parser("status", help="the linked GitHub repository and how far ahead/behind, from local refs (no network)")
    add_project_path(q)
    q.set_defaults(func=cmd_github_status)
    q = github_sub.add_parser("fetch", help="'git fetch' the linked GitHub remote (never a pull or a merge)")
    add_project_path(q)
    q.set_defaults(func=cmd_github_fetch)
    q = github_sub.add_parser(
        "push", help="show what a push to GitHub would do; with --yes, push it (never forced)",
    )
    add_project_path(q)
    q.add_argument("--commit-records", action="store_true", help="first commit exactly the changed .researchos/ record files ('Research OS：人工记录')")
    q.add_argument("--create-repo", metavar="NAME", default=None, help="no remote yet: create a PRIVATE GitHub repository NAME with gh, as 'origin'")
    q.add_argument("--yes", action="store_true", help="do it; without --yes nothing is written")
    q.set_defaults(func=cmd_github_push)

    p = sub.add_parser(
        "citations",
        help=(
            "The drafts' citations (DESIGN.md 11.4): per draft, what is cited and how it resolves "
            "(read only); 'researchos citations lookup --on|--off' sets the DOI lookup online (off by default)"
        ),
    )
    add_project_path(p)
    p.add_argument("--json", action="store_true", help="print the whole report as JSON")
    onoff = p.add_mutually_exclusive_group()
    onoff.add_argument("--on", action="store_true", help="with 'lookup': look DOIs up online (only the DOI is sent)")
    onoff.add_argument("--off", action="store_true", help="with 'lookup': never look DOIs up online")
    p.set_defaults(func=cmd_citations)

    p = sub.add_parser(
        "app",
        help=(
            "Build Research OS.app, the native macOS window around the web app (compiled with "
            "the system swiftc; falls back to the browser launcher bundle without it). "
            "Starts 'researchos serve' itself when no engine is running (default: "
            "~/Applications, macOS only; --dir works anywhere)"
        ),
    )
    p.add_argument(
        "--dir", default=None, metavar="DIR",
        help=(
            "directory to generate Research OS.app into instead of ~/Applications "
            "(e.g. /Applications); works on any platform"
        ),
    )
    # Hidden: for tests and trial builds, never for the researcher's own
    # install (one fixed port is how every app finds the one engine).
    p.add_argument("--port", type=_tcp_port, default=macapp.DEFAULT_PORT, help=argparse.SUPPRESS)
    p.add_argument(
        "--force", action="store_true",
        help="rebuild Research OS.app even when it is up to date (macOS may then ask again for the Documents folder)",
    )
    p.set_defaults(func=cmd_app)

    p = sub.add_parser(
        "attempts",
        help=(
            "Ingest a hand-maintained attempt timeline via .researchos/attempts.toml (config-gated, "
            "never guessed); lists registered attempts, or runs consistency checks with --check"
        ),
    )
    add_project_path(p)
    p.add_argument(
        "--check", action="store_true",
        help=(
            "run the three deterministic consistency checks (broken step references, stale "
            "verdicts, revived dead variables) instead of listing -- exits 1 if any check "
            "reports a finding"
        ),
    )
    p.set_defaults(func=cmd_attempts)

    p = sub.add_parser(
        "mappings",
        help=(
            "Ingest hand-drawn links from .researchos/mappings.toml (reads | writes | generates) as "
            "confirmed edges; prints counts and any refused entries"
        ),
    )
    add_project_path(p)
    p.set_defaults(func=cmd_mappings)

    p = sub.add_parser(
        "confirm",
        help="Record a human verdict on one machine link in .researchos/judgements.toml, then apply it",
    )
    p.add_argument("src", nargs="?", default=None, help="edge src node id, e.g. claim:paper.tex#abc123")
    p.add_argument("dst", nargs="?", default=None, help="edge dst node id, e.g. experiment:run_a")
    p.add_argument("type", nargs="?", default=None, help="edge type, e.g. backed_by")
    p.add_argument("extractor", nargs="?", default=None, help="edge extractor, e.g. claims")
    p.add_argument(
        "--status", required=True, choices=["confirmed", "rejected", "withdrawn", "undone"],
        help="the act: confirmed / rejected; withdrawn (the machine's status again); undone (takes back the last act)",
    )
    p.add_argument("--note", default=None, help="optional note stored with the judgment")
    p.add_argument(
        "--index", type=int, default=None, metavar="N",
        help=(
            "alternative to the 4 positional args: 1-based position in the --from-status queue, "
            "re-queried/re-sorted by THIS invocation in the same order as 'status --pending'. Not "
            "a stable id -- another confirm or ingest run can shift what index N means"
        ),
    )
    p.add_argument(
        "--from-status", default="pending", choices=sorted(db.EDGE_STATUSES),
        help="status to select --index from (default: pending)",
    )
    add_project_path(p, help=(
        "project root (default: '.'), after the four link args -- or, with --index, the one "
        "positional; or give it as --path"
    ))
    p.set_defaults(func=cmd_confirm)

    p = sub.add_parser(
        "review",
        help="List judgments not applied: under review (with reason, old verdict, basis then/now, candidates), "
             "in conflict, or held because their source could not be read",
    )
    p.add_argument("--json", action="store_true", help="print the list as JSON")
    add_project_path(p)
    p.set_defaults(func=cmd_review)

    p = sub.add_parser(
        "records",
        help="Inventory of this project's human records (.researchos/); --verify checks the index against them",
    )
    p.add_argument("--verify", action="store_true", help="check per link that the index's human state is what the record implies; exit 1 if not")
    p.add_argument(
        "--answer", choices=list(judgements.ANSWERS), default=None,
        help="when the judgment ledger has fewer entries than the index applied: 'file' takes the file as "
             "truth (以文件为准), 'restore' appends the missing entries back (把缺少的补回文件)",
    )
    p.add_argument(
        "--file", choices=["judgements", "notes", "decisions"], default="judgements",
        help="with --answer: which ledger the answer is for (default: judgements)",
    )
    p.add_argument("--clean", action="store_true", help="list kept copies nothing refers to (dry run)")
    p.add_argument("--yes", action="store_true", help="with --clean: remove them")
    p.add_argument(
        "--missing", default=None, metavar="ID[,ID...]",
        help="with --answer: the ids of the missing entries the question showed ('researchos records' prints "
             "them); if the file changed since, nothing is done and the question is shown again",
    )
    add_project_path(p)
    p.set_defaults(func=cmd_records)

    p = sub.add_parser(
        "variable",
        help="Variable definition cards in .researchos/variables/ (DESIGN.md 9.11): "
             "'researchos variable new|list|show|revise|confirm|answer|abandon|revive|settle'",
    )
    variable_sub = p.add_subparsers(dest="variable_command", required=True)
    q = variable_sub.add_parser("list", help="every card, the version in use, its draft and its problems")
    q.add_argument("--json", action="store_true", help="print the cards as JSON")
    add_project_path(q)
    q.set_defaults(func=cmd_variable_list)
    for name, func, text in (
        ("new", cmd_variable_new, "create .researchos/variables/<id>/v1.toml from the commented template"),
        ("show", cmd_variable_show, "one card: versions, checks, observations, history, references"),
        ("revise", cmd_variable_revise, "copy the version in use, byte for byte, to the next number as the draft"),
        ("confirm", cmd_variable_confirm, "confirm the draft: checks, copies, then the log entry"),
        ("abandon", cmd_variable_abandon, "record why the variable died (--note is required)"),
        ("revive", cmd_variable_revive, "record why an abandoned variable is back (--note is required)"),
        ("answer", cmd_variable_answer, "answer a card's question: new|correct (a confirmed version was edited), "
                                        "file|restore (its log has fewer entries than the index applied)"),
        ("settle", cmd_variable_settle, "settle two merged histories in a card's log by naming, with --keep, the "
                                        "confirmation that stands for each version in dispute"),
    ):
        q = variable_sub.add_parser(name, help=text)
        q.add_argument("id", help="the variable's id (its directory name; compared case-folded)")
        if name == "answer":
            q.add_argument("answer", choices=["new", "correct", "file", "restore"])
            q.add_argument("--version", type=int, default=None, help="with new|correct: which edited version")
            q.add_argument("--missing", default=None, metavar="ID[,ID...]",
                           help="with file|restore: the ids of the missing entries 'researchos variable show' printed")
        if name == "confirm":
            q.add_argument("--attest", choices=list(variables_mod.ATTESTED), default=None,
                           help="your answer: was the output file as it stands built with THIS definition? "
                                "Asked when omitted at a terminal; required otherwise (Research OS never answers for you)")
        if name in ("abandon", "revive"):
            q.add_argument("--note", required=True, help="the reason, in your words")
        if name == "settle":
            q.add_argument("--keep", action="append", default=[], metavar="ENTRY_ID",
                           help="an entry that stands (one per version in dispute; 'researchos variable show' lists them)")
            q.add_argument("--note", default=None, help="optional: why, in your words")
        if name == "show":
            q.add_argument("--json", action="store_true", help="print the card as JSON")
        add_project_path(q)
        q.set_defaults(func=func)

    p = sub.add_parser(
        "rebuild",
        help="Build a fresh index beside the current one, apply the record, compare per link, then swap "
             "(the previous index is kept one generation)",
    )
    add_project_path(p)
    p.set_defaults(func=cmd_rebuild)

    p = sub.add_parser(
        "migrate",
        help="Move the judgments of a pre-V5 index into .researchos/judgements.toml (explicit; --list shows every old index)",
    )
    p.add_argument("--list", action="store_true", help="list every un-retired pre-V5 index on this machine")
    p.add_argument("--yes", action="store_true", help="migrate (without it, only show what waits and how well it matches)")
    p.add_argument("--not-mine", action="store_true", help="this is not this project's index: leave it, and do not offer it again")
    p.add_argument("--from", dest="from_dir", default=None, metavar="DIR", help="migrate from this index directory (a stranded index)")
    p.add_argument("-v", "--verbose", action="store_true", help="print the scan's progress")
    add_project_path(p)
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser(
        "judge",
        help=(
            "optional semantic layer: annotate pending backed_by candidates via a local "
            "model (writes evidence.semantic_review only, status stays pending -- see "
            "researchos.semantic.judge)"
        ),
    )
    add_project_path(p)
    p.add_argument(
        "--limit", type=int, default=None, metavar="N",
        help="review at most N pending backed_by edges, in 'status --pending' order (default: all)",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="call the model and print what would be annotated, but write nothing to the database",
    )
    p.set_defaults(func=cmd_judge)

    # `mcp`'s own args (--path etc.) are parsed by researchos.mcp_server.main itself,
    # not by this parser -- see the argv[0] == "mcp" interception in main()
    # below, which also lazy-imports researchos.mcp_server (via _import_mcp_server)
    # so every other subcommand keeps working with the optional 'mcp' extra
    # uninstalled. Registered here only so it shows up in `researchos --help`'s
    # command list; its own --help is served by mcp_server's parser instead
    # (prog "researchos mcp"), which is why it takes no arguments here.
    sub.add_parser(
        "mcp",
        help=(
            "optional: expose the graph to MCP-capable clients (requires "
            "pip install \"rce[mcp]\"); args pass through, e.g. 'researchos mcp --path .'"
        ),
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    # DESIGN.md 12.8: the machine's `~/.researchos` becomes `~/.researchos` on the
    # first touch by any command, before anything reads or writes the home
    # (a project's `.researchos/` follows in the identity check, `researchos.project`).
    try:
        rename.ensure_home_renamed()
    except rename.RenameError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if argv and argv[0] == "mcp":
        try:
            mcp_server = _import_mcp_server()
        except ImportError as exc:
            print(
                "Error: the 'mcp' subcommand requires the optional 'mcp' extra, "
                "which is not installed (MCP is one of several optional exits, "
                "not a requirement to use researchos). Install it with: "
                'pip install "researchos[mcp]"\n'
                f"(underlying error: {exc})",
                file=sys.stderr,
            )
            return 1
        # Pass-through per T5's architecture: researchos.mcp_server.main does its own
        # argument parsing (e.g. --path), so forward the remainder untouched
        # rather than re-declaring the same options in this parser.
        return mcp_server.main(argv[1:])
    args = build_parser().parse_args(argv)
    if args.verbose:
        logging.basicConfig(level=logging.INFO)
    try:
        _settle_path(args)
        return args.func(args)
    except CliError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def legacy_main(argv: list[str] | None = None) -> int:
    """The `researchos` command, kept as an alias for one release (DESIGN.md 12.8):
    one line on stderr naming the new command, then exactly `main`."""
    print(names.LEGACY_COMMAND_NOTICE, file=sys.stderr)
    return main(argv)


if __name__ == "__main__":
    sys.exit(main())
