import entity_registry


def test_normalize_collapses_acronyms_symbols_and_diacritics():
    assert (
        entity_registry.normalize("  O C F A & Café—District  ")
        == "ocfa and cafe district"
    )


def test_load_registry_builds_normalized_ambiguities_and_aliases():
    entity_registry.load_registry.cache_clear()

    data, ambiguous, aliases = entity_registry.load_registry()

    assert data["schema_version"] == 1
    assert "ceo" in ambiguous
    assert "hca" in ambiguous

    ocfa = [
        (alias_norm, alias, entity)
        for alias_norm, alias, entity in aliases
        if alias_norm == "ocfa"
    ]
    assert len(ocfa) == 1
    assert ocfa[0][1] == "OCFA"
    assert ocfa[0][2]["canonical"] == "Orange County Fire Authority"


def test_load_registry_is_cached():
    entity_registry.load_registry.cache_clear()

    first = entity_registry.load_registry()
    second = entity_registry.load_registry()

    assert first is second


def test_match_entity_rejects_empty_and_ambiguous_queries():
    assert entity_registry.match_entity("") is None
    assert entity_registry.match_entity("   ") is None
    assert entity_registry.match_entity("CEO") is None
    assert entity_registry.match_entity("C E O") is None
    assert entity_registry.match_entity("HCA") is None


def test_match_entity_prefers_exact_alias_with_registry_metadata():
    result = entity_registry.match_entity("OCFA")

    assert result == {
        "heard": "OCFA",
        "canonical": "Orange County Fire Authority",
        "matched_alias": "OCFA",
        "match_type": "exact",
        "confidence": 1.0,
        "jurisdiction": "orange_county",
        "entity_type": "public_safety",
        "source": "local_entity_registry",
    }


def test_match_entity_normalization_allows_spaced_acronym_exact_match():
    result = entity_registry.match_entity("O C F A")

    assert result is not None
    assert result["canonical"] == "Orange County Fire Authority"
    assert result["match_type"] == "exact"
    assert result["confidence"] == 1.0


def test_match_entity_uses_conservative_fuzzy_match_for_minor_typo():
    result = entity_registry.match_entity("Orange County Fire Authorit")

    assert result is not None
    assert result["canonical"] == "Orange County Fire Authority"
    assert result["matched_alias"] == "Orange County Fire Authority"
    assert result["match_type"] == "fuzzy"
    assert 0.89 <= result["confidence"] < 1.0


def test_match_entity_respects_fuzzy_threshold_and_rejects_unknown_text():
    assert (
        entity_registry.match_entity(
            "Orange County Fire Authorit",
            fuzzy_threshold=0.999,
        )
        is None
    )
    assert entity_registry.match_entity("totally unrelated private company") is None


def test_find_entities_in_text_returns_explicit_registry_entities():
    results = entity_registry.find_entities_in_text(
        "OCFA responded near John Wayne Airport while OCTA coordinated traffic."
    )

    by_name = {row["canonical_text"]: row for row in results}

    assert set(by_name) == {
        "Orange County Fire Authority",
        "John Wayne Airport",
        "Orange County Transportation Authority",
    }

    assert by_name["John Wayne Airport"]["entity_type"] == "place"
    assert by_name["John Wayne Airport"]["registry_entity_type"] == "public_facility"
    assert by_name["Orange County Fire Authority"]["entity_type"] == "government_body"
    assert by_name["Orange County Fire Authority"]["status"] == "VERIFIED"
    assert by_name["Orange County Fire Authority"]["confidence"] == "high"
    assert by_name["Orange County Fire Authority"]["verification_source"] == "local_entity_registry"


def test_find_entities_in_text_rejects_ambiguous_and_municipal_short_aliases():
    assert (
        entity_registry.find_entities_in_text(
            "The CEO briefed Council and Public Works before the meeting."
        )
        == []
    )


def test_find_entities_in_text_accepts_municipal_canonical_names():
    results = entity_registry.find_entities_in_text(
        "The Public Works Department briefed the Planning Commission."
    )

    assert {row["canonical_text"] for row in results} == {
        "Public Works Department",
        "Planning Commission",
    }
    assert all(row["jurisdiction"] == "municipal" for row in results)


def test_find_entities_in_text_prefers_longest_overlapping_alias():
    results = entity_registry.find_entities_in_text(
        "The Orange County Board of Supervisors approved the item."
    )

    assert len(results) == 1
    assert results[0]["canonical_text"] == "Orange County Board of Supervisors"
    assert results[0]["observed_text"] == "Orange County Board of Supervisors"


def test_find_entities_in_text_returns_one_row_per_canonical_entity():
    results = entity_registry.find_entities_in_text(
        "OCFA coordinated with the Orange County Fire Authority."
    )

    assert len(results) == 1
    assert results[0]["canonical_text"] == "Orange County Fire Authority"


def test_find_entities_in_text_respects_token_boundaries():
    assert entity_registry.find_entities_in_text("The OCTAX proposal advanced.") == []


def test_find_entities_in_text_handles_empty_input():
    assert entity_registry.find_entities_in_text("") == []
    assert entity_registry.find_entities_in_text("   ") == []
