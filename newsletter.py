from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

from settings import (
    BUTTONDOWN_API_BASE,
    BUTTONDOWN_API_KEY,
)


MAX_NEWSLETTER_SUBJECT = 68

CITY_SUBJECT_NAMES = {
    "rsm": "RSM",
    "aliso-viejo": "Aliso Viejo",
    "mission-viejo": "Mission Viejo",
    "lake-forest": "Lake Forest",
    "laguna-niguel": "Laguna Niguel",
}

SUBJECT_REPLACEMENTS = (
    (
        r"\bgeotechnical engineering agreements\b",
        "Geotechnical Contracts",
    ),
    (
        r"\bprofessional services agreements\b",
        "Contracts",
    ),
    (
        r"\bschool zone speed limits\b",
        "School Zones",
    ),
    (
        r"\bschool zone speed limit\b",
        "School Zones",
    ),
    (
        r"\btraffic signal materials\b",
        "Traffic Signals",
    ),
    (
        r"\bagenda management software\b",
        "Agenda Software",
    ),
)


def now_utc():
    return datetime.now(
        timezone.utc
    ).isoformat()


def pretty_date(value):
    text = str(value or "").strip()

    if not text:
        return ""

    try:
        dt = datetime.strptime(
            text[:10],
            "%Y-%m-%d",
        )

        return (
            f"{dt.strftime('%B')} "
            f"{dt.day}, "
            f"{dt.year}"
        )

    except ValueError:
        return text


def _clean_subject_text(value):
    text = re.sub(
        r"\s+",
        " ",
        str(value or "").strip(),
    )

    return text.rstrip(". ")


def _subject_city(data):
    slug = str(
        data.get("city_slug") or ""
    ).strip()

    return (
        CITY_SUBJECT_NAMES.get(slug)
        or str(data.get("city_name") or "").strip()
        or "CouncilWatch"
    )


def _strip_city_council_prefix(
    headline,
    data,
    short_city,
):
    city = str(
        data.get("city_name") or ""
    ).strip()

    prefixes = [
        f"{city} City Council " if city else "",
        f"{city} Council " if city else "",
        (
            f"{short_city} City Council "
            if short_city
            else ""
        ),
        (
            f"{short_city} Council "
            if short_city
            else ""
        ),
        "City Council ",
    ]

    for prefix in prefixes:
        if (
            prefix
            and headline.lower().startswith(
                prefix.lower()
            )
        ):
            return headline[
                len(prefix):
            ].strip()

    return headline


def _compress_subject_phrases(value):
    text = value

    for pattern, replacement in SUBJECT_REPLACEMENTS:
        text = re.sub(
            pattern,
            replacement,
            text,
            flags=re.IGNORECASE,
        )

    text = re.sub(
        r"\s+and\s+",
        " & ",
        text,
        flags=re.IGNORECASE,
    )

    return _clean_subject_text(text)


