"""The glossary stays true (DESIGN.md 13.4, amended by 18.2; task V14).

13.4's table and the lists of words in 13.4's paragraph below it, 14.6, 15.5,
17.9 and 19.9 are the binding English of the interface. These tests read them out
of DESIGN.md and hold the English table in /i18n.js to them: a Chinese word
that is a key there carries exactly the English the design writes, and the
names that sentences carry read the same inside every sentence -- "a term
means one thing everywhere". When the design renames a word, the row changes
first and these tests then show every place the page still says the old one.
Every test names its scenario."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from test_v9_language import APP_HTML, CJK, FORBIDDEN, WEBAPP, _node, english_table, needs_node

DESIGN = Path(__file__).parents[1] / "DESIGN.md"

#: A word of one of the design's lists: a Chinese term (with a leading
#: capitalised ASCII token as in "AI 执行者", an aside in brackets and "as a
#: role" allowed), then its English in straight double quotes.
LISTED_WORD = re.compile(r'((?:[A-Z]{2,} )?[一-鿿][一-鿿\w]*)(?: \([^)]*\))?(?: as a role)? "([^"]+)"')

#: The sections whose lists of words are read (each a stretch of DESIGN.md
#: from its heading to the next one).
LISTS = [("13.4", "### 13.4", "### 13.5"), ("14.6", "### 14.6", "### 14.7"),
         ("15.5", "### 15.5", "### 15.6"), ("17.9", "### 17.9", "### 17.10"),
         ("19.9", "### 19.9", "### 19.10")]


def _section(head: str, nxt: str) -> str:
    text = DESIGN.read_text(encoding="utf-8")
    start = text.index(head)
    return text[start:text.index(nxt, start)]


def table_rows() -> list[tuple[str, str]]:
    """13.4's table as written: (Chinese cell, English cell), the header and
    the rule line left out."""
    rows = re.findall(r"^\| (.+?) \| (.+?) \|$", _section("### 13.4", "### 13.5"), re.M)
    assert rows[0] == ("中文", "English")
    return rows[1:]


def table_pairs() -> list[tuple[str, str]]:
    """The table's words one by one: a row `A / B / C` is three words in the
    order written, on both sides."""
    pairs = []
    for zh, en in table_rows():
        zh_parts, en_parts = zh.split(" / "), en.split(" / ")
        assert len(zh_parts) == len(en_parts), f"{zh} | {en}: the two sides give a different number of words"
        pairs += zip(zh_parts, en_parts)
    return pairs


def listed_pairs() -> list[tuple[str, str, str]]:
    """The words the lists write as `中文 "English"`: 13.4 below its table,
    14.6, 15.5, 17.9 and 19.9 -- (section, Chinese, English)."""
    found = []
    for name, head, nxt in LISTS:
        text = _section(head, nxt)
        if name == "13.4":
            text = text[text.index("\n\n", text.rindex("\n| ")):]  # below the table
        found += [(name, zh, en) for zh, en in LISTED_WORD.findall(re.sub(r"\s+", " ", text))]
    return found


def entry_forms(entry: Any) -> set[str]:
    """The English a table entry holds: its text, or both forms of a plural."""
    return {entry} if isinstance(entry, str) else set(entry.values())


# -- the glossary itself --------------------------------------------------------------------------------------


def test_the_table_is_read_whole_and_its_words_are_plain_english_and_unique():
    """The table parses to the rows the design shows; every Chinese cell has
    Chinese and every English cell none; no arrow or emoji (12.11); a Chinese
    word is given once, so it has one English."""
    rows = table_rows()
    assert len(rows) >= 45
    for zh, en in rows:
        assert CJK.search(zh) and not CJK.search(en), f"{zh} | {en}"
        assert not FORBIDDEN.search(en), en
        assert en.strip() == en and '"' not in en, en
    zhs = [zh for zh, _ in table_pairs()]
    assert len(zhs) == len(set(zhs)), sorted(w for w in set(zhs) if zhs.count(w) > 1)


#: 18.2's words (and 13.4's gains): each is a row, as written there.
TERMS_OF_18_2 = {
    "连线确认": "Link judgments", "关系图": "Relationship map", "研究判断": "Research assessment",
    "补上来源": "Add a source", "缺尝试": "Missing attempt",
    "正式版本": "Official version", "改动与备注": "Changes and comments", "备注": "Comment", "笔记": "Note",
    "图表与论断": "Figures and claims", "输出": "Outputs", "实验": "Experiment",
    "本机": "This Mac", "项目首页": "Project home", "负责人": "Owner", "任务": "Task",
    "中间数据": "Intermediate data", "同名文件": "Same-name files",
}

#: 19.9's words (with 19.12 item 1's 过程文件夹), in the order the design writes them: they are in a list, not in the
#: table, and held to the page as 17.9's are.
TERMS_OF_19_9 = {
    "过程文件": "Process files", "过程文件夹": "Process folder", "登记": "Register", "未登记": "Not registered",
    "移除登记": "Remove registration", "补登记以往运行的文件": "Register files from earlier runs",
    "登记所选": "Register selected", "不登记": "Do not register", "运行占用的空间": "Space used by runs",
    "撤回备份": "Undo backups", "撤回时移走的文件": "Files moved away by undo", "释放所有撤回备份": "Release all undo backups",
    "删除这些文件": "Delete these files", "任务已移除": "Task removed", "空间上限": "Space limit", "会话编号": "Session ID",
}

#: Words 18.2 retired, on either side: none of them may stand in the table
#: (a Chinese word is exact, an English word is a whole cell of a row).
RETIRED_CHINESE = ["判断", "判决", "成果", "官方版本", "产出", "实验记录", "工作台"]
RETIRED_ENGLISH = ["Judgments", "Verdict", "Relation map", "Published version", "Add source", "No attempt",
                   "Changes & notes", "Run record", "Link judgment", "Desk"]

#: 18.3's switch on 研究路线. Not 图: that key is "Figure" (the kind label of a figure on 图表与论断), and a Chinese word has one English.
TERMS_OF_18_3 = {"路线图": "Route map", "列表": "List"}


def test_the_table_names_the_terms_of_18_2_and_none_it_retired():
    """18.2's renames and additions are rows as written there; the retired
    words are gone from both sides of the table."""
    pairs = dict(table_pairs())
    for zh, en in TERMS_OF_18_2.items():
        assert pairs.get(zh) == en, f"{zh}: the table gives {pairs.get(zh)!r}, 18.2 gives {en!r}"
    assert [zh for zh in RETIRED_CHINESE if zh in pairs] == []
    assert [en for en in RETIRED_ENGLISH if en in pairs.values()] == []


@needs_node
def test_the_switch_of_the_one_research_routes_page_has_its_two_words_and_no_second_english_for_a_figure():
    """18.3: 研究路线's switch reads 路线图 / 列表, rows of the table; the page's
    English table holds exactly those words for them, and 图 stays "Figure"
    (the kind label of a figure on 图表与论断), so the switch cannot have taken
    the bare 图 for the map."""
    pairs = dict(table_pairs())
    table = english_table()
    for zh, en in TERMS_OF_18_3.items():
        assert pairs.get(zh) == en, f"{zh}: the table gives {pairs.get(zh)!r}, 18.3 gives {en!r}"
        assert table.get(zh) == en, f"{zh}: the page says {table.get(zh)!r}"
    assert "图" not in pairs and table["图"] == "Figure"


def test_the_lists_are_read_and_give_a_word_once_with_one_english():
    """13.4's paragraph below the table, 14.6, 15.5, 17.9 and 19.9 yield their
    words -- the ones the sections show -- and no word has two Englishes
    within the lists and the table together (成员 as a role is 'Member', its
    table row 'Members': one word, two forms)."""
    listed = listed_pairs()
    by_section: dict[str, list[str]] = {}
    for name, zh, _en in listed:
        by_section.setdefault(name, []).append(zh)
    assert {"等你批准", "发布"} <= set(by_section["13.4"])
    assert by_section["14.6"] == ["源自", "由它而来", "路线图", "还没标来处", "有改动", "待办"]
    assert by_section["15.5"] == ["全局变量", "展开不了", "项目里的文件夹"]
    assert {"AI 执行者", "撤回本次运行", "越过了限制"} <= set(by_section["17.9"])
    assert by_section["19.9"] == list(TERMS_OF_19_9)     # 19.9, as 17.9: its words in the order written, none missed by the reader
    given: dict[str, set[str]] = {}
    for zh, en in [*table_pairs(), *[(zh, en) for _, zh, en in listed]]:
        given.setdefault(zh, set()).add(en)
    for zh, forms in given.items():
        # A plural and its singular, or a word that wears an extra qualifier, is the same word; two
        # unrelated Englishes are not.
        stems = {re.sub(r"s$", "", form.lower()) for form in forms}
        assert len(stems) == 1, f"{zh}: {sorted(forms)}"


#: 21.2's words (the handback), listed in 17.9 and in 21.6's table: each is a key of the page's English table.
TERMS_OF_21_2 = {"交回": "Handback", "代理建议": "Suggested by the agent", "确认这个判断": "Confirm this assessment", "报告": "Report",
                 "运行日志": "Run log"}

# -- the English table holds to it -----------------------------------------------------------------------------


def keys_off_the_glossary(table: dict[str, Any]) -> tuple[list[str], int]:
    """Each glossary word that is a key of `table` and whose entry is not the
    design's English (or, for a counted word, one of its two forms); and how
    many words were compared."""
    pairs = [("13.4 table", zh, en) for zh, en in table_pairs()] + listed_pairs()
    wrong, compared = [], 0
    for where, zh, en in pairs:
        if zh not in table:
            continue
        compared += 1
        if en not in entry_forms(table[zh]):
            wrong.append(f"{where}: {zh!r} is {table[zh]!r} on the page, {en!r} in the design")
    return wrong, compared


@needs_node
def test_every_glossary_word_that_is_a_key_carries_exactly_the_glossarys_english():
    """For each word of 13.4's table and of the lists in 13.4, 14.6, 15.5,
    17.9 and 19.9 that is a key of the English table, the entry equals the design's
    English -- or, for a counted word, is one of its two forms. (A word that
    is no key of its own -- it lives inside sentences, or belongs to a page
    not yet converted, like 含义 to 确认这一版 -- is not compared here.) The
    count guards the parser: a design edit that makes it read nothing must
    not let this pass."""
    wrong, compared = keys_off_the_glossary(english_table())
    assert wrong == []
    assert compared >= 85, compared                       # 19.9 adds thirteen words that are keys of their own


@needs_node
def test_19_9s_words_are_the_ones_the_design_gives_and_the_page_holds_those_that_are_keys_and_the_sentences_that_carry_the_rest():
    """DESIGN 19.9 (with 19.12 item 1's 过程文件夹): the list is read as written; each word that is a key of the English
    table carries its English, and the three words that are no key of their own (登记, 未登记, 会话编号) are carried by the
    sentences of the page in the design's English, so a word the page says otherwise is found here."""
    given = {zh: en for name, zh, en in listed_pairs() if name == "19.9"}
    assert given == TERMS_OF_19_9
    table = english_table()
    keys = [zh for zh in given if zh in table]
    assert len(keys) == 13 and {"登记", "未登记", "会话编号"} == set(given) - set(keys), keys
    assert [zh for zh in keys if given[zh] not in entry_forms(table[zh])] == []
    # the words that live inside sentences: every English form of every key that carries them holds the design's English
    carried = {"登记": "regist", "未登记": "not registered", "会话编号": "session id"}      # register, registration, registered
    said = {zh: [k for k in table if zh in k] for zh in carried}
    for zh, keys_with in said.items():
        assert keys_with, zh
        for key in keys_with:
            for form in entry_forms(table[key]):
                assert carried[zh] in form.lower(), f"{zh}: {key!r} -> {form!r}"
    assert "工作文件夹" not in table and table["过程文件夹"] == "Process folder"          # the old word for a run's folder is gone


#: The names that stay the same inside every sentence that carries them,
#: each with the stem of its English (lower case, hyphens read as spaces),
#: which the design's row must itself contain. A sentence is free to be
#: built any way, but not to call 连线确认 something else.
NAMED_IN_SENTENCES = {
    "研究判断": "research assessment", "连线确认": "link judgment", "正式版本": "official",
    "关系图": "relationship map", "图表与论断": "figures and claims", "改动与备注": "changes and comments",
    "项目首页": "project home", "报告": "report", "同名文件": "same-name file",
    "备注": "comment", "笔记": "note", "任务": "task", "实验": "experiment", "输出": "output",
    "本机": "this mac", "公盘": "shared folder", "工作副本": "working copy", "来龙去脉": "provenance",
    "研究路线": "research route",
}


@needs_node
def test_a_named_term_reads_the_same_in_every_sentence_that_carries_it():
    """DESIGN 13.4: every English form of every key that contains one of
    these Chinese names holds its English (研究判断 is always a 'research
    assessment', never a 'verdict'; 关系图 always a 'relationship map')."""
    table = english_table()
    pairs = dict(table_pairs())
    drifted = []
    for zh, stem in NAMED_IN_SENTENCES.items():
        assert stem in pairs[zh].lower(), f"{zh}: the design's row {pairs[zh]!r} does not hold {stem!r}"
        for key, entry in table.items():
            if zh in key:
                for form in entry_forms(entry):
                    if stem.replace("-", " ") not in form.lower().replace("-", " "):
                        drifted.append(f"{zh} ({stem}): {key!r} -> {form!r}")
    assert drifted == []


def test_the_comparison_tells_a_drifted_word_from_a_plural_and_skips_a_missing_key():
    """The check itself, on a made-up table: 连线确认 written "Judgments" (the
    retired English) is reported, 任务 holding both forms of a counted word
    and 研究判断 written right pass, and a word that is no key (实验记录) is
    skipped; and the reader of the lists takes a bracketed aside and "as a
    role" and leaves a word with no Chinese term (PI) alone."""
    wrong, compared = keys_off_the_glossary({
        "连线确认": "Judgments", "任务": {"one": "Task", "other": "Tasks"}, "研究判断": "Research assessment"})
    assert compared == 3
    assert len(wrong) == 1 and wrong[0].startswith("13.4 table: '连线确认' is 'Judgments' on the page, 'Link judgments'")
    assert keys_off_the_glossary({"实验": "Experiment"}) == ([], 1)
    assert keys_off_the_glossary({"实验记录": "Run record"}) == ([], 0)
    assert LISTED_WORD.findall('运行 (a run, and the button) "Run"; AI 执行 (history source) "By an agent"; '
                               '成员 as a role "Member"; PI stays "PI"') == [
        ("运行", "Run"), ("AI 执行", "By an agent"), ("成员", "Member")]


# -- the engine's own sentences ----------------------------------------------------------------------------------


def test_the_engines_sentences_call_an_attempts_cell_and_a_link_judgment_by_the_pages_words():
    """The conflict line on a proposal's panel (proposals.REASONS) and the
    instruction shown under 发给它的指示 (agents.brief.rules) are engine
    sentences, so the key-based checks above never see them. An attempt's
    verdict cell is 研究判断 / "research assessment" in both languages -- not 结论,
    判决 or "verdict" -- and a person's confirming or rejecting of a machine
    link is 连线确认 / "link judgment", not 连接. (The column header the
    researcher writes in the attempt table is theirs and is not read here.)"""
    from researchos import proposals
    from researchos.agents import brief

    zh, en = proposals.REASONS["verdict_moved"]
    assert zh == "第 {n} 行的研究判断或结果在正式版本里也改了"
    assert en == "Row {n}'s research assessment or result also changed in the official version"
    for code, (reason_zh, reason_en) in proposals.REASONS.items():
        assert "结论" not in reason_zh and "判决" not in reason_zh, code
        assert "verdict" not in reason_en.lower(), code
    prot = {"dirs": [".researchos", ".git"], "files": ["data/raw.csv", "research/attempts.md"], "data": ["data/raw.csv"],
            "records": ["research/attempts.md"]}
    for runner in ("claude-code", "opencode", "codex", "deepseek-harness"):
        text_zh, text_en = brief.rules(lang="zh", runner=runner, prot=prot), brief.rules(lang="en", runner=runner, prot=prot)
        rule_zh = next(line for line in text_zh.splitlines() if line.startswith("1. "))
        rule_en = next(line for line in text_en.splitlines() if line.startswith("1. "))
        assert "研究判断" in rule_zh and "连线确认" in rule_zh and "判决" not in text_zh and "连接" not in text_zh
        assert "research assessment" in rule_en and "link judgment" in rule_en and "verdict" not in text_en.lower()


def test_history_says_research_assessment_where_an_attempts_assessment_did_not_change():
    """`researchos history` words an attempt version that kept its cell as
    研究判断不变, as the page's own words (12.13) do -- not 结论不变."""
    from researchos import history

    same = {"verdict": "现行", "result": "", "sha256": "a"}
    assert history._attempt_change(same, dict(same))["summary"] == "研究判断不变（现行）"
    assert history._attempt_change(same, dict(same), words=True)["summary"] == "研究判断不变（「现行」）"


@needs_node
def test_the_records_panel_and_the_attempt_form_call_the_verdict_cell_the_research_assessment():
    """DESIGN 13.4 / 18.2: the 记录 panel lists the attempts record as 尝试的研究判断
    and counts the rows that have one as 有研究判断 -- not 结论 -- and the attempt
    form's label for the cell, where the project's header is missing, is 研究判断
    too (app.html's own code, under node)."""
    src = APP_HTML.read_text(encoding="utf-8")
    start = src.index("const RECORD_KIND_WORDS = {")
    kinds = src[start:src.index("const RECORD_ISSUE_WORDS", start)]
    count = src[src.index("function recordCountText(row) {"):]
    count = count[:count.index("\n}\n") + 3]
    script = (kinds + count + "\nprocess.stdout.write(JSON.stringify([RECORD_KIND_WORDS.attempts, "
              'recordCountText({ code: "attempts", facts: { state: "present", attempts: 7, with_verdict: 5 } })]));\n')
    assert _node(script) == ["尝试的研究判断", "7 条尝试，其中 5 条有研究判断"]
    assert "结论" not in kinds + count
    from test_v10_desk_panel import _form_run

    for lang, assessment in [("zh", "研究判断"), ("en", "Research assessment")]:
        shown = _form_run('''
delete state.attemptsConfig.columns.verdict;
renderAttemptForm("append");
global.extra={labels:panelBody.all().filter(node=>node.tag==="LABEL").map(node=>node.textContent)};
''', lang=lang)
        assert assessment in shown["extra"]["labels"]
        assert "结论" not in shown["extra"]["labels"]
    # 21.6: the table's own header for the column (「状态」 in the Quant table) only finds it; the form, new row or existing
    # row, calls the cell 研究判断 as the attempt panel does.
    from test_v10_desk_panel import A16

    for lang, assessment in [("zh", "研究判断"), ("en", "Research assessment")]:
        for call in ('renderAttemptForm("append");', f'renderAttemptForm("update", {A16!r});'):
            shown = _form_run('''
state.attemptsConfig.columns.verdict = "状态";
''' + call + '''
global.extra={labels:panelBody.all().filter(node=>node.tag==="LABEL").map(node=>node.textContent)};
''', lang=lang)
            assert assessment in shown["extra"]["labels"], (lang, call)
            assert "状态" not in shown["extra"]["labels"], (lang, call)


@needs_node
def test_21_2s_handback_words_are_listed_in_17_9_and_21_6_and_the_page_holds_them_as_the_design_writes_them():
    """DESIGN 21.2 / 21.6 (task V16 B1-3): 交回, 代理建议, 确认这个判断, 报告 and 运行日志 are in 17.9's list as written, in
    21.6's table (the English column holds each), and keys of the page's English table that carry exactly that English."""
    given = {zh: en for name, zh, en in listed_pairs() if name == "17.9" and zh in TERMS_OF_21_2}
    assert given == TERMS_OF_21_2
    text = DESIGN.read_text(encoding="utf-8")
    six = text[text.index("### 21.6"):text.index("### 21.7")]
    english_column = " ".join(re.findall(r"^\| .+? \| .+? \| (.+?) \|$", six, re.M))
    for zh, en in TERMS_OF_21_2.items():
        assert en in english_column, f"{zh}: {en!r} is not in 21.6's English column"
    table = english_table()
    # 21.4: 报告 also heads the list of reports, so its entry has a plural; the word 21.6 gives is its singular
    singular = lambda en: en["one"] if isinstance(en, dict) else en
    assert {zh: singular(table[zh]) for zh in TERMS_OF_21_2} == TERMS_OF_21_2


#: 21.4 / 21.6's words for the top of 项目首页 and the column: the table's English column gives 「In progress: Attempts in progress,
#: Needs you」 for 现在在做什么 with its two parts, and 「Needs you」 for 待办's new word.
TERMS_OF_21_4 = {"现在在做什么": "In progress", "进行中的尝试": "Attempts in progress", "等你处理": "Needs you"}


@needs_node
def test_21_4s_words_are_in_21_6_s_table_and_the_page_holds_them_as_the_design_writes_them_and_the_old_word_is_gone():
    """DESIGN 21.4 / 21.6 (task V16 B2-1): the three words are in 21.6's English column and are keys of the page's English
    table with exactly that English; 待办 is a key no more, and no screen string says it (the design's lists keep it as the
    word 14.6 introduced, which 21.6 replaces)."""
    text = DESIGN.read_text(encoding="utf-8")
    six = text[text.index("### 21.6"):text.index("### 21.7")]
    english_column = " ".join(re.findall(r"^\| .+? \| .+? \| (.+?) \|$", six, re.M))
    for zh, en in TERMS_OF_21_4.items():
        assert en in english_column, f"{zh}: {en!r} is not in 21.6's English column"
    table = english_table()
    assert {zh: table[zh] for zh in TERMS_OF_21_4} == TERMS_OF_21_4
    assert "待办" not in table and "{n} 项待办" not in table
    assert table["{n} 项等你处理"] == {"one": "{n} needs you", "other": "{n} need you"}
    for name in ("desk.js", "workspace.js", "app.html", "team.js"):
        shown = (WEBAPP / name).read_text(encoding="utf-8")
        assert not re.search(r"""t\(["']待办["']""", shown), name


#: 21.5 / 21.6's words for the provenance pages (task V16 B3-1): the warning that replaces 未识别生成脚本 and 没有找到生成它的脚本,
#: its action, the quiet text beside it, 归入尝试 for the file page and the question of a broken link.
TERMS_OF_21_5 = {"缺生成脚本": "No generating script", "归入尝试": "File under an attempt", "指定生成它的脚本": "Assign a generating script",
                 "没有生成脚本": "No script writes it", "是这个吗？": "Is it this one?", "添加文件": "Add files"}


@needs_node
def test_21_5s_words_are_in_21_6_s_table_and_the_page_holds_them_as_the_design_writes_them_and_the_old_words_are_gone():
    """DESIGN 21.5 / 21.6 (task V16 B3-1): each word is in 21.6's English column as written and is a key of the page's English
    table with exactly that English; the words it replaces on these screens (未识别生成脚本, 没有找到生成它的脚本, and 补上来源 as
    the figure's button) are keys no more and no screen string says them."""
    text = DESIGN.read_text(encoding="utf-8")
    six = text[text.index("### 21.6"):text.index("### 21.7")]
    english_column = " ".join(re.findall(r"^\| .+? \| .+? \| (.+?) \|$", six, re.M))
    for zh, en in TERMS_OF_21_5.items():
        assert en in english_column, f"{zh}: {en!r} is not in 21.6's English column"
        assert zh in six, f"{zh} is not in 21.6's table"
    table = english_table()
    assert {zh: table[zh] for zh in TERMS_OF_21_5} == TERMS_OF_21_5
    assert table["其他同名文件 {n} 个"] == {"one": "{n} other same-name file", "other": "{n} other same-name files"}
    for old in ("未识别生成脚本", "没有找到生成它的脚本", "补上来源", "其他同名文件：{files}", "关联文件", "没能关联文件"):
        assert old not in table, old
    for name in ("node.js", "desk.js", "app.html", "team.js", "workspace.js"):
        shown = (WEBAPP / name).read_text(encoding="utf-8")
        assert not re.search(r"""t\(["'](未识别生成脚本|没有找到生成它的脚本|补上来源|关联文件|没能关联文件)["']""", shown), name


# -- 21.6 on every screen (task V16 B4-1) ----------------------------------------------------------------------

#: 21.6's table, row by row, in the order the design writes them: the Chinese words the screens carry for the row and the
#: English each has. `{n}` stands for the row's "n". A word that is a key of the page's English table carries exactly that
#: English (a counted word, one of its two forms); a word that lives only inside sentences (尝试表) is held by the checks
#: of `test_the_attempt_table_is_said_in_both_languages_and_the_old_word_is_nowhere`.
ROWS_OF_21_6: list[dict[str, str]] = [
    {"报告": "Report", "写报告": "Write a report"},
    {"要点": "Key points"},
    {"交回": "Handback"},
    {"等你处理": "Needs you"},
    {"备份": "Backups", "你的记录与备份": "Your records and backups"},
    {"运行日志": "Run log"},
    {"归入尝试": "File under an attempt"},
    {"添加文件": "Add files"},
    {"尝试表": "Attempt table"},
    {"列表": "List"},
    {"缺生成脚本": "No generating script"},
    {"指定生成它的脚本": "Assign a generating script"},
    {"没有生成脚本": "No script writes it"},
    {"是这个吗？": "Is it this one?", "其他同名文件 {n} 个": "{n} other same-name files"},
    {"代理建议": "Suggested by the agent", "确认这个判断": "Confirm this assessment"},
    {"现在在做什么": "In progress", "进行中的尝试": "Attempts in progress", "等你处理": "Needs you"},
]
#: The words 21.6 retires (the lead's list for B4-1), each of which the "Old" column of some row names or whose row says it
#: is gone. 清单 is retired on 数据与代码 only (the undo's plan, a task's catalogue and a run's list of changes are other
#: lists), so it has a scope of its own below.
RETIRED_WORDS = ("结果记录", "记录结果", "研究记录", "人工记录", "运行记录", "年表", "待办", "未识别生成脚本", "关联文件")
#: The files that show 数据与代码, 图表与论断 and the pages around them; 清单 is retired in their strings.
LIST_WORD_SCOPE = ("node.js", "app.html", "desk.js", "routemap.js", "canvas.js", "team.js", "canvas.py", "node_api.py", "nodepage.py",
                   "kind_rows.py", "provenance.py", "trace.py")
#: English the retired words had, matched in the page's English table (a plural, a hyphen or a "the" does not hide it).
RETIRED_ENGLISH = (r"\bresearch records?\b", r"\bresult records?\b", r"\brecord results?\b", r"\brun records?\b",
                   r"\bhuman records?\b", r"\blink files\b", r"\bto-?do\b", r"\bgenerating script identified\b", r"\bchronolog")
#: What must stay, each with why: (file, the text kept, reason). The scan takes the kept text out of a string before it looks
#: for a retired word in what is left, so an entry excuses that text and nothing else in the string.
ALLOWED_RETIRED: list[tuple[str, str, str]] = [
    ("app.html", "Research OS：人工记录", "the git commit message the app writes (13.1: on disk, never renamed); the tick under 推送 tells the "
                                         "person what the commit will be called"),
    ("githubpush.py", "Research OS：人工记录", "the same commit message, the constant that writes it"),
    ("workspace.py", "结果记录", "_RETIRED_TITLES: the title a headingless report had before 21.6, read back as 'no title typed' (a page that "
                              "read the file earlier sends it; 13.1: no file depends on the screen's words)"),
    ("attempts.py", "二、尝试途径总年表", "the researcher's own heading in the map file, which the reader finds the table by (21.6, last paragraph)"),
    ("identity.py", "人工记录", "the header comment written into .researchos/identity: on disk (13.1)"),
]
#: Python files whose strings are not on a screen: the command line is a developer surface that keeps its words (13.1, 21.6).
NOT_ON_A_SCREEN = {"cli.py": "the command line (13.1): its help and its printed lines keep their words"}


def rows_of_21_6() -> list[tuple[str, str, str]]:
    """21.6's table as written: (Old, New, English) per row, the header and the rule line left out."""
    text = DESIGN.read_text(encoding="utf-8")
    six = text[text.index("### 21.6"):text.index("### 21.7")]
    rows = re.findall(r"^\| (.+?) \| (.+?) \| (.+?) \|$", six, re.M)
    assert rows[0] == ("Old", "New", "English")
    return rows[1:]


@needs_node
def test_every_row_of_21_6_is_on_the_page_as_the_design_writes_it():
    """DESIGN 21.6 (task V16 B4-1): the table has the sixteen rows this test knows; each Chinese word of a row is in the row's
    "New" column and its English in the row's English column; and each word that is a key of the page's English table carries
    exactly that English (a counted word, one of its two forms)."""
    rows = rows_of_21_6()
    assert len(rows) == len(ROWS_OF_21_6) == 16
    table = english_table()
    compared = 0
    for number, ((_old, new, english), words) in enumerate(zip(rows, ROWS_OF_21_6), start=1):
        for zh, en in words.items():
            assert zh.replace("{n}", "n") in new, f"row {number}: {zh!r} is not in the New column {new!r}"
            assert en.replace("{n}", "n") in english, f"row {number}: {en!r} is not in the English column {english!r}"
            if zh not in table:
                continue
            compared += 1
            forms = {form.replace("{n}", "n") for form in entry_forms(table[zh])}
            assert en.replace("{n}", "n") in forms or en in entry_forms(table[zh]) or any(form.endswith(en) for form in entry_forms(table[zh])), \
                f"row {number}: {zh!r} is {table[zh]!r} on the page, {en!r} in the design"
    assert compared >= 14, compared
    # the words that are keys of their own, one by one (a word the design keeps as one English is one key)
    assert {zh: table[zh] for zh in ("备份", "你的记录与备份", "要点", "写报告", "运行日志", "列表")} == {
        "备份": "Backups", "你的记录与备份": "Your records and backups", "要点": "Key points", "写报告": "Write a report",
        "运行日志": "Run log", "列表": "List"}
    assert table["报告"] == {"one": "Report", "other": "Reports"}
    assert table["其他同名文件 {n} 个"] == {"one": "{n} other same-name file", "other": "{n} other same-name files"}


@needs_node
def test_the_retired_words_are_keys_of_the_english_table_no_more():
    """DESIGN 21.6 (task V16 B4-1): a retired Chinese string is not a key of /i18n.js -- not the word alone and not a sentence
    that carries it -- so its English cannot outlive it. (清单 is looked at in the scope the design gives it.)"""
    table = english_table()
    assert [key for key in table if any(word in key for word in RETIRED_WORDS)] == []
    assert [key for key in table if "清单" in key and "数据与代码" in key] == []
    for key in ("记录结果", "结果记录", "研究记录", "运行记录", "无关联研究记录", "保存研究记录", "输出清单暂时无法读取。", "关联研究记录",
                "此文献文件关联的研究记录", "改动了研究记录", "删除了研究记录", "尚未补充研究记录。", "运行记录：{task}"):
        assert key not in table, key


@needs_node
def test_the_attempt_table_is_said_in_both_languages_and_the_old_word_is_nowhere():
    """DESIGN 21.6: 年表 is 尝试表, "Attempt table" -- a word inside the sentences of the page, so the English of every key that
    says it is held to the word, and none says the old word's English. 尝试表 is said by at least a dozen keys."""
    table = english_table()
    keys = [key for key in table if "尝试表" in key]
    assert len(keys) >= 12, len(keys)
    saying = [key for key in keys if any("attempt table" in form.lower() for form in entry_forms(table[key]))]
    assert len(saying) >= 10, len(saying)
    assert [(key, form) for key in table for form in entry_forms(table[key]) if re.search(r"chronolog|timeline", form, re.I)] == []
    assert table["尝试表里还没有尝试。"] == "The attempt table has no attempts yet."


def _kept_out(where: str, text: str) -> str:
    """`text` without the strings ALLOWED_RETIRED keeps for the file `where`."""
    for name, kept, _why in ALLOWED_RETIRED:
        if name == where:
            text = text.replace(kept, "")
    return text


def _retired_in(where: str, text: str) -> list[str]:
    """The retired words a string shows once what the allowlist keeps is taken out of it (清单 only in LIST_WORD_SCOPE)."""
    left = _kept_out(where, text)
    words = [word for word in RETIRED_WORDS if word in left]
    if "清单" in left and where in LIST_WORD_SCOPE:
        words.append("清单")
    return words


def _markup_strings(markup: str) -> list[str]:
    """What app.html's markup shows: its text nodes (not scripts, styles or comments) and the attributes a screen reads."""
    from html.parser import HTMLParser

    found: list[str] = []

    class Reader(HTMLParser):
        skip = 0

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style"):
                self.skip += 1
            found.extend(v for k, v in attrs if k in ("title", "aria-label", "placeholder", "alt", "value") and v)

        def handle_endtag(self, tag):
            if tag in ("script", "style"):
                self.skip -= 1

        def handle_data(self, data):
            if not self.skip and data.strip():
                found.append(data.strip())

    Reader(convert_charrefs=True).feed(markup)
    return found


def _python_strings(path: Path) -> list[str]:
    """Every string constant of a module that is not a docstring or another bare string statement (comments and docstrings
    are the developer's, not the screen's)."""
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    bare = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)}
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in bare]


