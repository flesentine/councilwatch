from __future__ import annotations

from unittest.mock import patch

import pytest
from bs4 import BeautifulSoup

from discovery.sources import granicus


def _row(html: str):
    return BeautifulSoup(html, "html.parser").find("tr")


def _city(**overrides):
    city = {
        "slug": "rsm",
        "timezone": "America/Los_Angeles",
        "source_url": "https://example.test/ViewPublisher.php?view_id=9",
        "meeting_terms": ["city council"],
        "exclude_terms": ["planning commission"],
    }
    city.update(overrides)
    return city


def test_clip_id_from_href_onclick_fallback_markup_and_missing():
    assert granicus._clip_id_from_row(
        _row("<tr><td><a href='MediaPlayer.php?clip_id=123'>Video</a></td></tr>")
    ) == "123"

    assert granicus._clip_id_from_row(
        _row(
            "<tr><td><a href='javascript:void(0)' "
            "onclick=\"play('MediaPlayer.php?view_id=9&clip_id=456')\">Video</a></td></tr>"
        )
    ) == "456"

    assert granicus._clip_id_from_row(
        _row("<tr data-player='MediaPlayer.php?clip_id=789'><td>Meeting</td></tr>")
    ) == "789"

    assert granicus._clip_id_from_row(_row("<tr><td>Meeting</td></tr>")) == ""


def test_agenda_prefers_explicit_link_and_can_infer_from_view_and_clip():
    base = "https://example.test/ViewPublisher.php?view_id=9"
    explicit = _row(
        "<tr><td><a href='custom-agenda.pdf'>Agenda Packet</a></td></tr>"
    )

    assert granicus._agenda_from_row(base, explicit, "123") == (
        "https://example.test/custom-agenda.pdf"
    )

    no_agenda = _row("<tr><td>Meeting</td></tr>")
    assert granicus._agenda_from_row(base, no_agenda, "123") == (
        "https://example.test/AgendaViewer.php?view_id=9&clip_id=123"
    )
    assert granicus._agenda_from_row("https://example.test/archive", no_agenda, "123") == ""
    assert granicus._agenda_from_row(base, no_agenda, "") == ""


def test_recording_extracts_javascript_direct_href_and_inferred_player():
    base = "https://example.test/ViewPublisher.php?view_id=9"
    javascript = _row(
        "<tr><td><a href='javascript:void(0)' "
        "onclick=\"window.open('MediaPlayer.php?view_id=9&clip_id=123')\">Video</a></td></tr>"
    )
    assert granicus._recording_from_row(base, javascript, "123") == (
        "https://example.test/MediaPlayer.php?view_id=9&clip_id=123"
    )

    direct = _row("<tr><td><a href='MediaPlayer.php'>Video</a></td></tr>")
    assert granicus._recording_from_row(base, direct, "") == (
        "https://example.test/MediaPlayer.php"
    )

    no_player = _row("<tr><td>Meeting</td></tr>")
    assert granicus._recording_from_row(base, no_player, "456") == (
        "https://example.test/MediaPlayer.php?view_id=9&clip_id=456"
    )
    assert granicus._recording_from_row("https://example.test/archive", no_player, "456") == ""
    assert granicus._recording_from_row(base, no_player, "") == ""


def test_discover_filters_nonmatching_excluded_canceled_and_dateless_rows():
    city = _city()
    html = """
    <table>
      <tr><td>Random Board</td><td>Jan 1, 2000</td></tr>
      <tr><td>Planning Commission City Council Joint Meeting</td><td>Jan 2, 2000</td></tr>
      <tr><td>City Council Meeting Canceled</td><td>Jan 3, 2000</td></tr>
      <tr><td>City Council Meeting</td><td>Date TBD</td></tr>
    </table>
    """

    with patch("discovery.sources.granicus.fetch", return_value=(city["source_url"], html)):
        result = granicus.discover(city)

    assert result["latest_completed"] is None
    assert result["next_upcoming"] is None
    assert result["selected_meetings"] == []
    assert result["counts"] == {
        "completed_candidates": 0,
        "upcoming_candidates": 0,
    }


def test_discover_completed_recording_is_ready_and_missing_recording_waits():
    city = _city(exclude_terms=[])
    html = """
    <table>
      <tr>
        <td>City Council Regular Meeting</td><td>Jan 2, 2000</td>
        <td><a href='MediaPlayer.php?view_id=9&clip_id=200'>Video</a></td>
      </tr>
      <tr>
        <td>City Council Special Meeting</td><td>Jan 1, 2000</td>
      </tr>
    </table>
    """

    with patch("discovery.sources.granicus.fetch", return_value=(city["source_url"], html)):
        result = granicus.discover(city)

    assert result["counts"] == {
        "completed_candidates": 2,
        "upcoming_candidates": 0,
    }
    assert result["latest_completed"].external_id == "200"
    assert result["latest_completed"].status == "ready"
    assert result["latest_completed"].recording_status == "found"

    older = next(
        meeting
        for meeting in [result["latest_completed"], *result["selected_meetings"]]
        if meeting.meeting_date == "2000-01-02"
    )
    assert older.status == "ready"

    # The older no-recording row is not selected because only the latest
    # completed meeting for the configured type is returned, so inspect a
    # one-row discovery to prove its waiting state directly.
    missing_html = """
    <table><tr><td>City Council Special Meeting</td><td>Jan 1, 2000</td></tr></table>
    """
    with patch(
        "discovery.sources.granicus.fetch",
        return_value=(city["source_url"], missing_html),
    ):
        missing_result = granicus.discover(city)

    missing = missing_result["latest_completed"]
    assert missing.status == "waiting_recording"
    assert missing.recording_status == "missing"
    assert missing.recording_url == ""


