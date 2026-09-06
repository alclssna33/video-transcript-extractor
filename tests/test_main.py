import json
import time
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app.credentials import CLIENT_ID_KEY, CLIENT_SECRET_KEY
from app.db import create_job, get_job, get_setting, list_jobs
from app.main import create_app
from app.renderer import audio_filename

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "rtzr_response.json").read_text(encoding="utf-8")
)


def wait_for_done(client, job_id, attempts=100):
    """백그라운드 처리가 끝날 때까지 기다린다.

    상태 조회가 일시적으로 200이 아니거나 예상한 필드가 없어도(시스템 부하로 인한
    타이밍 문제 등) 폴링을 포기하지 않고 계속 재시도한다 — 실패로 볼 근거는
    attempts를 다 써도 완료 상태에 도달하지 못하는 것뿐이다.
    """
    for _ in range(attempts):
        response = client.get(f"/jobs/{job_id}/status")
        stage = response.json().get("stage") if response.status_code == 200 else None
        if stage in ("done", "failed", "stalled"):
            return stage
        time.sleep(0.05)
    raise AssertionError(f"작업이 끝나지 않았습니다: {job_id}")


def wait_for_stage(client, job_id, wanted, attempts=100):
    """지정한 stage 집합 중 하나에 도달할 때까지 기다린다."""
    for _ in range(attempts):
        response = client.get(f"/jobs/{job_id}/status")
        stage = response.json().get("stage") if response.status_code == 200 else None
        if stage in wanted:
            return stage
        time.sleep(0.05)
    raise AssertionError(f"작업이 {wanted}에 도달하지 않았습니다: {job_id}")


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
        # 원본이 없으면 실패한다 — 실제 ffmpeg처럼 동작해야 "재추출이 일어났는지"를
        # 원본을 지워두는 것만으로 테스트할 수 있다.
        if not Path(source).exists():
            raise FileNotFoundError(f"원본이 없습니다: {source}")
        Path(dest).write_bytes(b"audio")
        return Path(dest)

    def fake_probe_duration(path):
        if not Path(path).exists():
            raise FileNotFoundError(f"원본이 없습니다: {path}")
        return 3792.0

    monkeypatch.setattr("app.worker.extract_audio", fake_extract)
    monkeypatch.setattr("app.worker.probe_duration", fake_probe_duration)

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


def test_retry_reschedules_stalled_job(client, tmp_path):
    video = tmp_path / "weekly.mp4"
    video.write_bytes(b"video")
    created = client.post(
        "/jobs", data={"source": str(video), "title": "t"}, follow_redirects=False
    )
    job_id = created.headers["location"].rsplit("/", 1)[-1]
    wait_for_done(client, job_id)

    # 강제로 stalled 상태로 되돌려서 재확인 버튼을 검증한다
    conn = client.app.state.conn
    from app.db import update_job
    update_job(conn, job_id, stage="stalled", last_error="시간 초과")

    response = client.post(f"/jobs/{job_id}/retry", follow_redirects=False)

    assert response.status_code == 303
    # job이 이미 stalled 상태에서 시작하므로 wait_for_done을 그대로 쓰면
    # 백그라운드 작업이 실제로 시작되기 전에 (여전히 stalled인) 첫 폴링에서
    # "끝났다"고 오판할 수 있다. 여기서는 done 도달만 명시적으로 기다린다.
    for _ in range(100):
        response = client.get(f"/jobs/{job_id}/status")
        stage = response.json().get("stage") if response.status_code == 200 else None
        if stage == "done":
            break
        time.sleep(0.05)
    else:
        raise AssertionError(f"재시도 후 작업이 완료되지 않았습니다: {job_id}")

    assert stage == "done"


def test_retry_rejects_unknown_job(client):
    response = client.post("/jobs/nonexistent-id/retry")
    assert response.status_code == 404


def test_submit_with_audio_only_mode_stops_at_audio_ready(client, tmp_path):
    video = tmp_path / "weekly.mp4"
    video.write_bytes(b"video")

    created = client.post(
        "/jobs",
        data={"source": str(video), "title": "오디오만", "mode": "audio_only"},
        follow_redirects=False,
    )
    job_id = created.headers["location"].rsplit("/", 1)[-1]
    stage = wait_for_stage(client, job_id, {"audio_ready", "failed"})

    assert stage == "audio_ready"
    job = get_job(client.app.state.conn, job_id)
    assert job["mode"] == "audio_only"
    assert job["md_path"] is None


def test_submit_defaults_to_full_mode(client, tmp_path):
    video = tmp_path / "weekly.mp4"
    video.write_bytes(b"video")

    created = client.post(
        "/jobs", data={"source": str(video), "title": "기본값"}, follow_redirects=False
    )
    job_id = created.headers["location"].rsplit("/", 1)[-1]
    wait_for_stage(client, job_id, {"done", "failed"})

    assert get_job(client.app.state.conn, job_id)["mode"] == "full"


