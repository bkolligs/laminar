from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from youtube_transcript_api import YouTubeTranscriptApi


class TranscriptUnavailable(Exception):
    pass


@dataclass(slots=True)
class TranscriptSegment:
    text: str
    start: float
    duration: float
    timestamp: str


@dataclass(slots=True)
class TranscriptResult:
    text: str
    language_code: str | None
    language_name: str | None
    source: str
    is_generated: bool
    segments: list[TranscriptSegment]


def extract_video_id(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.netloc.endswith("youtube.com"):
        values = parse_qs(parsed.query)
        if "v" in values and values["v"]:
            return values["v"][0]
    if parsed.netloc == "youtu.be":
        return parsed.path.strip("/") or None
    return None


def fetch_transcript(
    video_id_or_url: str,
    languages: list[str] | None = None,
) -> TranscriptResult:
    video_id = extract_video_id(video_id_or_url) or video_id_or_url
    language_preferences = languages or ["en"]
    api = YouTubeTranscriptApi()
    try:
        transcript = api.list(video_id).find_transcript(language_preferences)
        fetched = transcript.fetch()
    except Exception as exc:  # pragma: no cover - third-party exceptions vary
        raise TranscriptUnavailable(str(exc)) from exc

    segments = [
        TranscriptSegment(
            text=snippet.text.strip(),
            start=snippet.start,
            duration=snippet.duration,
            timestamp=format_timestamp(snippet.start),
        )
        for snippet in fetched
        if snippet.text.strip()
    ]
    if not segments:
        raise TranscriptUnavailable("Transcript was empty")

    return TranscriptResult(
        text="\n".join(segment.text for segment in segments),
        language_code=fetched.language_code,
        language_name=fetched.language,
        source="youtube_transcript_api_generated"
        if transcript.is_generated
        else "youtube_transcript_api_manual",
        is_generated=transcript.is_generated,
        segments=segments,
    )


def format_timestamp(seconds: float) -> str:
    total_seconds = int(seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    remaining_seconds = total_seconds % 60

    if hours > 0:
        return f"{hours}:{minutes:02d}:{remaining_seconds:02d}"
    return f"{minutes}:{remaining_seconds:02d}"
