"""「根据所选文件生成草稿」 for a new attempt (DESIGN.md 16): a local model
reads the files the researcher picked and drafts two cells of the new row,
the description and the result. Nothing else -- not the verdict, not a
reason, not a proposal's one-sentence explanation: those are the
researcher's judgement (Section 0, "Humans own judgment").

The draft is returned to the form, never written anywhere: the researcher
edits it, and the row goes through the same preview, write and proposal
path as one typed by hand. A cell the files do not support stays empty.
What is checked here, deterministically, after the model answers (the
`better_match` rule of "Machine annotation vs. human judgement", applied to
file paths and numbers): each cell names the given files it rests on, and
names no other; every number in it occurs in the text of those files as it
was sent (or, as written, in their paths, which the model was shown). A cell that fails is dropped, with its reason, and logged. What
is only asked of the model in the prompt: no judgement of the attempt, no
paraphrased guess.

No example rows from the table are sent: a small model copied their wording
into the draft.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from copy import deepcopy
from typing import Any

from researchos.semantic.backend import LlmBackend

logger = logging.getLogger(__name__)

MAX_FILES = 5
# A local model's context is small (LM Studio often loads 4,096 tokens): the
# chosen files share this many characters, equal shares, a short file's unused
# share going to the others. A file cut is said to the model and to the form.
MAX_CHARS_TOTAL = 12_000
# The model may take minutes on a laptop; RCE_LLM_TIMEOUT wins when set.
TIMEOUT_SECONDS = 300.0
FIELDS = ("description", "result")

_CELL = {
    "type": "object",
    "additionalProperties": False,
    "required": ["text", "sources"],
    "properties": {"text": {"type": "string"}, "sources": {"type": "array", "items": {"type": "string"}}},
}
SCHEMA = {"type": "object", "additionalProperties": False,
          "required": list(FIELDS), "properties": {f: _CELL for f in FIELDS}}

SYSTEM = (
    "You help a researcher draft one new row of their research log. Each row records an attempt: "
    "'description' is what was done (the method, data, specification), 'result' is what came out "
    "(numbers and findings exactly as the files state them). Use only what the given files show. "
    "Reading a script is not running it: describe its method without claiming it has run. "
    "A print format, decision threshold or conditional message in code is not an observed result. "
    "Never infer sample sizes, coefficients, p-values or conclusions from code. If only scripts "
    "are supplied, leave 'result' empty unless actual results are written out in comments or text. "
    "If the files do not show what was done, return an empty description; if they show no outcome, "
    "return an empty result. Never invent, round or estimate a number. Do not judge whether the "
    "attempt succeeded. In 'sources', list the paths (exactly as given) of the files each text rests "
    "on. Cite only supplied files, never input or output paths mentioned inside their contents. "
    "Write every number in Arabic digits exactly as the files show it. Keep each text to one or two "
    "sentences. "
)
_LANGUAGE = {
    "zh": "Write in Chinese.",
    "en": "Write in English.",
    None: "Write in the language of the files' own comments and text.",
}

# A number in a cell or a file (after NFKC: full-width and superscript
# digits read as ASCII): an optional leading sign (U+2212 too) that is not a
# hyphen inside a word, ASCII digits that do not start inside a name (log10,
# x2) with optional thousands separators and a decimal part, an exponent
# that belongs to its number (1.23e-05 is one number, never 1.23 and 05), an
# optional trailing %.
_NUMBER = re.compile(
    r"(?:(?<![0-9A-Za-z_.])[-+−])?(?<![0-9A-Za-z_])"
    r"(?:[0-9]{1,3}(?:,[0-9]{3})+(?![0-9])(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?|\.[0-9]+)"
    r"(?:[eE][-+−]?[0-9]+)?%?"
)


def _numbers(text: str) -> list[str]:
    """Every number in `text`, normalized: no thousands commas, U+2212 as
    "-", no "%"."""
    text = unicodedata.normalize("NFKC", text)
    return [n.replace(",", "").replace("−", "-").rstrip("%") for n in _NUMBER.findall(text)]


def _unsupported_numbers(text: str, sources_text: str, names: str = "") -> list[str]:
    """The numbers in `text` that occur nowhere in `sources_text`, compared
    as normalized strings and as values ("0.10" is "0.1"). A comma in the
    files is read both ways, thousands separator and field separator (a CSV
    row "120,450,10" holds 120, 450 and 10). A number in `names` (the cited
    files' paths, which the model was shown) counts only as written."""
    known = set(_numbers(sources_text)) | set(_numbers(sources_text.replace(",", " ")))
    values = {float(n) for n in known}
    known |= set(_numbers(names))
    return [n for n in _numbers(text) if n not in known and float(n) not in values]


# The end of a text cut inside a number ("0.4567" cut to "0.45"): dropped.
_SPLIT_NUMBER = re.compile(r"(?<![0-9A-Za-z_.])[-+−]?(?:[0-9]+(?:,[0-9]{3})*(?:,[0-9]{0,2}|\.?[0-9]*(?:[eE][-+−]?[0-9]*)?)"
                           r"|\.[0-9]+(?:[eE][-+−]?[0-9]*)?)\Z")


def _cut(content: str, share: int) -> str:
    """The first `share` characters of `content`, never ending inside a word
    or a number the cut would split (16.2): back to the last space or line
    break, or, in a text with none, less the number cut in two."""
    if len(content) <= share or content[share].isspace():
        return content[:share]
    head = content[:share]
    space = re.search(r"\s(?=\S*\Z)", head)
    if space:
        return head[:space.end()]
    split = _SPLIT_NUMBER.search(head[-64:])  # the end only: a long run of digits stays linear
    return head[:len(head) - len(head[-64:]) + split.start()] if split else head


def _user_message(files: list[dict[str, Any]]) -> str:
    parts = []
    for f in files:
        cut = " (cut: only the beginning is shown)" if f["cut"] else ""
        parts.append(f"=== file: {f['path']}{cut} ===\n{f['content']}")
    parts.append("Allowed source paths for each 'sources' array (copy exactly, including directories; "
                 "do not translate or shorten):\n" + json.dumps([f["path"] for f in files], ensure_ascii=False))
    return "\n\n".join(parts)


def prepare_files(read: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each read file (`{path, content, truncated}`) cut to its share of
    MAX_CHARS_TOTAL, in the given order; `cut` says whether anything was
    left out."""
    budget, left, share = MAX_CHARS_TOTAL, len(read), {}
    for i in sorted(range(len(read)), key=lambda i: len(read[i]["content"])):  # shortest first
        share[i] = min(len(read[i]["content"]), budget // left)
        budget, left = budget - share[i], left - 1
    return [{"path": f["path"], "content": _cut(f["content"], share[i]),
             "cut": bool(f.get("truncated")) or len(f["content"]) > share[i]} for i, f in enumerate(read)]


def _check(name: str, cell: dict[str, Any], files: dict[str, str]) -> str | None:
    """Why the drafted `cell` is dropped, or None when it stands."""
    if not cell["text"].strip():
        return "empty"
    if not cell["sources"]:
        return "no_source"
    unknown = [p for p in cell["sources"] if p not in files]
    if unknown:
        logger.info("draft %s: the model named files it was not given: %r", name, unknown)
        return "unknown_source"
    missing = _unsupported_numbers(cell["text"], "\n".join(files[p] for p in cell["sources"]),
                                   "\n".join(cell["sources"]))
    if missing:
        logger.info("draft %s: numbers not in its cited files: %r", name, missing)
        return "unsupported_number"
    return None


def draft_attempt(files: list[dict[str, Any]], client: LlmBackend, language: str | None = None) -> dict[str, Any]:
    """The draft: `{fields: {description|result: {text, sources}}, dropped:
    {cell: "empty"|"no_source"|"unknown_source"|"unsupported_number"},
    model, cut: [paths]}`. A dropped cell is `{"text": "", "sources": []}`.
    `language` is "zh", "en" or None (the files' own). `model` is the one
    the server says answered, else the one asked for. Raises LlmError when
    the model cannot be reached or does not answer in the schema."""
    system = SYSTEM + _LANGUAGE.get(language, _LANGUAGE[None])
    sent = {f["path"]: f["content"] for f in files}
    # Constrained decoding must choose a supplied path, rather than reproduce
    # a long/Chinese name from memory. Keep the shared schema unchanged.
    schema = deepcopy(SCHEMA)
    for cell in schema["properties"].values():
        cell["properties"]["sources"]["items"]["enum"] = list(sent)
    reply = client.complete_json(system, _user_message(files), schema, name="attempt_draft")
    fields, dropped = {}, {}
    for name in FIELDS:
        reason = _check(name, reply[name], sent)
        if reason:
            logger.info("draft %s dropped: %s", name, reason)
            dropped[name] = reason
            fields[name] = {"text": "", "sources": []}
        else:
            named = set(reply[name]["sources"])
            fields[name] = {"text": reply[name]["text"].strip(), "sources": [p for p in sent if p in named]}
    return {"fields": fields, "dropped": dropped, "model": getattr(client, "last_model", None) or client.model,
            "cut": [f["path"] for f in files if f["cut"]]}
