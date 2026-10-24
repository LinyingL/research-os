"""Literature is readable by reference, independently of citation matching."""

from __future__ import annotations

import json
import sqlite3

import pytest

from researchos import bibliography, literature, project
from researchos.webapp import literature_api
from test_v7_citations import _conn, _scan, zotero  # noqa: F401
from test_v7_literature_view import lit_server, no_network  # noqa: F401


@pytest.fixture(autouse=True)
def missing_openalex(monkeypatch):
    monkeypatch.setattr(literature, "FETCH_OPENALEX", lambda url, timeout: b"null")


def _catalog(root):
    conn = _conn(root)
    try:
        return literature_api.citations_payload(conn, root)["library"]
    finally:
        conn.close()


def test_project_exports_form_a_deduplicated_reading_library_without_citations(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "references.bib").write_text(
        '@article{flight, title={Paper plane flight}, author={Smith and Jones}, '
        'year={2020}, doi={10.1234/flight}, journal={Flight Journal}}')
    (root / "references.json").write_text(json.dumps({"references": [
        {"title": "Paper plane flight", "authors": ["Smith", "Jones"], "year": 2020,
         "doi": "https://doi.org/10.1234/flight", "abstract": "<p>Two wing shapes.</p><p>Repeated launches.</p>"},
        {"title": "Wind tunnels", "year": 2021, "abstract": "Measured air flow."}]}))
    (root / "references.csv").write_text('title,year,doi\nPaper plane flight,2020,10.1234/flight\n')
    project.init_project(root)
    _scan(root)
    catalog = _catalog(root)
    assert not catalog["warnings"]
    assert len(catalog["items"]) == 2
    plane = next(i for i in catalog["items"] if i.get("doi"))
    assert plane["title"] == "Paper plane flight"
    assert plane["authors"] == ["Smith", "Jones"]
    assert plane["venue"] == "Flight Journal"
    assert plane["abstract"] == "Two wing shapes.\n\nRepeated launches."
    assert plane["abstract_source"] == "references.json"
    assert plane["files"] == ["references.bib", "references.csv", "references.json"]
    assert plane["doi_url"] == "https://doi.org/10.1234/flight"
    # Removing exports must not leave the old index's references as current items.
    for name in ("references.bib", "references.csv", "references.json"):
        (root / name).unlink()
    assert _catalog(root)["items"] == []


def test_bibtex_keeps_abstract_doi_and_nested_braces(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "sources.bib").write_text(
        '@article{flight, title={A {nested} title}, year=2020, doi={10.1234/plane}, '
        'abstract={An effect of {0.2}, with 50% success.}, booktitle={Proceedings}}')
    project.init_project(root)
    _scan(root)
    item, = _catalog(root)["items"]
    assert item["abstract"] == "An effect of {0.2}, with 50% success."
    assert item["doi"] == "10.1234/plane" and item["venue"] == "Proceedings"


def test_zotero_abstract_read_only_enriches_exact_project_doi(tmp_path, zotero):  # noqa: F811
    conn = sqlite3.connect(zotero.path)
    conn.execute("INSERT INTO fields VALUES (6, 'abstractNote', NULL)")
    conn.execute("INSERT INTO itemDataValues(valueID, value) VALUES (999, '<p>Choice under constraints.</p>')")
    conn.execute("INSERT INTO itemData SELECT itemID, 6, 999 FROM items WHERE key='SIMON55K'")
    conn.commit()
    conn.close()
    before = zotero.digest()
    library = literature.load_library()
    assert library.item("SIMON55K").metadata()["abstract"] == "Choice under constraints."
    root = tmp_path / "project"
    root.mkdir()
    (root / "references.bib").write_text('@article{s, title={Choice}, doi={10.2307/1884852}}')
    project.init_project(root)
    _scan(root)
    item, = _catalog(root)["items"]
    assert item["abstract"] == "Choice under constraints."
    assert item["abstract_source"] == "Zotero"
    assert item["pdf"]["item_key"] == "SIMON55K"
    assert zotero.digest() == before


def test_reference_catalog_refuses_changed_symlinks_and_corrupt_exports(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "references.json").write_text('[{"title":"Original"}]')
    project.init_project(root)
    _scan(root)
    outside = tmp_path / "outside.json"
    outside.write_text('[{"title":"Outside"}]')
    (root / "references.json").unlink()
    (root / "references.json").symlink_to(outside)
    catalog = _catalog(root)
    assert catalog["items"] == [] and len(catalog["warnings"]) == 1
    (root / "references.json").unlink()
    (root / "references.json").write_text('{broken')
    assert _catalog(root)["warnings"]


