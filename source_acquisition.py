from __future__ import annotations

import html
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from media import download_audio


def is_youtube_url(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower().split(":")[0]
    except Exception:
        return False

    return (
        host == "youtu.be"
        or host == "youtube.com"
        or host.endswith(".youtube.com")
    )


def _clean_caption_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    value = value.replace("\u200b", "")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _clean_vtt(vtt_path: Path) -> str:
    """
    Convert YouTube VTT into a compact full-meeting transcript.

    YouTube auto-captions frequently repeat words as individual
    cues are progressively expanded. We remove timestamp/metadata
    lines and collapse overlapping cue text while retaining the
    complete spoken content.
    """

    raw = vtt_path.read_text(
        encoding="utf-8",
        errors="replace",
    )

    blocks = re.split(r"\n\s*\n", raw)

    output_words: list[str] = []
    previous_cue = ""

    for block in blocks:
        lines = [
            line.strip()
            for line in block.splitlines()
            if line.strip()
        ]

        if not lines:
            continue

        timestamp_index = None

        for i, line in enumerate(lines):
            if "-->" in line:
                timestamp_index = i
                break

        if timestamp_index is None:
            continue

        cue_lines = lines[timestamp_index + 1 :]

        cleaned_lines = []

        for line in cue_lines:
            if line.startswith(("NOTE", "STYLE", "REGION")):
                continue

            line = _clean_caption_text(line)

            if line:
                cleaned_lines.append(line)

        cue = " ".join(cleaned_lines).strip()

        if not cue:
            continue

        if cue == previous_cue:
            continue

        previous_cue = cue

        cue_words = cue.split()

        if not output_words:
            output_words.extend(cue_words)
            continue

        max_overlap = min(
            len(cue_words),
            len(output_words),
            80,
        )

        overlap = 0

        for size in range(max_overlap, 0, -1):
            left = [
                w.lower()
                for w in output_words[-size:]
            ]

            right = [
                w.lower()
                for w in cue_words[:size]
            ]

            if left == right:
                overlap = size
                break

        output_words.extend(cue_words[overlap:])

    text = " ".join(output_words)

    text = re.sub(
        r"\s+([,.!?;:])",
        r"\1",
        text,
    )

    text = re.sub(r"\s+", " ", text).strip()

    return text


def _caption_rank(path: Path) -> tuple[int, str]:
    name = path.name.lower()

    if ".en-orig." in name:
        return (0, name)

    if ".en." in name:
        return (1, name)

    if ".en-" in name:
        return (2, name)

    return (3, name)


def youtube_captions(
    recording_url: str,
    workdir: Path,
    refresh: bool = False,
) -> dict:
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    clean_path = workdir / "captions-clean.txt"
    source_vtt = workdir / "captions-source.vtt"

    # Normal runs reuse already acquired captions.
    if (
        not refresh
        and clean_path.exists()
        and clean_path.stat().st_size >= 500
    ):
        text = clean_path.read_text(
            encoding="utf-8",
            errors="replace",
        )

        return {
            "kind": "captions",
            "text": text,
            "path": str(clean_path),
            "reused": True,
        }

    old_text = None

    if (
        clean_path.exists()
        and clean_path.stat().st_size >= 500
    ):
        old_text = clean_path.read_text(
            encoding="utf-8",
            errors="replace",
        )

    ytdlp = Path(sys.executable).with_name("yt-dlp")

    if not ytdlp.exists():
        raise RuntimeError(
            f"yt-dlp executable not found: {ytdlp}"
        )

    # Don't destroy our last known-good captions while refreshing.
    for old in workdir.glob("captions-new*.vtt"):
        try:
            old.unlink()
        except Exception:
            pass

    template = str(
        workdir / "captions-new.%(ext)s"
    )

    cmd = [
        str(ytdlp),
        "--no-playlist",
        "--force-ipv4",
        "--skip-download",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs",
        "en.*,en",
        "--sub-format",
        "vtt",
        "-o",
        template,
        recording_url,
    ]

    deno = (
        Path.home()
        / ".deno"
        / "bin"
        / "deno"
    )

    if deno.exists():
        cmd[1:1] = [
            "--js-runtimes",
            f"deno:{deno}",
        ]

    print(
        "    Checking YouTube captions first...",
        flush=True,
    )

    result = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    candidates = sorted(
        workdir.glob("captions-new*.vtt"),
        key=_caption_rank,
    )

    if not candidates:
        if old_text:
            print(
                "    Caption refresh failed; "
                "reusing existing cleaned captions.",
                flush=True,
            )

            return {
                "kind": "captions",
                "text": old_text,
                "path": str(clean_path),
                "reused": True,
            }

        tail = "\n".join(
            (result.stdout or "").splitlines()[-8:]
        )

        raise RuntimeError(
            "No usable English YouTube captions were "
            "downloaded.\n" + tail
        )

    chosen = candidates[0]

    text = _clean_vtt(chosen)

    if len(text.strip()) < 500:
        if old_text:
            print(
                "    New captions were unexpectedly short; "
                "reusing existing cleaned captions.",
                flush=True,
            )

            return {
                "kind": "captions",
                "text": old_text,
                "path": str(clean_path),
                "reused": True,
            }

        raise RuntimeError(
            "YouTube captions were unexpectedly short "
            f"after cleaning: {len(text)} characters"
        )

    shutil.copyfile(
        chosen,
        source_vtt,
    )

    clean_path.write_text(
        text,
        encoding="utf-8",
    )

    for temp in candidates:
        try:
            temp.unlink()
        except Exception:
            pass

    print(
        "    YouTube captions acquired:",
        f"{len(text):,} characters",
        flush=True,
    )

    return {
        "kind": "captions",
        "text": text,
        "path": str(clean_path),
        "reused": False,
    }


def acquire_source(
    recording_url: str,
    workdir: Path,
    audio_path: Path,
    refresh: bool = False,
) -> dict:
    """
    CouncilWatch source priority:

      YouTube:
        captions -> audio fallback

      Other sources:
        audio

    Returns either:
      {"kind": "captions", "text": ..., "path": ...}
    or:
      {"kind": "audio", "path": ...}
    """

    workdir = Path(workdir)
    audio_path = Path(audio_path)

    if is_youtube_url(recording_url):
        try:
            return youtube_captions(
                recording_url,
                workdir,
                refresh=refresh,
            )

        except Exception as exc:
            print()
            print(
                "    WARNING: YouTube captions unavailable:",
                type(exc).__name__,
                exc,
                flush=True,
            )
            print(
                "    Falling back to YouTube audio.",
                flush=True,
            )
            print()

    if (
        not refresh
        and audio_path.exists()
        and audio_path.stat().st_size > 0
    ):
        print(
            "    Reusing existing meeting audio:",
            f"{audio_path.stat().st_size / 1024 / 1024:.1f} MB",
            flush=True,
        )

    else:
        if audio_path.exists():
            try:
                audio_path.unlink()
            except Exception:
                pass

        download_audio(
            recording_url,
            audio_path,
        )

    if (
        not audio_path.exists()
        or audio_path.stat().st_size == 0
    ):
        raise RuntimeError(
            "Media extraction produced no usable audio."
        )

    return {
        "kind": "audio",
        "path": str(audio_path),
    }
