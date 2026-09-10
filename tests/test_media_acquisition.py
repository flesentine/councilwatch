import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import media


RECORDING_URL = (
    "https://example.granicus.com/"
    "MediaPlayer.php?view_id=2&clip_id=976"
)


def _response(page: str):
    response = Mock()
    response.text = page
    response.raise_for_status.return_value = None
    return response


def _install_fake_ytdlp(tmp_path, monkeypatch):
    python = tmp_path / "python"
    ytdlp = tmp_path / "yt-dlp"
    python.write_text("", encoding="utf-8")
    ytdlp.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "executable", str(python))
    return ytdlp


def test_run_success_passes_capture_options():
    result = SimpleNamespace(returncode=0, stderr="", stdout="")

    with patch("media.subprocess.run", return_value=result) as run:
        media._run(["example", "arg"])

    run.assert_called_once_with(
        ["example", "arg"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_run_raises_with_stderr_detail():
    result = SimpleNamespace(
        returncode=2,
        stderr="ffmpeg exploded",
        stdout="ignored stdout",
    )

    with patch("media.subprocess.run", return_value=result):
        with pytest.raises(RuntimeError, match="ffmpeg exploded"):
            media._run(["ffmpeg"])


def test_run_truncates_long_failure_detail():
    detail = "x" * 4500
    result = SimpleNamespace(returncode=1, stderr=detail, stdout="")

    with patch("media.subprocess.run", return_value=result):
        with pytest.raises(RuntimeError) as exc_info:
            media._run(["ffmpeg"])

    assert str(exc_info.value) == detail[-4000:]


def test_run_uses_generic_message_without_output():
    result = SimpleNamespace(returncode=9, stderr="", stdout="")

    with patch("media.subprocess.run", return_value=result):
        with pytest.raises(
            RuntimeError,
            match="Command failed with exit code 9",
        ):
            media._run(["example"])


def test_normalize_unescapes_and_trims_urls():
    assert media._normalize(
        "  https:\\/\\/example.com\\/a.m3u8?x=1&amp;y=2  "
    ) == "https://example.com/a.m3u8?x=1&y=2"


def test_normalize_expands_protocol_relative_urls():
    assert media._normalize("//example.com/stream.m3u8") == (
        "https://example.com/stream.m3u8"
    )


def test_ffmpeg_audio_builds_referer_origin_headers_and_returns_output(tmp_path):
    output = tmp_path / "nested" / "meeting.mp3"
    seen = {}

    def fake_run(cmd):
        seen["cmd"] = cmd
        output.write_bytes(b"a" * 10000)

    with patch.object(media, "_run", side_effect=fake_run):
        result = media._ffmpeg_audio(
            "https://cdn.example.com/stream.m3u8",
            output,
            referer="https://city.example.com/player/clip/976?view_id=2",
        )

    assert result == output
    assert output.parent.is_dir()

    cmd = seen["cmd"]
    assert cmd[:2] == ["ffmpeg", "-y"]
    assert "-referer" in cmd
    assert cmd[cmd.index("-referer") + 1] == (
        "https://city.example.com/player/clip/976?view_id=2"
    )
    assert "-headers" in cmd
    assert cmd[cmd.index("-headers") + 1] == (
        "Origin: https://city.example.com\\r\\n"
    )
    assert cmd[-1] == str(output)


def test_ffmpeg_audio_without_referer_omits_http_context(tmp_path):
    output = tmp_path / "meeting.mp3"
    seen = {}

    def fake_run(cmd):
        seen["cmd"] = cmd
        output.write_bytes(b"a" * 10001)

    with patch.object(media, "_run", side_effect=fake_run):
        assert media._ffmpeg_audio(
            "https://cdn.example.com/stream.m3u8",
            output,
        ) == output

    assert "-referer" not in seen["cmd"]
    assert "-headers" not in seen["cmd"]


def test_ffmpeg_audio_rejects_missing_or_tiny_output(tmp_path):
    output = tmp_path / "meeting.mp3"

    with patch.object(media, "_run", return_value=None):
        with pytest.raises(
            RuntimeError,
            match="ffmpeg did not produce usable audio",
        ):
            media._ffmpeg_audio("https://example.com/stream.m3u8", output)

    output.write_bytes(b"tiny")

    with patch.object(media, "_run", return_value=None):
        with pytest.raises(
            RuntimeError,
            match="ffmpeg did not produce usable audio",
        ):
            media._ffmpeg_audio("https://example.com/stream.m3u8", output)


def test_granicus_player_url_requires_clip_id():
    with pytest.raises(RuntimeError, match="No Granicus clip_id found"):
        media._granicus_player_url(
            "https://example.granicus.com/MediaPlayer.php?view_id=2"
        )


def test_granicus_player_url_allows_missing_view_id():
    assert media._granicus_player_url(
        "https://example.granicus.com/MediaPlayer.php?clip_id=976"
    ) == (
        "https://example.granicus.com/player/clip/976"
        "?view_id=&redirect=true"
    )


def test_granicus_stream_finds_late_archive_pattern_and_sets_request_contract():
    page = (
        "prefix "
        "https://archive-stream.granicus.com/OnDemand/meeting.m3u8"
        " suffix"
    )

    with patch(
        "media.requests.get",
        return_value=_response(page),
    ) as get:
        stream = media._granicus_stream(RECORDING_URL)

    assert stream == (
        "https://archive-stream.granicus.com/OnDemand/meeting.m3u8"
    )
    get.assert_called_once_with(
        "https://example.granicus.com/player/clip/976"
        "?view_id=2&redirect=true",
        headers=media.HEADERS,
        timeout=30,
        allow_redirects=True,
    )


def test_granicus_stream_supports_standard_video_url_pattern():
    page = (
        'standardVideoUrl = "https://archive-stream.granicus.com/'
        'OnDemand/standard.m3u8"'
    )

    with patch(
        "media.requests.get",
        return_value=_response(page),
    ):
        assert media._granicus_stream(RECORDING_URL).endswith(
            "/OnDemand/standard.m3u8"
        )


def test_granicus_stream_supports_object_src_pattern():
    page = (
        'player = {src: "https://archive-stream.granicus.com/'
        'OnDemand/object.m3u8"}'
    )

    with patch(
        "media.requests.get",
        return_value=_response(page),
    ):
        assert media._granicus_stream(RECORDING_URL).endswith(
            "/OnDemand/object.m3u8"
        )


def test_granicus_stream_raises_when_page_has_no_hls():
    with patch(
        "media.requests.get",
        return_value=_response("<html>no stream here</html>"),
    ):
        with pytest.raises(
            RuntimeError,
            match="Could not locate Granicus HLS stream",
        ):
            media._granicus_stream(RECORDING_URL)


def test_download_granicus_passes_stream_and_player_as_ffmpeg_referer(tmp_path):
    output = tmp_path / "meeting.mp3"
    stream = "https://archive-stream.granicus.com/meeting.m3u8"
    expected_player = (
        "https://example.granicus.com/player/clip/976"
        "?view_id=2&redirect=true"
    )

    with (
        patch.object(media, "_granicus_stream", return_value=stream),
        patch.object(
            media,
            "_ffmpeg_audio",
            return_value=output,
        ) as ffmpeg,
    ):
        assert media._download_granicus(RECORDING_URL, output) == output

    ffmpeg.assert_called_once_with(
        stream,
        output,
        referer=expected_player,
    )


def test_download_youtube_requires_sibling_ytdlp(tmp_path, monkeypatch):
    python = tmp_path / "python"
    python.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "executable", str(python))

    with pytest.raises(RuntimeError, match="yt-dlp missing"):
        media._download_youtube(
            "https://www.youtube.com/watch?v=test",
            tmp_path / "meeting.mp3",
        )


def test_download_youtube_first_attempt_success_cleans_stale_files(
    tmp_path,
    monkeypatch,
):
    ytdlp = _install_fake_ytdlp(tmp_path, monkeypatch)
    output = tmp_path / "meeting.mp3"
    stale = tmp_path / "meeting.part"
    stale.write_bytes(b"stale")
    calls = []

    def fake_run(cmd, check, timeout):
        calls.append((cmd, check, timeout))
        output.write_bytes(b"audio")
        return SimpleNamespace(returncode=0)

    with patch("media.subprocess.run", side_effect=fake_run):
        result = media._download_youtube(
            "https://www.youtube.com/watch?v=test",
            output,
        )

    assert result == output
    assert not stale.exists()
    assert len(calls) == 1

    cmd, check, timeout = calls[0]
    assert cmd[0] == str(ytdlp)
    assert check is True
    assert timeout == 1800
    assert "youtube:player_client=mweb" in cmd
    assert "bestaudio/best" in cmd
    assert str(output.with_suffix(".%(ext)s")) in cmd


def test_download_youtube_adds_deno_runtime_when_available(
    tmp_path,
    monkeypatch,
):
    ytdlp = _install_fake_ytdlp(tmp_path, monkeypatch)
    output = tmp_path / "meeting.mp3"
    deno = tmp_path / ".deno" / "bin" / "deno"
    deno.parent.mkdir(parents=True)
    deno.write_text("", encoding="utf-8")
    calls = []

    def fake_run(cmd, check, timeout):
        calls.append(cmd)
        output.write_bytes(b"audio")
        return SimpleNamespace(returncode=0)

    with (
        patch("pathlib.Path.home", return_value=tmp_path),
        patch("media.subprocess.run", side_effect=fake_run),
    ):
        assert media._download_youtube(
            "https://youtu.be/test",
            output,
        ) == output

    assert calls[0][0] == str(ytdlp)
    assert calls[0][1:3] == [
        "--js-runtimes",
        f"deno:{deno}",
    ]


def test_download_youtube_falls_through_timeout_and_command_failure(
    tmp_path,
    monkeypatch,
):
    _install_fake_ytdlp(tmp_path, monkeypatch)
    output = tmp_path / "meeting.mp3"
    calls = []

    def fake_run(cmd, check, timeout):
        calls.append(cmd)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)
        if len(calls) == 2:
            raise subprocess.CalledProcessError(returncode=7, cmd=cmd)

        output.write_bytes(b"audio")
        return SimpleNamespace(returncode=0)

    with (
        patch("media.subprocess.run", side_effect=fake_run),
        patch("time.sleep") as sleep,
    ):
        assert media._download_youtube(
            "https://www.youtube.com/watch?v=test",
            output,
        ) == output

    assert len(calls) == 3
    assert "youtube:player_client=mweb" in calls[0]
    assert "youtube:player_client=android_vr" in calls[1]
    assert "youtube:player_client=web_safari" in calls[2]
    assert "bestaudio[protocol*=m3u8]/bestaudio/best" in calls[2]
    assert sleep.call_count == 2
    sleep.assert_any_call(3)


