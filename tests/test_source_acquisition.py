from pathlib import Path
from types import SimpleNamespace

import pytest

import source_acquisition as sa


def _make_fake_runtime(tmp_path, monkeypatch, *, deno=False):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    python = runtime / "python"
    python.write_text("", encoding="utf-8")
    ytdlp = runtime / "yt-dlp"
    ytdlp.write_text("", encoding="utf-8")
    monkeypatch.setattr(sa.sys, "executable", str(python))

    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    if deno:
        deno_path = home / ".deno" / "bin" / "deno"
        deno_path.parent.mkdir(parents=True)
        deno_path.write_text("", encoding="utf-8")
        return ytdlp, deno_path

    return ytdlp, None


@pytest.mark.parametrize(
    "url",
    [
        "https://youtu.be/abc",
        "https://youtube.com/watch?v=abc",
        "https://www.youtube.com/watch?v=abc",
        "https://M.YOUTUBE.COM:443/watch?v=abc",
    ],
)
def test_is_youtube_url_accepts_supported_hosts(url):
    assert sa.is_youtube_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/youtube.com/watch?v=abc",
        "https://youtube.com.evil.example/watch?v=abc",
        "not a url",
        "",
    ],
)
def test_is_youtube_url_rejects_other_hosts(url):
    assert sa.is_youtube_url(url) is False


def test_is_youtube_url_fails_closed_on_parser_error(monkeypatch):
    def boom(_value):
        raise ValueError("bad URL")

    monkeypatch.setattr(sa, "urlparse", boom)
    assert sa.is_youtube_url("https://youtube.com/watch?v=abc") is False


def test_clean_caption_text_strips_markup_entities_and_spacing():
    raw = "  <c>One&nbsp; two</c>\u200b   &amp;  <b>three</b>  "
    assert sa._clean_caption_text(raw) == "One two & three"


def test_clean_vtt_collapses_duplicate_and_progressive_cues(tmp_path):
    vtt = tmp_path / "sample.vtt"
    vtt.write_text(
        """WEBVTT

00:00:00.000 --> 00:00:01.000
<v Speaker>Hello &amp; welcome</v>

00:00:01.000 --> 00:00:02.000
Hello &amp; welcome to Council

00:00:02.000 --> 00:00:03.000
Hello &amp; welcome to Council

00:00:03.000 --> 00:00:04.000
NOTE ignored metadata
to Council <c>meeting</c> !

STYLE
::cue { color: lime; }
""",
        encoding="utf-8",
    )

    assert sa._clean_vtt(vtt) == "Hello & welcome to Council meeting!"


def test_clean_vtt_limits_overlap_search_to_eighty_words(tmp_path):
    prefix = [f"w{i}" for i in range(90)]
    first = " ".join(prefix)
    second = " ".join(prefix + ["tail"])
    vtt = tmp_path / "long.vtt"
    vtt.write_text(
        f"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n{first}\n\n"
        f"00:00:01.000 --> 00:00:02.000\n{second}\n",
        encoding="utf-8",
    )

    text = sa._clean_vtt(vtt)
    # A 90-word overlap is intentionally outside the bounded search window,
    # so the second cue is retained rather than incorrectly assumed identical.
    assert text.split().count("w0") == 2
    assert text.endswith("tail")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("captions-new.en-orig.vtt", (0, "captions-new.en-orig.vtt")),
        ("captions-new.en.vtt", (1, "captions-new.en.vtt")),
        ("captions-new.en-US.vtt", (2, "captions-new.en-us.vtt")),
        ("captions-new.vtt", (3, "captions-new.vtt")),
    ],
)
def test_caption_rank_prefers_original_then_english_variants(name, expected):
    assert sa._caption_rank(Path(name)) == expected


def test_youtube_captions_reuses_existing_clean_transcript(tmp_path, monkeypatch):
    clean = tmp_path / "captions-clean.txt"
    text = "x" * 500
    clean.write_text(text, encoding="utf-8")

    monkeypatch.setattr(
        sa.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("yt-dlp should not run"),
    )

    result = sa.youtube_captions("https://youtu.be/abc", tmp_path)

    assert result == {
        "kind": "captions",
        "text": text,
        "path": str(clean),
        "reused": True,
    }


