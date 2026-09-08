import sys
import types
from unittest.mock import Mock, patch

fake = types.ModuleType("yt_dlp")
fake.YoutubeDL = object
sys.modules.setdefault("yt_dlp", fake)

from media import _granicus_player_url, _granicus_stream


RECORDING_URL = (
    "https://example.granicus.com/"
    "MediaPlayer.php?view_id=2&clip_id=976"
)


def response_with(page):
    response = Mock()
    response.text = page
    response.raise_for_status.return_value = None
    return response


def test_granicus_player_url():
    assert _granicus_player_url(RECORDING_URL) == (
        "https://example.granicus.com/player/clip/976"
        "?view_id=2&redirect=true"
    )


def test_granicus_stream_normalizes_protocol_relative_hls():
    page = (
        '<source src="//archive-stream.granicus.com/'
        'OnDemand/_definst_/mp4:archive/test/'
        'test_abc.mp4/playlist.m3u8">'
    )

    with patch(
        "media.requests.get",
        return_value=response_with(page),
    ):
        stream = _granicus_stream(RECORDING_URL)

    assert stream.startswith(
        "https://archive-stream.granicus.com/"
    )
    assert stream.endswith(
        "playlist.m3u8"
    )


def test_granicus_stream_unescapes_embedded_url():
    page = (
        'video_url = "https:\\/\\/archive-stream.granicus.com'
        '\\/OnDemand\\/meeting.m3u8?token=a&amp;b=c"'
    )

    with patch(
        "media.requests.get",
        return_value=response_with(page),
    ):
        stream = _granicus_stream(RECORDING_URL)

    assert stream == (
        "https://archive-stream.granicus.com/"
        "OnDemand/meeting.m3u8?token=a&b=c"
    )
