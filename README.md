# Laminar

Laminar is a local-first CLI for ingesting updates from blogs, YouTube channels, and X accounts into SQLite for deterministic retrieval by a downstream agent.

By default, Laminar keeps its state under `~/.laminar/`:
- `~/.laminar/laminar.db`
- `~/.laminar/config.toml`

## Quick Start

```bash
uv sync
mkdir -p ~/.laminar
uv run laminar source validate
uv run laminar scan
uv run laminar items list
```

## Config

Laminar uses TOML config with one `[[sources]]` entry per feed. The default config path is `~/.laminar/config.toml`.

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
