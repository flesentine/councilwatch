from __future__ import annotations

import io
import re
import requests
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup
from pypdf import PdfReader

UA = "Mozilla/5.0 CouncilWatchPrivateReview/0.3"
HEADERS = {"User-Agent": UA}


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
        parsed = urlparse(url)

        if (
            "granicus.com" in parsed.netloc.lower()
            and parsed.path.lower().endswith("/agendaviewer.php")
        ):
            q = parse_qs(parsed.query)

            view_id = (q.get("view_id") or [""])[0]
            clip_id = (q.get("clip_id") or [""])[0]

            if clip_id:
                player_url = (
                    f"{parsed.scheme}://{parsed.netloc}"
                    f"/MediaPlayer.php"
                    f"?view_id={view_id}&clip_id={clip_id}"
                )

                try:
                    pr = requests.get(
                        player_url,
                        headers=HEADERS,
                        timeout=30,
                    )
                    pr.raise_for_status()

                    soup = BeautifulSoup(
                        pr.text,
                        "html.parser",
                    )

                    for tag in soup(
                        ["script", "style", "noscript", "svg"]
                    ):
                        tag.decompose()

                    player_text = soup.get_text(
                        "\n",
                        strip=True,
                    )

                    return re.sub(
                        r"\n{3,}",
                        "\n\n",
                        player_text,
                    ).strip()[:limit]

                except Exception:
                    pass

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
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()

        text = soup.get_text("\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()

        # Some Granicus AgendaViewer pages redirect into a tiny
        # OnBase shell containing navigation but no actual agenda.
        # In that case, use the matching official MediaPlayer page,
        # which exposes the meeting's indexed agenda items.
        parsed = urlparse(url)

        is_granicus_agenda = (
            "granicus.com" in parsed.netloc.lower()
            and parsed.path.lower().endswith("/agendaviewer.php")
        )

        looks_like_shell = (
            len(text) < 1000
            or (
                "OnBase Agenda Online" in text
                and "Agenda Packet" in text
            )
        )

        if is_granicus_agenda and looks_like_shell:
            q = parse_qs(parsed.query)

            view_id = (q.get("view_id") or [""])[0]
            clip_id = (q.get("clip_id") or [""])[0]

            if clip_id:
                player_url = (
                    f"{parsed.scheme}://{parsed.netloc}"
                    f"/MediaPlayer.php"
                    f"?view_id={view_id}&clip_id={clip_id}"
                )

                try:
                    pr = requests.get(
                        player_url,
                        headers=HEADERS,
                        timeout=30,
                        allow_redirects=True,
                    )
                    pr.raise_for_status()

                    psoup = BeautifulSoup(
                        pr.text,
                        "html.parser",
                    )

                    for tag in psoup(
                        ["script", "style", "noscript", "svg"]
                    ):
                        tag.decompose()

                    player_text = psoup.get_text(
                        "\n",
                        strip=True,
                    )

                    player_text = re.sub(
                        r"\n{3,}",
                        "\n\n",
                        player_text,
                    ).strip()

                    if len(player_text) > len(text):
                        text = player_text

                except Exception:
                    pass

        return text[:limit]
    except Exception as exc:
        return f"[Agenda HTML parse failed: {type(exc).__name__}: {exc}]"
