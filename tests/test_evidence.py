from pathlib import Path

import pytest

from evidence import (
    agenda_snapshot_path,
    assemble_audit_notes,
    load_agenda_evidence,
    read_reviewer_evidence,
    reviewer_evidence_path,
    write_reviewer_evidence,
)


def test_agenda_snapshot_prefers_cache_and_refreshes_explicitly(tmp_path):
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    path = agenda_snapshot_path(
        drafts,
        "alpha",
        "100",
    )
    path.write_text(
        "CACHED AGENDA\n",
        encoding="utf-8",
    )

    calls = []

    def fetch(url):
        calls.append(url)
        return "LIVE AGENDA"

    text, source = load_agenda_evidence(
        drafts,
        "alpha",
        "100",
        "https://example.com/agenda",
        fetch,
    )
    assert (text, source) == (
        "CACHED AGENDA",
        "cache",
    )
    assert calls == []

    text, source = load_agenda_evidence(
        drafts,
        "alpha",
        "100",
        "https://example.com/agenda",
        fetch,
        refresh=True,
    )
    assert (text, source) == (
        "LIVE AGENDA",
        "live",
    )
    assert calls == [
        "https://example.com/agenda"
    ]
    assert path.read_text(
        encoding="utf-8"
    ) == "LIVE AGENDA\n"


def test_agenda_snapshot_falls_back_to_cache_on_live_failure(tmp_path):
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    path = agenda_snapshot_path(
        drafts,
        "alpha",
        "100",
    )
    path.write_text(
        "LAST GOOD AGENDA\n",
        encoding="utf-8",
    )

    def fail(_url):
        raise RuntimeError("city site down")

    text, source = load_agenda_evidence(
        drafts,
        "alpha",
        "100",
        "https://example.com/agenda",
        fail,
        refresh=True,
    )
    assert (text, source) == (
        "LAST GOOD AGENDA",
        "cache",
    )


def test_agenda_snapshot_propagates_failure_without_cache(tmp_path):
    drafts = tmp_path / "drafts"
    drafts.mkdir()

    def fail(_url):
        raise RuntimeError("city site down")

    with pytest.raises(
        RuntimeError,
        match="city site down",
    ):
        load_agenda_evidence(
            drafts,
            "alpha",
            "100",
            "https://example.com/agenda",
            fail,
        )

    text, source = load_agenda_evidence(
        drafts,
        "alpha",
        "100",
        "",
        fail,
    )
    assert (text, source) == (
        "",
        "none",
    )


def test_reviewer_evidence_round_trip_and_delete(tmp_path):
    drafts = tmp_path / "drafts"
    drafts.mkdir()

    path = write_reviewer_evidence(
        drafts,
        "alpha",
        "100",
        " Official recording 1:20 confirms the request. ",
    )
    assert path == reviewer_evidence_path(
        drafts,
        "alpha",
        "100",
    )
    assert read_reviewer_evidence(
        drafts,
        "alpha",
        "100",
    ) == (
        "Official recording 1:20 confirms the request."
    )

    write_reviewer_evidence(
        drafts,
        "alpha",
        "100",
        "   ",
    )
    assert not path.exists()
    assert read_reviewer_evidence(
        drafts,
        "alpha",
        "100",
    ) == ""


def test_assemble_audit_notes_keeps_source_boundary_explicit():
    bundled = assemble_audit_notes(
        "RAW SOURCE NOTES",
        verification_context=(
            "VERIFIED IDENTITY CONTEXT"
        ),
        reviewer_evidence=(
            "Official agenda says 54,184 square feet."
        ),
    )

    assert bundled.startswith(
        "RAW SOURCE NOTES"
    )
    assert (
        "VERIFIED IDENTITY CONTEXT"
        in bundled
    )
    assert (
        "REVIEWER-ADDED SOURCE EVIDENCE:"
        in bundled
    )
    assert (
        "Official agenda says 54,184 square feet."
        in bundled
    )
    assert "coverage plan" not in bundled.lower()
    assert "editorial summary" not in bundled.lower()