def test_different_dois_are_not_merged_by_title(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "references.json").write_text(json.dumps([
        {"title": "Same title", "year": 2020, "doi": "10.1234/one"},
        {"title": "Same title", "year": 2020, "doi": "10.1234/two"}]))
    project.init_project(root)
    _scan(root)
    assert len(_catalog(root)["items"]) == 2


def test_jats_abstract_is_plain_text_and_preserves_paragraphs():
    raw = '<jats:abstract><jats:p>An effect &lt; 0.05 and <i>italics</i>.</jats:p>'
    raw += '<script>untrusted()</script><jats:p>Second paragraph.</jats:p></jats:abstract>'
    result = literature.parse_crossref(json.dumps({"message": {"abstract": raw}}).encode())
    assert result["abstract"] == "An effect < 0.05 and italics.\n\nSecond paragraph."


@pytest.mark.parametrize("abstract", [None, "<jats:p>An observed effect.</jats:p>"])
def test_explicit_lookup_upgrades_old_cache_once_and_preserves_other_entries(monkeypatch, abstract):
    doi = "10.1234/plane"
    literature._write_json(literature.cache_path(), {doi: {"title": "Old cached title"}, "10.1234/other": {"title": "Other"}})
    literature.set_doi_lookup(True)
    calls = []
    def fetch(url, timeout):
        calls.append(url)
        return json.dumps({"message": {"title": ["Updated title"], "abstract": abstract}}).encode()
    monkeypatch.setattr(literature, "FETCH", fetch)
    first = literature_api.abstract_payload({"doi": doi})
    assert first["abstract"] == ("An observed effect." if abstract else None)
    assert first["checked"] is True
    assert literature_api.abstract_payload({"doi": doi}) == first
    assert calls == ["https://api.crossref.org/works/10.1234/plane"]
    assert literature.cached("10.1234/other")["title"] == "Other"


def test_failed_abstract_lookup_is_not_cached_and_can_be_retried(monkeypatch):
    literature.set_doi_lookup(True)
    def offline(url, timeout):
        raise literature.LookupFailed("offline", network=True)
    monkeypatch.setattr(literature, "FETCH", offline)
    with pytest.raises(literature_api.LiteratureRefused) as err:
        literature_api.abstract_payload({"doi": "10.1234/plane"})
    assert err.value.status == 502 and literature.cached("10.1234/plane") is None


def test_reading_library_is_offline_but_abstract_fetch_requires_opt_in(lit_server, monkeypatch):  # noqa: F811
    live, root = lit_server
    assert live.get("/api/citations")[0] == 200
    status, data = live.post("/api/literature/abstract", {"doi": "10.1234/plane"})
    assert status == 409 and data["state"] == "literature_lookup_off"
    assert live.post("/api/literature/abstract", {"doi": "https://example.com"})[0] == 400
    literature.set_doi_lookup(True)
    monkeypatch.setattr(literature, "FETCH", lambda url, timeout: json.dumps({"message": {"abstract": "Plain abstract."}}).encode())
    status, data = live.post("/api/literature/abstract", {"doi": "10.1234/plane"})
    assert status == 200 and data["abstract"] == "Plain abstract."


def test_zotero_candidates_without_a_doi_keep_their_library_address(lit_server):  # noqa: F811
    live, root = lit_server
    status, data = live.get("/api/citations")
    assert status == 200
    item = next(i for i in data["library"]["items"] if i["title"] == "Jones one")
    assert item["doi"] is None
    assert item["zotero_key"] == "JONES01A"
    assert item["zotero_url"] == "zotero://select/library/items/JONES01A"


def test_sources_are_bounded_and_do_not_treat_every_json_as_a_reference_list(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    (root / "references.json").write_text('[{"title":"Too large for this test"}]')
    (root / "model.json").write_text('[{"title":"Not a bibliography"}]')
    project.init_project(root)
    _scan(root)
    monkeypatch.setattr(bibliography, "MAX_SOURCE_BYTES", 10)
    catalog = _catalog(root)
    assert catalog["items"] == [] and [w["file"] for w in catalog["warnings"]] == ["references.json"]
