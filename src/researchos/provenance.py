"""When something broken is a warning, and what the page offers beside it (DESIGN.md 21.5; task V16 B3-1).

The rule, in one sentence: **the warning colour is for what the paper relies on and is broken.** The pages that list
files (图表与论断, 数据与代码, a node's 来龙去脉) apply it to every state the engine reports for a file; this module is
the rule and the few facts it needs, the pages only ask.

A warning is:

- a figure or a table the chosen paper includes -- a section of a draft marked 「设为论文正文」 includes it (with no
  draft chosen yet, any draft stands for the paper: nothing says which one is not) -- whose source is missing or
  broken (缺来源: no generating script, a script or a file of its chain not there, a script that cannot be read)
  or that no attempt holds (缺尝试);
- a claim of the paper with nothing behind it, or whose result's chain is broken;
- a broken link: a script names a file that is not there (断链), whatever the file is for.

Everything else is quiet text, in no colour: a file no script writes that is only read (a download in `raw/`), a
script that belongs to no attempt, a file nothing reads, a script with no reads or writes found, a figure or table
of a draft the paper does not include. A warning comes with its action beside it: 缺生成脚本 and 缺来源 for a
missing generator, 「指定生成它的脚本」 (a `generates` or `writes` entry, the write 补上来源 makes); 缺尝试,
「归入尝试」 (a `belongs_to` entry); a broken link whose file exists elsewhere, 「是这个吗？」 (`candidates`: a
`reads`, `writes` or `generates` entry from that file to the script). A quiet state offers the same action, quietly,
where there is one to take. Nothing is written without the person's click.

The words a row says are `CAUSES` (Chinese, as the engine's other labels are; the page translates them, 13.6).
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path
from sqlite3 import Connection
from typing import Any, Iterable

from researchos import db
from researchos.ingest import dataflow

#: The breaks that make a figure's or table's source missing or broken (the umbrella 缺来源).
SOURCE_BREAKS = ("no_generating_script", "script_missing", "data_missing", "script_unreadable")
#: What a draft can include as a figure or table, and which of these the mapping grammar can give a generating script.
PAPER_TYPES = ("figure", "dataset", "file")
GENERATED_BY = {"figure": "generates", "dataset": "writes"}
#: What can be filed under an attempt (`mappings.ATTEMPT_FROM_TYPES`).
ATTEMPTABLE = ("script", "dataset", "figure")
CANDIDATE_LIMIT = 50

#: Why a figure or table is not complete, in the words a row says (13.6: the page translates them).
CAUSES = {
    "no_script": "没有生成脚本",
    "word": "没有生成脚本：图是从 Word 文件里抽出来的",
    "script_gone": "生成它的脚本文件不在磁盘上",
    "unreadable": "生成它的脚本暂时无法读取",
    "reads_gone": "生成它的脚本读的文件不在",
    "script_no_attempt": "生成脚本没有归入尝试",
    "no_attempt": "还没有归入任何尝试",
}
#: A claim's: nothing behind it, or a result behind it whose chain is broken.
CLAIM_CAUSES = {"no_backing": "没有找到支撑这个数字的结果", "reads_gone": "依据的结果读的文件不在"}

#: The folder an extracted Word file's pictures are put in (`main_assets/`, `media/`) and the name Word gives them.
_WORD_FOLDER = re.compile(r"(^|[_\-. ])(assets|media|files|images)$", re.I)
_WORD_NAME = re.compile(r"^image\d+\.[a-z0-9]+$", re.I)


def section_file(node_id: str) -> str | None:
    """The draft a `section:` or `claim:` node id lives in."""
    prefix, _, rest = node_id.partition(":")
    return rest.rsplit("#", 1)[0] if prefix in ("section", "claim") and "#" in rest else None


class Paper:
    """The figures and tables the chosen paper includes: the targets of the `includes` links of sections of the drafts
    marked 「设为论文正文」 (`drafts`), or of every draft while none is marked. Read once for a request."""

    def __init__(self, conn: Connection, drafts: Iterable[str]) -> None:
        wanted = set(drafts)
        self.included: set[str] = set()
        for edge in db.query_edges(conn, type="includes"):
            if edge["status"] == "rejected":
                continue
            draft = section_file(edge["src"])
            if draft is not None and (not wanted or draft in wanted):
                self.included.add(edge["dst"])

    def has(self, node_id: str) -> bool:
        return node_id in self.included


def from_word(path: str | None) -> bool:
    """A picture a Word file was taken apart into: named `image<N>` (the name Word gives its pictures) in a folder
    called assets, media, files or images (`main_assets/`, `x/media/`) -- no script wrote it, a converter did."""
    if not path:
        return False
    folder, name = posixpath.split(path)
    return bool(_WORD_NAME.match(name)) and any(_WORD_FOLDER.search(part) for part in folder.split("/")[-2:])


def _cause(code: str) -> dict[str, str]:
    return {"code": code, "label": CAUSES[code]}


def causes(state: str, chain: dict[str, Any]) -> list[dict[str, str]]:
    """Why a figure or table is not complete, from what its trace knows (`state` is nodepage's: no_source,
    no_attempt, complete): the source's causes first and only those when the source is broken -- a figure with no
    generator has no attempt to speak of --, else the attempt's. A figure whose generating script is in no attempt
    says so, rather than the general 「还没有归入任何尝试」."""
    kinds = {b["kind"] for b in chain["breaks"]}
    found: list[dict[str, str]] = []
    if state == "no_source":
        if "no_generating_script" in kinds:
            found.append(_cause("word" if from_word(chain["node"].get("path")) else "no_script"))
        if "script_missing" in kinds:
            found.append(_cause("script_gone"))
        if "script_unreadable" in kinds:
            found.append(_cause("unreadable"))
        if "data_missing" in kinds:
            found.append(_cause("reads_gone"))
    elif state == "no_attempt":
        found.append(_cause("script_no_attempt" if chain["node"].get("made_by") else "no_attempt"))
    return found


def repair_of(state: str, node_type: str, cause_codes: Iterable[str], path: str | None) -> dict[str, Any] | None:
    """The action a figure or table row offers: 「指定生成它的脚本」 when no script generates it (and the mapping
    grammar can give a figure or a data file one), 「归入尝试」 when no attempt holds it; nothing otherwise -- a broken
    file or script is mended on the file's own page, where its links are."""
    codes = set(cause_codes)
    if state == "no_source" and codes & {"no_script", "word"} and node_type in GENERATED_BY:
        return {"type": GENERATED_BY[node_type], "label": "指定生成它的脚本", "file": path}
    if state == "no_attempt" and node_type in ATTEMPTABLE:
        return {"type": "belongs_to", "label": "归入尝试", "file": path}
    return None


