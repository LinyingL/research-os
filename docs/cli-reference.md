# Command-line reference

Research OS **0.3.0**.

For installation, see the [README](../README.md#install). Replace the
sample paths and file names below with your own. `researchos --help` lists
commands; `researchos <command> --help` lists their options.

## Scan a project

```bash
researchos init /path/to/project
researchos ingest /path/to/project
researchos status --path /path/to/project
```

`init` creates the project's records folder and local index. `ingest`
reads the project files. `status` shows what was indexed, relationships
waiting for review, and your team status.

To open the browser interface:

```bash
researchos serve /path/to/project
```

### Stata projects

The same scan reads `.do` files for data inputs, data outputs and exported
figures. It follows literal paths, resolvable macros and working-folder
changes. Paths that cannot be resolved are listed with a reason. When a
global appears to name a project folder, confirm the suggested folder in
the app before links depending on it are created.

Open a `.dta` file in the app to see its row count, variables, labels and
storage types. This reads the header, not the data rows. `.ado` files are
listed as scripts but are not scanned for file relationships in this version.
Research OS does not execute do-files or require Stata to scan them.

## Trace a result

Use the figure's path relative to the project root:

```bash
researchos trace figure:figures/result.png --path /path/to/project --chain
```

`--chain` shows the linked data, scripts, drafts, attempts, and variable
versions. Missing relationships are listed with ways to add them.

To inspect an object's immediate relationships, use `query`. For a graph
traversal as JSON, use `trace --json`:

```bash
researchos query figure:figures/result.png --path /path/to/project
researchos trace figure:figures/result.png --path /path/to/project --hops 4 --json
```

## Check data links

```bash
researchos lineage /path/to/project
```

The report lists inputs with no known producer, intermediate data links,
missing files, and files with duplicate names. It exits 1 for orphan inputs
or broken links, and 0 otherwise. Chains and duplicate names do not affect
the exit code.

## Read the attempt table

Create `.researchos/attempts.toml` inside the project. Set `file` to your
Markdown file, `heading` to the heading above the table, and `columns` to
its exact column names:

```toml
file = "00-project-map.md"
heading = "Attempt timeline"
steps_dir = "repro/steps"       # optional: scripts named by step number

# Optional consistency checks; keep these keys above [columns].
dead_variables = ["entropy weighting", "lnRate config ratio"]
active_verdicts = ["✅", "🕒"]
date_year = 2026               # needed for dates such as MM-DD

[columns]
id = "#"
date = "Date"
description = "Path"
variables = "Variables"
result = "Result"
verdict = "Verdict"
```

Give each attempt a unique ID and keep it when editing or sorting rows.
Without this configuration, `attempts` prints a template and exits 1.
Without `date_year`, date checks require `YYYY-MM-DD` dates.

```bash
researchos attempts /path/to/project
researchos attempts /path/to/project --check
```

`--check` reports missing step scripts, verdicts that may need updating,
and attempts that reuse abandoned variables. The last check requires
`dead_variables` and `active_verdicts`.

## Review an extracted relationship

List the pending relationships, then use the number shown in that list:

```bash
researchos status --path /path/to/project --pending
researchos confirm --path /path/to/project --index 1 --status confirmed
```

A relationship can also be identified by its source, destination, type,
and extractor:

```bash
researchos confirm <src> <dst> <type> <extractor> --path /path/to/project --status rejected
```

`confirm` records your judgment on a relationship. PI approval of a
project change uses `approve`, described below.

## Work with a team

For joining, keys, and folder permissions, see the
[English setup guide](install-and-join.en.md) or [中文指南](安装与加入团队.md).
To check your setup, choose the command for your role:

```bash
researchos collaborate --as pi /path/to/project
researchos collaborate --as member /path/to/working-copy
```

`collaborate` lists completed steps and the next command to run.
`researchos collaborate STEP /path/to/project --yes` runs a named step
once its prerequisites are met.

### Members: submit a change

Sync before editing. After changing your files, preview a proposal:

```bash
researchos share sync /path/to/working-copy
researchos propose files --sentence "Update the plotting script" --only scripts/plot.py /path/to/working-copy
```

Check the file list, then repeat the proposal command with `--yes`:

```bash
researchos propose files --sentence "Update the plotting script" --only scripts/plot.py /path/to/working-copy --yes
researchos proposals /path/to/working-copy
```

`--only` selects a file; repeat it to select more. Other proposal kinds are
`attempt_new`, `verdict_change`, `variable_version`, and `route_link`.
Abandoning an attempt requires a result and reason. Use
`researchos propose --help` for the arguments for each kind.

### PI: review and respond

```bash
researchos inbox /path/to/official-project
researchos proposals --show <ID> /path/to/official-project
```

Replace `<ID>` with the proposal's ID, then choose one action:

| Action | Command |
| --- | --- |
| Apply the changes and publish | `researchos approve <ID> --note "Use this version" /path/to/official-project` |
| Return for revision | `researchos return <ID> --reason "Explain the sample change" /path/to/official-project` |
| Leave a comment and keep it open | `researchos discuss <ID> --text "Which specification did you use?" /path/to/official-project` |

After editing the official project yourself, publish it:

```bash
researchos share publish /path/to/official-project
```

Members receive the accepted version on their next sync.

## Import experiment logs

```bash
researchos ingest /path/to/project --mlruns /path/to/mlruns
researchos ingest /path/to/project --wandb entity/project
```

MLflow records are read from the local directory. W&B access requires an
API key in `WANDB_API_KEY`. These commands import existing records;
they do not run your scripts.

## Optional: model-assisted drafts

This feature is in the app's New attempt form; it has no separate CLI
command. Start LM Studio's local server, load a model, then open Model
server in the form. Save the server address and model ID, choose up to five
text files from the project and click Generate draft. A server root such as
`http://127.0.0.1:1234` is accepted and expanded to `/v1` automatically.

Only empty description and result fields are filled. Each drafted field
names its source files, and unsupported numbers are rejected. Include
actual text logs or results to draft findings; a script alone does not
show that it ran. You review the draft before saving or proposing it.
Verdicts, reasons and approval decisions are not drafted.

The form shows where selected files will be sent. The default is this Mac;
another configured server receives those files. The CLI and other app
features work without a model.

## Optional: model annotations

```bash
researchos judge --path /path/to/project --dry-run
```

`judge` asks the configured model to review matches between draft claims
and experiment metrics. `--dry-run` still calls the model, but does not
save its annotations. Omit the flag to save them. The model cannot confirm
or reject a relationship.

The default endpoint is local. If you configure a remote endpoint, the
review context is sent there.

## Optional: MCP

From a source checkout, in your Research OS Python environment:

```bash
python -m pip install -e ".[mcp]"
researchos mcp --path /path/to/project
```

The stdio server provides `researchos_trace`, `researchos_find`,
`researchos_status`, and `researchos_confirm_edge`. The app and core CLI
commands work without this extra.

## Optional: AI agents

Running an AI agent from a task needs the same MCP extra. Install it with
`uv tool install --force --with "mcp<2" git+https://github.com/LinyingL/research-os`
or `python -m pip install -e ".[mcp]"`. The app starts and stops agents;
there is no CLI command to run one. Setup and use are described in
[Using project home](project-workspace.md#let-an-ai-agent-carry-out-a-task).

```bash
researchos mcp --agent-run <run> --path /path/to/project
```

The app starts this form of `mcp` for an agent run, as a child of Claude
Code, Codex, DeepSeek Harness, or OpenCode; you do not run it by hand. It requires the environment
variable `RESEARCHOS_AGENT_RUN` to be set to the same run id, and
refuses to start without it. Its tools are different from the plain server's:

| Tool | Purpose |
| --- | --- |
| `researchos_complete_task` | Write the result record, mark the task done and link the record to it. |
| `researchos_ask` | Record a question for the person. The agent stops and the task shows Waiting for your answer. |
| `researchos_save_note` | Save a new note. |
| `researchos_find`, `researchos_trace`, `researchos_status` | Read-only, as above. |

`researchos_confirm_edge` is not offered to an agent: confirming a link is
a human judgment. Writes made through these tools are attributed to the
agent and the person who started the run.

Inside an agent run (`RESEARCHOS_AGENT_RUN` set in the environment), every
other `researchos` or `rce` command refuses with an error and does nothing,
and so does any write to a project's records made through Research OS
code. An agent therefore cannot approve a proposal, publish or confirm a
link with the CLI. This is one of several checks; it is a barrier, not a
sandbox.

## Projects and stored records

| Command | Purpose |
| --- | --- |
| `researchos projects list` | List registered projects and unavailable folders. |
| `researchos projects remove <path>` | Remove an entry from the list; project files stay in place. |
| `researchos records <path>` | List research records and retained material for backup. |

The machine's indexes and local state live under `~/.researchos`.
Set `RESEARCHOS_HOME` to use another location.

`query`, `trace`, `status`, and `confirm` use the current directory when
`--path` is omitted. `attempts` accepts a positional path or `--path`;
`lineage` takes a positional path and defaults to the current directory.

For more diagnostic output, put `-v` before the command:

```bash
researchos -v attempts /path/to/project --check
```

## Older RCE installations

`rce` remains an alias for one release. On first use, Research OS renames
`.rce/` to `.researchos/` and `~/.rce` to `~/.researchos`, checking the
files before completing the migration. If a rename fails, it is rolled
back; if both names exist, the migration stops for you to resolve them.

`RCE_HOME` is still used when `RESEARCHOS_HOME` is unset.
