import json

import pytest

import street_registry


@pytest.fixture
def small_registry(tmp_path, monkeypatch):
    data = {
        "schema_version": 2,
        "source_url": "https://example.test/orange-county-streets",
        "streets": [
            {
                "canonical": "Main Street",
                "old_names": ["Old Main Street", ""],
            },
            {
                "canonical": "Joaquin Road",
                "old_names": [],
            },
            {
                "canonical": "Joaquin Street",
                "old_names": [],
            },
            {
                "canonical": "Maple Road",
                "old_names": [],
            },
            {
                "canonical": "Maple Street",
                "old_names": [],
            },
            {
                "canonical": "A-B Road",
                "old_names": [],
            },
            {
                "canonical": "A B Road",
                "old_names": [],
            },
        ],
    }

    path = tmp_path / "street_registry.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(street_registry, "REGISTRY_PATH", path)
    street_registry.load_registry.cache_clear()

    yield data

    street_registry.load_registry.cache_clear()


def test_normalize_handles_diacritics_punctuation_and_split_types():
    assert street_registry.normalize("  Cañon Park Way  ") == "canon parkway"
    assert street_registry.normalize("High Way") == "highway"
    assert street_registry.normalize("Boule-Vard") == "boule vard"


def test_street_type_and_base_without_type():
    assert street_registry.street_type("Main Street") == "street"
    assert street_registry.street_type("Via Verde") == ""
    assert street_registry.base_without_type("Main Street") == "main"
    assert street_registry.base_without_type("Via Verde") == "via verde"


def test_street_name_part_strips_supported_address_numbers():
    assert street_registry.street_name_part("26200 Enterprise Way") == "Enterprise Way"
    assert street_registry.street_name_part("123A Main Street") == "Main Street"
    assert street_registry.street_name_part("123-45 Main Street") == "Main Street"
    assert street_registry.street_name_part("Main Street") == "Main Street"
    assert street_registry.street_name_part(None) == ""


def test_looks_like_street_is_conservative():
    assert street_registry.looks_like_street("") is False
    assert street_registry.looks_like_street("123 Main Street") is True
    assert street_registry.looks_like_street("Via Verde") is True
    assert street_registry.looks_like_street("123A Calle Empresa") is True
    assert street_registry.looks_like_street("Central Park") is False


def test_phonetic_key_normalizes_common_transcription_variants():
    assert street_registry.phonetic_key("Joaquin Road") == street_registry.phonetic_key(
        "Hoaquin Road"
    )
    assert street_registry.phonetic_key("Phillip Street") == "flp"
    assert street_registry.phonetic_key("Quack Road") == "k"


def test_load_registry_builds_canonical_and_old_name_rows(small_registry):
    data, rows = street_registry.load_registry()

    assert data is small_registry
    assert {row["name"] for row in rows} >= {
        "Main Street",
        "Old Main Street",
        "Joaquin Road",
    }
    assert "" not in {row["name"] for row in rows}

    main_rows = [row for row in rows if row["row"]["canonical"] == "Main Street"]
    assert {row["type"] for row in main_rows} == {"street"}
    assert all(row["phonetic"] == street_registry.phonetic_key(row["name"]) for row in rows)


def test_load_registry_is_cached(small_registry):
    first = street_registry.load_registry()
    second = street_registry.load_registry()

    assert first is second


def test_source_url_comes_from_registry_metadata(small_registry):
    assert street_registry.source_url() == "https://example.test/orange-county-streets"


def test_match_street_rejects_empty_input(small_registry):
    assert street_registry.match_street("") is None
    assert street_registry.match_street("   ") is None


def test_match_street_exact_canonical_preserves_observed_address(small_registry):
    result = street_registry.match_street("26200 Main Street")

    assert result == {
        "observed_text": "26200 Main Street",
        "canonical_text": "Main Street",
        "status": "VERIFIED",
        "confidence": "high",
        "match_type": "exact",
        "source": "orange_county_street_registry",
    }


def test_match_street_exact_old_name_is_a_correction(small_registry):
    result = street_registry.match_street("Old Main Street")

    assert result == {
        "observed_text": "Old Main Street",
        "canonical_text": "Main Street",
        "status": "CORRECTED",
        "confidence": "high",
        "match_type": "exact_alias",
        "source": "orange_county_street_registry",
    }


def test_match_street_requires_matching_road_type_for_phonetic_match(small_registry):
    result = street_registry.match_street("Hoaquin Road")

    assert result is not None
    assert result["canonical_text"] == "Joaquin Road"
    assert result["match_type"] == "unique_phonetic"
    assert result["status"] == "CORRECTED"


def test_match_street_uses_conservative_unique_fuzzy_match(small_registry):
    result = street_registry.match_street(
        "Mair Street",
        fuzzy_threshold=0.70,
        minimum_margin=0.05,
    )

    assert result is not None
    assert result["canonical_text"] == "Main Street"
    assert result["match_type"] == "unique_fuzzy"
    assert result["status"] == "CORRECTED"
    assert result["score"] >= 0.70
    assert result["score"] - result["runner_up_score"] >= 0.05


def test_match_street_rejects_below_threshold_fuzzy_match(small_registry):
    assert street_registry.match_street("Totally Different Road") is None


def test_match_street_rejects_normalized_canonical_collision(small_registry):
    # These are distinct canonical strings in the fixture but normalize to the
    # same text. The matcher must fail closed instead of choosing arbitrarily.
    assert street_registry.match_street("A-B Road") is None


def test_match_street_rejects_insufficient_fuzzy_margin(small_registry):
    result = street_registry.match_street(
        "Mabple Road",
        fuzzy_threshold=0.50,
        minimum_margin=0.99,
    )

    assert result is None


def test_match_street_handles_registry_with_no_candidates(monkeypatch):
    monkeypatch.setattr(street_registry, "load_registry", lambda: ({}, []))

    assert street_registry.match_street("Main Road", fuzzy_threshold=0.0) is None
