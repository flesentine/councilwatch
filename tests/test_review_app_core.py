import asyncio
import json
from pathlib import Path

import pytest

import review_app as review
from gemini_worker import AuditIssue, AuditResult


class FakeRequest:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error

    async def json(self):
        if self.error:
            raise self.error
        return self.payload


def body_json(response):
    return json.loads(response.body.decode("utf-8"))


def body_text(response):
    return response.body.decode("utf-8")


def draft(
    slug="alpha",
    external_id="100",
    meeting_date="2026-09-01",
    review_status="needs_review",
    audit_ok=True,
    published=False,
    **updates,
):
    data = {
        "city_slug": slug,
        "city_name": "Alpha City" if slug == "alpha" else "Beta City",
        "external_id": str(external_id),
        "meeting_date": meeting_date,
        "meeting_title": "Regular City Council Meeting",
        "headline": "Council approves park contract",
        "dek": "A concise summary.",
        "body": ["The Council approved the park contract."],
        "key_facts": ["The contract was approved."],
        "verification_notes": ["No unresolved identity issues."],
        "entity_verification": [],
        "coverage_plan": [],
        "audit_issues": [],
        "audit_ok": audit_ok,
        "review_status": review_status,
        "review_note": "",
        "revision": 1,
        "published": published,
        "published_at": "2026-09-02T00:00:00+00:00" if published else None,
        "source_url": "https://example.com/source",
        "agenda_url": "https://example.com/agenda",
        "recording_url": "https://example.com/recording",
    }
    data.update(updates)
    return data


@pytest.fixture
def env(tmp_path, monkeypatch):
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    revisions = drafts / "_revisions"
    revisions.mkdir()
    status = tmp_path / "status.json"

    monkeypatch.setattr(review, "DRAFTS", drafts)
    monkeypatch.setattr(review, "REVISION_DIR", revisions)
    monkeypatch.setattr(review, "STATUS_FILE", status)
    monkeypatch.setattr(
        review,
        "CITY_NAMES",
        {"alpha": "Alpha City", "beta": "Beta City"},
    )
    return {"drafts": drafts, "revisions": revisions, "status": status}


def put(env, data, name=None):
    path = env["drafts"] / (
        name or f"{data['city_slug']}--{data['external_id']}.json"
    )
    review.write_json(path, data)
    return path


def test_helpers_and_json_io(env):
    assert review.esc('<b>&"') == "&lt;b&gt;&amp;&quot;"
    assert review.esc(None) == ""
    assert review.review_status({}) == "needs_review"
    assert review.review_label("needs_fix") == "Needs fix"
    assert review.review_label("custom_state") == "Custom State"
    assert review.short_message("short", 10) == "short"
    assert review.short_message("first | second", 7) == "first"
    assert review.short_message("abcdefghij", 6) == "abcde…"
    assert review.now().endswith("+00:00")

    path = env["drafts"] / "sample.json"
    review.write_json(path, {"name": "José"})
    assert review.read_json(path) == {"name": "José"}
    assert "José" in path.read_text(encoding="utf-8")


def test_status_current_draft_and_queue_loading(env):
    assert review.load_status() == {"cities": {}}
    env["status"].write_text("broken", encoding="utf-8")
    assert review.load_status() == {"cities": {}}
    env["status"].write_text(
        json.dumps({"cities": {"alpha": {"phase": "done"}}}),
        encoding="utf-8",
    )
    assert review.load_status()["cities"]["alpha"]["phase"] == "done"

    current = draft()
    assert review.is_current_draft(Path("alpha--100.json"), current)
    assert not review.is_current_draft(Path("alpha--100.before.json"), current)
    assert not review.is_current_draft(
        Path("alpha--100.json"),
        {"city_slug": "alpha", "external_id": ""},
    )

    a_new = draft(external_id="200", meeting_date="2026-09-03")
    b_same = draft(slug="beta", external_id="300", meeting_date="2026-09-03")
    a_old = draft(external_id="100", meeting_date="2026-09-01")
    for item in (a_old, b_same, a_new):
        put(env, item)

    (env["drafts"] / "broken.json").write_text("{bad", encoding="utf-8")
    (env["drafts"] / "list.json").write_text("[]", encoding="utf-8")
    put(env, a_new, "alpha--200.intelligence.json")
    put(env, a_new, "alpha--200.before-20260903.json")

    rows = review.load_draft_rows()
    assert [
        (data["city_slug"], data["external_id"])
        for _, data in rows
    ] == [("alpha", "200"), ("beta", "300"), ("alpha", "100")]
    assert [d["external_id"] for d in review.load_drafts()] == [
        "200", "300", "100"
    ]

    found_path, found = review.find_draft("alpha", 100)
    assert found_path.name == "alpha--100.json"
    assert found["external_id"] == "100"
    assert review.find_draft("alpha", "999") == (None, None)


