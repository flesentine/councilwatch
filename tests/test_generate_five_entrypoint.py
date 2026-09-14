import json
import sys

import generate_five


def test_main_handles_missing_ready_meetings_without_undefined_city_arg(
    tmp_path,
    monkeypatch,
    capsys,
):
    status_file = tmp_path / "status.json"

    monkeypatch.setattr(generate_five, "STATUS_FILE", status_file)
    monkeypatch.setattr(generate_five, "latest_ready_meetings", lambda: [])
    monkeypatch.setattr(sys, "argv", ["generate_five.py"])

    generate_five.main()

    output = capsys.readouterr().out
    assert "WARNING: expected 5 READY meetings; found 0." in output
    assert "Finished. Ready: 0 | Failed: 0" in output

    status = json.loads(status_file.read_text(encoding="utf-8"))
    assert status["started_at"]
    assert status["updated_at"]
    assert status["cities"] == {}
