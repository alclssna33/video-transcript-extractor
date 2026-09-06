from pathlib import Path
from app.config import load_config


def test_missing_credentials_returns_none_instead_of_raising(tmp_path, monkeypatch):
    """자격 증명은 설정 화면에서 입력할 수 있으므로 없어도 부팅되어야 한다."""
    monkeypatch.delenv("RTZR_CLIENT_ID", raising=False)
    monkeypatch.delenv("RTZR_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))

    cfg = load_config(env_path=tmp_path / "nonexistent.env")

    assert cfg.client_id is None
    assert cfg.client_secret is None
    assert cfg.transcripts_dir.is_dir()


def test_blank_credentials_are_treated_as_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("RTZR_CLIENT_ID", "   ")
    monkeypatch.setenv("RTZR_CLIENT_SECRET", "")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))

    cfg = load_config(env_path=tmp_path / "nonexistent.env")

    assert cfg.client_id is None
    assert cfg.client_secret is None


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