def test_discover_future_meeting_is_upcoming_and_uses_stable_id_when_clip_missing():
    city = _city(exclude_terms=[])
    html = """
    <table><tr><td>City Council Regular Meeting</td><td>Jan 15, 2099</td></tr></table>
    """

    with (
        patch("discovery.sources.granicus.fetch", return_value=(city["source_url"], html)),
        patch("discovery.sources.granicus.stable_id", return_value="stable-meeting-id") as stable,
    ):
        result = granicus.discover(city)

    meeting = result["next_upcoming"]
    assert meeting.kind == "upcoming"
    assert meeting.status == "upcoming"
    assert meeting.external_id == "stable-meeting-id"
    assert meeting.recording_status == "missing"
    stable.assert_called_once_with(
        "rsm", "2099-01-15", "City Council Regular Meeting"
    )


def test_discover_derives_agenda_from_recording_when_archive_url_has_no_view_id():
    city = _city(
        source_url="https://example.test/archive",
        exclude_terms=[],
    )
    final_url = "https://cdn.example.test/archive"
    html = """
    <table>
      <tr>
        <td>City Council Regular Meeting</td><td>Jan 2, 2000</td>
        <td><a href='MediaPlayer.php?view_id=44&clip_id=500'>Video</a></td>
      </tr>
    </table>
    """

    with patch("discovery.sources.granicus.fetch", return_value=(final_url, html)):
        result = granicus.discover(city)

    meeting = result["latest_completed"]
    assert meeting.source_url == final_url
    assert meeting.recording_url == (
        "https://cdn.example.test/MediaPlayer.php?view_id=44&clip_id=500"
    )
    assert meeting.agenda_url == (
        "https://cdn.example.test/AgendaViewer.php?view_id=44&clip_id=500"
    )


def test_discover_repairs_title_from_later_row_text_when_first_cell_is_not_meeting_name():
    city = _city(exclude_terms=[])
    html = """
    <table>
      <tr>
        <td>Jan 2, 2000</td>
        <td>| City Council Regular Meeting</td>
        <td><a href='MediaPlayer.php?view_id=9&clip_id=600'>Video</a></td>
      </tr>
    </table>
    """

    with patch("discovery.sources.granicus.fetch", return_value=(city["source_url"], html)):
        result = granicus.discover(city)

    assert result["latest_completed"].title == "City Council Regular Meeting Video"


def test_discover_sorts_candidates_and_selects_latest_completed_and_next_upcoming_per_type():
    city = _city(
        meeting_terms=["city council", "planning commission"],
        exclude_terms=[],
    )
    html = """
    <table>
      <tr><td>City Council Old</td><td>Jan 1, 2000</td><td><a href='MediaPlayer.php?view_id=9&clip_id=101'>Video</a></td></tr>
      <tr><td>City Council New</td><td>Jan 3, 2000</td><td><a href='MediaPlayer.php?view_id=9&clip_id=103'>Video</a></td></tr>
      <tr><td>Planning Commission Old</td><td>Jan 2, 2000</td><td><a href='MediaPlayer.php?view_id=9&clip_id=202'>Video</a></td></tr>
      <tr><td>Planning Commission New</td><td>Jan 4, 2000</td><td><a href='MediaPlayer.php?view_id=9&clip_id=204'>Video</a></td></tr>
      <tr><td>City Council Later Future</td><td>Feb 2, 2099</td></tr>
      <tr><td>City Council Next Future</td><td>Feb 1, 2099</td></tr>
      <tr><td>Planning Commission Later Future</td><td>Mar 2, 2099</td></tr>
      <tr><td>Planning Commission Next Future</td><td>Mar 1, 2099</td></tr>
    </table>
    """

    with patch("discovery.sources.granicus.fetch", return_value=(city["source_url"], html)):
        result = granicus.discover(city)

    assert result["latest_completed"].external_id == "204"
    assert result["next_upcoming"].meeting_date == "2099-02-01"
    assert result["counts"] == {
        "completed_candidates": 4,
        "upcoming_candidates": 4,
    }
    assert [meeting.title for meeting in result["selected_meetings"]] == [
        "City Council New",
        "City Council Next Future",
        "Planning Commission New",
        "Planning Commission Next Future",
    ]


def test_discover_deduplicates_joint_meeting_selected_for_multiple_terms():
    city = _city(
        meeting_terms=["city council", "planning commission"],
        exclude_terms=[],
    )
    html = """
    <table>
      <tr>
        <td>City Council Planning Commission Joint Meeting</td><td>Jan 2, 2000</td>
        <td><a href='MediaPlayer.php?view_id=9&clip_id=700'>Video</a></td>
      </tr>
    </table>
    """

    with patch("discovery.sources.granicus.fetch", return_value=(city["source_url"], html)):
        result = granicus.discover(city)

    assert [meeting.external_id for meeting in result["selected_meetings"]] == ["700"]


def test_discover_uses_default_meeting_term_timezone_and_propagates_fetch_failures():
    city = {
        "slug": "rsm",
        "source_url": "https://example.test/ViewPublisher.php?view_id=9",
    }
    html = """
    <table><tr><td>City Council Meeting</td><td>Jan 2, 2000</td></tr></table>
    """

    with patch("discovery.sources.granicus.fetch", return_value=(city["source_url"], html)):
        result = granicus.discover(city)

    assert result["latest_completed"].title == "City Council Meeting"

    with patch(
        "discovery.sources.granicus.fetch",
        side_effect=RuntimeError("archive unavailable"),
    ):
        with pytest.raises(RuntimeError, match="archive unavailable"):
            granicus.discover(city)