def test_download_youtube_reports_all_failure_modes_and_final_cleanup(
    tmp_path,
    monkeypatch,
):
    _install_fake_ytdlp(tmp_path, monkeypatch)
    output = tmp_path / "meeting.mp3"
    partial = tmp_path / "meeting.webm"
    calls = []

    def fake_run(cmd, check, timeout):
        calls.append(cmd)
        partial.write_bytes(b"partial")

        if len(calls) == 1:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)
        if len(calls) == 2:
            raise subprocess.CalledProcessError(returncode=13, cmd=cmd)

        return SimpleNamespace(returncode=0)

    with (
        patch("media.subprocess.run", side_effect=fake_run),
        patch("time.sleep"),
    ):
        with pytest.raises(RuntimeError) as exc_info:
            media._download_youtube(
                "https://www.youtube.com/watch?v=test",
                output,
            )

    message = str(exc_info.value)
    assert "mweb + PO token + IPv4: timed out after 30 minutes" in message
    assert "android_vr + IPv4: exit 13" in message
    assert (
        "web_safari HLS + IPv4: command completed but MP3 missing"
        in message
    )
    assert not partial.exists()


@pytest.mark.parametrize(
    ("url", "target"),
    [
        (
            "https://city.granicus.com/MediaPlayer.php?clip_id=1",
            "granicus",
        ),
        ("https://www.youtube.com/watch?v=abc", "youtube"),
        ("https://youtu.be/abc", "youtube"),
        ("https://video.example.com/archive/meeting", "youtube"),
    ],
)
def test_download_audio_dispatches_by_host(tmp_path, url, target):
    output = tmp_path / "meeting.mp3"

    with (
        patch.object(
            media,
            "_download_granicus",
            return_value=output,
        ) as granicus,
        patch.object(
            media,
            "_download_youtube",
            return_value=output,
        ) as youtube,
    ):
        assert media.download_audio(url, output) == output

    if target == "granicus":
        granicus.assert_called_once_with(url, output)
        youtube.assert_not_called()
    else:
        youtube.assert_called_once_with(url, output)
        granicus.assert_not_called()
