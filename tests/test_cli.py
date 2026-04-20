import io
import json
import subprocess
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from uuid import UUID

import pytest
import yaml

import laminar.adapters as adapters
import laminar.cli as cli
from laminar.cli import build_parser, run
from laminar.config import ConfigError
from laminar.models import ContentStatus, NormalizedItem, SourceConfig
from laminar.repository import Repository
from laminar.youtube import TranscriptResult, TranscriptSegment, TranscriptUnavailable


def test_scan_and_query(tmp_path: Path) -> None:
    blog_feed = tmp_path / "blog.xml"
    blog_feed.write_text(
        """
        <rss version="2.0">
          <channel>
            <title>Example Blog</title>
            <item>
              <title>SQLite for Feeds</title>
              <link>https://example.com/sqlite-feeds</link>
              <guid>post-1</guid>
              <pubDate>Tue, 14 Apr 2026 12:00:00 +0000</pubDate>
              <description>Finite feed retrieval.</description>
            </item>
          </channel>
        </rss>
        """
    )
    yt_feed = tmp_path / "youtube.xml"
    yt_feed.write_text(
        """
        <feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">
            <title>Example Channel</title>
          <entry>
            <yt:videoId>abc123</yt:videoId>
            <title>Daily Briefing</title>
            <link rel="alternate" href="https://www.youtube.com/watch?v=abc123"/>
            <published>2026-04-14T12:00:00+00:00</published>
            <author><name>Example Channel</name></author>
          </entry>
        </feed>
        """
    )
    x_payload = tmp_path / "x.json"
    x_payload.write_text(
        json.dumps(
            {
                "data": [
                    {
                        "id": "12345",
                        "author_id": "u1",
                        "text": "Short post on markets",
                        "created_at": "2026-04-14T15:00:00Z",
                    }
                ],
                "includes": {"users": [{"id": "u1", "username": "example"}]},
            }
        )
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text("database_path: laminar.db\n")
    db_path = tmp_path / "laminar.db"
    parser = build_parser()

    original_fetch = adapters.fetch_transcript

    def fake_fetch_transcript(
        video_id_or_url: str,
        languages: list[str] | None = None,
    ) -> TranscriptResult:
        assert video_id_or_url == "abc123"
        assert languages == ["en"]
        return TranscriptResult(
            text="market update\nfinite daily summary",
            language_code="en",
            language_name="English",
            source="youtube_transcript_api_manual",
            is_generated=False,
            segments=[
                TranscriptSegment(
                    text="market update",
                    start=0.0,
                    duration=1.0,
                    timestamp="0:00",
                ),
                TranscriptSegment(
                    text="finite daily summary",
                    start=1.0,
                    duration=1.0,
                    timestamp="0:01",
                ),
            ],
        )

    adapters.fetch_transcript = fake_fetch_transcript
    original_run = adapters.subprocess.run

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command == ["xurl", "https://x.com/example"]
        return subprocess.CompletedProcess(command, 0, stdout=x_payload.read_text(), stderr="")

    adapters.subprocess.run = fake_run
    try:
        with redirect_stdout(io.StringIO()):
            for args_list in (
            [
                "--config",
                str(config_path),
                "--db",
                str(db_path),
                "source",
                "add",
                "--name",
                "Example Feed",
                blog_feed.as_uri(),
            ],
            [
                "--config",
                str(config_path),
                "--db",
                str(db_path),
                "source",
                "add",
                "--type",
                "youtube",
                "--name",
                "Example Channel",
                yt_feed.as_uri(),
                "--transcript-language",
                "en",
            ],
            [
                "--config",
                str(config_path),
                "--db",
                str(db_path),
                "source",
                "add",
                "--type",
                "x",
                "--name",
                "Example X",
                "--paid",
                "https://x.com/example",
            ],
            [
                "--config",
                str(config_path),
                "--db",
                str(db_path),
                "source",
                "validate",
            ],
            ["--config", str(config_path), "--db", str(db_path), "scan"],
            ):
                args = parser.parse_args(args_list)
                assert run(args) == 0
    finally:
        adapters.fetch_transcript = original_fetch
        adapters.subprocess.run = original_run

    list_buffer = io.StringIO()
    with redirect_stdout(list_buffer):
        list_args = parser.parse_args(["--db", str(db_path), "items", "list", "--limit", "10"])
        assert run(list_args) == 0
    list_output = list_buffer.getvalue()
    assert "Items" in list_output
    assert "Title" in list_output
    repo = Repository(db_path)
    assert any(item.title == "SQLite for Feeds" for item in repo.list_items(limit=10))
    video_item_id = next(
        item.item_id for item in repo.list_items(limit=10) if item.title == "Daily Briefing"
    )

    search_buffer = io.StringIO()
    with redirect_stdout(search_buffer):
        search_args = parser.parse_args(["--db", str(db_path), "search", "finite"])
        assert run(search_args) == 0
    assert "Daily Briefing" in search_buffer.getvalue()

    show_buffer = io.StringIO()
    with redirect_stdout(show_buffer):
        show_args = parser.parse_args(["--db", str(db_path), "items", "show", video_item_id])
        assert run(show_args) == 0
    shown = json.loads(show_buffer.getvalue())
    assert UUID(shown["item_id"]).version == 4
    assert shown["item_type"] == "video"
    assert "market update" in shown["content_text"]
    assert shown["content_source"] == "youtube_transcript_api_manual"
    assert shown["raw_payload"]["transcript_segments"][0]["timestamp"] == "0:00"
    assert any(source.paid for source in repo.list_sources() if source.kind == "x")

    source_id = next(
        source.id for source in repo.list_sources() if source.name == "Example Channel"
    )
    source_show_buffer = io.StringIO()
    with redirect_stdout(source_show_buffer):
        source_show_args = parser.parse_args(["--db", str(db_path), "source", "show", source_id])
        assert run(source_show_args) == 0
    source_shown = json.loads(source_show_buffer.getvalue())
    assert source_shown["source_id"] == source_id
    assert source_shown["kind"] == "youtube"
    assert source_shown["name"] == "Example Channel"
    assert source_shown["transcript_languages"] == ["en"]
    assert source_shown["item_count"] == 1
    assert source_shown["logical_item_size_bytes"] > 0

    stats_buffer = io.StringIO()
    with redirect_stdout(stats_buffer):
        stats_args = parser.parse_args(["--db", str(db_path), "stats"])
        assert run(stats_args) == 0
    stats_output = stats_buffer.getvalue()
    assert "Overview" in stats_output
    assert "Sources by Kind" in stats_output
    assert "Items by Source" in stats_output
    assert "Logical Item Size" in stats_output
    assert "3" in stats_output
    assert "Example Feed" in stats_output
    assert "Example Channel" in stats_output
    assert "Example X" in stats_output
    assert "youtube" in stats_output
    assert "B" in stats_output


def test_source_show_reports_missing_source(tmp_path: Path) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"

    result = run(parser.parse_args(["--db", str(db_path), "source", "show", "missing"]))

    assert result == 1


def test_stats_include_items_without_matching_source(tmp_path: Path) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"
    repo = Repository(db_path)
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

    with redirect_stdout(io.StringIO()) as buffer:
        args = parser.parse_args(["--db", str(db_path), "stats"])
        assert run(args) == 0

    output = buffer.getvalue()
    assert "missing-source" in output
    assert "missing" in output


def test_scan_continues_after_source_failure_and_reports_item_statuses(
    tmp_path: Path,
) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"
    config_path = tmp_path / "config.yaml"
    config_path.write_text("database_path: laminar.db\n")
    repo = Repository(db_path)

    failing = SourceConfig(
        id="bad-source",
        kind="feed",
        name="Broken Feed",
        feed_url="https://example.invalid/feed.xml",
    )
    healthy = SourceConfig(
        id="good-source",
        kind="x",
        name="Healthy Feed",
        paid=True,
        handle="healthy",
        feed_url="https://x.com/healthy",
    )
    repo.upsert_source(failing)
    repo.upsert_source(healthy)
    repo.upsert_item(
        NormalizedItem(
            source_id="good-source",
            item_type="x_post",
            external_id="old-1",
            canonical_url="https://x.com/healthy/status/old-1",
            title="Already seen",
            author="healthy",
            published_at=None,
            excerpt="seen before",
            content_text="seen before",
        )
    )

    original_build_adapter = cli.build_adapter

    class FailingAdapter:
        def scan(
            self,
            source: SourceConfig,
            *,
            since: datetime | None = None,
            verbose=None,
        ) -> list[NormalizedItem]:
            raise HTTPError(source.feed_url or "", 404, "Not Found", hdrs=None, fp=None)

    class HealthyAdapter:
        def scan(
            self,
            source: SourceConfig,
            *,
            since: datetime | None = None,
            verbose=None,
        ) -> list[NormalizedItem]:
            return [
                NormalizedItem(
                    source_id=source.id,
                    item_type="x_post",
                    external_id="old-1",
                    canonical_url="https://x.com/healthy/status/old-1",
                    title="Already seen",
                    author="healthy",
                    published_at=None,
                    excerpt="seen before",
                    content_text="seen before",
                ),
                NormalizedItem(
                    source_id=source.id,
                    item_type="x_post",
                    external_id="new-1",
                    canonical_url="https://x.com/healthy/status/new-1",
                    title="Brand new",
                    author="healthy",
                    published_at=None,
                    excerpt="brand new",
                    content_text="brand new",
                ),
            ]

    def fake_build_adapter(source: SourceConfig):
        if source.id == "bad-source":
            return FailingAdapter()
        if source.id == "good-source":
            return HealthyAdapter()
        return original_build_adapter(source)

    cli.build_adapter = fake_build_adapter
    try:
        with redirect_stdout(io.StringIO()) as buffer:
            args = parser.parse_args(
                ["--config", str(config_path), "--db", str(db_path), "scan", "--include-paid"]
            )
            assert run(args) == 0
    finally:
        cli.build_adapter = original_build_adapter

    output = buffer.getvalue()
    assert "Scanning bad-source (Broken Feed)" in output
    assert "bad-source: unreachable - HTTP Error 404: Not Found" in output
    assert "Scanning good-source (Healthy Feed)" in output
    assert "good-source: this source is marked paid" in output
    assert "good-source: reachable" in output
    assert "good-source: existing Already seen" in output
    assert "good-source: new Brand new" in output
    assert "scan complete: 2 items seen, 1 new, 1 failed, 0 skipped" in output
    items = repo.list_items(limit=10)
    assert any(item.title == "Brand new" for item in items)
    runs = repo.list_scan_runs(limit=10)
    assert len(runs) == 1
    assert runs[0].status == "partial_failure"
    assert runs[0].sources_considered == 2
    assert runs[0].sources_scanned == 2
    assert runs[0].sources_failed == 1
    assert runs[0].items_seen == 2
    assert runs[0].items_new == 1
    assert runs[0].items_existing == 1
    detail = repo.get_scan_run(runs[0].scan_run_id)
    assert detail is not None
    assert [source.status for source in detail.sources] == ["failed", "success"]
    assert detail.sources[0].error == "HTTP Error 404: Not Found"
    assert [item.result for item in detail.items] == ["existing", "new"]


def test_scan_skips_paid_sources_without_include_paid(tmp_path: Path) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"
    repo = Repository(db_path)
    repo.upsert_source(
        SourceConfig(
            id="paid-source",
            kind="x",
            name="Paid X",
            paid=True,
            handle="example",
            feed_url="https://x.com/example",
        )
    )

    original_build_adapter = cli.build_adapter

    class FailingIfCalledAdapter:
        def scan(
            self,
            source: SourceConfig,
            *,
            since: datetime | None = None,
            verbose=None,
        ) -> list[NormalizedItem]:
            raise AssertionError("paid source should have been skipped")

    def fake_build_adapter(source: SourceConfig):
        if source.id == "paid-source":
            return FailingIfCalledAdapter()
        return original_build_adapter(source)

    cli.build_adapter = fake_build_adapter
    try:
        with redirect_stdout(io.StringIO()) as buffer:
            args = parser.parse_args(["--db", str(db_path), "scan"])
            assert run(args) == 0
    finally:
        cli.build_adapter = original_build_adapter

    output = buffer.getvalue()
    assert "Skipping paid-source (Paid X): paid source; rerun with --include-paid" in output
    assert "Scanning paid-source" not in output
    assert "scan complete: 0 items seen, 0 new, 0 failed, 1 skipped" in output
    runs = repo.list_scan_runs(limit=10)
    assert len(runs) == 1
    assert runs[0].status == "success"
    assert runs[0].sources_considered == 1
    assert runs[0].sources_scanned == 0
    assert runs[0].sources_skipped == 1
    detail = repo.get_scan_run(runs[0].scan_run_id)
    assert detail is not None
    assert len(detail.sources) == 1
    assert detail.sources[0].status == "skipped"
    assert detail.sources[0].skip_reason == "paid_not_included"

def test_scans_list_and_show_report_history(tmp_path: Path) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"
    repo = Repository(db_path)
    source = SourceConfig(id="feed-1", kind="feed", name="Example Feed")
    repo.upsert_source(source)
    run_id = repo.start_scan_run(
        include_paid=False,
        selected_source_kinds=["feed"],
        selected_source_ids=["feed-1"],
        started_at=datetime(2026, 4, 18, 12, 0, tzinfo=timezone.utc),
    )
    scan_source_id = repo.record_scan_source(
        run_id,
        source=source,
        status="success",
        started_at=datetime(2026, 4, 18, 12, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 4, 18, 12, 1, tzinfo=timezone.utc),
        cutoff_at=datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc),
        items_seen=1,
        items_new=1,
        items_existing=0,
    )
    repo.record_scan_item(
        scan_source_id,
        NormalizedItem(
            item_id="11111111-1111-4111-8111-111111111111",
            source_id="feed-1",
            item_type="feed",
            external_id="post-1",
            canonical_url="https://example.com/post-1",
            title="Fresh Post",
            author="Author",
            published_at=datetime(2026, 4, 18, 11, 30, tzinfo=timezone.utc),
            excerpt="Fresh",
            content_text="Fresh",
        ),
        result="new",
    )
    repo.finish_scan_run(
        run_id,
        status="success",
        sources_considered=1,
        sources_scanned=1,
        sources_skipped=0,
        sources_failed=0,
        items_seen=1,
        items_new=1,
        items_existing=0,
        finished_at=datetime(2026, 4, 18, 12, 2, tzinfo=timezone.utc),
    )

    with redirect_stdout(io.StringIO()) as buffer:
        args = parser.parse_args(["--db", str(db_path), "scans", "list"])
        assert run(args) == 0
    output = buffer.getvalue()
    assert "Scans" in output
    assert "Run" in output
    assert "success" in output
    assert "feed" in output
    assert "1/1" in output
    assert "1 seen" in output
    assert "1 new" in output
    assert "0 existing" in output

    with redirect_stdout(io.StringIO()) as buffer:
        args = parser.parse_args(["--db", str(db_path), "scans", "show", str(run_id)])
        assert run(args) == 0
    payload = json.loads(buffer.getvalue())
    assert payload["run"]["scan_run_id"] == run_id
    assert payload["run"]["status"] == "success"
    assert payload["sources"][0]["source_id"] == "feed-1"
    assert payload["sources"][0]["cutoff_at"] == "2026-04-17T12:00:00+00:00"
    assert payload["items"][0]["title"] == "Fresh Post"
    assert payload["items"][0]["result"] == "new"


