import io
import json
from contextlib import redirect_stdout
from pathlib import Path

from laminar.cli import build_parser, run


def test_scan_and_query(tmp_path: Path) -> None:
    transcript_path = tmp_path / "transcript.xml"
    transcript_path.write_text(
        """
        <transcript>
          <text start="0" dur="1">market update</text>
          <text start="1" dur="1">finite daily summary</text>
        </transcript>
        """
    )
    watch_path = tmp_path / "watch.html"
    watch_path.write_text(
        (
            'ytInitialPlayerResponse = {"captions":{"playerCaptionsTracklistRenderer":'
            '{"captionTracks":[{"baseUrl":"%s","languageCode":"en"}]}}};'
        )
        % transcript_path.as_uri()
    )
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
        f"""
        <feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">
          <title>Example Channel</title>
          <entry>
            <yt:videoId>abc123</yt:videoId>
            <title>Daily Briefing</title>
            <link rel="alternate" href="{watch_path.as_uri()}"/>
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
    config_path = tmp_path / "laminar.toml"
    config_path.write_text(
        f"""
        [[sources]]
        id = "blog-1"
        kind = "blog"
        label = "Example Blog"
        feed_url = "{blog_feed.as_uri()}"

        [[sources]]
        id = "yt-1"
        kind = "youtube"
        label = "Example Channel"
        feed_url = "{yt_feed.as_uri()}"
        transcript_languages = ["en"]

        [[sources]]
        id = "x-1"
        kind = "x"
        label = "Example X"
        handle = "example"
        command = ["cat", "{x_payload}"]
        """
    )
    db_path = tmp_path / "laminar.db"
    parser = build_parser()

    with redirect_stdout(io.StringIO()):
        validate_args = parser.parse_args(
            ["--config", str(config_path), "--db", str(db_path), "source", "validate"]
        )
        assert run(validate_args) == 0

        scan_args = parser.parse_args(["--config", str(config_path), "--db", str(db_path), "scan"])
        assert run(scan_args) == 0

    list_buffer = io.StringIO()
    with redirect_stdout(list_buffer):
        list_args = parser.parse_args(["--db", str(db_path), "items", "list", "--limit", "10"])
        assert run(list_args) == 0
    list_output = list_buffer.getvalue()
    assert "SQLite for Feeds" in list_output
    assert "Daily Briefing" in list_output

    search_buffer = io.StringIO()
    with redirect_stdout(search_buffer):
        search_args = parser.parse_args(["--db", str(db_path), "search", "finite"])
        assert run(search_args) == 0
    assert "Daily Briefing" in search_buffer.getvalue()

    show_buffer = io.StringIO()
    with redirect_stdout(show_buffer):
        show_args = parser.parse_args(["--db", str(db_path), "items", "show", "2"])
        assert run(show_args) == 0
    shown = json.loads(show_buffer.getvalue())
    assert shown["item_type"] == "video"
    assert "market update" in shown["content_text"]


def test_default_paths_live_under_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    parser = build_parser()
    args = parser.parse_args(["source", "validate"])

    assert args.config == str(tmp_path / ".laminar" / "config.toml")
    assert args.db == str(tmp_path / ".laminar" / "laminar.db")
