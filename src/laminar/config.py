from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from laminar.models import SourceConfig


class ConfigError(ValueError):
    pass


DEFAULT_CONFIG_YAML = """database_path: laminar.db
"""


@dataclass(slots=True)
class AppConfig:
    config_path: Path
    database_path: Path

def ensure_default_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if not config_path.exists():
        config_path.write_text(DEFAULT_CONFIG_YAML)

    raw_config = _load_yaml_mapping(config_path, label="Config")
    database_path = _resolve_optional_path(
        raw_config, key="database_path", base_dir=config_path.parent
    ) or (config_path.parent / "laminar.db")
    return AppConfig(config_path=config_path, database_path=database_path)


def validate_source(source: SourceConfig) -> None:
    kind = source.kind
    if kind not in {"feed", "youtube", "x"}:
        raise ConfigError(f"Source {source.id}: unsupported kind {source.kind!r}")

    if kind == "feed" and not source.feed_url:
        raise ConfigError(f"Source {source.id}: feed sources require feed_url")

    if kind == "youtube" and not source.feed_url and not source.metadata.get("uploads_playlist_id"):
        raise ConfigError(
            f"Source {source.id}: youtube sources require feed_url or metadata.uploads_playlist_id"
        )

    if kind == "x" and not source.feed_url and not source.metadata.get("api_url"):
        raise ConfigError(f"Source {source.id}: x sources require feed_url or api_url")


def _load_yaml_mapping(path: Path, *, label: str) -> dict[str, object]:
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{label} file must be a YAML mapping")
    return data


def _resolve_optional_path(
    raw_config: dict[str, object], *, key: str, base_dir: Path
) -> Path | None:
    value = raw_config.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"Field {key} must be a non-empty string")
    candidate = Path(value.strip()).expanduser()
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    return candidate
