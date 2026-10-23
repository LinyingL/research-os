"""What an agent is told (DESIGN.md 17.4, 17.5): the rules and the instruction.

Two texts go to the program, and both are plain functions of their inputs so
they can be read, tested and shown in the run log exactly as sent
(`instruction.txt`, 17.4):

- `rules` -- the list of 17.5 in plain words, addressed to the agent, and how
  to report (the tools of 17.4 under the names the runner shows the model).
  Claude Code takes it in `--append-system-prompt`; OpenCode has no such
  flag, so the manager puts it in front of the instruction on standard input.
  It is a statement of the rules, not their enforcement: the hook, the
  permission lists, the environment variable and the after-run check hold
  them whether or not the model reads this (17.5). What is said about
  enforcement depends on the runner: Claude Code's hook refuses every listed
  action and records the attempt; OpenCode has no hook, so only git and
  `researchos` / `rce` commands are blocked at call time and the rest is
  compared after the run.
- `instruction` -- the task and its date, the project, its research question
  and plan (`research/overview.md`, at most `OVERVIEW_LIMIT` characters, the
  cut marked) and the titles and paths of the notes and result records.
- `continuation` -- what 回答 (answer) and 打回 (send back) send to resume
  the same conversation (17.3).

Chinese or English by the language of the person who pressed 运行 (17.4;
`lang` is `"en"` or anything else for Chinese, as `settings.say` reads it).
The agent's report and notes are written in that language too. Like every
sentence Research OS writes, these carry no emoji and no arrow characters
(12.11); the person's own words (the task, the overview, titles) are passed
through as they are.

Rule 4 names the git commands from the hook's own list (`guard.GIT_MUTATING`, the
list Claude Code's denials and OpenCode's patterns are made from too, 18.1 item 6),
so that the words and the enforcement cannot drift apart.

Pure: nothing here reads a file or the environment.
"""

from __future__ import annotations

import re
from typing import Iterable, Mapping

from researchos.agents import guard

OVERVIEW_LIMIT = 8000
DOCUMENT_LIST_LIMIT = 50
#: Protected data files named one by one in the rules; the rest are counted.
PROTECTED_LIST_LIMIT = 30
#: The task file (snapshot.TASKS_PATH): spelled here so this module needs no
#: other. The agent never edits it directly (17.5 rule 5).
TASKS_PATH = "research/tasks.json"

_RUNNERS = ("claude-code", "opencode")
_TOOL_PREFIX = {"claude-code": "mcp__researchos__", "opencode": "researchos_"}
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
_SPACE_RE = re.compile(r"\s+")


def tool_name(runner: str, tool: str) -> str:
    """The name of one of Research OS's agent tools as the model sees it
    (17.4): `mcp__researchos__<tool>` for Claude Code, `researchos_<tool>`
    for OpenCode, where `<tool>` is the tool's own name
    (`researchos_complete_task`). A short name without the `researchos_`
    prefix (`complete_task`) is accepted and prefixed."""
    if runner not in _TOOL_PREFIX:
        raise ValueError(f"unknown runner: {runner!r}")
    if not isinstance(tool, str) or not tool.strip():
        raise ValueError("tool name is empty")
    tool = tool.strip()
    if not tool.startswith("researchos_"):
        tool = "researchos_" + tool
    return _TOOL_PREFIX[runner] + tool


def _is_en(lang: object) -> bool:
    return lang == "en"


def _line(text: object) -> str:
    """One display line: control characters dropped, whitespace collapsed."""
    return _SPACE_RE.sub(" ", _CONTROL_RE.sub(" ", str(text if text is not None else ""))).strip()


def _protected_data(prot: Mapping[str, object]) -> list[str]:
    """The protected data files, sorted. `prot["data"]` when the snapshot
    gave it; otherwise `files` less the records (older/other shapes)."""
    data = prot.get("data") if isinstance(prot, Mapping) else None
    if isinstance(data, (list, tuple, set, frozenset)):
        items = list(data)
    else:
        records = set(_protected_records(prot))
        files = prot.get("files") if isinstance(prot, Mapping) else None
        items = [f for f in files if f not in records] if isinstance(files, (list, tuple, set, frozenset)) else []
    return sorted({_line(i) for i in items if isinstance(i, str) and _line(i)})