def tone_of(state: str, in_paper: bool, cause_codes: Iterable[str] = (), repair: dict[str, Any] | None = None) -> str:
    """`ok` for a complete one; `warning` for what the paper includes and is not complete; `quiet` for the rest --
    and for what the paper includes when all that is missing is something the page can offer no action for (a
    generating script for a draft, an attempt for a file the grammar cannot file): a warning is where it can be
    acted on."""
    if state == "complete":
        return "ok"
    if not in_paper:
        return "quiet"
    if repair is None and set(cause_codes) <= {"no_script", "word", "script_no_attempt", "no_attempt"}:
        return "quiet"
    return "warning"


def break_tone(kind: str, node_type: str, in_paper: bool) -> str:
    """A break on a node's own page (来龙去脉): a warning when the node is a figure or table the paper includes and
    the page can offer the action (a generating script for a figure or a data file, an attempt for a figure, data
    file or script), quiet otherwise -- a script that belongs to no attempt, a download nothing writes, a draft
    nothing generates are normal on their own page. A chain broken further up (a script or file not there, a script
    that cannot be read) is the paper's when the node is. A broken link is not a break of this kind: it is always
    a warning (`candidates`, the pages' broken lines)."""
    if not in_paper:
        return "quiet"
    if kind == "no_generating_script":
        return "warning" if node_type in GENERATED_BY else "quiet"
    if kind == "no_attempt":
        return "warning" if node_type in ATTEMPTABLE else "quiet"
    return "warning" if node_type in PAPER_TYPES else "quiet"


