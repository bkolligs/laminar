from __future__ import annotations

from pathlib import Path

import yaml

from laminar.models import SourceConfig


class ConfigError(ValueError):
    pass


def load_config(path: str | Path) -> list[SourceConfig]:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")

    try:
        data = yaml.safe_load(config_path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("Config must be a mapping with a top-level 'sources' key")

    raw_sources = data.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ConfigError("Config must define at least one source entry under 'sources'")

    sources: list[SourceConfig] = []
    seen_ids: set[str] = set()
    for index, raw_source in enumerate(raw_sources, start=1):
        if not isinstance(raw_source, dict):
            raise ConfigError(f"Source entry #{index} must be a mapping")

        source_id = _require_str(raw_source, "id")
        if source_id in seen_ids:
            raise ConfigError(f"Duplicate source id: {source_id}")
        seen_ids.add(source_id)

        kind = _require_str(raw_source, "kind")
        label = _require_str(raw_source, "label")
        enabled = bool(raw_source.get("enabled", True))
        provider = _optional_str(raw_source, "provider")
        feed_url = _optional_str(raw_source, "feed_url")
        handle = _optional_str(raw_source, "handle")
        command = _optional_str_list(raw_source, "command")
        transcript_languages = _optional_str_list(raw_source, "transcript_languages")
        poll_interval = raw_source.get("poll_interval_minutes")
        if poll_interval is not None and not isinstance(poll_interval, int):
            raise ConfigError(
                f"Source {source_id}: poll_interval_minutes must be an integer"
            )

        metadata = {
            key: value
            for key, value in raw_source.items()
            if key
            not in {
                "id",
                "kind",
                "label",
                "enabled",
                "provider",
                "feed_url",
                "handle",
                "command",
                "transcript_languages",
                "poll_interval_minutes",
            }
        }

        source = SourceConfig(
            id=source_id,
            kind=kind,
            label=label,
            enabled=enabled,
            provider=provider,
            feed_url=feed_url,
            handle=handle,
            command=command,
            transcript_languages=transcript_languages,
            poll_interval_minutes=poll_interval,
            metadata=metadata,
        )
        _validate_source(source)
        sources.append(source)
    return sources


def _validate_source(source: SourceConfig) -> None:
    if source.kind not in {"blog", "youtube", "x"}:
        raise ConfigError(f"Source {source.id}: unsupported kind {source.kind!r}")

    if source.kind in {"blog", "youtube"} and not source.feed_url:
        raise ConfigError(f"Source {source.id}: {source.kind} sources require feed_url")

    if source.kind == "x" and not source.command and not source.metadata.get("api_url"):
        raise ConfigError(f"Source {source.id}: x sources require command or api_url")


def _require_str(raw_source: dict[str, object], key: str) -> str:
    value = _optional_str(raw_source, key)
    if not value:
        raise ConfigError(f"Missing required string field: {key}")
    return value


def _optional_str(raw_source: dict[str, object], key: str) -> str | None:
    value = raw_source.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"Field {key} must be a non-empty string")
    return value.strip()


def _optional_str_list(raw_source: dict[str, object], key: str) -> list[str]:
    value = raw_source.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ConfigError(f"Field {key} must be a list of strings")
    return [item.strip() for item in value]
