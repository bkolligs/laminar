from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from laminar.models import NormalizedItem, SourceConfig, StoredItem


SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    provider TEXT,
    feed_url TEXT,
    handle TEXT,
    metadata_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scans (
    scan_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    source_id TEXT,
    status TEXT NOT NULL,
    items_seen INTEGER NOT NULL DEFAULT 0,
    items_new INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS items (
    item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    item_type TEXT NOT NULL,
    external_id TEXT,
    canonical_url TEXT,
    title TEXT NOT NULL,
    author TEXT,
    published_at TEXT,
    retrieved_at TEXT NOT NULL,
    excerpt TEXT,
    content_status TEXT NOT NULL,
    content_language TEXT,
    content_source TEXT,
    raw_payload_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    UNIQUE(source_id, external_id),
    FOREIGN KEY(source_id) REFERENCES sources(source_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_items_canonical_url
ON items(canonical_url)
WHERE canonical_url IS NOT NULL;

CREATE TABLE IF NOT EXISTS item_contents (
    item_id INTEGER PRIMARY KEY,
    content_text TEXT,
    FOREIGN KEY(item_id) REFERENCES items(item_id) ON DELETE CASCADE
);
"""


class Repository:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def sync_sources(self, sources: list[SourceConfig]) -> None:
        with self.connect() as conn:
            for source in sources:
                conn.execute(
                    """
                    INSERT INTO sources (
                        source_id, kind, label, enabled, provider, feed_url, handle, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_id) DO UPDATE SET
                        kind = excluded.kind,
                        label = excluded.label,
                        enabled = excluded.enabled,
                        provider = excluded.provider,
                        feed_url = excluded.feed_url,
                        handle = excluded.handle,
                        metadata_json = excluded.metadata_json,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        source.id,
                        source.kind,
                        source.label,
                        int(source.enabled),
                        source.provider,
                        source.feed_url,
                        source.handle,
                        json.dumps(source.metadata, sort_keys=True),
                    ),
                )

    def start_scan(self, source_id: str | None) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO scans (started_at, source_id, status) VALUES (CURRENT_TIMESTAMP, ?, ?)",
                (source_id, "running"),
            )
            return int(cursor.lastrowid)

    def finish_scan(
        self,
        scan_id: int,
        *,
        status: str,
        items_seen: int,
        items_new: int,
        error: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE scans
                SET finished_at = CURRENT_TIMESTAMP, status = ?, items_seen = ?, items_new = ?, error = ?
                WHERE scan_id = ?
                """,
                (status, items_seen, items_new, error, scan_id),
            )

    def upsert_item(self, item: NormalizedItem) -> bool:
        payload_json = json.dumps(item.raw_payload, sort_keys=True)
        content_hash = self._content_hash(item)
        with self.connect() as conn:
            existing = None
            if item.canonical_url:
                existing = conn.execute(
                    "SELECT item_id FROM items WHERE canonical_url = ?",
                    (item.canonical_url,),
                ).fetchone()
            if existing is None and item.external_id:
                existing = conn.execute(
                    "SELECT item_id FROM items WHERE source_id = ? AND external_id = ?",
                    (item.source_id, item.external_id),
                ).fetchone()

            if existing is None:
                cursor = conn.execute(
                    """
                    INSERT INTO items (
                        source_id, item_type, external_id, canonical_url, title, author, published_at,
                        retrieved_at, excerpt, content_status, content_language, content_source,
                        raw_payload_json, content_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.source_id,
                        item.item_type,
                        item.external_id,
                        item.canonical_url,
                        item.title,
                        item.author,
                        _dt(item.published_at),
                        _dt(item.retrieved_at),
                        item.excerpt,
                        item.content_status,
                        item.content_language,
                        item.content_source,
                        payload_json,
                        content_hash,
                    ),
                )
                item_id = int(cursor.lastrowid)
                conn.execute(
                    "INSERT INTO item_contents (item_id, content_text) VALUES (?, ?)",
                    (item_id, item.content_text),
                )
                return True

            item_id = int(existing["item_id"])
            conn.execute(
                """
                UPDATE items
                SET title = ?, author = ?, published_at = ?, retrieved_at = ?, excerpt = ?,
                    content_status = ?, content_language = ?, content_source = ?,
                    raw_payload_json = ?, content_hash = ?
                WHERE item_id = ?
                """,
                (
                    item.title,
                    item.author,
                    _dt(item.published_at),
                    _dt(item.retrieved_at),
                    item.excerpt,
                    item.content_status,
                    item.content_language,
                    item.content_source,
                    payload_json,
                    content_hash,
                    item_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO item_contents (item_id, content_text) VALUES (?, ?)
                ON CONFLICT(item_id) DO UPDATE SET content_text = excluded.content_text
                """,
                (item_id, item.content_text),
            )
            return False

    def list_items(
        self,
        *,
        source_id: str | None = None,
        item_type: str | None = None,
        limit: int = 20,
    ) -> list[StoredItem]:
        conditions = []
        params: list[object] = []
        if source_id:
            conditions.append("i.source_id = ?")
            params.append(source_id)
        if item_type:
            conditions.append("i.item_type = ?")
            params.append(item_type)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"""
            SELECT i.*, c.content_text
            FROM items i
            LEFT JOIN item_contents c ON c.item_id = i.item_id
            {where_clause}
            ORDER BY COALESCE(i.published_at, i.retrieved_at) DESC
            LIMIT ?
        """
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_item(row) for row in rows]

    def get_item(self, item_id: int) -> StoredItem | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT i.*, c.content_text
                FROM items i
                LEFT JOIN item_contents c ON c.item_id = i.item_id
                WHERE i.item_id = ?
                """,
                (item_id,),
            ).fetchone()
        return _row_to_item(row) if row else None

    def search(self, query: str, *, limit: int = 20) -> list[StoredItem]:
        pattern = f"%{query.lower()}%"
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT i.*, c.content_text
                FROM items i
                LEFT JOIN item_contents c ON c.item_id = i.item_id
                WHERE lower(i.title) LIKE ?
                   OR lower(COALESCE(i.excerpt, '')) LIKE ?
                   OR lower(COALESCE(c.content_text, '')) LIKE ?
                ORDER BY COALESCE(i.published_at, i.retrieved_at) DESC
                LIMIT ?
                """,
                (pattern, pattern, pattern, limit),
            ).fetchall()
        return [_row_to_item(row) for row in rows]

    @staticmethod
    def _content_hash(item: NormalizedItem) -> str:
        parts = [
            item.source_id,
            item.external_id or "",
            item.canonical_url or "",
            item.title,
            item.excerpt or "",
            item.content_text or "",
        ]
        return "|".join(parts)


def _row_to_item(row: sqlite3.Row) -> StoredItem:
    return StoredItem(
        item_id=int(row["item_id"]),
        source_id=row["source_id"],
        item_type=row["item_type"],
        title=row["title"],
        canonical_url=row["canonical_url"],
        published_at=_parse_dt(row["published_at"]),
        author=row["author"],
        excerpt=row["excerpt"],
        content_text=row["content_text"],
        content_status=row["content_status"],
        content_language=row["content_language"],
        content_source=row["content_source"],
        raw_payload=json.loads(row["raw_payload_json"]),
    )


def _dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)
