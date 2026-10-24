# Research OS

[![Version: 0.3.0](https://img.shields.io/badge/version-0.3.0-956b4b)](CHANGELOG.md#030)
[![tests](https://github.com/LinyingL/research-os/actions/workflows/tests.yml/badge.svg)](https://github.com/LinyingL/research-os/actions/workflows/tests.yml)

**A workspace for research plans, notes, results, and team review.**

Start with a research question. Write a plan, keep notes as ideas develop,
and collect results alongside the data and code behind them. Research OS
keeps this work together across your projects, including the attempts you
set aside and the reasons you recorded.

Write directly in the app and continue using Stata, Python, R, or your
usual editor for analysis. Work on your own or share a project with a PI
and collaborators. Plans, notes, tasks, and research attempts can develop
in whatever order your work needs.

![Project home with a fictional paper airplane study in English](docs/images/project-home-demo-en.png)

## Version 0.3.0

This version brings writing and AI task execution into the research workspace:

- **Project home:** write plans, notes, and result records as Markdown.
  Reference project files and research attempts, preview figures and tables,
  and inspect saved changes. Existing Markdown files in `research/notes/`
  and `research/results/` can be opened and edited too.
- **One research routes page:** switch between the route map and a grouped
  list. Both open the same attempt panel, with its assessment, results,
  related files, comments, and review actions.
- **A simpler single-person view:** team menus appear when collaboration is
  enabled. Ordinary changes to an existing attempt's assessment and result
  can be saved directly, with undo; abandoning a route still records a reason
  and preserves its basis.
- **AI agents:** assign tasks to Codex, Claude Code, or OpenCode, follow their work,
  answer questions, and receive a result record. Preview the files an undo
  would affect and choose which changes to keep.
- **Interface and engine improvements:** clearer terms in English and Chinese,
  session checks for writes, stronger agent checks, and less repeated file
  hashing, parsing, and polling.

See the [changelog](CHANGELOG.md#030) for the version changes and upgrade notes.

## Everyday work

| Where | What you do |
| --- | --- |
| Project home | Write the research question and plan, keep notes, organize result records, and maintain an optional task list. |
| Research routes | Record attempts, connect directions that grew out of earlier work, and retain the reasons for continuing or abandoning them. |
| Figures and claims | Inspect figures, tables, and quantitative claims in manuscripts, together with the relationships recorded for them. |
| Data and code | Browse scripts and datasets, inspect their inputs and outputs, and follow file relationships. |
| Variables and literature | Keep variable definitions and their versions. Browse project references with abstracts, search titles and authors, and check citations separately. Connect a local Zotero library or fetch missing abstracts by DOI. |
| Team review | Submit changes from a working copy, compare them as PI, and publish the accepted version. |

A **note** is an editable document. A **comment** is a short, attributed
message attached to a file or research attempt. **Result records** hold
findings and interpretation; **Figures and claims** shows the material
linked from manuscripts.

## Install

The native app requires **macOS 12 or later**, Apple's command line tools,
and [uv](https://docs.astral.sh/uv/getting-started/installation/).
The engine and command line require **Python 3.11 or later**.

Install or update from the current source:

```bash
uv tool install --force git+https://github.com/LinyingL/research-os@main
researchos app
```

These commands track `main`. Published tags are listed under
[Releases](https://github.com/LinyingL/research-os/releases).

Open **Research OS.app** from `~/Applications`:

- **New project…** creates a folder and opens the research question and plan.
- **Add project…** opens an existing research folder.
- **Join a project from the shared folder…** joins a team working copy.

New projects start with an empty research record. Importing an old attempt
table is optional.

When updating, close the app before installing, then run `researchos app`
again. This rebuilds the app with the current menus and the session needed
to save changes. A window opened through a plain server URL can read the
project but cannot save; open it through the app or `researchos serve`.

### Command line and development

```bash
git clone https://github.com/LinyingL/research-os.git
cd research-os
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

To scan an existing project and open its browser interface:

```bash
researchos init /path/to/project
researchos ingest /path/to/project
researchos serve /path/to/project
```

The [command-line guide](docs/cli-reference.md) covers file tracing,
research records, variables, and team commands.

## Optional AI

### Optional: AI agents

Research OS runs **Codex**, **Claude Code**, **DeepSeek Harness**, or **OpenCode** installed on
your Mac. Install and configure your chosen program first, and check that
it works from your terminal. OpenCode can use a local model through LM
Studio or Ollama when configured for that provider.

For Claude Code, sign in through its own login flow. Pro and Max users can
use their Claude subscription; Claude and Claude Code share the plan's
usage limits. See the [official subscription guide](https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-or-max-plan).
Claude Code's configured credentials determine subscription or API billing.
Its model picker includes explicit Sonnet, Opus, Haiku, and Fable versions,
such as `claude-sonnet-5-5` and `claude-opus-4-6`. The selected ID is passed
unchanged. Keep Claude Code up to date with `claude update`; supported
versions depend on your account and CLI. Full IDs and dated snapshots can
also be entered manually. See the [Claude model guide](https://support.claude.com/en/articles/11940350-claude-code-model-configuration).
If a run reports an expired OAuth token, sign in again:

```bash
claude auth login --claudeai
```

For Codex, sign in with `codex login`. Research OS also detects the CLI
bundled with the macOS desktop app. It uses Codex's existing authentication;
no API key is required when signing in with ChatGPT. Choose an available full
model ID, such as `gpt-6.1-sol`, from the model version list, or enter an ID
manually. **Refresh models** reloads Codex's catalog. Model access depends on
your account and login method; GPT-5.5 is no longer available with ChatGPT
sign-in, but remains available with API authentication. See the
[Codex model guide](https://learn.chatgpt.com/docs/models).

For [DeepSeek Harness](https://www.deepseek.com/harness/), install the official
desktop app and sign in there. Research OS detects its bundled `dsh` in
`/Applications` or `~/Applications`; installing a separate CLI is optional.
Choose **DeepSeek Harness** in the agent settings, then an **Account** model
to use that sign-in, or an **API Key** model to use the key configured in
Harness. The suggestions include `deepseek-flash` and `deepseek-v4-pro`;
availability depends on your Harness version and account. Custom IDs use
`deepseek-account/<model>` or `deepseek-official/<model>`. Tasks run through
the official headless interface, with progress, replies, reports, and undo
in Research OS. They do not open or control a desktop chat window.
See the [official desktop CLI documentation](https://github.com/deepseek-ai/deepseek-harness/blob/master/apps/desktop/README.md#terminal-command).

Install Research OS with the optional MCP dependency so the agent can
report back:

```bash
uv tool install --force --with "mcp<2" git+https://github.com/LinyingL/research-os@main
researchos app
```

From a checkout, use `python -m pip install -e ".[mcp]"`.

In **Project home**, open **AI agent** in the task area and add the program
and model version. Select it as a task's **Owner**, save, and click **Run**. The agent
works in the project folder: it can read files, write code, run analysis,
and write notes. Its completion report becomes a result record linked to
the task.

For supported models, choose **Reasoning effort** separately from the model
version. **Default** uses the runner's configuration. Each run records the
selection and retains it when you reply or send work back.

The initial instruction includes the task, the saved research question
and plan up to 8,000 characters, and the titles and paths of up to 50 notes
and result records. The agent reads further files as needed. Conversations
in the Claude desktop app are not included.

Use **Run log** to follow progress and inspect file changes. You can stop a
run, answer a question, send completed work back for revision, or preview
and undo supported changes. Research assessments, link confirmation, and
team approval stay with people.

Agent checks restrict changes to existing data and project records, but
are not an operating system sandbox. Claude Code has checks before tool
calls; OpenCode relies on its permissions and the comparison after a run.
Codex runs in its workspace-write sandbox, with changes checked afterward.
Undo keeps copies of files up to 16 MB for the newest 20 runs, leaves later
edits alone, and cannot reverse installed software or changes outside the
project. See the [agent guide](docs/project-workspace.md#let-an-ai-agent-carry-out-a-task)
for the full scope and limits.

Claude Code sends the content it reads to Anthropic. OpenCode uses the
model provider you configure, which may be local.

### Draft attempt fields with a local model

Start LM Studio's local server and load a model. In **New attempt**, open
**Model server**, enter the server address and model ID, and save. A usual
LM Studio address is `http://127.0.0.1:1234/v1`.

Choose up to five project text files and click **Generate draft**. The
model fills empty description and result fields and identifies their
source files. Include a text log or results file to draft findings; code
alone does not show what happened when it ran. Review the draft before
saving. Selected content goes to the server address shown in the form.

## Work alone or with a team

Single-person projects save directly. To start a team, choose **Start
collaboration…** in the project menu. Team members work in their own local
copies and submit changes through a shared folder:

1. Write in Research OS and run analysis in your usual tools.
2. Select the changes, explain them, and submit a proposal.
3. The PI reviews the differences, approves them or requests revisions.
4. Approved changes enter the official project and are published. Members
   sync to receive them.

An unapproved proposal stays separate from the official version. Research
attempts, assessments, variable versions, project files, and links between
attempts use PI review. Written plans, notes, and result records use the
file review workflow. Comments, link judgments, and manually recorded file
relationships sync without individual approval.

The PI's computer holds the official project. The shared folder carries
snapshots and proposals, transported by a service such as OneDrive.
Research OS does not host a collaboration server.

**Upgrade every team member to 0.3.0 before using agent tasks.** Version
0.2.0 cannot read the result links an agent adds to a task record.

## Files and research tools

Your written work stays in the project folder:

```text
research/overview.md      Research question and plan
research/notes/           Notes
research/results/         Result records, including agent reports
research/tasks.json      Optional tasks
.researchos/              Project identity, settings, and research records
```

The file index and retained local history live under `~/.researchos/`,
outside the project. Back up the project and local history together;
rebuilding an index does not restore old file contents. Local history
retains text up to 5 MB; fingerprints and summaries of larger files are
not restorable copies. Team snapshots retain published file contents
separately. Conflicting edits are kept for a person to resolve.

Research OS reads supported Python, R, and Stata scripts, Markdown and
LaTeX manuscripts, bibliographies, and optional git history and MLflow/W&B
experiment records. Stata support includes `.do` inputs and outputs,
resolvable macros, and `.dta` structure; `.ado` files are listed as scripts.
Scanning reads files without running the analysis.

Relationships show the evidence they came from. Imported run records can
provide evidence of execution; a relationship found in code alone does
not establish that it ran. File tracing is limited to the formats and paths
the readers can resolve.

## Earlier demo views

These English screenshots use synthetic data from the 0.1.0 Demo.
Their layouts predate 0.3.0.

<details>
<summary>Research routes, result context, and PI review</summary>

![Research routes with six attempts and a proposal awaiting PI review](docs/images/research-routes.png)

![A figure linked to data, scripts, a research attempt, and a manuscript](docs/images/result-provenance.png)

![PI review with previous and proposed content, a code diff, and review controls](docs/images/proposal-review.png)

</details>

## Documentation and feedback

- [Project home](docs/project-workspace.md) · [项目首页](docs/项目首页.md)
- [Install and join a team](docs/install-and-join.en.md) · [安装与加入团队](docs/安装与加入团队.md)
- [Command-line guide](docs/cli-reference.md)
- [Changelog](CHANGELOG.md) · [Design](DESIGN.md)
- [Report an issue](https://github.com/LinyingL/research-os/issues)

## License

[MIT](LICENSE). Copyright © 2026 [Linying Li](https://github.com/LinyingL).
