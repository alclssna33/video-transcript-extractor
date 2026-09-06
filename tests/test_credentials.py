import pytest

from app.config import Config
from app.credentials import (
    CLIENT_ID_KEY,
    CLIENT_SECRET_KEY,
    CredentialsMissingError,
    make_asr_client,
    mask_secret,
    resolve_credentials,
)
from app.db import connect, init_db, set_setting


def make_conn(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    init_db(conn)
    return conn


def env_config(tmp_path, client_id=None, client_secret=None):
    return Config(client_id=client_id, client_secret=client_secret, data_dir=tmp_path)


def test_returns_none_when_nothing_is_configured(tmp_path):
    conn = make_conn(tmp_path)

    assert resolve_credentials(conn, env_config(tmp_path)) is None


def test_falls_back_to_env(tmp_path):
    conn = make_conn(tmp_path)

    credentials = resolve_credentials(conn, env_config(tmp_path, "env-id", "env-secret"))

    assert credentials.client_id == "env-id"
    assert credentials.client_secret == "env-secret"
    assert credentials.source == "env"


def test_db_setting_wins_over_env(tmp_path):
    conn = make_conn(tmp_path)
    set_setting(conn, CLIENT_ID_KEY, "db-id")
    set_setting(conn, CLIENT_SECRET_KEY, "db-secret")

    credentials = resolve_credentials(conn, env_config(tmp_path, "env-id", "env-secret"))

    assert credentials.client_id == "db-id"
    assert credentials.client_secret == "db-secret"
    assert credentials.source == "db"


def test_partial_db_setting_falls_back_to_env(tmp_path):
    """id만 저장되고 secret이 없으면 DB 설정은 무효로 본다."""
    conn = make_conn(tmp_path)
    set_setting(conn, CLIENT_ID_KEY, "db-id")

    credentials = resolve_credentials(conn, env_config(tmp_path, "env-id", "env-secret"))

    assert credentials.source == "env"


def test_partial_env_config_is_treated_as_unconfigured(tmp_path):
    """반쪽짜리 .env(한쪽만 채워짐)도 무효다 — 자격 증명은 쌍으로만 유효하다."""
    conn = make_conn(tmp_path)

    assert resolve_credentials(conn, env_config(tmp_path, "env-id", None)) is None
    assert resolve_credentials(conn, env_config(tmp_path, None, "env-secret")) is None


def test_blanking_db_setting_restores_env_fallback(tmp_path):
    """설정 화면에서 값을 비우면 .env로 되돌아가야 한다."""
    conn = make_conn(tmp_path)
    set_setting(conn, CLIENT_ID_KEY, "db-id")
    set_setting(conn, CLIENT_SECRET_KEY, "db-secret")
    assert resolve_credentials(conn, env_config(tmp_path, "env-id", "env-secret")).source == "db"

    set_setting(conn, CLIENT_ID_KEY, "")
    set_setting(conn, CLIENT_SECRET_KEY, "")

    assert resolve_credentials(conn, env_config(tmp_path, "env-id", "env-secret")).source == "env"


def test_make_asr_client_raises_with_guidance_when_missing(tmp_path):
    conn = make_conn(tmp_path)

    with pytest.raises(CredentialsMissingError) as exc:
        make_asr_client(conn, env_config(tmp_path))

    assert "설정" in str(exc.value)


def test_make_asr_client_builds_client_from_credentials(tmp_path):
    conn = make_conn(tmp_path)
    set_setting(conn, CLIENT_ID_KEY, "db-id")
    set_setting(conn, CLIENT_SECRET_KEY, "db-secret")

    client = make_asr_client(conn, env_config(tmp_path))

    assert client is not None


def test_mask_secret_shows_only_last_four():
    assert mask_secret("abcdefghij") == "••••ghij"


def test_mask_secret_hides_short_values_entirely():
    assert mask_secret("abc") == "••••"
