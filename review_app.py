from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from agenda import agenda_text
from gemini_worker import StoryDraft, audit_story
from meeting_intelligence import audit_verification_context
from newsletter import ensure_buttondown_draft
from publishing import (
    publish_copy,
    remove_published_copy,
    published_path,
)
from settings import DRAFTS, STATUS_FILE, CITY_NAMES


app = FastAPI(title="CouncilWatch Private Review")

REVISION_DIR = DRAFTS / "_revisions"
REVISION_DIR.mkdir(parents=True, exist_ok=True)


CSS = """
:root{color-scheme:light dark}
*{box-sizing:border-box}
body{margin:0;font-family:Inter,ui-sans-serif,system-ui,-apple-system,sans-serif;background:#f5f3ee;color:#171717}
a{color:inherit}
.wrap{width:min(1120px,calc(100% - 32px));margin:0 auto}
header{padding:34px 0 24px;border-bottom:1px solid #cbc6bb;background:#faf8f3}
.brand{font-family:Georgia,serif;font-size:31px;font-weight:700}
.kicker{text-transform:uppercase;letter-spacing:.13em;font-size:11px;font-weight:750;color:#6b665d;margin-bottom:8px}
.sub{color:#625e56;max-width:760px;line-height:1.55}
main{padding:28px 0 60px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(285px,1fr));gap:16px}
.card{background:#fff;border:1px solid #d9d5cc;border-radius:12px;padding:20px;text-decoration:none;display:block}
.city{font-size:12px;letter-spacing:.08em;text-transform:uppercase;font-weight:800;color:#6b665d}
.date{font-size:13px;color:#777168;margin-top:5px}
.headline{font-family:Georgia,serif;font-size:23px;line-height:1.12;margin:12px 0 9px}
.dek{line-height:1.45;color:#4b4842}
.badge{display:inline-block;border:1px solid #aaa49a;border-radius:999px;padding:4px 8px;font-size:10px;font-weight:800;text-transform:uppercase;margin-top:14px}
.badge.approved{border-color:#39915a}
.badge.needs_fix{border-color:#d28d28}
.badge.rejected{border-color:#b94f4f}
.badge.needs_review{border-color:#527aa8}
.processing{opacity:.75}
.fail{border-color:#b96d6d}
.story{max-width:800px}
.story h1{font-family:Georgia,serif;font-size:clamp(36px,6vw,58px);line-height:1.02;margin:12px 0}
.story .dek{font-family:Georgia,serif;font-size:21px;line-height:1.35;margin-bottom:18px}
.meta{font-size:13px;color:#6f6a62;border-top:1px solid #cbc6bb;border-bottom:1px solid #cbc6bb;padding:11px 0;margin-bottom:26px}
.story p{font-family:Georgia,serif;font-size:19px;line-height:1.7;margin:0 0 21px}
.panel{margin-top:34px;padding:18px;border:1px solid #d1ccc2;background:#fff;border-radius:10px}
.panel h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em}
.panel li{margin:8px 0;line-height:1.45}
.sources a{display:block;margin:7px 0;overflow-wrap:anywhere}
.back{display:inline-block;margin-bottom:22px}
.note{background:#ede9df;padding:12px 14px;border-radius:8px;margin:12px 0;color:#524e47;font-size:13px}
.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
button,.button{font:inherit;font-weight:700;border:1px solid #aaa49a;background:#fff;color:#171717;border-radius:8px;padding:10px 14px;cursor:pointer;text-decoration:none;display:inline-block}
button.primary{background:#171717;color:#fff}
button.warn{border-color:#c98928}
button.danger{border-color:#b94f4f}
textarea,input{width:100%;font:inherit;border:1px solid #aaa49a;border-radius:8px;padding:10px;background:#fff;color:#171717}
textarea{min-height:110px;resize:vertical}
.edit-label{display:block;font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.06em;margin:20px 0 6px}
.edit-body{min-height:420px}
.queue-stats{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 18px}
.stat{background:#fff;border:1px solid #d9d5cc;border-radius:8px;padding:8px 11px;font-size:12px}
.review-note{white-space:pre-wrap}
.audit-stale{font-weight:800}
@media(prefers-color-scheme:dark){
body{background:#151515;color:#eee}
.card,.panel,.stat{background:#1e1e1e;border-color:#454545}
header{background:#191919;border-color:#444}
.sub,.dek,.date,.city,.meta,.back,.note{color:#bbb}
.note{background:#252525}
button,.button,textarea,input{background:#202020;color:#eee;border-color:#555}
button.primary{background:#eee;color:#111}
}
"""


