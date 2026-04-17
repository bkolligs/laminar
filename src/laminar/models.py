from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> str:
    return str(uuid4())


@dataclass(slots=True)
class SourceConfig:
    id: str
    kind: str
    label: str
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
    content_status: str = "available"
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
    title: str
    canonical_url: str | None
    published_at: datetime | None
    author: str | None
    excerpt: str | None
    content_text: str | None
    content_status: str
    content_language: str | None
    content_source: str | None
    raw_payload: dict[str, Any]


@dataclass(slots=True)
class SourceStats:
    source_id: str
    label: str
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
