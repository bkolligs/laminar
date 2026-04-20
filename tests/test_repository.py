import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from laminar.models import NormalizedItem, SourceConfig
from laminar.repository import Repository


def test_persists_source_config_round_trip(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "laminar.db")
    repo.upsert_source(
        SourceConfig(
            id="yt-1",
            kind="youtube",
            name="Example Channel",
            enabled=False,
            paid=True,
            feed_url="https://www.youtube.com/feeds/videos.xml?channel_id=123",
            transcript_languages=["en", "es"],
            metadata={"region": "us"},
        )
    )

    sources = repo.list_sources()

    assert len(sources) == 1
    assert sources[0].id == "yt-1"
    assert sources[0].enabled is False
    assert sources[0].paid is True
    assert sources[0].transcript_languages == ["en", "es"]
    assert sources[0].metadata["region"] == "us"


def test_records_last_successful_scan_time(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "laminar.db")
    repo.upsert_source(SourceConfig(id="feed-1", kind="feed", name="Example Feed"))

    scanned_at = datetime(2026, 4, 14, 18, 30, tzinfo=timezone.utc)
    repo.mark_source_scan_succeeded("feed-1", scanned_at)

    assert repo.last_successful_scan_at("feed-1") == scanned_at


def test_get_source_returns_full_source_details(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "laminar.db")
    scanned_at = datetime(2026, 4, 14, 18, 30, tzinfo=timezone.utc)
    repo.upsert_source(
        SourceConfig(
            id="yt-1",
            kind="youtube",
            name="Example Channel",
            enabled=False,
            paid=True,
            feed_url="https://www.youtube.com/feeds/videos.xml?channel_id=123",
            handle="@example",
            transcript_languages=["en", "es"],
            metadata={"region": "us"},
        )
    )
    repo.mark_source_scan_succeeded("yt-1", scanned_at)

    source = repo.get_source("yt-1")

    assert source is not None
    assert source.id == "yt-1"
    assert source.name == "Example Channel"
    assert source.enabled is False
    assert source.paid is True
    assert source.feed_url == "https://www.youtube.com/feeds/videos.xml?channel_id=123"
    assert source.handle == "@example"
    assert source.transcript_languages == ["en", "es"]
    assert source.metadata == {"region": "us"}
    assert source.last_successful_scan_at == scanned_at


def test_stats_reports_totals_by_source_and_kind(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "laminar.db")
    repo.upsert_source(SourceConfig(id="feed-1", kind="feed", name="Example Feed"))
    repo.upsert_source(SourceConfig(id="yt-1", kind="youtube", name="Example Channel"))
    repo.upsert_source(
        SourceConfig(
            id="x-1",
            kind="x",
            name="Example X",
            paid=True,
            enabled=False,
        )
    )
    repo.upsert_item(
        NormalizedItem(
            source_id="feed-1",
            item_type="feed",
            external_id="post-1",
            canonical_url="https://example.com/post-1",
            title="Post One",
            author="Author",
            published_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
            excerpt="One",
            content_text="One",
        )
    )
    repo.upsert_item(
        NormalizedItem(
            source_id="feed-1",
            item_type="feed",
            external_id="post-2",
            canonical_url="https://example.com/post-2",
            title="Post Two",
            author="Author",
            published_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
            excerpt="Two",
            content_text="Two",
        )
    )
    repo.upsert_item(
        NormalizedItem(
            source_id="yt-1",
            item_type="video",
            external_id="video-1",
            canonical_url="https://youtube.com/watch?v=video-1",
            title="Video One",
            author="Channel",
            published_at=datetime(2026, 4, 16, tzinfo=timezone.utc),
            excerpt="Video",
            content_text="Transcript",
        )
    )

    stats = repo.stats()
    sources_by_id = {source.source_id: source for source in stats.sources}
    kinds_by_name = {kind.kind: kind for kind in stats.kinds}

    assert stats.total_sources == 3
    assert stats.total_items == 3
    assert stats.total_size_bytes > 0
    assert [(source.source_id, source.item_count) for source in stats.sources] == [
        ("feed-1", 2),
        ("yt-1", 1),
        ("x-1", 0),
    ]
    assert {
        kind.kind: (kind.source_count, kind.item_count) for kind in stats.kinds
    } == {
        "feed": (1, 2),
        "x": (1, 0),
        "youtube": (1, 1),
    }
    assert sources_by_id["feed-1"].size_bytes > sources_by_id["yt-1"].size_bytes > 0
    assert sources_by_id["x-1"].size_bytes == 0
    assert kinds_by_name["feed"].size_bytes == sources_by_id["feed-1"].size_bytes
    assert kinds_by_name["youtube"].size_bytes == sources_by_id["yt-1"].size_bytes
    assert kinds_by_name["x"].size_bytes == 0
    assert stats.total_size_bytes == sum(source.size_bytes for source in stats.sources)


