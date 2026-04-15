from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from laminar.adapters import build_adapter
from laminar.config import ConfigError, load_config
from laminar.repository import Repository


def default_state_dir() -> Path:
    return Path.home() / ".laminar"


def default_config_path() -> Path:
    return default_state_dir() / "config.toml"


def default_db_path() -> Path:
    return default_state_dir() / "laminar.db"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="laminar", description="Ingest and query feed items."
    )
    parser.add_argument(
        "--config",
        default=str(default_config_path()),
        help="Path to source config.",
    )
    parser.add_argument(
        "--db",
        default=str(default_db_path()),
        help="Path to SQLite database.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    source_parser = subparsers.add_parser("source")
    source_subparsers = source_parser.add_subparsers(
        dest="source_command", required=True
    )
    source_subparsers.add_parser("validate")
    source_subparsers.add_parser("list")

    scan_parser = subparsers.add_parser("scan")
    scan_parser.add_argument("source_ids", nargs="*")

    items_parser = subparsers.add_parser("items")
    items_subparsers = items_parser.add_subparsers(dest="items_command", required=True)

    items_list = items_subparsers.add_parser("list")
    items_list.add_argument("--source")
    items_list.add_argument("--type")
    items_list.add_argument("--limit", type=int, default=20)

    items_show = items_subparsers.add_parser("show")
    items_show.add_argument("item_id", type=int)

    search_parser = subparsers.add_parser("search")
    search_parser.add_argument("query")
    search_parser.add_argument("--limit", type=int, default=20)

    return parser


def run(args: argparse.Namespace) -> int:
    command = args.command
    if command == "source":
        return _run_source(args)
    if command == "scan":
        return _run_scan(args)
    if command == "items":
        return _run_items(args)
    if command == "search":
        return _run_search(args)
    raise ValueError(f"Unsupported command: {command}")


def _run_source(args: argparse.Namespace) -> int:
    sources = load_config(args.config)
    repo = Repository(args.db)
    repo.sync_sources(sources)
    if args.source_command == "validate":
        print(f"Validated {len(sources)} sources from {args.config}")
        return 0

    for source in sources:
        status = "enabled" if source.enabled else "disabled"
        print(f"{source.id}\t{source.kind}\t{status}\t{source.label}")
    return 0


def _run_scan(args: argparse.Namespace) -> int:
    sources = load_config(args.config)
    selected = {source_id for source_id in args.source_ids}
    active_sources = [
        source
        for source in sources
        if source.enabled and (not selected or source.id in selected)
    ]

    repo = Repository(args.db)
    repo.sync_sources(sources)
    total_seen = 0
    total_new = 0
    for source in active_sources:
        scan_id = repo.start_scan(source.id)
        try:
            adapter = build_adapter(source)
            items = adapter.scan(source)
            new_count = 0
            for item in items:
                if repo.upsert_item(item):
                    new_count += 1
            repo.finish_scan(
                scan_id, status="success", items_seen=len(items), items_new=new_count
            )
            total_seen += len(items)
            total_new += new_count
            print(f"{source.id}: scanned {len(items)} items, {new_count} new")
        except Exception as exc:
            repo.finish_scan(
                scan_id, status="failed", items_seen=0, items_new=0, error=str(exc)
            )
            raise
    print(f"scan complete: {total_seen} items seen, {total_new} new")
    return 0


def _run_items(args: argparse.Namespace) -> int:
    repo = Repository(args.db)
    if args.items_command == "list":
        items = repo.list_items(
            source_id=args.source, item_type=args.type, limit=args.limit
        )
        for item in items:
            published = item.published_at.isoformat() if item.published_at else "-"
            print(
                f"{item.item_id}\t{item.item_type}\t{item.source_id}\t{published}\t{item.title}"
            )
        return 0

    item = repo.get_item(args.item_id)
    if item is None:
        print(f"Item {args.item_id} not found", file=sys.stderr)
        return 1

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
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _run_search(args: argparse.Namespace) -> int:
    repo = Repository(args.db)
    items = repo.search(args.query, limit=args.limit)
    for item in items:
        print(f"{item.item_id}\t{item.item_type}\t{item.source_id}\t{item.title}")
    return 0


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
