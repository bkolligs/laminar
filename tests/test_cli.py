import io
import json
from contextlib import redirect_stdout
from urllib.error import HTTPError
from pathlib import Path
from uuid import UUID

import laminar.adapters as adapters
import laminar.cli as cli
from laminar.cli import build_parser, run
from laminar.models import NormalizedItem, SourceConfig
from laminar.repository import Repository
from laminar.youtube import TranscriptResult, TranscriptSegment


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
    with redirect_stdout(io.StringIO()):
        for args_list in (
            [
                "--config",
                str(config_path),
                "--db",
                str(db_path),
                "source",
                "add",
                "--kind",
                "blog",
                "--label",
                "Example Blog",
                "--feed-url",
                blog_feed.as_uri(),
            ],
            [
                "--config",
                str(config_path),
                "--db",
                str(db_path),
                "source",
                "add",
                "--kind",
                "youtube",
                "--label",
                "Example Channel",
                "--feed-url",
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
                "--kind",
                "x",
                "--label",
                "Example X",
                "--costs-money",
                "--handle",
                "example",
                "--command",
                "cat",
                str(x_payload),
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
    adapters.fetch_transcript = original_fetch

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
    assert any(source.costs_money for source in repo.list_sources() if source.kind == "x")


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
        kind="blog",
        label="Broken Feed",
        feed_url="https://example.invalid/feed.xml",
    )
    healthy = SourceConfig(
        id="good-source",
        kind="x",
        label="Healthy Feed",
        costs_money=True,
        handle="healthy",
        command=["unused"],
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
        def scan(self, source: SourceConfig) -> list[NormalizedItem]:
            raise HTTPError(source.feed_url or "", 404, "Not Found", hdrs=None, fp=None)

    class HealthyAdapter:
        def scan(self, source: SourceConfig) -> list[NormalizedItem]:
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
    assert "good-source: this source uses a paid or metered integration" in output
    assert "good-source: reachable" in output
    assert "good-source: existing Already seen" in output
    assert "good-source: new Brand new" in output
    assert "scan complete: 2 items seen, 1 new, 1 failed, 0 skipped" in output
    items = repo.list_items(limit=10)
    assert any(item.title == "Brand new" for item in items)


def test_scan_skips_paid_sources_without_include_paid(tmp_path: Path) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"
    repo = Repository(db_path)
    repo.upsert_source(
        SourceConfig(
            id="paid-source",
            kind="x",
            label="Paid X",
            costs_money=True,
            handle="example",
            command=["unused"],
        )
    )

    original_build_adapter = cli.build_adapter

    class FailingIfCalledAdapter:
        def scan(self, source: SourceConfig) -> list[NormalizedItem]:
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


def test_default_paths_live_under_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    parser = build_parser()
    args = parser.parse_args(["source", "validate"])

    assert args.config == str(tmp_path / ".laminar" / "config.yaml")
    assert args.db is None


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
            "--kind",
            "blog",
            "--label",
            "Example Blog",
            "--feed-url",
            "file:///tmp/feed.xml",
        ]
    )
    assert run(add_args) == 0

    with redirect_stdout(io.StringIO()) as buffer:
        list_args = parser.parse_args(["--db", str(db_path), "source", "list"])
        assert run(list_args) == 0

    output = buffer.getvalue().strip()
    assert "Example Blog" in output
    assert "blog" in output
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
            "--kind",
            "x",
            "--label",
            "Paid X",
            "--costs-money",
            "--handle",
            "example",
            "--command",
            "echo",
            "[]",
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
            "--kind",
            "x",
            "--label",
            "Default Paid X",
            "--handle",
            "example",
            "--command",
            "echo",
            "[]",
        ]
    )
    assert run(add_args) == 0

    repo = Repository(db_path)
    sources = repo.list_sources()

    assert len(sources) == 1
    assert sources[0].kind == "x"
    assert sources[0].costs_money is True


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


def test_items_show_rejects_ambiguous_title(tmp_path: Path, capsys) -> None:
    parser = build_parser()
    db_path = tmp_path / "laminar.db"
    repo = Repository(db_path)
    repo.upsert_source(SourceConfig(id="yt-a", kind="youtube", label="Channel A"))
    repo.upsert_source(SourceConfig(id="yt-b", kind="youtube", label="Channel B"))
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
