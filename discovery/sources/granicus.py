
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from .common import Meeting, absolute, clean_text, contains_meeting_title, fetch, parse_date_from_text, stable_id


def _clip_id_from_row(row) -> str:
    for a in row.find_all("a"):
        href = a.get("href", "")
        onclick = a.get("onclick", "")
        blob = href + " " + onclick
        m = re.search(r"clip_id=(\d+)", blob)
        if m:
            return m.group(1)
    # Last resort: Granicus markup can contain clip_id outside the href.
    m = re.search(r"clip_id=(\d+)", str(row))
    return m.group(1) if m else ""


def _agenda_from_row(base: str, row, clip_id: str = "") -> str:
    for a in row.find_all("a"):
        label = clean_text(a.get_text(" ", strip=True)).lower()
        href = a.get("href", "")
        if "agenda" in label and href:
            return absolute(base, href)

    # Granicus AgendaViewer URLs are predictable even when
    # the archive row does not expose an Agenda link.
    if clip_id:
        q = parse_qs(urlparse(base).query)
        view_id = (q.get("view_id") or [""])[0]

        if view_id:
            return absolute(
                base,
                f"AgendaViewer.php?view_id={view_id}&clip_id={clip_id}",
            )

    return ""


def _recording_from_row(base: str, row, clip_id: str) -> str:
    for a in row.find_all("a"):
        href = a.get("href", "")
        onclick = a.get("onclick", "")
        blob = href + " " + onclick
        if "MediaPlayer.php" in blob:
            # Extract URL even if buried in javascript.
            m = re.search(r"(MediaPlayer\.php\?[^\"' )]+)", blob)
            if m:
                return absolute(base, m.group(1).replace("&amp;", "&"))
            if href and not href.lower().startswith("javascript"):
                return absolute(base, href)
    if clip_id:
        # MediaPlayer URL is stable across Granicus publishers; infer view_id from source URL.
        q = parse_qs(urlparse(base).query)
        view_id = (q.get("view_id") or [""])[0]
        if view_id:
            return absolute(base, f"MediaPlayer.php?view_id={view_id}&clip_id={clip_id}")
    return ""


def discover(city: dict) -> dict:
    final_url, html = fetch(city["source_url"])
    soup = BeautifulSoup(html, "html.parser")
    today = datetime.now(ZoneInfo(city.get("timezone", "America/Los_Angeles"))).date()

    completed = []
    upcoming = []
    include = city.get("meeting_terms", ["city council"])
    exclude = city.get("exclude_terms", [])

    for row in soup.find_all("tr"):
        text = clean_text(row.get_text(" ", strip=True))
        if not text or not contains_meeting_title(text, include, exclude):
            continue
        if "cancel" in text.lower():
            continue
        d = parse_date_from_text(text)
        if not d:
            continue

        clip_id = _clip_id_from_row(row)
        agenda = _agenda_from_row(final_url, row)
        recording = _recording_from_row(final_url, row, clip_id)

        # Granicus uses the same view_id/clip_id for MediaPlayer
        # and AgendaViewer. If the archive row omitted an agenda
        # link, derive it from the confirmed recording URL.
        if not agenda and recording and "MediaPlayer.php" in recording:
            agenda = recording.replace(
                "MediaPlayer.php",
                "AgendaViewer.php",
            )

        # Use first table cell as title when possible.
        # Preserve any configured meeting body, rather than
        # forcing everything to "City Council".
        cells = row.find_all(["td", "th"])
        title = clean_text(cells[0].get_text(" ", strip=True)) if cells else text

        if (
            not title
            or not any(term.lower() in title.lower() for term in include)
        ):
            title = next(
                (
                    clean_text(c)
                    for c in text.split("|")
                    if any(term.lower() in c.lower() for term in include)
                ),
                title or "Government Meeting",
            )

        ext = clip_id or stable_id(city["slug"], d.isoformat(), title)
        # Treat every same-day meeting as upcoming.
        # The autonomous processor already refuses same-day
        # processing; discovery should describe it consistently.
        if d >= today:
            kind = "upcoming"
        else:
            kind = "completed"

        status = (
            "ready"
            if kind == "completed" and recording
            else "upcoming"
            if kind == "upcoming"
            else "waiting_recording"
        )
        m = Meeting(
            city_slug=city["slug"], title=title, meeting_date=d.isoformat(), external_id=ext,
            source_url=final_url, agenda_url=agenda, recording_url=recording,
            recording_status="found" if recording else "missing", status=status, kind=kind,
        )
        (completed if kind == "completed" else upcoming).append(m)

    completed.sort(key=lambda x: x.meeting_date, reverse=True)
    upcoming.sort(key=lambda x: x.meeting_date)

    # Select the latest completed and next upcoming meeting
    # for EACH configured meeting type. This lets a city track
    # City Council + Planning Commission without backfilling
    # the entire historical archive.
    selected_meetings = []
    seen_ids = set()

    for term in include:
        term_lower = term.lower()

        latest_for_term = next(
            (
                m for m in completed
                if term_lower in (m.title or "").lower()
            ),
            None,
        )

        upcoming_for_term = next(
            (
                m for m in upcoming
                if term_lower in (m.title or "").lower()
            ),
            None,
        )

        for m in (latest_for_term, upcoming_for_term):
            if not m:
                continue

            key = (m.city_slug, m.external_id)

            if key in seen_ids:
                continue

            seen_ids.add(key)
            selected_meetings.append(m)

    return {
        "latest_completed": completed[0] if completed else None,
        "next_upcoming": upcoming[0] if upcoming else None,
        "selected_meetings": selected_meetings,
        "counts": {
            "completed_candidates": len(completed),
            "upcoming_candidates": len(upcoming),
        },
        "source_url": final_url,
    }
