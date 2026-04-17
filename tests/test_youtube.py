from youtube_transcript_api import _errors as transcript_errors

from laminar.models import ContentStatus
from laminar.youtube import _status_from_transcript_error, extract_video_id, format_timestamp


def test_extract_video_id_from_urls() -> None:
    assert (
        extract_video_id("https://www.youtube.com/watch?v=EBw7gsDPAYQ") == "EBw7gsDPAYQ"
    )
    assert extract_video_id("https://youtu.be/EBw7gsDPAYQ") == "EBw7gsDPAYQ"


def test_format_timestamp() -> None:
    assert format_timestamp(0) == "0:00"
    assert format_timestamp(75) == "1:15"
    assert format_timestamp(3670) == "1:01:10"


def test_status_from_transcript_error_classifies_missing() -> None:
    assert _status_from_transcript_error(transcript_errors.NoTranscriptFound("video", [], [])) == ContentStatus.MISSING


def test_status_from_transcript_error_classifies_rate_limited() -> None:
    assert _status_from_transcript_error(transcript_errors.RequestBlocked("too many requests")) == ContentStatus.RATE_LIMITED
