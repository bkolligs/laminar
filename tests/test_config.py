from pathlib import Path

from laminar.config import ensure_default_config


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