def test_audio_download_returns_file(client, tmp_path):
    video = tmp_path / "weekly.mp4"
    video.write_bytes(b"video")
    created = client.post(
        "/jobs",
        data={"source": str(video), "title": "주간회의", "mode": "audio_only"},
        follow_redirects=False,
    )
    job_id = created.headers["location"].rsplit("/", 1)[-1]
    wait_for_stage(client, job_id, {"audio_ready", "failed"})

    response = client.get(f"/jobs/{job_id}/audio")

    assert response.status_code == 200
    # Starlette는 Content-Disposition의 파일명을 RFC 5987 percent-encoding으로 내보낸다.
    job = get_job(client.app.state.conn, job_id)
    expected = quote(audio_filename(job["created_at"], job["title"]))
    assert expected in response.headers["content-disposition"]
    assert response.content == b"audio"


def test_audio_download_404_when_no_audio(client):
    conn = client.app.state.conn
    job_id = create_job(conn, title="t", source="s", source_type="file")

    response = client.get(f"/jobs/{job_id}/audio")

    assert response.status_code == 404


def test_audio_download_404_for_unknown_job(client):
    assert client.get("/jobs/nonexistent/audio").status_code == 404


def test_audio_delete_404_for_unknown_job(client):
    assert client.post("/jobs/nonexistent/audio/delete").status_code == 404


def test_submit_rejects_unknown_mode(client, tmp_path):
    """알 수 없는 값을 조용히 full로 바꾸면 의도치 않게 RTZR 크레딧을 쓴다."""
    video = tmp_path / "weekly.mp4"
    video.write_bytes(b"video")

    response = client.post(
        "/jobs", data={"source": str(video), "title": "t", "mode": "AUDIO_ONLY"}
    )

    assert response.status_code == 400


def test_transcribe_continues_audio_ready_job(client, tmp_path):
    video = tmp_path / "weekly.mp4"
    video.write_bytes(b"video")
    created = client.post(
        "/jobs",
        data={"source": str(video), "title": "이어서", "mode": "audio_only"},
        follow_redirects=False,
    )
    job_id = created.headers["location"].rsplit("/", 1)[-1]
    wait_for_stage(client, job_id, {"audio_ready", "failed"})

    video.unlink()  # 재추출이 일어나면 원본이 없어 실패한다

    response = client.post(f"/jobs/{job_id}/transcribe", follow_redirects=False)

    assert response.status_code == 303
    assert wait_for_stage(client, job_id, {"done", "failed"}) == "done"
    assert get_job(client.app.state.conn, job_id)["mode"] == "full"


def test_transcribe_rejects_job_not_in_audio_ready(client):
    conn = client.app.state.conn
    job_id = create_job(conn, title="t", source="s", source_type="file")

    response = client.post(f"/jobs/{job_id}/transcribe")

    assert response.status_code == 400


def test_transcribe_404_for_unknown_job(client):
    response = client.post("/jobs/nonexistent/transcribe")

    assert response.status_code == 404


def test_delete_audio_removes_file(client, tmp_path):
    video = tmp_path / "weekly.mp4"
    video.write_bytes(b"video")
    created = client.post(
        "/jobs",
        data={"source": str(video), "title": "삭제", "mode": "audio_only"},
        follow_redirects=False,
    )
    job_id = created.headers["location"].rsplit("/", 1)[-1]
    wait_for_stage(client, job_id, {"audio_ready", "failed"})

    response = client.post(f"/jobs/{job_id}/audio/delete", follow_redirects=False)

    assert response.status_code == 303
    assert client.get(f"/jobs/{job_id}/audio").status_code == 404


def test_delete_audio_clears_audio_path(client, tmp_path):
    """경로가 남아 있으면 UI가 죽은 다운로드 버튼을 계속 보여준다."""
    video = tmp_path / "weekly.mp4"
    video.write_bytes(b"video")
    created = client.post(
        "/jobs",
        data={"source": str(video), "title": "삭제", "mode": "audio_only"},
        follow_redirects=False,
    )
    job_id = created.headers["location"].rsplit("/", 1)[-1]
    wait_for_stage(client, job_id, {"audio_ready", "failed"})
    assert get_job(client.app.state.conn, job_id)["audio_path"] is not None

    client.post(f"/jobs/{job_id}/audio/delete", follow_redirects=False)

    assert get_job(client.app.state.conn, job_id)["audio_path"] is None


def test_settings_page_shows_unset_state(client):
    response = client.get("/settings")

    assert response.status_code == 200
    assert "설정" in response.text


