
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Optional
from urllib.parse import urljoin

import requests
from dateutil import parser as dateparser

HEADERS = {
    "User-Agent": "CouncilWatchPi/0.2 (+civic meeting discovery; contact: local pilot)"
}

@dataclass
class Meeting:
    city_slug: str
    title: str
    meeting_date: str
    external_id: str
    source_url: str
    agenda_url: str = ""
    recording_url: str = ""
    recording_status: str = "unknown"  # found / missing / unknown / not_expected
    status: str = "unknown"             # ready / waiting_recording / agenda_only / upcoming / canceled / unknown
    kind: str = "completed"             # completed / upcoming
    notes: str = ""

    def to_dict(self):
        return asdict(self)


def fetch(url: str, timeout: int = 30) -> tuple[str, str]:
    r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    return r.url, r.text


def clean_text(value: str) -> str:
    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value).strip()
    # Granicus sometimes exposes a Unix timestamp before the human-readable date.
    value = re.sub(r"\b1\d{9}\s+(?=[A-Z][a-z]{2,8}\b)", "", value)
    return value


def parse_date_from_text(text: str) -> Optional[date]:
    text = clean_text(text)
    pats = [
        r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},\s+20\d{2}\b",
        r"\b\d{1,2}/\d{1,2}/20\d{2}\b",
    ]
    for pat in pats:
        m = re.search(pat, text, re.I)
        if m:
            try:
                return dateparser.parse(m.group(0), fuzzy=False).date()
            except Exception:
                pass
    return None


def stable_id(*parts: str) -> str:
    raw = "|".join(parts).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


def absolute(base: str, href: str) -> str:
    return urljoin(base, href or "")


def contains_meeting_title(text: str, include: list[str], exclude: list[str] | None = None) -> bool:
    t = clean_text(text).lower()
    if include and not any(term.lower() in t for term in include):
        return False
    if exclude and any(term.lower() in t for term in exclude):
        return False
    return True