def test_scan_uses_last_successful_scan_time_for_incremental_blog_and_youtube(
    tmp_path: Path,
) -> None:
    blog_feed = tmp_path / "blog.xml"
    blog_feed.write_text(
        """
        <rss version="2.0">
          <channel>
            <title>Example Blog</title>
            <item>
              <title>New Post</title>
              <link>https://example.com/new-post</link>
              <guid>post-2</guid>
              <pubDate>Tue, 14 Apr 2026 13:00:00 +0000</pubDate>
              <description>Fresh item.</description>
            </item>
            <item>
              <title>Old Post</title>
              <link>https://example.com/old-post</link>
              <guid>post-1</guid>
              <pubDate>Tue, 14 Apr 2026 12:00:00 +0000</pubDate>
              <description>Existing item.</description>
            </item>
          </channel>
        </rss>
        """
    )
    yt_feed = tmp_path / "youtube.xml"
    yt_feed.write_text(
        """
        <feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">
          <title>Example Channel</title>
          <entry>
            <yt:videoId>new123</yt:videoId>
            <title>New Video</title>
            <link rel="alternate" href="https://www.youtube.com/watch?v=new123"/>
            <published>2026-04-14T13:00:00+00:00</published>
            <author><name>Example Channel</name></author>
          </entry>
          <entry>
            <yt:videoId>old123</yt:videoId>
            <title>Old Video</title>
            <link rel="alternate" href="https://www.youtube.com/watch?v=old123"/>
            <published>2026-04-14T12:00:00+00:00</published>
            <author><name>Example Channel</name></author>
          </entry>
        </feed>
        """
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text("database_path: laminar.db\n")
    db_path = tmp_path / "laminar.db"
    parser = build_parser()

    original_fetch = adapters.fetch_transcript
    fetch_calls: list[str] = []

    def fake_fetch_transcript(
        video_id_or_url: str,
        languages: list[str] | None = None,
    ) -> TranscriptResult:
        fetch_calls.append(video_id_or_url)
        return TranscriptResult(
            text=f"transcript for {video_id_or_url}",
            language_code="en",
            language_name="English",
            source="youtube_transcript_api_manual",
            is_generated=False,
            segments=[
                TranscriptSegment(
                    text=f"transcript for {video_id_or_url}",
                    start=0.0,
                    duration=1.0,
                    timestamp="0:00",
                )
            ],
        )

    adapters.fetch_transcript = fake_fetch_transcript
    try:
        with redirect_stdout(io.StringIO()):
            for args_list in (
                [
                "--config",
                str(config_path),
                "--db",
                str(db_path),
                "source",
                "add",
                "--name",
                "Example Feed",
                blog_feed.as_uri(),
            ],
            [
                "--config",
                str(config_path),
                "--db",
                str(db_path),
                "source",
                "add",
                "--type",
                "youtube",
                "--name",
                "Example Channel",
                yt_feed.as_uri(),
                "--transcript-language",
                "en",
            ],
            ):
                args = parser.parse_args(args_list)
                assert run(args) == 0

        repo = Repository(db_path)
        sources_by_label = {source.name: source.id for source in repo.list_sources()}
        cutoff = datetime(2026, 4, 14, 12, 30, tzinfo=timezone.utc)
        blog_source_id = sources_by_label["Example Feed"]
        youtube_source_id = sources_by_label["Example Channel"]
        repo.mark_source_scan_succeeded(blog_source_id, cutoff)
        repo.mark_source_scan_succeeded(youtube_source_id, cutoff)

        scan_buffer = io.StringIO()
        with redirect_stdout(scan_buffer):
            args = parser.parse_args(["--config", str(config_path), "--db", str(db_path), "scan"])
            assert run(args) == 0

        output = scan_buffer.getvalue()
        assert f"{blog_source_id}: new New Post" in output
        assert f"{blog_source_id}: new Old Post" not in output
        assert f"{youtube_source_id}: new New Video" in output
        assert f"{youtube_source_id}: new Old Video" not in output
        assert fetch_calls == ["new123"]
    finally:
        adapters.fetch_transcript = original_fetch


def test_scan_uses_youtube_api_uploads_playlist_metadata(tmp_path: Path) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"
    repo = Repository(db_path)
    repo.upsert_source(
        SourceConfig(
            id="youtube-source",
            kind="youtube",
            name="Example Channel",
            feed_url="https://www.youtube.com/watch?v=abc123",
            transcript_languages=["en"],
            metadata={
                "channel_id": "UC123",
                "uploads_playlist_id": "UU123",
            },
        )
    )
    repo.mark_source_scan_succeeded(
        "youtube-source",
        datetime(2026, 4, 14, 12, 30, tzinfo=timezone.utc),
    )

    original_iter_uploads = adapters.youtube_api.iter_uploads
    original_fetch = adapters.fetch_transcript
    fetch_calls: list[str] = []

    def fake_iter_uploads(uploads_playlist_id: str, **kwargs: object):
        assert uploads_playlist_id == "UU123"
        assert kwargs["limit"] == 5
        assert kwargs["page_size"] == 1
        return iter(
            [
                adapters.youtube_api.YouTubeUpload(
                    video_id="new123",
                    title="New API Video",
                    description="Fresh upload",
                    channel_title="Example Channel",
                    published_at=datetime(2026, 4, 14, 13, 0, tzinfo=timezone.utc),
                    canonical_url="https://www.youtube.com/watch?v=new123",
                ),
                adapters.youtube_api.YouTubeUpload(
                    video_id="old123",
                    title="Old API Video",
                    description="Older upload",
                    channel_title="Example Channel",
                    published_at=datetime(2026, 4, 14, 12, 0, tzinfo=timezone.utc),
                    canonical_url="https://www.youtube.com/watch?v=old123",
                ),
            ]
        )

    def fake_fetch_transcript(
        video_id_or_url: str,
        languages: list[str] | None = None,
    ) -> TranscriptResult:
        fetch_calls.append(video_id_or_url)
        assert languages == ["en"]
        return TranscriptResult(
            text=f"transcript for {video_id_or_url}",
            language_code="en",
            language_name="English",
            source="youtube_transcript_api_manual",
            is_generated=False,
            segments=[
                TranscriptSegment(
                    text=f"transcript for {video_id_or_url}",
                    start=0.0,
                    duration=1.0,
                    timestamp="0:00",
                )
            ],
        )

    adapters.youtube_api.iter_uploads = fake_iter_uploads
    adapters.fetch_transcript = fake_fetch_transcript
    try:
        scan_buffer = io.StringIO()
        with redirect_stdout(scan_buffer):
            args = parser.parse_args(["--db", str(db_path), "scan", "--source", "youtube"])
            assert run(args) == 0
    finally:
        adapters.youtube_api.iter_uploads = original_iter_uploads
        adapters.fetch_transcript = original_fetch

    output = scan_buffer.getvalue()
    assert "Scanning youtube-source (Example Channel)" in output
    assert "youtube-source: discovered New API Video (new123)" in output
    assert "youtube-source: transcript found for new123" in output
    assert "youtube-source: new New API Video" in output
    assert "youtube-source: new Old API Video" not in output
    assert fetch_calls == ["new123"]


def test_source_add_rejects_invalid_num_items(tmp_path: Path) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"

    add_args = parser.parse_args(
        [
            "--db",
            str(db_path),
            "source",
            "add",
            "--name",
            "Example Channel",
            "--num-items",
            "0",
            "https://www.youtube.com/watch?v=abc123",
        ]
    )

    with pytest.raises(ConfigError, match="--num-items must be at least 1"):
        run(add_args)



def test_scan_stops_after_youtube_transcript_failure(tmp_path: Path) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"
    repo = Repository(db_path)
    repo.upsert_source(
        SourceConfig(
            id="youtube-source",
            kind="youtube",
            name="Example Channel",
            feed_url="https://www.youtube.com/watch?v=abc123",
            transcript_languages=["en"],
            metadata={
                "channel_id": "UC123",
                "uploads_playlist_id": "UU123",
            },
        )
    )

    original_iter_uploads = adapters.youtube_api.iter_uploads
    original_fetch = adapters.fetch_transcript
    fetch_calls: list[str] = []

    def fake_iter_uploads(uploads_playlist_id: str, **kwargs: object):
        assert uploads_playlist_id == "UU123"
        assert kwargs["limit"] == 5
        assert kwargs["page_size"] == 1
        return iter(
            [
                adapters.youtube_api.YouTubeUpload(
                    video_id="new123",
                    title="New API Video",
                    description="Fresh upload",
                    channel_title="Example Channel",
                    published_at=datetime(2026, 4, 14, 13, 0, tzinfo=timezone.utc),
                    canonical_url="https://www.youtube.com/watch?v=new123",
                ),
                adapters.youtube_api.YouTubeUpload(
                    video_id="next123",
                    title="Second API Video",
                    description="Should not be fetched after transcript failure",
                    channel_title="Example Channel",
                    published_at=datetime(2026, 4, 14, 12, 0, tzinfo=timezone.utc),
                    canonical_url="https://www.youtube.com/watch?v=next123",
                ),
            ]
        )

    def fake_fetch_transcript(
        video_id_or_url: str,
        languages: list[str] | None = None,
    ) -> TranscriptResult:
        fetch_calls.append(video_id_or_url)
        raise TranscriptUnavailable(
            "YouTube is receiving too many requests from your IP address",
            content_status=ContentStatus.RATE_LIMITED,
        )

    adapters.youtube_api.iter_uploads = fake_iter_uploads
    adapters.fetch_transcript = fake_fetch_transcript
    try:
        scan_buffer = io.StringIO()
        with redirect_stdout(scan_buffer):
            args = parser.parse_args(["--db", str(db_path), "scan", "--source", "youtube"])
            assert run(args) == 0
    finally:
        adapters.youtube_api.iter_uploads = original_iter_uploads
        adapters.fetch_transcript = original_fetch

    output = scan_buffer.getvalue()
    assert "youtube-source: discovered New API Video (new123)" in output
    assert "youtube-source: transcript rate-limited for new123" in output
    assert "youtube-source: new New API Video" in output
    assert "youtube-source: transcript rate-limited for New API Video" in output
    assert "youtube-source: new Second API Video" not in output
    stored_items = Repository(db_path).list_items(limit=10)
    assert len(stored_items) == 1
    assert stored_items[0].content_status == ContentStatus.RATE_LIMITED
    assert fetch_calls == ["new123"]



def test_scan_filters_x_items_using_last_successful_scan_time(tmp_path: Path) -> None:
    x_payload = tmp_path / "x.json"
    x_payload.write_text(
        json.dumps(
            {
                "data": [
                    {
                        "id": "2",
                        "author_id": "u1",
                        "text": "new post",
                        "created_at": "2026-04-14T13:00:00Z",
                    },
                    {
                        "id": "1",
                        "author_id": "u1",
                        "text": "old post",
                        "created_at": "2026-04-14T12:00:00Z",
                    },
                ],
                "includes": {"users": [{"id": "u1", "username": "example"}]},
            }
        )
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text("database_path: laminar.db\n")
    db_path = tmp_path / "laminar.db"
    parser = build_parser()

    with redirect_stdout(io.StringIO()):
        args = parser.parse_args(
            [
                "--config",
                str(config_path),
                "--db",
                str(db_path),
                "source",
                "add",
                "--type",
                "x",
                "--name",
                "Example X",
                "--paid",
                "https://x.com/example",
            ]
        )
        assert run(args) == 0

    repo = Repository(db_path)
    source_id = next(source.id for source in repo.list_sources() if source.name == "Example X")
    cutoff = datetime(2026, 4, 14, 12, 30, tzinfo=timezone.utc)
    repo.mark_source_scan_succeeded(source_id, cutoff)

    original_run = adapters.subprocess.run

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command == ["xurl", "https://x.com/example"]
        return subprocess.CompletedProcess(command, 0, stdout=x_payload.read_text(), stderr="")

    adapters.subprocess.run = fake_run
    try:
        scan_buffer = io.StringIO()
        with redirect_stdout(scan_buffer):
            args = parser.parse_args(
                ["--config", str(config_path), "--db", str(db_path), "scan", "--include-paid"]
            )
            assert run(args) == 0
    finally:
        adapters.subprocess.run = original_run

    output = scan_buffer.getvalue()
    assert f"{source_id}: new new post" in output
    assert f"{source_id}: new old post" not in output


def test_scan_verbose_reports_incremental_cutoffs_and_skipped_items(
    tmp_path: Path,
) -> None:
    blog_feed = tmp_path / "blog.xml"
    blog_feed.write_text(
        """
        <rss version="2.0">
          <channel>
            <title>Example Blog</title>
            <item>
              <title>New Post</title>
              <link>https://example.com/new-post</link>
              <guid>post-2</guid>
              <pubDate>Tue, 14 Apr 2026 13:00:00 +0000</pubDate>
              <description>Fresh item.</description>
            </item>
            <item>
              <title>Old Post</title>
              <link>https://example.com/old-post</link>
              <guid>post-1</guid>
              <pubDate>Tue, 14 Apr 2026 12:00:00 +0000</pubDate>
              <description>Existing item.</description>
            </item>
          </channel>
        </rss>
        """
    )
    x_payload = tmp_path / "x.json"
    x_payload.write_text(
        json.dumps(
            {
                "data": [
                    {
                        "id": "2",
                        "author_id": "u1",
                        "text": "new post",
                        "created_at": "2026-04-14T13:00:00Z",
                    },
                    {
                        "id": "1",
                        "author_id": "u1",
                        "text": "old post",
                        "created_at": "2026-04-14T12:00:00Z",
                    },
                ],
                "includes": {"users": [{"id": "u1", "username": "example"}]},
            }
        )
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text("database_path: laminar.db\n")
    db_path = tmp_path / "laminar.db"
    parser = build_parser()

    with redirect_stdout(io.StringIO()):
        for args_list in (
            [
                "--config",
                str(config_path),
                "--db",
                str(db_path),
                "source",
                "add",
                "--name",
                "Example Feed",
                blog_feed.as_uri(),
            ],
            [
                "--config",
                str(config_path),
                "--db",
                str(db_path),
                "source",
                "add",
                "--type",
                "x",
                "--name",
                "Example X",
                "--paid",
                "https://x.com/example",
            ],
        ):
            args = parser.parse_args(args_list)
            assert run(args) == 0

    repo = Repository(db_path)
    sources_by_label = {source.name: source.id for source in repo.list_sources()}
    cutoff = datetime(2026, 4, 14, 12, 30, tzinfo=timezone.utc)
    repo.mark_source_scan_succeeded(sources_by_label["Example Feed"], cutoff)
    repo.mark_source_scan_succeeded(sources_by_label["Example X"], cutoff)

    original_run = adapters.subprocess.run

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command == ["xurl", "https://x.com/example"]
        return subprocess.CompletedProcess(command, 0, stdout=x_payload.read_text(), stderr="")

    adapters.subprocess.run = fake_run
    try:
        scan_buffer = io.StringIO()
        with redirect_stdout(scan_buffer):
            args = parser.parse_args(
                [
                    "--config",
                    str(config_path),
                    "--db",
                    str(db_path),
                    "--verbose",
                    "scan",
                    "--include-paid",
                ]
            )
            assert run(args) == 0
    finally:
        adapters.subprocess.run = original_run

    output = scan_buffer.getvalue()
    blog_source_id = sources_by_label["Example Feed"]
    x_source_id = sources_by_label["Example X"]
    assert f"{blog_source_id}: incremental cutoff is 2026-04-14T12:30:00+00:00" in output
    assert (
        f"{blog_source_id}: stopping rss scan at 2026-04-14T12:00:00+00:00 because it is at or before cutoff 2026-04-14T12:30:00+00:00"
        in output
    )
    assert f"{x_source_id}: x payload yielded 2 candidate posts before cutoff filtering" in output
    assert (
        f"{x_source_id}: skipping x post 1 from 2026-04-14T12:00:00+00:00 because it is at or before cutoff 2026-04-14T12:30:00+00:00"
        in output
    )


def test_scan_records_scan_start_as_success_watermark(tmp_path: Path) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"
    config_path = tmp_path / "config.yaml"
    config_path.write_text("database_path: laminar.db\n")
    repo = Repository(db_path)
    repo.upsert_source(
        SourceConfig(
            id="blog-source",
            kind="feed",
            name="Example Feed",
            feed_url="https://example.com/feed.xml",
        )
    )
    previous_scan_at = datetime(2026, 4, 14, 12, 0, tzinfo=timezone.utc)
    repo.mark_source_scan_succeeded("blog-source", previous_scan_at)

    original_build_adapter = cli.build_adapter
    observed_since: list[datetime | None] = []
    returned_item = NormalizedItem(
        source_id="blog-source",
        item_type="feed",
        external_id="post-1",
        canonical_url="https://example.com/post-1",
        title="Published During Scan",
        author="Author",
        published_at=datetime(2026, 4, 14, 12, 5, tzinfo=timezone.utc),
        excerpt="Excerpt",
        content_text="Body",
    )

    class WatermarkAdapter:
        def scan(
            self,
            source: SourceConfig,
            *,
            since: datetime | None = None,
            verbose=None,
        ) -> list[NormalizedItem]:
            observed_since.append(since)
            return [returned_item]

    def fake_build_adapter(source: SourceConfig):
        if source.id == "blog-source":
            return WatermarkAdapter()
        return original_build_adapter(source)

    cli.build_adapter = fake_build_adapter
    try:
        before_run = datetime.now(timezone.utc)
        args = parser.parse_args(
            ["--config", str(config_path), "--db", str(db_path), "scan"]
        )
        assert run(args) == 0
        after_run = datetime.now(timezone.utc)
    finally:
        cli.build_adapter = original_build_adapter

    assert observed_since == [previous_scan_at]
    recorded_scan_at = repo.last_successful_scan_at("blog-source")
    assert recorded_scan_at is not None
    assert previous_scan_at <= recorded_scan_at
    assert before_run <= recorded_scan_at <= after_run


def test_scan_can_filter_by_source_kind(tmp_path: Path) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"
    repo = Repository(db_path)
    blog_source = SourceConfig(
        id="blog-source",
        kind="feed",
        name="Example Feed",
        feed_url="https://example.com/feed.xml",
    )
    youtube_source = SourceConfig(
        id="youtube-source",
        kind="youtube",
        name="Example Channel",
        feed_url="https://example.com/youtube.xml",
        transcript_languages=["en"],
    )
    x_source = SourceConfig(
        id="x-source",
        kind="x",
        name="Example X",
        paid=True,
        handle="example",
        feed_url="https://x.com/example",
    )
    repo.upsert_source(blog_source)
    repo.upsert_source(youtube_source)
    repo.upsert_source(x_source)

    original_build_adapter = cli.build_adapter

    class StaticAdapter:
        def __init__(self, title: str) -> None:
            self.title = title

        def scan(
            self,
            source: SourceConfig,
            *,
            since: datetime | None = None,
            verbose=None,
        ) -> list[NormalizedItem]:
            return [
                NormalizedItem(
                    source_id=source.id,
                    item_type=(
                        "feed"
                        if source.kind == "feed"
                        else "video"
                        if source.kind == "youtube"
                        else "x_post"
                    ),
                    external_id=f"{source.id}-1",
                    canonical_url=f"https://example.com/{source.id}/1",
                    title=self.title,
                    author=source.name,
                    published_at=None,
                    excerpt=self.title,
                    content_text=self.title,
                )
            ]

    def fake_build_adapter(source: SourceConfig):
        if source.id == "blog-source":
            return StaticAdapter("Feed item")
        if source.id == "youtube-source":
            return StaticAdapter("YouTube item")
        if source.id == "x-source":
            return StaticAdapter("X item")
        return original_build_adapter(source)

    cli.build_adapter = fake_build_adapter
    try:
        with redirect_stdout(io.StringIO()) as buffer:
            args = parser.parse_args(["--db", str(db_path), "scan", "--source", "youtube"])
            assert run(args) == 0
    finally:
        cli.build_adapter = original_build_adapter

    output = buffer.getvalue()
    assert "Scanning youtube-source (Example Channel)" in output
    assert "youtube-source: new YouTube item" in output
    assert "Scanning blog-source" not in output
    assert "Scanning x-source" not in output
    assert "scan complete: 1 items seen, 1 new, 0 failed, 0 skipped" in output


def test_scan_can_combine_source_kind_and_source_id_filters(tmp_path: Path) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"
    repo = Repository(db_path)
    repo.upsert_source(
        SourceConfig(
            id="blog-source",
            kind="feed",
            name="Example Feed",
            feed_url="https://example.com/feed.xml",
        )
    )
    repo.upsert_source(
        SourceConfig(
            id="youtube-source",
            kind="youtube",
            name="Example Channel",
            feed_url="https://example.com/youtube.xml",
        )
    )

    original_build_adapter = cli.build_adapter

    class StaticAdapter:
        def scan(
            self,
            source: SourceConfig,
            *,
            since: datetime | None = None,
            verbose=None,
        ) -> list[NormalizedItem]:
            return [
                NormalizedItem(
                    source_id=source.id,
                    item_type="video",
                    external_id="item-1",
                    canonical_url=f"https://example.com/{source.id}/1",
                    title="Matched item",
                    author=source.name,
                    published_at=None,
                    excerpt="Matched item",
                    content_text="Matched item",
                )
            ]

    def fake_build_adapter(source: SourceConfig):
        if source.id in {"blog-source", "youtube-source"}:
            return StaticAdapter()
        return original_build_adapter(source)

    cli.build_adapter = fake_build_adapter
    try:
        with redirect_stdout(io.StringIO()) as buffer:
            args = parser.parse_args(
                ["--db", str(db_path), "scan", "--source", "youtube", "blog-source"]
            )
            assert run(args) == 0
    finally:
        cli.build_adapter = original_build_adapter

    output = buffer.getvalue()
    assert "Scanning blog-source" not in output
    assert "Scanning youtube-source" not in output
    assert "scan complete: 0 items seen, 0 new, 0 failed, 0 skipped" in output


def test_default_paths_live_under_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    parser = build_parser()
    args = parser.parse_args(["source", "validate"])

    assert args.config == str(tmp_path / ".laminar" / "config.yaml")
    assert args.db is None


def test_scan_short_flags_enable_include_paid_and_verbose() -> None:
    parser = build_parser()

    args = parser.parse_args(["scan", "-i", "-v"])

    assert args.include_paid is True
    assert args.verbose is True
    assert args.global_verbose is False


def test_global_verbose_flag_survives_scan_subparser_defaults() -> None:
    parser = build_parser()

    args = parser.parse_args(["-v", "scan", "-i"])

    assert args.include_paid is True
    assert args.global_verbose is True
    assert args.verbose is False


def test_source_validate_creates_default_config(tmp_path: Path) -> None:
    parser = build_parser()
    config_path = tmp_path / "config.yaml"
    db_path = tmp_path / "laminar.db"

    with redirect_stdout(io.StringIO()) as buffer:
        args = parser.parse_args(
            ["--config", str(config_path), "--db", str(db_path), "source", "validate"]
        )
        assert run(args) == 0

    assert "Validated 0 sources" in buffer.getvalue()
    assert config_path.exists()


def test_source_list_reads_sources_from_database(tmp_path: Path) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"

    add_args = parser.parse_args(
        [
            "--db",
            str(db_path),
            "source",
            "add",
            "--name",
            "Example Feed",
            "file:///tmp/feed.xml",
        ]
    )
    assert run(add_args) == 0

    with redirect_stdout(io.StringIO()) as buffer:
        list_args = parser.parse_args(["--db", str(db_path), "source", "list"])
        assert run(list_args) == 0

    output = buffer.getvalue().strip()
    assert "Example Feed" in output
    assert "feed" in output
    assert "enabled" in output
    assert "free" in output
    source_id = repo_source_id_from_db(db_path)
    assert UUID(source_id).version == 4


def test_source_list_shows_paid_sources(tmp_path: Path) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"

    add_args = parser.parse_args(
        [
            "--db",
            str(db_path),
            "source",
            "add",
            "--type",
            "x",
            "--name",
            "Paid X",
            "--paid",
            "https://x.com/example",
        ]
    )
    assert run(add_args) == 0

    with redirect_stdout(io.StringIO()) as buffer:
        list_args = parser.parse_args(["--db", str(db_path), "source", "list"])
        assert run(list_args) == 0

    output = buffer.getvalue()
    assert "Paid X" in output
    assert "paid" in output


def test_x_sources_default_to_paid(tmp_path: Path) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"

    add_args = parser.parse_args(
        [
            "--db",
            str(db_path),
            "source",
            "add",
            "--name",
            "Default Paid X",
            "https://x.com/example",
        ]
    )
    assert run(add_args) == 0

    repo = Repository(db_path)
    sources = repo.list_sources()

    assert len(sources) == 1
    assert sources[0].kind == "x"
    assert sources[0].paid is True


def test_x_profile_urls_infer_handle(tmp_path: Path) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"

    add_args = parser.parse_args(
        [
            "--db",
            str(db_path),
            "source",
            "add",
            "--name",
            "Example X",
            "https://x.com/example",
        ]
    )
    assert run(add_args) == 0

    sources = Repository(db_path).list_sources()
    assert len(sources) == 1
    assert sources[0].handle == "example"


def test_youtube_urls_infer_youtube_kind_and_store_api_metadata(tmp_path: Path) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"
    original_resolve = cli.youtube_api.resolve_channel_from_url

    def fake_resolve_channel_from_url(url: str):
        assert url == "https://www.youtube.com/watch?v=abc123"
        return cli.youtube_api.YouTubeChannel(
            channel_id="UC123",
            title="Example Channel",
            uploads_playlist_id="UU123",
        )

    cli.youtube_api.resolve_channel_from_url = fake_resolve_channel_from_url
    try:
        add_args = parser.parse_args(
            [
                "--db",
                str(db_path),
                "source",
                "add",
                "--name",
                "Example Channel",
                "https://www.youtube.com/watch?v=abc123",
            ]
        )
        assert run(add_args) == 0
    finally:
        cli.youtube_api.resolve_channel_from_url = original_resolve

    sources = Repository(db_path).list_sources()
    assert len(sources) == 1
    assert sources[0].kind == "youtube"
    assert sources[0].transcript_languages == ["en"]
    assert sources[0].metadata["channel_id"] == "UC123"
    assert sources[0].metadata["uploads_playlist_id"] == "UU123"
    assert sources[0].metadata["num_items"] == 5


def test_youtube_sources_allow_custom_num_items(tmp_path: Path) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"
    original_resolve = cli.youtube_api.resolve_channel_from_url

    def fake_resolve_channel_from_url(url: str):
        assert url == "https://www.youtube.com/watch?v=abc123"
        return cli.youtube_api.YouTubeChannel(
            channel_id="UC123",
            title="Example Channel",
            uploads_playlist_id="UU123",
        )

    cli.youtube_api.resolve_channel_from_url = fake_resolve_channel_from_url
    try:
        add_args = parser.parse_args(
            [
                "--db",
                str(db_path),
                "source",
                "add",
                "--name",
                "Example Channel",
                "--num-items",
                "12",
                "https://www.youtube.com/watch?v=abc123",
            ]
        )
        assert run(add_args) == 0
    finally:
        cli.youtube_api.resolve_channel_from_url = original_resolve

    sources = Repository(db_path).list_sources()
    assert len(sources) == 1
    assert sources[0].metadata["num_items"] == 12


def test_non_youtube_urls_default_to_feed_kind(tmp_path: Path) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"

    add_args = parser.parse_args(
        [
            "--db",
            str(db_path),
            "source",
            "add",
            "--name",
            "Example Feed",
            "https://example.com/feed.xml",
        ]
    )
    assert run(add_args) == 0

    sources = Repository(db_path).list_sources()
    assert len(sources) == 1
    assert sources[0].kind == "feed"
    assert sources[0].transcript_languages == []


def test_youtube_sources_default_transcript_language_to_english(tmp_path: Path) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"

    add_args = parser.parse_args(
        [
            "--db",
            str(db_path),
            "source",
            "add",
            "--type",
            "youtube",
            "--name",
            "Example Channel",
            "https://example.com/youtube.xml",
        ]
    )
    assert run(add_args) == 0

    sources = Repository(db_path).list_sources()
    assert len(sources) == 1
    assert sources[0].transcript_languages == ["en"]
    assert sources[0].metadata["num_items"] == 5


def test_explicit_type_overrides_url_inference(tmp_path: Path) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"

    add_args = parser.parse_args(
        [
            "--db",
            str(db_path),
            "source",
            "add",
            "--type",
            "feed",
            "--name",
            "Forced Feed",
            "https://x.com/example",
        ]
    )
    assert run(add_args) == 0

    sources = Repository(db_path).list_sources()
    assert len(sources) == 1
    assert sources[0].kind == "feed"
    assert sources[0].paid is False
    assert sources[0].handle is None


def test_x_list_sources_use_xurl_and_store_list_contents(tmp_path: Path) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"
    list_url = "https://x.com/i/lists/9876543210"
    expected_target = (
        "/2/lists/9876543210/tweets"
        "?expansions=author_id&max_results=100&tweet.fields=created_at&user.fields=username"
    )

    add_args = parser.parse_args(
        [
            "--db",
            str(db_path),
            "source",
            "add",
            "--type",
            "x",
            "--name",
            "AI List",
            list_url,
        ]
    )
    assert run(add_args) == 0

    original_run = adapters.subprocess.run

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command == ["xurl", expected_target]
        assert kwargs["check"] is True
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "items": [
                        {
                            "id": "12345",
                            "text": "Short post on markets",
                            "created_at": "2026-04-14T15:00:00Z",
                            "user": {"screen_name": "example"},
                        }
                    ]
                }
            ),
            stderr="",
        )

    adapters.subprocess.run = fake_run
    try:
        scan_args = parser.parse_args(
            ["--db", str(db_path), "scan", "--include-paid"]
        )
        assert run(scan_args) == 0
    finally:
        adapters.subprocess.run = original_run

    items = Repository(db_path).list_items(limit=10)
    assert len(items) == 1
    assert items[0].title == "Short post on markets"
    assert items[0].canonical_url == "https://x.com/example/status/12345"