def test_backup_shell_and_home(env):
    data = draft(revision=7, review_status="approved", review_note="Looks <good>")
    path = put(env, data)
    backup = review.backup_revision(path, data)
    assert "alpha--100--rev007--" in backup.name
    assert review.read_json(backup) == data

    shell = body_text(review.shell("<strong>Body</strong>", '<unsafe>&"'))
    assert "<strong>Body</strong>" in shell
    assert '<title>&lt;unsafe&gt;&amp;&quot;</title>' in shell

    env["status"].write_text(
        json.dumps({
            "cities": {
                "beta": {
                    "phase": "failed",
                    "message": "Fetcher failed | detailed traceback",
                }
            }
        }),
        encoding="utf-8",
    )
    home = body_text(review.home())
    assert "<strong>1</strong> approved" in home
    assert "Looks &lt;good&gt;" in home
    assert "/story/alpha/100" in home
    assert 'class="card processing fail"' in home
    assert "Fetcher failed" in home
    assert "detailed traceback" not in home


def test_story_and_edit_rendering(env):
    assert review.story("alpha", "404").status_code == 404
    assert review.edit_story("alpha", "404").status_code == 404

    data = draft(
        review_status="approved",
        audit_ok=True,
        headline='Headline <unsafe> "quoted"',
        body=["Paragraph one.", "Paragraph two."],
        key_facts=["Fact one", "Fact two"],
        verification_notes=["Note one", "Note two"],
        entity_verification=[{
            "status": "CORRECTED",
            "observed_text": "Council Member Otto",
            "canonical_text": "Stephanie Oddo",
            "evidence": "Official roster <matched>",
            "official_source_url": "https://example.com/person?a=1&b=2",
        }, "ignore"],
        coverage_plan=[{
            "rank": 1,
            "score": 9,
            "must_include": True,
            "topic": "Park Contract",
            "action_status": "approved",
            "summary": "Council action",
            "why_it_matters": "Local spending",
        }, "ignore"],
        audit_issues=[{
            "severity": "minor",
            "field": "dek",
            "draft_text": "A concise summary.",
            "source_evidence": "Source notes",
            "correction": "Clarify wording",
        }, "legacy issue"],
    )
    put(env, data)

    page = body_text(review.story("alpha", "100"))
    assert "Stephanie Oddo" in page
    assert "source notes: “Council Member Otto”" in page
    assert "Official roster &lt;matched&gt;" in page
    assert "person?a=1&amp;b=2" in page
    assert "MUST INCLUDE" in page
    assert "MINOR · dek" in page
    assert "legacy issue" in page
    assert "Publish locally" in page
    assert "Approved · ready to publish" in page
    assert "Official source -&gt;" in page
    assert "Agenda -&gt;" in page
    assert "Recording -&gt;" in page

    edit = body_text(review.edit_story("alpha", "100"))
    assert "Edit draft" in edit
    assert 'Headline &lt;unsafe&gt; &quot;quoted&quot;' in edit
    assert "Paragraph one.\n\nParagraph two." in edit
    assert "Fact one\nFact two" in edit
    assert "Note one\nNote two" in edit


