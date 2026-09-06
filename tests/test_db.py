import json

import pytest

from app.db import (
    connect,
    create_job,
    get_job,
    get_setting,
    init_db,
    list_jobs,
    list_jobs_by_stage,
    set_setting,
    update_job,
)


def _make_legacy_db(tmp_path):
    """mode 컬럼이 없던 구버전 스키마 DB를 만든다."""
    conn = connect(tmp_path / "jobs.db")
    conn.execute("""
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, source TEXT NOT NULL,
            source_type TEXT NOT NULL, stage TEXT NOT NULL, audio_path TEXT,
            rtzr_transcribe_id TEXT, submitted_at TEXT, duration_sec REAL,
            keywords TEXT, spk_count INTEGER, speaker_map TEXT, md_path TEXT,
            attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )
    """)
    conn.execute(
        "INSERT INTO jobs (id, title, source, source_type, stage, attempts,"
        " created_at, updated_at) VALUES ('old1', '옛날작업', 's', 'file', 'done', 0, 'x', 'y')"
    )
    conn.commit()
    return conn


def test_create_and_get_job(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    init_db(conn)

    job_id = create_job(
        conn,
        title="주간회의",
        source="D:/videos/weekly.mp4",
        source_type="file",
        keywords=["개비공"],
        spk_count=2,
    )

    job = get_job(conn, job_id)
    assert job["title"] == "주간회의"
    assert job["stage"] == "pending"
    assert job["source_type"] == "file"
    assert json.loads(job["keywords"]) == ["개비공"]
    assert job["spk_count"] == 2
    assert job["attempts"] == 0


def test_update_job_changes_fields_and_touches_updated_at(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    init_db(conn)
    job_id = create_job(conn, title="t", source="s", source_type="url")
    before = get_job(conn, job_id)["updated_at"]

    update_job(conn, job_id, stage="submitted", rtzr_transcribe_id="abc123")

    job = get_job(conn, job_id)
    assert job["stage"] == "submitted"
    assert job["rtzr_transcribe_id"] == "abc123"
    assert job["updated_at"] >= before


def test_update_job_rejects_unknown_column(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    init_db(conn)
    job_id = create_job(conn, title="t", source="s", source_type="url")

    try:
        update_job(conn, job_id, bogus="x")
    except ValueError as exc:
        assert "bogus" in str(exc)
    else:
        raise AssertionError("알 수 없는 컬럼은 ValueError를 발생시켜야 한다")


def test_list_jobs_newest_first(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    init_db(conn)
    first = create_job(conn, title="첫번째", source="s", source_type="url")
    second = create_job(conn, title="두번째", source="s", source_type="url")

    jobs = list_jobs(conn)

    assert [job["id"] for job in jobs] == [second, first]


def test_init_db_is_idempotent(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    init_db(conn)
    init_db(conn)
    assert list_jobs(conn) == []


def test_list_jobs_by_stage_filters_and_orders(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    init_db(conn)
    j1 = create_job(conn, title="first", source="s", source_type="url")
    j2 = create_job(conn, title="second", source="s", source_type="url")

    update_job(conn, j1, stage="done")
    update_job(conn, j2, stage="submitted")

    result = list_jobs_by_stage(conn, ("submitted", "done"))
    assert len(result) == 2

    result = list_jobs_by_stage(conn, ("pending",))
    assert result == []


def test_create_job_with_none_keywords_defaults_to_empty_list(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    init_db(conn)
    job_id = create_job(conn, title="t", source="s", source_type="url", keywords=None)
    job = get_job(conn, job_id)
    assert json.loads(job["keywords"]) == []


def test_create_job_defaults_to_full_mode(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    init_db(conn)
    job_id = create_job(conn, title="t", source="s", source_type="file")

    assert get_job(conn, job_id)["mode"] == "full"


def test_create_job_accepts_audio_only_mode(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    init_db(conn)
    job_id = create_job(conn, title="t", source="s", source_type="file", mode="audio_only")

    assert get_job(conn, job_id)["mode"] == "audio_only"


def test_mode_is_updatable(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    init_db(conn)
    job_id = create_job(conn, title="t", source="s", source_type="file", mode="audio_only")

    update_job(conn, job_id, mode="full")

    assert get_job(conn, job_id)["mode"] == "full"


def test_settings_round_trip(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    init_db(conn)

    assert get_setting(conn, "rtzr_client_id") is None

    set_setting(conn, "rtzr_client_id", "cid-1")
    assert get_setting(conn, "rtzr_client_id") == "cid-1"

    set_setting(conn, "rtzr_client_id", "cid-2")
    assert get_setting(conn, "rtzr_client_id") == "cid-2"


def test_migration_adds_mode_column_to_existing_db(tmp_path):
    """mode 컬럼이 없던 기존 DB도 데이터를 잃지 않고 마이그레이션되어야 한다."""
    conn = _make_legacy_db(tmp_path)

    init_db(conn)  # 마이그레이션 실행

    job = get_job(conn, "old1")
    assert job["title"] == "옛날작업"   # 기존 데이터 보존
    assert job["mode"] == "full"        # 기본값으로 해석


def test_init_db_is_idempotent_on_already_migrated_db(tmp_path):
    """이미 마이그레이션된 DB에서 다시 실행하면 ALTER를 건너뛰어야 한다."""
    conn = _make_legacy_db(tmp_path)

    init_db(conn)  # 마이그레이션
    init_db(conn)  # 재실행 — 여기서 duplicate column 에러가 나면 안 된다
    init_db(conn)

    job = get_job(conn, "old1")
    assert job["title"] == "옛날작업"
    assert job["mode"] == "full"


def test_set_setting_strips_whitespace(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    init_db(conn)

    set_setting(conn, "rtzr_client_id", "  cid-1  ")

    assert get_setting(conn, "rtzr_client_id") == "cid-1"


def test_blank_setting_is_removed_so_env_fallback_works(tmp_path):
    """설정을 비우면 .env 폴백으로 되돌아갈 수 있어야 한다."""
    conn = connect(tmp_path / "jobs.db")
    init_db(conn)
    set_setting(conn, "rtzr_client_id", "cid-1")

    set_setting(conn, "rtzr_client_id", "   ")

    assert get_setting(conn, "rtzr_client_id") is None


def test_create_job_rejects_unknown_mode(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    init_db(conn)

    with pytest.raises(ValueError) as exc:
        create_job(conn, title="t", source="s", source_type="file", mode="banana")

    assert "mode" in str(exc.value)


def test_update_job_rejects_unknown_mode_and_stage(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    init_db(conn)
    job_id = create_job(conn, title="t", source="s", source_type="file")

    with pytest.raises(ValueError):
        update_job(conn, job_id, mode="banana")

    with pytest.raises(ValueError):
        update_job(conn, job_id, stage="nowhere")
