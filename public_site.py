from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse


BASE = Path(__file__).resolve().parent
PUBLISHED = BASE / "published"

app = FastAPI(
    title="CouncilWatch Public Site"
)


CSS = """
*{box-sizing:border-box}

body{
    margin:0;
    background:#f7f5ef;
    color:#181818;
    font-family:Georgia,"Times New Roman",serif;
}

header{
    background:#fcfbf7;
    border-bottom:1px solid #bbb5aa;
}

.wrap{
    width:min(940px,calc(100% - 32px));
    margin:0 auto;
}

header .wrap{
    padding:28px 0 22px;
}

.brand{
    font-size:36px;
    font-weight:700;
    letter-spacing:-.02em;
}

.tagline{
    margin-top:5px;
    font-family:Arial,sans-serif;
    color:#666;
    font-size:13px;
}

main{
    padding:28px 0 60px;
}

.card{
    display:block;
    text-decoration:none;
    color:inherit;
    padding:28px 0;
    border-bottom:1px solid #cbc6bc;
    transition:opacity .15s ease;
}

.card:hover{
    opacity:.72;
}

.city{
    font-family:Arial,sans-serif;
    text-transform:uppercase;
    letter-spacing:.1em;
    font-size:11px;
    font-weight:700;
    color:#68635b;
}

.date{
    font-family:Arial,sans-serif;
    color:#777168;
    font-size:12px;
    margin-top:5px;
}

.assisted{
    display:inline-block;
    margin-top:9px;
    font-family:Arial,sans-serif;
    text-transform:uppercase;
    letter-spacing:.08em;
    font-size:9px;
    font-weight:700;
    color:#777168;
    border:1px solid #c9c4ba;
    border-radius:999px;
    padding:4px 7px;
}

.headline{
    font-size:32px;
    line-height:1.07;
    margin:8px 0;
}

.dek{
    font-size:17px;
    line-height:1.45;
    color:#4a4741;
}

.story{
    width:min(760px,100%);
}

.story h1{
    font-size:clamp(38px,7vw,60px);
    line-height:1.02;
    letter-spacing:-.025em;
    margin:12px 0;
}

.story .dek{
    font-size:21px;
    line-height:1.42;
    margin:15px 0 20px;
}

.meta{
    border-top:1px solid #bbb5aa;
    border-bottom:1px solid #bbb5aa;
    padding:10px 0;
    margin-bottom:28px;
    font-family:Arial,sans-serif;
    color:#666;
    font-size:12px;
}

.story p{
    font-size:19px;
    line-height:1.72;
    margin:0 0 22px;
}

.panel{
    border-top:1px solid #bbb5aa;
    margin-top:32px;
    padding-top:18px;
}

.panel h2{
    font-family:Arial,sans-serif;
    text-transform:uppercase;
    letter-spacing:.08em;
    font-size:12px;
}

.panel li{
    margin:8px 0;
    line-height:1.45;
}

.sources a{
    display:block;
    margin:8px 0;
    overflow-wrap:anywhere;
}

.note{
    margin-top:32px;
    padding-top:16px;
    border-top:1px solid #bbb5aa;
    font-family:Arial,sans-serif;
    color:#666;
    font-size:12px;
    line-height:1.45;
}

.back{
    font-family:Arial,sans-serif;
    font-size:13px;
    color:#555;
}

.empty{
    padding:50px 0;
    color:#666;
}
"""


def esc(value):
    return html.escape(
        str(value or "")
    )


def format_date(value):
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


def safe_url(value):
    value = str(value or "").strip()

    try:
        parsed = urlparse(value)
    except Exception:
        return ""

    if parsed.scheme not in {
        "http",
        "https",
    }:
        return ""

    return value


def read_json(path):
    return json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )


def load_articles():
    rows = []

    if not PUBLISHED.exists():
        return rows

    for path in PUBLISHED.glob("*.json"):
        try:
            data = read_json(path)
        except Exception:
            continue

        if not isinstance(data, dict):
            continue

        article_id = str(
            data.get("article_id") or ""
        ).strip()

        headline = str(
            data.get("headline") or ""
        ).strip()

        if not article_id or not headline:
            continue

        rows.append(data)

    rows.sort(
        key=lambda d: (
            d.get("published_at_utc")
            or d.get("meeting_date")
            or ""
        ),
        reverse=True,
    )

    return rows