def test_story_published_stale_and_newsletter_labels(env):
    data = draft(
        published=True,
        review_status="approved",
        audit_ok=True,
        newsletter_draft_id="bd-1",
        newsletter_draft_revision=2,
        revision=2,
    )
    path = put(env, data)

    page = body_text(review.story("alpha", "100"))
    assert "Published locally" in page
    assert "Unpublish" in page
    assert "Newsletter draft ready" in page
    assert "YES" in page

    data["published"] = False
    data["audit_ok"] = False
    data["audit_status"] = "stale_after_manual_edit"
    data["newsletter_draft_id"] = ""
    data["newsletter_draft_error"] = "network down"
    review.write_json(path, data)

    page = body_text(review.story("alpha", "100"))
    assert "STALE — article was edited after the last audit" in page
    assert "Newsletter draft failed" in page
    assert "Publish locally" not in page


def test_review_action_validation_and_state_transitions(env, monkeypatch):
    missing = asyncio.run(
        review.review_action(
            "alpha", "404", FakeRequest({"status": "approved"})
        )
    )
    assert missing.status_code == 404

    path = put(env, draft(audit_ok=False))
    bad_json = asyncio.run(
        review.review_action(
            "alpha", "100", FakeRequest(error=ValueError("bad"))
        )
    )
    assert bad_json.status_code == 400

    invalid = asyncio.run(
        review.review_action(
            "alpha", "100", FakeRequest({"status": "published"})
        )
    )
    assert invalid.status_code == 400

    blocked = asyncio.run(
        review.review_action(
            "alpha", "100", FakeRequest({"status": "approved"})
        )
    )
    assert blocked.status_code == 409

    data = review.read_json(path)
    data["audit_ok"] = True
    review.write_json(path, data)

    approved = asyncio.run(
        review.review_action(
            "alpha",
            "100",
            FakeRequest({"status": "approved", "note": "  checked  "}),
        )
    )
    assert approved == {"ok": True, "review_status": "approved"}
    stored = review.read_json(path)
    assert stored["review_note"] == "checked"
    assert stored["approved_at"]
    assert stored["rejected_at"] is None

    stored["published"] = True
    stored["published_at"] = review.now()
    review.write_json(path, stored)
    removed = []
    monkeypatch.setattr(
        review,
        "remove_published_copy",
        lambda target: removed.append(target["external_id"]) or Path("old.html"),
    )

    rejected = asyncio.run(
        review.review_action(
            "alpha", "100", FakeRequest({"status": "rejected", "note": "No"})
        )
    )
    assert rejected == {"ok": True, "review_status": "rejected"}
    stored = review.read_json(path)
    assert stored["approved_at"] is None
    assert stored["rejected_at"]
    assert stored["published"] is False
    assert stored["unpublished_at"]
    assert removed == ["100"]


def test_save_story_validation(env):
    missing = asyncio.run(
        review.save_story(
            "alpha", "404", FakeRequest({"headline": "H", "body": ["B"]})
        )
    )
    assert missing.status_code == 404

    put(env, draft())

    bad = asyncio.run(
        review.save_story(
            "alpha", "100", FakeRequest(error=ValueError("bad"))
        )
    )
    assert bad.status_code == 400

    blank_headline = asyncio.run(
        review.save_story(
            "alpha", "100", FakeRequest({"headline": "   ", "body": ["Body"]})
        )
    )
    assert blank_headline.status_code == 400

    blank_body = asyncio.run(
        review.save_story(
            "alpha", "100", FakeRequest({"headline": "Headline", "body": []})
        )
    )
    assert blank_body.status_code == 400


def test_save_story_rejects_whitespace_only_body(env):
    put(env, draft())
    response = asyncio.run(
        review.save_story(
            "alpha",
            "100",
            FakeRequest({
                "headline": "Headline",
                "dek": "Dek",
                "body": ["   ", "\n\t"],
                "key_facts": [],
                "verification_notes": [],
            }),
        )
    )
    assert response.status_code == 400
    assert body_json(response)["error"] == "Article body cannot be blank."


