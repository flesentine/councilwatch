import official_entities as oe


class FakeResponse:
    def __init__(self, text="", error=None):
        self.text = text
        self.error = error

    def raise_for_status(self):
        if self.error is not None:
            raise self.error


def setup_function():
    oe._CACHE.clear()


def test_normalize_collapses_whitespace_casefolds_and_handles_none():
    assert oe._normalize("  Keri\n  Lynn\tBAERT  ") == "keri lynn baert"
    assert oe._normalize(None) == ""


def test_fetch_reads_official_html_strips_non_content_and_caches(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(
            """
            <html>
              <head><style>.hidden { display:none }</style></head>
              <body>
                <h1>Mayor &amp; City Council</h1>
                <script>bad script</script>
                <noscript>noscript noise</noscript>
                <svg><text>icon noise</text></svg>
                <p>Keri Lynn Baert</p>
              </body>
            </html>
            """
        )

    monkeypatch.setattr(oe.requests, "get", fake_get)

    url = "https://city.example.test/council"
    first = oe._fetch(url)
    second = oe._fetch(url)

    assert first == second
    assert "Mayor & City Council" in first
    assert "Keri Lynn Baert" in first
    assert "bad script" not in first
    assert "noscript noise" not in first
    assert "icon noise" not in first
    assert len(calls) == 1
    assert calls[0][0] == url
    assert calls[0][1]["headers"] == oe.HEADERS
    assert calls[0][1]["timeout"] == 20


def test_fetch_failure_warns_returns_empty_and_caches_failure(monkeypatch, capsys):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        raise RuntimeError("site unavailable")

    monkeypatch.setattr(oe.requests, "get", fake_get)

    url = "https://city.example.test/broken"
    assert oe._fetch(url) == ""
    assert oe._fetch(url) == ""

    output = capsys.readouterr().out
    assert "WARNING: official entity source failed:" in output
    assert url in output
    assert "RuntimeError" in output
    assert "site unavailable" in output
    assert calls == [url]


def test_fetch_http_status_failure_is_handled(monkeypatch):
    monkeypatch.setattr(
        oe.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(
            "ignored",
            error=ValueError("403 forbidden"),
        ),
    )

    assert oe._fetch("https://city.example.test/forbidden") == ""


def test_official_entity_material_uses_city_sources_and_skips_empty_pages(
    monkeypatch,
):
    monkeypatch.setattr(
        oe,
        "OFFICIAL_ENTITY_SOURCES",
        {
            "rsm": [
                "https://city.test/council",
                "https://city.test/directory",
                "https://city.test/empty",
            ]
        },
    )

    texts = {
        "https://city.test/council": "Mayor Carol Gamble\nCouncilmember Keri Lynn Baert",
        "https://city.test/directory": "City Manager Jane Example",
        "https://city.test/empty": "",
    }
    seen = []

    def fake_fetch(url):
        seen.append(url)
        return texts[url]

    monkeypatch.setattr(oe, "_fetch", fake_fetch)

    result = oe.official_entity_material({"city_slug": "rsm"})

    assert seen == [
        "https://city.test/council",
        "https://city.test/directory",
        "https://city.test/empty",
    ]
    assert result["pages"] == [
        {
            "url": "https://city.test/council",
            "text": texts["https://city.test/council"],
        },
        {
            "url": "https://city.test/directory",
            "text": texts["https://city.test/directory"],
        },
    ]
    assert result["text"] == (
        "OFFICIAL CITY SOURCE:\n"
        "https://city.test/council\n"
        "Mayor Carol Gamble\nCouncilmember Keri Lynn Baert\n\n"
        "OFFICIAL CITY SOURCE:\n"
        "https://city.test/directory\n"
        "City Manager Jane Example"
    )


def test_official_entity_material_unknown_or_missing_city_is_empty(monkeypatch):
    monkeypatch.setattr(oe, "OFFICIAL_ENTITY_SOURCES", {"rsm": ["unused"]})

    assert oe.official_entity_material({"city_slug": "unknown"}) == {
        "pages": [],
        "text": "",
    }
    assert oe.official_entity_material({}) == {
        "pages": [],
        "text": "",
    }


def test_find_official_support_prefers_agenda_source_with_normalized_match():
    result = oe.find_official_support(
        "Keri   Lynn Baert",
        "Roll Call:\nKERI LYNN\tBAERT present",
        "https://city.test/agenda",
        [
            {
                "url": "https://city.test/council",
                "text": "Keri Lynn Baert",
            }
        ],
    )

    assert result == "https://city.test/agenda"


def test_find_official_support_returns_empty_when_agenda_matches_but_has_no_url():
    result = oe.find_official_support(
        "Keri Lynn Baert",
        "Councilmember Keri Lynn Baert",
        "",
        [
            {
                "url": "https://city.test/council",
                "text": "Keri Lynn Baert",
            }
        ],
    )

    # Agenda support is checked first; without a source URL it cannot be cited.
    assert result == ""


def test_find_official_support_falls_back_to_official_page():
    pages = [
        {
            "url": "https://city.test/council",
            "text": "Mayor Carol Gamble",
        },
        {
            "url": "https://city.test/directory",
            "text": "Councilmember Keri\nLynn Baert",
        },
    ]

    assert oe.find_official_support(
        "KERI LYNN BAERT",
        "No matching agenda name",
        "https://city.test/agenda",
        pages,
    ) == "https://city.test/directory"


def test_find_official_support_blank_or_missing_entity_returns_empty():
    pages = [
        {
            "url": "https://city.test/council",
            "text": "Keri Lynn Baert",
        }
    ]

    assert oe.find_official_support(
        "   ",
        "Keri Lynn Baert",
        "https://city.test/agenda",
        pages,
    ) == ""

    assert oe.find_official_support(
        "Someone Else",
        "Keri Lynn Baert",
        "https://city.test/agenda",
        pages,
    ) == ""
