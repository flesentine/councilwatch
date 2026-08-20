from __future__ import annotations

import re
import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 CouncilWatchOfficialVerification/1.0"
HEADERS = {"User-Agent": UA}

OFFICIAL_ENTITY_SOURCES = {
    "rsm": [
        "https://www.cityofrsm.org/160/Mayor-City-Council",
        "https://www.cityofrsm.org/Directory.aspx?did=8",
    ],
    "aliso-viejo": [
        "https://avcity.org/222/City-Council",
        "https://avcity.org/directory.aspx?did=17",
        "https://avcity.org/directory.aspx?did=8",
    ],
    "mission-viejo": [
        "https://www.missionviejo.gov/government/city-council",
        "https://www.missionviejo.gov/government/city-directory",
    ],
    "laguna-niguel": [
        "https://www.cityoflagunaniguel.org/396/Mayor-City-Council",
        "https://www.cityoflagunaniguel.org/Directory.aspx?did=80",
        "https://www.cityoflagunaniguel.org/Directory.aspx?did=70",
    ],
    "lake-forest": [
        "https://www.lakeforestca.gov/city_government/city_council/index.php",
        "https://www.lakeforestca.gov/services/contact_us/city_directory.php",
    ],
}

_CACHE = {}


def _normalize(text):
    return re.sub(
        r"\s+",
        " ",
        str(text or ""),
    ).strip().casefold()


def _fetch(url):
    if url in _CACHE:
        return _CACHE[url]

    try:
        r = requests.get(
            url,
            headers=HEADERS,
            timeout=20,
        )
        r.raise_for_status()

        soup = BeautifulSoup(
            r.text,
            "html.parser",
        )

        for tag in soup(
            ["script", "style", "noscript", "svg"]
        ):
            tag.decompose()

        text = soup.get_text(
            "\n",
            strip=True,
        )

    except Exception as exc:
        print(
            "WARNING: official entity source failed:",
            url,
            type(exc).__name__,
            exc,
        )
        text = ""

    _CACHE[url] = text
    return text


def official_entity_material(meeting):
    slug = meeting.get("city_slug", "")

    pages = []

    for url in OFFICIAL_ENTITY_SOURCES.get(
        slug,
        [],
    ):
        text = _fetch(url)

        if text:
            pages.append({
                "url": url,
                "text": text,
            })

    combined = []

    for page in pages:
        combined.append(
            "OFFICIAL CITY SOURCE:\n"
            + page["url"]
            + "\n"
            + page["text"]
        )

    return {
        "pages": pages,
        "text": "\n\n".join(combined),
    }


def find_official_support(
    canonical,
    agenda,
    agenda_url,
    pages,
):
    wanted = _normalize(canonical)

    if not wanted:
        return ""

    if wanted in _normalize(agenda):
        return agenda_url or ""

    for page in pages:
        if wanted in _normalize(page["text"]):
            return page["url"]

    return ""
