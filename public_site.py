from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse


BASE = Path(__file__).resolve().parent
PUBLISHED = BASE / "published"

CITY_FILTERS = [
    ("all", "All cities"),
    ("rsm", "Rancho Santa Margarita"),
    ("aliso-viejo", "Aliso Viejo"),
    ("mission-viejo", "Mission Viejo"),
    ("lake-forest", "Lake Forest"),
    ("laguna-niguel", "Laguna Niguel"),
]

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

.section-head{
    display:flex;
    align-items:flex-end;
    justify-content:space-between;
    gap:20px;
    margin-bottom:18px;
}

.section-title{
    margin:0;
    font-size:26px;
    line-height:1.1;
}

.story-count{
    font-family:Arial,sans-serif;
    color:#777168;
    font-size:12px;
    white-space:nowrap;
}

.filters{
    display:flex;
    flex-wrap:wrap;
    gap:8px;
    margin:0 0 14px;
}

.filter{
    display:inline-block;
    text-decoration:none;
    font-family:Arial,sans-serif;
    font-size:11px;
    color:#5f5a52;
    border:1px solid #c9c4ba;
    border-radius:999px;
    padding:7px 10px;
    background:#fcfbf7;
}

.filter:hover{
    border-color:#7d776d;
}

.filter.active{
    color:#fff;
    background:#1e1e1e;
    border-color:#1e1e1e;
}


.signup{
    margin-top:42px;
    padding:25px 0 28px;
    border-top:1px solid #bbb5aa;
    border-bottom:1px solid #bbb5aa;
}

.signup h2{
    margin:0 0 7px;
    font-size:22px;
}

.signup-copy{
    max-width:660px;
    margin:0 0 16px;
    font-size:16px;
    line-height:1.5;
    color:#4a4741;
}

.signup-form{
    display:flex;
    gap:8px;
    max-width:570px;
}

.signup-form input[type="email"]{
    flex:1;
    min-width:0;
    padding:11px 12px;
    border:1px solid #aaa49a;
    background:#fcfbf7;
    color:#181818;
    border-radius:4px;
    font:15px Arial,sans-serif;
}

.signup-form button{
    padding:11px 17px;
    border:1px solid #1e1e1e;
    border-radius:4px;
    background:#1e1e1e;
    color:#fff;
    font:700 14px Arial,sans-serif;
    cursor:pointer;
}

.signup-form button:hover{
    opacity:.82;
}

.signup-small{
    margin-top:9px;
    font:11px/1.4 Arial,sans-serif;
    color:#777168;
}

@media(max-width:600px){
    .signup-form{
        display:block;
    }

    .signup-form input[type="email"]{
        width:100%;
        margin-bottom:8px;
    }

    .signup-form button{
        width:100%;
    }
}

.about{
    margin-top:44px;
    padding-top:24px;
    border-top:1px solid #bbb5aa;
}

.about h2{
    margin:0 0 10px;
    font-size:22px;
}

.about p{
    max-width:720px;
    margin:0;
    font-size:16px;
    line-height:1.55;
    color:#4a4741;
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

/*
Only use story borders as separators BETWEEN stories.
The About section supplies the final divider.
*/
.card:last-of-type{
    border-bottom:0;
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


def filter_nav(active_slug):
    links = []

    for slug, label in CITY_FILTERS:
        cls = (
            "filter active"
            if slug == active_slug
            else "filter"
        )

        href = (
            "/"
            if slug == "all"
            else f"/?city={slug}"
        )

        links.append(
            f"""
            <a class="{cls}"
               href="{href}">
              {esc(label)}
            </a>
            """
        )

    return "".join(links)



def signup_panel():
    return """
    <section class="signup">
      <h2>Get CouncilWatch updates</h2>

      <p class="signup-copy">
        Get new South Orange County local-government
        coverage and CouncilWatch updates by email.
      </p>

      <form
        class="signup-form"
        action="https://buttondown.com/api/emails/embed-subscribe/CouncilWatch"
        method="post"
      >
        <input
          type="email"
          name="email"
          placeholder="Your email address"
          autocomplete="email"
          aria-label="Email address"
          required
        >

        <input
          type="hidden"
          name="embed"
          value="1"
        >

        <input
          type="hidden"
          name="utm_source"
          value="councilwatch"
        >

        <input
          type="hidden"
          name="utm_medium"
          value="website"
        >

        <input
          type="hidden"
          name="utm_campaign"
          value="site_signup"
        >

        <button type="submit">
          Subscribe
        </button>
      </form>

      <div class="signup-small">
        Subscribe with your email address.
      </div>
    </section>
    """


def about_panel():
    return """
    <section class="about">
      <h2>About CouncilWatch</h2>
      <p>
        CouncilWatch follows public meetings across five South
        Orange County cities and turns official agendas,
        recordings and meeting materials into concise local
        government coverage. Reports are reviewed before
        publication, with links back to official sources.
      </p>
    </section>
    """


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
def home(
    request: Request,
):
    all_articles = load_articles()

    valid_slugs = {
        slug
        for slug, _ in CITY_FILTERS
    }

    selected = str(
        request.query_params.get(
            "city",
            "all",
        )
    ).strip()

    if selected not in valid_slugs:
        selected = "all"

    if selected == "all":
        articles = all_articles
    else:
        articles = [
            article
            for article in all_articles
            if article.get(
                "city_slug"
            ) == selected
        ]

    count_label = (
        "1 published report"
        if len(articles) == 1
        else f"{len(articles)} published reports"
    )

    top = f"""
    <div class="section-head">
      <h1 class="section-title">
        Latest coverage
      </h1>

      <div class="story-count">
        {esc(count_label)}
      </div>
    </div>

    <nav class="filters"
         aria-label="Filter stories by city">
      {filter_nav(selected)}
    </nav>
    """

    if not articles:
        return shell(
            top
            + """
            <div class="empty">
              No published coverage for this city yet.
            </div>
            """
            + signup_panel()
            + about_panel()
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
        top
        + "".join(cards)
        + signup_panel()
        + about_panel()
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

      {signup_panel()}

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
