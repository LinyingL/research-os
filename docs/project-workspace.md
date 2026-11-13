# Project home

For Research OS **1.0.0**.

Choose **New project…**, enter a name and choose its save location. The app
creates a project folder and opens **Project home**. You can start writing
your research question and plan immediately. An empty attempt record is
prepared for when you need it; importing history is optional.

Use **Add project…** to open an existing research folder. Opening its home
page does not change its files. Its first saved plan creates the writing
page without changing its existing attempt table.
If it has no attempt table, clicking **New attempt** creates an empty table
and its settings automatically, then opens the form. Existing files are kept.

## Write and organize

Write freely, then click **Save**, or press Command-S in the writing area.
**Preview** displays headings, lists, tables and local image references.
Use **New note** for ideas or discussions and **Write a report** to keep
findings, figures and your interpretation together. Notes and reports
are listed by their last change, newest first. A document's title is the first
heading of its file. Research OS writes a heading only when you type a title
different from the one the file shows, in whichever language the screen is.

Markdown files you put in `research/notes/` or `research/results/` yourself
are listed too, whatever their names, and so are the copies a sync service
leaves beside a file; you can open and save them like the others. Research OS
does not read the files in `research/` as a paper draft: a number in a note
never becomes a claim.

The optional task list records what you want to do, who will do it and a
date when useful. Tasks can be completed, edited or removed independently.
There are no required stages or tasks to finish before recording research.

## Reference your files

Continue running code in Stata or your usual tools. Save their outputs in
the project folder, then open **Reference a file or attempt** and click
**Refresh files**. Choose a file and click **Insert reference** to add a
relative reference; supported images appear in the document preview. Files stay
in place. The attempts are listed by number (2 before 10). When the project has
more than 500 files, the list shows the first 500 and says so; a link you type
by hand to an existing file beyond them still works in the preview and in
**Submit changes**.

References identify the files you chose. They do not assert that a script
ran or produced a particular result. You can add links to research attempts
as well. Raw HTML is shown as text in the preview.

## Let an AI agent carry out a task

A task's owner can be an AI agent on your Mac: Codex, Claude Code, DeepSeek Harness, or a model
reached through OpenCode. The agent does the task itself, not a draft of it.

