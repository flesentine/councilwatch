import sys
import types

fake = types.ModuleType("yt_dlp")
fake.YoutubeDL = object
sys.modules.setdefault("yt_dlp", fake)

from media import extract_media_urls


def test_extract_granicus_hls():
    page = '<source src="//archive-stream.granicus.com/OnDemand/_definst_/mp4:archive/test/test_abc.mp4/playlist.m3u8">'
    urls = extract_media_urls(page)
    assert urls
    assert urls[0].startswith("https://archive-stream.granicus.com/")
    assert ".m3u8" in urls[0]


def test_extract_asx_ref():
    urls = extract_media_urls('<REF HREF="https://example.com/meeting.mp3"/>')
    assert "https://example.com/meeting.mp3" in urls
