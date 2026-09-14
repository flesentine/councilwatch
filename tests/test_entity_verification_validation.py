import json
from types import SimpleNamespace

import meeting_intelligence as mi
import street_registry


class _FakeModels:
    def __init__(self, payload):
        self._payload = payload

    def generate_content(self, **_kwargs):
        return SimpleNamespace(text=json.dumps(self._payload))


class _FakeClient:
    def __init__(self, payload):
        self.models = _FakeModels(payload)


def _install_verifier(monkeypatch, entities, support=None):
    payload = {"entities": entities}
    monkeypatch.setattr(
        mi.genai,
        "Client",
        lambda: _FakeClient(payload),
    )
    monkeypatch.setattr(
        mi,
        "official_entity_material",
        lambda _meeting: {
            "text": "Official city roster material",
            "pages": [
                {
                    "url": "https://city.example.gov/roster",
                    "text": "Official city roster material",
                }
            ],
        },
    )

    support = support or {}

    def fake_find_official_support(value, _agenda, _agenda_url, _pages):
        return support.get(value, "")

    monkeypatch.setattr(
        mi,
        "find_official_support",
        fake_find_official_support,
    )


def _meeting():
    return {
        "city_name": "Testville",
        "meeting_date": "2026-09-01",
        "agenda_url": "https://city.example.gov/agenda",
    }


def test_verify_entities_promotes_exact_official_support_and_demotes_unsupported_claims(monkeypatch):
    _install_verifier(
        monkeypatch,
        [
            {
                "observed_text": "Jane Doe",
                "canonical_text": "Jane Doe",
                "entity_type": "person",
                "status": "UNVERIFIED",
                "confidence": "low",
                "evidence": "model was cautious",
            },
            {
                "observed_text": "OC Transportation",
                "canonical_text": "Orange County Transportation Authority",
                "entity_type": "organization",
                "status": "CORRECTED",
                "confidence": "medium",
                "evidence": "official name proposed",
            },
            {
                "observed_text": "Mystery Project",
                "canonical_text": "Unsupported Project Name",
                "entity_type": "project",
                "status": "VERIFIED",
                "confidence": "high",
                "evidence": "unsupported model claim",
            },
            {
                "observed_text": "Verified Project",
                "canonical_text": "Verified Project",
                "entity_type": "project",
                "status": "MAYBE",
                "confidence": "medium",
                "evidence": "invalid model status",
            },
        ],
        support={
            "Jane Doe": "https://city.example.gov/agenda",
            "Orange County Transportation Authority": "https://octa.net/about",
            "Verified Project": "https://city.example.gov/projects/verified",
        },
    )

    result = mi.verify_entities(
        _meeting(),
        "Jane Doe discussed the Verified Project.",
        "Official agenda text",
    )

    by_observed = {item["observed_text"]: item for item in result}

    assert by_observed["Jane Doe"]["status"] == "VERIFIED"
    assert by_observed["Jane Doe"]["confidence"] == "high"
    assert by_observed["Jane Doe"]["official_source_url"].endswith("/agenda")

    assert by_observed["OC Transportation"]["status"] == "CORRECTED"
    assert by_observed["OC Transportation"]["canonical_text"] == (
        "Orange County Transportation Authority"
    )

    assert by_observed["Mystery Project"]["status"] == "UNVERIFIED"
    assert by_observed["Mystery Project"]["canonical_text"] == "Mystery Project"
    assert by_observed["Mystery Project"]["confidence"] == "low"

    assert by_observed["Verified Project"]["status"] == "VERIFIED"
    assert by_observed["Verified Project"]["confidence"] == "high"


def test_verify_entities_rejects_unrelated_person_identity_even_when_official_exists(monkeypatch):
    _install_verifier(
        monkeypatch,
        [
            {
                "observed_text": "Mr. Ordona",
                "canonical_text": "Kevin O'Connor",
                "entity_type": "person",
                "status": "CORRECTED",
                "confidence": "high",
                "evidence": "directory contains Kevin O'Connor",
            }
        ],
        support={
            "Kevin O'Connor": "https://city.example.gov/staff/oconnor",
        },
    )

    result = mi.verify_entities(
        _meeting(),
        "Mr. Ordona spoke during public comment.",
        "Official agenda text",
    )

    item = result[0]
    assert item["observed_text"] == "Mr. Ordona"
    assert item["canonical_text"] == "Mr. Ordona"
    assert item["status"] == "UNVERIFIED"
    assert item["confidence"] == "low"
    assert "rejected the proposed identity" in item["evidence"]
    assert item["official_source_url"] == ""


