# Research OS

[![tests](https://github.com/LinyingL/research-context-engine/actions/workflows/tests.yml/badge.svg)](https://github.com/LinyingL/research-context-engine/actions/workflows/tests.yml)

A **local-first** provenance engine for research projects. It reads your
existing git repo, LaTeX/.bib sources, and W&B/MLflow run history, and builds
a queryable evidence graph that answers one question: *where did this result
come from?* No cloud, no accounts, no lock-in — everything lives in one
SQLite file on your own machine, at `~/.researchos/graphs/<id>/graph.db`. It sits
outside the project on purpose: a project in an iCloud- or Dropbox-synced
folder makes opening a database there block until the file provider hands it
back, and SQLite under a sync provider is a documented corruption path. Your
project's own `.researchos/` keeps only files you wrote (`attempts.toml`, backups),
and `researchos init` leaves a one-line `.researchos/README` saying where the graph went.
Set `RESEARCHOS_HOME` to put that state somewhere else (the old `RCE_HOME`
is still honoured while `RESEARCHOS_HOME` is unset).

**Formerly RCE.** The command was `rce`, the folders `.rce/` and `~/.rce`.
`rce` still works for one release (it prints `rce is now researchos` and runs
the same command). The first command or app start after upgrading renames
each project's `.rce/` to `.researchos/` and `~/.rce` to `~/.researchos` --
one rename on the same disk, then every file is read back and compared byte
for byte; on any failure the folder is renamed back and left exactly as it
was. A folder that has both names is left alone and reported. The macOS app
is now `Research OS.app` (a new bundle identifier, so macOS asks once more
for access to the Documents folder).

## Quick start

```bash
pip install -e .            # zero required third-party dependencies
researchos init /path/to/project   # creates the graph (outside the project; `researchos status` prints where)
researchos ingest /path/to/project [--mlruns DIR] [--wandb entity/project]
researchos trace figure:overview.png --path /path/to/project --hops 4
```

None of the above touch any AI client — `researchos` is a complete, useful tool on
its own from the command line.

- `researchos init <path>` — initialize the graph database for a project.
- `researchos ingest <path>` — scan git history, LaTeX/.bib sources, and
  (optionally) MLflow/W&B runs into the graph.
- `researchos query <node-id> [--path P]` — show one node and its immediate,
  single-hop edges, with evidence.
- `researchos trace <node-id> [--path P] [--hops N] [--json]` — walk the multi-hop
  provenance chain outward from a node (default 4 hops, must be >= 1);
  `--json` for scripting.
- `researchos lineage [<path>] [--orphans] [--json]` — read-only report over the
  data-lineage graph (`script --reads/writes--> dataset|figure`, built by
  `researchos ingest`): **orphan inputs** (a data file read by a script but written
  by none — "where did this come from?"), **lineage chains** (a file with
  both a writer and a reader, shown as `written by file:line` /
  `read by file:line`), **broken links** (a script reads/writes a file that
  isn't actually on disk), and **duplicate copies** (a file a script reads
  has other same-named copies elsewhere in the project — which one did it
  actually read?). Every block prints only when it has something to say; an
  entirely empty result states what was scanned and why, never a bare `OK`.
  `--orphans` narrows the report to the first block alone. Exits 1 if any
  orphan input or broken link was found, 0 otherwise (duplicates/chains are
  informational and never affect the exit code).
- `researchos projects list` / `researchos projects remove <path>` — inspect and prune the
  registry of known projects that the app's project switcher reads
  (`~/.researchos/projects.json`). `list` flags an entry whose directory is gone or
  which was never `researchos init`ed; `remove` drops one entry and deletes nothing
  on disk. Both exist so the registry never has to be hand-edited.
- `researchos status [--path P] [--pending] [--limit N]` — whole-graph node/edge
  counts, the graph file's actual location, and the pending confirmation
  queue's size; `--pending` also lists
  each pending edge (src/dst/type/extractor/confidence/evidence), so it can
  be reviewed and acted on without the optional `mcp` extra.
- `researchos confirm <src> <dst> <type> <extractor> --status confirmed|rejected`
  (or `researchos confirm --index N --status ...` against the queue `--pending`
  just showed) — confirm or reject one pending edge; the baseline
  counterpart to the optional MCP server's `rce_confirm_edge` tool.
- `researchos judge [--path P] [--limit N] [--dry-run]` — optional semantic layer:
  a local model reviews pending `backed_by` candidates and annotates each
  with its opinion (`evidence.semantic_review`). It never confirms or
  rejects anything — see "Design principles" below.
- `researchos attempts [<path>] [--path P] [--check] [-v]` — ingest a
  hand-maintained attempt-timeline table (a Markdown table logging every
  research path tried, one row per attempt) via `.researchos/attempts.toml`, then
  either list what is registered, or run three deterministic consistency
  checks with `--check`: broken step references, stale verdicts, and
  revived dead variables. Config-gated on purpose — with no
  `.researchos/attempts.toml` present it prints a copy-pasteable template and
  exits 1 rather than guessing which table in the project is the attempt
  timeline. See "`researchos attempts` configuration" below for the file format.

`--path` defaults to `.` for `query`/`trace`/`status`/`confirm`/`attempts`,
so they also work by `cd`-ing into the project root first and dropping
`--path` entirely; `attempts` (like `init`/`ingest`) also accepts the
project root as a plain positional argument instead. `lineage` follows the
`init`/`ingest` convention directly: a plain positional `path` (default `.`),
no separate `--path` flag.

A global `-v`/`--verbose` flag (placed before the subcommand, e.g. `researchos -v
attempts --check`) turns on INFO-level diagnostic logging — skip reasons,
orphan-node preservation, and similar detail that every extractor already
logs but which is otherwise invisible in normal use.

### `researchos attempts` configuration

`researchos attempts` never guesses which table in your project is the attempt
timeline — create `.researchos/attempts.toml` (relative to the project root):

```toml
# All top-level keys below MUST appear before the [columns] table -- TOML
# nests any bare key written after a [table] header into that table.
file = "00-project-map.md"      # markdown file, relative to the project root
heading = "Attempt timeline"    # heading right above the table (prefix match is enough)
steps_dir = "repro/steps"       # optional: numbered step-script dir, for step-ref linking

# Optional, gates the "revived dead variable" check:
dead_variables = ["entropy weighting", "lnRate config ratio"]
active_verdicts = ["✅", "🕒"]   # verdict markers that count as "alive"

# Optional, gates the "stale verdict" check's looser date parsing (a bare
# "MM-DD", a "<=MM-DD"/"≤MM-DD" upper bound, or an "MM-DD~DD" range) --
# leave unset and only a full "YYYY-MM-DD" date parses.
date_year = 2026

[columns]                       # markdown header text for each field, as YOU wrote it
id = "#"
date = "Date"
description = "Path"
variables = "Variables"
result = "Result"
verdict = "Verdict"
```

Running `researchos attempts` with no config prints this exact template (see
`researchos.ingest.attempts.SAMPLE_CONFIG`) and exits 1.

## Optional: MCP

`researchos mcp` starts an MCP (Model Context Protocol) stdio server exposing the
same graph — `researchos_trace`, `researchos_find`, `researchos_status`, `researchos_confirm_edge` — to
any MCP-capable client, including open-source and locally hosted-model
clients, not just one particular vendor's assistant. It is one of several
ways to consume the graph, not a requirement; install it separately with:

```bash
pip install "researchos[mcp]"
```

Everything above works without it.

## Design principles

- **Deterministic first.** Git/LaTeX/.bib/run-log parsing is plain code
  (AST, regex, standard APIs) with zero model calls. `Claim` nodes and
  `backed_by` candidate edges are already populated this way: a claim's
  printed number is matched against experiment metrics with no model
  involved, and every candidate is written `status=pending` for a human to
  confirm or reject — the extractor itself never does, and neither can the
  semantic layer below.
- **Humans own judgement.** A local semantic layer (`researchos judge`) is
  implemented: it reviews pending `backed_by` candidates and attaches a
  model's opinion to each as `evidence.semantic_review`. It is structurally
  forbidden from confirming or rejecting anything — it can only annotate;
  moving an edge to `confirmed`/`rejected` is a separate, human-only write
  path (`researchos confirm`). Confidence-scored `supports` edges are a planned
  extension on top of this same annotation-only layer; `supports` is still
  schema-only until then.
- **Every edge is evidence-carrying.** No edge exists without a recorded
  extractor, an evidence pointer (file:line / commit SHA / run ID), and a
  confidence score.
- **No edge is invented.** If the graph has no provenance for something, Research OS
  says so explicitly — it never guesses or fabricates a chain to look
  useful.

See `DESIGN.md` for the full product specification.
