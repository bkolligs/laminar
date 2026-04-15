# Laminar

![Laminar logo](assets/laminar-logo.png)

Laminar is a local-first CLI for ingesting updates from blogs, YouTube channels, X accounts, and X lists into SQLite for deterministic retrieval by a downstream agent.

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
uv run laminar source add --kind x --label "AI Researchers" --feed-url https://x.com/i/lists/1234567890
uv run laminar source remove SOURCE_ID
uv run laminar source remove --recursive SOURCE_ID
uv run laminar items remove ITEM_ID
uv run laminar source list
uv run laminar scan --include-paid
uv run laminar scan -i -v
uv run laminar scan --source youtube
```

X sources are treated as paid by default. Use `--costs-money` for any other source backed by a paid or metered API. Paid sources are skipped by default during `scan`; pass `--include-paid` when you want to scan them.

For `--kind x`, Laminar can either run an explicit `--command` or resolve `--feed-url` through `xurl` automatically. For X list browser URLs like `https://x.com/i/lists/...`, Laminar translates them into the corresponding list-tweets API request before invoking `xurl`, then normalizes each returned post into the database as an `x_post` item.

`laminar source remove` deletes the source definition and its scan history. Pass `--recursive` if you also want to delete all items already ingested from that source.

`laminar items remove` deletes a single stored item and its associated stored content. Like `items show`, it accepts an exact item ID, a unique item ID prefix, or a unique title.

Pass `-v` or `--verbose` to `laminar scan` when you want detailed progress logging, including the active incremental cutoff and when older entries are skipped because they are at or before that watermark. Use `-i` or `--include-paid` to include paid sources.

`laminar scan` stores the last successful scan time for each source and uses it as an incremental cutoff on later runs. Blog and YouTube feed scans assume reverse-chronological ordering and stop once they reach older entries; X scans currently still invoke the configured command or resolved `xurl` target and then filter out items at or before the last successful scan time locally.
