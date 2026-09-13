import json
from pathlib import Path

import notifications as n


class FakeResponse:
    def __init__(self, error=None):
        self.error = error

    def raise_for_status(self):
        if self.error is not None:
            raise self.error


def test_load_state_returns_dict(tmp_path, monkeypatch):
    state_file = tmp_path / "notification_state.json"
    state_file.write_text(
        json.dumps({"rsm:981": {"headline": "Story"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(n, "STATE_FILE", state_file)

    assert n._load_state() == {
        "rsm:981": {"headline": "Story"}
    }


def test_load_state_fails_closed_for_missing_malformed_or_non_dict(
    tmp_path,
    monkeypatch,
):
    state_file = tmp_path / "notification_state.json"
    monkeypatch.setattr(n, "STATE_FILE", state_file)

    assert n._load_state() == {}

    state_file.write_text("{not json", encoding="utf-8")
    assert n._load_state() == {}

    state_file.write_text("[1, 2, 3]", encoding="utf-8")
    assert n._load_state() == {}


def test_save_state_creates_parent_and_atomically_replaces_temp(
    tmp_path,
    monkeypatch,
):
    state_file = tmp_path / "nested" / "notification_state.json"
    monkeypatch.setattr(n, "STATE_FILE", state_file)

    state = {
        "rsm:981": {
            "headline": "St. Junípero Serra financing",
        }
    }

    n._save_state(state)

    assert state_file.exists()
    assert not state_file.with_suffix(".tmp").exists()
    assert json.loads(state_file.read_text(encoding="utf-8")) == state
    assert "Junípero" in state_file.read_text(encoding="utf-8")


def test_human_date_formats_valid_date_and_preserves_invalid_values():
    assert n._human_date("2026-09-09") == "September 9, 2026"
    assert n._human_date("not-a-date") == "not-a-date"
    assert n._human_date(None) == ""


def test_send_skips_when_topic_not_configured(monkeypatch, capsys):
    monkeypatch.setattr(n, "NTFY_TOPIC", "")

    called = []
    monkeypatch.setattr(
        n.requests,
        "post",
        lambda *args, **kwargs: called.append((args, kwargs)),
    )

    assert n._send("Title", "Message") is False
    assert called == []
    assert "NTFY_TOPIC is not configured" in capsys.readouterr().out


def test_send_posts_utf8_payload_headers_and_click(monkeypatch):
    monkeypatch.setattr(n, "NTFY_TOPIC", "councilwatch-test")
    monkeypatch.setattr(n, "NTFY_SERVER", "https://ntfy.example/")

    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()

    monkeypatch.setattr(n.requests, "post", fake_post)

    assert n._send(
        "CouncilWatch",
        "St. Junípero Serra",
        "https://review.example/story/rsm/981",
    ) is True

    assert len(calls) == 1
    url, kwargs = calls[0]
    assert url == "https://ntfy.example/councilwatch-test"
    assert kwargs["data"] == "St. Junípero Serra".encode("utf-8")
    assert kwargs["headers"] == {
        "Title": "CouncilWatch",
        "Priority": "default",
        "Tags": "newspaper",
        "Click": "https://review.example/story/rsm/981",
    }
    assert kwargs["timeout"] == 15


def test_send_uses_default_server_without_click(monkeypatch):
    monkeypatch.setattr(n, "NTFY_TOPIC", "topic")
    monkeypatch.setattr(n, "NTFY_SERVER", "")

    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()

    monkeypatch.setattr(n.requests, "post", fake_post)

    assert n._send("Title", "Message") is True
    assert calls[0][0] == "https://ntfy.sh/topic"
    assert "Click" not in calls[0][1]["headers"]


def test_send_retries_transient_failures_and_succeeds_on_third_attempt(
    monkeypatch,
):
    monkeypatch.setattr(n, "NTFY_TOPIC", "topic")
    monkeypatch.setattr(n, "NTFY_SERVER", "https://ntfy.example")

    attempts = []
    sleeps = []

    def fake_post(url, **kwargs):
        attempts.append(url)
        if len(attempts) < 3:
            raise RuntimeError("temporary outage")
        return FakeResponse()

    monkeypatch.setattr(n.requests, "post", fake_post)
    monkeypatch.setattr(n.time, "sleep", lambda seconds: sleeps.append(seconds))

    assert n._send("Title", "Message") is True
    assert len(attempts) == 3
    assert sleeps == [2, 2]


def test_send_returns_false_after_three_failures(monkeypatch, capsys):
    monkeypatch.setattr(n, "NTFY_TOPIC", "topic")
    monkeypatch.setattr(n, "NTFY_SERVER", "https://ntfy.example")

    attempts = []
    sleeps = []

    def fail_post(url, **kwargs):
        attempts.append(url)
        return FakeResponse(ValueError("503 unavailable"))

    monkeypatch.setattr(n.requests, "post", fail_post)
    monkeypatch.setattr(n.time, "sleep", lambda seconds: sleeps.append(seconds))

    assert n._send("Title", "Message") is False
    assert len(attempts) == 3
    assert sleeps == [2, 2]

    output = capsys.readouterr().out
    assert "WARNING: phone notification failed:" in output
    assert "ValueError" in output
    assert "503 unavailable" in output


def test_notify_ready_for_review_requires_identity(monkeypatch, capsys):
    monkeypatch.setattr(n, "_load_state", lambda: {})

    assert n.notify_ready_for_review({}, {}) is False
    assert "meeting identity missing" in capsys.readouterr().out


def test_notify_ready_for_review_deduplicates_successful_send(
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(
        n,
        "_load_state",
        lambda: {"rsm:981": {"sent_at": "earlier"}},
    )

    send_calls = []
    monkeypatch.setattr(
        n,
        "_send",
        lambda *args, **kwargs: send_calls.append((args, kwargs)),
    )

    assert n.notify_ready_for_review(
        {"city_slug": "rsm", "external_id": "981"},
        {},
    ) is True
    assert send_calls == []
    assert "already sent: rsm:981" in capsys.readouterr().out


def test_notify_ready_for_review_builds_message_link_and_saves_state(
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(n, "COUNCILWATCH_REVIEW_BASE_URL", "https://review.example/")
    monkeypatch.setattr(n, "_load_state", lambda: {})

    sent = []

    def fake_send(title, message, click=""):
        sent.append((title, message, click))
        return True

    saved = []
    monkeypatch.setattr(n, "_send", fake_send)
    monkeypatch.setattr(n, "_save_state", lambda state: saved.append(state))

    meeting = {
        "city_slug": "rsm",
        "external_id": 981,
        "city_name": "Meeting city should lose",
        "meeting_date": "2026-09-01",
    }
    story = {
        "city_name": "Rancho Santa Margarita",
        "meeting_date": "2026-09-09",
        "headline": "  Council adopts conduit financing  ",
    }

    assert n.notify_ready_for_review(meeting, story) is True

    assert sent == [
        (
            "CouncilWatch - Ready for review",
            (
                "Rancho Santa Margarita - September 9, 2026\n"
                "Council adopts conduit financing"
            ),
            "https://review.example/story/rsm/981",
        )
    ]

    assert len(saved) == 1
    state = saved[0]
    assert set(state) == {"rsm:981"}
    record = state["rsm:981"]
    assert record["city_slug"] == "rsm"
    assert record["external_id"] == "981"
    assert record["headline"] == "Council adopts conduit financing"
    assert record["sent_at"]
    assert "Phone notification sent:" in capsys.readouterr().out


def test_notify_ready_for_review_uses_story_identity_and_safe_fallbacks(
    monkeypatch,
):
    monkeypatch.setattr(n, "COUNCILWATCH_REVIEW_BASE_URL", "")
    monkeypatch.setattr(n, "_load_state", lambda: {})

    sent = []
    monkeypatch.setattr(
        n,
        "_send",
        lambda title, message, click="": (
            sent.append((title, message, click)) or True
        ),
    )
    monkeypatch.setattr(n, "_save_state", lambda state: None)

    assert n.notify_ready_for_review(
        {},
        {
            "city_slug": "lake-forest",
            "external_id": "77",
            "meeting_date": "bad-date",
            "headline": "",
        },
    ) is True

    assert sent == [
        (
            "CouncilWatch - Ready for review",
            "lake-forest - bad-date\nNew CouncilWatch story",
            "",
        )
    ]


def test_notify_ready_for_review_failed_send_is_not_recorded(monkeypatch):
    monkeypatch.setattr(n, "_load_state", lambda: {})
    monkeypatch.setattr(n, "_send", lambda *args, **kwargs: False)

    saved = []
    monkeypatch.setattr(n, "_save_state", lambda state: saved.append(state))

    assert n.notify_ready_for_review(
        {
            "city_slug": "rsm",
            "external_id": "981",
            "meeting_date": "2026-09-09",
        },
        {"headline": "Story"},
    ) is False

    assert saved == []


def test_send_test_notification_adds_review_root_when_configured(monkeypatch):
    monkeypatch.setattr(n, "COUNCILWATCH_REVIEW_BASE_URL", "https://review.example/")

    calls = []
    monkeypatch.setattr(
        n,
        "_send",
        lambda *args: calls.append(args) or True,
    )

    assert n.send_test_notification() is True
    assert calls == [
        (
            "CouncilWatch - Notification test",
            (
                "Phone notifications are connected.\n"
                "Future audited meetings will appear here."
            ),
            "https://review.example/",
        )
    ]


def test_send_test_notification_has_no_click_without_review_base(monkeypatch):
    monkeypatch.setattr(n, "COUNCILWATCH_REVIEW_BASE_URL", "")

    calls = []
    monkeypatch.setattr(
        n,
        "_send",
        lambda *args: calls.append(args) or True,
    )

    assert n.send_test_notification() is True
    assert calls[0][2] == ""