def _protected_records(prot: Mapping[str, object]) -> list[str]:
    records = prot.get("records") if isinstance(prot, Mapping) else None
    if not isinstance(records, (list, tuple)):
        return []
    return [_line(r) for r in records if isinstance(r, str) and _line(r)]


def _attempt_table(prot: Mapping[str, object]) -> str | None:
    """The attempt table's path: the record that is not the task file."""
    for rel in _protected_records(prot):
        if rel != TASKS_PATH:
            return rel
    return None


def _git_verbs(separator: str) -> str:
    """The git commands rule 4 forbids, every one the hook refuses (`guard.GIT_MUTATING`), sorted so that the text
    is the same on every start. A command added to that list is in the rules the agent is sent with no second edit
    here."""
    return separator.join(sorted(guard.GIT_MUTATING))


def _code_list(paths: Iterable[str]) -> str:
    return ", ".join(f"`{p}`" for p in paths)


def rules(*, lang: str, runner: str, prot: dict) -> str:
    """The binding rules of 17.5 in plain words, and how to finish (17.4).

    `prot` is `snapshot.protected(...)`: its `data` files (up to
    `PROTECTED_LIST_LIMIT` named by path, then a count) and its attempt
    table are named so the agent knows exactly what is off limits."""
    complete = tool_name(runner, "researchos_complete_task")
    ask = tool_name(runner, "researchos_ask")
    note = tool_name(runner, "researchos_save_note")
    find = tool_name(runner, "researchos_find")
    trace = tool_name(runner, "researchos_trace")
    status = tool_name(runner, "researchos_status")
    table = _attempt_table(prot)
    data = _protected_data(prot)
    shown, more = data[:PROTECTED_LIST_LIMIT], max(0, len(data) - PROTECTED_LIST_LIMIT)
    hooked = runner == "claude-code"  # only Claude Code has a hook that refuses every listed action (17.5)

    if _is_en(lang):
        table_words = f"the attempt table (`{table}`)" if table else "the attempt table"
        if shown:
            files_words = ("Data files that existed before this run and are therefore protected: "
                           + _code_list(shown) + (f", and {more} more." if more else "."))
        else:
            files_words = "No data file of the project is protected by name, but the rule holds for any that exists."
        return "\n".join([
            "You are carrying out a task for a researcher through Research OS. You act on the person's "
            "instruction, as an errand-runner: do the work directly in the working folder, which is the "
            "project folder. You may write and edit code and notes, run Python, R or Stata, install "
            "packages and download data.",
            "",
            ("You must never do the following. Research OS refuses these and records the attempt; a refused "
             "call is not a reason to try another way round." if hooked else
             "You must never do the following. Research OS blocks git commands and `researchos` or `rce` "
             "commands the moment you call them; everything else is compared with the project after the run, "
             "listed as out of bounds, and can be undone. So do not do any of these things at all, whether "
             "or not a call is blocked."),
            f"1. Make a judgment that belongs to the person: an attempt's verdict or result in {table_words}, "
            "confirming or rejecting a link, confirming a variable definition, or anything inside "
            "`.researchos/`.",
            "2. Act for the team: propose, approve or return a proposal, publish, sync, or push to GitHub.",
            "3. Change or delete a data file that existed before this run (.csv, .tsv, .xlsx, .xls, .parquet, "
            ".feather, .rds, .rdata, .dta, .sav, .json, .h5, .pkl, .sqlite, .db, .npy, .npz, .mat, "
            ".sas7bdat, .jsonl, .pickle; package.json and package-lock.json are not data). Write new "
            "files instead. " + files_words,
            "4. Change git's state, with any of these git commands: " + _git_verbs(", ") + ". Deleting or "
            "renaming a branch counts too. Reading it (status, diff, log) is fine.",
            f"5. Edit `{TASKS_PATH}` directly. The tools below mark the task done.",
            "6. Run the `researchos` or `rce` command, or contact the Research OS app in any other way.",
            "",
            "How to finish:",
            f"- When the task is done, call `{complete}` with a Markdown report: what you did, the commands "
            "you ran, the files you produced, and the key numbers exactly as your actual runs produced them. "
            "Never invent or estimate a number, a file or a result. Give it a short title naming the result "
            "(a few words). This call also marks the task done.",
            f"- If you are blocked, or a decision belongs to the person, call `{ask}` with one clear "
            "question and then stop. Their answer continues this conversation.",
            f"- To keep a note for the project, call `{note}` with a title and the content.",
            f"- To look things up in the project's index you may use `{find}`, `{trace}` and `{status}`; they "
            "only read.",
            "- Write the report and any note in English.",
        ])

    table_words = f"尝试表（`{table}`）" if table else "尝试表"
    if shown:
        files_words = ("运行开始前就存在、因此受保护的数据文件：" + _code_list(shown)
                       + (f"，以及另外 {more} 个。" if more else "。"))
    else:
        files_words = "项目里没有按名字列出的受保护数据文件，但这条规则对任何已存在的数据文件都适用。"
    return "\n".join([
        "你在替一位研究者通过 Research OS 完成一项任务。你是按这个人的指示办事的执行者：直接在工作文件夹"
        "（也就是项目文件夹）里把事情做完。你可以写、改代码和笔记，运行 Python、R 或 Stata，安装软件包，"
        "下载数据。",
        "",
        ("下面这些事绝对不能做。Research OS 会拒绝它们并记下这次尝试；被拒绝之后不要换个办法再试。" if hooked else
         "下面这些事绝对不能做。git 命令以及 `researchos`、`rce` 命令在你调用的那一刻就会被拦下；其余的事在运行结束后"
         "会与项目逐一比对，列为越界，并且可以被撤回。所以这些事根本不要做，不管某次调用有没有被拦下。"),
        f"1. 不替人做判断：不要写尝试的判决或结果（{table_words}），不要确认或否定任何连接，"
        "不要确认变量定义，也不要碰 `.researchos/` 里的任何东西。",
        "2. 不替团队行事：不要提议，不要批准或退回提议，不要发布、同步，也不要推送到 GitHub。",
        "3. 不修改、不删除运行开始前就存在的数据文件（.csv、.tsv、.xlsx、.xls、.parquet、.feather、.rds、"
        ".rdata、.dta、.sav、.json、.h5、.pkl、.sqlite、.db、.npy、.npz、.mat、.sas7bdat、.jsonl、.pickle；"
        "package.json 和 package-lock.json 不算数据文件）；需要新数据时写新文件。" + files_words,
        "4. 不改变 git 的状态：下面这些 git 命令一概不用：" + _git_verbs("、") + "；也不删除或重命名分支。"
        "只读的操作（status、diff、log）可以。",
        f"5. 不直接编辑 `{TASKS_PATH}`；任务的完成标记由下面的工具写入。",
        "6. 不运行 `researchos` 或 `rce` 命令，也不以任何其他方式联系 Research OS 应用。",
        "",
        "怎样收尾：",
        f"- 任务完成后，调用 `{complete}`，参数 report 是一份 Markdown 报告：做了什么、运行过的命令、"
        "产出的文件，以及关键数字，数字必须原样取自你真实运行的结果。绝不编造或估算数字、文件或结论。"
        "再给一个几个词的简短标题，说明这是什么结果。这一次调用同时把任务标为完成。",
        f"- 如果做不下去，或需要这个人来决定，调用 `{ask}`，提出一个清楚的问题，然后停下。"
        "他们的回答会接上这段对话。",
        f"- 想给项目留一条笔记，调用 `{note}`，给出标题和内容。",
        f"- 想查项目的索引，可以用 `{find}`、`{trace}` 和 `{status}`；它们只读。",
        "- 报告和笔记用中文写。",
    ])