def test_x_list_browser_url_is_translated_to_list_tweets_api_target() -> None:
    target = adapters._x_command_target("https://x.com/i/lists/9876543210")

    assert target == (
        "/2/lists/9876543210/tweets"
        "?expansions=author_id&max_results=100&tweet.fields=created_at&user.fields=username"
    )


def test_source_add_help_uses_url_type_and_paid_flags(capsys) -> None:
    parser = build_parser()

    try:
        parser.parse_args(["source", "add", "--help"])
    except SystemExit as exc:
        assert exc.code == 0

    captured = capsys.readouterr()
    help_text = captured.out
    assert "URL" in help_text
    assert "--name" in help_text
    assert "--type" in help_text
    assert "--paid" in help_text
    assert "inferred from the URL" in help_text
    assert "Defaults to en." in help_text
    assert "{feed,youtube,x}" in help_text
    assert "blog" not in help_text
    assert "\"label\"" not in help_text
    assert "--label" not in help_text
    assert "--kind" not in help_text
    assert "--feed-url" not in help_text
    assert "--command" not in help_text
    assert "--handle" not in help_text


def test_source_remove_deletes_source_without_items(tmp_path: Path) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"
    repo = Repository(db_path)
    repo.upsert_source(SourceConfig(id="feed-1", kind="feed", name="Example Feed"))

    with redirect_stdout(io.StringIO()) as buffer:
        remove_args = parser.parse_args(["--db", str(db_path), "source", "remove", "feed-1"])
        assert run(remove_args) == 0

    assert "Removed source feed-1" in buffer.getvalue()
    assert repo.list_sources() == []


