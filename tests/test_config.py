import pytest
from pathlib import Path
from app.config import load_config, ConfigError


def test_missing_credentials_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("RTZR_CLIENT_ID", raising=False)
    monkeypatch.delenv("RTZR_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    with pytest.raises(ConfigError):
        load_config(env_path=tmp_path / "nonexistent.env")


def test_loads_credentials_and_creates_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("RTZR_CLIENT_ID", "cid")
    monkeypatch.setenv("RTZR_CLIENT_SECRET", "secret")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))

    cfg = load_config(env_path=tmp_path / "nonexistent.env")

    assert cfg.client_id == "cid"
    assert cfg.client_secret == "secret"
    assert cfg.transcripts_dir.is_dir()
    assert cfg.raw_dir.is_dir()
    assert cfg.media_dir.is_dir()
    assert cfg.inbox_dir.is_dir()
    assert cfg.db_path.parent.is_dir()


def test_whitespace_data_dir_falls_back_to_default(tmp_path, monkeypatch):
    monkeypatch.setenv("RTZR_CLIENT_ID", "cid")
    monkeypatch.setenv("RTZR_CLIENT_SECRET", "secret")
    monkeypatch.setenv("DATA_DIR", "   ")  # whitespace string

    cfg = load_config(env_path=tmp_path / "nonexistent.env")

    assert cfg.data_dir == Path("data")
    assert cfg.transcripts_dir.is_dir()
    assert cfg.raw_dir.is_dir()
    assert cfg.media_dir.is_dir()
    assert cfg.inbox_dir.is_dir()
