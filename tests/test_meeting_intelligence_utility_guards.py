from types import SimpleNamespace

import pytest

import meeting_intelligence as mi


def test_retry_api_call_returns_immediately_on_success(monkeypatch):
    sleeps = []
    monkeypatch.setattr(mi.time, "sleep", lambda seconds: sleeps.append(seconds))

    assert mi.retry_api_call("test", lambda: "ok") == "ok"
    assert sleeps == []


def test_retry_api_call_retries_temporary_failure_with_backoff(monkeypatch):
    calls = []
    sleeps = []

    def fn():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("503 UNAVAILABLE")
        return "ok"

    monkeypatch.setattr(mi.time, "sleep", lambda seconds: sleeps.append(seconds))

    assert mi.retry_api_call("test", fn) == "ok"
    assert len(calls) == 2
    assert sleeps == [20]


def test_retry_api_call_honors_larger_suggested_retry_interval(monkeypatch):
    calls = []
    sleeps = []

    def fn():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("503 retrying in 30.5s")
        return "ok"

    monkeypatch.setattr(mi.time, "sleep", lambda seconds: sleeps.append(seconds))

    assert mi.retry_api_call("test", fn) == "ok"
    assert sleeps == [33]


def test_retry_api_call_does_not_retry_non_temporary_error(monkeypatch):
    sleeps = []
    monkeypatch.setattr(mi.time, "sleep", lambda seconds: sleeps.append(seconds))

    with pytest.raises(RuntimeError, match="bad request"):
        mi.retry_api_call(
            "test",
            lambda: (_ for _ in ()).throw(RuntimeError("bad request")),
        )

    assert sleeps == []


def test_retry_api_call_defers_bare_quota_429_without_hammering(monkeypatch):
    sleeps = []
    monkeypatch.setattr(mi.time, "sleep", lambda seconds: sleeps.append(seconds))

    with pytest.raises(RuntimeError, match="429"):
        mi.retry_api_call(
            "test",
            lambda: (_ for _ in ()).throw(RuntimeError("429 RESOURCE_EXHAUSTED")),
        )

    assert sleeps == []


def test_parse_json_accepts_plain_fenced_and_embedded_objects():
    assert mi._parse_json('{"ok": true}') == {"ok": True}
    assert mi._parse_json('```json\n{"value": 3}\n```') == {"value": 3}
    assert mi._parse_json('Model says: {"name": "RSM"} end') == {"name": "RSM"}

    with pytest.raises(ValueError, match="parseable JSON"):
        mi._parse_json("no object here")


def test_domains_for_combines_city_defaults_and_meeting_hosts():
    domains = mi._domains_for(
        {
            "city_slug": "rsm",
            "source_url": "https://meetings.example.gov/watch/123",
            "agenda_url": "https://agenda.example.org/item/15",
        }
    )

    assert "cityofrsm.org" in domains
    assert "cityofrsm.granicus.com" in domains
    assert "meetings.example.gov" in domains
    assert "agenda.example.org" in domains


def test_host_allowed_accepts_government_public_agency_and_known_subdomains():
    domains = {"city.example.org"}

    assert mi._host_allowed("records.ca.gov", domains)
    assert mi._host_allowed("www.ocgov.com", domains)
    assert mi._host_allowed("octa.net", domains)
    assert mi._host_allowed("city.example.org", domains)
    assert mi._host_allowed("docs.city.example.org", domains)
    assert not mi._host_allowed("example.com", domains)
    assert not mi._host_allowed("", domains)


def test_official_url_is_real_accepts_matching_fetched_agenda_without_http(monkeypatch):
    def unexpected_get(*_args, **_kwargs):
        raise AssertionError("HTTP should not be used for the already-fetched agenda")

    monkeypatch.setattr(mi.requests, "get", unexpected_get)

    meeting = {"agenda_url": "https://city.example.gov/agenda/15"}
    assert mi._official_url_is_real(
        "https://city.example.gov/agenda/15/",
        meeting,
        "Capital Improvement Plan",
        "15. CAPITAL IMPROVEMENT PLAN",
        {"city.example.gov"},
    )


def test_official_url_is_real_validates_allowed_redirect_and_status(monkeypatch):
    monkeypatch.setattr(
        mi.requests,
        "get",
        lambda *_args, **_kwargs: SimpleNamespace(
            url="https://docs.city.example.org/final",
            status_code=200,
        ),
    )

    assert mi._official_url_is_real(
        "https://city.example.org/start",
        {},
        "",
        "",
        {"city.example.org"},
    )


def test_official_url_is_real_rejects_untrusted_hosts_redirects_and_errors(monkeypatch):
    domains = {"city.example.org"}

    assert not mi._official_url_is_real(
        "https://untrusted.example.com/item",
        {},
        "",
        "",
        domains,
    )

    monkeypatch.setattr(
        mi.requests,
        "get",
        lambda *_args, **_kwargs: SimpleNamespace(
            url="https://untrusted.example.com/final",
            status_code=200,
        ),
    )
    assert not mi._official_url_is_real(
        "https://city.example.org/start",
        {},
        "",
        "",
        domains,
    )

    def failing_get(*_args, **_kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(mi.requests, "get", failing_get)
    assert not mi._official_url_is_real(
        "https://city.example.org/start",
        {},
        "",
        "",
        domains,
    )


def test_person_surname_token_strips_roles_and_punctuation():
    assert mi._person_surname_token("Councilmember Jane O'Connor") == "oconnor"
    assert mi._person_surname_token("Mayor Pro Tem John Smith") == "smith"
    assert mi._person_surname_token("Mayor") == ""


def test_person_correction_plausible_is_conservative():
    assert mi._person_correction_plausible("O'Conner", "O'Connor")
    assert mi._person_correction_plausible("Council Member Smith", "John Smith")
    assert not mi._person_correction_plausible("Li", "Lee")
    assert not mi._person_correction_plausible("Anderson", "Rodriguez")


def test_role_labeled_person_key_preserves_given_name_distinctions():
    assert mi._role_labeled_person_key("Council Member John Smith") == "john smith"
    assert mi._role_labeled_person_key("Mayor Pro Tem Jane O'Connor") == "jane oconnor"
    assert mi._role_labeled_person_key("Vice Mayor Smith") == "smith"


def test_role_labeled_person_candidates_extract_and_deduplicate_safe_forms():
    notes = (
        "Council Member John Smith spoke first. "
        "Council Member John Smith spoke again. "
        "Council Member Jane Smith responded. "
        "Mayor Alice Jones closed the discussion. "
        "Council Member Comments and Actions followed."
    )

    assert mi._role_labeled_person_candidates(notes) == [
        "Council Member John Smith",
        "Council Member Jane Smith",
        "Mayor Alice Jones",
    ]


def test_person_soundex_is_stable_for_close_surname_spellings():
    assert mi._person_soundex("Smith") == "S530"
    assert mi._person_soundex("Smyth") == "S530"
    assert mi._person_soundex("") == ""


def test_role_labeled_person_correction_requires_role_and_conservative_phonetics():
    role_candidates = ["Council Member Smyth"]

    assert mi._role_labeled_person_correction_plausible(
        "Smyth",
        "Smith",
        role_candidates,
    )
    assert not mi._role_labeled_person_correction_plausible(
        "Smyth",
        "Smith",
        [],
    )
    assert not mi._role_labeled_person_correction_plausible(
        "Li",
        "Lee",
        ["Council Member Li"],
    )
    assert not mi._role_labeled_person_correction_plausible(
        "Smyth",
        "Jones",
        role_candidates,
    )
