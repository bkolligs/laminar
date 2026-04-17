from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from laminar.adapters import build_adapter
from laminar.config import (
    AppConfig,
    ConfigError,
    ensure_default_config,
    normalize_source_kind,
    validate_source,
)
from laminar.models import SourceConfig
from laminar.repository import Repository
from rich.console import Console
from rich.table import Table


def _console() -> Console:
    return Console(file=sys.stdout)


def default_state_dir() -> Path:
    return Path.home() / ".laminar"


def default_config_path() -> Path:
    return default_state_dir() / "config.yaml"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="laminar", description="Ingest and query feed items."
    )
    parser.add_argument(
        "--config",
        default=str(default_config_path()),
        help="Path to behavior config.",
    )
    parser.add_argument(
        "--db",
        help="Path to SQLite database. Overrides database_path in config.yaml.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        dest="global_verbose",
        action="store_true",
        help="Show detailed scan and ingest logging.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    source_parser = subparsers.add_parser("source")
    source_parser.set_defaults(command="source")
    source_subparsers = source_parser.add_subparsers(
        dest="source_command", required=True
    )
    source_subparsers.add_parser("validate")
    source_subparsers.add_parser("list")
    show_source = source_subparsers.add_parser("show")
    show_source.add_argument("source_id")

    add_source = source_subparsers.add_parser("add")
    add_source.add_argument("--kind", required=True, choices=["feed", "youtube", "x"])
    add_source.add_argument("--label", required=True)
    add_source.add_argument("--provider")
    add_source.add_argument("--feed-url")
    add_source.add_argument("--handle")
    add_source.add_argument("--command", dest="source_exec", nargs="+")
    add_source.add_argument("--transcript-language", action="append", default=[])
    add_source.add_argument("--poll-interval-minutes", type=int)
    add_source.add_argument(
        "--costs-money",
        action="store_true",
        help="Mark this source as using a paid or metered integration.",
    )
    add_source.add_argument("--disable", action="store_true")
    add_source.add_argument(
        "--metadata",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Extra metadata to persist with the source.",
    )

    remove_source = source_subparsers.add_parser("remove")
    remove_source.add_argument("source_id")
    remove_source.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="Also delete this source's items.",
    )

    scan_parser = subparsers.add_parser("scan")
    scan_parser.set_defaults(command="scan")
    scan_parser.add_argument(
        "-i",
        "--include-paid",
        action="store_true",
        help="Include sources marked as paid or metered.",
    )
    scan_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show detailed scan and ingest logging.",
    )
    scan_parser.add_argument(
        "--source",
        dest="source_kinds",
        action="append",
        choices=["feed", "youtube", "x"],
        default=[],
        help="Limit scan to one or more source kinds.",
    )
    scan_parser.add_argument("source_ids", nargs="*")

    items_parser = subparsers.add_parser("items")
    items_parser.set_defaults(command="items")
    items_subparsers = items_parser.add_subparsers(dest="items_command", required=True)

    items_list = items_subparsers.add_parser("list")
    items_list.add_argument("--source")
    items_list.add_argument("--type")
    items_list.add_argument("--limit", type=int, default=20)

    items_show = items_subparsers.add_parser("show")
    items_show.add_argument("item_id")

    items_remove = items_subparsers.add_parser("remove")
    items_remove.add_argument("item_id")

    search_parser = subparsers.add_parser("search")
    search_parser.set_defaults(command="search")
    search_parser.add_argument("query")
    search_parser.add_argument("--limit", type=int, default=20)

    stats_parser = subparsers.add_parser("stats")
    stats_parser.set_defaults(command="stats")

    return parser


def run(args: argparse.Namespace) -> int:
    command = args.command
    if command is None and hasattr(args, "source_command"):
        command = "source"
    if command is None and hasattr(args, "items_command"):
        command = "items"
    if command == "source":
        return _run_source(args)
    if command == "scan":
        return _run_scan(args)
    if command == "items":
        return _run_items(args)
    if command == "search":
        return _run_search(args)
    if command == "stats":
        return _run_stats(args)
    raise ValueError(f"Unsupported command: {command}")


