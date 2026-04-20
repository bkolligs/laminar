from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> str:
    return str(uuid4())


class ContentStatus(StrEnum):
    AVAILABLE = "available"
    MISSING = "missing"
    FETCH_FAILED = "fetch_failed"
    RATE_LIMITED = "rate_limited"

    @classmethod
    def coerce(cls, value: "ContentStatus | str") -> "ContentStatus":
        if isinstance(value, cls):
            return value
        return cls(value)


@dataclass(slots=True)
class SourceConfig:
    id: str
    kind: str
    name: str
    enabled: bool = True
    costs_money: bool = False
    feed_url: str | None = None
    handle: str | None = None
    transcript_languages: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    last_successful_scan_at: datetime | None = None


@dataclass(slots=True)
class NormalizedItem:
    source_id: str
    item_type: str
    external_id: str | None
    canonical_url: str | None
    title: str
    author: str | None
    published_at: datetime | None
    excerpt: str | None
    content_text: str | None = None
    content_status: ContentStatus = ContentStatus.AVAILABLE
    content_language: str | None = None
    content_source: str | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)
    retrieved_at: datetime = field(default_factory=utc_now)
    item_id: str = field(default_factory=new_uuid)


@dataclass(slots=True)
class StoredItem:
    item_id: str
    source_id: str
    item_type: str
    external_id: str | None
    title: str
    canonical_url: str | None
    published_at: datetime | None
    retrieved_at: datetime
    author: str | None
    excerpt: str | None
    content_text: str | None
    content_status: ContentStatus
    content_language: str | None
    content_source: str | None
    raw_payload: dict[str, Any]


@dataclass(slots=True)
class SourceStats:
    source_id: str
    name: str
    kind: str
    enabled: bool
    costs_money: bool
    item_count: int
    size_bytes: int


@dataclass(slots=True)
class SourceKindStats:
    kind: str
    source_count: int
    item_count: int
    size_bytes: int


@dataclass(slots=True)
class RepositoryStats:
    total_sources: int
    total_items: int
    total_size_bytes: int
    sources: list[SourceStats]
    kinds: list[SourceKindStats]


@dataclass(slots=True)
class ScanRunSummary:
    scan_run_id: int
    started_at: datetime
    finished_at: datetime | None
    status: str
    include_paid: bool
    selected_source_kinds: list[str]
    selected_source_ids: list[str]
    sources_considered: int
    sources_scanned: int
    sources_skipped: int
    sources_failed: int
    items_seen: int
    items_new: int
    items_existing: int
    error: str | None = None


@dataclass(slots=True)
class ScanSourceHistory:
    scan_source_id: int
    scan_run_id: int
    source_id: str
    source_kind: str
    source_name: str
    status: str
    skip_reason: str | None
    started_at: datetime | None
    finished_at: datetime | None
    cutoff_at: datetime | None
    items_seen: int
    items_new: int
    items_existing: int
    error: str | None = None


@dataclass(slots=True)
class ScanItemHistory:
    scan_item_id: int
    scan_source_id: int
    source_id: str
    item_id: str | None
    external_id: str | None
    canonical_url: str | None
    title: str
    published_at: datetime | None
    result: str
    content_status: ContentStatus
    content_source: str | None
    failure_reason: str | None = None


@dataclass(slots=True)
class ScanRunDetail:
    run: ScanRunSummary
    sources: list[ScanSourceHistory]
    items: list[ScanItemHistory]