def esc(x):
    return html.escape(str(x or ""))


def now():
    return datetime.now(timezone.utc).isoformat()


def short_message(value, limit=180):
    text = str(value or "")
    if len(text) <= limit:
        return text
    first = text.split(" | ")[0]
    if len(first) > limit:
        first = first[:limit - 1] + "…"
    return first


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, data):
    path.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def load_status():
    if STATUS_FILE.exists():
        try:
            return json.loads(
                STATUS_FILE.read_text(
                    encoding="utf-8"
                )
            )
        except Exception:
            pass
    return {"cities": {}}


def is_current_draft(path, data):
    slug = str(data.get("city_slug") or "")
    external_id = str(data.get("external_id") or "")

    if not slug or not external_id:
        return False

    # A real current draft must have exactly:
    # city-slug--external-id.json
    # This automatically excludes our *.before-*.json backups.
    return path.name == f"{slug}--{external_id}.json"


def load_draft_rows():
    rows = []

    for p in DRAFTS.glob("*.json"):
        try:
            data = read_json(p)
        except Exception:
            continue

        # drafts/ also contains intelligence/test/support JSON.
        # Only story objects belong in the review queue.
        if not isinstance(data, dict):
            continue

        if not is_current_draft(p, data):
            continue

        rows.append((p, data))

    city_order = {
        slug: i
        for i, slug in enumerate(CITY_NAMES)
    }

    rows.sort(
        key=lambda row: (
            row[1].get("meeting_date") or "",
            -city_order.get(
                row[1].get("city_slug"),
                99,
            ),
        ),
        reverse=True,
    )

    return rows


def load_drafts():
    return [
        data
        for _, data in load_draft_rows()
    ]


def find_draft(slug, external_id):
    for path, data in load_draft_rows():
        if (
            data.get("city_slug") == slug
            and str(data.get("external_id"))
            == str(external_id)
        ):
            return path, data

    return None, None


def review_status(data):
    return data.get(
        "review_status",
        "needs_review",
    )


def review_label(status):
    return {
        "needs_review": "Needs review",
        "approved": "Approved",
        "needs_fix": "Needs fix",
        "rejected": "Rejected",
    }.get(status, status.replace("_", " ").title())


