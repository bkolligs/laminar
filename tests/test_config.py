from pathlib import Path

import pytest

from laminar.config import (
    ConfigError,
    ensure_default_config,
    validate_source,
)
from laminar.models import SourceConfig


def test_loads_database_path_from_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("database_path: custom.db\n")

    runtime = ensure_default_config(config_path)

    assert runtime.database_path == tmp_path / "custom.db"


def test_creates_default_config_file(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"

    runtime = ensure_default_config(config_path)

    assert config_path.exists()
    assert runtime.database_path == tmp_path / "laminar.db"


def test_x_sources_can_validate_with_feed_url() -> None:
    source = SourceConfig(
        id="x-list",
        kind="x",
        name="Example List",
        feed_url="https://x.com/i/lists/1234567890",
    )

    validate_source(source)


def test_x_sources_still_require_a_url_or_api_url() -> None:
    source = SourceConfig(id="x-empty", kind="x", name="Broken X")

    with pytest.raises(
        ConfigError,
        match="x sources require feed_url or api_url",
    ):
        validate_source(source)
