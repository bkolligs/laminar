from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from typing import Callable, Protocol
from urllib.parse import parse_qsl, urlencode, urlparse
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
        verbose: Callable[[str], None] | None = None,
    ) -> list[NormalizedItem]: ...


class BlogAdapter:
    def scan(
        self,
        source: SourceConfig,
        *,
        since: datetime | None = None,
        verbose: Callable[[str], None] | None = None,
    ) -> list[NormalizedItem]:
        xml_text = fetch_text(source.feed_url or "")
        root = ElementTree.fromstring(xml_text)
        if root.tag == "feed" or root.tag.endswith("}feed"):
            return self._scan_atom(root, source, since=since, verbose=verbose)
        return self._scan_rss(root, source, since=since, verbose=verbose)

    def _scan_rss(
        self,
        root: ElementTree.Element,
        source: SourceConfig,
        *,
        since: datetime | None = None,
        verbose: Callable[[str], None] | None = None,
    ) -> list[NormalizedItem]:
        items: list[NormalizedItem] = []
        channel_title = _find_text(root, "./channel/title")
        for entry in root.findall("./channel/item"):
            published_at = _parse_dt(_find_text(entry, "./pubDate"))
            if since and published_at and published_at <= since:
                _verbose_log(
                    verbose,
                    f"{source.id}: stopping rss scan at {_display_dt(published_at)} because it is at or before cutoff {_display_dt(since)}",
                )
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
        verbose: Callable[[str], None] | None = None,
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
                _verbose_log(
                    verbose,
                    f"{source.id}: stopping atom scan at {_display_dt(published_at)} because it is at or before cutoff {_display_dt(since)}",
                )
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
        verbose: Callable[[str], None] | None = None,
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
                _verbose_log(
                    verbose,
                    f"{source.id}: stopping youtube scan at {_display_dt(published_at)} because it is at or before cutoff {_display_dt(since)}",
                )
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
                    _verbose_log(
                        verbose,
                        f"{source.id}: transcript available for {video_id} in {transcript_language or 'unknown language'} via {transcript_source or 'unknown source'}",
                    )
                except TranscriptUnavailable:
                    transcript_status = "missing"
                    _verbose_log(
                        verbose,
                        f"{source.id}: transcript missing for {video_id}",
                    )

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
        verbose: Callable[[str], None] | None = None,
    ) -> list[NormalizedItem]:
        payload = _run_x_command(source, verbose=verbose)
        data = json.loads(payload)
        tweets = _extract_x_tweets(data)
        if tweets is None:
            raise ValueError(
                f"Source {source.id}: x payload must contain tweet-like objects"
            )
        _verbose_log(
            verbose,
            f"{source.id}: x payload yielded {len(tweets)} candidate posts before cutoff filtering",
        )

        users = _extract_x_users(data)

        items: list[NormalizedItem] = []
        for tweet in tweets:
            tweet_id = _string_value(tweet.get("id"))
            username = _tweet_username(tweet, users) or source.handle
            canonical_url = _tweet_canonical_url(tweet, tweet_id, username)
            text = _tweet_text(tweet)
            published_at = _parse_dt(_tweet_created_at(tweet))
            if since and published_at and published_at <= since:
                _verbose_log(
                    verbose,
                    f"{source.id}: skipping x post {tweet_id or '(unknown id)'} from {_display_dt(published_at)} because it is at or before cutoff {_display_dt(since)}",
                )
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


def _run_x_command(
    source: SourceConfig,
    *,
    verbose: Callable[[str], None] | None = None,
) -> str:
    command = source.command[:]
    if not command:
        if source.feed_url:
            command = ["xurl", _x_command_target(source.feed_url)]
        else:
            api_url = source.metadata.get("api_url")
            if not isinstance(api_url, str) or not api_url:
                raise ValueError(f"Source {source.id}: missing x command target")
            command = ["xurl", api_url]
    _verbose_log(verbose, f"{source.id}: running x command {' '.join(command)}")

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


