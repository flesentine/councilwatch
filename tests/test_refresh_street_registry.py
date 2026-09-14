import io
import json
import urllib.parse

import pytest

import refresh_street_registry as refresh


def test_smart_title_preserves_internal_lowercase_words():
    assert refresh.smart_title("  VIA DE LA PAZ  ") == "Via de la Paz"
    assert refresh.smart_title("DE ANZA") == "De Anza"
    assert refresh.smart_title("") == ""


def test_make_name_expands_suffixes_prefixes_and_embedded_types():
    assert refresh.make_name(None, "main", "st") == "Main Street"
    assert refresh.make_name("n", "main", "rd") == "N Main Road"
    assert refresh.make_name("", "el toro rd", "") == "El Toro Road"
    assert refresh.make_name("", "civic center", "") == "Civic Center"
    assert refresh.make_name("", "harbor", "allee") == "Harbor Allee"
    assert refresh.make_name(None, None, None) == ""


def test_fetch_page_builds_expected_arcgis_request(monkeypatch):
    payload = {"features": [{"attributes": {"OBJECTID": 7}}]}
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["timeout"] = timeout
        return io.StringIO(json.dumps(payload))

    monkeypatch.setattr(refresh, "BATCH", 25)
    monkeypatch.setattr(refresh.urllib.request, "urlopen", fake_urlopen)

    assert refresh.fetch_page(50) == payload

    parsed = urllib.parse.urlparse(captured["url"])
    query = urllib.parse.parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "ocgis.com"
    assert parsed.path.endswith("/FeatureServer/2/query")
    assert query == {
        "where": ["STREETNAME IS NOT NULL"],
        "outFields": [
            "OBJECTID,STREETNAME,OLDSTREETNAME,PREFIX,SUFFIX,STREETCODE"
        ],
        "returnGeometry": ["false"],
        "resultOffset": ["50"],
        "resultRecordCount": ["25"],
        "orderByFields": ["OBJECTID"],
        "f": ["json"],
    }
    assert captured["headers"]["User-agent"] == "CouncilWatch/1.0"
    assert captured["timeout"] == 60


def test_main_paginates_aggregates_deduplicates_and_sorts(tmp_path, monkeypatch, capsys):
    out = tmp_path / "street_registry.json"
    pages = {
        0: {
            "features": [
                {
                    "attributes": {
                        "STREETNAME": "MAIN",
                        "OLDSTREETNAME": "OLD MAIN",
                        "PREFIX": "",
                        "SUFFIX": "ST",
                        "STREETCODE": 20,
                    }
                },
                {
                    "attributes": {
                        "STREETNAME": "main",
                        "OLDSTREETNAME": "HISTORIC MAIN",
                        "PREFIX": None,
                        "SUFFIX": "st",
                        "STREETCODE": 10,
                    }
                },
                {
                    "attributes": {
                        "STREETNAME": "ZETA",
                        "OLDSTREETNAME": "",
                        "PREFIX": "s",
                        "SUFFIX": "AV",
                        "STREETCODE": None,
                    }
                },
            ]
        },
        3: {
            "features": [
                {
                    "attributes": {
                        "STREETNAME": "ALPHA RD",
                        "OLDSTREETNAME": "CAÑÓN VIEJO",
                        "PREFIX": "",
                        "SUFFIX": "",
                        "STREETCODE": 30,
                    }
                },
                {"attributes": {}},
            ]
        },
    }
    calls = []

    def fake_fetch_page(offset):
        calls.append(offset)
        return pages[offset]

    monkeypatch.setattr(refresh, "BATCH", 3)
    monkeypatch.setattr(refresh, "OUT", out)
    monkeypatch.setattr(refresh, "fetch_page", fake_fetch_page)

    refresh.main()

    assert calls == [0, 3]
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema_version"] == 2
    assert data["source"] == "Orange County Street Centerlines With Labels"
    assert data["source_agency"] == "County of Orange GIS"
    assert data["source_url"].endswith("/FeatureServer/2")
    assert data["feature_rows"] == 4
    assert [row["canonical"] for row in data["streets"]] == [
        "Alpha Road",
        "Main Street",
        "S Zeta Avenue",
    ]

    by_name = {row["canonical"]: row for row in data["streets"]}
    assert by_name["Main Street"] == {
        "canonical": "Main Street",
        "official_streetname": "MAIN",
        "prefix": "",
        "suffix": "ST",
        "old_names": ["Historic Main", "Old Main"],
        "street_codes": [10, 20],
    }
    assert by_name["Alpha Road"]["old_names"] == ["Cañón Viejo"]
    assert by_name["Alpha Road"]["street_codes"] == [30]
    assert by_name["S Zeta Avenue"]["old_names"] == []
    assert by_name["S Zeta Avenue"]["street_codes"] == []
    assert out.read_text(encoding="utf-8").endswith("\n")
    assert "Fetched 3 rows at offset 0" in capsys.readouterr().out


def test_main_raises_on_arcgis_error_without_writing_output(tmp_path, monkeypatch):
    out = tmp_path / "street_registry.json"
    monkeypatch.setattr(refresh, "OUT", out)
    monkeypatch.setattr(
        refresh,
        "fetch_page",
        lambda offset: {"error": {"code": 500, "message": "service down"}},
    )

    with pytest.raises(RuntimeError) as excinfo:
        refresh.main()

    assert '"code": 500' in str(excinfo.value)
    assert "service down" in str(excinfo.value)
    assert not out.exists()


def test_main_writes_empty_registry_for_empty_page(tmp_path, monkeypatch):
    out = tmp_path / "street_registry.json"
    monkeypatch.setattr(refresh, "OUT", out)
    monkeypatch.setattr(refresh, "fetch_page", lambda offset: {"features": []})

    refresh.main()

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["feature_rows"] == 0
    assert data["streets"] == []


def test_main_skips_nonempty_street_when_name_builder_returns_empty(tmp_path, monkeypatch):
    out = tmp_path / "street_registry.json"
    monkeypatch.setattr(refresh, "OUT", out)
    monkeypatch.setattr(
        refresh,
        "fetch_page",
        lambda offset: {
            "features": [
                {
                    "attributes": {
                        "STREETNAME": "MAIN",
                        "OLDSTREETNAME": "OLD MAIN",
                        "PREFIX": "",
                        "SUFFIX": "ST",
                        "STREETCODE": 1,
                    }
                }
            ]
        },
    )
    monkeypatch.setattr(refresh, "make_name", lambda *args: "")

    refresh.main()

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["feature_rows"] == 0
    assert data["streets"] == []
