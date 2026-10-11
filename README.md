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
  queue's size; then who you are, your role in the project, the shared
  folder (last snapshot, whether it verified, how far your copy is behind),
  the proposals waiting, and the notes and history kept (see "Working with a
  team" below); `--pending` also lists
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

## Working with a team

This section is for a PI who runs a project with one or more collaborators
and does not use git. Nothing here needs git, a server or an account: the
team shares one folder on OneDrive (or any folder a sync service keeps in
step), and Research OS on each person's computer reads and writes plain
files in it.

**Three places.**

- **The official version** is the project folder on the PI's computer.
  Only the PI's Research OS changes it. Nobody ever connects to the PI's
  computer: it is where approvals are applied, nothing more.
- **The shared folder** on OneDrive has two parts. `已发布` ("published")
  holds signed snapshots of the official version and the PI's answers to
  proposals; only the PI writes there. `提议/<member e-mail>` ("proposals")
  is each member's own corner, where their proposals go.
- **A working copy** is a member's own copy of the project, made from the
  latest snapshot. Members work there as they always have.

A member's change -- a new attempt in the attempt table, a changed verdict,
a new variable definition, a new or changed script or data file -- reaches
the official version only as a **proposal** the PI approves. Until then it
is nowhere in the official record. A project with no team (or only its PI)
has no approval step at all.

`researchos collaborate` walks either person through the setup below in
order: it shows each step, whether it is done, and the exact next command;
`researchos collaborate STEP --yes` runs that one step, and a step taken out
of order is refused with the reason. `researchos status` says at any time
who you are, your role, the state of the shared folder and what is waiting.

### What the PI does once

```bash
researchos me set --name "Li Lin" --email li@example.com   # the e-mail of your OneDrive account
researchos init ~/Projects/my-paper                         # if the project is not a Research OS project yet
researchos team init ~/Projects/my-paper                    # you, as the PI, with your signing key
researchos share set ~/OneDrive/my-paper-shared ~/Projects/my-paper
researchos share publish ~/Projects/my-paper                # the first signed snapshot
```

`team init` prints your key's fingerprint (`SHA256:...`). Tell it to each
member by phone or in person: they compare it with what they see when they
join. Then, for each member, once they have sent you their key file:

```bash
researchos team add wang@example.com --name "Wang" --key wang.pub ~/Projects/my-paper
researchos share publish ~/Projects/my-paper                # their copy now carries the team
```

**Sharing permissions in OneDrive -- these are what make "only the PI can
approve" a rule rather than a request.** In the shared folder, create
`提议/<member e-mail>` for each member (the e-mail exactly as they gave it to
`researchos me set`, lower case). Then share:

| Folder | With | Permission |
|---|---|---|
| the shared folder itself (and so `已发布`) | every member | **Can view** |
| `提议/<member e-mail>` | that member only | **Can edit** |

Members add the shared folder to their own OneDrive ("Add shortcut to My
files") so it syncs to their computer. On every computer, mark the shared
folder "Always keep on this device": Research OS never reads a file that is
still only in the cloud, and says so instead.

### What a member does once

```bash
researchos me set --name "Wang" --email wang@example.com
researchos me key --out wang.pub          # send this file and your e-mail to the PI
researchos share join ~/OneDrive/my-paper-shared ~/Projects/my-paper-copy
```

`share join` makes the working copy in a new, empty folder and prints the
PI's key fingerprint. Compare it with the one the PI told you; if they
differ, stop and ask. Once the PI has added you and published, run
`researchos share sync ~/Projects/my-paper-copy`.

### The daily loop

A member:

```bash
researchos share sync ~/Projects/my-paper-copy        # start from the latest official version
# ... work as usual: scripts, data, the attempt table, variable cards ...
researchos propose attempt_new --sentence "Big panel with 2025 data" ~/Projects/my-paper-copy
#   shows exactly what would be sent; add --yes to send it
researchos proposals ~/Projects/my-paper-copy         # pending, approved, or returned with the reason
```

The kinds are `attempt_new` (a new row in the attempt table), `verdict_change`
(abandoning a route needs `--result` and `--reason`), `variable_version` (a
new definition of a variable) and `files` (scripts and data alone).

The PI:

```bash
researchos inbox ~/Projects/my-paper                    # what waits for you
researchos proposals --show <ID> ~/Projects/my-paper    # exactly what approving would change
researchos approve <ID> --note "OK" ~/Projects/my-paper # applied, recorded, published
researchos return <ID> --reason "..." ~/Projects/my-paper
researchos discuss <ID> --text "..." ~/Projects/my-paper  # a note; the proposal stays open
researchos share publish ~/Projects/my-paper            # after changing the official version yourself
```

Every approved change is recorded with who proposed it and who approved
it, and an abandoned route keeps the result and the reason it was dropped.

### When both sides changed the same file

If the PI changed a file after the member's last sync and the member changed
it too, the PI is told which file, and the proposal cannot be approved; the
member syncs and proposes again. When the member syncs, their own version
is kept and the official one is put beside it as `<name>.official.<ext>`,
with the difference shown. Compare the two, keep what is right in the
original file, and delete the `.official` one.

### What Research OS never does

- **It never merges two people's edits of a file.** It shows both and lets
  a person decide.
- **It never edits anything in the shared folder.** Every snapshot,
  proposal and answer is written once under a new name and never changed;
  an unfinished one (no `ready.toml`) and a sync service's conflict copy are
  reported and never read.
- **It never contacts the PI's computer**, and working with a team opens
  no network connection of its own: the only link between the team is the
  shared folder, which OneDrive keeps in step.
- **It never accepts a snapshot it cannot verify.** Snapshots are signed with
  the PI's key (with `ssh-keygen`, part of macOS); a snapshot written by
  anyone else is refused, and the last good one is kept. Where `ssh-keygen`
  is missing, Research OS says so, and the sharing permissions above are the
  only protection.

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
