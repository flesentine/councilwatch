import sys

import generate_five


def test_main_prints_private_review_app_port(monkeypatch, tmp_path, capsys):
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    status = tmp_path / "status.json"

    monkeypatch.setattr(generate_five, "DRAFTS", drafts)
    monkeypatch.setattr(generate_five, "STATUS_FILE", status)
    monkeypatch.setattr(generate_five, "latest_ready_meetings", lambda: [])
    monkeypatch.setattr(sys, "argv", ["generate_five.py"])

    generate_five.main()

    output = capsys.readouterr().out
    assert "Review: http://raspberrypi.local:8088" in output