def test_source_remove_rejects_non_recursive_delete_when_items_exist(
    tmp_path: Path, capsys
) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"
    repo = Repository(db_path)
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

    remove_args = parser.parse_args(["--db", str(db_path), "source", "remove", "feed-1"])
    assert run(remove_args) == 1

    captured = capsys.readouterr()
    assert "rerun with --recursive" in captured.err
    assert [source.id for source in repo.list_sources()] == ["feed-1"]
    assert len(repo.list_items(limit=10)) == 1


def test_source_remove_recursive_deletes_source_and_items(tmp_path: Path) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"
    repo = Repository(db_path)
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

    with redirect_stdout(io.StringIO()) as buffer:
        remove_args = parser.parse_args(
            ["--db", str(db_path), "source", "remove", "--recursive", "feed-1"]
        )
        assert run(remove_args) == 0

    output = buffer.getvalue()
    assert "Removed source feed-1 and deleted 1 items" in output
    assert repo.list_sources() == []
    assert repo.list_items(limit=10) == []


def test_scan_and_query_atom_blog_feed(tmp_path: Path) -> None:
    atom_feed = tmp_path / "atom.xml"
    atom_feed.write_text(
        """
        <feed xmlns="http://www.w3.org/2005/Atom">
          <title>Example Atom Blog</title>
          <entry>
            <id>tag:example.com,2026:post-1</id>
            <title>Atom Entry</title>
            <link rel="alternate" href="https://example.com/atom-entry" />
            <updated>2026-04-14T12:00:00Z</updated>
            <author><name>Example Author</name></author>
            <summary>Summary text</summary>
            <content>Full content text</content>
          </entry>
        </feed>
        """
    )
    parser = build_parser()
    db_path = tmp_path / "laminar.db"

    add_args = parser.parse_args(
        [
            "--db",
            str(db_path),
            "source",
            "add",
            "--name",
            "Example Atom Feed",
            atom_feed.as_uri(),
        ]
    )
    assert run(add_args) == 0

    scan_args = parser.parse_args(["--db", str(db_path), "scan"])
    assert run(scan_args) == 0

    items = Repository(db_path).list_items(limit=10)
    assert len(items) == 1
    assert items[0].title == "Atom Entry"
    assert items[0].canonical_url == "https://example.com/atom-entry"
    assert items[0].content_text == "Full content text"


