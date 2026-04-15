from pathlib import Path

import pytest

from laminar.config import ConfigError, load_config


def test_load_config(tmp_path: Path) -> None:
    path = tmp_path / "laminar.yaml"
    path.write_text(
        """
        sources:
          - id: blog-1
            kind: blog
            label: Example Blog
            enabled: true
            feed_url: file:///tmp/feed.xml
          - id: x-1
            kind: x
            label: Example X
            command:
              - xurl
              - https://api.x.test
        """
    )

    sources = load_config(path)

    assert [source.id for source in sources] == ["blog-1", "x-1"]
    assert sources[1].command[0] == "xurl"


def test_rejects_duplicate_ids(tmp_path: Path) -> None:
    path = tmp_path / "laminar.yaml"
    path.write_text(
        """
        sources:
          - id: dup
            kind: blog
            label: One
            feed_url: file:///tmp/feed.xml
          - id: dup
            kind: youtube
            label: Two
            feed_url: file:///tmp/feed.xml
        """
    )

    with pytest.raises(ConfigError):
        load_config(path)
