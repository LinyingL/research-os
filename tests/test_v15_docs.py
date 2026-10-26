"""The pages describe registration as the page offers it (DESIGN.md 19.5, task V15 docs): a person registers a file only
from the review lists, which offer files that a run created and nobody registered, so a page may say such a file can be
registered wherever it lies but not that any file can be."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEBAPP = ROOT / "src" / "researchos" / "webapp"
ENGLISH = ("CHANGELOG.md", "README.md", "docs/project-workspace.md")
CHINESE = ("docs/项目首页.md",)


def _blocks(name: str) -> list[str]:
    """The paragraphs and list items of a page, each on one line."""
    text = (ROOT / name).read_text(encoding="utf-8")
    return [re.sub(r"\s+", " ", block) for block in re.split(r"\n\s*\n|\n- ", text)]


def test_the_page_calls_the_register_endpoint_only_from_the_review_list():
    """The premise of the pages' wording: no other control registers a file, so a file nobody offered cannot be
    registered from the page. A new caller means the pages must say what it does."""
    calls = []
    for path in sorted(WEBAPP.iterdir()):
        if path.suffix in (".js", ".html"):
            text = path.read_text(encoding="utf-8")
            for hit in re.finditer(r'"/api/outputs/register"', text):
                inside = re.match(r"function (\w+)", text[text.rfind("function ", 0, hit.start()):])
                calls.append((path.name, inside.group(1) if inside else None))
    assert calls == [("workspace.js", "registerBackfill")]


@pytest.mark.parametrize("name", ENGLISH)
def test_english_pages_do_not_say_that_any_file_can_be_registered(name):
    blocks = _blocks(name)
    assert not [b for b in blocks if "wherever it is" in b.lower()]
    where = [b for b in blocks if "wherever" in b.lower()]
    assert where, name
    assert all("nobody registered" in b for b in where), where


@pytest.mark.parametrize("name", CHINESE)
def test_chinese_pages_do_not_say_that_any_file_can_be_registered(name):
    where = [b for b in _blocks(name) if "哪里都可以登记" in b]
    assert where, name
    assert all("还没有人登记" in b for b in where), where