def retired_words_on_screens() -> tuple[list[str], set[tuple[str, str]], int]:
    """(what is found, the allowlist entries that excused something, how many strings were read). Read: app.html's markup, every
    string literal of its scripts and of each served script (so every t() argument, every table of words and the English
    table itself), every string constant of the engine's Python modules, and the shell's Swift lines."""
    from test_v9_language import _scripts_of, _served_scripts, _unescape, js_literals

    found, used, read = [], set(), 0

    def check(where: str, text: str, how: str) -> None:
        nonlocal read
        read += 1
        for name, kept, _why in ALLOWED_RETIRED:
            if name == where and kept in text:
                used.add((name, kept))
        found.extend(f"{where} {how}: {word} in {text.strip()[:90]!r}" for word in _retired_in(where, text))

    for text in _markup_strings(APP_HTML.read_text(encoding="utf-8")):
        check("app.html", text, "markup")
    for path in [APP_HTML, *_served_scripts()]:
        for _start, _quote, raw in js_literals(_scripts_of(path)):
            check(path.name, _unescape(raw), "string")
    for path in sorted(WEBAPP.parent.rglob("*.py")):
        if path.name in NOT_ON_A_SCREEN:
            continue
        for text in _python_strings(path):
            check(path.name, text, "string")
    for path in sorted((WEBAPP / "shell").glob("*.swift")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.lstrip().startswith("//"):
                check(path.name, line, "line")
    return found, used, read


def test_no_screen_shows_a_retired_word_of_21_6():
    """DESIGN 21.6, 21.9 item 6 (task V16 B4-1): in everything the app can show -- app.html's text and attributes, every string
    of every script it serves (a t() argument, a table of words, the English table), every string of the engine's modules (a
    sentence it composes, a say() pair) and the shell -- none of 结果记录, 记录结果, 研究记录, 人工记录, 运行记录, 年表, 待办, 未识别生成脚本,
    关联文件, nor 清单 on 数据与代码 and its neighbours. What must stay is ALLOWED_RETIRED, each with its reason; an entry that
    excuses nothing is stale and is reported."""
    found, used, read = retired_words_on_screens()
    assert found == []
    assert used == {(name, kept) for name, kept, _why in ALLOWED_RETIRED}, "an allowlist entry excuses nothing any more"
    assert read > 5000, read                                  # the scan reads the page, not nothing


def test_the_scan_for_retired_words_finds_a_word_and_excuses_only_what_is_listed():
    """The check itself: a retired word in a string is found; the git commit message in app.html is excused but the same words
    elsewhere in that string are not, and the same text in another file is not excused; 清单 is retired in node.js and not in
    workspace.js (where it names an undo's plan); a docstring and a comment are not read."""
    assert _retired_in("workspace.js", "改动了研究记录") == ["研究记录"]
    assert _retired_in("app.html", "提交说明「Research OS：人工记录」") == []
    assert _retired_in("app.html", "提交说明「Research OS：人工记录」，你的人工记录") == ["人工记录"]
    assert _retired_in("node.js", "Research OS：人工记录") == ["人工记录"]
    assert _retired_in("node.js", "输出清单暂时无法读取。") == ["清单"] and _retired_in("workspace.js", "没能读出撤回的清单。") == []
    assert _markup_strings('<button title="查看年表">记录</button><script>"年表"</script><!-- 年表 --><p> 年表 </p>') == ["查看年表", "记录", "年表"]
    sample = Path(__file__).parent / "_scratch_retired_docstring.py"
    try:
        sample.write_text('"""年表 in a docstring."""\nNAME = "尝试表"\nTITLE = "年表"\n', encoding="utf-8")
        assert _python_strings(sample) == ["尝试表", "年表"]
    finally:
        sample.unlink(missing_ok=True)


@needs_node
def test_the_page_shows_no_retired_english_either():
    """English mode shows the new words (21.9 item 6): no entry of the page's English table says what a retired word said --
    a research record, a result record, Record results, a run record, a human record, Link files, a to-do, 'No generating
    script identified' -- and the entries that replaced them say the design's English."""
    table = english_table()
    assert [(key, form) for key, value in table.items() for form in entry_forms(value)
            if any(re.search(pattern, form, re.I) for pattern in RETIRED_ENGLISH)] == []
    assert table["写报告"] == "Write a report" and table["要点"] == "Key points" and table["备份"] == "Backups"
    assert table["不关联报告"] == "No linked report" and table["报告：{title}"] == "Report: {title}"
    assert table["提到这个文件的笔记与报告"] == "Notes and reports that mention this file"
    assert table["运行日志：{task}"] == "Run log: {task}"
    assert table["有 {n} 处越界改动，详见运行日志。"]["other"] == "There are {n} out-of-bounds changes. See the run log."
