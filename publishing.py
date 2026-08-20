from __future__ import annotations

import json
from pathlib import Path

from settings import DRAFTS


PUBLISHED_DIR = DRAFTS.parent / "published"
PUBLISHED_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


def published_path(data: dict) -> Path:
    slug = str(
        data.get("city_slug")
        or ""
    )

    external_id = str(
        data.get("external_id")
        or ""
    )

    if not slug or not external_id:
        raise ValueError(
            "Cannot publish without city_slug and external_id."
        )

    return (
        PUBLISHED_DIR
        / f"{slug}--{external_id}.json"
    )


def public_article_payload(
    data: dict,
) -> dict:
    """
    Return only fields intended for eventual public use.

    Explicitly excludes:
    - audit working data
    - entity-verification working data
    - coverage-plan/editorial reasoning
    - review notes/status
    - internal processing state
    """

    return {
        "schema_version": 1,
        "article_id": (
            f"{data.get('city_slug')}--"
            f"{data.get('external_id')}"
        ),
        "city_slug":
            data.get("city_slug"),
        "city_name":
            data.get("city_name"),
        "meeting_date":
            data.get("meeting_date"),
        "meeting_title":
            data.get("meeting_title"),
        "external_id":
            data.get("external_id"),
        "headline":
            data.get("headline"),
        "dek":
            data.get("dek"),
        "body":
            data.get("body", []),
        "key_facts":
            data.get("key_facts", []),
        "source_url":
            data.get("source_url"),
        "agenda_url":
            data.get("agenda_url"),
        "recording_url":
            data.get("recording_url"),
        "revision":
            int(
                data.get(
                    "revision",
                    1,
                )
            ),
        "published_at_utc":
            data.get("published_at"),
        "generated_at_utc":
            data.get("generated_at_utc"),
        "technology_assisted":
            True,
    }


def publish_copy(
    data: dict,
) -> Path:
    path = published_path(data)
    tmp = path.with_suffix(
        ".json.tmp"
    )

    payload = public_article_payload(
        data
    )

    tmp.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    tmp.replace(path)

    return path


def remove_published_copy(
    data: dict,
) -> Path:
    path = published_path(data)

    try:
        path.unlink()
    except FileNotFoundError:
        pass

    return path
