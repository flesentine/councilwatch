
from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo
from dateutil import parser as dateparser
from bs4 import BeautifulSoup

from .common import Meeting, absolute, clean_text, fetch, parse_date_from_text, stable_id
from .granicus import discover as discover_granicus

MONTH_DAY = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\s+\d{1,2}\b", re.I
)


def _official_upcoming(city: dict):
    url = city.get("upcoming_url")
    if not url:
        return None
    final_url, html = fetch(url)
    soup = BeautifulSoup(html, "html.parser")
    today = datetime.now(ZoneInfo(city.get("timezone", "America/Los_Angeles"))).date()
    candidates = []

    # Mission Viejo event cards render the date near a "City Council Meeting" link.
    for a in soup.find_all("a", href=True):
        label = clean_text(a.get_text(" ", strip=True))
        if "city council meeting" not in label.lower():
            continue
        parent = a
        text = label
        for _ in range(5):
            parent = getattr(parent, "parent", None)
            if parent is None:
                break
            text = clean_text(parent.get_text(" ", strip=True))
            d = parse_date_from_text(text)
            if not d:
                m = MONTH_DAY.search(text)
                if m:
                    try:
                        d = dateparser.parse(f"{m.group(0)}, {today.year}", fuzzy=False).date()
                    except Exception:
                        d = None
            if d:
                break
        if not d or d <= today:
            continue
        candidates.append((d, absolute(final_url, a["href"])))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    d, href = candidates[0]
    return Meeting(
        city_slug=city["slug"], title="City Council Meeting",
        meeting_date=d.isoformat(),
        external_id=stable_id(city["slug"], d.isoformat(), "City Council Meeting"),
        source_url=href or final_url,
        agenda_url=city.get("agenda_index", ""), recording_url="",
        recording_status="missing", status="upcoming", kind="upcoming",
        notes="Upcoming date discovered from the City of Mission Viejo official council events page.",
    )


def discover(city: dict) -> dict:
    result = discover_granicus(city)

    # Mission Viejo publishes some government bodies through
    # separate Granicus ViewPublisher feeds. Merge their
    # selected meetings into the normal discovery result.
    selected = list(
        result.get("selected_meetings", [])
    )

    seen = {
        (m.city_slug, str(m.external_id))
        for m in selected
    }

    for cfg in city.get(
        "additional_granicus_sources",
        [],
    ):
        extra_city = dict(city)
        extra_city.update(cfg)

        extra = discover_granicus(extra_city)

        candidates = list(
            extra.get("selected_meetings", [])
        )

        if not candidates:
            for key in (
                "latest_completed",
                "next_upcoming",
            ):
                m = extra.get(key)
                if m:
                    candidates.append(m)

        for m in candidates:
            key = (
                m.city_slug,
                str(m.external_id),
            )

            if key in seen:
                continue

            seen.add(key)
            selected.append(m)

    result["selected_meetings"] = selected

    try:
        upcoming = _official_upcoming(city)
        if upcoming:
            result["next_upcoming"] = upcoming
    except Exception as exc:
        result.setdefault("warnings", []).append(
            f"Mission Viejo upcoming-event probe failed: "
            f"{type(exc).__name__}: {exc}"
        )

    return result