def repo_source_id_from_db(db_path: Path) -> str:
    repo = Repository(db_path)
    sources = repo.list_sources()
    assert len(sources) == 1
    return sources[0].id


def test_items_show_accepts_exact_title(tmp_path: Path) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"
    repo = Repository(db_path)
    repo.upsert_item(
        NormalizedItem(
            source_id="yt-1",
            item_type="video",
            external_id="abc123",
            canonical_url="https://youtube.com/watch?v=abc123",
            title="Daily Briefing",
            author="Channel",
            published_at=None,
            excerpt="Summary",
            content_text="Transcript",
        )
    )

    with redirect_stdout(io.StringIO()) as buffer:
        show_args = parser.parse_args(["--db", str(db_path), "items", "show", "Daily Briefing"])
        assert run(show_args) == 0

    shown = json.loads(buffer.getvalue())
    assert shown["title"] == "Daily Briefing"


def test_items_remove_accepts_exact_item_id(tmp_path: Path) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"
    repo = Repository(db_path)
    repo.upsert_item(
        NormalizedItem(
            item_id="item-1",
            source_id="yt-1",
            item_type="video",
            external_id="abc123",
            canonical_url="https://youtube.com/watch?v=abc123",
            title="Daily Briefing",
            author="Channel",
            published_at=None,
            excerpt="Summary",
            content_text="Transcript",
        )
    )

    with redirect_stdout(io.StringIO()) as buffer:
        remove_args = parser.parse_args(["--db", str(db_path), "items", "remove", "item-1"])
        assert run(remove_args) == 0

    assert "Removed item item-1" in buffer.getvalue()
    assert repo.list_items(limit=10) == []