**Set up.** Install your chosen program and sign in to it. Install
Research OS with the optional MCP extra (see the
[README](../README.md#optional-ai-agents)); without it, **Run** says what is
missing. Open **Settings** at the foot of the sidebar (the task area of
**Project home** has **Change in Settings** beside **AI agent**), to its **AI agent**
section. Research OS shows whether it
found each program and its version; if not, enter the program's full path.
Add an agent by choosing the program and **Model version**. Codex's list
shows full IDs such as `gpt-6.1-sol`; **Refresh models** reads the current
catalog. Claude Code offers named versions such as Sonnet 5.5 and Opus 4.6,
alongside their full IDs. Keep Claude Code current with `claude update` to
use newer models. The Claude list is a set of documented suggestions;
account access is checked when the program runs. **Enter a full ID…** accepts an exact version or snapshot, including
Claude Code model IDs and OpenCode's `provider/model` names. Research OS
passes that ID unchanged; the runner decides whether your account can use it.
Codex uses its own `codex login` authentication and can also use the CLI
bundled with the macOS desktop app. GPT-5.5 requires API authentication;
it is no longer available with ChatGPT sign-in. See the
[Codex model guide](https://learn.chatgpt.com/docs/models).
A model served locally through OpenCode works the same way.

**Output folders.** Open **Output folders** in the task area to choose where
code, data, figures, tables, references, documents, variable drafts, and
process files belong. Defaults reuse common existing directories. Saving keeps
files in place and does not create empty output folders. Each run receives its
own copy of the locations; later setting changes apply to new runs.

**Process files.** An output is a file you will read or use again: a figure, a
table, a dataset meant for further work, a document, a reference export, a
variable draft, and the code needed to reproduce any of them. Everything an
agent writes only to carry out the task is a process file: one-off probe and
check scripts and their output, verification files, downloaded third-party
sources, scratch data and caches. These go to the task's **Process folder**,
`work/<date>-<task name>/` by default, for example `work/20261025-Price-check/`.
The date is the day the task first ran and the name is the task's first line,
shortened. Later runs of the same task (an answer, a send back, a new start) use
the same folder. Research OS does not create it; the agent does when it needs
it. The index skips the process folder, its files are not outputs, and a data file
in it is not protected, so a later run of the task may overwrite its own process
files. The location has to stand apart: Research OS refuses a choice that
equals, contains or lies inside another location, or lies under `research/`. If
no usable folder is left, **Output folders** asks you to choose one for process
files, and a run does not start until you do.

The agent lists its final files when completing the task. The app checks each
file's location, existence, and supported format, then lists it in the relevant
view. Figures and plain CSV/TSV tables have previews; bibliography exports
provide their available abstracts, and registered PDFs have file links. A reference
export can have any filename when registered. A data or table file may be
compressed with one of `.gz`, `.bz2`, `.xz` or `.zst` (`prices_long.csv.gz`) and
is registered as the kind it holds; it has no preview, and the rule that keeps
agents from changing data files that existed before a run does not cover
compressed ones. Missing files stop appearing;
changed files carry **File changed**. These links record the task's declaration,
not proof of execution or a confirmed research assessment. In **Figures and
claims**, **Data & code**, **Literature** and **Variables**, the registered rows
stand under one heading per task: the first heading of the task's report
(or the task's first line, or **Task removed** when the task is no longer in the
list), the date of the run that produced the files, and **Open the result** once,
which goes to **Project home** and opens the task's report. A row does not
repeat the task; it shows its own date only when that differs from the heading's.

**Task outputs.** A task that has a report shows its files in its row,
under **Task outputs**, which collapses with the task's other details. The
registered files are grouped by kind; each opens in the file panel, figures show
small previews, and each has **Remove registration**. The **Process files**
group is only its count until you open it; opened, it is like every other group:
a group of more than eight files shows the first eight and **N more** for the
rest (a task can register hundreds of process files). What you opened stays
open when the row is drawn again. When a run ends, Research OS
sorts the files it created: its own records, outputs registered for the task,
files in the process folder, caches, and the rest. If the task's runs that were
not undone left files in the rest (a question and its answer are two runs of one
conversation, and all count), the row says so next, for example **3 new files not
registered**, and **Register…** lists them for review (below). Last come the
process folder's path and the number of files in it.

**Register and remove.** You register files from the two review lists below,
**Register…** in a task's row and **Register files from earlier runs…** in
**Output folders**. They offer the files that a run created and nobody
registered. Such a file can be registered wherever it lies, in an output
folder or anywhere else in the project, as any kind its format allows; process
files accept any format. Records, anything under `research/notes/` or
`research/results/`, and `.researchos/` are not offered and cannot be
registered; nor can a file you wrote yourself, which no run created.
**Remove registration** asks first and leaves the file where it is. A registration whose
file has gone stays in the row, marked **File no longer there**, so you can
remove it; a file that is only in the cloud and cannot be read on this Mac is
marked **The file is only in the cloud** and still counts as registered. Process
files you register show only in the task's row; the views do not list them. A
task holds up to 1,000 registered files, fewer when the paths are long, because
a record is at most 200 KB; a registration that would not fit is refused whole,
with a message to register fewer files or remove registrations first. A document
that a run created and that is registered as a document, by the agent or by you
from the review list, is not read as a paper draft, so its numbers do not become
claims; a draft that existed before the run and that the agent edited stays one.

**Register files from earlier runs.** Files that agents created in earlier runs
were never registered. **Register files from earlier runs…** in **Output
folders** (and **Register…** in a task's row, for all of that task's runs that
were not undone) lists the ones still there, grouped by task with the run's date
and agent. A file registered from here is dated by the run that created it.
Runs you undid and tasks with no report are left out, and so are records,
caches, files in a run's own process folder and files already registered. Each file
has a suggested kind taken from its folder and name: files under folders such as
`checks`, `logs`, `tmp` or `sources`, and `.log` files, as process files; images
as figures; `.bib` as literature; code, data and documents by their format. A
file with no clear kind is listed with **Do not register**. Change a kind,
clear a box, then **Register selected**. Nothing is written and no file is moved
until then. If a file is refused (it changed or is gone since the list was made,
or it does not fit its kind, as a reference export with nothing readable does
not), the message names it so you know which box to clear. The list shows up to
5,000 files at a time; register those and open it again for the rest.

Variable definitions use the native TOML format and appear as drafts under
**Variables**. **Import as variable draft** creates a card, or the next draft
version of an existing confirmed card. An existing open draft must be resolved
first. Importing leaves confirmed versions in place and does not confirm the
new definition. Confirmation uses the card's separate human action.

Folder settings live in `research/folders.json`; saving them writes all eight
locations. Each task report has its own `<report-id>.outputs.json` beside it in
`research/results/`, so independent tasks can also be undone independently. The
app maintains these registration files; agents use the completion tool, and
your registrations and removals go through the same files. Submitting changed
tasks also selects the registered files and metadata for the existing team
review, and a member's changed registrations are offered there even when the
task itself did not change.

**Reasoning effort.** Select a level beside the model when adding an agent,
or change it in the saved agent's row. **Default** leaves the runner's own
configuration in force. Claude Code and DeepSeek Harness offer levels for
supported model versions; Codex reads them from its current model catalog.
The control is hidden for models without known supported levels, including
OpenCode. The run log records the selected setting. Replies and revisions
in the same conversation retain that setting; a new run uses the current choice.

For DeepSeek Harness, Research OS detects the official desktop app's bundled
`dsh` in `/Applications` or `~/Applications`, or a standalone `dsh` on PATH.
Choose an **Account** model to use the Harness desktop sign-in, or an **API Key**
model for Harness's configured key. The suggested IDs are `deepseek-flash` and
`deepseek-v4-pro`; access is checked when the task runs. A custom ID includes
its route: `deepseek-account/<model>` or `deepseek-official/<model>`. Research OS
starts a headless session and continues that recorded session when you reply.
It uses a temporary task configuration, leaving the desktop profile untouched;
desktop-only plugins and Office authoring resources are not loaded.

**Run.** Set the task's **Owner** to the agent and click **Run**. The first
time you run each agent after opening the page, the app tells you where
project content goes, that the agent changes files directly, and what an undo
cannot bring back (software it installs, content it downloads, what it does
in git, anything outside the project folder). It also names the project's files
over 16 MB (the ten biggest, then a count): they are not backed up before a
run, so a change to one cannot be undone. The task shows its state: Preparing,
Running, Waiting for your answer, Done, Ended without a report, Failed,
Stopped, Interrupted, or Went out of bounds (below). One run goes at a time in a project; **Stop** ends it
and everything it started. A run is stopped after the time limit (two hours
by default; you can change it in the same area, together with a spending
limit for Claude Code), and when you quit the app.

**What it does.** The agent works in the project folder. It can read your
files, write code, run Python, R or Stata, install packages, download data
and write notes. It is told where each kind of file belongs (the locations in
**Output folders**), which process folder and subfolder name are its task's, that
process files go to the process folder, and that nothing is created under
`research/`, where only Research OS writes. Programs it leaves running when it
finishes are ended. When it finishes it writes a report, marks the task
done and links it; **Open the result** opens that record. The record is the
task's one report: a document you asked for (a plan, a review, a write-up) goes
to the documents folder as an output, and the record stays short, with the
conclusion, the key numbers and what to read first. The agent names files in its
report where they matter but adds no list of its outputs: Research OS appends
the section **Registered output files** to the record. In the preview, a file path
written in code style opens that file, and so does a link written from the
project folder rather than from the record's own folder; the record itself is
not rewritten. Figures and numbers in it should come from what it ran: check
them as you would a colleague's.

**What it may not do.** It never decides an attempt's verdict or result in
the attempt table, confirms or rejects a link, or touches `.researchos/`. It
never proposes, approves, publishes or syncs, pushes to GitHub, or changes
git's state (commit, reset, checkout, and the like). It does not change or
delete a data file (`.csv`, `.tsv`, `.xlsx`, `.xls`, `.parquet`, `.feather`, `.rds`, `.rdata`, `.dta`, `.sav`, `.json`, `.h5`, `.pkl`, `.sqlite`, `.db`, `.npy`, `.npz`, `.mat`, `.sas7bdat`, `.jsonl`, `.pickle`; `package.json` and `package-lock.json` are not data) that existed before the run, or move or remove
a folder holding one; it writes new files instead. It does not edit
`research/tasks.json` except through its own tools. Research OS enforces
this with checks before each step where the program allows, and by
comparing the folder before and after every run. A change that still gets
through is shown first, in the warning colour, as an **Out-of-bounds change**,
with **Undo this run** beside it. A run that ends with one reads **Went out
of bounds**, never Done, whatever else happened; the task keeps the done mark
the agent gave it. Git's hooks and configuration (`.git/hooks`, `.git/config`)
and Research OS's backups count too: a hook planted, or a backup deleted, is
out of bounds. So are the settings of the agent programs in the project
(`.claude/`, `.opencode/`, `.codex/`, `opencode.json`): a run cannot write them, and a
change there is out of bounds, since a later run would otherwise load what an
earlier one wrote. Claude Code does not load the project's own
`.claude/settings*.json` at all.

**What it cannot read, and is not given.** Claude Code's reading tools (Read,
Glob, Grep, LS) are refused inside Research OS's own folder (`~/.researchos`)
and `~/.ssh`. The agent program is not told where Research OS keeps its files,
and the environment variables whose names look like credentials (`TOKEN`,
`SECRET`, `PASSWORD`, `API_KEY`, `AUTH`, `SESSION`, `COOKIE`, `AWS_*`,
`GITHUB_*`, and the like) are not passed on, except what the program needs to
reach its model. The run log lists the names left out, never the values.

**The run log.** **Run log** shows what the agent said, each command and file
it used, what was refused, how the run ended, the files it created, changed
and deleted, any cost the program reports, and the instruction that was
sent. It also names the agent program's own session (**Session ID**), so you can
find the conversation in that program; Research OS never reads, moves or deletes
the programs' own session records. The log stays on the Mac that ran it: the
newest 200 runs of a project are kept, and an older run that still holds files
moved away by an undo keeps them, and its record, until you delete the files. In
a project of more than 50,000 files only the first 50,000 are recorded before a
run (data files are always checked); the log says so, and changes to the other
files cannot be checked or undone. Files the system or
an editor leaves, such as `.DS_Store` or `~$` lock files, are not counted.

**Answer, send back, undo.** If the agent needs a decision, it asks one
question and stops; the task shows **Waiting for your answer**. **Answer**
continues the same conversation. If the work is not right, **Send back** with
what to change; a task already marked done is reopened first. Only the
latest run of a task can be continued. Clearing the box of a task that has a
report, to mark it not done yourself, asks first: "This task already has a
report. Once it is marked not done it can be run again, and a new run rewrites
the same report. Mark it as not done?" Marking a task done never asks.
**Undo this run** first shows what it will do: the files it will put back
(changed or deleted ones, restored from copies taken before the run, and the
done mark and result link it set) and the files it will move away (the ones the
run created, moved into the run's own folder; Research OS never deletes them,
you can, under **Space used by runs** below), each with its time and a
checkbox, all checked.
It also lists the files it cannot undo, with the reason. Clear the boxes of
files you want to keep, then **Confirm undo**; the files you cleared are left
as they are and the run log lists them as "You kept this". Python bytecode
(`__pycache__`) and pytest caches are included. Newly created folders are
removed once empty; folders created after the run stay. A folder that
existed before the run is never removed. A new environment such as `.venv` or
`node_modules` is moved away as a whole if it has not changed since the run.
Existing environments are kept; changes inside them are not backed up.
A file you changed after the run, or
one too large to copy beforehand (over 16 MB), cannot be undone: it is left
as it is and listed with the reason. The run then reads **Partly undone**. A run cannot be undone while a later run
touched the same files: undo the later one first. Copies of the newest 20
runs of a project are kept, within the space limit below; older runs can no
longer be undone. An undone run releases its copies, so it cannot be undone
twice.

**Space used by runs.** A project's undo backups keep one read-only copy of each
distinct content, shared by the runs that need it, so a file that is the same in
several runs is stored once. A project's undo backups, files moved away by
undo, and run logs may take up to 5 GB; change the **Space limit** (1 to 100
GB) in the AI agent section of **Settings**, where the time limit is. When they pass the limit,
Research OS releases the oldest run's backup first, then the next, and those
runs can no longer be undone; a run that is going, or waiting for its changes
to be compared, keeps its own. Files moved away by undo are never removed
automatically: when they alone pass the limit, the panel says so, and that each
new run's undo backup is then released as soon as the run ends, so new runs
cannot be undone until you delete moved-away files. **Space used
by runs**, in the same area, shows the sizes of **Undo backups**, **Files moved
away by undo** and **Run log** against the limit; lists the runs that hold
moved-away files, with their task (or **Task removed**), date, size and count,
each with **Delete these files**; and offers **Release all undo backups**. Both
ask first, and neither can be reversed. Backups made by an earlier version are
converted in the background the first time a project is cleaned up or the
panel is opened; the panel says **Tidying undo backups…** meanwhile.

**Attribution.** Each file the agent created or changed is recorded in
**Change history** as made by an agent, naming the agent and the person who
started the run. The report opens with a line saying which agent
wrote it, when, and for whom. For a file tracked by git, the history is
git's; the run log names the agent's change.

**Privacy.** Codex sends the project content it reads to OpenAI.
DeepSeek Harness sends it to DeepSeek.
Claude Code sends the project content it reads to Anthropic.
OpenCode sends it to the model provider you configured in OpenCode, which
may be a server on your Mac. Research OS does not send it anywhere itself.

**In a team.** An agent works where the person who started it works: on the
PI's Mac in the official project, on a member's Mac in their working copy.
What it writes is that person's local change. Nothing is proposed for you;
check the result, then use **Submit changes** as for your own edits. Other
members see the task's owner and outcome and the history, not the run log.
Update every member's Research OS before assigning tasks to agents: once an
agent finishes a task, the task file carries a `report` link that 0.2.0
cannot read. The same goes for process files: update every member before anyone
saves output folders or registers one. A version from before process files
cannot read `research/folders.json` once it lists the process-files location,
and a task's registrations that include a process file make its views say the
list cannot be read.

**Limits.** The checks are a barrier, not a sandbox: they read the agent's
commands as text, so an agent set on getting around them with shell commands
could. OpenCode is checked only after the run. Codex uses a workspace-write
sandbox and is checked afterward; it has no Research OS check before each
tool call. Codex runs ignore user configuration and execution rules; the
run supplies its own sandbox and dedicated Research OS tools without
changing the user's Codex settings or login. Use a backed-up copy of the
project until you trust the result.

## Save and review

Documents are stored under `research/` as Markdown, and optional tasks as
`research/tasks.json`. Previous saved contents are retained in local history
and backups. If another editor changed a file, the app keeps your unsaved
draft and offers **Keep draft** or **Reload file** before you save again.
**Change history** opens the document's saved versions and differences. If the
project's identity file cannot be read for the moment (it may be only in the
cloud), the page says so, lists no attempts and stays read-only.

In a team, members save in their own working copies, and each document and the
task list is marked with its state: **My change, not yet proposed**,
**Proposed** or **Approved, awaiting sync**. **Submit changes** opens the
existing proposal preview; a document's referenced local files are also
selected for review, and submitting the tasks takes along the reports
that the changed tasks link, so that an approved task never points at a report
the official project lacks. Check that selection, explain the change and send
it. Approval applies it to the official project. On the PI's page, a note or a
report says it goes to the members with the next **Publish**. A Mac that
is not a member of the project says so, and the project can only be read there.
A single-person project saves directly.