def test_saving_settings_persists_credentials(client):
    response = client.post(
        "/settings",
        data={"client_id": "saved-id", "client_secret": "saved-secret-1234"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    conn = client.app.state.conn
    assert get_setting(conn, CLIENT_ID_KEY) == "saved-id"
    assert get_setting(conn, CLIENT_SECRET_KEY) == "saved-secret-1234"


def test_settings_page_masks_saved_secret(client):
    client.post(
        "/settings",
        data={"client_id": "saved-id", "client_secret": "saved-secret-1234"},
        follow_redirects=False,
    )

    response = client.get("/settings")

    assert "1234" in response.text                     # 끝 4자리는 확인용으로 노출
    assert "saved-secret-1234" not in response.text    # 원문은 절대 노출 금지


def test_settings_rejects_partial_input(client):
    response = client.post(
        "/settings", data={"client_id": "only-id", "client_secret": ""}
    )

    assert response.status_code == 400
    assert "돌아가기" in response.text


def test_saved_settings_win_over_env(client):
    """설정 화면 값이 .env보다 우선해야 한다."""
    assert ".env 파일" in client.get("/settings").text  # 저장 전에는 .env 출처

    client.post(
        "/settings",
        data={"client_id": "db-id", "client_secret": "db-secret-9999"},
        follow_redirects=False,
    )

    response = client.get("/settings")

    assert "9999" in response.text
    assert "설정 화면" in response.text


def test_clearing_settings_falls_back_to_env(client):
    """오타를 저장해도 되돌릴 수 있어야 한다 — 지우면 .env로 복귀."""
    client.post(
        "/settings",
        data={"client_id": "db-id", "client_secret": "db-secret-9999"},
        follow_redirects=False,
    )
    assert "설정 화면" in client.get("/settings").text

    response = client.post("/settings/clear", follow_redirects=False)

    assert response.status_code == 303
    assert get_setting(client.app.state.conn, CLIENT_ID_KEY) is None
    assert get_setting(client.app.state.conn, CLIENT_SECRET_KEY) is None
    assert ".env 파일" in client.get("/settings").text  # 픽스처가 env를 설정해 둔다


def test_clear_button_hidden_when_credentials_come_from_env(client):
    """지울 DB 값이 없으면 버튼을 보여줄 이유가 없다."""
    response = client.get("/settings")

    assert ".env 파일" in response.text
    assert "저장된 값 지우기" not in response.text


def test_app_without_credentials_warns_but_still_extracts_audio(tmp_path, monkeypatch):
    """자격 증명이 없어도 앱은 뜨고, 오디오 추출(1단계)은 동작해야 한다."""
    monkeypatch.delenv("RTZR_CLIENT_ID", raising=False)
    monkeypatch.delenv("RTZR_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("app.config.load_dotenv", lambda *a, **k: None)

    def fake_extract(source, dest):
        Path(dest).write_bytes(b"audio")
        return Path(dest)

    monkeypatch.setattr("app.worker.extract_audio", fake_extract)
    monkeypatch.setattr("app.worker.probe_duration", lambda path: 60.0)

    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"video")

    app = create_app(poll_interval=0)  # asr 주입 없음 — 진짜 팩토리를 쓴다
    with TestClient(app) as unconfigured:
        index = unconfigured.get("/")
        assert index.status_code == 200
        assert "자격 증명이 설정되지 않았습니다" in index.text

        created = unconfigured.post(
            "/jobs",
            data={"source": str(video), "title": "강의", "mode": "audio_only"},
            follow_redirects=False,
        )
        job_id = created.headers["location"].rsplit("/", 1)[-1]
        stage = wait_for_stage(unconfigured, job_id, {"audio_ready", "failed"})

    assert stage == "audio_ready", "자격 증명 없이도 오디오 추출은 성공해야 한다"


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


def test_detail_hides_audio_buttons_when_file_is_gone(client, tmp_path):
    """예전 규칙으로 오디오가 지워진 job은 죽은 다운로드 버튼을 보여주면 안 된다."""
    video = tmp_path / "weekly.mp4"
    video.write_bytes(b"video")
    created = client.post(
        "/jobs",
        data={"source": str(video), "title": "옛날작업", "mode": "audio_only"},
        follow_redirects=False,
    )
    job_id = created.headers["location"].rsplit("/", 1)[-1]
    wait_for_stage(client, job_id, {"audio_ready", "failed"})
    assert "오디오 다운로드" in client.get(f"/jobs/{job_id}").text

    # DB에는 경로가 남은 채 파일만 사라진 상태를 만든다(예전 정리 규칙의 결과)
    Path(get_job(client.app.state.conn, job_id)["audio_path"]).unlink()

    response = client.get(f"/jobs/{job_id}")

    assert "오디오 다운로드" not in response.text
    assert "오디오 삭제" not in response.text