def test_items_remove_accepts_exact_title(tmp_path: Path) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"
    repo = Repository(db_path)
    repo.upsert_item(
        NormalizedItem(
            item_id="item-1",
            source_id="yt-1",
            item_type="video",
            external_id="abc123",
            canonical_url="https://youtube.com/watch?v=abc123",
            title="Daily Briefing",
            author="Channel",
            published_at=None,
            excerpt="Summary",
            content_text="Transcript",
        )
    )

    remove_args = parser.parse_args(
        ["--db", str(db_path), "items", "remove", "Daily Briefing"]
    )
    assert run(remove_args) == 0
    assert repo.list_items(limit=10) == []


def test_items_show_accepts_unique_item_id_prefix(tmp_path: Path) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"
    repo = Repository(db_path)
    repo.upsert_item(
        NormalizedItem(
            source_id="yt-1",
            item_type="video",
            external_id="abc123",
            canonical_url="https://youtube.com/watch?v=abc123",
            title="Daily Briefing",
            author="Channel",
            published_at=None,
            excerpt="Summary",
            content_text="Transcript",
        )
    )
    item = repo.list_items(limit=1)[0]
    prefix = repo.shortest_unique_item_prefix(item.item_id)

    with redirect_stdout(io.StringIO()) as buffer:
        show_args = parser.parse_args(["--db", str(db_path), "items", "show", prefix])
        assert run(show_args) == 0

    shown = json.loads(buffer.getvalue())
    assert shown["item_id"] == item.item_id


