"""Project references for reading, independent of citation matching.

Read indexed BibTeX and references/bibliography CSV or JSON exports. Missing
sources never remain as readable entries. No network, index or project writes.
Zotero enriches project references by exact DOI; unrelated library items stay out.
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from sqlite3 import Connection
from typing import Any

from researchos import cloud, db, literature
from researchos.ingest import latex

MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_ITEMS = 5000
_EXPORT_NAMES = {"references", "bibliography", "refs"}


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _authors(value: Any) -> list[str]:
    if isinstance(value, list):
        names = []
        for person in value:
            name = _text(person)
            if isinstance(person, dict):
                name = " ".join(str(person.get(k) or "") for k in ("given", "family")).strip()
                name = name or _text(person.get("literal"))
            if name:
                names.append(name)
        return names
    return [p.strip() for p in re.split(r"\s+and\s+|;", value) if p.strip()] if isinstance(value, str) else []


def _record(raw: dict[str, Any]) -> dict[str, Any] | None:
    d = {str(k).lower(): v for k, v in raw.items()}
    title = _text(d.get("title"))
    raw_doi = _text(d.get("doi")) or ""
    dois = literature.find_dois(raw_doi)
    doi = dois[0][2] if dois else None
    if not title and not doi:
        return None
    year = d.get("year")
    if year is None and isinstance(d.get("issued"), dict):
        parts = d["issued"].get("date-parts")
        if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
            year = parts[0][0]
    return {"title": title, "authors": _authors(d.get("authors") or d.get("author")),
            "year": str(year) if isinstance(year, (str, int)) else None, "doi": doi,
            "venue": _text(d.get("venue") or d.get("journal") or d.get("booktitle")
                           or d.get("container-title")),
            "abstract": literature.abstract_text(d.get("abstract") or d.get("abstractnote"))}


def _read_source(root: Path, rel: str) -> list[dict[str, Any]]:
    path = root / rel
    # The inventory is a mirror, not authority to read outside the project.
    if (Path(rel).is_absolute() or ".." in Path(rel).parts or path.is_symlink()
            or not path.resolve().is_relative_to(root.resolve())):
        raise OSError("reference source is outside the project or is a symlink")
    cloud.check_readable(path)
    with path.open("rb") as stream:
        body = stream.read(MAX_SOURCE_BYTES + 1)
    if len(body) > MAX_SOURCE_BYTES:
        raise ValueError("reference source exceeds 4 MiB")
    text = body.decode("utf-8-sig")
    if path.suffix.lower() == ".bib":
        return [e.fields for e in latex.parse_bib_entries(text)][:MAX_ITEMS]
    if path.suffix.lower() == ".csv":
        return [r for _, r in zip(range(MAX_ITEMS), csv.DictReader(io.StringIO(text)))]
    value = json.loads(text)
    if isinstance(value, dict):
        value = value.get("references", value.get("items", []))
    if not isinstance(value, list):
        raise ValueError("reference JSON must contain a list")
    return [v for v in value[:MAX_ITEMS] if isinstance(v, dict)]


def catalog(conn: Connection, root: Path, report: dict[str, Any],
            library: literature.ZoteroLibrary) -> dict[str, Any]:
    """Unique project references, with source files and actual abstracts."""
    paths = {r[0] for r in conn.execute(
        "SELECT source FROM scan_sources WHERE extractor IN ('inventory', 'latex') AND status != 'absent'")}
    records = db.get_nodes_by_type(conn, "reference")
    paths.update(r["attrs"]["bib_file"] for r in records if r["attrs"].get("bib_file"))
    sources = sorted(p for p in paths if Path(p).suffix.lower() == ".bib" or
                     (Path(p).stem.lower() in _EXPORT_NAMES and Path(p).suffix.lower() in (".csv", ".json")))
    entries: dict[str, dict[str, Any]] = {}
    titles: dict[str, str] = {}
    warnings: list[dict[str, str]] = []

    def add(raw: dict[str, Any], source: str, *, node: str | None = None, file: str | None = None) -> None:
        item = _record(raw)
        if item is None:
            return
        doi = item["doi"]
        title_key = " ".join((item["title"] or "").casefold().split())
        title_key += "|" + (item["year"] or "")
        key = "doi:" + doi if doi else "title:" + title_key
        # Enrich identifier-free exports, but never merge two different DOIs.
        prior_key = titles.get(title_key)
        if prior_key and (not doi or not entries[prior_key].get("doi")):
            key = prior_key
        if key not in entries:
            if len(entries) >= MAX_ITEMS:
                return
            entries[key] = dict(item, id=key, node=node, sources=[], files=[], abstract_source=None)
        current = entries[key]
        for field, value in item.items():
            if value and not current.get(field):
                current[field] = value
        if item["abstract"] and not current["abstract_source"]:
            current["abstract_source"] = {"zotero": "Zotero", "crossref": "Crossref"}.get(source, source)
        if node and not current.get("node"):
            current["node"] = node
        if source == "zotero" and literature.valid_zotero_key(raw.get("zotero_key")):
            current["zotero_key"] = raw["zotero_key"]
            current["attachments"] = raw.get("attachments") or []
        if source not in current["sources"]:
            current["sources"].append(source)
        if file and file not in current["files"]:
            current["files"].append(file)
        if doi:
            # Later exports with the DOI must find this same entry.
            entries.setdefault("doi:" + doi, current)
        titles.setdefault(title_key, key)

    for rel in sources:
        if not (root / rel).exists():
            continue
        try:
            for raw in _read_source(root, rel):
                add(raw, rel, file=rel)
        except (OSError, ValueError, csv.Error) as exc:
            warnings.append({"file": rel, "detail": str(exc)})
    for draft in report.get("drafts", []):
        for citation in draft["citations"]:
            for target in citation["targets"]:
                if target.get("judged") == "rejected":
                    continue
                add(target, target.get("source") or "entry", node=target.get("node"))

    cache = literature._read_json(literature.cache_path())
    unique = {id(v): v for v in entries.values()}
    for item in unique.values():
        doi = item.get("doi")
        zotero = library.by_doi(doi) if doi else None
        meta = cache.get(doi) if doi else None
        item["abstract_checked"] = isinstance(meta, dict) and "abstract" in meta
        for extra, source in ((zotero.metadata() if zotero else None, "Zotero"),
                              (meta, "Crossref")):
            if not isinstance(extra, dict):
                continue
            for field in ("title", "authors", "year", "venue", "abstract"):
                value = literature.abstract_text(extra.get(field)) if field == "abstract" else extra.get(field)
                if value and not item.get(field):
                    item[field] = value
                    if field == "abstract":
                        item["abstract_source"] = source
            if zotero:
                item["zotero_key"] = zotero.key
                item["attachments"] = [a.payload() for a in zotero.attachments]
    items = sorted(unique.values(), key=lambda r: ((r.get("title") or r.get("doi") or "").casefold(), r["id"]))
    return {"items": items, "warnings": warnings}