def backup_revision(path, data):
    revision = int(data.get("revision", 1))

    stamp = datetime.now(
        timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")

    backup = REVISION_DIR / (
        f"{data['city_slug']}--"
        f"{data['external_id']}--"
        f"rev{revision:03d}--"
        f"{stamp}.json"
    )

    write_json(
        backup,
        data,
    )

    return backup


def shell(content, title="CouncilWatch Private Review"):
    return HTMLResponse(
        f'''<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<style>{CSS}</style>
</head>
<body>
<header>
<div class="wrap">
<div class="kicker">Private draft review</div>
<div class="brand">CouncilWatch</div>
<div class="sub">
Five-city pilot. Nothing shown here is published.
Approve, edit, reject or send drafts back for fixes.
</div>
</div>
</header>
<main>
<div class="wrap">{content}</div>
</main>
</body>
</html>'''
    )


@app.get("/", response_class=HTMLResponse)
def home():
    drafts = load_drafts()
    status = load_status().get("cities", {})

    counts = {
        "needs_review": 0,
        "approved": 0,
        "needs_fix": 0,
        "rejected": 0,
    }

    for d in drafts:
        s = review_status(d)
        counts[s] = counts.get(s, 0) + 1

    stats = f'''
    <div class="queue-stats">
      <div class="stat"><strong>{counts["needs_review"]}</strong> needs review</div>
      <div class="stat"><strong>{counts["approved"]}</strong> approved</div>
      <div class="stat"><strong>{counts["needs_fix"]}</strong> needs fix</div>
      <div class="stat"><strong>{counts["rejected"]}</strong> rejected</div>
    </div>
    '''

    cards = []

    for d in drafts:
        slug = d["city_slug"]
        external_id = d["external_id"]
        rs = review_status(d)

        note = d.get("review_note")
        note_html = (
            f'<div class="note">{esc(note)}</div>'
            if note else ""
        )

        cards.append(
            f'''
            <a class="card"
               href="/story/{esc(slug)}/{esc(external_id)}">
              <div class="city">{esc(d.get("city_name"))}</div>
              <div class="date">{esc(d.get("meeting_date"))}</div>
              <div class="headline">{esc(d.get("headline"))}</div>
              <div class="dek">{esc(d.get("dek"))}</div>
              {note_html}
              <div class="badge {esc(rs)}">
                {esc(review_label(rs))}
              </div>
            </a>
            '''
        )

    cities_with_drafts = {
        d["city_slug"]
        for d in drafts
    }

    for slug, city in CITY_NAMES.items():
        if slug in cities_with_drafts:
            continue

        st = status.get(slug, {})
        phase = st.get(
            "phase",
            "waiting",
        )

        msg = short_message(
            st.get(
                "message",
                "Waiting for generator.",
            )
        )

        cls = (
            "card processing"
            + (
                " fail"
                if phase == "failed"
                else ""
            )
        )

        cards.append(
            f'''
            <div class="{cls}">
              <div class="city">{esc(city)}</div>
              <div class="headline">
                {esc(phase.replace("_", " ").title())}
              </div>
              <div class="dek">{esc(msg)}</div>
              <div class="badge">{esc(phase)}</div>
            </div>
            '''
        )

    content = (
        '<div class="note">'
        'This is the private review queue. '
        'Approved does not mean published.'
        '</div>'
        + stats
        + '<div class="grid">'
        + "".join(cards)
        + "</div>"
    )

    return shell(content)


@app.get(
    "/story/{slug}/{external_id}",
    response_class=HTMLResponse,
)
def story(slug: str, external_id: str):
    _, target = find_draft(
        slug,
        external_id,
    )

    if not target:
        return HTMLResponse(
            "Draft not found",
            status_code=404,
        )

    paras = "".join(
        f"<p>{esc(p)}</p>"
        for p in target.get("body", [])
    )

    facts = "".join(
        f"<li>{esc(x)}</li>"
        for x in target.get(
            "key_facts",
            [],
        )
    )

    notes = "".join(
        f"<li>{esc(x)}</li>"
        for x in target.get(
            "verification_notes",
            [],
        )
    )

    entity_rows = []

    for item in target.get(
        "entity_verification",
        [],
    ):
        if not isinstance(item, dict):
            continue

        status = esc(
            item.get(
                "status",
                "UNVERIFIED",
            )
        )

        observed = esc(
            item.get(
                "observed_text",
                "",
            )
        )

        canonical = esc(
            item.get(
                "canonical_text",
                observed,
            )
        )

        evidence = esc(
            item.get(
                "evidence",
                "",
            )
        )

        source = item.get(
            "official_source_url",
            "",
        )

        if (
            observed
            and canonical
            and observed != canonical
        ):
            label = (
                f"{canonical} "
                f"(source notes: “{observed}”)"
            )
        else:
            label = canonical or observed

        source_html = ""

        if source:
            source_html = (
                f'<br><a href="{esc(source)}" '
                f'target="_blank" '
                f'rel="noreferrer">'
                f'Official verification source -&gt;'
                f'</a>'
            )

        entity_rows.append(
            f"<li>"
            f"<strong>{status} · {label}</strong>"
            f"<br>{evidence}"
            f"{source_html}"
            f"</li>"
        )

    entities_html = "".join(
        entity_rows
    )


    coverage_rows = []

    for item in target.get(
        "coverage_plan",
        [],
    ):
        if not isinstance(item, dict):
            continue

        must = (
            " · MUST INCLUDE"
            if item.get(
                "must_include"
            )
            else ""
        )

        coverage_rows.append(
            f"<li>"
            f"<strong>"
            f"#{esc(item.get('rank'))} · "
            f"{esc(item.get('score'))}/10"
            f"{must} · "
            f"{esc(item.get('topic'))}"
            f"</strong>"
            f"<br>Status: "
            f"{esc(item.get('action_status'))}"
            f"<br>{esc(item.get('summary'))}"
            f"<br><em>Why it matters:</em> "
            f"{esc(item.get('why_it_matters'))}"
            f"</li>"
        )

    coverage_html = "".join(
        coverage_rows
    )


    issue_rows = []

    for item in target.get(
        "audit_issues",
        [],
    ):
        if isinstance(item, dict):
            sev = esc(
                item.get(
                    "severity",
                    "issue",
                ).upper()
            )

            field = esc(
                item.get(
                    "field",
                    "draft",
                )
            )

            draft_text = esc(
                item.get(
                    "draft_text",
                    "",
                )
            )

            evidence = esc(
                item.get(
                    "source_evidence",
                    "",
                )
            )

            correction = esc(
                item.get(
                    "correction",
                    "",
                )
            )

            issue_rows.append(
                f"<li>"
                f"<strong>{sev} · {field}</strong><br>"
                f"Draft: “{draft_text}”<br>"
                f"Evidence: {evidence}<br>"
                f"Fix: {correction}"
                f"</li>"
            )
        else:
            issue_rows.append(
                f"<li>{esc(item)}</li>"
            )

    issues = "".join(issue_rows)

    links = []

    for label, key in [
        ("Official source", "source_url"),
        ("Agenda", "agenda_url"),
        ("Recording", "recording_url"),
    ]:
        url = target.get(key)

        if url:
            links.append(
                f'<a href="{esc(url)}" '
                f'target="_blank" '
                f'rel="noreferrer">'
                f'{esc(label)} -&gt;</a>'
            )

    audit_ok = target.get("audit_ok")

    if audit_ok is True:
        audit_label = "YES"
    elif target.get("audit_status") == "stale_after_manual_edit":
        audit_label = (
            "STALE — article was edited "
            "after the last audit"
        )
    else:
        audit_label = "NO"

    rs = review_status(target)

    is_published = bool(
        target.get("published")
    )

    if is_published:
        publication_label = "Published locally"

        publish_controls = """
        <button class="danger"
                onclick="unpublishStory()">
          Unpublish
        </button>
        """

    elif (
        rs == "approved"
        and audit_ok is True
    ):
        publication_label = (
            "Approved · ready to publish"
        )

        publish_controls = """
        <button class="primary"
                onclick="publishStory()">
          Publish locally
        </button>
        """

    else:
        publication_label = "Not published"
        publish_controls = ""

    newsletter_id = str(
        target.get(
            "newsletter_draft_id"
        )
        or ""
    ).strip()

    newsletter_error = str(
        target.get(
            "newsletter_draft_error"
        )
        or ""
    ).strip()

    newsletter_revision = int(
        target.get(
            "newsletter_draft_revision",
            0,
        )
        or 0
    )

    current_revision = int(
        target.get(
            "revision",
            1,
        )
        or 1
    )

    if (
        newsletter_id
        and newsletter_revision
        == current_revision
    ):
        newsletter_label = (
            "Newsletter draft ready"
        )

    elif newsletter_error:
        newsletter_label = (
            "Newsletter draft failed"
        )

    elif is_published:
        newsletter_label = (
            "Newsletter draft pending"
        )

    else:
        newsletter_label = (
            "Newsletter not created"
        )

    current_note = target.get(
        "review_note",
        "",
    )

    review_panel = f'''
    <div class="panel">
      <h2>Review decision</h2>

      <div>
        Current status:
        <strong>{esc(review_label(rs))}</strong>
      </div>

      <div style="margin-top:6px">
        Newsletter:
        <strong>{esc(newsletter_label)}</strong>
      </div>

      <div style="margin-top:12px">
        <textarea
          id="review-note"
          placeholder="Optional review note. For Needs Fix, describe what should change."
        >{esc(current_note)}</textarea>
      </div>

      <div class="actions">
        <button class="primary"
                onclick="setReview('approved')">
          Approve
        </button>

        <button class="warn"
                onclick="setReview('needs_fix')">
          Needs Fix
        </button>

        <button class="danger"
                onclick="setReview('rejected')">
          Reject
        </button>

        <a class="button"
           href="/edit/{esc(slug)}/{esc(external_id)}">
          Edit
        </a>

        <button onclick="reaudit()">
          Re-audit
        </button>

        {publish_controls}
      </div>

      <div id="action-message"
           class="note"
           style="display:none"></div>
    </div>
    '''

    script = """
<script>
const SLUG = %s;
const EXTERNAL_ID = %s;

async function setReview(status) {
    const note = document.getElementById(
        "review-note"
    ).value;

    const r = await fetch(
        `/api/review/${SLUG}/${EXTERNAL_ID}`,
        {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                status: status,
                note: note
            })
        }
    );

    const data = await r.json();

    if (!r.ok) {
        const box = document.getElementById(
            "action-message"
        );
        box.style.display = "block";
        box.textContent = data.error || "Action failed.";
        return;
    }

    location.reload();
}

async function reaudit() {
    const box = document.getElementById(
        "action-message"
    );

    box.style.display = "block";
    box.textContent = "Re-auditing this exact revision...";

    const r = await fetch(
        `/api/reaudit/${SLUG}/${EXTERNAL_ID}`,
        {
            method: "POST"
        }
    );

    const data = await r.json();

    if (!r.ok) {
        box.textContent = data.error || "Re-audit failed.";
        return;
    }

    location.reload();
}


async function publishStory() {
    const box = document.getElementById(
        "action-message"
    );

    box.style.display = "block";
    box.textContent =
        "Publishing approved revision locally...";

    const r = await fetch(
        `/api/publish/${SLUG}/${EXTERNAL_ID}`,
        {
            method: "POST"
        }
    );

    const data = await r.json();

    if (!r.ok) {
        box.textContent =
            data.error || "Publish failed.";
        return;
    }

    location.reload();
}


async function unpublishStory() {
    if (!confirm(
        "Remove this article from the local published store?"
    )) {
        return;
    }

    const box = document.getElementById(
        "action-message"
    );

    box.style.display = "block";
    box.textContent = "Unpublishing...";

    const r = await fetch(
        `/api/unpublish/${SLUG}/${EXTERNAL_ID}`,
        {
            method: "POST"
        }
    );

    const data = await r.json();

    if (!r.ok) {
        box.textContent =
            data.error || "Unpublish failed.";
        return;
    }

    location.reload();
}
</script>
""" % (
        json.dumps(slug),
        json.dumps(str(external_id)),
    )

    content = f'''
    <div class="story">

      <a class="back" href="/">
        &larr; Review queue
      </a>

      <div class="city">
        {esc(target.get("city_name"))}
      </div>

      <h1>{esc(target.get("headline"))}</h1>

      <div class="dek">
        {esc(target.get("dek"))}
      </div>

      <div class="meta">
        {esc(target.get("meeting_date"))}
        ·
        {esc(target.get("meeting_title"))}
        ·
        {esc(review_label(rs))}
        ·
        {esc(publication_label)}
      </div>

      {review_panel}

      {paras}

      <div class="panel">
        <h2>Key facts</h2>
        <ul>
          {facts or "<li>None supplied.</li>"}
        </ul>
      </div>

      <div class="panel">
        <h2>Verification notes</h2>
        <ul>
          {notes or "<li>No extra notes.</li>"}
        </ul>
      </div>

      <div class="panel">
        <h2>Names & places — official-source verification</h2>
        <ul>
          {entities_html or "<li>No entity verification stored for this revision.</li>"}
        </ul>
      </div>

      <div class="panel">
        <h2>Whole-meeting coverage plan</h2>
        <ul>
          {coverage_html or "<li>No coverage plan stored for this revision.</li>"}
        </ul>
      </div>

      <div class="panel">
        <h2>Audit</h2>
        <div>
          Audit passed:
          <strong>{esc(audit_label)}</strong>
        </div>
        <ul>
          {issues or "<li>No material issues flagged.</li>"}
        </ul>
      </div>

      <div class="panel sources">
        <h2>Official sources</h2>
        {"".join(links)}
      </div>

    </div>

    {script}
    '''

    return shell(
        content,
        target.get("headline")
        or "CouncilWatch Draft",
    )


@app.get(
    "/edit/{slug}/{external_id}",
    response_class=HTMLResponse,
)
def edit_story(slug: str, external_id: str):
    _, target = find_draft(
        slug,
        external_id,
    )

    if not target:
        return HTMLResponse(
            "Draft not found",
            status_code=404,
        )

    body_text = "\n\n".join(
        target.get(
            "body",
            [],
        )
    )

    facts_text = "\n".join(
        target.get(
            "key_facts",
            [],
        )
    )

    notes_text = "\n".join(
        target.get(
            "verification_notes",
            [],
        )
    )

    script = """
<script>
const SLUG = %s;
const EXTERNAL_ID = %s;

async function saveStory() {
    const body = document
        .getElementById("body")
        .value
        .split(/\\n\\s*\\n/)
        .map(x => x.trim())
        .filter(Boolean);

    const keyFacts = document
        .getElementById("facts")
        .value
        .split("\\n")
        .map(x => x.trim())
        .filter(Boolean);

    const verificationNotes = document
        .getElementById("notes")
        .value
        .split("\\n")
        .map(x => x.trim())
        .filter(Boolean);

    const payload = {
        headline: document
            .getElementById("headline")
            .value.trim(),
        dek: document
            .getElementById("dek")
            .value.trim(),
        body: body,
        key_facts: keyFacts,
        verification_notes: verificationNotes
    };

    const r = await fetch(
        `/api/story/${SLUG}/${EXTERNAL_ID}/save`,
        {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify(payload)
        }
    );

    const data = await r.json();

    if (!r.ok) {
        document.getElementById(
            "save-message"
        ).textContent =
            data.error || "Save failed.";
        return;
    }

    location.href =
        `/story/${SLUG}/${EXTERNAL_ID}`;
}
</script>
""" % (
        json.dumps(slug),
        json.dumps(str(external_id)),
    )

    content = f'''
    <div class="story">

      <a class="back"
         href="/story/{esc(slug)}/{esc(external_id)}">
         &larr; Cancel edit
      </a>

      <div class="city">
        {esc(target.get("city_name"))}
      </div>

      <h1>Edit draft</h1>

      <div class="note">
        Saving creates a revision.
        Any previous approval is removed,
        and the audit becomes stale until you run Re-audit.
      </div>

      <label class="edit-label">
        Headline
      </label>
      <input id="headline"
             value="{esc(target.get("headline"))}">

      <label class="edit-label">
        Dek
      </label>
      <textarea id="dek">{esc(target.get("dek"))}</textarea>

      <label class="edit-label">
        Article
      </label>
      <textarea id="body"
                class="edit-body">{esc(body_text)}</textarea>

      <label class="edit-label">
        Key facts — one per line
      </label>
      <textarea id="facts">{esc(facts_text)}</textarea>

      <label class="edit-label">
        Verification notes — one per line
      </label>
      <textarea id="notes">{esc(notes_text)}</textarea>

      <div class="actions">
        <button class="primary"
                onclick="saveStory()">
          Save new revision
        </button>

        <a class="button"
           href="/story/{esc(slug)}/{esc(external_id)}">
          Cancel
        </a>
      </div>

      <div id="save-message"
           class="note"></div>

    </div>

    {script}
    '''

    return shell(
        content,
        "Edit CouncilWatch Draft",
    )


@app.post(
    "/api/review/{slug}/{external_id}"
)
async def review_action(
    slug: str,
    external_id: str,
    request: Request,
):
    path, target = find_draft(
        slug,
        external_id,
    )

    if not target:
        return JSONResponse(
            {
                "ok": False,
                "error": "Draft not found.",
            },
            status_code=404,
        )

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    status = str(
        payload.get(
            "status",
            "",
        )
    )

    note = str(
        payload.get(
            "note",
            "",
        )
    ).strip()

    allowed = {
        "needs_review",
        "approved",
        "needs_fix",
        "rejected",
    }

    if status not in allowed:
        return JSONResponse(
            {
                "ok": False,
                "error": "Invalid review status.",
            },
            status_code=400,
        )

    if (
        status == "approved"
        and target.get("audit_ok") is not True
    ):
        return JSONResponse(
            {
                "ok": False,
                "error": (
                    "This exact revision does not have "
                    "a passing audit. Click Re-audit first."
                ),
            },
            status_code=409,
        )

    backup_revision(
        path,
        target,
    )

    target["review_status"] = status
    target["review_note"] = note
    target["review_updated_at"] = now()

    if status == "approved":
        target["approved_at"] = now()
        target["rejected_at"] = None

    else:
        target["approved_at"] = None

        if status == "rejected":
            target["rejected_at"] = now()

        if target.get("published"):
            remove_published_copy(
                target
            )

            target["published"] = False
            target["published_at"] = None
            target["unpublished_at"] = now()

    write_json(
        path,
        target,
    )

    return {
        "ok": True,
        "review_status": status,
    }


@app.post(
    "/api/story/{slug}/{external_id}/save"
)
async def save_story(
    slug: str,
    external_id: str,
    request: Request,
):
    path, target = find_draft(
        slug,
        external_id,
    )

    if not target:
        return JSONResponse(
            {
                "ok": False,
                "error": "Draft not found.",
            },
            status_code=404,
        )

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            {
                "ok": False,
                "error": "Invalid edit payload.",
            },
            status_code=400,
        )

    headline = str(
        payload.get(
            "headline",
            "",
        )
    ).strip()

    dek = str(
        payload.get(
            "dek",
            "",
        )
    ).strip()

    body = payload.get(
        "body",
        [],
    )

    key_facts = payload.get(
        "key_facts",
        [],
    )

    verification_notes = payload.get(
        "verification_notes",
        [],
    )

    if not headline:
        return JSONResponse(
            {
                "ok": False,
                "error": "Headline cannot be blank.",
            },
            status_code=400,
        )

    if (
        not isinstance(body, list)
        or not body
    ):
        return JSONResponse(
            {
                "ok": False,
                "error": "Article body cannot be blank.",
            },
            status_code=400,
        )

    backup_revision(
        path,
        target,
    )

    target["headline"] = headline
    target["dek"] = dek

    target["body"] = [
        str(x).strip()
        for x in body
        if str(x).strip()
    ]

    target["key_facts"] = [
        str(x).strip()
        for x in key_facts
        if str(x).strip()
    ]

    target["verification_notes"] = [
        str(x).strip()
        for x in verification_notes
        if str(x).strip()
    ]

    target["revision"] = (
        int(
            target.get(
                "revision",
                1,
            )
        )
        + 1
    )

    target["manually_edited"] = True
    target["last_manual_edit_at"] = now()

    # Any edit invalidates approval and the old audit.
    target["review_status"] = "needs_review"
    target["review_note"] = (
        "Manually edited. Re-audit this revision before approval."
    )

    target["approved_at"] = None

    target["audit_ok"] = False
    target["audit_issues"] = []
    target["audit_status"] = (
        "stale_after_manual_edit"
    )

    if target.get("published"):
        remove_published_copy(
            target
        )

        target["unpublished_at"] = now()

    target["published"] = False
    target["published_at"] = None

    write_json(
        path,
        target,
    )

    return {
        "ok": True,
        "revision": target["revision"],
    }


