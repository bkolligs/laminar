from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from uuid import UUID

from laminar.models import NormalizedItem, SourceConfig
from laminar.repository import Repository


def test_persists_source_config_round_trip(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "laminar.db")
    repo.upsert_source(
        SourceConfig(
            id="yt-1",
            kind="youtube",
            label="Example Channel",
            enabled=False,
            costs_money=True,
            feed_url="https://www.youtube.com/feeds/videos.xml?channel_id=123",
            command=["unused"],
            transcript_languages=["en", "es"],
            poll_interval_minutes=30,
            metadata={"region": "us"},
        )
    )

    sources = repo.list_sources()

    assert len(sources) == 1
    assert sources[0].id == "yt-1"
    assert sources[0].enabled is False
    assert sources[0].costs_money is True
    assert sources[0].transcript_languages == ["en", "es"]
    assert sources[0].poll_interval_minutes == 30
    assert sources[0].metadata["region"] == "us"


def test_adds_costs_money_column_for_existing_databases(tmp_path: Path) -> None:
    db_path = tmp_path / "laminar.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE sources (
            source_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            label TEXT NOT NULL,
            enabled INTEGER NOT NULL,
            provider TEXT,
            feed_url TEXT,
            handle TEXT,
            command_json TEXT NOT NULL DEFAULT '[]',
            transcript_languages_json TEXT NOT NULL DEFAULT '[]',
            poll_interval_minutes INTEGER,
            metadata_json TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.commit()
    conn.close()

    repo = Repository(db_path)
    repo.upsert_source(SourceConfig(id="x-1", kind="x", label="Paid X", costs_money=True))

    sources = repo.list_sources()

    assert len(sources) == 1
    assert sources[0].costs_money is True


def test_adds_last_successful_scan_column_for_existing_databases(tmp_path: Path) -> None:
    db_path = tmp_path / "laminar.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE sources (
            source_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            label TEXT NOT NULL,
            enabled INTEGER NOT NULL,
            costs_money INTEGER NOT NULL DEFAULT 0,
            provider TEXT,
            feed_url TEXT,
            handle TEXT,
            command_json TEXT NOT NULL DEFAULT '[]',
            transcript_languages_json TEXT NOT NULL DEFAULT '[]',
            poll_interval_minutes INTEGER,
            metadata_json TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.commit()
    conn.close()

    repo = Repository(db_path)

    with repo.connect() as conn:
        columns = {
            str(row["name"]) for row in conn.execute("PRAGMA table_info(sources)").fetchall()
        }

    assert "last_successful_scan_at" in columns


