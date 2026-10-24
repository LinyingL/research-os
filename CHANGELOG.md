# Changelog

## 0.3.0

### Research workspace

- Create projects from a name and save location, with an empty research record.
  Importing an existing attempt table is optional.
- Prepare an empty attempt table automatically when an existing project opens
  its first new-attempt form. Preserve existing files and configured tables.
- Write research questions, plans, notes, and result records in Project home.
  Preview Markdown, figures, and tables, and reference project files and attempts.
- Open existing plans, notes, and result records in preview. Enter editing
  explicitly and return to preview after saving; new documents start in editing.
  Keep the current mode and unsaved writing during background refreshes.
- Open existing Markdown files in `research/notes/` and `research/results/`,
  including files named outside the app. List documents by their last change.
- Preserve unsaved writing, detect external edits, and keep saved versions.
  Team submissions include referenced files and the result records linked by tasks.
- Keep optional tasks with an owner, date, completion status, and result link.
  Tasks have no required order and do not gate writing or research attempts.

### Research routes and interface

- Preview Word (.docx) body text and tables in the file panel. Keep external
  opening for complete Word layout, and explain unsupported formats instead of
  presenting them as an engine error.
- Browse project references by title, author, and year, with a reading pane for
  abstracts. Read BibTeX and references CSV/JSON exports, enrich matching DOIs
  from Zotero, and keep citation checking in its own tab. Fetch missing abstracts
  from Crossref on request when online DOI lookup is enabled; cache the result
  locally and distinguish an unavailable abstract from a failed request.
- Resize the shared file and detail panel by dragging its left edge. Keep its
  width on this device, support keyboard adjustment, and reset on double-click.
- Combine the route map and grouped attempt list into one Research routes page,
  with a shared detail panel and pending work alongside either view.
- Hide team navigation and identity reminders in single-person projects. Start
  collaboration from the project menu when needed.
- Save ordinary assessment and result edits directly, with undo. New attempts,
  conflicting edits, abandoning a route, and team proposals retain their previews.
- Distinguish written notes from object comments, result records from Figures
  and claims, and research assessments from link judgments in both languages.
- Keep the workspace's own notes and results out of manuscript claim extraction.
- Add Linying Li's name and GitHub profile to About, and adopt the MIT license.

### AI agents

- Assign tasks to a locally installed Codex, Claude Code, DeepSeek Harness, or OpenCode program. Agents
  can write code, run analysis, and finish with a result record linked to the task.
- Select complete model versions from the runner's catalog, or enter a full ID.
  Claude Code offers explicit Sonnet, Opus, Haiku, and Fable versions with
  readable version labels and exact IDs; existing alias-based tasks are kept.
  Preserve the selected ID for task ownership, execution, and continuation.
  Detect the macOS desktop app's bundled Codex CLI as well as saved paths.
- Choose reasoning effort separately for each Claude Code, Codex, or DeepSeek
  Harness model. Offer model-specific levels, retain the runner's default when
  unset, and record the selection for the run and its continuations.
- Run Codex in a workspace-write sandbox with a dedicated Research OS MCP
  server, JSON event logs, and continuation in the recorded session.
- Detect DeepSeek Harness's bundled desktop CLI and run tasks through its
  headless interface. Choose account or API-key authentication explicitly;
  reuse the supported credential store without changing the desktop profile.
  Keep the workspace-write policy, MCP reports, recorded-session replies,
  and undo. Model suggestions use official Harness IDs; custom IDs are accepted.
- Show progress, commands, file changes, questions, and completion reports.
  Answer questions or send completed work back for revision.
- Preview undo actions, choose files to keep, and restore supported changes.
  Include Python bytecode and pytest caches in undo; clean up new empty folders
  while preserving folders and files that existed before or changed afterward.
  Track newly created environment directories and archive them whole when
  unchanged, without scanning pre-existing dependencies.
  Copies are retained for files up to 16 MB in the newest 20 runs; the newest
  200 runs retain their logs on the Mac that ran them.
- Attribute agent-written results and file changes to the agent and the person
  who started it. Team changes remain local until submitted and reviewed.
- Strengthen checks for protected records, existing data, runner settings,
  sensitive paths, and credential-like environment variables. These checks
  are not an operating system sandbox; OpenCode is checked after execution.
- Recover interrupted runs and identify changes that went out of bounds.

### Engine and performance

- Require the engine's session for writes from the app or browser. A window
  opened through a plain server URL remains read-only.
- Reuse unchanged file fingerprints and parsed records, cache verified team
  metadata, and reduce repeated work in result tracing and change history.
- Read agent events incrementally, avoid repeated run listings, and pause
  polling in hidden windows. Cap file previews instead of reading entire files.

### Upgrading

- Close the app, install the current source, and run `researchos app` again.
  Rebuilding updates the native menus and enables the session used for saving.
- Upgrade every team member before using agent tasks: 0.2.0 cannot read the
  `report` links they add to `research/tasks.json`.
- AI task execution requires the optional MCP dependency and a configured
  Claude Code or OpenCode installation. Local-model draft assistance remains
  available separately.
- This version number identifies the source tree. Published tags and downloads
  are listed on the repository's Releases page.

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
