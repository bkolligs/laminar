from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from laminar.models import NormalizedItem, SourceConfig, StoredItem


SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    costs_money INTEGER NOT NULL DEFAULT 0,
    provider TEXT,
    feed_url TEXT,
    handle TEXT,
    command_json TEXT NOT NULL DEFAULT '[]',
    transcript_languages_json TEXT NOT NULL DEFAULT '[]',
    poll_interval_minutes INTEGER,
    metadata_json TEXT NOT NULL,
    last_successful_scan_at TEXT,
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
    item_id TEXT PRIMARY KEY,
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
    item_id TEXT PRIMARY KEY,
    content_text TEXT,
    FOREIGN KEY(item_id) REFERENCES items(item_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS item_title_map (
    title TEXT NOT NULL,
    item_id TEXT NOT NULL,
    PRIMARY KEY (title, item_id),
    FOREIGN KEY(item_id) REFERENCES items(item_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_item_title_map_title
ON item_title_map(title);
"""


class Repository:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._ensure_schema_columns(conn)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def upsert_source(self, source: SourceConfig) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sources (
                    source_id, kind, label, enabled, costs_money, provider, feed_url, handle,
                    command_json, transcript_languages_json, poll_interval_minutes, metadata_json,
                    last_successful_scan_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    kind = excluded.kind,
                    label = excluded.label,
                    enabled = excluded.enabled,
                    costs_money = excluded.costs_money,
                    provider = excluded.provider,
                    feed_url = excluded.feed_url,
                    handle = excluded.handle,
                    command_json = excluded.command_json,
                    transcript_languages_json = excluded.transcript_languages_json,
                    poll_interval_minutes = excluded.poll_interval_minutes,
                    metadata_json = excluded.metadata_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    source.id,
                    source.kind,
                    source.label,
                    int(source.enabled),
                    int(source.costs_money),
                    source.provider,
                    source.feed_url,
                    source.handle,
                    json.dumps(source.command),
                    json.dumps(source.transcript_languages),
                    source.poll_interval_minutes,
                    json.dumps(source.metadata, sort_keys=True),
                    self.last_successful_scan_at(source.id),
                ),
            )

    def list_sources(self) -> list[SourceConfig]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    source_id,
                    kind,
                    label,
                    enabled,
                    costs_money,
                    provider,
                    feed_url,
                    handle,
                    command_json,
                    transcript_languages_json,
                    poll_interval_minutes,
                    metadata_json,
                    last_successful_scan_at
                FROM sources
                ORDER BY source_id
                """
            ).fetchall()
        return [_row_to_source(row) for row in rows]

    def remove_source(self, source_id: str, *, recursive: bool = False) -> int | None:
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM sources WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            if existing is None:
                return None

            item_rows = conn.execute(
                "SELECT item_id, title FROM items WHERE source_id = ?",
                (source_id,),
            ).fetchall()
            if item_rows and not recursive:
                raise ValueError(
                    f"Source {source_id} still has {len(item_rows)} items; rerun with --recursive"
                )

            for row in item_rows:
                conn.execute(
                    "DELETE FROM item_contents WHERE item_id = ?",
                    (str(row["item_id"]),),
                )
                conn.execute(
                    "DELETE FROM item_title_map WHERE item_id = ?",
                    (str(row["item_id"]),),
                )
            if item_rows:
                conn.execute("DELETE FROM items WHERE source_id = ?", (source_id,))
                for title in {str(row["title"]) for row in item_rows}:
                    self._refresh_title_map_for_title(conn, title)

            conn.execute("DELETE FROM scans WHERE source_id = ?", (source_id,))
            conn.execute("DELETE FROM sources WHERE source_id = ?", (source_id,))
            return len(item_rows)

    def last_successful_scan_at(self, source_id: str) -> datetime | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT last_successful_scan_at FROM sources WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        if row is None:
            return None
        return _parse_dt(row["last_successful_scan_at"])

    def mark_source_scan_succeeded(
        self,
        source_id: str,
        scanned_at: datetime | None = None,
    ) -> None:
        timestamp = scanned_at or datetime.now(timezone.utc)
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE sources
                SET last_successful_scan_at = ?, updated_at = CURRENT_TIMESTAMP
                WHERE source_id = ?
                """,
                (_dt(timestamp), source_id),
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
                    "SELECT item_id, title FROM items WHERE source_id = ? AND external_id = ?",
                    (item.source_id, item.external_id),
                ).fetchone()
            elif existing is not None:
                existing = conn.execute(
                    "SELECT item_id, title FROM items WHERE item_id = ?",
                    (existing["item_id"],),
                ).fetchone()

            if existing is None:
                item_id = item.item_id
                conn.execute(
                    """
                    INSERT INTO items (
                        item_id, source_id, item_type, external_id, canonical_url, title, author, published_at,
                        retrieved_at, excerpt, content_status, content_language, content_source,
                        raw_payload_json, content_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item_id,
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
                conn.execute(
                    "INSERT INTO item_contents (item_id, content_text) VALUES (?, ?)",
                    (item_id, item.content_text),
                )
                self._refresh_title_map_for_title(conn, item.title)
                return True

            item_id = str(existing["item_id"])
            previous_title = str(existing["title"])
            conn.execute(
                """
                UPDATE items
                SET external_id = ?, canonical_url = ?, title = ?, author = ?, published_at = ?, retrieved_at = ?, excerpt = ?,
                    content_status = ?, content_language = ?, content_source = ?,
                    raw_payload_json = ?, content_hash = ?
                WHERE item_id = ?
                """,
                (
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
            if previous_title != item.title:
                self._refresh_title_map_for_title(conn, previous_title)
            self._refresh_title_map_for_title(conn, item.title)
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

    def get_item(self, item_id: str) -> StoredItem | None:
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

    def remove_item(self, item_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT title FROM items WHERE item_id = ?",
                (item_id,),
            ).fetchone()
            if row is None:
                return False

            title = str(row["title"])
            conn.execute("DELETE FROM item_contents WHERE item_id = ?", (item_id,))
            conn.execute("DELETE FROM item_title_map WHERE item_id = ?", (item_id,))
            conn.execute("DELETE FROM items WHERE item_id = ?", (item_id,))
            self._refresh_title_map_for_title(conn, title)
            return True

    def find_items_by_id_prefix(self, prefix: str, *, limit: int = 10) -> list[StoredItem]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT i.*, c.content_text
                FROM items i
                LEFT JOIN item_contents c ON c.item_id = i.item_id
                WHERE i.item_id LIKE ? || '%'
                ORDER BY i.item_id
                LIMIT ?
                """,
                (prefix, limit),
            ).fetchall()
        return [_row_to_item(row) for row in rows]

    def shortest_unique_item_prefix(self, item_id: str) -> str:
        with self.connect() as conn:
            row = conn.execute(
                """
                WITH RECURSIVE prefix_lengths(length) AS (
                    SELECT 1
                    UNION ALL
                    SELECT length + 1
                    FROM prefix_lengths
                    WHERE length < length(?)
                )
                SELECT substr(?, 1, prefix_lengths.length) AS prefix
                FROM prefix_lengths
                WHERE (
                    SELECT COUNT(*)
                    FROM items i
                    WHERE i.item_id LIKE substr(?, 1, prefix_lengths.length) || '%'
                ) = 1
                ORDER BY prefix_lengths.length
                LIMIT 1
                """,
                (item_id, item_id, item_id),
            ).fetchone()
        if row is None:
            return item_id
        return str(row["prefix"])

    def find_items_by_title(self, title: str) -> list[StoredItem]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT i.*, c.content_text
                FROM item_title_map m
                JOIN items i ON i.item_id = m.item_id
                LEFT JOIN item_contents c ON c.item_id = i.item_id
                WHERE m.title = ?
                ORDER BY COALESCE(i.published_at, i.retrieved_at) DESC
                """,
                (title,),
            ).fetchall()
        return [_row_to_item(row) for row in rows]

    def lookup_titles_for_raw_title(self, raw_title: str) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT m.title
                FROM item_title_map m
                JOIN items i ON i.item_id = m.item_id
                WHERE i.title = ?
                ORDER BY m.title
                """,
                (raw_title,),
            ).fetchall()
        return [str(row["title"]) for row in rows]

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

    @staticmethod
    def _refresh_title_map_for_title(conn: sqlite3.Connection, title: str) -> None:
        rows = conn.execute(
            """
            SELECT i.item_id, i.title, COALESCE(s.label, i.source_id) AS source_name
            FROM items i
            LEFT JOIN sources s ON s.source_id = i.source_id
            WHERE i.title = ?
            ORDER BY COALESCE(i.published_at, i.retrieved_at) DESC, i.item_id
            """,
            (title,),
        ).fetchall()

        item_ids = [str(row["item_id"]) for row in rows]
        conn.execute("DELETE FROM item_title_map WHERE item_id IN (SELECT item_id FROM items WHERE title = ?)", (title,))
        if not rows:
            return

        if len(rows) == 1:
            conn.execute(
                "INSERT INTO item_title_map (title, item_id) VALUES (?, ?)",
                (title, item_ids[0]),
            )
            return

        used_names: set[str] = set()
        for row in rows:
            base_name = f"{row['title']} ({row['source_name']})"
            lookup_name = base_name
            if lookup_name in used_names:
                lookup_name = f"{base_name} [{str(row['item_id'])[:8]}]"
            used_names.add(lookup_name)
            conn.execute(
                "INSERT INTO item_title_map (title, item_id) VALUES (?, ?)",
                (lookup_name, str(row["item_id"])),
            )


    @staticmethod
    def _ensure_schema_columns(conn: sqlite3.Connection) -> None:
        source_columns = {
            str(row["name"]) for row in conn.execute("PRAGMA table_info(sources)").fetchall()
        }
        if "costs_money" not in source_columns:
            conn.execute(
                "ALTER TABLE sources ADD COLUMN costs_money INTEGER NOT NULL DEFAULT 0"
            )
        if "last_successful_scan_at" not in source_columns:
            conn.execute("ALTER TABLE sources ADD COLUMN last_successful_scan_at TEXT")


def _row_to_item(row: sqlite3.Row) -> StoredItem:
    return StoredItem(
        item_id=str(row["item_id"]),
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


def _row_to_source(row: sqlite3.Row) -> SourceConfig:
    kind = str(row["kind"])
    return SourceConfig(
        id=row["source_id"],
        kind=kind,
        label=row["label"],
        enabled=bool(row["enabled"]),
        costs_money=bool(row["costs_money"]) or kind == "x",
        provider=row["provider"],
        feed_url=row["feed_url"],
        handle=row["handle"],
        command=_json_list(row["command_json"]),
        transcript_languages=_json_list(row["transcript_languages_json"]),
        poll_interval_minutes=row["poll_interval_minutes"],
        metadata=_json_object(row["metadata_json"]),
    )


def _dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _json_list(value: str | None) -> list[str]:
    if not value:
        return []
    data = json.loads(value)
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, str)]


def _json_object(value: str | None) -> dict[str, object]:
    if not value:
        return {}
    data = json.loads(value)
    if not isinstance(data, dict):
        return {}
    return data
