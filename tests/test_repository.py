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
