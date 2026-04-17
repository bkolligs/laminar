# Laminar

> Never look at an algorithmic feed that you don't control again

![Laminar logo](assets/laminar-logo.png)

Laminar is a local-first CLI for ingesting updates from feeds, YouTube channels, X accounts, and X lists into SQLite for deterministic retrieval by a downstream agent.

By default, Laminar keeps its state under `~/.laminar/`:
- `~/.laminar/laminar.db`
- `~/.laminar/config.yaml`

## Quick Start

```bash
uv sync
uv run laminar source validate
uv run laminar source add --name "SCMP News" https://www.scmp.com/rss/4/feed/
uv run laminar scan
uv run laminar stats
uv run laminar items list
```

## Config

Laminar stores source definitions in SQLite. `config.yaml` is now behavior-only and currently controls the database location. Running `laminar source validate` on a fresh install will create `config.yaml` if it does not exist.

```yaml
database_path: laminar.db
```

Add sources through the CLI:

```bash
uv run laminar source add --name "SCMP News" https://www.scmp.com/rss/4/feed/
uv run laminar source add --name "Example Channel" "https://www.youtube.com/feeds/videos.xml?channel_id=..." --transcript-language en
uv run laminar source add --name "Example on X" https://x.com/example
uv run laminar source add --name "AI Researchers" https://x.com/i/lists/1234567890
uv run laminar source show SOURCE_ID
uv run laminar source remove SOURCE_ID
uv run laminar source remove --recursive SOURCE_ID
uv run laminar items remove ITEM_ID
uv run laminar source list
uv run laminar scan --include-paid
uv run laminar scan -i -v
uv run laminar scan --source youtube
uv run laminar stats
```

`laminar source add` always takes the source URL as a positional argument. When `--type` is omitted, Laminar infers X URLs as `x`, YouTube URLs as `youtube`, and treats everything else as `feed`. If you pass `--type`, that explicit value wins. For now, the user-facing CLI only exposes `feed`, `youtube`, and `x`. Every option in `laminar source add --help` includes a description so the command is easier to discover.

X sources are treated as paid by default, including when the type is inferred from an X URL. Use `--paid` for any other source backed by a paid or metered API. Paid sources are skipped by default during `scan`; pass `--include-paid` when you want to scan them.

For X sources, Laminar resolves the source URL through `xurl` automatically. X list browser URLs like `https://x.com/i/lists/...` are detected automatically and translated into the corresponding list-tweets API request before invoking `xurl`; other X URLs are treated as user sources.

`laminar source remove` deletes the source definition and its scan history. Pass `--recursive` if you also want to delete all items already ingested from that source.

`laminar source show` prints the stored details for a single source as JSON, including its config fields, last successful scan timestamp, item count, and logical item size.

`laminar items remove` deletes a single stored item and its associated stored content. Like `items show`, it accepts an exact item ID, a unique item ID prefix, or a unique title.

Pass `-v` or `--verbose` to `laminar scan` when you want detailed progress logging, including the active incremental cutoff and when older entries are skipped because they are at or before that watermark. Use `-i` or `--include-paid` to include paid sources.

`laminar scan` stores the last successful scan time for each source and uses it as an incremental cutoff on later runs. Feed and YouTube scans assume reverse-chronological ordering and stop once they reach older entries; X scans invoke `xurl` for the configured source URL and then filter out items at or before the last successful scan time locally.

`laminar stats` shows an overview of total sources and items, plus approximate logical item size derived from the text stored in SQLite. It also groups counts and size by source kind and by individual source.
