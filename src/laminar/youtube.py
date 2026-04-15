from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from xml.etree import ElementTree

from laminar.fetch import fetch_text


class TranscriptUnavailable(Exception):
    pass


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


def fetch_transcript(video_id: str, languages: list[str] | None = None) -> tuple[str, str | None, str]:
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    return fetch_transcript_from_watch_url(watch_url, languages=languages)


def fetch_transcript_from_watch_url(
    watch_url: str,
    languages: list[str] | None = None,
) -> tuple[str, str | None, str]:
    page = fetch_text(watch_url)
    player_response = _extract_player_response(page)
    captions = (
        player_response.get("captions", {})
        .get("playerCaptionsTracklistRenderer", {})
        .get("captionTracks", [])
    )
    if not captions:
        raise TranscriptUnavailable("No captions available")

    preferred = languages or []
    track = _select_track(captions, preferred)
    if track is None:
        raise TranscriptUnavailable("No matching caption track")

    transcript_url = _with_format(track["baseUrl"])
    transcript_xml = fetch_text(transcript_url)
    transcript = _parse_transcript_xml(transcript_xml)
    if not transcript:
        raise TranscriptUnavailable("Transcript was empty")
    language = track.get("languageCode")
    return transcript, language, "youtube_captions"


def _extract_player_response(page: str) -> dict:
    patterns = [
        r"ytInitialPlayerResponse\s*=\s*(\{.*?\});",
        r'"playerResponse":"({.*?})"',
    ]
    for pattern in patterns:
        match = re.search(pattern, page, re.DOTALL)
        if not match:
            continue
        payload = match.group(1)
        try:
            if pattern.startswith('"'):
                payload = bytes(payload, "utf-8").decode("unicode_escape")
            return json.loads(payload)
        except json.JSONDecodeError:
            continue
    raise TranscriptUnavailable("Could not parse YouTube player response")


def _select_track(captions: list[dict], preferred_languages: list[str]) -> dict | None:
    for language in preferred_languages:
        for track in captions:
            if track.get("languageCode") == language:
                return track
    return captions[0] if captions else None


def _with_format(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme == "file":
        return url
    query = parse_qs(parsed.query)
    query["fmt"] = ["srv3"]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def _parse_transcript_xml(xml_text: str) -> str:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise TranscriptUnavailable("Invalid transcript payload") from exc

    segments: list[str] = []
    for node in root.findall(".//text"):
        text = "".join(node.itertext()).strip()
        if not text:
            continue
        if not segments or segments[-1] != text:
            segments.append(text)
    return "\n".join(segments)
