# Laminar

Laminar is a local-first CLI for ingesting updates from blogs, YouTube channels, and X accounts into SQLite for deterministic retrieval by a downstream agent.

## Quick Start

```bash
uv sync
uv run laminar source validate --config laminar.toml
uv run laminar scan --config laminar.toml
uv run laminar items list --db laminar.db
```

## Config

Laminar uses TOML config with one `[[sources]]` entry per feed.

```toml
[[sources]]
id = "example-blog"
kind = "blog"
label = "Example Blog"
enabled = true
feed_url = "https://example.com/feed.xml"

[[sources]]
id = "example-youtube"
kind = "youtube"
label = "Example Channel"
enabled = true
feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=..."
transcript_languages = ["en"]

[[sources]]
id = "example-x"
kind = "x"
label = "Example on X"
enabled = true
command = ["xurl", "https://api.x.com/2/..."]
```