def test_items_show_rejects_ambiguous_item_id_prefix(tmp_path: Path, capsys) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"
    repo = Repository(db_path)
    first = NormalizedItem(
        item_id="aaaaaaaa-1111-1111-1111-111111111111",
        source_id="yt-1",
        item_type="video",
        external_id="abc123",
        canonical_url="https://youtube.com/watch?v=abc123",
        title="Daily Briefing",
        author="Channel",
        published_at=None,
        excerpt="Summary",
        content_text="Transcript",
    )
    second = NormalizedItem(
        item_id="aaaaaaab-2222-2222-2222-222222222222",
        source_id="yt-2",
        item_type="video",
        external_id="abc124",
        canonical_url="https://youtube.com/watch?v=abc124",
        title="Market Wrap",
        author="Channel",
        published_at=None,
        excerpt="Summary",
        content_text="Transcript",
    )
    repo.upsert_item(first)
    repo.upsert_item(second)

    show_args = parser.parse_args(["--db", str(db_path), "items", "show", "aaaaaaa"])
    assert run(show_args) == 1
    captured = capsys.readouterr()
    assert "Item ID prefix 'aaaaaaa' is ambiguous" in captured.err
    assert "aaaaaaaa" in captured.err
    assert "aaaaaaab" in captured.err


def test_items_remove_rejects_missing_item(tmp_path: Path, capsys) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"

    remove_args = parser.parse_args(["--db", str(db_path), "items", "remove", "missing"])
    assert run(remove_args) == 1

    captured = capsys.readouterr()
    assert "Item missing not found" in captured.err