def _cut_overview(overview: str, limit: int = OVERVIEW_LIMIT) -> tuple[str, bool]:
    """The overview cut to `limit` characters, at a line boundary when one
    falls in the second half of the allowance (else mid-line); and whether
    anything was cut."""
    if len(overview) <= limit:
        return overview, False
    head = overview[:limit]
    boundary = head.rfind("\n")
    if boundary >= limit // 2:
        head = head[:boundary]
    return head.rstrip(), True


def _kind_word(kind: object, en: bool) -> str:
    if kind == "result":
        return "result record" if en else "结果记录"
    return "note" if en else "笔记"


def instruction(*, lang: str, task: dict, project_name: str, overview: str | None,
                documents: list[dict]) -> str:
    """The instruction for a first run (17.4): the task and its date, the
    project, its research question and plan, the notes and result records.

    `task` is `{"text", "due"}`; `overview` the content of
    `research/overview.md` or None; `documents` `[{"path", "kind", "title"}]`
    (`kind` is `note` or `result`; the first `DOCUMENT_LIST_LIMIT` are
    listed, the rest counted). The rules are separate (`rules`)."""
    en = _is_en(lang)
    text = str(task.get("text") or "").strip() if isinstance(task, Mapping) else ""
    due = _line(task.get("due")) if isinstance(task, Mapping) else ""
    name = _line(project_name)
    parts: list[str] = []

    if en:
        parts += ["Please carry out the following task.", "", "## The task", "", text or "(no text)"]
        if due:
            parts += ["", f"Due date: {due}"]
        parts += ["", "## The project", "", name or "(unnamed)",
                  "The working folder is the project folder."]
        parts += ["", "## Research question and plan (research/overview.md)", ""]
    else:
        parts += ["请完成下面这项任务。", "", "## 任务", "", text or "（没有内容）"]
        if due:
            parts += ["", f"截止日期：{due}"]
        parts += ["", "## 项目", "", name or "（未命名）", "工作文件夹就是项目文件夹。"]
        parts += ["", "## 研究问题与计划（research/overview.md）", ""]

    body = overview.strip() if isinstance(overview, str) else ""
    if body:
        shown, cut = _cut_overview(body)
        parts.append(shown)
        if cut:
            parts.append("")
            parts.append(f"[Cut here at {OVERVIEW_LIMIT} characters; read research/overview.md for the rest.]"
                         if en else f"[内容在 {OVERVIEW_LIMIT} 个字符处截断；其余部分请读 research/overview.md。]")
    else:
        parts.append("(There is no overview file yet.)" if en else "（还没有概述文件。）")

    parts += ["", "## Notes and result records already in the project" if en else "## 项目里已有的笔记和结果记录", ""]
    docs = [d for d in (documents or []) if isinstance(d, Mapping) and _line(d.get("path"))]
    if not docs:
        parts.append("(None yet.)" if en else "（还没有。）")
    else:
        for doc in docs[:DOCUMENT_LIST_LIMIT]:
            path, title, word = _line(doc.get("path")), _line(doc.get("title")), _kind_word(doc.get("kind"), en)
            if en:
                parts.append(f"- `{path}` ({word})" + (f": {title}" if title else ""))
            else:
                parts.append(f"- `{path}`（{word}）" + (f"：{title}" if title else ""))
        extra = len(docs) - DOCUMENT_LIST_LIMIT
        if extra > 0:
            parts.append(f"- ... and {extra} more." if en else f"- 以及另外 {extra} 项。")

    parts += ["", "Read whatever you need from the project, do the work, and finish as the rules say."
              if en else "需要什么就去项目里读，把事做完，并按规则收尾。"]
    return "\n".join(parts) + "\n"


def continuation(*, lang: str, kind: str, message: str) -> str:
    """What resumes a conversation (17.3): `kind` `"answer"` (the person
    answered the agent's question) or `"send_back"` (the person sent the
    task back with instructions). Each reminds the agent how to finish."""
    if kind not in ("answer", "send_back"):
        raise ValueError(f"unknown continuation kind: {kind!r}")
    words = str(message if message is not None else "").strip()
    if _is_en(lang):
        head = ("The person answered your question:" if kind == "answer"
                else "The person sent the task back with these instructions:")
        tail = ("Carry on with the task. When it is done, finish by calling the complete tool with your "
                "report; if you need another decision from the person, call the ask tool with one clear "
                "question and stop.")
    else:
        head = "这个人回答了你的问题：" if kind == "answer" else "这个人把任务打回了，要求如下："
        tail = "请继续完成任务。完成后调用完成工具提交报告；如果还需要这个人决定，调用提问工具，提出一个清楚的问题，然后停下。"
    return f"{head}\n\n{words}\n\n{tail}\n"
