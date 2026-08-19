
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import yt_dlp

from .common import clean_text, contains_meeting_title


def latest_matching_video(channel_url: str, terms: list[str], limit: int = 30) -> dict | None:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "playlistend": limit,
        "socket_timeout": 25,
        "skip_download": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(channel_url, download=False)
    except Exception:
        return None

    entries = (info or {}).get("entries") or []
    for e in entries:
        title = clean_text(str(e.get("title") or ""))
        if not contains_meeting_title(title, terms):
            continue
        if any(x in title.lower() for x in ("shorts", "promo", "preview")):
            continue
        video_id = str(e.get("id") or "")
        if not video_id:
            continue
        upload_date = str(e.get("upload_date") or "")
        timestamp = e.get("timestamp") or e.get("release_timestamp")
        iso_date = ""
        if len(upload_date) == 8 and upload_date.isdigit():
            iso_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
        elif timestamp:
            iso_date = datetime.fromtimestamp(timestamp, ZoneInfo("America/Los_Angeles")).date().isoformat()
        return {
            "id": video_id,
            "title": title,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "date": iso_date,
        }
    return None
