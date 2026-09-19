import types

import agenda


class FakeResponse:
    def __init__(
        self,
        *,
        text="",
        content=b"",
        headers=None,
        url="https://example.test/agenda",
        error=None,
    ):
        self.text = text
        self.content = content
        self.headers = headers or {}
        self.url = url
        self.error = error

    def raise_for_status(self):
        if self.error is not None:
            raise self.error


def test_empty_url_returns_empty_string():
    assert agenda.agenda_text("") == ""


def test_generic_fetch_failure_is_reported(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(agenda.requests, "get", fail)

    assert agenda.agenda_text("https://example.test/agenda") == (
        "[Agenda fetch failed: RuntimeError: network down]"
    )


def test_failed_granicus_agenda_falls_back_to_media_player(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))

        if "/AgendaViewer.php" in url:
            raise RuntimeError("agenda unavailable")

        if "GeneratedAgendaViewer.php" in url:
            raise RuntimeError("generated agenda unavailable")

        assert url == (
            "https://city.test.granicus.com/MediaPlayer.php"
            "?view_id=2&clip_id=981"
        )

        return FakeResponse(
            text=(
                "<html><body><h1>City Council</h1>"
                "<script>ignore me</script>"
                "<p>Item 6.1 School financing</p></body></html>"
            ),
            url=url,
        )

    monkeypatch.setattr(agenda.requests, "get", fake_get)

    text = agenda.agenda_text(
        "https://city.test.granicus.com/AgendaViewer.php"
        "?view_id=2&clip_id=981"
    )

    assert "City Council" in text
    assert "Item 6.1 School financing" in text
    assert "ignore me" not in text
    assert len(calls) == 3
    assert calls[0][1]["allow_redirects"] is True
    assert calls[1][1]["allow_redirects"] is True
    assert calls[2][1]["allow_redirects"] is True
    assert "MediaPlayer.php" in calls[1][0]
    assert "GeneratedAgendaViewer.php" in calls[2][0]


def test_failed_granicus_player_preserves_original_fetch_error(monkeypatch):
    def fake_get(url, **kwargs):
        if "/AgendaViewer.php" in url:
            raise ValueError("agenda failure")
        raise RuntimeError("player failure")

    monkeypatch.setattr(agenda.requests, "get", fake_get)

    assert agenda.agenda_text(
        "https://city.test.granicus.com/AgendaViewer.php"
        "?view_id=2&clip_id=981"
    ) == "[Agenda fetch failed: ValueError: agenda failure]"


