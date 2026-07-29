"""Tests for mcp_common.paths."""

from pathlib import Path

from mcp_common.paths import config_dir, config_file, data_dir


class TestConfigDir:
    def test_returns_path_under_home_config(self):
        result = config_dir("test-mcp-common-paths-tool")
        assert result == Path.home() / ".config" / "test-mcp-common-paths-tool"

    def test_creates_directory(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        result = config_dir("my-tool")
        assert result.exists()
        assert result.is_dir()

    def test_permissions_are_user_only(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        result = config_dir("my-tool")
        mode = result.stat().st_mode & 0o777
        assert mode == 0o700

    def test_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        r1 = config_dir("my-tool")
        r2 = config_dir("my-tool")
        assert r1 == r2


class TestConfigFile:
    def test_returns_config_json_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        result = config_file("my-tool")
        assert result.name == "config.json"
        assert result.parent == config_dir("my-tool")

    def test_does_not_create_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        result = config_file("new-tool")
        assert not result.exists()


class TestDataDir:
    def test_returns_path_under_local_share(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        result = data_dir("my-tool")
        assert result == tmp_path / ".local" / "share" / "my-tool"

    def test_creates_directory(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        result = data_dir("my-tool")
        assert result.exists()
        assert result.is_dir()

    def test_permissions_are_user_only(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        result = data_dir("my-tool")
        mode = result.stat().st_mode & 0o777
        assert mode == 0o700
