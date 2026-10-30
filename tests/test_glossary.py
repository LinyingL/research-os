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

from test_v9_language import APP_HTML, CJK, FORBIDDEN, _node, english_table, needs_node

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
    "补上来源": "Add a source", "缺尝试": "Missing attempt", "关联文件": "Link files",
    "正式版本": "Official version", "改动与备注": "Changes and comments", "备注": "Comment", "笔记": "Note",
    "图表与论断": "Figures and claims", "结果记录": "Result records", "输出": "Outputs", "实验": "Experiment",
    "本机": "This Mac", "项目首页": "Project home", "负责人": "Owner", "任务": "Task",
    "未识别生成脚本": "No generating script identified", "中间数据": "Intermediate data", "同名文件": "Same-name files",
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
    "项目首页": "project home", "结果记录": "result record", "同名文件": "same-name file",
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
    assert {zh: table[zh] for zh in TERMS_OF_21_2} == TERMS_OF_21_2