def claim_tone(state: str) -> str:
    """A claim is listed only from the paper, so what is missing behind it is what the paper relies on: a claim with
    nothing behind it or a broken result behind it is a warning; one whose result no attempt holds is quiet -- the
    attempt is filed on the result, where it is acted on."""
    return "warning" if state == "no_source" else ("ok" if state == "complete" else "quiet")


# -- a broken link: is it this one? (21.5) -------------------------------------------------------


def _shared_folders(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a.split("/") if a else [], b.split("/") if b else []):
        if x != y:
            break
        n += 1
    return n


def candidates(root: Path, script: str, target: str, kind: str, names: Any) -> list[dict[str, Any]]:
    """The files that may be the one a script names and cannot find. `target` is the path as the script's text gave
    it (relative to the project), `kind` `reads` or `writes`, `names` a `lineage.Names`.

    First, the path the script's own text names when it is taken from each of the script's folders in turn, the
    script's own first and the project's root last -- scripts are often run from the folder of their package
    (`environment/R_packages.lock.csv` named by `00_Current/replication_v9/environment/check_R_environment.R` is
    `00_Current/replication_v9/environment/R_packages.lock.csv`) --, those that are a file. Then the other files of
    the same name, the nearest first by the folders they share with the script (then by path).

    Each is `{"path", "package", "link": {"from", "to", "type"}}`: `link` is the entry that says the script reads
    (data first) or writes it, in the form `/api/mappings/add` takes. A file the grammar cannot give that entry (an
    image a script reads) is left out. Reads the disk for the folders named, never the whole project."""
    found: list[dict[str, Any]] = []
    seen = {target}

    def add(path: str, package: bool) -> None:
        if path in seen:
            return
        node_type = dataflow.node_type_for_path(path)
        if kind == "reads":
            link = {"from": path, "to": script, "type": "reads"} if node_type == "dataset" else None
        else:
            link = ({"from": script, "to": path, "type": "generates" if node_type == "figure" else "writes"}
                    if node_type in ("figure", "dataset") else None)
        seen.add(path)
        if link is not None:
            found.append({"path": path, "package": package, "link": link})

    folder = posixpath.dirname(script)
    while folder:
        path = posixpath.normpath(posixpath.join(folder, target))
        if not path.startswith("../") and (root / path).is_file():
            add(path, True)
        folder = posixpath.dirname(folder)
    where = posixpath.dirname(script)
    rest = sorted((p for p in names.get(posixpath.basename(target)) if p not in seen),
                  key=lambda p: (-_shared_folders(posixpath.dirname(p), where), p))
    for path in rest:
        add(path, False)
    return found[:CANDIDATE_LIMIT]


# -- names in a list ---------------------------------------------------------------------------------


def distinct_names(paths: list[str]) -> dict[str, str]:
    """For a list of files, the nearest folders that tell apart those of one name: `{path: "folder"}` for each path
    whose base name another of the list also has -- the folder just above it, or the two nearest when another has
    the same one, and so on, as few as tell this path from every other of its name --, and nothing for a name that
    stands alone or a file in the project's root (`复核诊断.R` three times: 交付_20260927_审查修订, 交付_20261006,
    and the one in the root, which needs no folder)."""
    groups: dict[str, list[str]] = {}
    for path in paths:
        groups.setdefault(posixpath.basename(path), []).append(path)
    out: dict[str, str] = {}
    for same in groups.values():
        if len(same) < 2:
            continue
        up = {p: posixpath.dirname(p).split("/")[::-1] if posixpath.dirname(p) else [] for p in same}
        for path, parts in up.items():
            if not parts:
                continue
            depth = 1
            while depth < len(parts) and any(other != path and up[other][:depth] == parts[:depth] for other in same):
                depth += 1
            out[path] = "/".join(parts[:depth][::-1])
    return out