@app.post(
    "/api/publish/{slug}/{external_id}"
)
def publish_story(
    slug: str,
    external_id: str,
):
    path, target = find_draft(
        slug,
        external_id,
    )

    if not target:
        return JSONResponse(
            {
                "ok": False,
                "error": "Draft not found.",
            },
            status_code=404,
        )

    if target.get("audit_ok") is not True:
        return JSONResponse(
            {
                "ok": False,
                "error": (
                    "Publish blocked: this exact revision "
                    "does not have a passing audit."
                ),
            },
            status_code=409,
        )

    if review_status(
        target
    ) != "approved":
        return JSONResponse(
            {
                "ok": False,
                "error": (
                    "Publish blocked: approve this exact "
                    "revision first."
                ),
            },
            status_code=409,
        )

    backup_revision(
        path,
        target,
    )

    stamp = now()

    target["published"] = True
    target["published_at"] = stamp
    target["last_published_at"] = stamp
    target["unpublished_at"] = None
    target["published_revision"] = int(
        target.get(
            "revision",
            1,
        )
    )

    public_path = publish_copy(
        target
    )

    # Newsletter drafting is deliberately non-blocking.
    # A Buttondown outage must never undo or prevent
    # an otherwise valid CouncilWatch publication.
    try:
        newsletter_result = ensure_buttondown_draft(
            target
        )

    except Exception as exc:
        newsletter_result = {
            "ok": False,
            "created": False,
            "updated": False,
            "error": str(exc),
        }

        target[
            "newsletter_draft_error"
        ] = str(exc)

        target[
            "newsletter_draft_last_attempt_at"
        ] = now()

    write_json(
        path,
        target,
    )

    return {
        "ok": True,
        "published": True,
        "published_at": stamp,
        "published_revision":
            target["published_revision"],
        "local_path":
            str(public_path),
        "newsletter":
            newsletter_result,
    }