def test_save_story_success_revokes_audit_approval_and_publication(env, monkeypatch):
    path = put(env, draft(
        review_status="approved",
        audit_ok=True,
        published=True,
        revision=3,
        approved_at=review.now(),
    ))
    removed = []
    monkeypatch.setattr(
        review,
        "remove_published_copy",
        lambda target: removed.append(target["external_id"]) or Path("old.html"),
    )

    result = asyncio.run(
        review.save_story(
            "alpha",
            "100",
            FakeRequest({
                "headline": "  New headline  ",
                "dek": "  New dek  ",
                "body": [" First ", "", " Second "],
                "key_facts": [" Fact ", "  "],
                "verification_notes": [" Note ", ""],
            }),
        )
    )
    assert result == {"ok": True, "revision": 4}

    stored = review.read_json(path)
    assert stored["headline"] == "New headline"
    assert stored["body"] == ["First", "Second"]
    assert stored["key_facts"] == ["Fact"]
    assert stored["verification_notes"] == ["Note"]
    assert stored["revision"] == 4
    assert stored["manually_edited"] is True
    assert stored["review_status"] == "needs_review"
    assert stored["approved_at"] is None
    assert stored["audit_ok"] is False
    assert stored["audit_issues"] == []
    assert stored["audit_status"] == "stale_after_manual_edit"
    assert stored["published"] is False
    assert stored["published_at"] is None
    assert stored["unpublished_at"]
    assert removed == ["100"]


def test_publish_gates_success_and_newsletter_failure(env, monkeypatch):
    assert review.publish_story("alpha", "404").status_code == 404

    path = put(env, draft(audit_ok=False, review_status="approved"))
    assert review.publish_story("alpha", "100").status_code == 409

    data = review.read_json(path)
    data["audit_ok"] = True
    data["review_status"] = "needs_review"
    review.write_json(path, data)
    assert review.publish_story("alpha", "100").status_code == 409

    data["review_status"] = "approved"
    data["revision"] = 5
    review.write_json(path, data)

    monkeypatch.setattr(
        review, "publish_copy", lambda target: Path("/tmp/public.html")
    )
    monkeypatch.setattr(
        review,
        "ensure_buttondown_draft",
        lambda target: {"ok": True, "created": True, "updated": False},
    )
    result = review.publish_story("alpha", "100")
    assert result["ok"] is True
    assert result["published_revision"] == 5
    assert result["local_path"] == "/tmp/public.html"

    def fail_newsletter(_target):
        raise RuntimeError("Buttondown unavailable")

    data = review.read_json(path)
    data["published"] = False
    review.write_json(path, data)
    monkeypatch.setattr(review, "ensure_buttondown_draft", fail_newsletter)

    result = review.publish_story("alpha", "100")
    assert result["ok"] is True
    assert result["newsletter"]["ok"] is False
    stored = review.read_json(path)
    assert stored["published"] is True
    assert stored["newsletter_draft_error"] == "Buttondown unavailable"
    assert stored["newsletter_draft_last_attempt_at"]


def test_unpublish(env, monkeypatch):
    assert review.unpublish_story("alpha", "404").status_code == 404

    path = put(env, draft(published=True))
    monkeypatch.setattr(
        review,
        "remove_published_copy",
        lambda target: Path("/tmp/removed.html"),
    )
    result = review.unpublish_story("alpha", "100")
    assert result["published"] is False
    assert result["removed_path"] == "/tmp/removed.html"
    stored = review.read_json(path)
    assert stored["published"] is False
    assert stored["published_at"] is None
    assert stored["unpublished_at"] == result["unpublished_at"]


def test_reaudit_missing_inputs_and_model_failure(env, monkeypatch):
    assert review.reaudit_story("alpha", "404").status_code == 404

    put(env, draft())
    missing_notes = review.reaudit_story("alpha", "100")
    assert missing_notes.status_code == 400

    (env["drafts"] / "alpha--100.notes.txt").write_text(
        "Source notes", encoding="utf-8"
    )
    monkeypatch.setattr(
        review,
        "audit_story",
        lambda *args: (_ for _ in ()).throw(RuntimeError("model down")),
    )
    failed = review.reaudit_story("alpha", "100")
    assert failed.status_code == 500
    assert "RuntimeError: model down" in body_json(failed)["error"]


