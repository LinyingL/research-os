# Research OS command-line guide

[Back to the project overview](../README.md)

This guide accompanies **0.1.0 Demo**. It assumes `researchos` is installed;
see the [README](../README.md#try-the-demo) for installation.
Run `researchos --help` or `researchos <command> --help` for all options.

## Core commands

| Command | Purpose |
| --- | --- |
| `researchos init <path>` | Initialize a project's identity and derived graph. |
| `researchos ingest <path>` | Scan project sources and optional experiment logs. |
| `researchos status --path <path>` | Inspect graph counts, pending relationships, your role, and collaboration state. |
| `researchos query <node-id> --path <path>` | Show an object and its immediate relationships, with evidence. |
| `researchos trace <node-id> --path <path> --chain` | Follow a result's data, scripts, drafts, attempts, and variable links. |
| `researchos trace <node-id> --path <path> --hops 4 --json` | Walk the evidence graph as structured output. |
| `researchos lineage <path>` | Report orphan inputs, chains, broken links, and same-named file copies. |
| `researchos attempts <path> --check` | Read the configured attempt table and check its consistency. |
| `researchos projects list` | List registered projects and flag unavailable ones. |
| `researchos projects remove <path>` | Remove a registry entry without deleting project files. |
| `researchos records <path>` | List the project's records and retained material. |
| `researchos serve <path>` | Start the local browser interface. |

`query`, `trace`, `status`, and `confirm` default to the current directory
when `--path` is omitted. `attempts` accepts either a positional project
path or `--path`; `lineage` uses a positional path, defaulting to `.`.
The global `-v` flag enables diagnostic logging, for example:

```bash
researchos -v attempts /path/to/project --check
```

`lineage` exits 1 when it finds orphan inputs or broken links, and 0
otherwise; chains and duplicate names are informational.

## Experiment logs

```bash
researchos ingest /path/to/project --mlruns /path/to/mlruns
researchos ingest /path/to/project --wandb entity/project
```

These connectors read existing tracker records. They do not execute your
research scripts or prove that every artifact has a complete runtime
history. W&B access uses its optional integration and configured account.

## Configure the attempt timeline

Research OS reads a Markdown attempt table you already maintain. Create
`.researchos/attempts.toml` in the project to identify it explicitly:

```toml
# Put top-level keys before [columns].
file = "00-project-map.md"
heading = "Attempt timeline"
steps_dir = "repro/steps"       # optional, for links from step numbers

# Optional consistency-check settings:
dead_variables = ["entropy weighting", "lnRate config ratio"]
active_verdicts = ["✅", "🕒"]
date_year = 2026               # permits dates such as MM-DD

[columns]                     # the exact headers in your Markdown table
id = "#"
date = "Date"
description = "Path"
variables = "Variables"
result = "Result"
verdict = "Verdict"
```

Use stable, distinct labels in the ID column. With no configuration,
`researchos attempts` prints a template and exits 1 instead of guessing
which table to read. Without `date_year`, the date checks expect full
`YYYY-MM-DD` dates.

```bash
researchos attempts /path/to/project
researchos attempts /path/to/project --check
```

The checks cover broken step references, stale verdicts, and revived dead
variables. The last check requires the optional variable/verdict settings.

## Review extracted relationships

```bash
researchos status --path /path/to/project --pending
researchos confirm --path /path/to/project --index 1 --status confirmed
```

Use the index shown by the pending queue. A relationship can also be named
by its source, destination, type, and extractor:

```bash
researchos confirm <src> <dst> <type> <extractor> --path /path/to/project --status rejected
```

These are judgments about extracted relationships, distinct from the PI's
approval of a proposed project change.

## Work with a team

For the full setup, including public keys, shared-folder permissions,
joining, and conflict handling, use the [English installation and team
guide](install-and-join.en.md) or [中文指南](安装与加入团队.md).

```bash
researchos collaborate --as pi /path/to/project
researchos collaborate --as member /path/to/working-copy
researchos status --path /path/to/project
```

Choose the `collaborate` command for your role. It shows the setup steps,
their state, and the next command.
`researchos collaborate STEP /path/to/project --yes` runs one step; it
refuses a step whose prerequisites are missing.

### Member: sync, preview, propose

```bash
researchos share sync /path/to/working-copy
researchos propose attempt_new --sentence "Test the extended panel" /path/to/working-copy
```

The proposal command previews what it would send. Repeat it with `--yes`
to submit. Kinds are `attempt_new`, `verdict_change`, `variable_version`,
`files`, and `route_link`. Abandoning a route requires a result and reason.
Use `researchos propose --help` for each kind's arguments.

```bash
researchos proposals /path/to/working-copy
```

### PI: review, decide, publish

```bash
researchos inbox /path/to/official-project
researchos proposals --show <ID> /path/to/official-project
researchos approve <ID> --note "Use this version" /path/to/official-project
researchos return <ID> --reason "Explain the sample change" /path/to/official-project
researchos discuss <ID> --text "Which specification did you use?" /path/to/official-project
```

Approval applies and records the changes, then publishes a snapshot. A
return keeps the reason; a discussion leaves the proposal open. After
making your own changes as PI, publish them with:

```bash
researchos share publish /path/to/official-project
```

Shared snapshots live under `已发布`; each member's proposals live under
`提议/<member e-mail>`. Members need view access to the published folder
and edit access to their own proposal folder. On personal OneDrive these
can appear as two separate shortcuts; the team guides explain how to set
both paths. Keep shared content downloaded locally.

## Optional model annotations

```bash
researchos judge --path /path/to/project --dry-run
```

`judge` reviews pending claim-to-metric candidates using a configured
OpenAI-compatible model endpoint, local by default. It writes an opinion
into the evidence; it cannot confirm or reject a relationship. Only this
optional layer needs a model. A remote endpoint sends the relevant
review context to that endpoint; the core workflow needs no model.

## Optional MCP access

From a source checkout, in the same environment used to install Research OS:

```bash
python -m pip install -e ".[mcp]"
researchos mcp --path /path/to/project
```

The stdio server exposes `researchos_trace`, `researchos_find`,
`researchos_status`, and `researchos_confirm_edge` to MCP clients. It is
optional; the app and core CLI commands work without the MCP dependency.

## Compatibility with RCE

Research OS was previously called RCE. The `rce` command remains an alias
for one release and prints the new name. On first use, the migration
renames `.rce/` to `.researchos/` and `~/.rce` to `~/.researchos`, verifies
the files, and rolls the rename back on failure. If both folder names
exist, it stops and reports them rather than choosing one.

`RESEARCHOS_HOME` selects the location of machine-local state. The legacy
`RCE_HOME` is still honored when `RESEARCHOS_HOME` is unset. The new macOS
app identity may prompt once more for access to your Documents folder.
