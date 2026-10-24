"""Missing Crossref abstracts can be supplemented without mislabelling sources."""

import json
from datetime import date, timedelta

import pytest

from researchos import bibliography, literature
from researchos.webapp import literature_api

DOI = "10.1234/plane"


def _answer(abstract=True, doi=DOI):
    return json.dumps({"id": "https://openalex.org/W123", "doi": "https://doi.org/" + doi,
                       "abstract_inverted_index": {"Repeated": [0], "launches": [1], "matter.": [2]} if abstract else None}).encode()


def _crossref_cache(abstract=None):
    literature._write_json(literature.cache_path(), {DOI: {"title": "Paper planes", "abstract": abstract}})
    literature.set_doi_lookup(True)


def test_old_crossref_negative_cache_does_not_prevent_openalex_lookup(monkeypatch):
    _crossref_cache()
    calls = []
    def fetch(url, timeout):
        calls.append(url)
        return _answer()
    monkeypatch.setattr(literature, "FETCH_OPENALEX", fetch)
    result = literature_api.abstract_payload({"doi": DOI})
    assert result["abstract"] == "Repeated launches matter."
    assert result["source"] == "OpenAlex" and result["source_url"] == "https://openalex.org/W123"
    assert len(calls) == 1 and DOI in calls[0]
    assert literature_api.abstract_payload({"doi": DOI}) == result
    assert len(calls) == 1
    # An older app still reads the Crossref cache: never label OpenAlex text as Crossref.
    assert literature.cached(DOI)["abstract"] is None
    assert literature._read_json(literature.abstract_cache_path())[DOI]["source"] == "OpenAlex"


def test_crossref_abstract_needs_no_second_provider(monkeypatch):
    _crossref_cache("An existing publisher abstract.")
    monkeypatch.setattr(literature, "FETCH_OPENALEX", lambda *args: pytest.fail("second provider was unnecessary"))
    result = literature_api.abstract_payload({"doi": DOI})
    assert result["abstract"] == "An existing publisher abstract." and result["source"] == "Crossref"


def test_openalex_can_succeed_when_crossref_is_temporarily_unavailable(monkeypatch):
    literature.set_doi_lookup(True)
    def offline(*args):
        raise literature.LookupFailed("Crossref timeout", network=True)
    monkeypatch.setattr(literature, "FETCH", offline)
    monkeypatch.setattr(literature, "FETCH_OPENALEX", lambda *args: _answer())
    assert literature_api.abstract_payload({"doi": DOI})["source"] == "OpenAlex"


def test_incomplete_lookup_is_not_reported_as_no_abstract(monkeypatch):
    _crossref_cache()
    def limited(*args):
        raise literature.LookupFailed("OpenAlex HTTP 429")
    monkeypatch.setattr(literature, "FETCH_OPENALEX", limited)
    with pytest.raises(literature_api.LiteratureRefused) as err:
        literature_api.abstract_payload({"doi": DOI})
    assert err.value.status == 502
    assert DOI not in literature._read_json(literature.abstract_cache_path())


def test_negative_lookup_can_be_forced_to_retry_and_expires_after_a_week(monkeypatch):
    _crossref_cache()
    calls = []
    def missing(*args):
        calls.append("OpenAlex")
        return _answer(False)
    monkeypatch.setattr(literature, "FETCH_OPENALEX", missing)
    monkeypatch.setattr(literature, "FETCH", lambda *args: b'{"message":{}}')
    assert literature_api.abstract_payload({"doi": DOI})["abstract"] is None
    assert literature_api.abstract_payload({"doi": DOI})["abstract"] is None
    assert calls == ["OpenAlex"]
    literature_api.abstract_payload({"doi": DOI, "refresh": True})
    assert calls == ["OpenAlex", "OpenAlex"]
    cached = literature._read_json(literature.abstract_cache_path())
    cached[DOI]["checked_at"] = (date.today() - timedelta(days=8)).isoformat()
    literature._write_json(literature.abstract_cache_path(), cached)
    literature_api.abstract_payload({"doi": DOI})
    assert len(calls) == 3


@pytest.mark.parametrize("index", [
    {"wrong": [99999999]}, {"a": [0], "b": [0]}, {"hole": [1]}, {"bad": [-1]},
    {"bad": [True]}, {"bad": "0"}, ["wrong"],
])
def test_invalid_or_incomplete_word_positions_never_become_an_abstract(index):
    body = json.loads(_answer())
    body["abstract_inverted_index"] = index
    with pytest.raises(literature.LookupFailed):
        literature.parse_openalex(json.dumps(body).encode(), DOI)


def test_exact_doi_is_required_and_valid_positions_are_restored_in_order():
    with pytest.raises(literature.LookupFailed, match="different DOI"):
        literature.parse_openalex(_answer(doi="10.1234/different"), DOI)
    assert literature.parse_openalex(_answer(), DOI)["abstract"] == "Repeated launches matter."
    assert literature.parse_openalex(b"null", DOI)["abstract"] is None


def test_catalog_reads_supplementary_abstract_with_its_actual_source(conn, tmp_path):
    _crossref_cache()
    literature._write_json(literature.abstract_cache_path(), {
        DOI: {"abstract": "Repeated launches matter.", "source": "OpenAlex", "source_url": "https://openalex.org/W123",
              "checked_at": date.today().isoformat()}})
    report = {"drafts": [{"citations": [{"targets": [{"title": "Paper planes", "doi": DOI, "source": "crossref"}]}]}]}
    lib = literature.ZoteroLibrary(None, literature.LIBRARY_ABSENT)
    item, = bibliography.catalog(conn, tmp_path, report, lib)["items"]
    assert item["abstract_source"] == "OpenAlex" and item["abstract_source_url"] == "https://openalex.org/W123"
    assert item["abstract_checked"] is True


def test_supplementary_cache_is_offline_when_just_reading_the_catalog(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(literature, "FETCH_OPENALEX", lambda *args: pytest.fail("catalog used the network"))
    report = {"drafts": [{"citations": [{"targets": [{"title": "Paper planes", "doi": DOI}]}]}]}
    item, = bibliography.catalog(conn, tmp_path, report, literature.ZoteroLibrary(None, literature.LIBRARY_ABSENT))["items"]
    assert not item["abstract"] and not item["abstract_checked"]


def test_refresh_option_must_be_boolean():
    literature.set_doi_lookup(True)
    with pytest.raises(literature_api.LiteratureRefused) as err:
        literature_api.abstract_payload({"doi": DOI, "refresh": "true"})
    assert err.value.status == 400


def test_provider_redirects_never_leave_the_expected_https_host():
    import urllib.request
    handler = literature._OpenAlexRedirectsOnly()
    request = urllib.request.Request("https://api.openalex.org/works/x")
    for target in ("http://api.openalex.org/works/x", "https://evil.example/", "https://api.crossref.org/works/x"):
        assert handler.redirect_request(request, None, 302, "", {}, target) is None
