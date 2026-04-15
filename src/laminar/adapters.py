from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from typing import Protocol
from xml.etree import ElementTree

from laminar.fetch import fetch_text
from laminar.models import NormalizedItem, SourceConfig
from laminar.youtube import (
    TranscriptUnavailable,
    extract_video_id,
    fetch_transcript,
)


class Adapter(Protocol):
    def scan(
        self,
        source: SourceConfig,
        *,
        since: datetime | None = None,
    ) -> list[NormalizedItem]: ...


class BlogAdapter:
    def scan(
        self,
        source: SourceConfig,
        *,
        since: datetime | None = None,
    ) -> list[NormalizedItem]:
        xml_text = fetch_text(source.feed_url or "")
        root = ElementTree.fromstring(xml_text)
        if root.tag == "feed" or root.tag.endswith("}feed"):
            return self._scan_atom(root, source, since=since)
        return self._scan_rss(root, source, since=since)

    def _scan_rss(
        self,
        root: ElementTree.Element,
        source: SourceConfig,
        *,
        since: datetime | None = None,
    ) -> list[NormalizedItem]:
        items: list[NormalizedItem] = []
        channel_title = _find_text(root, "./channel/title")
        for entry in root.findall("./channel/item"):
            published_at = _parse_dt(_find_text(entry, "./pubDate"))
            if since and published_at and published_at <= since:
                break
            url = _find_text(entry, "./link")
            description = _find_text(entry, "./description")
            items.append(
                NormalizedItem(
                    source_id=source.id,
                    item_type="blog",
                    external_id=_find_text(entry, "./guid") or url,
                    canonical_url=url,
                    title=_find_text(entry, "./title") or "(untitled blog post)",
                    author=_find_text(entry, "./author") or channel_title,
                    published_at=published_at,
                    excerpt=description,
                    content_text=description,
                    content_source="rss",
                    raw_payload={"feed_label": channel_title},
                )
            )
        return items

    def _scan_atom(
        self,
        root: ElementTree.Element,
        source: SourceConfig,
        *,
        since: datetime | None = None,
    ) -> list[NormalizedItem]:
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        items: list[NormalizedItem] = []
        feed_title = _find_text(root, "./atom:title", ns)
        for entry in root.findall("./atom:entry", ns):
            published_at = _parse_dt(
                _find_text(entry, "./atom:published", ns)
                or _find_text(entry, "./atom:updated", ns)
            )
            if since and published_at and published_at <= since:
                break
            url = _find_text(entry, "./atom:link[@rel='alternate']", ns, attr="href")
            if url is None:
                url = _find_text(entry, "./atom:link", ns, attr="href")
            summary = _find_text(entry, "./atom:summary", ns)
            content = _find_text(entry, "./atom:content", ns)
            items.append(
                NormalizedItem(
                    source_id=source.id,
                    item_type="blog",
                    external_id=_find_text(entry, "./atom:id", ns) or url,
                    canonical_url=url,
                    title=_find_text(entry, "./atom:title", ns)
                    or "(untitled blog post)",
                    author=_find_text(entry, "./atom:author/atom:name", ns)
                    or feed_title,
                    published_at=published_at,
                    excerpt=summary or content,
                    content_text=content or summary,
                    content_source="atom",
                    raw_payload={"feed_label": feed_title},
                )
            )
        return items


