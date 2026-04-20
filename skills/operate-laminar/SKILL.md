---
name: operate-laminar
description: Operate the Laminar local-first CLI for source management, source and item import/export, scans, search, stats, and ingestion debugging. Use when Codex needs to add, validate, show, list, import, export, or remove Laminar sources; import, export, inspect, or remove stored items; run `laminar scan`; troubleshoot feed, YouTube, or X ingestion; or make Laminar code changes in this repository while preserving the CLI/config/adapter/repository split.
---

# Operate Laminar

Use Laminar through its CLI first. Prefer `uv run laminar ...` for user-facing workflows and use direct Python/module inspection only when debugging implementation details or writing tests.

Laminar is local-first and SQLite-backed. By default it uses `~/.laminar/config.yaml` and `~/.laminar/laminar.db`, so for tests and reproductions prefer explicit `--config` and `--db` paths to avoid touching a real user state directory unless the task is explicitly about the default install.

## Quick Start

- Run setup with `uv sync` if the environment is not ready.
- Validate config and stored sources with `uv run laminar source validate`.
- Export or import sources with `uv run laminar source export sources.yaml` and `uv run laminar source import sources.yaml`.
- Run scans with `uv run laminar scan` or `uv run laminar scan --include-paid`.
- Export or import items with `uv run laminar items export items.yaml` and `uv run laminar items import items.yaml`.
- Inspect results with `uv run laminar items list`, `uv run laminar search <query>`, and `uv run laminar stats`.
- Run `uv run pytest` after behavior changes. Update `README.md` and tests when defaults or user-facing behavior change.

## Source Workflows

Use `laminar source add --name <NAME> URL` as the normal way to create sources.

- `--type` accepts `feed`, `youtube`, or `x`. If omitted, Laminar infers X URLs as `x`, YouTube feed URLs as `youtube`, and everything else as a feed source.
- The source URL is positional. Do not rewrite it into a named flag.
- For YouTube, use repeatable `--transcript-language` flags when transcript preference matters.
- X sources are treated as paid by default, including inferred X URLs. Other paid sources should be marked with `--paid`.
- Use `--disable` to save a source without scanning it yet.
- Use repeatable `--metadata KEY=VALUE` to preserve extra source metadata.

Use these commands for inspection and cleanup:

- `uv run laminar source list`
- `uv run laminar source show SOURCE_ID`
- `uv run laminar source export [PATH]`
- `uv run laminar source import PATH`
- `uv run laminar source remove SOURCE_ID`
- `uv run laminar source remove --recursive SOURCE_ID`

Prefer the CLI over manual database edits when changing source state.

Source import/export expectations:

- `laminar source export` writes all stored sources to YAML, including source IDs, metadata, transcript languages, and `last_successful_scan_at`. When `PATH` is omitted, it writes YAML to stdout.
- `laminar source import` upserts sources by source ID.

## Scan And Retrieval Workflows

Use `laminar scan` as the primary ingest entry point.

- Paid sources are skipped by default. Pass `--include-paid` when the task requires scanning them.
- Scans should continue after per-source failures and report status per source.
- Use `-v` or `--verbose` when debugging incremental cutoffs or skipped entries.
- Use `--source feed`, `--source youtube`, or `--source x` to limit scan kinds.
- Pass source IDs after `scan` to target specific configured sources.

Use these commands to inspect what Laminar stored:

- `uv run laminar items list --limit 20`
- `uv run laminar items list --source SOURCE_ID`
- `uv run laminar items show ITEM_ID`
- `uv run laminar items export [PATH]`
- `uv run laminar items export [PATH] --source SOURCE_ID`
- `uv run laminar items export [PATH] --type video`
- `uv run laminar items import PATH`
- `uv run laminar items remove ITEM_ID`
- `uv run laminar search "query terms"`
- `uv run laminar stats`

Search is text-based over title, excerpt, and stored content text. YouTube items should include transcript text when captions are available.

Item import/export expectations:

- `laminar items export` writes stored items to YAML and supports `--source` and `--type` filters. When `PATH` is omitted, it writes YAML to stdout.
- `laminar items import` upserts items using canonical URL first, then `(source_id, external_id)`.
- Importing items does not create source definitions. If imported items reference missing source IDs, Laminar keeps the items and reports those sources as `[missing source]` in stats until the sources are added or imported separately.

## Implementation Guardrails

Keep Laminar's layering intact when editing code:

- `src/laminar/cli.py`: argument parsing and command dispatch
- `src/laminar/config.py`: config loading and validation
- `src/laminar/adapters.py`: source-kind-specific ingestion behavior
- `src/laminar/youtube.py`: transcript helpers
- `src/laminar/repository.py`: SQLite schema and persistence

Preserve these expectations unless the user asks to change them:

- Dedupe uses canonical URL first, then `(source_id, external_id)`.
- X ingestion relies on `xurl` or a compatible command returning the expected JSON shape.
- X sources, including legacy SQLite rows, are treated as paid by default.
- `laminar scan` skips paid sources unless `--include-paid` is set.
- Blog ingestion is RSS/Atom style feed ingestion.
- YouTube live feed support is known to be imperfect.

Avoid hardcoding user-specific paths other than the default `~/.laminar` state location.

## Validation

After code or behavior changes, run the narrowest useful test plus the full suite when practical.

- Start with targeted tests such as `uv run pytest tests/test_cli.py` or another affected file.
- Run `uv run pytest` before wrapping up if the change touches user-visible behavior, persistence, or adapters.
- If you changed CLI defaults, help text, or behavior described to users, update `README.md` in the same change.

When reproducing CLI behavior in tests, prefer temporary config and database paths instead of the default `~/.laminar` state.
