# Laminar

![Laminar logo](assets/laminar-logo.png)

Laminar is a local-first CLI for ingesting updates from blogs, YouTube channels, and X accounts into SQLite for deterministic retrieval by a downstream agent.

By default, Laminar keeps its state under `~/.laminar/`:
- `~/.laminar/laminar.db`
- `~/.laminar/config.yaml`

## Quick Start

```bash
uv sync
uv run laminar source validate
uv run laminar source add --kind blog --label "Example Blog" --feed-url https://example.com/feed.xml
uv run laminar scan
uv run laminar items list
```

## Config

Laminar stores source definitions in SQLite. `config.yaml` is now behavior-only and currently controls the database location. Running `laminar source validate` on a fresh install will create `config.yaml` if it does not exist.

```yaml
database_path: laminar.db
```

Add sources through the CLI:

```bash
uv run laminar source add --kind blog --label "Example Blog" --feed-url https://example.com/feed.xml
uv run laminar source add --kind youtube --label "Example Channel" --feed-url "https://www.youtube.com/feeds/videos.xml?channel_id=..." --transcript-language en
uv run laminar source add --kind x --label "Example on X" --command xurl https://api.x.com/2/...
uv run laminar source list
uv run laminar scan --include-paid
```

X sources are treated as paid by default. Use `--costs-money` for any other source backed by a paid or metered API. Paid sources are skipped by default during `scan`; pass `--include-paid` when you want to scan them.