def find_article(article_id):
    for article in load_articles():
        if str(
            article.get("article_id")
        ) == str(article_id):
            return article

    return None


def shell(
    content,
    title="CouncilWatch",
):
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport"
      content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<style>{CSS}</style>
</head>

<body>

<header>
  <div class="wrap">
    <div class="brand">
      CouncilWatch
    </div>

    <div class="tagline">
      Independent local government coverage
      from South Orange County
    </div>
  </div>
</header>

<main>
  <div class="wrap">
    {content}
  </div>
</main>

</body>
</html>"""
    )


@app.get(
    "/",
    response_class=HTMLResponse,
)
def home():
    articles = load_articles()

    if not articles:
        return shell(
            """
            <div class="empty">
              No articles have been published yet.
            </div>
            """
        )

    cards = []

    for article in articles:
        article_id = esc(
            article.get("article_id")
        )

        cards.append(
            f"""
            <a class="card"
               href="/article/{article_id}">

              <div class="city">
                {esc(article.get("city_name"))}
              </div>

              <div class="date">
                {esc(format_date(article.get("meeting_date")))}
              </div>

              {
                '<div class="assisted">Technology-assisted</div>'
                if article.get("technology_assisted")
                else ""
              }

              <div class="headline">
                {esc(article.get("headline"))}
              </div>

              <div class="dek">
                {esc(article.get("dek"))}
              </div>

            </a>
            """
        )

    return shell(
        "".join(cards)
    )


@app.get(
    "/article/{article_id}",
    response_class=HTMLResponse,
)
def article_page(
    article_id: str,
):
    article = find_article(
        article_id
    )

    if not article:
        return HTMLResponse(
            "Article not found",
            status_code=404,
        )

    body = "".join(
        f"<p>{esc(paragraph)}</p>"
        for paragraph
        in article.get(
            "body",
            [],
        )
    )

    facts = "".join(
        f"<li>{esc(item)}</li>"
        for item
        in article.get(
            "key_facts",
            [],
        )
    )

    source_links = []

    for label, key in [
        (
            "Official meeting source",
            "source_url",
        ),
        (
            "Official agenda",
            "agenda_url",
        ),
        (
            "Official recording",
            "recording_url",
        ),
    ]:
        url = safe_url(
            article.get(key)
        )

        if url:
            source_links.append(
                f"""
                <a href="{esc(url)}"
                   target="_blank"
                   rel="noopener noreferrer">
                  {esc(label)} →
                </a>
                """
            )

    technology_note = ""

    if article.get(
        "technology_assisted"
    ):
        technology_note = """
        <div class="note">
          This report was prepared using
          technology-assisted analysis of official
          public meeting materials and was reviewed
          before publication.
        </div>
        """

    content = f"""
    <div class="story">

      <a class="back" href="/">
        ← CouncilWatch
      </a>

      <div class="city"
           style="margin-top:25px">
        {esc(article.get("city_name"))}
      </div>

      {
        '<div class="assisted">Technology-assisted</div>'
        if article.get("technology_assisted")
        else ""
      }

      <h1>
        {esc(article.get("headline"))}
      </h1>

      <div class="dek">
        {esc(article.get("dek"))}
      </div>

      <div class="meta">
        {esc(format_date(article.get("meeting_date")))}
        · Local government coverage
      </div>

      {body}

      <div class="panel">
        <h2>Key facts</h2>
        <ul>
          {
            facts
            or "<li>No additional key facts.</li>"
          }
        </ul>
      </div>

      <div class="panel sources">
        <h2>Official sources</h2>
        {
            "".join(source_links)
            or "No source links available."
        }
      </div>

      {technology_note}

    </div>
    """

    return shell(
        content,
        article.get("headline")
        or "CouncilWatch",
    )


@app.get("/health")
def health():
    articles = load_articles()

    return JSONResponse(
        {
            "ok": True,
            "published_articles":
                len(articles),
        }
    )
