from datetime import datetime, timezone
from pathlib import Path

from laminar.models import NormalizedItem
from laminar.repository import Repository


def test_dedupes_by_canonical_url(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "laminar.db")
    first = NormalizedItem(
        source_id="blog-1",
        item_type="blog",
        external_id="1",
        canonical_url="https://example.com/post",
        title="First title",
        author="Author",
        published_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
        excerpt="One",
        content_text="One",
    )
    second = NormalizedItem(
        source_id="blog-2",
        item_type="blog",
        external_id="2",
        canonical_url="https://example.com/post",
        title="Updated title",
        author="Author",
        published_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
        excerpt="Two",
        content_text="Two",
    )

    inserted_first = repo.upsert_item(first)
    inserted_second = repo.upsert_item(second)
    items = repo.list_items(limit=10)

    assert inserted_first is True
    assert inserted_second is False
    assert len(items) == 1
    assert items[0].title == "Updated title"


def test_search_uses_content_text(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "laminar.db")
    repo.upsert_item(
        NormalizedItem(
            source_id="yt-1",
            item_type="video",
            external_id="abc123",
            canonical_url="https://youtube.com/watch?v=abc123",
            title="Release notes",
            author="Channel",
            published_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
            excerpt="Video summary",
            content_text="The transcript mentions sqlite vectors later",
        )
    )

    results = repo.search("sqlite")

    assert len(results) == 1
    assert results[0].item_type == "video"


def test_persists_youtube_transcript_metadata_and_segments(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "laminar.db")
    transcript_segments = [
        {
            "text": "So this is nothing new",
            "start": 0.0,
            "duration": 2.4,
            "timestamp": "0:00",
        },
        {
            "text": "and we have seen this setup before",
            "start": 2.4,
            "duration": 2.7,
            "timestamp": "0:02",
        },
    ]
    repo.upsert_item(
        NormalizedItem(
            source_id="yt-direct-1",
            item_type="video",
            external_id="xbDfIZIB0NQ",
            canonical_url="https://www.youtube.com/watch?v=xbDfIZIB0NQ",
            title="Iran War Accelerates Wealth Shift",
            author="Sean Foo Gold",
            published_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
            excerpt="Direct video transcript ingest",
            content_text=(
                "So this is nothing new\n"
                "and we have seen this setup before"
            ),
            content_status="available",
            content_language="en",
            content_source="youtube_transcript_api_generated",
            raw_payload={
                "video_id": "xbDfIZIB0NQ",
                "transcript_is_generated": True,
                "transcript_segments": transcript_segments,
            },
        )
    )

    stored = repo.get_item(1)

    assert stored is not None
    assert stored.item_type == "video"
    assert stored.content_language == "en"
    assert stored.content_source == "youtube_transcript_api_generated"
    assert "nothing new" in (stored.content_text or "")
    assert stored.raw_payload["video_id"] == "xbDfIZIB0NQ"
    assert stored.raw_payload["transcript_is_generated"] is True
    assert stored.raw_payload["transcript_segments"][0]["timestamp"] == "0:00"
