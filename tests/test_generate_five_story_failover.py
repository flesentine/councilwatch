import pytest

import generate_five


MEETING = {
    "city_slug": "laguna-niguel",
    "city_name": "Laguna Niguel",
    "external_id": "meeting-1",
    "meeting_date": "2026-08-18",
}


def _set_models(monkeypatch, primary, fallbacks):
    monkeypatch.setattr(generate_five, "STORY_MODEL", primary)
    monkeypatch.setattr(
        generate_five,
        "STORY_FALLBACK_MODELS",
        list(fallbacks),
    )
    monkeypatch.setattr(
        generate_five.meeting_intelligence,
        "STORY_MODEL",
        primary,
    )
    monkeypatch.setattr(
        generate_five.gemini_worker,
        "STORY_MODEL",
        primary,
    )
    monkeypatch.setattr(
        generate_five.process_city_module,
        "STORY_MODEL",
        primary,
    )


def test_story_model_candidates_deduplicates_primary(monkeypatch):
    _set_models(
        monkeypatch,
        "primary",
        ["primary", "fallback-a", "fallback-a", "fallback-b"],
    )

    assert generate_five.story_model_candidates() == [
        "primary",
        "fallback-a",
        "fallback-b",
    ]


def test_story_failover_reuses_saved_evidence(monkeypatch):
    _set_models(
        monkeypatch,
        "primary",
        ["fallback-a", "fallback-b"],
    )

    calls = []

    def fake_process_city(
        slug,
        *,
        force_story,
        force_notes,
        meeting_override,
    ):
        calls.append(
            (
                generate_five.meeting_intelligence.STORY_MODEL,
                generate_five.gemini_worker.STORY_MODEL,
                generate_five.process_city_module.STORY_MODEL,
                force_story,
                force_notes,
                slug,
                meeting_override,
            )
        )

        if len(calls) == 1:
            raise RuntimeError(
                "503 UNAVAILABLE: model high demand"
            )

        return "ok"

    monkeypatch.setattr(
        generate_five,
        "process_city",
        fake_process_city,
    )

    result = generate_five.process_city_with_story_failover(
        "laguna-niguel",
        MEETING,
        force_story=True,
        force_notes=True,
    )

    assert result == "ok"
    assert calls[0][:5] == (
        "primary",
        "primary",
        "primary",
        True,
        True,
    )
    assert calls[1][:5] == (
        "fallback-a",
        "fallback-a",
        "fallback-a",
        True,
        False,
    )

    assert generate_five.meeting_intelligence.STORY_MODEL == "primary"
    assert generate_five.gemini_worker.STORY_MODEL == "primary"
    assert generate_five.process_city_module.STORY_MODEL == "primary"


def test_story_failover_does_not_mask_nonretryable_error(monkeypatch):
    _set_models(
        monkeypatch,
        "primary",
        ["fallback-a"],
    )

    calls = []

    def fake_process_city(*args, **kwargs):
        calls.append(
            generate_five.meeting_intelligence.STORY_MODEL
        )
        raise ValueError("invalid structured response")

    monkeypatch.setattr(
        generate_five,
        "process_city",
        fake_process_city,
    )

    with pytest.raises(
        ValueError,
        match="invalid structured response",
    ):
        generate_five.process_city_with_story_failover(
            "laguna-niguel",
            MEETING,
            force_story=False,
            force_notes=False,
        )

    assert calls == ["primary"]


def test_story_failover_tries_all_configured_models(monkeypatch):
    _set_models(
        monkeypatch,
        "primary",
        ["fallback-a", "fallback-b"],
    )

    calls = []

    def fake_process_city(*args, **kwargs):
        calls.append(
            (
                generate_five.meeting_intelligence.STORY_MODEL,
                kwargs["force_notes"],
            )
        )
        raise RuntimeError("ServerError: 503 UNAVAILABLE")

    monkeypatch.setattr(
        generate_five,
        "process_city",
        fake_process_city,
    )

    with pytest.raises(
        RuntimeError,
        match="503 UNAVAILABLE",
    ):
        generate_five.process_city_with_story_failover(
            "laguna-niguel",
            MEETING,
            force_story=False,
            force_notes=True,
        )

    assert calls == [
        ("primary", True),
        ("fallback-a", False),
        ("fallback-b", False),
    ]
