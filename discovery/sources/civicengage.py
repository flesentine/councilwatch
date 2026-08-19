
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag

from .common import Meeting, absolute, clean_text, fetch, parse_date_from_text, stable_id
from .youtube_tools import latest_matching_video


def discover(city: dict) -> dict:
    final_url, html = fetch(city["source_url"])
    soup = BeautifulSoup(html, "html.parser")
    today = datetime.now(
        ZoneInfo(
            city.get(
                "timezone",
                "America/Los_Angeles",
            )
        )
    ).date()

    include = city.get(
        "meeting_terms",
        ["city council"],
    )

    # By default, every discovered meeting type is expected
    # to have a recording. Cities may override this with
    # recording_terms when some bodies publish agendas only.
    recording_terms = city.get(
        "recording_terms",
        include,
    )

    records = []

    for h in soup.find_all("h3"):
        heading = clean_text(
            h.get_text(" ", strip=True)
        )

        d = parse_date_from_text(heading)

        if not d:
            continue

        # CivicEngage stores the meeting description and
        # download link in the h3's parent container. This is
        # important because cancellation notices may not appear
        # in the sibling text we previously inspected.
        # Only trust the h3 parent as an event container if it
        # contains exactly one h3. Some CivicEngage layouts use a
        # broad parent containing several events; using that text
        # can leak another meeting type/date into this record.
        parent = h.parent
        container = None

        if isinstance(parent, Tag):
            if len(parent.find_all("h3")) == 1:
                container = parent

        parts = [heading]

        if container:
            container_text = clean_text(
                container.get_text(
                    " ",
                    strip=True,
                )
            )

            if container_text:
                parts.append(container_text)

        agenda = ""

        if container:
            for a in container.find_all(
                "a",
                href=True,
            ):
                label = clean_text(
                    a.get_text(
                        " ",
                        strip=True,
                    )
                ).lower()

                href = a.get("href", "")

                if href and (
                    "agenda" in label
                    or "download" in label
                    or "staff report" in label
                ):
                    agenda = absolute(
                        final_url,
                        href,
                    )
                    break

        node = h.next_sibling
        steps = 0

        while node is not None and steps < 12:
            steps += 1

            if (
                isinstance(node, Tag)
                and node.name == "h3"
            ):
                break

            if isinstance(node, Tag):
                txt = clean_text(
                    node.get_text(
                        " ",
                        strip=True,
                    )
                )

                if txt:
                    parts.append(txt)

                for a in node.find_all(
                    "a",
                    href=True,
                ):
                    label = clean_text(
                        a.get_text(
                            " ",
                            strip=True,
                        )
                    ).lower()

                    if (
                        not agenda
                        and (
                            "agenda" in label
                            or "download" in label
                        )
                    ):
                        agenda = absolute(
                            final_url,
                            a["href"],
                        )

            node = node.next_sibling

        blob = clean_text(
            " ".join(parts)
        )

        blob_lower = blob.lower()

        matched_term = next(
            (
                term
                for term in include
                if term.lower() in blob_lower
            ),
            None,
        )

        if not matched_term:
            continue

        if (
            "cancellation notice" in blob_lower
            or "cancelled" in blob_lower
            or "canceled" in blob_lower
        ):
            continue

        if "planning commission" in blob_lower:
            title = "Planning Commission Meeting"

        elif "city council" in blob_lower:
            title = "City Council Meeting"

            if "special city council" in blob_lower:
                title = (
                    "Special City Council Meeting"
                )

            elif "regular city council" in blob_lower:
                title = (
                    "Regular City Council Meeting"
                )

        else:
            title = (
                matched_term.title()
                + " Meeting"
            )

        ext = stable_id(
            city["slug"],
            d.isoformat(),
            title,
        )

        records.append(
            (
                d,
                title,
                ext,
                agenda,
                blob[:300],
                matched_term,
            )
        )

    records.sort(
        key=lambda x: x[0],
        reverse=True,
    )

    selected = []

    for term in include:
        term_lower = term.lower()

        recording_expected = any(
            recording_term.lower() == term_lower
            for recording_term in recording_terms
        )

        matching = [
            x
            for x in records
            if x[5].lower() == term_lower
        ]

        # For CivicEngage, don't promote a past calendar/date
        # candidate to a completed meeting unless it has an
        # agenda/download link. This prevents scheduled, canceled,
        # or phantom calendar dates from becoming completed rows.
        completed_rows = [
            x
            for x in matching
            if x[0] < today
            and x[3]
        ]

        upcoming_rows = sorted(
            [
                x
                for x in matching
                if x[0] >= today
            ],
            key=lambda x: x[0],
        )

        yt = None

        if (
            recording_expected
            and city.get("youtube_url")
        ):
            yt = latest_matching_video(
                city["youtube_url"],
                [term],
            )

        if completed_rows:
            (
                d,
                title,
                ext,
                agenda,
                note,
                _,
            ) = completed_rows[0]

            recording = ""

            if yt:
                yt_date = yt.get("date")

                if not yt_date:
                    parsed = (
                        parse_date_from_text(
                            yt.get(
                                "title",
                                "",
                            )
                        )
                    )

                    if parsed:
                        yt_date = (
                            parsed.isoformat()
                        )

                if (
                    not yt_date
                    or yt_date == d.isoformat()
                ):
                    recording = yt["url"]

            selected.append(
                Meeting(
                    city_slug=city["slug"],
                    title=title,
                    meeting_date=d.isoformat(),
                    external_id=ext,
                    source_url=final_url,
                    agenda_url=agenda,
                    recording_url=recording,
                    recording_status=(
                        "found"
                        if recording
                        else (
                            "unknown"
                            if recording_expected
                            else "not_expected"
                        )
                    ),
                    status=(
                        "ready"
                        if recording
                        else (
                            "waiting_recording"
                            if recording_expected
                            else "agenda_only"
                        )
                    ),
                    kind="completed",
                    notes=(
                        (
                            "AgendaCenter meeting found; "
                            "recording not expected for "
                            "this meeting type."
                        )
                        if not recording_expected
                        else (
                            "AgendaCenter meeting found; "
                            "YouTube recording matching "
                            "is best-effort."
                        )
                    ),
                )
            )

        if upcoming_rows:
            (
                d,
                title,
                ext,
                agenda,
                note,
                _,
            ) = upcoming_rows[0]

            selected.append(
                Meeting(
                    city_slug=city["slug"],
                    title=title,
                    meeting_date=d.isoformat(),
                    external_id=ext,
                    source_url=final_url,
                    agenda_url=agenda,
                    recording_status="missing",
                    status="upcoming",
                    kind="upcoming",
                )
            )

    completed = sorted(
        [
            m
            for m in selected
            if m.kind == "completed"
        ],
        key=lambda m: m.meeting_date,
        reverse=True,
    )

    upcoming = sorted(
        [
            m
            for m in selected
            if m.kind == "upcoming"
        ],
        key=lambda m: m.meeting_date,
    )

    return {
        "latest_completed": (
            completed[0]
            if completed
            else None
        ),
        "next_upcoming": (
            upcoming[0]
            if upcoming
            else None
        ),
        "selected_meetings": selected,
        "counts": {
            "agenda_records": len(records),
        },
        "source_url": final_url,
    }
