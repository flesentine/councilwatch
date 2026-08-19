
from __future__ import annotations

from bs4 import BeautifulSoup

from .common import Meeting, clean_text, fetch, parse_date_from_text, stable_id
from .youtube_tools import latest_matching_video


def _next_publicinput(city: dict):
    url = city.get("upcoming_url")
    if not url:
        return None
    try:
        final_url, html = fetch(url)
    except Exception:
        return None
    soup = BeautifulSoup(html, "html.parser")
    # PublicInput puts upcoming event names in h3 headings.
    for h in soup.find_all(["h2", "h3", "h4"]):
        title = clean_text(h.get_text(" ", strip=True))
        if "city council" not in title.lower() and "meeting of the city council" not in title.lower():
            continue
        block = h.parent.get_text(" ", strip=True) if h.parent else title
        d = parse_date_from_text(block)
        # PublicInput may omit year from rendered card; leave unknown rather than guessing.
        if not d:
            continue
        agenda = ""
        for a in h.parent.find_all("a", href=True) if h.parent else []:
            if "agenda" in clean_text(a.get_text(" ", strip=True)).lower():
                from .common import absolute
                agenda = absolute(final_url, a["href"])
                break
        return Meeting(
            city_slug=city["slug"], title=title, meeting_date=d.isoformat(),
            external_id=stable_id(city["slug"], d.isoformat(), title), source_url=final_url,
            agenda_url=agenda, recording_status="missing", status="upcoming", kind="upcoming",
        )
    return None



def _regular_schedule_fallback(city: dict):
    cfg = city.get("regular_schedule")
    if not cfg:
        return None
    from calendar import monthcalendar
    from datetime import date, datetime
    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo(city.get("timezone", "America/Los_Angeles"))).date()
    weekday = int(cfg.get("weekday", 1))
    week_numbers = list(cfg.get("weeks", [1, 3]))
    candidates = []
    for offset in range(3):
        y = today.year + (today.month - 1 + offset) // 12
        m = (today.month - 1 + offset) % 12 + 1
        cal = monthcalendar(y, m)
        weekday_days = [week[weekday] for week in cal if week[weekday] != 0]
        for n in week_numbers:
            if 1 <= n <= len(weekday_days):
                d = date(y, m, weekday_days[n-1])
                if d > today:
                    candidates.append(d)
    if not candidates:
        return None
    d = min(candidates)
    title = cfg.get("title", "City Council Meeting")
    return Meeting(
        city_slug=city["slug"], title=title, meeting_date=d.isoformat(),
        external_id=stable_id(city["slug"], d.isoformat(), title),
        source_url=city.get("source_url", ""), agenda_url="", recording_url="",
        recording_status="missing", status="upcoming", kind="upcoming",
        notes=("Expected regular meeting date derived from the City's published "
               "first/third-Tuesday schedule; confirm when the official agenda posts."),
    )

def discover(city: dict) -> dict:
    terms = city.get(
        "meeting_terms",
        ["city council"],
    )

    selected = []
    seen_ids = set()

    # Fetch the latest recording for EACH configured body.
    # This prevents a newer City Council video from hiding
    # the latest Planning Commission recording.
    for term in terms:
        latest = latest_matching_video(
            city["youtube_url"],
            [term],
        )

        if not latest:
            continue

        video_id = str(latest["id"])

        if video_id in seen_ids:
            continue

        seen_ids.add(video_id)

        title_date = parse_date_from_text(
            latest.get("title", "")
        )

        d = (
            latest.get("date")
            or (
                title_date.isoformat()
                if title_date
                else ""
            )
        )

        selected.append(
            Meeting(
                city_slug=city["slug"],
                title=latest["title"],
                meeting_date=d,
                external_id=video_id,
                source_url=city["source_url"],
                agenda_url=city.get(
                    "agenda_index",
                    "",
                ),
                recording_url=latest["url"],
                recording_status="found",
                status="ready",
                kind="completed",
                notes=(
                    "Meeting date is based on the "
                    "YouTube archive metadata or title."
                ),
            )
        )

    selected.sort(
        key=lambda m: m.meeting_date or "",
        reverse=True,
    )

    completed = (
        selected[0]
        if selected
        else None
    )

    upcoming = _next_publicinput(city)

    if upcoming is None:
        upcoming = _regular_schedule_fallback(city)

    return {
        "latest_completed": completed,
        "next_upcoming": upcoming,
        "selected_meetings": selected,
        "counts": {
            "completed_candidates": len(selected),
        },
        "source_url": city["source_url"],
    }