def test_stats_include_items_without_matching_source(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "laminar.db")
    repo.upsert_source(SourceConfig(id="feed-1", kind="feed", name="Example Feed"))
    repo.upsert_item(
        NormalizedItem(
            source_id="feed-1",
            item_type="feed",
            external_id="post-1",
            canonical_url="https://example.com/post-1",
            title="Post One",
            author="Author",
            published_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
            excerpt="One",
            content_text="One",
        )
    )
    repo.upsert_item(
        NormalizedItem(
            source_id="missing-source",
            item_type="feed",
            external_id="orphan-1",
            canonical_url="https://example.com/orphan-1",
            title="Orphaned Post",
            author="Unknown",
            published_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
            excerpt="Missing source",
            content_text="Missing source content",
        )
    )

    stats = repo.stats()
    sources_by_id = {source.source_id: source for source in stats.sources}
    kinds_by_name = {kind.kind: kind for kind in stats.kinds}

    assert stats.total_sources == 1
    assert stats.total_items == 2
    assert "missing-source" in sources_by_id
    assert sources_by_id["missing-source"].name == "[missing source]"
    assert sources_by_id["missing-source"].kind == "missing"
    assert sources_by_id["missing-source"].enabled is False
    assert sources_by_id["missing-source"].paid is False
    assert sources_by_id["missing-source"].item_count == 1
    assert sources_by_id["missing-source"].size_bytes > 0
    assert kinds_by_name["missing"].source_count == 1
    assert kinds_by_name["missing"].item_count == 1
    assert kinds_by_name["missing"].size_bytes == sources_by_id["missing-source"].size_bytes


def test_remove_source_requires_recursive_when_items_exist(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "laminar.db")
    repo.upsert_source(SourceConfig(id="feed-1", kind="feed", name="Example Feed"))
    repo.upsert_item(
        NormalizedItem(
            source_id="feed-1",
            item_type="feed",
            external_id="post-1",
            canonical_url="https://example.com/post-1",
            title="Post One",
            author="Author",
            published_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
            excerpt="One",
            content_text="One",
        )
    )

    with pytest.raises(
        ValueError,
        match="still has 1 items; rerun with --recursive",
    ):
        repo.remove_source("feed-1")


