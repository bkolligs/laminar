# AGENTS

## Project Summary

Laminar is a local-first Python CLI for ingesting updates from multiple content sources and storing them in SQLite for deterministic retrieval by downstream agents.

Current source types:
- feed
- youtube
- x

Current storage model:
- SQLite only
- default state directory: `~/.laminar`
- default config path: `~/.laminar/config.yaml`
- default database path: `~/.laminar/laminar.db`

## Tooling

- Package and environment management: `uv`
- Tests: `pytest`
- Entry point: `laminar`

Common commands:

```bash
uv sync
uv run pytest
uv run laminar source validate
uv run laminar source export sources.yaml
uv run laminar source import sources.yaml
uv run laminar scan
uv run laminar scan --include-paid
uv run laminar items list
uv run laminar items export items.yaml
uv run laminar items import items.yaml
```

## Code Layout

- `src/laminar/cli.py`: CLI argument parsing and command dispatch
- `src/laminar/config.py`: source config loading and validation
- `src/laminar/models.py`: typed data models
- `src/laminar/adapters.py`: source adapters for blog, YouTube, and X
- `src/laminar/youtube.py`: YouTube transcript retrieval helpers
- `src/laminar/fetch.py`: HTTP fetch helper
- `src/laminar/repository.py`: SQLite schema and persistence logic
- `tests/`: pytest suite

## Conventions

- Use Python 3.11+ features only.
- Keep the project stdlib-first unless a dependency clearly earns its complexity.
- Use `pytest`, not `unittest`.
- `main()` functions should be at the bottom of a file.
- Prefer small, typed helper functions over large command handlers.
- Keep provider-specific logic inside adapters or source-specific helper modules.
- Do not hardcode user-specific paths except for the default `~/.laminar` state location.

## Current Behavior Expectations

- Blog ingestion is RSS/Atom-style feed ingestion.
- `laminar source export/import` round-trip source definitions through YAML.
- `laminar items export/import` round-trip stored items through YAML.
- YouTube items should include transcript text when captions are available.
- X ingestion currently assumes `xurl` or another configured command returns JSON in the expected shape.
- X sources are treated as paid/metered by default, including legacy rows loaded from SQLite.
- Sources can also be explicitly marked as paid via `--costs-money`.
- Dedupe is based on canonical URL first, then `(source_id, external_id)`.
- Search is text-based over title, excerpt, and stored content text.
- `laminar scan` skips paid sources by default unless `--include-paid` is passed.
- `laminar scan` should continue after per-source failures, reporting reachable/unreachable status and coloring new items green, existing items cyan, failures red, and paid-source skips grey.

## Known Gaps

- YouTube live feed support is not robust yet; some real channels return `404` from YouTube feed endpoints.
- Semantic/vector search is not implemented yet.
- Config is YAML today.
- Email generation and digest synthesis are out of scope for the current CLI.

## Guidance For Future Agents

- Preserve the separation between config loading, adapters, repository logic, and CLI wiring.
- If adding a new source type, introduce it through a new adapter path and normalized item behavior instead of special-casing command handlers.
- If changing storage, keep migrations and backward compatibility in mind for the `~/.laminar/laminar.db` default.
- If adding external dependencies, update `pyproject.toml`, regenerate `uv.lock`, and run `uv run pytest`.
- If changing defaults or user-facing behavior, update `README.md` and tests in the same change.
