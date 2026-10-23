"""The glossary stays true (DESIGN.md 13.4, amended by 18.2; task V14).

13.4's table and the lists of words in 13.4's paragraph below it, 14.6, 15.5
and 17.9 are the binding English of the interface. These tests read them out
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

from test_v9_language import CJK, FORBIDDEN, english_table, needs_node

DESIGN = Path(__file__).parents[1] / "DESIGN.md"

#: A word of one of the design's lists: a Chinese term (with a leading
#: capitalised ASCII token as in "AI 执行者", an aside in brackets and "as a
#: role" allowed), then its English in straight double quotes.
LISTED_WORD = re.compile(r'((?:[A-Z]{2,} )?[一-鿿][一-鿿\w]*)(?: \([^)]*\))?(?: as a role)? "([^"]+)"')

#: The sections whose lists of words are read (each a stretch of DESIGN.md
#: from its heading to the next one).
LISTS = [("13.4", "### 13.4", "### 13.5"), ("14.6", "### 14.6", "### 14.7"),
         ("15.5", "### 15.5", "### 15.6"), ("17.9", "### 17.9", "### 17.10")]


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
    14.6, 15.5 and 17.9 -- (section, Chinese, English)."""
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

#: Words 18.2 retired, on either side: none of them may stand in the table
#: (a Chinese word is exact, an English word is a whole cell of a row).
RETIRED_CHINESE = ["判断", "判决", "成果", "官方版本", "产出", "实验记录"]
RETIRED_ENGLISH = ["Judgments", "Verdict", "Relation map", "Published version", "Add source", "No attempt",
                   "Changes & notes", "Run record", "Link judgment"]


def test_the_table_names_the_terms_of_18_2_and_none_it_retired():
    """18.2's renames and additions are rows as written there; the retired
    words are gone from both sides of the table."""
    pairs = dict(table_pairs())
    for zh, en in TERMS_OF_18_2.items():
        assert pairs.get(zh) == en, f"{zh}: the table gives {pairs.get(zh)!r}, 18.2 gives {en!r}"
    assert [zh for zh in RETIRED_CHINESE if zh in pairs] == []
    assert [en for en in RETIRED_ENGLISH if en in pairs.values()] == []


def test_the_lists_are_read_and_give_a_word_once_with_one_english():
    """13.4's paragraph below the table, 14.6, 15.5 and 17.9 yield their
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
    given: dict[str, set[str]] = {}
    for zh, en in [*table_pairs(), *[(zh, en) for _, zh, en in listed]]:
        given.setdefault(zh, set()).add(en)
    for zh, forms in given.items():
        # A plural and its singular, or a word that wears an extra qualifier, is the same word; two
        # unrelated Englishes are not.
        stems = {re.sub(r"s$", "", form.lower()) for form in forms}
        assert len(stems) == 1, f"{zh}: {sorted(forms)}"


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
    """For each word of 13.4's table and of the lists in 13.4, 14.6, 15.5 and
    17.9 that is a key of the English table, the entry equals the design's
    English -- or, for a counted word, is one of its two forms. (A word that
    is no key of its own -- it lives inside sentences, or belongs to a page
    not yet converted, like 含义 to 确认这一版 -- is not compared here.) The
    count guards the parser: a design edit that makes it read nothing must
    not let this pass."""
    wrong, compared = keys_off_the_glossary(english_table())
    assert wrong == []
    assert compared >= 70, compared


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
