"""This Mac's own settings for Research OS (`settings.json` under the
Research OS home), and the interface language (DESIGN.md Section 13).

**The file.** One JSON object, per person and per Mac, beside
`projects.json`: `doi_online_lookup` (11.4, `researchos.literature`) and
`interface_language` (13.2). Every write is read-modify-write and keeps the
other keys: a setting is written only when the person changes it, never by
default, so a file nobody touched stays absent. The read is tolerant: a
file that is missing, unreadable or not a JSON object reads as `{}`.

**The interface language** (13.2) is `"zh"` or `"en"`; no saved choice is
`None`, and so is anything else found in the file -- the page then follows
the Mac: inside the shell, the shell's own answer from
`Locale.preferredLanguages`, injected at document start; else
`navigator.languages`. The shell reads `Locale.preferredLanguages` itself.
The engine never guesses: a sentence it composes for the page is in the
language the page asks for on that request, in the header
`X-ResearchOS-Lang` (`request_language`); without it, Chinese, so the
command line, MCP and an older page are unaffected. `say` is the one
helper the engine uses for such a sentence (13.6).

The language is a property of the screen, never of the record (13.1):
nothing written to a project, a record file or the shared drive depends
on it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from researchos import paths

SETTINGS_FILENAME = "settings.json"
LANGUAGE_SETTING = "interface_language"
LANGUAGES = ("zh", "en")
#: The request header the page sets on every API call (`apiGet`/`apiPost`).
LANG_HEADER = "X-ResearchOS-Lang"


def read_json(path: Path) -> dict[str, Any]:
    """A JSON object file, tolerantly: anything that is not a readable JSON
    object is `{}`."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path: Path, data: Mapping[str, Any]) -> None:
    """`data` as the whole of `path`, atomically and durably, keys sorted."""
    from researchos.records import files  # noqa: PLC0415 -- records imports paths, which this module imports

    path.parent.mkdir(parents=True, exist_ok=True)
    files.durable_write(path, (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def settings_path() -> Path:
    return paths.researchos_home() / SETTINGS_FILENAME


def read_settings() -> dict[str, Any]:
    return read_json(settings_path())


def update_setting(key: str, value: Any) -> None:
    """Write one setting, keeping every other key as it is."""
    data = read_settings()
    data[key] = value
    write_json(settings_path(), data)


# -- the interface language (13.2) ----------------------------------------------------


def interface_language() -> str | None:
    """`"zh"` or `"en"` when the person chose one on this Mac; `None` when
    nothing (or nothing valid) is saved."""
    value = read_settings().get(LANGUAGE_SETTING)
    return value if value in LANGUAGES else None


def set_interface_language(lang: str) -> None:
    """Save the person's choice; only `"zh"` and `"en"` (ValueError
    otherwise). Writes that one key."""
    if not isinstance(lang, str) or lang not in LANGUAGES:
        raise ValueError(f"interface language must be one of {', '.join(LANGUAGES)}, not {lang!r}")
    update_setting(LANGUAGE_SETTING, lang)


def request_language(value: str | None) -> str:
    """The language a request asks for: `"en"` only when its
    `X-ResearchOS-Lang` header says `en` (case and surrounding space
    aside); anything else, an absent header included, is `"zh"`."""
    return "en" if isinstance(value, str) and value.strip().lower() == "en" else "zh"


def say(zh: str, en: str, lang: str, **values: Any) -> str:
    """A sentence the engine composes for the page, in the request's
    language (13.6): `en` when `lang` is `"en"`, else `zh`, its `{name}`
    fields filled from `values` (`str.format`; a literal brace is doubled)."""
    return (en if lang == "en" else zh).format(**values)