def test_verify_entities_accepts_plausible_person_correction_with_official_support(monkeypatch):
    _install_verifier(
        monkeypatch,
        [
            {
                "observed_text": "O'Conner",
                "canonical_text": "O'Connor",
                "entity_type": "person",
                "status": "UNVERIFIED",
                "confidence": "low",
                "evidence": "phonetic transcript form",
            }
        ],
        support={
            "O'Connor": "https://city.example.gov/roster",
        },
    )

    result = mi.verify_entities(
        _meeting(),
        "Council Member O'Conner spoke.",
        "Official agenda text",
    )

    corrected = next(
        item for item in result if item["observed_text"] == "O'Conner"
    )
    assert corrected["canonical_text"] == "O'Connor"
    assert corrected["status"] == "CORRECTED"
    assert corrected["confidence"] == "high"


def test_verify_entities_prefers_exact_observed_source_when_proposed_canonical_is_unsupported(monkeypatch):
    _install_verifier(
        monkeypatch,
        [
            {
                "observed_text": "RSM City Hall",
                "canonical_text": "City Hall",
                "entity_type": "place",
                "status": "CORRECTED",
                "confidence": "medium",
                "evidence": "model simplification",
            }
        ],
        support={
            "RSM City Hall": "https://city.example.gov/facilities/city-hall",
        },
    )
    monkeypatch.setattr(street_registry, "looks_like_street", lambda _value: False)

    result = mi.verify_entities(
        _meeting(),
        "The meeting was held at RSM City Hall.",
        "Official agenda text",
    )

    item = result[0]
    assert item["canonical_text"] == "RSM City Hall"
    assert item["status"] == "VERIFIED"
    assert item["confidence"] == "high"
    assert item["official_source_url"].endswith("/city-hall")


def test_verify_entities_uses_street_registry_before_generic_official_support(monkeypatch):
    _install_verifier(
        monkeypatch,
        [
            {
                "observed_text": "Santa Margerita Parkway",
                "canonical_text": "Santa Margerita Parkway",
                "entity_type": "place",
                "status": "UNVERIFIED",
                "confidence": "low",
                "evidence": "transcript spelling",
            }
        ],
    )

    monkeypatch.setattr(street_registry, "looks_like_street", lambda _value: True)
    monkeypatch.setattr(street_registry, "street_name_part", lambda value: value)
    monkeypatch.setattr(
        street_registry,
        "match_street",
        lambda _value: {
            "canonical_text": "Santa Margarita Parkway",
            "status": "CORRECTED",
            "confidence": "high",
            "match_type": "fuzzy",
        },
    )
    monkeypatch.setattr(
        street_registry,
        "source_url",
        lambda: "https://gis.example.gov/streets",
    )

    result = mi.verify_entities(
        _meeting(),
        "Santa Margerita Parkway was discussed.",
        "Official agenda text",
    )

    item = result[0]
    assert item["entity_type"] == "street"
    assert item["canonical_text"] == "Santa Margarita Parkway"
    assert item["status"] == "CORRECTED"
    assert item["verification_source"] == "orange_county_street_registry"
    assert item["official_source_url"] == "https://gis.example.gov/streets"


def test_verify_entities_retains_omitted_role_labeled_official_as_unverified(monkeypatch):
    _install_verifier(monkeypatch, [])

    result = mi.verify_entities(
        _meeting(),
        "Council Member Alice Jones spoke about traffic safety.",
        "Official agenda text",
    )

    assert result == [
        {
            "observed_text": "Council Member Alice Jones",
            "canonical_text": "Council Member Alice Jones",
            "entity_type": "person",
            "status": "UNVERIFIED",
            "confidence": "low",
            "evidence": (
                "The recording explicitly identified this speaker by elected-official "
                "role, but the secondary verifier omitted the candidate. CouncilWatch "
                "retained it as UNVERIFIED rather than silently dropping it."
            ),
            "official_source_url": "",
        }
    ]


def test_verify_entities_skips_invalid_rows_and_fills_missing_observed_or_canonical(monkeypatch):
    _install_verifier(
        monkeypatch,
        [
            "not a mapping",
            {},
            {
                "observed_text": "",
                "canonical_text": "Canonical Agency",
                "entity_type": "organization",
                "status": "UNVERIFIED",
            },
            {
                "observed_text": "Observed Program",
                "canonical_text": "",
                "entity_type": "program",
                "status": "UNVERIFIED",
            },
        ],
        support={
            "Canonical Agency": "https://city.example.gov/agency",
        },
    )

    result = mi.verify_entities(
        _meeting(),
        "Observed Program was mentioned.",
        "Official agenda text",
    )

    assert len(result) == 2
    assert result[0]["observed_text"] == "Canonical Agency"
    assert result[0]["canonical_text"] == "Canonical Agency"
    assert result[0]["status"] == "VERIFIED"

    assert result[1]["observed_text"] == "Observed Program"
    assert result[1]["canonical_text"] == "Observed Program"
    assert result[1]["status"] == "UNVERIFIED"
