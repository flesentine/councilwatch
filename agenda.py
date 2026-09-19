from __future__ import annotations

import io
import re
import requests
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup
from pypdf import PdfReader

UA = "Mozilla/5.0 CouncilWatchPrivateReview/0.3"
HEADERS = {"User-Agent": UA}


def _clean_html_text(raw_html: str) -> str:
    soup = BeautifulSoup(
        raw_html,
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

    return re.sub(
        r"\n{3,}",
        "\n\n",
        text,
    ).strip()


def _granicus_agenda_alternates(parsed):
    if (
        "granicus.com"
        not in parsed.netloc.lower()
        or not parsed.path.lower().endswith(
            "/agendaviewer.php"
        )
    ):
        return []

    q = parse_qs(
        parsed.query
    )

    view_id = (
        q.get("view_id")
        or [""]
    )[0]

    clip_id = (
        q.get("clip_id")
        or [""]
    )[0]

    if not clip_id:
        return []

    base = (
        f"{parsed.scheme}://"
        f"{parsed.netloc}"
    )

    query = (
        f"?view_id={view_id}"
        f"&clip_id={clip_id}"
    )

    return [
        base
        + "/MediaPlayer.php"
        + query,
        base
        + "/GeneratedAgendaViewer.php"
        + query,
    ]


def _fetch_html_text(url: str) -> str:
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30,
        allow_redirects=True,
    )

    response.raise_for_status()

    return _clean_html_text(
        response.text
    )


def agenda_text(url: str, limit: int = 50000) -> str:
    if not url:
        return ""
    try:
        r = requests.get(
            url,
            headers=HEADERS,
            timeout=30,
            allow_redirects=True,
        )
        r.raise_for_status()

    except Exception as exc:
        parsed = urlparse(
            url
        )

        best_alternate = ""

        for alternate_url in (
            _granicus_agenda_alternates(
                parsed
            )
        ):
            try:
                alternate_text = (
                    _fetch_html_text(
                        alternate_url
                    )
                )

                if (
                    len(
                        alternate_text
                    )
                    > len(
                        best_alternate
                    )
                ):
                    best_alternate = (
                        alternate_text
                    )

            except Exception:
                continue

        if best_alternate:
            return best_alternate[
                :limit
            ]

        return (
            f"[Agenda fetch failed: "
            f"{type(exc).__name__}: {exc}]"
        )

    ctype = (r.headers.get("content-type") or "").lower()
    if "pdf" in ctype or r.url.lower().split("?")[0].endswith(".pdf"):
        try:
            reader = PdfReader(io.BytesIO(r.content))
            text = "\n".join((p.extract_text() or "") for p in reader.pages)
            return re.sub(r"\n{3,}", "\n\n", text).strip()[:limit]
        except Exception as exc:
            return f"[Agenda PDF parse failed: {type(exc).__name__}: {exc}]"

    try:
        text = _clean_html_text(
            r.text
        )

        parsed = urlparse(
            url
        )

        # Granicus deployments can expose different amounts of
        # meeting material through AgendaViewer, MediaPlayer and
        # GeneratedAgendaViewer. All three are official Granicus
        # representations of the same clip. Compare them and keep
        # the richest text instead of relying on a page-length
        # heuristic or assuming MediaPlayer is always complete.
        for alternate_url in (
            _granicus_agenda_alternates(
                parsed
            )
        ):
            try:
                alternate_text = (
                    _fetch_html_text(
                        alternate_url
                    )
                )

                if (
                    len(
                        alternate_text
                    )
                    > len(
                        text
                    )
                ):
                    text = alternate_text

            except Exception:
                continue

        return text[:limit]
    except Exception as exc:
        return f"[Agenda HTML parse failed: {type(exc).__name__}: {exc}]"