def _x_command_target(feed_url: str) -> str:
    parsed = urlparse(feed_url)
    if parsed.scheme in {"http", "https"} and parsed.netloc.endswith("x.com"):
        path_parts = [part for part in parsed.path.split("/") if part]
        if len(path_parts) >= 3 and path_parts[0] == "i" and path_parts[1] == "lists":
            list_id = path_parts[2]
            params = dict(parse_qsl(parsed.query, keep_blank_values=False))
            params.setdefault("max_results", "100")
            params.setdefault("expansions", "author_id")
            params.setdefault("tweet.fields", "created_at")
            params.setdefault("user.fields", "username")
            return f"/2/lists/{list_id}/tweets?{urlencode(sorted(params.items()))}"
    return feed_url


def _extract_x_tweets(data: object) -> list[dict[str, object]] | None:
    if isinstance(data, list):
        return _coerce_x_tweets(data)

    if not isinstance(data, dict):
        return None

    for key in ("data", "items", "tweets"):
        value = data.get(key)
        if isinstance(value, list):
            return _coerce_x_tweets(value)

    if _looks_like_x_tweet(data):
        return [data]
    return None


def _extract_x_users(data: object) -> dict[str, str]:
    if not isinstance(data, dict):
        return {}
    includes = data.get("includes", {})
    if not isinstance(includes, dict):
        return {}

    users: dict[str, str] = {}
    raw_users = includes.get("users", [])
    if not isinstance(raw_users, list):
        return users

    for user in raw_users:
        if not isinstance(user, dict):
            continue
        user_id = _string_value(user.get("id"))
        username = _string_value(user.get("username")) or _string_value(
            user.get("screen_name")
        )
        if user_id and username:
            users[user_id] = username
    return users


def _looks_like_x_tweet(node: dict[str, object]) -> bool:
    if _tweet_text(node) is None:
        return False
    return _string_value(node.get("id")) is not None


def _tweet_text(tweet: dict[str, object]) -> str | None:
    direct_text = _string_value(tweet.get("text")) or _string_value(
        tweet.get("full_text")
    )
    if direct_text:
        return direct_text

    for path in (
        ("legacy", "full_text"),
        ("legacy", "text"),
        ("note_tweet", "note_tweet_results", "result", "text"),
    ):
        value = _nested_string(tweet, *path)
        if value:
            return value
    return None


def _tweet_created_at(tweet: dict[str, object]) -> str | None:
    return _string_value(tweet.get("created_at")) or _nested_string(
        tweet, "legacy", "created_at"
    )


def _tweet_username(tweet: dict[str, object], users: dict[str, str]) -> str | None:
    author_id = _string_value(tweet.get("author_id"))
    if author_id and author_id in users:
        return users[author_id]

    for path in (
        ("username",),
        ("screen_name",),
        ("user", "username"),
        ("user", "screen_name"),
        ("author", "username"),
        ("author", "screen_name"),
        ("legacy", "screen_name"),
        ("core", "user_results", "result", "legacy", "screen_name"),
        ("user_results", "result", "legacy", "screen_name"),
    ):
        value = _nested_string(tweet, *path)
        if value:
            return value
    return None


def _tweet_canonical_url(
    tweet: dict[str, object],
    tweet_id: str | None,
    username: str | None,
) -> str | None:
    for key in ("url", "permalink"):
        value = _string_value(tweet.get(key))
        if value:
            return value
    if username and tweet_id:
        return f"https://x.com/{username}/status/{tweet_id}"
    return None


def _nested_string(node: object, *path: str) -> str | None:
    current = node
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return _string_value(current)


def _string_value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, int):
        return str(value)
    return None


def _coerce_x_tweets(value: list[object]) -> list[dict[str, object]]:
    tweets: list[dict[str, object]] = []
    for item in value:
        if isinstance(item, dict) and _looks_like_x_tweet(item):
            tweets.append(item)
    return tweets


def _verbose_log(
    verbose: Callable[[str], None] | None,
    message: str,
) -> None:
    if verbose is not None:
        verbose(message)


def _display_dt(value: datetime | None) -> str:
    if value is None:
        return "(unknown time)"
    return value.isoformat()
