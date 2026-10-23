# Research OS

[![Latest release: 0.2.0](https://img.shields.io/badge/latest_release-0.2.0-956b4b)](https://github.com/LinyingL/research-os/releases/tag/v0.2.0)
[![tests](https://github.com/LinyingL/research-os/actions/workflows/tests.yml/badge.svg)](https://github.com/LinyingL/research-os/actions/workflows/tests.yml)

**A workspace for research plans, notes, results, and team review.**

Research OS keeps a project's written work, files, and decisions together.
Write your research question and plan, keep notes as ideas develop, and
organize findings alongside the data, code, and figures they refer to.
Use it to manage several projects on your own or work with a PI and
collaborators.

Write directly in the app and continue using Stata, R, Python, or your
usual editor for analysis and code. Plans, notes, results, and tasks can
develop in whatever order your research needs.

> This README describes **`main`, the development version**. The latest
> tagged release is **0.2.0**, with Stata support and model-assisted drafts.
> Project home, writing in the app, and AI task execution are on `main`;
> they are not included in the 0.2.0 release.

Project home, using a fictional paper airplane study in English:

![Project home with a fictional paper airplane study in English](docs/images/project-home-demo-en.png)

## What you can do

| Area | In practice |
| --- | --- |
| Plans and notes | Start a project with a name and folder. Write the research question, plans, ideas, and discussions directly in the app. |
| Results | Keep findings and interpretations with references to figures, tables, data, and scripts. Preview Markdown and local images. |
| Research routes | Record what you tried, how attempts relate, and why you continued or abandoned a direction. |
| Files and history | Follow links between results, code, and data. Inspect saved changes and the decisions recorded alongside them. |
| Team review | Work in local copies, submit changes with an explanation, and review them as PI before publishing the accepted version. |
| Tasks | Keep an optional list of work, with an owner, date, and completion status. Assign a task to an AI agent when useful. |
| Variables and literature | Keep variable definitions and their revisions, follow draft citations, and connect a local Zotero library. |

A result note can link a figure to its script, input data, and research
attempt. If you later set that route aside, its notes and reasons remain
part of the project. Extracted file relationships show their source;
relationships read from code alone do not establish that it ran.

Research OS reads supported Python, R, and Stata scripts, Markdown and
LaTeX documents, bibliographies, and optional git history and MLflow/W&B
run logs. Stata support includes `.do` file inputs and outputs, resolvable
macros, and `.dta` structure. Scanning reads files without running Stata.

## Research routes, results, and review

These three views are from the **0.1.0 Demo**, using synthetic data.

**Research routes.** See the attempts made so far, the directions set
aside, and proposals awaiting review.

![Research route map with six attempts and a proposal awaiting PI review](docs/images/research-routes.png)

**Result context.** Open a figure to inspect its linked scripts, data,
research attempt, and paper draft, together with its history and comments.

![A figure linked to source data, scripts, an intermediate dataset, and a paper draft](docs/images/result-provenance.png)

**PI review.** Compare the proposed changes and their explanation before
approving them or asking for revisions.

![PI review with the previous and proposed rows, a code diff, and review controls](docs/images/proposal-review.png)

## Work alone or with a team

For your own projects, save plans and notes directly and keep track of
results across projects. Single-person projects have no approval step.
New projects start with an empty research record; importing a historical
table is optional.

In a team, each person works in a local copy:

1. Edit documents in Research OS and work on code and analysis in your
   usual tools.
2. Review your changes, explain them, and submit a proposal through the
   shared folder.
3. The PI compares the changes and their context, then approves them or
   returns them for revision.
4. Approved changes are applied to the PI's project and published.
   Members sync to receive that version.

An unapproved proposal stays separate from the official version. Approval
covers research attempts, verdicts, variable versions, project files, and
links between attempts. Plans and notes saved as project documents use
the file review workflow. Comments on files or attempts, link judgments,
and manually recorded file relationships sync without
individual approval.

The PI's computer holds the accepted project, the shared folder carries
published snapshots and proposals, and members keep their working copies
locally. A service such as OneDrive transports the shared files. Research
OS manages this exchange; it does not host a collaboration server.

## Install

The native app requires **macOS 12 or later**, Apple's command line tools,
and `uv`. The engine and command line require **Python 3.11 or later**.

### Current development version

To use the project writing workspace and optional AI agents described here:

```bash
uv tool install --force git+https://github.com/LinyingL/research-os@main
researchos app
```

Open **Research OS.app** from `~/Applications`. Choose **New project…** to
start with a research question and plan, **Add project…** to open an
existing folder, or **Join a project from the shared folder…** to join a
team.

### Tagged release: 0.2.0

To install the published 0.2.0 version instead:

```bash
uv tool install --force git+https://github.com/LinyingL/research-os@v0.2.0
researchos app
```

This release includes research routes, file relationships, team review,
Stata support, and local-model assistance for drafting attempt fields.
See the [release notes](CHANGELOG.md) and the 0.2.0 installation and team
setup guides in [English](docs/install-and-join.en.md) or
[中文](docs/安装与加入团队.md).

### Command line and development

```bash
git clone --branch main https://github.com/LinyingL/research-os.git
cd research-os
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

For an existing project, replace the example path with your own:

```bash
researchos init /path/to/project
researchos ingest /path/to/project
researchos serve /path/to/project
```

See the [command-line guide](docs/cli-reference.md) for queries, file
tracing, research records, and team commands.

## Optional AI

### Draft attempt fields with a local model

Start LM Studio's local server and load a model. In **New attempt**, open
**Model server**, enter the server address and model ID, and save. The
usual LM Studio address is `http://127.0.0.1:1234/v1`.

Choose up to five project text files and click **Generate draft**. The
model fills empty description and result fields and identifies the source
files used. Include a text log or results file to draft findings; a script
alone does not show what happened when it ran. Review the draft before
saving. Verdicts and approval decisions stay with the researcher.

The model receives the selected files at the server address you configure,
which is shown before generation.

### Optional: AI agents

On `main`, a task can be assigned to **Claude Code** or an **OpenCode**
model. Install and configure the chosen program first, and check that it
can run from your terminal. OpenCode can use a local model through LM
Studio or Ollama when configured for that provider.

Install Research OS with the optional MCP dependency so the agent can
report back:

```bash
uv tool install --force --with "mcp<2" git+https://github.com/LinyingL/research-os@main
researchos app
```

From a checkout, use `python -m pip install -e ".[mcp]"`.

In **Project home**, open **AI agent** in the **Tasks** area and add the
program and model. Assign it as a task's **Owner**, save the task, then
click **Run**. The agent works directly in the project folder: it can write
code, run analysis, and write notes. Its completion report becomes a
result record linked to the task.

The run log shows progress and file changes. You can stop a run, answer a
question, send completed work back for revision, or undo supported changes.
Research judgments and team approval remain with people. The checks and
undo limits are described in the [project home guide](docs/project-workspace.md#let-an-ai-agent-carry-out-a-task).

Claude Code sends the content it reads to Anthropic. OpenCode uses the
model provider you configure. The agent's checks are not an operating
system sandbox; Claude Code has checks before tool calls, while OpenCode
relies on its permissions and the comparison after a run. Undo retains
copies of files up to 16 MB for the newest 20 runs and leaves later edits
alone. Upgrade all team members before using agent tasks: 0.2.0 cannot
read the result links they add to the task record.

## Files, history, and limits

Documents are saved as Markdown in the project's `research/` folder.
Project identity, configuration, and other research records live in
`.researchos/`. The derived file index and retained local history live
under `~/.researchos/`, outside the project folder. Back up both the project
and local history; rebuilding the index does not restore old file contents.

File relationships may come from code, imported experiment logs, or a person's
statement. Their basis stays visible, and the app does not promise a
complete execution history for every result. Local file history retains
text up to 5 MB; a fingerprint or summary of a larger file is not a
restorable copy. Published team snapshots retain file contents separately.

Conflicting edits need a person to resolve them. Sync preserves the local
file and places the official version beside it for comparison. The app
is still in early development; use a backed-up project when trying a new
version.

## Documentation and feedback

- [Using project home](docs/project-workspace.md) · [项目首页](docs/项目首页.md)
- [Install and join a team — 0.2.0](docs/install-and-join.en.md) · [中文指南](docs/安装与加入团队.md)
- [Command-line guide](docs/cli-reference.md)
- [Product and architecture design](DESIGN.md)
- [Report a bug or suggest a change](https://github.com/LinyingL/research-os/issues)

## License

[MIT](LICENSE). Copyright © 2026 [Linying Li](https://github.com/LinyingL).