def test_items_show_rejects_ambiguous_title(tmp_path: Path, capsys) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"
    repo = Repository(db_path)
    repo.upsert_source(SourceConfig(id="yt-a", kind="youtube", name="Channel A"))
    repo.upsert_source(SourceConfig(id="yt-b", kind="youtube", name="Channel B"))
    for suffix in ("a", "b"):
        repo.upsert_item(
            NormalizedItem(
                source_id=f"yt-{suffix}",
                item_type="video",
                external_id=f"abc12{suffix}",
                canonical_url=f"https://youtube.com/watch?v=abc12{suffix}",
                title="Daily Briefing",
                author="Channel",
                published_at=None,
                excerpt="Summary",
                content_text="Transcript",
            )
        )

    show_args = parser.parse_args(["--db", str(db_path), "items", "show", "Daily Briefing"])
    assert run(show_args) == 1
    captured = capsys.readouterr()
    assert "Multiple items share the title" in captured.err
    assert "Daily Briefing (Channel A)" in captured.err
    assert "Daily Briefing (Channel B)" in captured.err


def test_source_export_and_import_round_trip_yaml(tmp_path: Path) -> None:
    parser = build_parser()
    source_db_path = tmp_path / "source.db"
    import_db_path = tmp_path / "import.db"
    export_path = tmp_path / "sources.yaml"
    repo = Repository(source_db_path)
    scanned_at = datetime(2026, 4, 18, 12, 30, tzinfo=timezone.utc)
    repo.upsert_source(
        SourceConfig(
            id="yt-1",
            kind="youtube",
            name="Example Channel",
            enabled=False,
            paid=True,
            feed_url="https://www.youtube.com/watch?v=abc123",
            handle="@example",
            transcript_languages=["en", "es"],
            metadata={"channel_id": "UC123", "num_items": 7},
        )
    )
    repo.mark_source_scan_succeeded("yt-1", scanned_at)

    export_args = parser.parse_args(["--db", str(source_db_path), "source", "export", str(export_path)])
    assert run(export_args) == 0

    exported = yaml.safe_load(export_path.read_text())
    assert exported["version"] == 1
    assert exported["sources"][0]["id"] == "yt-1"
    assert exported["sources"][0]["last_successful_scan_at"] == scanned_at.isoformat()

    import_args = parser.parse_args(["--db", str(import_db_path), "source", "import", str(export_path)])
    assert run(import_args) == 0

    imported = Repository(import_db_path).get_source("yt-1")
    assert imported is not None
    assert imported.name == "Example Channel"
    assert imported.enabled is False
    assert imported.paid is True
    assert imported.transcript_languages == ["en", "es"]
    assert imported.metadata == {"channel_id": "UC123", "num_items": 7}
    assert imported.last_successful_scan_at == scanned_at


def test_source_export_without_path_writes_yaml_to_stdout(tmp_path: Path) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"
    repo = Repository(db_path)
    repo.upsert_source(
        SourceConfig(
            id="feed-1",
            kind="feed",
            name="Example Feed",
            feed_url="https://example.com/feed.xml",
        )
    )

    with redirect_stdout(io.StringIO()) as buffer:
        export_args = parser.parse_args(["--db", str(db_path), "source", "export"])
        assert run(export_args) == 0

    exported = yaml.safe_load(buffer.getvalue())
    assert exported["version"] == 1
    assert exported["sources"][0]["id"] == "feed-1"


def test_items_export_and_import_round_trip_yaml(tmp_path: Path) -> None:
    parser = build_parser()
    source_db_path = tmp_path / "source.db"
    import_db_path = tmp_path / "import.db"
    export_path = tmp_path / "items.yaml"
    repo = Repository(source_db_path)
    retrieved_at = datetime(2026, 4, 18, 14, 45, tzinfo=timezone.utc)
    published_at = datetime(2026, 4, 18, 13, 0, tzinfo=timezone.utc)
    repo.upsert_item(
        NormalizedItem(
            item_id="11111111-1111-4111-8111-111111111111",
            source_id="yt-1",
            item_type="video",
            external_id="abc123",
            canonical_url="https://youtube.com/watch?v=abc123",
            title="Daily Briefing",
            author="Channel",
            published_at=published_at,
            excerpt="Summary",
            content_text="Transcript text",
            content_status=ContentStatus.RATE_LIMITED,
            content_language="en",
            content_source="youtube_transcript_api",
            raw_payload={"video_id": "abc123"},
            retrieved_at=retrieved_at,
        )
    )

    export_args = parser.parse_args(["--db", str(source_db_path), "items", "export", str(export_path)])
    assert run(export_args) == 0

    exported = yaml.safe_load(export_path.read_text())
    assert exported["version"] == 1
    assert exported["items"][0]["item_id"] == "11111111-1111-4111-8111-111111111111"
    assert exported["items"][0]["retrieved_at"] == retrieved_at.isoformat()

    import_args = parser.parse_args(["--db", str(import_db_path), "items", "import", str(export_path)])
    assert run(import_args) == 0

    items = Repository(import_db_path).list_items(limit=10)
    assert len(items) == 1
    imported = items[0]
    assert imported.item_id == "11111111-1111-4111-8111-111111111111"
    assert imported.source_id == "yt-1"
    assert imported.item_type == "video"
    assert imported.external_id == "abc123"
    assert imported.canonical_url == "https://youtube.com/watch?v=abc123"
    assert imported.title == "Daily Briefing"
    assert imported.author == "Channel"
    assert imported.published_at == published_at
    assert imported.retrieved_at == retrieved_at
    assert imported.excerpt == "Summary"
    assert imported.content_text == "Transcript text"
    assert imported.content_status == ContentStatus.RATE_LIMITED
    assert imported.content_language == "en"
    assert imported.content_source == "youtube_transcript_api"
    assert imported.raw_payload == {"video_id": "abc123"}


def test_items_export_without_path_writes_yaml_to_stdout(tmp_path: Path) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"
    repo = Repository(db_path)
    repo.upsert_item(
        NormalizedItem(
            item_id="11111111-1111-4111-8111-111111111111",
            source_id="yt-1",
            item_type="video",
            external_id="abc123",
            canonical_url="https://youtube.com/watch?v=abc123",
            title="Daily Briefing",
            author="Channel",
            published_at=datetime(2026, 4, 18, 13, 0, tzinfo=timezone.utc),
            excerpt="Summary",
            content_text="Transcript text",
        )
    )

    with redirect_stdout(io.StringIO()) as buffer:
        export_args = parser.parse_args(["--db", str(db_path), "items", "export"])
        assert run(export_args) == 0

    exported = yaml.safe_load(buffer.getvalue())
    assert exported["version"] == 1
    assert exported["items"][0]["item_id"] == "11111111-1111-4111-8111-111111111111"


def test_items_import_updates_existing_entries(tmp_path: Path) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"
    import_path = tmp_path / "items.yaml"
    repo = Repository(db_path)
    repo.upsert_item(
        NormalizedItem(
            item_id="11111111-1111-4111-8111-111111111111",
            source_id="yt-1",
            item_type="video",
            external_id="abc123",
            canonical_url="https://youtube.com/watch?v=abc123",
            title="Initial Title",
            author="Channel",
            published_at=datetime(2026, 4, 18, 13, 0, tzinfo=timezone.utc),
            excerpt="Summary",
            content_text="Transcript text",
        )
    )
    import_path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "items": [
                    {
                        "item_id": "99999999-9999-4999-8999-999999999999",
                        "source_id": "yt-1",
                        "item_type": "video",
                        "external_id": "abc123",
                        "canonical_url": "https://youtube.com/watch?v=abc123",
                        "title": "Updated Title",
                        "author": "Channel",
                        "published_at": "2026-04-18T13:00:00+00:00",
                        "retrieved_at": "2026-04-18T14:00:00+00:00",
                        "excerpt": "Updated summary",
                        "content_text": "Updated transcript",
                        "content_status": "available",
                        "content_language": "en",
                        "content_source": "youtube_transcript_api",
                        "raw_payload": {"video_id": "abc123"},
                    }
                ],
            },
            sort_keys=False,
        )
    )

    import_args = parser.parse_args(["--db", str(db_path), "items", "import", str(import_path)])
    assert run(import_args) == 0

    items = repo.list_items(limit=10)
    assert len(items) == 1
    assert items[0].title == "Updated Title"
    assert items[0].excerpt == "Updated summary"
    assert items[0].content_text == "Updated transcript"