def test_x_sources_are_treated_as_paid_when_loading_legacy_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "laminar.db"
    repo = Repository(db_path)
    with repo.connect() as conn:
        conn.execute(
            """
            INSERT INTO sources (
                source_id, kind, label, enabled, costs_money, provider, feed_url, handle,
                command_json, transcript_languages_json, poll_interval_minutes, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "x-legacy",
                "x",
                "Jeremy Howard",
                1,
                0,
                None,
                None,
                "jeremyphoward",
                "[]",
                "[]",
                None,
                "{}",
            ),
        )

    sources = repo.list_sources()

    assert len(sources) == 1
    assert sources[0].kind == "x"
    assert sources[0].costs_money is True


def test_records_last_successful_scan_time(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "laminar.db")
    repo.upsert_source(SourceConfig(id="blog-1", kind="blog", label="Example Blog"))

    scanned_at = datetime(2026, 4, 14, 18, 30, tzinfo=timezone.utc)
    repo.mark_source_scan_succeeded("blog-1", scanned_at)

    assert repo.last_successful_scan_at("blog-1") == scanned_at


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


def test_refreshes_canonical_url_when_existing_item_is_updated(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "laminar.db")
    repo.upsert_item(
        NormalizedItem(
            source_id="x-1",
            item_type="x_post",
            external_id="tweet-1",
            canonical_url=None,
            title="Initial title",
            author="example",
            published_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
            excerpt="One",
            content_text="One",
        )
    )

    repo.upsert_item(
        NormalizedItem(
            source_id="x-1",
            item_type="x_post",
            external_id="tweet-1",
            canonical_url="https://x.com/example/status/tweet-1",
            title="Initial title",
            author="example",
            published_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
            excerpt="One",
            content_text="One",
        )
    )

    items = repo.list_items(limit=10)

    assert len(items) == 1
    assert items[0].canonical_url == "https://x.com/example/status/tweet-1"


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
    assert UUID(results[0].item_id).version == 4


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

    items = repo.list_items(limit=10)
    assert len(items) == 1
    stored = repo.get_item(items[0].item_id)

    assert stored is not None
    assert stored.item_type == "video"
    assert UUID(stored.item_id).version == 4
    assert stored.content_language == "en"
    assert stored.content_source == "youtube_transcript_api_generated"
    assert "nothing new" in (stored.content_text or "")
    assert stored.raw_payload["video_id"] == "xbDfIZIB0NQ"
    assert stored.raw_payload["transcript_is_generated"] is True
    assert stored.raw_payload["transcript_segments"][0]["timestamp"] == "0:00"


def test_find_items_by_title_returns_exact_matches(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "laminar.db")
    repo.upsert_item(
        NormalizedItem(
            source_id="yt-1",
            item_type="video",
            external_id="abc123",
            canonical_url="https://youtube.com/watch?v=abc123",
            title="Daily Briefing",
            author="Channel",
            published_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
            excerpt="One",
            content_text="One",
        )
    )
    repo.upsert_item(
        NormalizedItem(
            source_id="yt-1",
            item_type="video",
            external_id="abc124",
            canonical_url="https://youtube.com/watch?v=abc124",
            title="Other Video",
            author="Channel",
            published_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
            excerpt="Two",
            content_text="Two",
        )
    )

    matches = repo.find_items_by_title("Daily Briefing")

    assert len(matches) == 1
    assert matches[0].title == "Daily Briefing"


def test_title_map_updates_when_item_title_changes(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "laminar.db")
    repo.upsert_source(
        SourceConfig(
            id="yt-1",
            kind="youtube",
            label="Channel One",
        )
    )
    item = NormalizedItem(
        source_id="yt-1",
        item_type="video",
        external_id="abc123",
        canonical_url="https://youtube.com/watch?v=abc123",
        title="Old Title",
        author="Channel",
        published_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
        excerpt="One",
        content_text="One",
    )
    repo.upsert_item(item)

    updated = NormalizedItem(
        item_id=item.item_id,
        source_id="yt-1",
        item_type="video",
        external_id="abc123",
        canonical_url="https://youtube.com/watch?v=abc123",
        title="New Title",
        author="Channel",
        published_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
        excerpt="One",
        content_text="One",
    )
    repo.upsert_item(updated)

    assert repo.find_items_by_title("Old Title") == []
    matches = repo.find_items_by_title("New Title")
    assert len(matches) == 1
    assert matches[0].item_id == item.item_id


def test_title_map_uses_source_name_for_collisions(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "laminar.db")
    repo.upsert_source(SourceConfig(id="yt-1", kind="youtube", label="Channel One"))
    repo.upsert_source(SourceConfig(id="yt-2", kind="youtube", label="Channel Two"))
    repo.upsert_item(
        NormalizedItem(
            source_id="yt-1",
            item_type="video",
            external_id="abc123",
            canonical_url="https://youtube.com/watch?v=abc123",
            title="Daily Briefing",
            author="Channel One",
            published_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
            excerpt="One",
            content_text="One",
        )
    )
    repo.upsert_item(
        NormalizedItem(
            source_id="yt-2",
            item_type="video",
            external_id="abc124",
            canonical_url="https://youtube.com/watch?v=abc124",
            title="Daily Briefing",
            author="Channel Two",
            published_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
            excerpt="Two",
            content_text="Two",
        )
    )

    assert repo.find_items_by_title("Daily Briefing") == []
    channel_one = repo.find_items_by_title("Daily Briefing (Channel One)")
    channel_two = repo.find_items_by_title("Daily Briefing (Channel Two)")
    assert len(channel_one) == 1
    assert len(channel_two) == 1
    assert repo.lookup_titles_for_raw_title("Daily Briefing") == [
        "Daily Briefing (Channel One)",
        "Daily Briefing (Channel Two)",
    ]