class YouTubeAdapter:
    def scan(
        self,
        source: SourceConfig,
        *,
        since: datetime | None = None,
    ) -> list[NormalizedItem]:
        xml_text = fetch_text(source.feed_url or "")
        root = ElementTree.fromstring(xml_text)
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "yt": "http://www.youtube.com/xml/schemas/2015",
        }
        channel_title = _find_text(root, "./atom:title", ns)
        items: list[NormalizedItem] = []
        for entry in root.findall("./atom:entry", ns):
            published_at = _parse_dt(_find_text(entry, "./atom:published", ns))
            if since and published_at and published_at <= since:
                break
            video_url = _find_text(
                entry, "./atom:link[@rel='alternate']", ns, attr="href"
            )
            video_id = _find_text(entry, "./yt:videoId", ns) or extract_video_id(
                video_url
            )
            transcript_text = None
            transcript_status = "missing"
            transcript_language = None
            transcript_source = None
            transcript_segments: list[dict[str, object]] = []
            transcript_is_generated = None
            if video_url and video_id:
                try:
                    transcript = fetch_transcript(
                        video_id,
                        source.transcript_languages,
                    )
                    transcript_text = transcript.text
                    transcript_language = transcript.language_code
                    transcript_source = transcript.source
                    transcript_is_generated = transcript.is_generated
                    transcript_segments = [
                        {
                            "text": segment.text,
                            "start": segment.start,
                            "duration": segment.duration,
                            "timestamp": segment.timestamp,
                        }
                        for segment in transcript.segments
                    ]
                    transcript_status = "available"
                except TranscriptUnavailable:
                    transcript_status = "missing"

            items.append(
                NormalizedItem(
                    source_id=source.id,
                    item_type="video",
                    external_id=video_id,
                    canonical_url=video_url,
                    title=_find_text(entry, "./atom:title", ns) or "(untitled video)",
                    author=_find_text(entry, "./atom:author/atom:name", ns)
                    or channel_title,
                    published_at=published_at,
                    excerpt=_find_text(entry, "./atom:group/atom:description", ns)
                    or _find_text(entry, "./atom:title", ns),
                    content_text=transcript_text,
                    content_status=transcript_status,
                    content_language=transcript_language,
                    content_source=transcript_source,
                    raw_payload={
                        "video_id": video_id,
                        "channel_title": channel_title,
                        "transcript_is_generated": transcript_is_generated,
                        "transcript_segments": transcript_segments,
                    },
                )
            )
        return items


class XAdapter:
    def scan(
        self,
        source: SourceConfig,
        *,
        since: datetime | None = None,
    ) -> list[NormalizedItem]:
        payload = _run_x_command(source)
        data = json.loads(payload)
        tweets = data.get("data") if isinstance(data, dict) else data
        if not isinstance(tweets, list):
            raise ValueError(
                f"Source {source.id}: x payload must be a JSON list or object with data[]"
            )

        includes = data.get("includes", {}) if isinstance(data, dict) else {}
        users = {
            user.get("id"): user.get("username")
            for user in includes.get("users", [])
            if isinstance(user, dict)
        }

        items: list[NormalizedItem] = []
        for tweet in tweets:
            if not isinstance(tweet, dict):
                continue
            tweet_id = str(tweet.get("id")) if tweet.get("id") is not None else None
            author_id = (
                str(tweet.get("author_id"))
                if tweet.get("author_id") is not None
                else None
            )
            username = users.get(author_id) or source.handle
            canonical_url = None
            if username and tweet_id:
                canonical_url = f"https://x.com/{username}/status/{tweet_id}"
            text = tweet.get("text")
            published_at = _parse_dt(tweet.get("created_at"))
            if since and published_at and published_at <= since:
                continue
            items.append(
                NormalizedItem(
                    source_id=source.id,
                    item_type="x_post",
                    external_id=tweet_id,
                    canonical_url=canonical_url,
                    title=_title_from_text(text),
                    author=username,
                    published_at=published_at,
                    excerpt=text,
                    content_text=text,
                    content_source="x_api",
                    raw_payload=tweet,
                )
            )
        return items


def build_adapter(source: SourceConfig) -> Adapter:
    if source.kind == "blog":
        return BlogAdapter()
    if source.kind == "youtube":
        return YouTubeAdapter()
    if source.kind == "x":
        return XAdapter()
    raise ValueError(f"Unsupported source kind: {source.kind}")


def _find_text(
    element: ElementTree.Element,
    path: str,
    ns: dict[str, str] | None = None,
    attr: str | None = None,
) -> str | None:
    target = element.find(path, ns or {})
    if target is None:
        return None
    if attr:
        value = target.attrib.get(attr)
        return value.strip() if value else None
    text = "".join(target.itertext()).strip()
    return text or None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%SZ",
    ):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def _run_x_command(source: SourceConfig) -> str:
    command = source.command[:]
    if not command:
        api_url = source.metadata.get("api_url")
        if not isinstance(api_url, str) or not api_url:
            raise ValueError(f"Source {source.id}: missing api_url")
        command = ["xurl", api_url]

    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _title_from_text(text: str | None) -> str:
    if not text:
        return "(untitled x post)"
    line = " ".join(text.split())
    return line[:72] + ("..." if len(line) > 72 else "")