def test_youtube_captions_requires_runtime_ytdlp(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    python = runtime / "python"
    python.write_text("", encoding="utf-8")
    monkeypatch.setattr(sa.sys, "executable", str(python))

    with pytest.raises(RuntimeError, match="yt-dlp executable not found"):
        sa.youtube_captions("https://youtu.be/abc", tmp_path, refresh=True)


def test_youtube_caption_refresh_reuses_old_text_when_download_finds_nothing(
    tmp_path, monkeypatch
):
    _make_fake_runtime(tmp_path, monkeypatch)
    old = "old transcript " * 50
    clean = tmp_path / "captions-clean.txt"
    clean.write_text(old, encoding="utf-8")
    stale = tmp_path / "captions-new.stale.vtt"
    stale.write_text("stale", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        assert not stale.exists()
        return SimpleNamespace(stdout="one\ntwo\nthree")

    monkeypatch.setattr(sa.subprocess, "run", fake_run)

    result = sa.youtube_captions(
        "https://youtu.be/abc", tmp_path, refresh=True
    )

    assert result["reused"] is True
    assert result["text"] == old
    assert clean.read_text(encoding="utf-8") == old


def test_youtube_caption_download_failure_reports_output_tail(tmp_path, monkeypatch):
    _make_fake_runtime(tmp_path, monkeypatch)
    lines = [f"line-{i}" for i in range(12)]
    monkeypatch.setattr(
        sa.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="\n".join(lines)),
    )

    with pytest.raises(RuntimeError) as excinfo:
        sa.youtube_captions("https://youtu.be/abc", tmp_path, refresh=True)

    message = str(excinfo.value)
    assert "No usable English YouTube captions were downloaded." in message
    assert "line-4" in message
    assert "line-11" in message
    assert "line-3" not in message


def test_youtube_caption_refresh_reuses_old_text_when_new_caption_is_short(
    tmp_path, monkeypatch
):
    _make_fake_runtime(tmp_path, monkeypatch)
    old = "old transcript " * 50
    clean = tmp_path / "captions-clean.txt"
    clean.write_text(old, encoding="utf-8")

    def fake_run(cmd, **kwargs):
        (tmp_path / "captions-new.en.vtt").write_text(
            "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nshort\n",
            encoding="utf-8",
        )
        return SimpleNamespace(stdout="ok")

    monkeypatch.setattr(sa.subprocess, "run", fake_run)

    result = sa.youtube_captions(
        "https://youtu.be/abc", tmp_path, refresh=True
    )

    assert result["reused"] is True
    assert result["text"] == old
    assert clean.read_text(encoding="utf-8") == old


def test_youtube_caption_short_without_old_text_is_rejected(tmp_path, monkeypatch):
    _make_fake_runtime(tmp_path, monkeypatch)

    def fake_run(cmd, **kwargs):
        (tmp_path / "captions-new.en.vtt").write_text(
            "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nshort\n",
            encoding="utf-8",
        )
        return SimpleNamespace(stdout="ok")

    monkeypatch.setattr(sa.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="unexpectedly short after cleaning"):
        sa.youtube_captions("https://youtu.be/abc", tmp_path, refresh=True)


def test_youtube_caption_success_prefers_en_orig_and_uses_deno(tmp_path, monkeypatch):
    ytdlp, deno = _make_fake_runtime(tmp_path, monkeypatch, deno=True)
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        (tmp_path / "captions-new.en.vtt").write_text("english", encoding="utf-8")
        (tmp_path / "captions-new.en-orig.vtt").write_text(
            "original", encoding="utf-8"
        )
        (tmp_path / "captions-new.en-US.vtt").write_text("us", encoding="utf-8")
        return SimpleNamespace(stdout="downloaded")

    chosen = []

    def fake_clean(path):
        chosen.append(path.name)
        return "clean transcript " * 40

    monkeypatch.setattr(sa.subprocess, "run", fake_run)
    monkeypatch.setattr(sa, "_clean_vtt", fake_clean)

    result = sa.youtube_captions(
        "https://youtu.be/abc", tmp_path, refresh=True
    )

    assert chosen == ["captions-new.en-orig.vtt"]
    assert result["kind"] == "captions"
    assert result["reused"] is False
    assert result["text"] == "clean transcript " * 40
    assert result["path"] == str(tmp_path / "captions-clean.txt")
    assert (tmp_path / "captions-clean.txt").read_text(encoding="utf-8") == result["text"]
    assert (tmp_path / "captions-source.vtt").read_text(encoding="utf-8") == "original"
    assert list(tmp_path.glob("captions-new*.vtt")) == []

    cmd = seen["cmd"]
    assert cmd[0] == str(ytdlp)
    assert cmd[1:3] == ["--js-runtimes", f"deno:{deno}"]
    assert "--write-subs" in cmd
    assert "--write-auto-subs" in cmd
    assert "--sub-langs" in cmd
    assert "en.*,en" in cmd
    assert cmd[-1] == "https://youtu.be/abc"
    assert seen["kwargs"]["text"] is True
    assert seen["kwargs"]["stderr"] is sa.subprocess.STDOUT


def test_acquire_source_returns_youtube_captions_without_audio(tmp_path, monkeypatch):
    expected = {
        "kind": "captions",
        "text": "caption text",
        "path": str(tmp_path / "captions-clean.txt"),
        "reused": False,
    }
    monkeypatch.setattr(sa, "is_youtube_url", lambda url: True)
    monkeypatch.setattr(sa, "youtube_captions", lambda *args, **kwargs: expected)
    monkeypatch.setattr(
        sa,
        "download_audio",
        lambda *args, **kwargs: pytest.fail("audio fallback should not run"),
    )

    result = sa.acquire_source(
        "https://youtu.be/abc",
        tmp_path,
        tmp_path / "meeting.mp3",
    )

    assert result is expected


def test_acquire_source_reuses_existing_audio_after_caption_failure(
    tmp_path, monkeypatch
):
    audio = tmp_path / "meeting.mp3"
    audio.write_bytes(b"existing")
    monkeypatch.setattr(sa, "is_youtube_url", lambda url: True)

    def caption_failure(*args, **kwargs):
        raise RuntimeError("captions unavailable")

    monkeypatch.setattr(sa, "youtube_captions", caption_failure)
    monkeypatch.setattr(
        sa,
        "download_audio",
        lambda *args, **kwargs: pytest.fail("existing audio should be reused"),
    )

    result = sa.acquire_source("https://youtu.be/abc", tmp_path, audio)

    assert result == {"kind": "audio", "path": str(audio)}
    assert audio.read_bytes() == b"existing"


def test_acquire_source_downloads_audio_for_non_youtube_source(tmp_path, monkeypatch):
    audio = tmp_path / "meeting.mp3"
    calls = []
    monkeypatch.setattr(sa, "is_youtube_url", lambda url: False)

    def fake_download(url, output):
        calls.append((url, output))
        output.write_bytes(b"new audio")

    monkeypatch.setattr(sa, "download_audio", fake_download)

    result = sa.acquire_source(
        "https://example.com/meeting",
        tmp_path,
        audio,
    )

    assert calls == [("https://example.com/meeting", audio)]
    assert result == {"kind": "audio", "path": str(audio)}


def test_acquire_source_refresh_replaces_existing_audio(tmp_path, monkeypatch):
    audio = tmp_path / "meeting.mp3"
    audio.write_bytes(b"old audio")
    monkeypatch.setattr(sa, "is_youtube_url", lambda url: False)
    saw_missing_before_download = []

    def fake_download(url, output):
        saw_missing_before_download.append(not output.exists())
        output.write_bytes(b"refreshed audio")

    monkeypatch.setattr(sa, "download_audio", fake_download)

    result = sa.acquire_source(
        "https://example.com/meeting",
        tmp_path,
        audio,
        refresh=True,
    )

    assert saw_missing_before_download == [True]
    assert audio.read_bytes() == b"refreshed audio"
    assert result == {"kind": "audio", "path": str(audio)}


def test_acquire_source_rejects_missing_or_empty_audio(tmp_path, monkeypatch):
    audio = tmp_path / "meeting.mp3"
    monkeypatch.setattr(sa, "is_youtube_url", lambda url: False)
    monkeypatch.setattr(sa, "download_audio", lambda url, output: None)

    with pytest.raises(RuntimeError, match="produced no usable audio"):
        sa.acquire_source("https://example.com/meeting", tmp_path, audio)

    audio.write_bytes(b"")
    with pytest.raises(RuntimeError, match="produced no usable audio"):
        sa.acquire_source("https://example.com/meeting", tmp_path, audio)
