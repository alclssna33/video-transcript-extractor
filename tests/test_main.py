import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import create_job, get_job, list_jobs
from app.main import create_app

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "rtzr_response.json").read_text(encoding="utf-8")
)


def wait_for_done(client, job_id, attempts=100):
    """백그라운드 처리가 끝날 때까지 기다린다."""
    for _ in range(attempts):
        stage = client.get(f"/jobs/{job_id}/status").json()["stage"]
        if stage in ("done", "failed", "stalled"):
            return stage
        time.sleep(0.05)
    raise AssertionError(f"작업이 끝나지 않았습니다: {job_id}")


class FakeAsr:
    def submit(self, audio_path, *, keywords=None, spk_count=None):
        return "transcribe-1"

    def poll(self, transcribe_id):
        return FIXTURE


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RTZR_CLIENT_ID", "cid")
    monkeypatch.setenv("RTZR_CLIENT_SECRET", "secret")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))

    def fake_extract(source, dest):
        Path(dest).write_bytes(b"audio")
        return Path(dest)

    monkeypatch.setattr("app.worker.extract_audio", fake_extract)
    monkeypatch.setattr("app.worker.probe_duration", lambda path: 3792.0)

    app = create_app(asr=FakeAsr(), poll_interval=0)
    with TestClient(app) as test_client:
        yield test_client


def test_index_renders_submit_form(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "영상 경로" in response.text or "파일 경로" in response.text


def test_submit_local_file_creates_job_and_produces_markdown(client, tmp_path):
    video = tmp_path / "weekly.mp4"
    video.write_bytes(b"video")

    response = client.post(
        "/jobs",
        data={"source": str(video), "title": "주간회의", "keywords": "개비공, 리턴제로"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    job_id = response.headers["location"].rsplit("/", 1)[-1]
    wait_for_done(client, job_id)

    conn = client.app.state.conn
    job = get_job(conn, job_id)
    assert job["title"] == "주간회의"
    assert json.loads(job["keywords"]) == ["개비공", "리턴제로"]
    assert job["stage"] == "done"
    assert Path(job["md_path"]).exists()


def test_submit_rejects_missing_source(client):
    response = client.post("/jobs", data={"source": "", "title": "t"})

    assert response.status_code == 400


def test_submit_rejects_nonexistent_local_path(client, tmp_path):
    response = client.post(
        "/jobs", data={"source": str(tmp_path / "없는파일.mp4"), "title": "t"}
    )

    assert response.status_code == 400
    assert "찾을 수 없" in response.text


def test_job_status_endpoint_returns_stage(client, tmp_path):
    video = tmp_path / "weekly.mp4"
    video.write_bytes(b"video")
    created = client.post(
        "/jobs", data={"source": str(video), "title": "t"}, follow_redirects=False
    )
    job_id = created.headers["location"].rsplit("/", 1)[-1]
    wait_for_done(client, job_id)

    response = client.get(f"/jobs/{job_id}/status")

    assert response.status_code == 200
    assert response.json()["stage"] == "done"


def test_detail_page_shows_transcript(client, tmp_path):
    video = tmp_path / "weekly.mp4"
    video.write_bytes(b"video")
    created = client.post(
        "/jobs", data={"source": str(video), "title": "주간회의"}, follow_redirects=False
    )
    job_id = created.headers["location"].rsplit("/", 1)[-1]
    wait_for_done(client, job_id)

    response = client.get(f"/jobs/{job_id}")

    assert response.status_code == 200
    assert "안녕하세요, 오늘 주간회의 시작하겠습니다." in response.text
    assert "화자 1" in response.text


def test_rename_speakers_regenerates_markdown(client, tmp_path):
    video = tmp_path / "weekly.mp4"
    video.write_bytes(b"video")
    created = client.post(
        "/jobs", data={"source": str(video), "title": "주간회의"}, follow_redirects=False
    )
    job_id = created.headers["location"].rsplit("/", 1)[-1]
    wait_for_done(client, job_id)

    response = client.post(
        f"/jobs/{job_id}/speakers",
        data={"speaker_0": "김팀장", "speaker_1": "이대리"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    markdown = Path(get_job(client.app.state.conn, job_id)["md_path"]).read_text(
        encoding="utf-8"
    )
    assert "**[김팀장]" in markdown
    assert "화자 1" not in markdown


def test_rename_speakers_rejects_job_without_transcript(client):
    conn = client.app.state.conn
    job_id = create_job(conn, title="t", source="s", source_type="file")

    response = client.post(f"/jobs/{job_id}/speakers", data={"speaker_0": "x"})

    assert response.status_code == 400


def test_inbox_files_are_picked_up(tmp_path, monkeypatch):
    monkeypatch.setenv("RTZR_CLIENT_ID", "cid")
    monkeypatch.setenv("RTZR_CLIENT_SECRET", "secret")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        "app.worker.extract_audio", lambda source, dest: (Path(dest).write_bytes(b"a"), Path(dest))[1]
    )
    monkeypatch.setattr("app.worker.probe_duration", lambda path: 60.0)

    inbox = tmp_path / "data" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "강의녹화.mp4").write_bytes(b"video")

    app = create_app(asr=FakeAsr(), poll_interval=0)
    with TestClient(app) as client:
        titles = [job["title"] for job in list_jobs(client.app.state.conn)]

    assert "강의녹화" in titles


def test_inbox_file_is_not_duplicated_on_restart(tmp_path, monkeypatch):
    """두 번째 앱 시작에서 같은 inbox 파일에 대해 job이 다시 만들어지면 안 된다."""
    monkeypatch.setenv("RTZR_CLIENT_ID", "cid")
    monkeypatch.setenv("RTZR_CLIENT_SECRET", "secret")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        "app.worker.extract_audio", lambda source, dest: (Path(dest).write_bytes(b"a"), Path(dest))[1]
    )
    monkeypatch.setattr("app.worker.probe_duration", lambda path: 60.0)

    inbox = tmp_path / "data" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "강의녹화.mp4").write_bytes(b"video")

    with TestClient(create_app(asr=FakeAsr(), poll_interval=0)) as client:
        pass  # 첫 시작에서 job 생성됨

    with TestClient(create_app(asr=FakeAsr(), poll_interval=0)) as client:
        titles = [job["title"] for job in list_jobs(client.app.state.conn)]

    assert titles.count("강의녹화") == 1