def test_reaudit_evidence_boundary_filters_stale_and_revokes_approval(
    env, monkeypatch
):
    path = put(env, draft(
        review_status="approved",
        audit_ok=True,
        published=True,
        body=["The Council approved the park contract."],
    ))
    (env["drafts"] / "alpha--100.notes.txt").write_text(
        "SOURCE NOTES ONLY", encoding="utf-8"
    )
    review.write_json(
        env["drafts"] / "alpha--100.intelligence.json",
        {"entities": [{"canonical_text": "Stephanie Oddo"}]},
    )

    monkeypatch.setattr(
        review,
        "audit_verification_context",
        lambda intelligence: "VERIFIED IDENTITY CONTEXT",
    )
    monkeypatch.setattr(review, "agenda_text", lambda url: "OFFICIAL AGENDA")
    captured = {}

    def fake_audit(meeting, notes, agenda, story):
        captured.update(
            meeting=meeting, notes=notes, agenda=agenda, story=story
        )
        return AuditResult(
            ok=False,
            issues=[
                AuditIssue(
                    severity="material",
                    field="body",
                    draft_text="The Council approved the park contract.",
                    source_evidence="Agenda says discussed",
                    correction="Change approved to discussed.",
                ),
                AuditIssue(
                    severity="material",
                    field="body",
                    draft_text="Stale sentence not present.",
                    source_evidence="Old issue",
                    correction="Delete.",
                ),
            ],
        )

    monkeypatch.setattr(review, "audit_story", fake_audit)
    removed = []
    monkeypatch.setattr(
        review,
        "remove_published_copy",
        lambda target: removed.append(target["external_id"]) or Path("old.html"),
    )

    result = review.reaudit_story("alpha", "100")
    assert result == {
        "ok": True,
        "audit_ok": False,
        "issues": 1,
        "material": 1,
    }
    assert captured["notes"] == (
        "SOURCE NOTES ONLY\n\nVERIFIED IDENTITY CONTEXT"
    )
    assert captured["agenda"] == "OFFICIAL AGENDA"
    assert captured["meeting"]["external_id"] == "100"

    stored = review.read_json(path)
    assert len(stored["audit_issues"]) == 1
    assert stored["audit_status"] == "fresh"
    assert stored["review_status"] == "needs_review"
    assert stored["approved_at"] is None
    assert stored["published"] is False
    assert stored["unpublished_at"]
    assert removed == ["100"]


def test_reaudit_tolerates_bad_intelligence_and_agenda_failure(env, monkeypatch):
    path = put(env, draft(audit_ok=False))
    (env["drafts"] / "alpha--100.notes.txt").write_text(
        "SOURCE NOTES", encoding="utf-8"
    )
    (env["drafts"] / "alpha--100.intelligence.json").write_text(
        "not json", encoding="utf-8"
    )
    monkeypatch.setattr(
        review,
        "agenda_text",
        lambda url: (_ for _ in ()).throw(RuntimeError("agenda down")),
    )
    captured = {}

    def clean_audit(meeting, notes, agenda, story):
        captured["notes"] = notes
        captured["agenda"] = agenda
        return AuditResult(ok=True, issues=[])

    monkeypatch.setattr(review, "audit_story", clean_audit)
    result = review.reaudit_story("alpha", "100")
    assert result["audit_ok"] is True
    assert captured == {"notes": "SOURCE NOTES", "agenda": ""}
    assert review.read_json(path)["review_status"] == "needs_review"


def test_health_counts_states_and_publication(env):
    items = [
        draft(external_id="1", review_status="needs_review"),
        draft(external_id="2", review_status="approved", published=True),
        draft(external_id="3", review_status="needs_fix"),
        draft(external_id="4", review_status="rejected"),
        draft(external_id="5", review_status="approved"),
    ]
    for item in items:
        put(env, item)

    assert review.health() == {
        "ok": True,
        "draft_count": 5,
        "needs_review": 1,
        "approved": 2,
        "needs_fix": 1,
        "rejected": 1,
        "published": 1,
    }