@app.post(
    "/api/unpublish/{slug}/{external_id}"
)
def unpublish_story(
    slug: str,
    external_id: str,
):
    path, target = find_draft(
        slug,
        external_id,
    )

    if not target:
        return JSONResponse(
            {
                "ok": False,
                "error": "Draft not found.",
            },
            status_code=404,
        )

    backup_revision(
        path,
        target,
    )

    removed = remove_published_copy(
        target
    )

    stamp = now()

    target["published"] = False
    target["published_at"] = None
    target["unpublished_at"] = stamp

    write_json(
        path,
        target,
    )

    return {
        "ok": True,
        "published": False,
        "unpublished_at": stamp,
        "removed_path":
            str(removed),
    }


@app.post(
    "/api/reaudit/{slug}/{external_id}"
)
def reaudit_story(
    slug: str,
    external_id: str,
):
    path, target = find_draft(
        slug,
        external_id,
    )

    if not target:
        return JSONResponse(
            {
                "ok": False,
                "error": "Draft not found.",
            },
            status_code=404,
        )

    notes_path = DRAFTS / (
        f"{slug}--{external_id}.notes.txt"
    )

    if not notes_path.exists():
        return JSONResponse(
            {
                "ok": False,
                "error": (
                    "Source notes are missing, so this "
                    "draft cannot be re-audited."
                ),
            },
            status_code=400,
        )

    notes = notes_path.read_text(
        encoding="utf-8"
    )

    # Match the production processor's evidence boundary:
    # source notes + deterministic identity/spelling context.
    # Never feed coverage-plan/editorial reasoning into audit.
    intelligence_path = DRAFTS / (
        f"{slug}--{external_id}.intelligence.json"
    )

    intelligence = {}

    if intelligence_path.exists():
        try:
            loaded = read_json(
                intelligence_path
            )

            if isinstance(loaded, dict):
                intelligence = loaded
        except Exception:
            intelligence = {}

    audit_notes = notes

    if intelligence:
        audit_notes += (
            "\n\n"
            + audit_verification_context(
                intelligence
            )
        )

    agenda = ""

    agenda_url = target.get(
        "agenda_url"
    )

    if agenda_url:
        try:
            agenda = agenda_text(
                agenda_url
            )
        except Exception:
            agenda = ""

    meeting = {
        "city_slug": target.get(
            "city_slug"
        ),
        "city_name": target.get(
            "city_name"
        ),
        "meeting_date": target.get(
            "meeting_date"
        ),
        "title": target.get(
            "meeting_title"
        ),
        "external_id": target.get(
            "external_id"
        ),
        "source_url": target.get(
            "source_url"
        ),
        "agenda_url": target.get(
            "agenda_url"
        ),
        "recording_url": target.get(
            "recording_url"
        ),
    }

    story = StoryDraft(
        headline=target.get(
            "headline",
            "",
        ),
        dek=target.get(
            "dek",
            "",
        ),
        body=target.get(
            "body",
            [],
        ),
        key_facts=target.get(
            "key_facts",
            [],
        ),
        verification_notes=target.get(
            "verification_notes",
            [],
        ),
    )

    try:
        audit = audit_story(
            meeting,
            audit_notes,
            agenda,
            story,
        )
    except Exception as exc:
        return JSONResponse(
            {
                "ok": False,
                "error": (
                    f"Audit failed: "
                    f"{type(exc).__name__}: {exc}"
                ),
            },
            status_code=500,
        )

    fields = {
        "headline": story.headline,
        "dek": story.dek,
        "body": "\n".join(
            story.body
        ),
        "key_facts": "\n".join(
            story.key_facts
        ),
        "verification_notes": "\n".join(
            story.verification_notes
        ),
    }

    valid = []

    for issue in audit.issues:
        quoted = (
            issue.draft_text
            or ""
        ).strip()

        if (
            quoted
            and quoted
            in fields.get(
                issue.field,
                "",
            )
        ):
            valid.append(
                issue
            )

    material = [
        issue
        for issue in valid
        if issue.severity.lower()
        == "material"
    ]

    backup_revision(
        path,
        target,
    )

    target["audit_ok"] = (
        len(material) == 0
    )

    target["audit_issues"] = [
        issue.model_dump()
        for issue in valid
    ]

    target["audit_status"] = "fresh"
    target["audit_checked_at"] = now()

    # Re-audit never auto-approves.
    if target.get(
        "review_status"
    ) == "approved":

        if target.get("published"):
            remove_published_copy(
                target
            )

            target["published"] = False
            target["published_at"] = None
            target["unpublished_at"] = now()

        target["review_status"] = (
            "needs_review"
        )
        target["approved_at"] = None

    write_json(
        path,
        target,
    )

    return {
        "ok": True,
        "audit_ok": target[
            "audit_ok"
        ],
        "issues": len(valid),
        "material": len(material),
    }


@app.get("/health")
def health():
    drafts = load_drafts()

    return {
        "ok": True,
        "draft_count": len(drafts),
        "needs_review": sum(
            1
            for d in drafts
            if review_status(d)
            == "needs_review"
        ),
        "approved": sum(
            1
            for d in drafts
            if review_status(d)
            == "approved"
        ),
        "needs_fix": sum(
            1
            for d in drafts
            if review_status(d)
            == "needs_fix"
        ),
        "rejected": sum(
            1
            for d in drafts
            if review_status(d)
            == "rejected"
        ),
        "published": sum(
            1
            for d in drafts
            if bool(
                d.get("published")
            )
        ),
    }
