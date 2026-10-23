# Project home

This guide describes the development version after 0.2.0.

Choose **New project…**, enter a name and choose its save location. The app
creates a project folder and opens **Project home**. You can start writing
your research question and plan immediately. An empty attempt record is
prepared for when you need it; importing history is optional.

Use **Add project…** to open an existing research folder. Opening its home
page does not change its files. Its first saved plan creates the writing
page without changing its existing attempt table.

## Write and organize

Write freely, then click **Save**, or press Command-S in the writing area.
**Preview** displays headings, lists, tables and local image references.
Use **New note** for ideas or discussions and **Record results** to keep
findings, figures and your interpretation together. Notes and result records
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

A task's owner can be an AI agent on your Mac: Claude Code, or a model
reached through OpenCode. The agent does the task itself, not a draft of it.

**Set up.** Install Claude Code or OpenCode and sign in to it. Install
Research OS with the optional MCP extra (see the
[README](../README.md#optional-ai-agents)); without it, **Run** says what is
missing. In the task area open **AI agent**. Research OS shows whether it
found each program and its version; if not, enter the program's full path.
Add an agent by choosing the program and a model name: `sonnet`, `opus` or
a full model name for Claude Code, or `provider/model` as `opencode models`
lists it. A model served locally through OpenCode works the same way.

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
and write notes. Programs it leaves running when it finishes are ended.
When it finishes it writes a result record, marks the task
done and links it; **Open the result** opens that record. Figures and
numbers in it should come from what it ran: check them as you would a
colleague's.

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
out of bounds. So are the settings of Claude Code and OpenCode in the project
(`.claude/`, `.opencode/`, `opencode.json`): a run cannot write them, and a
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
sent. The log stays on the Mac that ran it: the newest 200 runs of a project
are kept. In a project of more than 50,000 files only the first 50,000 are
recorded before a run (data files are always checked); the log says so, and
changes to the other files cannot be checked or undone. Files the system or
an editor leaves, such as `.DS_Store` or `~$` lock files, are not counted.

**Answer, send back, undo.** If the agent needs a decision, it asks one
question and stops; the task shows **Waiting for your answer**. **Answer**
continues the same conversation. If the work is not right, **Send back** with
what to change; a task already marked done is reopened first. Only the
latest run of a task can be continued. **Undo this run** first shows what it
will do: the files it will put back (changed or deleted ones, restored from
copies taken before the run, and the done mark and result link it set) and
the files it will move away (the ones the run created, moved into the run's
own folder, never deleted), each with its time and a checkbox, all checked.
It also lists the files it cannot undo, with the reason. Clear the boxes of
files you want to keep, then **Confirm undo**; the files you cleared are left
as they are and the run log lists them as "You kept this". A folder that
existed before the run is never removed. A file you changed after the run, or
one too large to copy beforehand (over 16 MB), cannot be undone: it is left
as it is and listed with the reason. The run then reads **Partly undone**. A run cannot be undone while a later run
touched the same files: undo the later one first. Copies of the newest 20
runs of a project are kept; older runs can no longer be undone.

**Attribution.** Each file the agent created or changed is recorded in
**Change history** as made by an agent, naming the agent and the person who
started the run. The result record opens with a line saying which agent
wrote it, when, and for whom. For a file tracked by git, the history is
git's; the run log names the agent's change.

**Privacy.** Claude Code sends the project content it reads to Anthropic.
OpenCode sends it to the model provider you configured in OpenCode, which
may be a server on your Mac. Research OS does not send it anywhere itself.

**In a team.** An agent works where the person who started it works: on the
PI's Mac in the official project, on a member's Mac in their working copy.
What it writes is that person's local change. Nothing is proposed for you;
check the result, then use **Submit changes** as for your own edits. Other
members see the task's owner and outcome and the history, not the run log.
Update every member's Research OS before assigning tasks to agents: once an
agent finishes a task, the task file carries a `report` link that 0.2.0
cannot read.

**Limits.** The checks are a barrier, not a sandbox: they read the agent's
commands as text, so an agent set on getting around them with shell commands
could. OpenCode is checked only after the run. Use a backed-up copy of the
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
selected for review, and submitting the tasks takes along the result records
that the changed tasks link, so that an approved task never points at a record
the official project lacks. Check that selection, explain the change and send
it. Approval applies it to the official project. On the PI's page, a note or a
result record says it goes to the members with the next **Publish**. A Mac that
is not a member of the project says so, and the project can only be read there.
A single-person project saves directly.