def _trim_subject_core(
    core,
    budget,
):
    if len(core) <= budget:
        return core

    boundaries = [
        match.start()
        for match in re.finditer(
            r",\s+|\s+&\s+|\s+-\s+",
            core,
        )
        if match.start() <= budget
    ]

    if boundaries:
        boundary = boundaries[-1]
        candidate = core[
            :boundary
        ].rstrip(" ,&-")

        if len(candidate) >= min(
            34,
            max(20, budget // 2),
        ):
            return candidate

    if budget <= 4:
        return core[:budget]

    cut = core[: budget - 3].rstrip()

    if " " in cut:
        cut = cut.rsplit(
            " ",
            1,
        )[0]

    return cut.rstrip(" ,&-") + "..."


def newsletter_subject(
    data,
    max_length=MAX_NEWSLETTER_SUBJECT,
):
    """
    Build a concise email subject from the article headline.

    The website headline remains untouched. Only the email
    subject is shortened, with a hard length cap so common
    mail clients and Buttondown are less likely to truncate it.
    """

    headline = _clean_subject_text(
        data.get("headline")
    )

    if not headline:
        return "CouncilWatch update"

    short_city = _subject_city(data)

    core = _strip_city_council_prefix(
        headline,
        data,
        short_city,
    )

    core = _compress_subject_phrases(
        core
    )

    prefix = (
        f"{short_city}: "
        if short_city
        else ""
    )

    candidate = prefix + core

    if len(candidate) <= max_length:
        return candidate

    # If the headline is still long, remove a generic action
    # verb before dropping substantive topics.
    shorter_core = re.sub(
        (
            r"^(?:Approves|Reviews|Awards|Adopts|"
            r"Authorizes|Considers|Endorses|Accepts|"
            r"Receives|Discusses|Votes to|Moves to)\s+"
        ),
        "",
        core,
        count=1,
        flags=re.IGNORECASE,
    ).strip()

    candidate = prefix + shorter_core

    if len(candidate) <= max_length:
        return candidate

    budget = max(
        1,
        max_length - len(prefix),
    )

    return (
        prefix
        + _trim_subject_core(
            shorter_core,
            budget,
        )
    )[:max_length]


def newsletter_body(data):
    """
    Build Buttondown rich HTML.

    We explicitly select Buttondown's fancy editor mode so
    Markdown is never shown literally in the rendered email.
    """

    dek = str(
        data.get("dek") or ""
    ).strip()

    city = str(
        data.get("city_name") or ""
    ).strip()

    meeting_date = pretty_date(
        data.get("meeting_date")
    )

    body = [
        str(p).strip()
        for p in data.get("body", [])
        if str(p).strip()
    ]

    meta = " · ".join(
        x
        for x in (
            city,
            meeting_date,
        )
        if x
    )

    parts = [
        "<!-- buttondown-editor-mode: fancy -->",
    ]

    if meta:
        parts.append(
            "<p><strong>"
            + html.escape(meta)
            + "</strong></p>"
        )

    if dek:
        parts.append(
            "<p><em>"
            + html.escape(dek)
            + "</em></p>"
        )

    for paragraph in body:
        parts.append(
            "<p>"
            + html.escape(paragraph)
            + "</p>"
        )

    sources = []

    for label, key in (
        ("Official source", "source_url"),
        ("Agenda", "agenda_url"),
        ("Recording", "recording_url"),
    ):
        url = str(
            data.get(key) or ""
        ).strip()

        if url.startswith(
            ("http://", "https://")
        ):
            sources.append(
                '<li><a href="'
                + html.escape(
                    url,
                    quote=True,
                )
                + '">'
                + html.escape(label)
                + "</a></li>"
            )

    if sources:
        parts.extend([
            "<hr>",
            "<h2>Official sources</h2>",
            "<ul>",
            *sources,
            "</ul>",
        ])

    parts.extend([
        "<hr>",
        (
            "<p><small>"
            "This report was prepared using "
            "technology-assisted analysis of official "
            "public meeting materials and was reviewed "
            "before publication."
            "</small></p>"
        ),
        (
            "<p><small>"
            "CouncilWatch covers local government "
            "across South Orange County."
            "</small></p>"
        ),
    ])

    return "\n".join(parts)

def _request(
    method,
    path,
    payload,
):
    if not BUTTONDOWN_API_KEY:
        raise RuntimeError(
            "BUTTONDOWN_API_KEY is not configured."
        )

    url = (
        BUTTONDOWN_API_BASE.rstrip("/")
        + "/"
        + path.lstrip("/")
    )

    request = urllib.request.Request(
        url,
        data=json.dumps(
            payload
        ).encode("utf-8"),
        method=method,
        headers={
            "Authorization":
                f"Token {BUTTONDOWN_API_KEY}",
            "Content-Type":
                "application/json",
        },
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=30,
        ) as response:
            raw = response.read().decode(
                "utf-8"
            )

    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(
            "utf-8",
            errors="replace",
        )

        raise RuntimeError(
            f"Buttondown HTTP {exc.code}: "
            f"{detail[:500]}"
        ) from exc

    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Buttondown connection failed: "
            f"{exc.reason}"
        ) from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError(
            "Buttondown returned invalid JSON."
        )


def ensure_buttondown_draft(
    data,
    force_update=False,
):
    """
    Create or update a Buttondown draft.

    Never sends an email.
    """

    revision = int(
        data.get("revision", 1)
    )

    existing_id = str(
        data.get(
            "newsletter_draft_id"
        )
        or ""
    ).strip()

    existing_revision = int(
        data.get(
            "newsletter_draft_revision",
            0,
        )
        or 0
    )

    if (
        existing_id
        and existing_revision == revision
        and not force_update
    ):
        return {
            "ok": True,
            "created": False,
            "updated": False,
            "id": existing_id,
            "status": "draft",
            "subject": data.get(
                "newsletter_draft_subject"
            ),
            "message":
                "Existing Buttondown draft is current.",
        }

    subject = newsletter_subject(data)

    payload = {
        "subject": subject,
        "body": newsletter_body(data),
        "status": "draft",
    }

    attempt = now_utc()

    try:
        if existing_id:
            result = _request(
                "PATCH",
                f"emails/{existing_id}",
                payload,
            )

            created = False
            updated = True

        else:
            result = _request(
                "POST",
                "emails",
                payload,
            )

            created = True
            updated = False

        draft_id = str(
            result.get("id")
            or existing_id
        ).strip()

        if not draft_id:
            raise RuntimeError(
                "Buttondown response did not contain "
                "an email ID."
            )

        data[
            "newsletter_draft_id"
        ] = draft_id

        data[
            "newsletter_draft_revision"
        ] = revision

        data[
            "newsletter_draft_status"
        ] = "draft"

        data[
            "newsletter_draft_subject"
        ] = subject

        data[
            "newsletter_draft_last_attempt_at"
        ] = attempt

        data[
            "newsletter_draft_error"
        ] = None

        if created:
            data[
                "newsletter_draft_created_at"
            ] = attempt

        return {
            "ok": True,
            "created": created,
            "updated": updated,
            "id": draft_id,
            "status":
                result.get("status")
                or "draft",
            "subject": subject,
            "subject_length": len(subject),
        }

    except Exception as exc:
        data[
            "newsletter_draft_last_attempt_at"
        ] = attempt

        data[
            "newsletter_draft_error"
        ] = str(exc)

        return {
            "ok": False,
            "created": False,
            "updated": False,
            "id": existing_id or None,
            "error": str(exc),
        }
