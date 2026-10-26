# Changelog

## Unreleased

### Files from AI agents

- Give each task a work folder for its process files: the one-off scripts,
  check output, downloaded sources and scratch data an agent writes only to
  carry out the task. By default it is `work/<date>-<task name>/`, shared by
  the task's later runs, and **Output folders** lists its location as
  **Process files**. The index skips it and its files are not outputs.
- Tell each agent where every kind of file goes, which work folder and
  subfolder name are its task's, and that nothing is created under
  `research/`. The result record is the task's one report: a document you
  asked for goes to the documents folder, the record stays short, and Research
  OS writes the record's list of output files.
- Sort the files each run created after it ends: Research OS's own records,
  registered outputs, process files, caches, and the rest. A task whose latest
  run left files nobody registered says so in its row, for example
  **3 new files not registered**, with **Register…** to review them.
- Register a file wherever it is, as a kind you choose, and remove a
  registration without touching the file. In a team, a member's changed
  registrations are offered in **Submit changes** like a changed task. A task
  holds up to 1,000 registered files.
- Show a task's files in its row under **Task outputs**: the registered files
  grouped by kind, each opening in the file panel (figures with a small
  preview) and each with **Remove registration**; then the new files not
  registered; then the work folder's path and file count. A registration whose
  file is gone stays in the row, marked **File no longer there**, until it is
  removed.
- Say on every registered row in Figures and claims, Data & code and
  Literature which task produced the file and when. A task that is no longer
  in the list reads **Task removed**.
- Link the files a result record names. In its preview, a file path written
  in code style opens that file, and so does a link written from the project
  folder rather than from the record's own folder. The record itself is not
  rewritten.
- Offer the files that earlier runs created and nobody registered, from
  **Output folders** (**Register files from earlier runs…**). Each file comes
  with a suggested kind taken from its folder and name, or **Do not
  register**. Nothing is registered or moved until **Register selected**.
- Ask before a task that has a result is marked not done by clearing its box:
  a new run rewrites the same result record. Marking a task done never asks.
- Open the record from **Open the result** on a registered row in Figures and
  claims, Data & code or Literature. It opened in a page that was hidden, so
  nothing seemed to happen.
- Store each distinct content once in a project's undo backups, as a
  read-only copy shared by the runs that need it, instead of copying the
  project again for every run. A run that is undone releases its backup and
  cannot be undone twice.
- Keep a project's undo backups, files moved away by undo, and run logs within
  5 GB (1 to 100 GB, set under **Space limit** in the AI agent area). Over the
  limit, the oldest run's backup is released first and that run can no longer
  be undone. Files moved away by undo are removed only when you delete them.
- Show **Space used by runs** in the AI agent area: the sizes of **Undo
  backups**, **Files moved away by undo** and **Run log** against the limit,
  the runs that hold moved-away files with **Delete these files**, and
  **Release all undo backups**. Both ask first. Name the agent program's
  session in the run log as **Session ID**.

### Upgrading

- Upgrade every team member before anyone saves output folders or registers a
  process file. A version from before process files cannot read
  `research/folders.json` once it lists the process-files location, and a
  task's registrations that include a process file make its output views say
  the list cannot be read.
- Undo backups made before this version are converted in the background the
  first time a project's runs are cleaned up or **Space used by runs** is
  opened; the panel says **Tidying undo backups…** meanwhile. Backups of runs
  that were already undone are released.

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
- Collapse task details individually, opening only the three newest tasks by
  default. Keep manual disclosure choices and unsent replies during refreshes.

### Research routes and interface

- Preview Word (.docx) body text and tables in the file panel. Keep external
  opening for complete Word layout, and explain unsupported formats instead of
  presenting them as an engine error.
- Browse project references by title, author, and year, with a reading pane for
  abstracts. Read BibTeX and references CSV/JSON exports, enrich matching DOIs
  from Zotero, and keep citation checking in its own tab. Fetch missing abstracts
  from Crossref on request when online DOI lookup is enabled; cache the result
  locally and distinguish an unavailable abstract from a failed request.
- Look up missing abstracts in OpenAlex when Crossref has none, checking the
  exact DOI and keeping the abstract's source separate from reference metadata.
  Add a missing-abstract batch action and allow retrying unsuccessful lookups.
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

- Set project output folders for code, data, figures, tables, references,
  documents, and variable drafts, reusing existing directories. Give each run
  a fixed copy of those locations without moving files or creating a template tree.
- Accept typed final-file declarations through the completion tool. Validate
  their locations, existence, formats, reference exports, and variable TOML;
  retain the task, run, report, and file fingerprints in a project catalog.
  Show registered products in the relevant views before a rescan, including
  bounded CSV/TSV samples and image previews. Keep missing files out of the
  catalog view and mark changed ones.
- Import reported variable definitions only through a human action, into a new
  card or the next version draft. Keep open drafts and confirmed versions intact;
  never confirm a definition on behalf of the agent. Include the output catalog
  in run undo and link registered files from the result record for team review.
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