def test_failed_granicus_without_clip_id_does_not_try_player(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        raise RuntimeError("agenda failure")

    monkeypatch.setattr(agenda.requests, "get", fake_get)

    result = agenda.agenda_text(
        "https://city.test.granicus.com/AgendaViewer.php?view_id=2"
    )

    assert result == "[Agenda fetch failed: RuntimeError: agenda failure]"
    assert len(calls) == 1


def test_pdf_response_extracts_pages_normalizes_and_limits(monkeypatch):
    response = FakeResponse(
        content=b"fake-pdf",
        headers={"content-type": "application/pdf"},
        url="https://example.test/agenda?id=5",
    )
    monkeypatch.setattr(agenda.requests, "get", lambda *a, **k: response)

    pages = [
        types.SimpleNamespace(extract_text=lambda: "Page one\n\n\n\nItem A"),
        types.SimpleNamespace(extract_text=lambda: None),
        types.SimpleNamespace(extract_text=lambda: "Page three"),
    ]
    monkeypatch.setattr(
        agenda,
        "PdfReader",
        lambda stream: types.SimpleNamespace(pages=pages),
    )

    text = agenda.agenda_text("https://example.test/agenda", limit=24)

    assert text == "Page one\n\nItem A\n\nPage t"


def test_pdf_extension_is_enough_to_select_pdf_parser(monkeypatch):
    response = FakeResponse(
        content=b"fake-pdf",
        headers={"content-type": "application/octet-stream"},
        url="https://example.test/agenda.PDF?download=1",
    )
    monkeypatch.setattr(agenda.requests, "get", lambda *a, **k: response)
    monkeypatch.setattr(
        agenda,
        "PdfReader",
        lambda stream: types.SimpleNamespace(
            pages=[types.SimpleNamespace(extract_text=lambda: "PDF TEXT")]
        ),
    )

    assert agenda.agenda_text("https://example.test/source") == "PDF TEXT"


def test_pdf_parse_failure_is_reported(monkeypatch):
    response = FakeResponse(
        content=b"bad",
        headers={"content-type": "application/pdf"},
        url="https://example.test/agenda.pdf",
    )
    monkeypatch.setattr(agenda.requests, "get", lambda *a, **k: response)

    def fail_reader(stream):
        raise RuntimeError("bad pdf")

    monkeypatch.setattr(agenda, "PdfReader", fail_reader)

    assert agenda.agenda_text("https://example.test/agenda.pdf") == (
        "[Agenda PDF parse failed: RuntimeError: bad pdf]"
    )


def test_html_response_removes_non_content_tags_and_limits(monkeypatch):
    html = """
    <html>
      <head><style>.x {display:none}</style></head>
      <body>
        <h1>Regular Meeting</h1>
        <script>secret script</script>
        <noscript>fallback noise</noscript>
        <svg><text>icon noise</text></svg>
        <p>Consent Calendar</p>
      </body>
    </html>
    """
    response = FakeResponse(
        text=html,
        headers={"content-type": "text/html"},
        url="https://example.test/agenda",
    )
    monkeypatch.setattr(agenda.requests, "get", lambda *a, **k: response)

    text = agenda.agenda_text("https://example.test/agenda", limit=27)

    assert text == "Regular Meeting\nConsent Cal"
    assert "secret" not in text
    assert "noise" not in text


def test_granicus_onbase_shell_uses_richer_media_player_text(monkeypatch):
    shell = (
        "<html><body><h1>OnBase Agenda Online</h1>"
        "<p>Agenda Packet</p>"
        + "<p>Navigation shell filler text.</p>" * 40
        + "</body></html>"
    )
    player = (
        "<html><body><h1>City Council Regular Meeting</h1>"
        + "<p>Indexed agenda item with substantial detail.</p>" * 40
        + "</body></html>"
    )
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if "/AgendaViewer.php" in url:
            return FakeResponse(
                text=shell,
                headers={"content-type": "text/html"},
                url=url,
            )
        assert "MediaPlayer.php" in url
        return FakeResponse(text=player, url=url)

    monkeypatch.setattr(agenda.requests, "get", fake_get)

    text = agenda.agenda_text(
        "https://city.test.granicus.com/AgendaViewer.php"
        "?view_id=2&clip_id=981"
    )

    assert "City Council Regular Meeting" in text
    assert "Indexed agenda item" in text
    assert "OnBase Agenda Online" not in text
    assert len(calls) == 3
    assert calls[1][1]["allow_redirects"] is True
    assert "MediaPlayer.php" in calls[1][0]
    assert "GeneratedAgendaViewer.php" in calls[2][0]


def test_short_granicus_shell_fallback_keeps_original_when_player_is_shorter(
    monkeypatch,
):
    shell = "<html><body><p>Original shell agenda text here.</p></body></html>"
    player = "<html><body><p>short</p></body></html>"

    def fake_get(url, **kwargs):
        if "/AgendaViewer.php" in url:
            return FakeResponse(
                text=shell,
                headers={"content-type": "text/html"},
                url=url,
            )
        return FakeResponse(text=player, url=url)

    monkeypatch.setattr(agenda.requests, "get", fake_get)

    text = agenda.agenda_text(
        "https://city.test.granicus.com/AgendaViewer.php"
        "?view_id=2&clip_id=981"
    )

    assert text == "Original shell agenda text here."


def test_granicus_shell_player_failure_keeps_original_text(monkeypatch):
    shell = "<html><body><p>Original shell agenda text here.</p></body></html>"

    def fake_get(url, **kwargs):
        if "/AgendaViewer.php" in url:
            return FakeResponse(
                text=shell,
                headers={"content-type": "text/html"},
                url=url,
            )
        raise RuntimeError("player unavailable")

    monkeypatch.setattr(agenda.requests, "get", fake_get)

    assert agenda.agenda_text(
        "https://city.test.granicus.com/AgendaViewer.php"
        "?view_id=2&clip_id=981"
    ) == "Original shell agenda text here."


def test_non_granicus_short_page_does_not_trigger_player_fallback(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return FakeResponse(
            text="<html><body><p>Short but valid</p></body></html>",
            headers={"content-type": "text/html"},
            url=url,
        )

    monkeypatch.setattr(agenda.requests, "get", fake_get)

    assert agenda.agenda_text("https://example.test/agenda") == "Short but valid"
    assert calls == ["https://example.test/agenda"]


def test_html_parse_failure_is_reported(monkeypatch):
    response = FakeResponse(
        text="<html></html>",
        headers={"content-type": "text/html"},
        url="https://example.test/agenda",
    )
    monkeypatch.setattr(agenda.requests, "get", lambda *a, **k: response)

    def fail_soup(*args, **kwargs):
        raise RuntimeError("parser exploded")

    monkeypatch.setattr(agenda, "BeautifulSoup", fail_soup)

    assert agenda.agenda_text("https://example.test/agenda") == (
        "[Agenda HTML parse failed: RuntimeError: parser exploded]"
    )



def test_granicus_nonobvious_shell_over_old_threshold_uses_richer_player(
    monkeypatch,
):
    shell = (
        "<html><body>"
        + "<p>Agenda navigation and meeting shell text.</p>" * 35
        + "</body></html>"
    )
    player = (
        "<html><body><h1>City Council Regular Meeting</h1>"
        "<p>4.5 RESOLUTION REAPPROPRIATING FUND BALANCES "
        "AND AMENDING THE BUDGET</p>"
        "<p>4.7 ADOPTION OF THE CAPER FOR CDBG FUNDS</p>"
        "<p>4.8 PROPOSED LEASE RENEWAL</p>"
        + "<p>Indexed agenda detail.</p>" * 60
        + "</body></html>"
    )

    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)

        if "/AgendaViewer.php" in url:
            return FakeResponse(
                text=shell,
                headers={"content-type": "text/html"},
                url=url,
            )

        assert "MediaPlayer.php" in url
        return FakeResponse(
            text=player,
            headers={"content-type": "text/html"},
            url=url,
        )

    monkeypatch.setattr(
        agenda.requests,
        "get",
        fake_get,
    )

    text = agenda.agenda_text(
        "https://city.test.granicus.com/AgendaViewer.php"
        "?view_id=3&clip_id=788"
    )

    assert len(
        "Agenda navigation and meeting shell text." * 35
    ) > 1000
    assert "4.5 RESOLUTION REAPPROPRIATING" in text
    assert "4.7 ADOPTION OF THE CAPER" in text
    assert "4.8 PROPOSED LEASE RENEWAL" in text
    assert len(calls) == 3



def test_granicus_generated_agenda_wins_when_richer_than_other_views(
    monkeypatch,
):
    shell = (
        "<html><body>"
        "<p>Agenda navigation shell.</p>"
        "</body></html>"
    )

    player = (
        "<html><body>"
        "<p>Meeting player with limited index.</p>"
        "<p>4.8 PROPOSED LEASE RENEWAL</p>"
        "</body></html>"
    )

    generated = (
        "<html><body>"
        "<h1>AGENDA</h1>"
        "<p>STUDY SESSION FISCAL YEAR 2025-26 YEAR-END "
        "RESULTS AND FIVE-YEAR FINANCIAL FORECAST</p>"
        "<p>4.5 RESOLUTION REAPPROPRIATING CERTAIN "
        "FISCAL YEAR 2025-26 FUND BALANCES AND AMENDING "
        "THE FISCAL YEAR 2026-27 BUDGET</p>"
        "<p>4.7 ADOPTION OF THE 2025-2026 CONSOLIDATED "
        "ANNUAL PERFORMANCE AND EVALUATION REPORT (CAPER) "
        "FOR EXPENDITURES OF COMMUNITY DEVELOPMENT BLOCK "
        "GRANT (CDBG) FUNDS</p>"
        "<p>4.8 PROPOSED LEASE RENEWAL WITH FAMILY "
        "ASSISTANCE MINISTRIES</p>"
        "</body></html>"
    )

    calls = []

    def fake_get(url, **kwargs):
        calls.append(
            url
        )

        if "/AgendaViewer.php" in url:
            return FakeResponse(
                text=shell,
                headers={
                    "content-type":
                        "text/html"
                },
                url=url,
            )

        if "MediaPlayer.php" in url:
            return FakeResponse(
                text=player,
                headers={
                    "content-type":
                        "text/html"
                },
                url=url,
            )

        if "GeneratedAgendaViewer.php" in url:
            return FakeResponse(
                text=generated,
                headers={
                    "content-type":
                        "text/html"
                },
                url=url,
            )

        raise AssertionError(
            url
        )

    monkeypatch.setattr(
        agenda.requests,
        "get",
        fake_get,
    )

    text = agenda.agenda_text(
        "https://city.test.granicus.com/AgendaViewer.php"
        "?view_id=3&clip_id=788"
    )

    assert "FIVE-YEAR FINANCIAL FORECAST" in text
    assert "4.5 RESOLUTION REAPPROPRIATING" in text
    assert "4.7 ADOPTION OF THE 2025-2026" in text
    assert "COMMUNITY DEVELOPMENT BLOCK" in text
    assert "4.8 PROPOSED LEASE RENEWAL" in text
    assert len(
        calls
    ) == 3



def test_failed_granicus_primary_chooses_richest_alternate(
    monkeypatch,
):
    player = (
        "<html><body>"
        "<p>4.8 PROPOSED LEASE RENEWAL</p>"
        "</body></html>"
    )

    generated = (
        "<html><body>"
        "<p>4.5 RESOLUTION REAPPROPRIATING FUND BALANCES "
        "AND AMENDING THE BUDGET</p>"
        "<p>4.7 ADOPTION OF THE CAPER FOR CDBG FUNDS</p>"
        "<p>4.8 PROPOSED LEASE RENEWAL</p>"
        "</body></html>"
    )

    calls = []

    def fake_get(url, **kwargs):
        calls.append(
            url
        )

        if "/AgendaViewer.php" in url:
            raise RuntimeError(
                "primary unavailable"
            )

        if "MediaPlayer.php" in url:
            return FakeResponse(
                text=player,
                headers={
                    "content-type":
                        "text/html"
                },
                url=url,
            )

        if "GeneratedAgendaViewer.php" in url:
            return FakeResponse(
                text=generated,
                headers={
                    "content-type":
                        "text/html"
                },
                url=url,
            )

        raise AssertionError(
            url
        )

    monkeypatch.setattr(
        agenda.requests,
        "get",
        fake_get,
    )

    text = agenda.agenda_text(
        "https://city.test.granicus.com/AgendaViewer.php"
        "?view_id=3&clip_id=788"
    )

    assert "4.5 RESOLUTION REAPPROPRIATING" in text
    assert "4.7 ADOPTION OF THE CAPER" in text
    assert "4.8 PROPOSED LEASE RENEWAL" in text
    assert len(calls) == 3
