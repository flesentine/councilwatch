from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import requests

from settings import (
    WORK,
    NTFY_SERVER,
    NTFY_TOPIC,
    COUNCILWATCH_REVIEW_BASE_URL,
)


STATE_FILE = WORK / "notification_state.json"


def _load_state():
    try:
        data = json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    return {}


def _save_state(state):
    STATE_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp = STATE_FILE.with_suffix(".tmp")

    temp.write_text(
        json.dumps(
            state,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    temp.replace(STATE_FILE)


def _human_date(value):
    try:
        dt = datetime.strptime(
            str(value),
            "%Y-%m-%d",
        )
        return dt.strftime("%B %-d, %Y")
    except Exception:
        return str(value or "")


def _send(title, message, click=""):
    if not NTFY_TOPIC:
        print(
            "Notification skipped: NTFY_TOPIC "
            "is not configured."
        )
        return False

    server = (
        NTFY_SERVER
        or "https://ntfy.sh"
    ).rstrip("/")

    headers = {
        "Title": title,
        "Priority": "default",
        "Tags": "newspaper",
    }

    if click:
        headers["Click"] = click

    url = f"{server}/{NTFY_TOPIC}"

    last_error = None

    # A temporary network problem should not immediately
    # cause us to miss the notification.
    for attempt in range(1, 4):
        try:
            response = requests.post(
                url,
                data=message.encode("utf-8"),
                headers=headers,
                timeout=15,
            )

            response.raise_for_status()
            return True

        except Exception as exc:
            last_error = exc

            if attempt < 3:
                time.sleep(2)

    print(
        "WARNING: phone notification failed:",
        type(last_error).__name__,
        last_error,
    )

    return False


def notify_ready_for_review(meeting, story):
    """
    Send exactly one successful ready-for-review
    notification per city + meeting ID.

    A failed send is NOT recorded, so a later manual
    rerun can try again.
    """

    slug = str(
        meeting.get("city_slug")
        or story.get("city_slug")
        or ""
    )

    external_id = str(
        meeting.get("external_id")
        or story.get("external_id")
        or ""
    )

    if not slug or not external_id:
        print(
            "WARNING: notification skipped; "
            "meeting identity missing."
        )
        return False

    key = f"{slug}:{external_id}"

    state = _load_state()

    if key in state:
        print(
            "Phone notification already sent:",
            key,
        )
        return True

    city = (
        story.get("city_name")
        or meeting.get("city_name")
        or slug
    )

    date = _human_date(
        story.get("meeting_date")
        or meeting.get("meeting_date")
    )

    headline = str(
        story.get("headline")
        or "New CouncilWatch story"
    ).strip()

    message = (
        f"{city} - {date}\n"
        f"{headline}"
    )

    review_base = (
        COUNCILWATCH_REVIEW_BASE_URL
        or ""
    ).rstrip("/")

    click = ""

    if review_base:
        click = (
            f"{review_base}/story/"
            f"{slug}/{external_id}"
        )

    sent = _send(
        "CouncilWatch - Ready for review",
        message,
        click,
    )

    if not sent:
        return False

    state[key] = {
        "city_slug": slug,
        "external_id": external_id,
        "headline": headline,
        "sent_at": datetime.now().astimezone().isoformat(),
    }

    _save_state(state)

    print(
        "Phone notification sent:",
        city,
        external_id,
    )

    return True


def send_test_notification():
    return _send(
        "CouncilWatch - Notification test",
        (
            "Phone notifications are connected.\n"
            "Future audited meetings will appear here."
        ),
        (
            COUNCILWATCH_REVIEW_BASE_URL.rstrip("/")
            + "/"
            if COUNCILWATCH_REVIEW_BASE_URL
            else ""
        ),
    )


if __name__ == "__main__":
    import sys

    if "--test" in sys.argv:
        ok = send_test_notification()

        print(
            "TEST:",
            "SENT" if ok else "FAILED",
        )

        raise SystemExit(
            0 if ok else 1
        )

    print(
        "Use: python notifications.py --test"
    )