def test_remove_source_recursive_deletes_source_items_and_scans(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "laminar.db")
    repo.upsert_source(SourceConfig(id="feed-1", kind="feed", name="Example Feed"))
    scan_run_id = repo.start_scan_run(
        include_paid=False,
        selected_source_kinds=[],
        selected_source_ids=[],
    )
    repo.record_scan_source(
        scan_run_id,
        source=SourceConfig(id="feed-1", kind="feed", name="Example Feed"),
        status="success",
        items_seen=0,
        items_new=0,
        items_existing=0,
    )
    repo.upsert_item(
        NormalizedItem(
            source_id="feed-1",
            item_type="feed",
            external_id="post-1",
            canonical_url="https://example.com/post-1",
            title="Shared Title",
            author="Author",
            published_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
            excerpt="One",
            content_text="One",
        )
    )
    repo.upsert_source(SourceConfig(id="feed-2", kind="feed", name="Other Feed"))
    repo.upsert_item(
        NormalizedItem(
            source_id="feed-2",
            item_type="feed",
            external_id="post-2",
            canonical_url="https://example.com/post-2",
            title="Shared Title",
            author="Author",
            published_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
            excerpt="Two",
            content_text="Two",
        )
    )

    removed_items = repo.remove_source("feed-1", recursive=True)

    assert removed_items == 1
    assert [source.id for source in repo.list_sources()] == ["feed-2"]
    items = repo.list_items(limit=10)
    assert len(items) == 1
    assert items[0].source_id == "feed-2"
    matches = repo.find_items_by_title("Shared Title")
    assert len(matches) == 1
    assert matches[0].source_id == "feed-2"
    with repo.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM scans WHERE source_id = ?",
            ("feed-1",),
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM scan_sources WHERE source_id = ?",
            ("feed-1",),
        ).fetchone()[0] == 0


def test_scan_history_records_run_source_and_item_details(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "laminar.db")
    source = SourceConfig(id="feed-1", kind="feed", name="Example Feed")
    repo.upsert_source(source)
    run_started_at = datetime(2026, 4, 18, 12, 0, tzinfo=timezone.utc)
    source_started_at = datetime(2026, 4, 18, 12, 1, tzinfo=timezone.utc)
    source_finished_at = datetime(2026, 4, 18, 12, 2, tzinfo=timezone.utc)
    run_finished_at = datetime(2026, 4, 18, 12, 3, tzinfo=timezone.utc)
    cutoff_at = datetime(2026, 4, 17, 8, 0, tzinfo=timezone.utc)

    scan_run_id = repo.start_scan_run(
        include_paid=True,
        selected_source_kinds=["feed"],
        selected_source_ids=["feed-1"],
        started_at=run_started_at,
    )
    scan_source_id = repo.record_scan_source(
        scan_run_id,
        source=source,
        status="success",
        started_at=source_started_at,
        finished_at=source_finished_at,
        cutoff_at=cutoff_at,
        items_seen=2,
        items_new=1,
        items_existing=1,
    )
    repo.record_scan_item(
        scan_source_id,
        NormalizedItem(
            item_id="11111111-1111-4111-8111-111111111111",
            source_id="feed-1",
            item_type="feed",
            external_id="post-1",
            canonical_url="https://example.com/post-1",
            title="Brand New",
            author="Author",
            published_at=datetime(2026, 4, 18, 11, 0, tzinfo=timezone.utc),
            excerpt="One",
            content_text="One",
        ),
        result="new",
    )
    repo.record_scan_item(
        scan_source_id,
        NormalizedItem(
            item_id="22222222-2222-4222-8222-222222222222",
            source_id="feed-1",
            item_type="feed",
            external_id="post-2",
            canonical_url="https://example.com/post-2",
            title="Already Seen",
            author="Author",
            published_at=datetime(2026, 4, 18, 10, 0, tzinfo=timezone.utc),
            excerpt="Two",
            content_text="Two",
            content_status="missing",
            content_source="rss",
        ),
        result="existing",
    )
    repo.finish_scan_run(
        scan_run_id,
        status="success",
        sources_considered=1,
        sources_scanned=1,
        sources_skipped=0,
        sources_failed=0,
        items_seen=2,
        items_new=1,
        items_existing=1,
        finished_at=run_finished_at,
    )

    runs = repo.list_scan_runs(limit=10)
    assert len(runs) == 1
    assert runs[0].scan_run_id == scan_run_id
    assert runs[0].status == "success"
    assert runs[0].include_paid is True
    assert runs[0].selected_source_kinds == ["feed"]
    assert runs[0].selected_source_ids == ["feed-1"]
    assert runs[0].items_existing == 1

    detail = repo.get_scan_run(scan_run_id)

    assert detail is not None
    assert detail.run.started_at == run_started_at
    assert detail.run.finished_at == run_finished_at
    assert len(detail.sources) == 1
    assert detail.sources[0].source_id == "feed-1"
    assert detail.sources[0].cutoff_at == cutoff_at
    assert detail.sources[0].items_new == 1
    assert detail.sources[0].items_existing == 1
    assert len(detail.items) == 2
    assert [item.result for item in detail.items] == ["new", "existing"]
    assert detail.items[1].content_status.value == "missing"


