from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

from settings import (
    BUTTONDOWN_API_BASE,
    BUTTONDOWN_API_KEY,
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


def newsletter_body(data):
    headline = str(
        data.get("headline") or ""
    ).strip()

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

    lines = []

    if headline:
        lines.extend([
            f"# {headline}",
            "",
        ])

    if dek:
        lines.extend([
            f"*{dek}*",
            "",
        ])

    meta = " · ".join(
        x
        for x in (
            city,
            meeting_date,
        )
        if x
    )

    if meta:
        lines.extend([
            f"**{meta}**",
            "",
        ])

    for paragraph in body:
        lines.extend([
            paragraph,
            "",
        ])

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
                f"- [{label}]({url})"
            )

    if sources:
        lines.extend([
            "## Official sources",
            "",
            *sources,
            "",
        ])

    lines.extend([
        "---",
        "",
        (
            "This report was prepared using "
            "technology-assisted analysis of official "
            "public meeting materials and was reviewed "
            "before publication."
        ),
        "",
        (
            "CouncilWatch covers local government "
            "across South Orange County."
        ),
    ])

    return "\n".join(lines).strip() + "\n"


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
            "message":
                "Existing Buttondown draft is current.",
        }

    payload = {
        "subject": str(
            data.get("headline") or ""
        ).strip(),
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
