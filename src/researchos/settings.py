"""This Mac's own settings for Research OS (`settings.json` under the
Research OS home), and the interface language (DESIGN.md Section 13).

**The file.** One JSON object, per person and per Mac, beside
`projects.json`: `doi_online_lookup` (11.4, `researchos.literature`),
`interface_language` (13.2), `llm_base_url` and `llm_model` (16). Every
write is read-modify-write and keeps the
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
import re
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from researchos import paths

SETTINGS_FILENAME = "settings.json"
LANGUAGE_SETTING = "interface_language"
LANGUAGES = ("zh", "en")
#: The request header the page sets on every API call (`apiGet`/`apiPost`).
LANG_HEADER = "X-ResearchOS-Lang"


def _first(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """A repeated key keeps its FIRST value, as Foundation's reader does."""
    out: dict[str, Any] = {}
    for key, value in pairs:
        out.setdefault(key, value)
    return out


def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} is not JSON")


def read_json(path: Path) -> dict[str, Any]:
    """A JSON object file, tolerantly: anything that is not a readable JSON
    object is `{}` -- nested too deeply to read included. The shell reads
    settings.json with Foundation (RCEShell.swift `savedInterfaceLanguage`),
    and both accept the same bytes (13.2: one source of truth): UTF-8, a
    BOM allowed; a repeated key's first value; no NaN or Infinity; no lone
    surrogate."""
    try:
        data = json.loads(path.read_bytes().decode("utf-8-sig"), object_pairs_hook=_first,
                          parse_constant=_no_constant)
        json.dumps(data, ensure_ascii=False).encode("utf-8")  # a lone surrogate: Foundation refuses the file
    except (OSError, UnicodeError, ValueError, RecursionError):
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


# -- the local model for 「生成草稿」 (DESIGN.md 16) -------------------------------

LLM_BASE_URL_SETTING = "llm_base_url"
LLM_MODEL_SETTING = "llm_model"


def without_userinfo(text: str) -> str:
    """`text` (an address, or a message naming one) with any user:password@
    after a `://` left out (16.2: never shown, never logged)."""
    return re.sub(r"://[^/?#\s]*@", "://", text)


def check_llm_base_url(url: str) -> None:
    """ValueError unless `url` is an http:// or https:// address with a host
    name, a port that is a number (when one is given), no user:password
    (urllib cannot use one; a key is RCE_LLM_API_KEY) and no whitespace or
    control character. The message never repeats a password."""
    shown = without_userinfo(url)
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 -- reading it is what checks the port
    except ValueError as exc:
        raise ValueError(f"the model server address is not a valid address: {shown!r} ({without_userinfo(str(exc))})") from None
    if parts.username is not None or parts.password is not None:
        raise ValueError(f"the model server address must not carry a user name or password: {shown!r}")
    if (parts.scheme not in ("http", "https") or not parts.hostname
            or any(c.isspace() or ord(c) < 0x21 or ord(c) == 0x7F for c in url)):
        raise ValueError(f"the model server address must be http:// or https:// and name a host, not {shown!r}")


def llm_settings() -> dict[str, str | None]:
    """The model server's address and model as saved on this Mac; `None`
    for one not saved (the backend's RCE_LLM_* env vars, then its defaults,
    apply). A saved address that `check_llm_base_url` refuses reads as not
    saved, so a damaged file never breaks the reader."""
    data = read_settings()
    out = {key: (v.strip() if isinstance(v := data.get(name), str) and v.strip() else None)
           for key, name in (("base_url", LLM_BASE_URL_SETTING), ("model", LLM_MODEL_SETTING))}
    if out["base_url"] is not None:
        try:
            check_llm_base_url(out["base_url"])
        except ValueError:
            out["base_url"] = None
    return out


def set_llm_settings(base_url: str | None = None, model: str | None = None) -> None:
    """Save the keys given; `None` leaves that key as it is, an empty
    string removes it (the default applies again). ValueError, writing
    nothing, for an address `check_llm_base_url` refuses."""
    changes: dict[str, str] = {}
    if base_url is not None:
        changes[LLM_BASE_URL_SETTING] = base_url.strip()
        if changes[LLM_BASE_URL_SETTING]:
            check_llm_base_url(changes[LLM_BASE_URL_SETTING])
    if model is not None:
        changes[LLM_MODEL_SETTING] = model.strip()
    if not changes:
        return
    data = read_settings()
    for name, value in changes.items():
        if value:
            data[name] = value
        else:
            data.pop(name, None)
    write_json(settings_path(), data)
