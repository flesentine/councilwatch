import json

from fastapi.testclient import TestClient

import public_site


client = TestClient(public_site.app)


def _write_article(directory, name, **overrides):
    article = {
        "article_id": name,
        "headline": f"Headline {name}",
        "dek": f"Dek {name}",
        "city_name": "Rancho Santa Margarita",
        "city_slug": "rsm",
        "meeting_date": "2026-09-10",
        "published_at_utc": "2026-09-10T20:00:00Z",
        "body": ["Paragraph one.", "Paragraph two."],
        "key_facts": ["Fact one", "Fact two"],
        "source_url": "https://example.com/meeting",
        "agenda_url": "https://example.com/agenda.pdf",
        "recording_url": "https://example.com/video",
        "technology_assisted": False,
    }
    article.update(overrides)
    path = directory / f"{name}.json"
    path.write_text(json.dumps(article), encoding="utf-8")
    return article


def test_small_helpers_escape_dates_and_urls():
    assert public_site.esc("<b>&") == "&lt;b&gt;&amp;"
    assert public_site.esc(None) == ""

    assert public_site.format_date(None) == ""
    assert public_site.format_date("") == ""
    assert public_site.format_date("2026-09-14") == "September 14, 2026"
    assert public_site.format_date("2026-09-14T18:00:00Z") == "September 14, 2026"
    assert public_site.format_date("not-a-date") == "not-a-date"

    assert public_site.safe_url(" https://example.com/a ") == "https://example.com/a"
    assert public_site.safe_url("http://example.com") == "http://example.com"
    assert public_site.safe_url("javascript:alert(1)") == ""
    assert public_site.safe_url("mailto:test@example.com") == ""
    assert public_site.safe_url("/relative/path") == ""
    assert public_site.safe_url(None) == ""


def test_load_articles_missing_directory_returns_empty(tmp_path, monkeypatch):
    missing = tmp_path / "missing"
    monkeypatch.setattr(public_site, "PUBLISHED", missing)

    assert public_site.load_articles() == []


def test_load_articles_skips_bad_rows_and_sorts_newest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(public_site, "PUBLISHED", tmp_path)

    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    (tmp_path / "list.json").write_text("[]", encoding="utf-8")
    (tmp_path / "no-id.json").write_text(
        json.dumps({"headline": "Missing id"}),
        encoding="utf-8",
    )
    (tmp_path / "no-headline.json").write_text(
        json.dumps({"article_id": "missing-headline"}),
        encoding="utf-8",
    )

    older = _write_article(
        tmp_path,
        "older",
        published_at_utc="2026-09-01T12:00:00Z",
        meeting_date="2026-09-01",
    )
    fallback_date = _write_article(
        tmp_path,
        "fallback-date",
        published_at_utc="",
        meeting_date="2026-09-05",
    )
    newest = _write_article(
        tmp_path,
        "newest",
        published_at_utc="2026-09-12T12:00:00Z",
        meeting_date="2026-09-12",
    )

    rows = public_site.load_articles()

    assert rows == [newest, fallback_date, older]
    assert public_site.find_article("fallback-date") == fallback_date
    assert public_site.find_article("does-not-exist") is None


def test_navigation_panels_and_shell_escape_content():
    nav = public_site.filter_nav("rsm")
    assert 'href="/"' in nav
    assert 'href="/?city=rsm"' in nav
    assert 'class="filter active"' in nav
    assert "Rancho Santa Margarita" in nav

    assert "buttondown.com" in public_site.signup_panel()
    assert "About CouncilWatch" in public_site.about_panel()

    response = public_site.shell("<p>Body</p>", "Unsafe <Title>")
    html = response.body.decode("utf-8")
    assert "<p>Body</p>" in html
    assert "Unsafe &lt;Title&gt;" in html
    assert "Independent local government coverage" in html