def _run_source(args: argparse.Namespace) -> int:
    runtime = _load_runtime(args)
    repo = Repository(runtime.database_path)
    if args.source_command == "add":
        source = _build_source_from_args(args)
        validate_source(source)
        repo.upsert_source(source)
        _console().print(f"Saved source {source.id} in {runtime.database_path}")
        return 0
    if args.source_command == "remove":
        try:
            removed_items = repo.remove_source(
                args.source_id,
                recursive=args.recursive,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if removed_items is None:
            print(f"Source {args.source_id} not found", file=sys.stderr)
            return 1
        if args.recursive:
            _console().print(
                f"Removed source {args.source_id} and deleted {removed_items} items from {runtime.database_path}"
            )
        else:
            _console().print(f"Removed source {args.source_id} from {runtime.database_path}")
        return 0
    if args.source_command == "show":
        source = repo.get_source(args.source_id)
        if source is None:
            print(f"Source {args.source_id} not found", file=sys.stderr)
            return 1
        source_stats = next(
            (entry for entry in repo.stats().sources if entry.source_id == source.id),
            None,
        )
        payload = {
            "source_id": source.id,
            "kind": source.kind,
            "label": source.label,
            "enabled": source.enabled,
            "costs_money": source.costs_money,
            "provider": source.provider,
            "feed_url": source.feed_url,
            "handle": source.handle,
            "command": source.command,
            "transcript_languages": source.transcript_languages,
            "poll_interval_minutes": source.poll_interval_minutes,
            "metadata": source.metadata,
            "last_successful_scan_at": (
                source.last_successful_scan_at.isoformat()
                if source.last_successful_scan_at
                else None
            ),
            "item_count": source_stats.item_count if source_stats else 0,
            "logical_item_size_bytes": source_stats.size_bytes if source_stats else 0,
        }
        _print_json(payload)
        return 0

    sources = repo.list_sources()
    if args.source_command == "validate":
        for source in sources:
            validate_source(source)
        _console().print(f"Validated {len(sources)} sources in {runtime.database_path}")
        return 0

    table = Table(title="Sources")
    table.add_column("ID", style="cyan")
    table.add_column("Kind", style="magenta")
    table.add_column("Status")
    table.add_column("Cost")
    table.add_column("Label", style="bold")
    for source in sources:
        table.add_row(
            source.id,
            source.kind,
            "enabled" if source.enabled else "disabled",
            "paid" if source.costs_money else "free",
            source.label,
        )
    _console().print(table)
    return 0


def _run_scan(args: argparse.Namespace) -> int:
    runtime = _load_runtime(args)
    repo = Repository(runtime.database_path)
    console = _console()
    verbose = bool(getattr(args, "verbose", False) or getattr(args, "global_verbose", False))

    def verbose_print(message: str) -> None:
        if verbose:
            console.print(message, style="dim", soft_wrap=True)

    sources = repo.list_sources()
    selected = {source_id for source_id in args.source_ids}
    selected_kinds = {normalize_source_kind(kind) for kind in args.source_kinds}
    active_sources = [
        source
        for source in sources
        if source.enabled
        and (not selected or source.id in selected)
        and (
            not selected_kinds
            or normalize_source_kind(source.kind) in selected_kinds
        )
    ]

    total_seen = 0
    total_new = 0
    total_failed = 0
    total_skipped = 0
    verbose_print(
        f"scan configuration: {len(active_sources)} active sources, include_paid={args.include_paid}, selected_kinds={sorted(selected_kinds) or ['all']}, selected_ids={sorted(selected) or ['all']}"
    )
    for source in active_sources:
        if source.costs_money and not args.include_paid:
            console.print(
                f"Skipping {source.id} ({source.label}): paid source; rerun with --include-paid",
                style="grey50",
            )
            total_skipped += 1
            continue
        scan_started_at = datetime.now(timezone.utc)
        scan_id = repo.start_scan(source.id)
        console.print(f"Scanning {source.id} ({source.label})")
        if source.costs_money:
            console.print(
                f"{source.id}: this source uses a paid or metered integration",
                style="yellow",
            )
        try:
            adapter = build_adapter(source)
            previous_scan_at = repo.last_successful_scan_at(source.id)
            if previous_scan_at is None:
                verbose_print(f"{source.id}: no previous successful scan; ingesting all available items")
            else:
                verbose_print(
                    f"{source.id}: incremental cutoff is {previous_scan_at.isoformat()}"
                )
            items = adapter.scan(
                source,
                since=previous_scan_at,
                verbose=verbose_print,
            )
            verbose_print(
                f"{source.id}: adapter returned {len(items)} items after cutoff filtering"
            )
            console.print(f"{source.id}: reachable", style="green")
            new_count = 0
            for item in items:
                if repo.upsert_item(item):
                    new_count += 1
                    console.print(f"{source.id}: new {item.title}", style="green")
                else:
                    console.print(f"{source.id}: existing {item.title}", style="cyan")
            repo.finish_scan(
                scan_id, status="success", items_seen=len(items), items_new=new_count
            )
            repo.mark_source_scan_succeeded(source.id, scan_started_at)
            verbose_print(
                f"{source.id}: marked successful scan watermark at {scan_started_at.isoformat()}"
            )
            total_seen += len(items)
            total_new += new_count
        except Exception as exc:
            repo.finish_scan(
                scan_id, status="failed", items_seen=0, items_new=0, error=str(exc)
            )
            total_failed += 1
            console.print(f"{source.id}: unreachable - {exc}", style="red")
            continue
    console.print(
        f"scan complete: {total_seen} items seen, {total_new} new, {total_failed} failed, {total_skipped} skipped"
    )
    return 0


def _run_items(args: argparse.Namespace) -> int:
    runtime = _load_runtime(args)
    repo = Repository(runtime.database_path)
    if args.items_command == "list":
        items = repo.list_items(
            source_id=args.source, item_type=args.type, limit=args.limit
        )
        table = Table(title="Items")
        table.add_column("ID", style="cyan")
        table.add_column("Type", style="magenta")
        table.add_column("Source")
        table.add_column("Published")
        table.add_column("Title", style="bold")
        for item in items:
            published = item.published_at.isoformat() if item.published_at else "-"
            table.add_row(
                item.item_id,
                item.item_type,
                item.source_id,
                published,
                item.title,
            )
        _console().print(table)
        return 0

    item = _resolve_item(repo, args.item_id)
    if item is None:
        return 1

    if args.items_command == "remove":
        repo.remove_item(item.item_id)
        _console().print(f"Removed item {item.item_id} from {runtime.database_path}")
        return 0

    payload = {
        "item_id": item.item_id,
        "source_id": item.source_id,
        "item_type": item.item_type,
        "title": item.title,
        "canonical_url": item.canonical_url,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "author": item.author,
        "excerpt": item.excerpt,
        "content_status": item.content_status,
        "content_language": item.content_language,
        "content_source": item.content_source,
        "content_text": item.content_text,
        "raw_payload": item.raw_payload,
    }
    _print_json(payload)
    return 0


def _run_search(args: argparse.Namespace) -> int:
    runtime = _load_runtime(args)
    repo = Repository(runtime.database_path)
    items = repo.search(args.query, limit=args.limit)
    table = Table(title=f"Search Results: {args.query}")
    table.add_column("ID", style="cyan")
    table.add_column("Type", style="magenta")
    table.add_column("Source")
    table.add_column("Title", style="bold")
    for item in items:
        table.add_row(item.item_id, item.item_type, item.source_id, item.title)
    _console().print(table)
    return 0


def _run_stats(args: argparse.Namespace) -> int:
    runtime = _load_runtime(args)
    repo = Repository(runtime.database_path)
    stats = repo.stats()
    console = _console()

    overview = Table(title="Overview")
    overview.add_column("Metric", style="cyan")
    overview.add_column("Value", justify="right", style="bold")
    overview.add_row("Sources", str(stats.total_sources))
    overview.add_row("Items", str(stats.total_items))
    overview.add_row("Logical Item Size", _format_bytes(stats.total_size_bytes))
    console.print(overview)

    kind_table = Table(title="Sources by Kind")
    kind_table.add_column("Kind", style="magenta")
    kind_table.add_column("Sources", justify="right")
    kind_table.add_column("Items", justify="right")
    kind_table.add_column("Size", justify="right")
    for kind in stats.kinds:
        kind_table.add_row(
            kind.kind,
            str(kind.source_count),
            str(kind.item_count),
            _format_bytes(kind.size_bytes),
        )
    console.print(kind_table)

    source_table = Table(title="Items by Source")
    source_table.add_column("ID", style="cyan")
    source_table.add_column("Label", style="bold")
    source_table.add_column("Kind", style="magenta")
    source_table.add_column("Status")
    source_table.add_column("Cost")
    source_table.add_column("Items", justify="right")
    source_table.add_column("Size", justify="right")
    for source in stats.sources:
        source_table.add_row(
            source.source_id,
            source.label,
            source.kind,
            "enabled" if source.enabled else "disabled",
            "paid" if source.costs_money else "free",
            str(source.item_count),
            _format_bytes(source.size_bytes),
        )
    console.print(source_table)
    return 0


def _format_bytes(size_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(size_bytes)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size_bytes} B"


def _print_json(payload: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
    sys.stdout.write("\n")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        exit_code = run(args)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    except Exception as exc:  # pragma: no cover - defensive top-level fallback
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    raise SystemExit(exit_code)


def _load_runtime(args: argparse.Namespace) -> AppConfig:
    runtime = ensure_default_config(args.config)
    if args.db:
        return AppConfig(
            config_path=runtime.config_path,
            database_path=Path(args.db),
        )
    return runtime


def _build_source_from_args(args: argparse.Namespace) -> SourceConfig:
    costs_money = args.costs_money or args.kind == "x"
    return SourceConfig(
        id=str(uuid4()),
        kind=normalize_source_kind(args.kind),
        label=args.label,
        enabled=not args.disable,
        costs_money=costs_money,
        provider=args.provider,
        feed_url=args.feed_url,
        handle=args.handle,
        command=args.source_exec or [],
        transcript_languages=args.transcript_language,
        poll_interval_minutes=args.poll_interval_minutes,
        metadata=_parse_metadata(args.metadata),
    )


def _parse_metadata(pairs: list[str]) -> dict[str, object]:
    metadata: dict[str, object] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ConfigError(f"Metadata entries must be KEY=VALUE pairs: {pair}")
        key, value = pair.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise ConfigError(f"Metadata entries must be KEY=VALUE pairs: {pair}")
        metadata[key] = value
    return metadata


def _resolve_item(repo: Repository, item_ref: str):
    item = repo.get_item(item_ref)
    if item is None:
        prefix_matches = repo.find_items_by_id_prefix(item_ref)
        if len(prefix_matches) == 1:
            item = prefix_matches[0]
        elif len(prefix_matches) > 1:
            print(
                f"Item ID prefix {item_ref!r} is ambiguous. Try one of:",
                file=sys.stderr,
            )
            for match in prefix_matches:
                print(
                    f"- {repo.shortest_unique_item_prefix(match.item_id)}\t{match.title}",
                    file=sys.stderr,
                )
            return None
    if item is None:
        matches = repo.find_items_by_title(item_ref)
        if len(matches) == 1:
            item = matches[0]
        elif len(matches) > 1:
            print(
                f"Multiple items found with title {item_ref!r}; use the item ID instead.",
                file=sys.stderr,
            )
            return None
    if item is None:
        title_candidates = repo.lookup_titles_for_raw_title(item_ref)
        if len(title_candidates) > 1:
            print(
                f"Multiple items share the title {item_ref!r}. Try one of:",
                file=sys.stderr,
            )
            for candidate in title_candidates:
                print(f"- {candidate}", file=sys.stderr)
            return None
        print(f"Item {item_ref} not found", file=sys.stderr)
        return None
    return item
