from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from laminar.models import (
    ContentStatus,
    NormalizedItem,
    RepositoryStats,
    ScanItemHistory,
    ScanRunDetail,
    ScanRunSummary,
    ScanSourceHistory,
    SourceConfig,
    SourceKindStats,
    SourceStats,
    StoredItem,
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    paid INTEGER NOT NULL DEFAULT 0,
    feed_url TEXT,
    handle TEXT,
    transcript_languages_json TEXT NOT NULL DEFAULT '[]',
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

CREATE TABLE IF NOT EXISTS scan_runs (
    scan_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    include_paid INTEGER NOT NULL DEFAULT 0,
    selected_source_kinds_json TEXT NOT NULL DEFAULT '[]',
    selected_source_ids_json TEXT NOT NULL DEFAULT '[]',
    sources_considered INTEGER NOT NULL DEFAULT 0,
    sources_scanned INTEGER NOT NULL DEFAULT 0,
    sources_skipped INTEGER NOT NULL DEFAULT 0,
    sources_failed INTEGER NOT NULL DEFAULT 0,
    items_seen INTEGER NOT NULL DEFAULT 0,
    items_new INTEGER NOT NULL DEFAULT 0,
    items_existing INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS scan_sources (
    scan_source_id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_run_id INTEGER NOT NULL,
    source_id TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_name TEXT NOT NULL,
    status TEXT NOT NULL,
    skip_reason TEXT,
    started_at TEXT,
    finished_at TEXT,
    cutoff_at TEXT,
    items_seen INTEGER NOT NULL DEFAULT 0,
    items_new INTEGER NOT NULL DEFAULT 0,
    items_existing INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    FOREIGN KEY(scan_run_id) REFERENCES scan_runs(scan_run_id)
);

CREATE TABLE IF NOT EXISTS scan_items (
    scan_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_source_id INTEGER NOT NULL,
    source_id TEXT NOT NULL,
    item_id TEXT,
    external_id TEXT,
    canonical_url TEXT,
    title TEXT NOT NULL,
    published_at TEXT,
    result TEXT NOT NULL,
    content_status TEXT NOT NULL,
    content_source TEXT,
    failure_reason TEXT,
    FOREIGN KEY(scan_source_id) REFERENCES scan_sources(scan_source_id)
);

CREATE INDEX IF NOT EXISTS idx_scan_sources_run_id
ON scan_sources(scan_run_id);

CREATE INDEX IF NOT EXISTS idx_scan_sources_source_id
ON scan_sources(source_id);

CREATE INDEX IF NOT EXISTS idx_scan_items_scan_source_id
ON scan_items(scan_source_id);

CREATE INDEX IF NOT EXISTS idx_scan_items_source_id
ON scan_items(source_id);

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
                    source_id, kind, name, enabled, paid, feed_url, handle,
                    transcript_languages_json, metadata_json,
                    last_successful_scan_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    kind = excluded.kind,
                    name = excluded.name,
                    enabled = excluded.enabled,
                    paid = excluded.paid,
                    feed_url = excluded.feed_url,
                    handle = excluded.handle,
                    transcript_languages_json = excluded.transcript_languages_json,
                    metadata_json = excluded.metadata_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    source.id,
                    source.kind,
                    source.name,
                    int(source.enabled),
                    int(source.paid),
                    source.feed_url,
                    source.handle,
                    json.dumps(source.transcript_languages),
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
                    name,
                    enabled,
                    paid,
                    feed_url,
                    handle,
                    transcript_languages_json,
                    metadata_json,
                    last_successful_scan_at
                FROM sources
                ORDER BY source_id
                """
            ).fetchall()
        return [_row_to_source(row) for row in rows]

    def get_source(self, source_id: str) -> SourceConfig | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    source_id,
                    kind,
                    name,
                    enabled,
                    paid,
                    feed_url,
                    handle,
                    transcript_languages_json,
                    metadata_json,
                    last_successful_scan_at
                FROM sources
                WHERE source_id = ?
                """,
                (source_id,),
            ).fetchone()
        return _row_to_source(row) if row else None

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

            scan_source_ids = [
                int(row["scan_source_id"])
                for row in conn.execute(
                    "SELECT scan_source_id FROM scan_sources WHERE source_id = ?",
                    (source_id,),
                ).fetchall()
            ]
            if scan_source_ids:
                placeholders = ",".join("?" for _ in scan_source_ids)
                conn.execute(
                    f"DELETE FROM scan_items WHERE scan_source_id IN ({placeholders})",
                    scan_source_ids,
                )
                conn.execute(
                    f"DELETE FROM scan_sources WHERE scan_source_id IN ({placeholders})",
                    scan_source_ids,
                )
                conn.execute(
                    """
                    DELETE FROM scan_runs
                    WHERE scan_run_id IN (
                        SELECT sr.scan_run_id
                        FROM scan_runs sr
                        LEFT JOIN scan_sources ss ON ss.scan_run_id = sr.scan_run_id
                        GROUP BY sr.scan_run_id
                        HAVING COUNT(ss.scan_source_id) = 0
                    )
                    """
                )

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

    def start_scan_run(
        self,
        *,
        include_paid: bool,
        selected_source_kinds: list[str],
        selected_source_ids: list[str],
        started_at: datetime | None = None,
    ) -> int:
        timestamp = started_at or datetime.now(timezone.utc)
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO scan_runs (
                    started_at,
                    status,
                    include_paid,
                    selected_source_kinds_json,
                    selected_source_ids_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    _dt(timestamp),
                    "running",
                    int(include_paid),
                    json.dumps(selected_source_kinds),
                    json.dumps(selected_source_ids),
                ),
            )
            return int(cursor.lastrowid)

    def record_scan_source(
        self,
        scan_run_id: int,
        *,
        source: SourceConfig,
        status: str,
        skip_reason: str | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        cutoff_at: datetime | None = None,
        items_seen: int,
        items_new: int,
        items_existing: int,
        error: str | None = None,
    ) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO scan_sources (
                    scan_run_id,
                    source_id,
                    source_kind,
                    source_name,
                    status,
                    skip_reason,
                    started_at,
                    finished_at,
                    cutoff_at,
                    items_seen,
                    items_new,
                    items_existing,
                    error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scan_run_id,
                    source.id,
                    source.kind,
                    source.name,
                    status,
                    skip_reason,
                    _dt(started_at),
                    _dt(finished_at),
                    _dt(cutoff_at),
                    items_seen,
                    items_new,
                    items_existing,
                    error,
                ),
            )
            return int(cursor.lastrowid)

    def update_scan_source(
        self,
        scan_source_id: int,
        *,
        status: str,
        skip_reason: str | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        cutoff_at: datetime | None = None,
        items_seen: int,
        items_new: int,
        items_existing: int,
        error: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE scan_sources
                SET status = ?, skip_reason = ?, started_at = ?, finished_at = ?, cutoff_at = ?,
                    items_seen = ?, items_new = ?, items_existing = ?, error = ?
                WHERE scan_source_id = ?
                """,
                (
                    status,
                    skip_reason,
                    _dt(started_at),
                    _dt(finished_at),
                    _dt(cutoff_at),
                    items_seen,
                    items_new,
                    items_existing,
                    error,
                    scan_source_id,
                ),
            )

    def record_scan_item(
        self,
        scan_source_id: int,
        item: NormalizedItem,
        *,
        result: str,
        failure_reason: str | None = None,
    ) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO scan_items (
                    scan_source_id,
                    source_id,
                    item_id,
                    external_id,
                    canonical_url,
                    title,
                    published_at,
                    result,
                    content_status,
                    content_source,
                    failure_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scan_source_id,
                    item.source_id,
                    item.item_id,
                    item.external_id,
                    item.canonical_url,
                    item.title,
                    _dt(item.published_at),
                    result,
                    item.content_status,
                    item.content_source,
                    failure_reason,
                ),
            )
            return int(cursor.lastrowid)

    def finish_scan_run(
        self,
        scan_run_id: int,
        *,
        status: str,
        sources_considered: int,
        sources_scanned: int,
        sources_skipped: int,
        sources_failed: int,
        items_seen: int,
        items_new: int,
        items_existing: int,
        error: str | None = None,
        finished_at: datetime | None = None,
    ) -> None:
        timestamp = finished_at or datetime.now(timezone.utc)
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE scan_runs
                SET finished_at = ?, status = ?, sources_considered = ?, sources_scanned = ?,
                    sources_skipped = ?, sources_failed = ?, items_seen = ?, items_new = ?,
                    items_existing = ?, error = ?
                WHERE scan_run_id = ?
                """,
                (
                    _dt(timestamp),
                    status,
                    sources_considered,
                    sources_scanned,
                    sources_skipped,
                    sources_failed,
                    items_seen,
                    items_new,
                    items_existing,
                    error,
                    scan_run_id,
                ),
            )

    def list_scan_runs(self, *, limit: int = 20) -> list[ScanRunSummary]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM scan_runs
                ORDER BY scan_run_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_row_to_scan_run(row) for row in rows]

    def get_scan_run(self, scan_run_id: int) -> ScanRunDetail | None:
        with self.connect() as conn:
            run_row = conn.execute(
                """
                SELECT *
                FROM scan_runs
                WHERE scan_run_id = ?
                """,
                (scan_run_id,),
            ).fetchone()
            if run_row is None:
                return None
            source_rows = conn.execute(
                """
                SELECT *
                FROM scan_sources
                WHERE scan_run_id = ?
                ORDER BY scan_source_id
                """,
                (scan_run_id,),
            ).fetchall()
            item_rows = conn.execute(
                """
                SELECT si.*
                FROM scan_items si
                JOIN scan_sources ss ON ss.scan_source_id = si.scan_source_id
                WHERE ss.scan_run_id = ?
                ORDER BY si.scan_item_id
                """,
                (scan_run_id,),
            ).fetchall()
        return ScanRunDetail(
            run=_row_to_scan_run(run_row),
            sources=[_row_to_scan_source(row) for row in source_rows],
            items=[_row_to_scan_item(row) for row in item_rows],
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
        limit: int | None = 20,
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
        limit_clause = "LIMIT ?" if limit is not None else ""
        query = f"""
            SELECT i.*, c.content_text
            FROM items i
            LEFT JOIN item_contents c ON c.item_id = i.item_id
            {where_clause}
            ORDER BY COALESCE(i.published_at, i.retrieved_at) DESC
            {limit_clause}
        """
        if limit is not None:
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

    def stats(self) -> RepositoryStats:
        with self.connect() as conn:
            totals = conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM sources) AS total_sources,
                    (SELECT COUNT(*) FROM items) AS total_items,
                    (
                        SELECT COALESCE(SUM(
                            length(CAST(i.item_id AS BLOB)) +
                            length(CAST(i.source_id AS BLOB)) +
                            length(CAST(i.item_type AS BLOB)) +
                            length(CAST(COALESCE(i.external_id, '') AS BLOB)) +
                            length(CAST(COALESCE(i.canonical_url, '') AS BLOB)) +
                            length(CAST(i.title AS BLOB)) +
                            length(CAST(COALESCE(i.author, '') AS BLOB)) +
                            length(CAST(COALESCE(i.published_at, '') AS BLOB)) +
                            length(CAST(i.retrieved_at AS BLOB)) +
                            length(CAST(COALESCE(i.excerpt, '') AS BLOB)) +
                            length(CAST(i.content_status AS BLOB)) +
                            length(CAST(COALESCE(i.content_language, '') AS BLOB)) +
                            length(CAST(COALESCE(i.content_source, '') AS BLOB)) +
                            length(CAST(i.raw_payload_json AS BLOB)) +
                            length(CAST(i.content_hash AS BLOB)) +
                            length(CAST(COALESCE(c.content_text, '') AS BLOB))
                        ), 0)
                        FROM items i
                        LEFT JOIN item_contents c ON c.item_id = i.item_id
                    ) AS total_size_bytes
                """
            ).fetchone()
            source_rows = conn.execute(
                """
                WITH source_item_stats AS (
                    SELECT
                        i.source_id,
                        COUNT(i.item_id) AS item_count,
                        COALESCE(SUM(
                            length(CAST(i.item_id AS BLOB)) +
                            length(CAST(i.source_id AS BLOB)) +
                            length(CAST(i.item_type AS BLOB)) +
                            length(CAST(COALESCE(i.external_id, '') AS BLOB)) +
                            length(CAST(COALESCE(i.canonical_url, '') AS BLOB)) +
                            length(CAST(i.title AS BLOB)) +
                            length(CAST(COALESCE(i.author, '') AS BLOB)) +
                            length(CAST(COALESCE(i.published_at, '') AS BLOB)) +
                            length(CAST(i.retrieved_at AS BLOB)) +
                            length(CAST(COALESCE(i.excerpt, '') AS BLOB)) +
                            length(CAST(i.content_status AS BLOB)) +
                            length(CAST(COALESCE(i.content_language, '') AS BLOB)) +
                            length(CAST(COALESCE(i.content_source, '') AS BLOB)) +
                            length(CAST(i.raw_payload_json AS BLOB)) +
                            length(CAST(i.content_hash AS BLOB)) +
                            length(CAST(COALESCE(c.content_text, '') AS BLOB))
                        ), 0) AS size_bytes
                    FROM items i
                    LEFT JOIN item_contents c ON c.item_id = i.item_id
                    GROUP BY i.source_id
                )
                SELECT
                    s.source_id,
                    s.name,
                    s.kind,
                    s.enabled,
                    s.paid,
                    COALESCE(sis.item_count, 0) AS item_count,
                    COALESCE(sis.size_bytes, 0) AS size_bytes
                FROM sources s
                LEFT JOIN source_item_stats sis ON sis.source_id = s.source_id
                UNION ALL
                SELECT
                    sis.source_id,
                    '[missing source]' AS name,
                    'missing' AS kind,
                    0 AS enabled,
                    0 AS paid,
                    sis.item_count,
                    sis.size_bytes
                FROM source_item_stats sis
                LEFT JOIN sources s ON s.source_id = sis.source_id
                WHERE s.source_id IS NULL
                ORDER BY 6 DESC, 7 DESC, 1
                """
            ).fetchall()
            kind_rows = conn.execute(
                """
                WITH source_rollups AS (
                    SELECT
                        s.source_id,
                        s.kind,
                        COUNT(i.item_id) AS item_count,
                        COALESCE(SUM(
                            length(CAST(i.item_id AS BLOB)) +
                            length(CAST(i.source_id AS BLOB)) +
                            length(CAST(i.item_type AS BLOB)) +
                            length(CAST(COALESCE(i.external_id, '') AS BLOB)) +
                            length(CAST(COALESCE(i.canonical_url, '') AS BLOB)) +
                            length(CAST(i.title AS BLOB)) +
                            length(CAST(COALESCE(i.author, '') AS BLOB)) +
                            length(CAST(COALESCE(i.published_at, '') AS BLOB)) +
                            length(CAST(i.retrieved_at AS BLOB)) +
                            length(CAST(COALESCE(i.excerpt, '') AS BLOB)) +
                            length(CAST(i.content_status AS BLOB)) +
                            length(CAST(COALESCE(i.content_language, '') AS BLOB)) +
                            length(CAST(COALESCE(i.content_source, '') AS BLOB)) +
                            length(CAST(i.raw_payload_json AS BLOB)) +
                            length(CAST(i.content_hash AS BLOB)) +
                            length(CAST(COALESCE(c.content_text, '') AS BLOB))
                        ), 0) AS size_bytes
                    FROM sources s
                    LEFT JOIN items i ON i.source_id = s.source_id
                    LEFT JOIN item_contents c ON c.item_id = i.item_id
                    GROUP BY s.source_id, s.kind
                    UNION ALL
                    SELECT
                        i.source_id,
                        'missing' AS kind,
                        COUNT(i.item_id) AS item_count,
                        COALESCE(SUM(
                            length(CAST(i.item_id AS BLOB)) +
                            length(CAST(i.source_id AS BLOB)) +
                            length(CAST(i.item_type AS BLOB)) +
                            length(CAST(COALESCE(i.external_id, '') AS BLOB)) +
                            length(CAST(COALESCE(i.canonical_url, '') AS BLOB)) +
                            length(CAST(i.title AS BLOB)) +
                            length(CAST(COALESCE(i.author, '') AS BLOB)) +
                            length(CAST(COALESCE(i.published_at, '') AS BLOB)) +
                            length(CAST(i.retrieved_at AS BLOB)) +
                            length(CAST(COALESCE(i.excerpt, '') AS BLOB)) +
                            length(CAST(i.content_status AS BLOB)) +
                            length(CAST(COALESCE(i.content_language, '') AS BLOB)) +
                            length(CAST(COALESCE(i.content_source, '') AS BLOB)) +
                            length(CAST(i.raw_payload_json AS BLOB)) +
                            length(CAST(i.content_hash AS BLOB)) +
                            length(CAST(COALESCE(c.content_text, '') AS BLOB))
                        ), 0) AS size_bytes
                    FROM items i
                    LEFT JOIN item_contents c ON c.item_id = i.item_id
                    LEFT JOIN sources s ON s.source_id = i.source_id
                    WHERE s.source_id IS NULL
                    GROUP BY i.source_id
                )
                SELECT
                    kind,
                    COUNT(*) AS source_count,
                    SUM(item_count) AS item_count,
                    SUM(size_bytes) AS size_bytes
                FROM source_rollups
                GROUP BY kind
                ORDER BY 2 DESC, 4 DESC, 1
                """
            ).fetchall()
        return RepositoryStats(
            total_sources=int(totals["total_sources"]),
            total_items=int(totals["total_items"]),
            total_size_bytes=int(totals["total_size_bytes"]),
            sources=[
                SourceStats(
                    source_id=str(row["source_id"]),
                    name=str(row["name"]),
                    kind=str(row["kind"]),
                    enabled=bool(row["enabled"]),
                    paid=bool(row["paid"]),
                    item_count=int(row["item_count"]),
                    size_bytes=int(row["size_bytes"]),
                )
                for row in source_rows
            ],
            kinds=[
                SourceKindStats(
                    kind=str(row["kind"]),
                    source_count=int(row["source_count"]),
                    item_count=int(row["item_count"]),
                    size_bytes=int(row["size_bytes"]),
                )
                for row in kind_rows
            ],
        )

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
            SELECT i.item_id, i.title, COALESCE(s.name, i.source_id) AS source_name
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

def _row_to_item(row: sqlite3.Row) -> StoredItem:
    return StoredItem(
        item_id=str(row["item_id"]),
        source_id=row["source_id"],
        item_type=row["item_type"],
        external_id=row["external_id"],
        title=row["title"],
        canonical_url=row["canonical_url"],
        published_at=_parse_dt(row["published_at"]),
        retrieved_at=_parse_dt(row["retrieved_at"]) or datetime.now(timezone.utc),
        author=row["author"],
        excerpt=row["excerpt"],
        content_text=row["content_text"],
        content_status=ContentStatus.coerce(str(row["content_status"])),
        content_language=row["content_language"],
        content_source=row["content_source"],
        raw_payload=json.loads(row["raw_payload_json"]),
    )


def _row_to_source(row: sqlite3.Row) -> SourceConfig:
    kind = str(row["kind"])
    return SourceConfig(
        id=row["source_id"],
        kind=kind,
        name=row["name"],
        enabled=bool(row["enabled"]),
        paid=bool(row["paid"]),
        feed_url=row["feed_url"],
        handle=row["handle"],
        transcript_languages=_json_list(row["transcript_languages_json"]),
        metadata=_json_object(row["metadata_json"]),
        last_successful_scan_at=_parse_dt(row["last_successful_scan_at"]),
    )


def _row_to_scan_run(row: sqlite3.Row) -> ScanRunSummary:
    return ScanRunSummary(
        scan_run_id=int(row["scan_run_id"]),
        started_at=_parse_dt(row["started_at"]) or datetime.now(timezone.utc),
        finished_at=_parse_dt(row["finished_at"]),
        status=str(row["status"]),
        include_paid=bool(row["include_paid"]),
        selected_source_kinds=_json_list(row["selected_source_kinds_json"]),
        selected_source_ids=_json_list(row["selected_source_ids_json"]),
        sources_considered=int(row["sources_considered"]),
        sources_scanned=int(row["sources_scanned"]),
        sources_skipped=int(row["sources_skipped"]),
        sources_failed=int(row["sources_failed"]),
        items_seen=int(row["items_seen"]),
        items_new=int(row["items_new"]),
        items_existing=int(row["items_existing"]),
        error=row["error"],
    )


def _row_to_scan_source(row: sqlite3.Row) -> ScanSourceHistory:
    return ScanSourceHistory(
        scan_source_id=int(row["scan_source_id"]),
        scan_run_id=int(row["scan_run_id"]),
        source_id=str(row["source_id"]),
        source_kind=str(row["source_kind"]),
        source_name=str(row["source_name"]),
        status=str(row["status"]),
        skip_reason=row["skip_reason"],
        started_at=_parse_dt(row["started_at"]),
        finished_at=_parse_dt(row["finished_at"]),
        cutoff_at=_parse_dt(row["cutoff_at"]),
        items_seen=int(row["items_seen"]),
        items_new=int(row["items_new"]),
        items_existing=int(row["items_existing"]),
        error=row["error"],
    )


def _row_to_scan_item(row: sqlite3.Row) -> ScanItemHistory:
    return ScanItemHistory(
        scan_item_id=int(row["scan_item_id"]),
        scan_source_id=int(row["scan_source_id"]),
        source_id=str(row["source_id"]),
        item_id=row["item_id"],
        external_id=row["external_id"],
        canonical_url=row["canonical_url"],
        title=str(row["title"]),
        published_at=_parse_dt(row["published_at"]),
        result=str(row["result"]),
        content_status=ContentStatus.coerce(str(row["content_status"])),
        content_source=row["content_source"],
        failure_reason=row["failure_reason"],
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