def test_scan_history_persists_skipped_and_failed_sources(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "laminar.db")
    skipped = SourceConfig(id="x-1", kind="x", name="Example X", paid=True)
    failed = SourceConfig(id="feed-1", kind="feed", name="Broken Feed")
    repo.upsert_source(skipped)
    repo.upsert_source(failed)

    scan_run_id = repo.start_scan_run(
        include_paid=False,
        selected_source_kinds=["feed", "x"],
        selected_source_ids=[],
    )
    repo.record_scan_source(
        scan_run_id,
        source=skipped,
        status="skipped",
        skip_reason="paid_not_included",
        items_seen=0,
        items_new=0,
        items_existing=0,
    )
    repo.record_scan_source(
        scan_run_id,
        source=failed,
        status="failed",
        items_seen=0,
        items_new=0,
        items_existing=0,
        error="HTTP Error 404: Not Found",
    )
    repo.finish_scan_run(
        scan_run_id,
        status="partial_failure",
        sources_considered=2,
        sources_scanned=1,
        sources_skipped=1,
        sources_failed=1,
        items_seen=0,
        items_new=0,
        items_existing=0,
    )

    detail = repo.get_scan_run(scan_run_id)

    assert detail is not None
    assert detail.run.status == "partial_failure"
    assert [source.status for source in detail.sources] == ["skipped", "failed"]
    assert detail.sources[0].skip_reason == "paid_not_included"
    assert detail.sources[1].error == "HTTP Error 404: Not Found"


def test_dedupes_by_canonical_url(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "laminar.db")
    first = NormalizedItem(
        source_id="feed-1",
        item_type="feed",
        external_id="1",
        canonical_url="https://example.com/post",
        title="First title",
        author="Author",
        published_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
        excerpt="One",
        content_text="One",
    )
    second = NormalizedItem(
        source_id="feed-2",
        item_type="feed",
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


def test_remove_item_deletes_content_and_refreshes_title_map(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "laminar.db")
    repo.upsert_source(SourceConfig(id="yt-1", kind="youtube", name="Channel One"))
    repo.upsert_source(SourceConfig(id="yt-2", kind="youtube", name="Channel Two"))
    repo.upsert_item(
        NormalizedItem(
            item_id="item-1",
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
            item_id="item-2",
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

    assert repo.remove_item("item-1") is True
    assert repo.get_item("item-1") is None
    assert repo.find_items_by_title("Daily Briefing (Channel One)") == []
    matches = repo.find_items_by_title("Daily Briefing")
    assert len(matches) == 1
    assert matches[0].item_id == "item-2"

    with repo.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM item_contents WHERE item_id = ?",
            ("item-1",),
        ).fetchone()[0] == 0


def test_remove_item_returns_false_when_missing(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "laminar.db")

    assert repo.remove_item("missing-item") is False


def test_title_map_updates_when_item_title_changes(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "laminar.db")
    repo.upsert_source(
        SourceConfig(
            id="yt-1",
            kind="youtube",
            name="Channel One",
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
    repo.upsert_source(SourceConfig(id="yt-1", kind="youtube", name="Channel One"))
    repo.upsert_source(SourceConfig(id="yt-2", kind="youtube", name="Channel Two"))
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
