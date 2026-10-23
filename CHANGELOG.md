# Changelog

## Unreleased

- Make the engine ask for its own session before it saves anything. A program
  running on your Mac, an AI agent's script included, can no longer save,
  approve or publish by calling Research OS's local address: the app, and the
  browser tab `researchos serve` opens, carry the session; a window opened
  from the plain address still reads, and says it cannot save. After updating,
  run `researchos app` again: an app built before this change opens the plain
  address and cannot save until it is rebuilt (the window says so). The
  launcher-script version of the app, built when Xcode's command line tools
  are missing, opens the page with the session too.
- Keep an AI agent from reading the engine's secret and from preparing the next
  run. Claude Code's Read, Glob, Grep and LS are refused inside Research OS's
  folder and `~/.ssh`, as is a Bash command that names them; the agent program
  is not told where Research OS keeps its files and gets none of the
  environment variables whose names look like credentials (the run log lists
  the names it was not given). Claude Code no longer loads the project's own
  `.claude/settings*.json` (the project's `CLAUDE.md` is still read), and
  `.claude/`, `.opencode/` and `opencode.json`
  can neither be written by a run nor changed without the run reading Went out
  of bounds. Undo now says a file has no copy when it has none, instead of
  saying you kept it; a run whose folder was not there when Research OS
  started is compared when the folder is back; deleting backups cannot hide
  behind one newly planted backup name.
- Tighten the leftovers of the agent checks. An agent may not change or delete
  existing `.sqlite`, `.db`, `.npy`, `.npz`, `.mat`, `.sas7bdat`, `.jsonl` or
  `.pickle` files either, while `package.json` and `package-lock.json` (which
  `npm install` writes) are no longer counted as data. OpenCode is stopped from
  git commands that do not start with `git` (`/usr/bin/git push`,
  `cd x && git commit`). The run log tells a refused step, an error and a run
  that failed in your language, with the program's own words behind
  Details, and a run state the page does not know no longer shows its
  raw code. A program's `stderr.log` is capped at 20 MB, as the event log is.
- Stop calling two different things 判断. An attempt's verdict stays 研究判断
  ("Research assessment"), so the proposal that changes it reads 修改研究判断
  and 提议修改研究判断. A person's confirming or rejecting of a machine link is
  now 连线确认 ("Link judgments") on the node page, the 待复核 list and its
  count, the desk, the canvas link card and every notice about the record file.
  The count in the top bar and the desk's line also hold the variable cards of a
  changed script, so they say "N 项待复核" ("N items need review") and the list's
  subtitle names the cards beside the links. 研究判断 is also the word where the
  proposal's conflict line, the instruction sent to an agent, the 记录 panel, the
  attempt form and `researchos history` said 结论 or 判决 ("verdict").
  Nothing on disk changes: `judgements.toml`, its header and the commands keep
  their names.
- Settle the single words. 正式版本 is "Official version" on every screen (the
  English said "Published version" in places), 官方的一份 is 正式的一份, "official
  copy", and "publish" is only the act. 本机 is "This Mac", never "this
  computer". The experiment kind that MLflow and W&B bring is 实验
  ("Experiment") on the node page, no longer 实验记录 ("Run record"). A script's
  outputs are 输出 where they were 产出. The page 成果 is now 图表与论断
  ("Figures and claims"): the sidebar, the page titles, the app's View menu and
  every sentence that names it. 项目首页's section for result documents is
  "Result records" in English. A result document with no heading is titled
  结果记录 ("Result record"), no longer the attempt cell's 结果 ("Result").
  Nothing on disk changes: the command name is still `results`, and the header
  of `papers.toml` keeps its words.
- Stop calling two different things 笔记. What you write on an object (a script,
  a data file, an attempt, a figure) is 备注 ("Comment") on the node page as it
  already was in the route map's panel, and the node page now uses the panel's
  sentences (添加备注, 写一条备注…). The English said "Note" for these comments
  in places; it says "Comment" and "Comments", and the section is "Changes and
  comments". 笔记 ("Note") is only a document in 项目首页. A proposal shows the
  备注 of a line you drew as 备注, where it said 说明. Nothing on disk changes:
  `notes.toml`, its header and the `researchos note` command keep their names.
- Keep the glossary true. DESIGN 13.4's table now lists the words as the page
  speaks them (项目首页, 连线确认, 研究判断, 备注, 笔记, 图表与论断, 正式版本,
  "Relationship map", "Add a source", "Missing attempt" and the rest), and a new
  test, `tests/test_glossary.py`, holds the English table to it: a word of the
  glossary that is a key carries exactly the English written there, and a
  name such as 连线确认 reads the same in every sentence that has it. The
  English said "relation map" in a few sentences (where a confirmation was made,
  whose arrangement stays their own); it now says "relationship map" everywhere.
- Tidy the words of the page. In English, a route abandoned by you reads
  "Abandoned yesterday 14:02; recorded the state of 4 files" as one sentence, a
  proposed change of a research assessment names the two assessments inside one
  sentence, and 提交提议 (Submit a proposal) is in English with its kinds and
  fields. 项目首页 is fully translated: its buttons, fields, run states and
  refusals, and its two disclosures (引用文件或尝试, 详情) draw the same quiet
  chevron as the rest of the page instead of the browser's triangle. 49
  English sentences that no screen uses any more are gone. The check for
  arrows and emoji now also refuses », ⟶, ‹, ›, ⏵, ● and ○, and reads the
  stylesheets. Nothing on disk changes.
- Make 研究路线 one page. The route map (the old 工作台) and the list of
  attempts grouped by status are two ways of looking at the same page, switched
  by 路线图 / 列表 ("Route map" / "List") under the title; both show the 待办
  column beside them, and a route opened from the list opens the same side panel
  as a card on the map. The choice is remembered for each project in this
  browser and starts on the map. The sidebar has one item for the page, and in a
  project with a team it shows how many things wait in 待办 when there are any
  (a project of one person shows none). The View menu has one 研究路线 item, ⌘2,
  and 项目首页 is ⌘1, so the shortcuts follow the sidebar; an app built earlier
  still opens the page from both of its old menu items. After updating, run
  `researchos app` again to get the new menu. Nothing on disk changes.
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
  the model: Claude Code's calls are checked before each step by a check that
  refuses whenever it cannot decide, and every run is compared with the folder
  as it was before. A change that gets past it is shown first in the run as an
  out-of-bounds change.
- Answer, send back or undo. An agent that asks a question stops and shows
  Waiting for your answer; Answer resumes the same conversation. Send back
  reopens a finished task with your instructions. Undo this run moves the files
  the run created aside, restores the ones it changed from copies taken before
  it started (files up to 16 MB, for the newest 20 runs), and leaves anything
  you changed afterwards as it is; a run that could not be fully undone reads
  Partly undone.
- Undo this run now says what it will do and asks first. It lists the files it
  will put back and the files it will move away, each with its time and a
  checked box, and the files it cannot undo with the reason; clear a box to
  keep that file, and the run log lists it as "You kept this". A folder that
  existed before the run is never removed. The confirmation before your first
  run of an agent now says what an undo cannot bring back (installed software,
  downloads, git, anything outside the project folder) and names the project's
  files over 16 MB, which are not backed up. When Research OS starts, runs that
  an earlier session left going, in any project, are recovered at once.
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
- `research/tasks.json` may now carry a `report` field on a task, which
  Research OS 0.2.0 refuses to read. Update every team member's Research OS
  before assigning tasks to agents.


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
