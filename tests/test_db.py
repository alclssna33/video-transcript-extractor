import json

from app.db import connect, create_job, get_job, init_db, list_jobs, update_job


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
