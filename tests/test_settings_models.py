import settings


def test_default_transcript_fallback_models_are_current():
    assert settings.DEFAULT_TRANSCRIPT_FALLBACK_MODELS == (
        "gemini-3.5-flash",
        "gemini-3.6-flash",
    )

    assert "gemini-2.5-flash" not in (
        settings.DEFAULT_TRANSCRIPT_FALLBACK_MODELS
    )


def test_active_default_fallback_chain_matches_declared_defaults():
    assert settings.TRANSCRIPT_FALLBACK_MODELS == list(
        settings.DEFAULT_TRANSCRIPT_FALLBACK_MODELS
    )
