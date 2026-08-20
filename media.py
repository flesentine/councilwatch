from __future__ import annotations

import html
import re
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
import yt_dlp

UA = "Mozilla/5.0 CouncilWatchPrivateReview/0.4"
HEADERS = {"User-Agent": UA}


def _run(cmd: list[str]) -> None:
    result = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        if len(detail) > 4000:
            detail = detail[-4000:]

        raise RuntimeError(
            detail
            or f"Command failed with exit code {result.returncode}"
        )


def _normalize(url: str) -> str:
    return html.unescape(url).replace("\\/", "/").strip()


def _ffmpeg_audio(input_url: str, output: Path, referer: str = "") -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-user_agent",
        UA,
    ]

    if referer:
        cmd += [
            "-referer",
            referer,
        ]

        from urllib.parse import urlparse
        r = urlparse(referer)
        origin = f"{r.scheme}://{r.netloc}"

        cmd += [
            "-headers",
            f"Origin: {origin}\\r\\n",
        ]

    cmd += [
        "-i",
        input_url,
        "-map",
        "0:a:0?",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-b:a",
        "64k",
        str(output),
    ]

    _run(cmd)

    if not output.exists() or output.stat().st_size < 10000:
        raise RuntimeError("ffmpeg did not produce usable audio")

    return output


def _granicus_player_url(recording_url: str) -> str:
    u = urlparse(recording_url)
    q = parse_qs(u.query)

    clip_id = (q.get("clip_id") or [""])[0]
    view_id = (q.get("view_id") or [""])[0]

    if not clip_id:
        raise RuntimeError("No Granicus clip_id found")

    return (
        f"{u.scheme or 'https'}://{u.netloc}"
        f"/player/clip/{clip_id}"
        f"?view_id={view_id}&redirect=true"
    )


def _granicus_stream(recording_url: str) -> str:
    player = _granicus_player_url(recording_url)

    r = requests.get(
        player,
        headers=HEADERS,
        timeout=30,
        allow_redirects=True,
    )
    r.raise_for_status()

    text = html.unescape(r.text).replace("\\/", "/")

    patterns = [
        r'video_url\s*=\s*["\']([^"\']+\.m3u8[^"\']*)',
        r'standardVideoUrl\s*=\s*["\']([^"\']+\.m3u8[^"\']*)',
        r'<source[^>]+src=["\']([^"\']+\.m3u8[^"\']*)',
        r'src\s*:\s*["\']([^"\']+\.m3u8[^"\']*)',
        r'(https://archive-stream\.granicus\.com/[^"\']+\.m3u8)',
    ]

    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            return _normalize(m.group(1))

    raise RuntimeError(
        f"Could not locate Granicus HLS stream in {player}"
    )


def _download_granicus(recording_url: str, output: Path) -> Path:
    stream = _granicus_stream(recording_url)

    print(
        f"    Granicus stream: {stream}",
        flush=True,
    )

    player = _granicus_player_url(recording_url)

    return _ffmpeg_audio(
        stream,
        output,
        referer=player,
    )


def _download_youtube(recording_url, output):
    """
    Robust CouncilWatch YouTube downloader.

    Strategy:
      1. mweb + PO token + IPv4
      2. android_vr + IPv4
      3. web_safari HLS + IPv4

    yt-dlp downloads the remote media.
    FFmpeg only converts the completed local file.
    """

    import subprocess
    import sys
    import time
    from pathlib import Path as _Path

    output = _Path(output)

    deno = (
        _Path.home()
        / ".deno"
        / "bin"
        / "deno"
    )

    ytdlp = (
        _Path(sys.executable)
        .with_name("yt-dlp")
    )

    if not ytdlp.exists():
        raise RuntimeError(
            f"yt-dlp missing: {ytdlp}"
        )

    def cleanup():
        for old in output.parent.glob(
            output.stem + ".*"
        ):
            try:
                old.unlink()
            except Exception:
                pass

    attempts = [
        {
            "name": "mweb + PO token + IPv4",
            "extractor": "youtube:player_client=mweb",
            "format": "bestaudio/best",
            "needs_deno": True,
        },
        {
            "name": "android_vr + IPv4",
            "extractor": "youtube:player_client=android_vr",
            "format": "bestaudio/best",
            "needs_deno": True,
        },
        {
            "name": "web_safari HLS + IPv4",
            "extractor": "youtube:player_client=web_safari",
            "format":
                "bestaudio[protocol*=m3u8]/bestaudio/best",
            "needs_deno": True,
        },
    ]

    errors = []

    for number, attempt in enumerate(
        attempts,
        start=1,
    ):
        cleanup()

        print()
        print(
            f"    YouTube attempt {number}/"
            f"{len(attempts)}:"
        )
        print(
            f"    {attempt['name']}"
        )

        template = str(
            output.with_suffix(".%(ext)s")
        )

        cmd = [
            str(ytdlp),

            "--no-playlist",

            # Current yt-dlp guidance for YouTube
            # 403 problems on IPv6.
            "--force-ipv4",

            "--retries",
            "4",

            "--fragment-retries",
            "4",

            "--retry-sleep",
            "http:5",

            "--retry-sleep",
            "fragment:5",

            "--extractor-args",
            attempt["extractor"],

            "-f",
            attempt["format"],

            "-x",

            "--audio-format",
            "mp3",

            "--audio-quality",
            "128K",

            "-o",
            template,

            recording_url,
        ]

        if (
            attempt["needs_deno"]
            and deno.exists()
        ):
            cmd[1:1] = [
                "--js-runtimes",
                f"deno:{deno}",
            ]

        try:
            subprocess.run(
                cmd,
                check=True,
                timeout=1800,
            )

            if (
                output.exists()
                and output.stat().st_size > 0
            ):
                print()
                print(
                    "    YouTube download succeeded:"
                )
                print(
                    f"    {attempt['name']}"
                )

                return output

            errors.append(
                f"{attempt['name']}: "
                "command completed but MP3 missing"
            )

        except subprocess.TimeoutExpired:
            errors.append(
                f"{attempt['name']}: timed out after 30 minutes"
            )

            print()
            print(
                "    Attempt timed out; "
                "trying fallback..."
            )

            time.sleep(3)

        except subprocess.CalledProcessError as exc:
            errors.append(
                f"{attempt['name']}: "
                f"exit {exc.returncode}"
            )

            print()
            print(
                "    Attempt failed; "
                "trying fallback..."
            )

            time.sleep(3)

    cleanup()

    raise RuntimeError(
        "All YouTube download methods failed: "
        + " | ".join(errors)
    )


def download_audio(recording_url: str, output: Path) -> Path:
    host = urlparse(recording_url).netloc.lower()

    if "granicus.com" in host:
        return _download_granicus(recording_url, output)

    if "youtube.com" in host or "youtu.be" in host:
        return _download_youtube(recording_url, output)

    return _download_youtube(recording_url, output)
