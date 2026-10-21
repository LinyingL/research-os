# Changelog

## Unreleased

- Add author credits and a GitHub profile link to About; adopt the MIT license.
- Create a project from its name and save location. The app prepares an empty
  attempt record; no historical table or configuration file is needed first.
- Write research questions, plans, notes and result interpretations on the
  project home page. Save as Markdown, preview figures and tables, and reference
  files already produced by Stata or other research tools.
- Keep optional tasks with an owner, date and completion checkbox. Tasks have
  no required order and do not gate writing or research attempts.
- Preserve unsaved drafts during refreshes, detect conflicting file edits, and
  retain saved versions. Team drafts use the existing PI proposal workflow.
- Avoid opening unrelated protected and cloud folders while adding a project.
- Shorten interface copy and distinguish saving, proposing, approval and publication.
  Correct file-name matching, research-assessment labels and backup timing in previews.
- Let an AI agent carry out a task. A task's owner can now be Claude Code or a
  model reached through OpenCode, both installed on your Mac. Add the agent
  under AI agent in the task area, assign a task and click Run. The agent
  works directly in the project folder: it can write code, run Python, R or
  Stata, install packages, download data, write notes, and finish with a result
  record that marks the task done. The app shows its progress in a run log.
- Keep human judgments with people. An agent never decides an attempt's verdict
  or result, confirms a link, touches `.researchos/`, proposes, approves,
  publishes or syncs, changes git's state, edits `research/tasks.json` except
  through its own tools, or changes a data file that existed before the run
  (new data goes to new files). Research OS enforces this and does not rely on
  the model. A change that gets past it is shown first in the run as an
  out-of-bounds change.
- Answer, send back or undo. An agent that asks a question stops and shows
  Waiting for your answer; Answer resumes the same conversation. Send back
  reopens a finished task with your instructions. Undo this run moves the files
  the run created aside, restores the ones it changed from copies taken before
  it started, and leaves anything you changed afterwards as it is.
- Attribute the agent's work. Its files appear in the change history as made
  by an agent, with the agent and the person who started the run named, and the
  result record opens with a line saying which agent wrote it and for whom. In
  a team the agent works in the working copy of the person who ran it; nothing
  is proposed automatically.
- Running an agent needs the optional MCP extra. Install it with
  `uv tool install --force --with "mcp<2" git+https://github.com/LinyingL/research-os`
  or `python -m pip install -e ".[mcp]"`. Without it the app works as before and
  says what to install when you click Run.
- The guard is a barrier, not a sandbox: it reads the agent's commands as text
  and compares the folder after the run, and an agent set on getting around it
  with shell commands could. Claude Code sends what it reads to Anthropic;
  OpenCode sends it to the model provider you configured, which may be a local
  server. OpenCode has no check at each step, only the check after the run.
- `research/tasks.json` may now carry a `report` field on a task and agent
  owners. Research OS 0.2.0 refuses to read such a file, so update every
  team member's Research OS together.


## 0.2.0 — 2026-10-20

### Stata support

- Read `.do` files to link scripts to their data inputs, data outputs and
  exported figures, including resolvable macros and working-folder changes.
- Suggest project folders for globals and create the dependent links after
  a person confirms the folder. Unresolved paths stay visible with a reason.
- Inspect `.dta` row counts, variables, labels and storage types from the
  header, without reading data rows. Track structural changes between versions.
- Connect `.dta` readers and writers across Stata, Python and R pipelines.

Scanning does not execute Stata. `.ado` files are listed as scripts; parsing
their file relationships, interpreting Stata result tables and logs, and
building variable cards from `gen`/`egen` are outside this release's Stata support.

### Local-model assistance

- Generate an attempt's description and result from up to five chosen text
  files using LM Studio or another OpenAI-compatible server.
- Fill empty fields only, identify their source files and leave unsupported
  content empty. Review and edit the draft before saving or submitting it.
- Configure the server address and model in the form. The destination is
  shown before file contents are sent; the default is this Mac.
- Keep verdicts, reasons and approval decisions under the researcher's control.

The model is optional. Reading code does not establish execution or an
observed result; include a text log or results file to draft findings.

### Fixes included

- Accept a model server's root address and add `/v1` automatically.
- Handle LM Studio responses that put complete, schema-valid JSON in
  `reasoning_content` while leaving the final answer empty.
- Restrict draft citations to selected file paths, including Chinese names,
  and reject extra fields and unsupported numbers.

### Installation and updates

The macOS app is built from source on your Mac. Install this version with:

```bash
uv tool install git+https://github.com/LinyingL/research-os@v0.2.0
researchos app
```

For an existing uv installation, close the app first and use:

```bash
uv tool install --force git+https://github.com/LinyingL/research-os@v0.2.0
researchos app
```

The update uses the existing project and machine-local records. It does not
require reinitializing projects or joining teams again.
