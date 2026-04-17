from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator
from urllib.parse import parse_qs, urlparse

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from laminar.youtube import extract_video_id


class YouTubeApiError(RuntimeError):
    pass


@dataclass(slots=True)
class YouTubeChannel:
    channel_id: str
    title: str | None
    uploads_playlist_id: str


@dataclass(slots=True)
class YouTubeUpload:
    video_id: str
    title: str
    description: str | None
    channel_title: str | None
    published_at: datetime | None
    canonical_url: str


def looks_like_youtube_url(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    return host == "youtu.be" or host.endswith("youtube.com")


def resolve_channel_from_url(
    url: str,
    *,
    api_key: str | None = None,
) -> YouTubeChannel:
    channel_id = _channel_id_from_feed_url(url) or _channel_id_from_channel_url(url)
    if channel_id:
        return get_channel(channel_id, api_key=api_key)

    video_id = extract_video_id(url)
    if video_id:
        return _channel_from_video_id(video_id, api_key=api_key)

    handle = _handle_from_url(url)
    if handle:
        return get_channel_by_handle(handle, api_key=api_key)

    username = _username_from_url(url)
    if username:
        return get_channel_by_username(username, api_key=api_key)

    raise YouTubeApiError(
        "Unsupported YouTube URL; use a watch URL, youtu.be URL, /channel/ URL, @handle URL, /user/ URL, or feeds/videos.xml?channel_id=..."
    )


def get_channel(
    channel_id: str,
    *,
    api_key: str | None = None,
) -> YouTubeChannel:
    service = _service(api_key)
    try:
        response = (
            service.channels()
            .list(part="snippet,contentDetails", id=channel_id)
            .execute()
        )
    except HttpError as exc:  # pragma: no cover - network/runtime dependent
        raise YouTubeApiError(str(exc)) from exc
    return _channel_from_response(response, label=f"channel id {channel_id}")


def get_channel_by_handle(
    handle: str,
    *,
    api_key: str | None = None,
) -> YouTubeChannel:
    service = _service(api_key)
    normalized_handle = handle.lstrip("@")
    try:
        response = (
            service.channels()
            .list(part="snippet,contentDetails", forHandle=normalized_handle)
            .execute()
        )
    except HttpError as exc:  # pragma: no cover - network/runtime dependent
        raise YouTubeApiError(str(exc)) from exc
    return _channel_from_response(response, label=f"handle @{normalized_handle}")


def get_channel_by_username(
    username: str,
    *,
    api_key: str | None = None,
) -> YouTubeChannel:
    service = _service(api_key)
    try:
        response = (
            service.channels()
            .list(part="snippet,contentDetails", forUsername=username)
            .execute()
        )
    except HttpError as exc:  # pragma: no cover - network/runtime dependent
        raise YouTubeApiError(str(exc)) from exc
    return _channel_from_response(response, label=f"username {username}")


def iter_uploads(
    uploads_playlist_id: str,
    *,
    api_key: str | None = None,
    page_size: int = 50,
    limit: int | None = None,
) -> Iterator[YouTubeUpload]:
    service = _service(api_key)
    next_page_token: str | None = None
    remaining = limit

    while True:
        batch_size = page_size
        if remaining is not None:
            if remaining <= 0:
                break
            batch_size = min(batch_size, remaining)
        request = service.playlistItems().list(
            part="snippet,contentDetails",
            playlistId=uploads_playlist_id,
            maxResults=batch_size,
            pageToken=next_page_token,
        )
        try:
            response = request.execute()
        except HttpError as exc:  # pragma: no cover - network/runtime dependent
            raise YouTubeApiError(str(exc)) from exc

        items = response.get("items", [])
        if not isinstance(items, list):
            raise YouTubeApiError("playlistItems.list returned a malformed response")

        for raw_item in items:
            upload = _upload_from_response_item(raw_item)
            if upload is None:
                continue
            yield upload
            if remaining is not None:
                remaining -= 1
                if remaining <= 0:
                    return

        next_page_token = response.get("nextPageToken")
        if not isinstance(next_page_token, str) or not next_page_token:
            break


def _api_key(explicit_api_key: str | None = None) -> str:
    api_key = explicit_api_key or os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        raise YouTubeApiError("Missing YOUTUBE_API_KEY")
    return api_key


def _service(api_key: str | None = None):
    return build(
        "youtube",
        "v3",
        developerKey=_api_key(api_key),
        cache_discovery=False,
    )


def _channel_from_video_id(
    video_id: str,
    *,
    api_key: str | None = None,
) -> YouTubeChannel:
    service = _service(api_key)
    try:
        response = service.videos().list(part="snippet", id=video_id).execute()
    except HttpError as exc:  # pragma: no cover - network/runtime dependent
        raise YouTubeApiError(str(exc)) from exc

    items = response.get("items", [])
    if not isinstance(items, list) or not items:
        raise YouTubeApiError(f"No video found for id {video_id}")

    first = items[0]
    if not isinstance(first, dict):
        raise YouTubeApiError("videos.list returned a malformed response")

    snippet = first.get("snippet")
    if not isinstance(snippet, dict):
        raise YouTubeApiError("videos.list response was missing snippet")

    channel_id = snippet.get("channelId")
    if not isinstance(channel_id, str) or not channel_id:
        raise YouTubeApiError("videos.list response was missing snippet.channelId")

    return get_channel(channel_id, api_key=api_key)


def _channel_from_response(response: object, *, label: str) -> YouTubeChannel:
    if not isinstance(response, dict):
        raise YouTubeApiError("YouTube API returned a malformed response")

    items = response.get("items", [])
    if not isinstance(items, list) or not items:
        raise YouTubeApiError(f"No channel found for {label}")

    first = items[0]
    if not isinstance(first, dict):
        raise YouTubeApiError("YouTube API returned a malformed channel entry")

    channel_id = first.get("id")
    snippet = first.get("snippet")
    content_details = first.get("contentDetails")
    if not isinstance(channel_id, str) or not channel_id:
        raise YouTubeApiError("channels.list response was missing id")
    if not isinstance(snippet, dict):
        raise YouTubeApiError("channels.list response was missing snippet")
    if not isinstance(content_details, dict):
        raise YouTubeApiError("channels.list response was missing contentDetails")

    related_playlists = content_details.get("relatedPlaylists")
    if not isinstance(related_playlists, dict):
        raise YouTubeApiError(
            "channels.list response was missing contentDetails.relatedPlaylists"
        )

    uploads_playlist_id = related_playlists.get("uploads")
    if not isinstance(uploads_playlist_id, str) or not uploads_playlist_id:
        raise YouTubeApiError("Channel did not expose an uploads playlist")

    title = snippet.get("title")
    if not isinstance(title, str) or not title.strip():
        title = None

    return YouTubeChannel(
        channel_id=channel_id,
        title=title,
        uploads_playlist_id=uploads_playlist_id,
    )


def _upload_from_response_item(raw_item: object) -> YouTubeUpload | None:
    if not isinstance(raw_item, dict):
        return None

    snippet = raw_item.get("snippet")
    content_details = raw_item.get("contentDetails")
    if not isinstance(snippet, dict) or not isinstance(content_details, dict):
        return None

    video_id = content_details.get("videoId")
    if not isinstance(video_id, str) or not video_id:
        resource_id = snippet.get("resourceId")
        if isinstance(resource_id, dict):
            maybe_video_id = resource_id.get("videoId")
            if isinstance(maybe_video_id, str) and maybe_video_id:
                video_id = maybe_video_id
    if not video_id:
        return None

    title = snippet.get("title")
    if not isinstance(title, str) or not title.strip():
        title = "(untitled video)"

    description = snippet.get("description")
    if not isinstance(description, str):
        description = None

    channel_title = snippet.get("videoOwnerChannelTitle")
    if not isinstance(channel_title, str) or not channel_title.strip():
        channel_title = snippet.get("channelTitle")
    if not isinstance(channel_title, str) or not channel_title.strip():
        channel_title = None

    return YouTubeUpload(
        video_id=video_id,
        title=title,
        description=description,
        channel_title=channel_title,
        published_at=_parse_dt(snippet.get("publishedAt")),
        canonical_url=f"https://www.youtube.com/watch?v={video_id}",
    )


def _channel_id_from_feed_url(url: str) -> str | None:
    parsed = urlparse(url)
    if not looks_like_youtube_url(url) or parsed.path != "/feeds/videos.xml":
        return None
    values = parse_qs(parsed.query)
    channel_ids = values.get("channel_id", [])
    if not channel_ids:
        return None
    channel_id = channel_ids[0].strip()
    return channel_id or None


def _channel_id_from_channel_url(url: str) -> str | None:
    parsed = urlparse(url)
    if not looks_like_youtube_url(url):
        return None
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) >= 2 and path_parts[0] == "channel":
        channel_id = path_parts[1].strip()
        return channel_id or None
    return None


def _handle_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    if not looks_like_youtube_url(url):
        return None
    path_parts = [part for part in parsed.path.split("/") if part]
    if path_parts and path_parts[0].startswith("@"):
        handle = path_parts[0].lstrip("@").strip()
        return handle or None
    return None


def _username_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    if not looks_like_youtube_url(url):
        return None
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) >= 2 and path_parts[0] == "user":
        username = path_parts[1].strip()
        return username or None
    return None


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
    ):
        try:
            return datetime.strptime(value, fmt).astimezone(timezone.utc)
        except ValueError:
            continue
    return None
