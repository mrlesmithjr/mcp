"""Tests for mcp_common.config: layered precedence."""

import json
import os
from pathlib import Path

from mcp_common.config import load_layered_config


def _write_config(cfg_dir: Path, data: dict) -> None:
    """Write a config.json into cfg_dir."""
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(json.dumps(data))


class TestLoadLayeredConfig:
    """Validate the three-layer precedence: config.json < .env < env vars."""

    def test_returns_empty_dict_when_no_config(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        result = load_layered_config("nonexistent-tool", {})
        assert result == {}

    def test_reads_config_json(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        cfg_dir = tmp_path / ".config" / "my-tool"
        _write_config(cfg_dir, {"api_key": "from-file", "timeout": 30})
        result = load_layered_config("my-tool", {})
        assert result["api_key"] == "from-file"
        assert result["timeout"] == 30

    def test_env_var_overrides_config_json(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        cfg_dir = tmp_path / ".config" / "my-tool"
        _write_config(cfg_dir, {"api_key": "from-file"})
        monkeypatch.setenv("MY_TOOL_API_KEY", "from-env")
        result = load_layered_config("my-tool", {"api_key": "MY_TOOL_API_KEY"})
        assert result["api_key"] == "from-env"

    def test_config_json_key_preserved_when_no_env_var(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        cfg_dir = tmp_path / ".config" / "my-tool"
        _write_config(cfg_dir, {"api_key": "from-file", "base_url": "https://example.com"})
        # Map only api_key; base_url has no env var and must survive.
        monkeypatch.setenv("MY_TOOL_API_KEY", "from-env")
        result = load_layered_config("my-tool", {"api_key": "MY_TOOL_API_KEY"})
        assert result["api_key"] == "from-env"
        assert result["base_url"] == "https://example.com"

    def test_dot_env_sets_env_vars(self, tmp_path, monkeypatch):
        """A .env file in the config dir should populate env vars as fallback."""
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        cfg_dir = tmp_path / ".config" / "env-tool"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / ".env").write_text("ENV_TOOL_KEY=from-dotenv\n")
        # Ensure the env var is not already set.
        monkeypatch.delenv("ENV_TOOL_KEY", raising=False)
        load_layered_config("env-tool", {"api_key": "ENV_TOOL_KEY"})
        # After load, os.environ should have been populated from .env.
        assert os.environ.get("ENV_TOOL_KEY") == "from-dotenv"

    def test_existing_env_var_wins_over_dot_env(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        cfg_dir = tmp_path / ".config" / "env-tool"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / ".env").write_text("ENV_TOOL_KEY=from-dotenv\n")
        monkeypatch.setenv("ENV_TOOL_KEY", "from-real-env")
        result = load_layered_config("env-tool", {"api_key": "ENV_TOOL_KEY"})
        assert result["api_key"] == "from-real-env"

    def test_none_env_map_treated_as_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        cfg_dir = tmp_path / ".config" / "my-tool"
        _write_config(cfg_dir, {"key": "value"})
        result = load_layered_config("my-tool", None)
        assert result["key"] == "value"

    def test_blank_env_var_does_not_override(self, tmp_path, monkeypatch):
        """An env var that is set but blank should not overwrite config.json."""
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        cfg_dir = tmp_path / ".config" / "my-tool"
        _write_config(cfg_dir, {"api_key": "from-file"})
        monkeypatch.setenv("MY_TOOL_API_KEY", "   ")
        result = load_layered_config("my-tool", {"api_key": "MY_TOOL_API_KEY"})
        assert result["api_key"] == "from-file"

    def test_nested_dict_from_config_json_preserved(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        cfg_dir = tmp_path / ".config" / "my-tool"
        _write_config(cfg_dir, {"database": {"path": "/tmp/foo.db", "pool": 5}})
        result = load_layered_config("my-tool", {})
        assert result["database"]["path"] == "/tmp/foo.db"
        assert result["database"]["pool"] == 5