def test_home_empty_and_invalid_city_falls_back_to_all(tmp_path, monkeypatch):
    monkeypatch.setattr(public_site, "PUBLISHED", tmp_path)

    response = client.get("/")
    assert response.status_code == 200
    assert "0 published reports" in response.text
    assert "No published coverage for this city yet." in response.text
    assert "Get CouncilWatch updates" in response.text
    assert "About CouncilWatch" in response.text

    _write_article(tmp_path, "one")

    response = client.get("/?city=totally-invalid")
    assert response.status_code == 200
    assert "1 published report" in response.text
    assert "Headline one" in response.text
    assert 'class="filter active"' in response.text


def test_home_city_filter_and_card_escaping(tmp_path, monkeypatch):
    monkeypatch.setattr(public_site, "PUBLISHED", tmp_path)

    _write_article(
        tmp_path,
        "rsm-story",
        headline="Budget <Approved>",
        dek="Taxes & roads",
        city_name="Rancho <Santa> Margarita",
        city_slug="rsm",
        meeting_date="2026-09-11",
        published_at_utc="2026-09-11T20:00:00Z",
    )
    _write_article(
        tmp_path,
        "mission-story",
        headline="Mission Viejo Story",
        city_name="Mission Viejo",
        city_slug="mission-viejo",
        meeting_date="2026-09-12",
        published_at_utc="2026-09-12T20:00:00Z",
    )

    response = client.get("/?city=rsm")

    assert response.status_code == 200
    assert "1 published report" in response.text
    assert "Budget &lt;Approved&gt;" in response.text
    assert "Taxes &amp; roads" in response.text
    assert "Rancho &lt;Santa&gt; Margarita" in response.text
    assert "September 11, 2026" in response.text
    assert "Mission Viejo Story" not in response.text
    assert 'href="/article/rsm-story"' in response.text


def test_article_page_404_and_full_render(tmp_path, monkeypatch):
    monkeypatch.setattr(public_site, "PUBLISHED", tmp_path)

    missing = client.get("/article/not-there")
    assert missing.status_code == 404
    assert missing.text == "Article not found"

    _write_article(
        tmp_path,
        "full",
        headline="Council <Vote>",
        dek="A & B",
        city_name="Lake Forest",
        city_slug="lake-forest",
        body=["First <paragraph>", "Second & paragraph"],
        key_facts=["Fact <one>", "Fact & two"],
        source_url="https://city.example/meeting",
        agenda_url="javascript:alert(1)",
        recording_url="http://video.example/watch",
        technology_assisted=True,
    )

    response = client.get("/article/full")

    assert response.status_code == 200
    assert "Council &lt;Vote&gt;" in response.text
    assert "A &amp; B" in response.text
    assert "First &lt;paragraph&gt;" in response.text
    assert "Second &amp; paragraph" in response.text
    assert "Fact &lt;one&gt;" in response.text
    assert "Fact &amp; two" in response.text
    assert "Official meeting source" in response.text
    assert "https://city.example/meeting" in response.text
    assert "Official recording" in response.text
    assert "http://video.example/watch" in response.text
    assert "Official agenda" not in response.text
    assert "javascript:alert(1)" not in response.text
    assert "technology-assisted analysis" in response.text
    assert "September 10, 2026" in response.text
    assert "<title>Council &lt;Vote&gt;</title>" in response.text


def test_article_page_fallbacks_for_empty_facts_sources_and_title(tmp_path, monkeypatch):
    monkeypatch.setattr(public_site, "PUBLISHED", tmp_path)

    _write_article(
        tmp_path,
        "fallbacks",
        headline="Fallback Story",
        body=[],
        key_facts=[],
        source_url="",
        agenda_url="ftp://example.com/agenda",
        recording_url=None,
        technology_assisted=False,
    )

    response = client.get("/article/fallbacks")

    assert response.status_code == 200
    assert "No additional key facts." in response.text
    assert "No source links available." in response.text
    assert "technology-assisted analysis" not in response.text


def test_health_counts_only_valid_published_articles(tmp_path, monkeypatch):
    monkeypatch.setattr(public_site, "PUBLISHED", tmp_path)

    _write_article(tmp_path, "one")
    _write_article(tmp_path, "two")
    (tmp_path / "bad.json").write_text("not json", encoding="utf-8")

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "published_articles": 2,
    }
