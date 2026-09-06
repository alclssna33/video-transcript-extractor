import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.db import connect, create_job, get_job, init_db, update_job
from app.worker import Worker

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "rtzr_response.json").read_text(encoding="utf-8")
)


class FakeAsr:
    """RTZR 대역. 제출 즉시 완료된 것으로 응답한다."""

    def __init__(self, payload=FIXTURE):
        self.payload = payload
        self.submitted = []

    def submit(self, audio_path, *, keywords=None, spk_count=None):
        self.submitted.append((Path(audio_path).name, keywords, spk_count))
        return "transcribe-1"

    def poll(self, transcribe_id):
        return self.payload


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    conn = connect(tmp_path / "jobs.db")
    init_db(conn)
    for name in ("transcripts", "raw", "media"):
        (tmp_path / name).mkdir()

    def fake_extract(source, dest):
        Path(dest).write_bytes(b"audio")
        return Path(dest)

    monkeypatch.setattr("app.worker.extract_audio", fake_extract)
    monkeypatch.setattr("app.worker.probe_duration", lambda path: 3792.0)
    return conn, tmp_path


def make_worker(conn, tmp_path, asr=None):
    client = asr or FakeAsr()
    return Worker(
        conn=conn,
        asr_factory=lambda: client,
        transcripts_dir=tmp_path / "transcripts",
        raw_dir=tmp_path / "raw",
        media_dir=tmp_path / "media",
        poll_interval=0,
    )


def test_process_job_writes_raw_json_and_markdown(workspace, tmp_path):
    conn, root = workspace
    source = root / "weekly.mp4"
    source.write_bytes(b"video")
    job_id = create_job(conn, title="주간회의", source=str(source), source_type="file")

    make_worker(conn, root).process(job_id)

    job = get_job(conn, job_id)
    assert job["stage"] == "done"
    assert Path(job["md_path"]).exists()
    assert (root / "raw" / f"{job_id}.json").exists()
    assert "주간회의" in Path(job["md_path"]).read_text(encoding="utf-8")


def test_process_job_saves_transcribe_id_immediately(workspace, tmp_path):
    """재시작 복구의 핵심. 제출 직후 id가 DB에 있어야 한다."""
    conn, root = workspace
    source = root / "weekly.mp4"
    source.write_bytes(b"video")
    job_id = create_job(conn, title="t", source=str(source), source_type="file")

    class RecordingAsr(FakeAsr):
        def __init__(self, conn, job_id):
            super().__init__()
            self.conn = conn
            self.job_id = job_id
            self.id_at_poll_time = None

        def poll(self, transcribe_id):
            self.id_at_poll_time = get_job(self.conn, self.job_id)["rtzr_transcribe_id"]
            return self.payload

    asr = RecordingAsr(conn, job_id)
    make_worker(conn, root, asr=asr).process(job_id)

    assert asr.id_at_poll_time == "transcribe-1"


def test_process_job_passes_keywords_and_spk_count(workspace):
    conn, root = workspace
    source = root / "weekly.mp4"
    source.write_bytes(b"video")
    job_id = create_job(
        conn, title="t", source=str(source), source_type="file",
        keywords=["개비공"], spk_count=2,
    )
    asr = FakeAsr()

    make_worker(conn, root, asr=asr).process(job_id)

    assert asr.submitted[0][1] == ["개비공"]
    assert asr.submitted[0][2] == 2


def test_failed_job_records_error_and_does_not_raise(workspace):
    conn, root = workspace
    job_id = create_job(
        conn, title="t", source=str(root / "없는파일.mp4"), source_type="file"
    )

    class ExplodingAsr(FakeAsr):
        def submit(self, *args, **kwargs):
            raise RuntimeError("boom")

    make_worker(conn, root, asr=ExplodingAsr()).process(job_id)

    job = get_job(conn, job_id)
    assert job["stage"] == "failed"
    assert "boom" in job["last_error"]


def test_recover_resets_incomplete_stages_to_pending(workspace):
    conn, root = workspace
    job_id = create_job(conn, title="t", source="s", source_type="file")
    update_job(conn, job_id, stage="extracted")

    make_worker(conn, root).recover()

    assert get_job(conn, job_id)["stage"] == "pending"


def test_recover_keeps_submitted_jobs_for_polling(workspace):
    conn, root = workspace
    job_id = create_job(conn, title="t", source="s", source_type="file")
    update_job(conn, job_id, stage="submitted", rtzr_transcribe_id="transcribe-1")

    make_worker(conn, root).recover()

    assert get_job(conn, job_id)["stage"] == "submitted"


def test_regenerate_markdown_applies_new_speaker_names(workspace):
    conn, root = workspace
    source = root / "weekly.mp4"
    source.write_bytes(b"video")
    job_id = create_job(conn, title="주간회의", source=str(source), source_type="file")
    worker = make_worker(conn, root)
    worker.process(job_id)

    worker.regenerate(job_id, speaker_map={"0": "김팀장", "1": "이대리"})

    markdown = Path(get_job(conn, job_id)["md_path"]).read_text(encoding="utf-8")
    assert "**[김팀장]" in markdown
    assert "화자 1" not in markdown


def test_resuming_after_mid_poll_crash_does_not_resubmit(workspace):
    conn, root = workspace
    source = root / "weekly.mp4"
    source.write_bytes(b"video")
    job_id = create_job(conn, title="주간회의", source=str(source), source_type="file")

    class CrashesOncePollingAsr(FakeAsr):
        def __init__(self):
            super().__init__()
            self.poll_calls = 0

        def poll(self, transcribe_id):
            self.poll_calls += 1
            if self.poll_calls == 1:
                raise RuntimeError("네트워크 끊김")
            return self.payload

    asr = CrashesOncePollingAsr()
    worker = make_worker(conn, root, asr=asr)

    worker.process(job_id)  # 첫 시도: poll()에서 예외 -> failed
    assert get_job(conn, job_id)["stage"] == "failed"

    worker.process(job_id)  # 재시도: 재제출 없이 이어서 진행되어야 함

    assert get_job(conn, job_id)["stage"] == "done"
    assert len(asr.submitted) == 1  # submit()은 딱 한 번만 호출됐어야 함


def test_resuming_from_fetched_stage_does_not_repoll(workspace):
    """raw JSON이 이미 있으면 poll()을 다시 부르지 않고 바로 markdown을 만들어야 한다."""
    conn, root = workspace
    source = root / "weekly.mp4"
    source.write_bytes(b"video")
    job_id = create_job(conn, title="주간회의", source=str(source), source_type="file")

    # 미리 fetched 단계까지 도달한 상태를 만든다: raw JSON을 직접 써두고 stage를 fetched로 설정
    raw_path = root / "raw" / f"{job_id}.json"
    raw_path.write_text(json.dumps(FIXTURE, ensure_ascii=False), encoding="utf-8")
    update_job(
        conn, job_id,
        stage="fetched",
        audio_path=str(root / "media" / f"{job_id}.m4a"),
        rtzr_transcribe_id="transcribe-1",
        duration_sec=3792.0,
    )

    class ForbiddenAsr(FakeAsr):
        def submit(self, *args, **kwargs):
            raise AssertionError("fetched 단계에서는 submit()이 호출되면 안 된다")

        def poll(self, transcribe_id):
            raise AssertionError("fetched 단계에서는 poll()이 호출되면 안 된다")

    worker = make_worker(conn, root, asr=ForbiddenAsr())
    worker.process(job_id)

    job = get_job(conn, job_id)
    assert job["stage"] == "done"
    assert Path(job["md_path"]).exists()


def test_write_markdown_adds_suffix_on_filename_collision(workspace):
    conn, root = workspace
    source_a = root / "a.mp4"
    source_a.write_bytes(b"video")
    source_b = root / "b.mp4"
    source_b.write_bytes(b"video")

    job_a = create_job(conn, title="주간회의", source=str(source_a), source_type="file")
    job_b = create_job(conn, title="주간회의", source=str(source_b), source_type="file")

    worker = make_worker(conn, root)
    worker.process(job_a)
    worker.process(job_b)

    path_a = Path(get_job(conn, job_a)["md_path"])
    path_b = Path(get_job(conn, job_b)["md_path"])

    assert path_a != path_b
    assert path_a.exists()
    assert path_b.exists()


def test_regenerate_does_not_invoke_collision_logic(workspace, monkeypatch):
    conn, root = workspace
    source = root / "weekly.mp4"
    source.write_bytes(b"video")
    job_id = create_job(conn, title="주간회의", source=str(source), source_type="file")
    worker = make_worker(conn, root)
    worker.process(job_id)
    original_path = get_job(conn, job_id)["md_path"]

    calls = []
    original_method = worker._unique_transcript_path

    def spy(*args, **kwargs):
        calls.append(1)
        return original_method(*args, **kwargs)

    monkeypatch.setattr(worker, "_unique_transcript_path", spy)

    worker.regenerate(job_id, speaker_map={"0": "김팀장"})

    assert calls == []  # 기존 경로를 재사용했으니 접미사 로직은 아예 호출되지 않아야 한다
    assert get_job(conn, job_id)["md_path"] == original_path


def test_timeout_marks_job_stalled_not_failed(workspace):
    """과금된 결과를 버리지 않기 위해 failed가 아니라 stalled여야 한다."""
    conn, root = workspace
    source = root / "weekly.mp4"
    source.write_bytes(b"video")
    job_id = create_job(conn, title="t", source=str(source), source_type="file")

    class NeverFinishesAsr(FakeAsr):
        def poll(self, transcribe_id):
            return {"id": transcribe_id, "status": "transcribing"}

    worker = make_worker(conn, root, asr=NeverFinishesAsr())
    monkeypatched_attempts = 2
    import app.worker as worker_module
    worker_module.MAX_POLL_ATTEMPTS = monkeypatched_attempts

    worker.process(job_id)

    job = get_job(conn, job_id)
    assert job["stage"] == "stalled"
    assert job["rtzr_transcribe_id"] == "transcribe-1"  # id는 보존되어야 한다

    worker_module.MAX_POLL_ATTEMPTS = 240  # 다른 테스트에 영향 없도록 원복


def test_expired_job_is_marked_failed_with_resubmit_notice(workspace):
    conn, root = workspace
    job_id = create_job(conn, title="t", source="s", source_type="file")
    four_days_ago = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
    update_job(
        conn, job_id, stage="stalled",
        rtzr_transcribe_id="transcribe-1", submitted_at=four_days_ago,
    )

    make_worker(conn, root).process(job_id)

    job = get_job(conn, job_id)
    assert job["stage"] == "failed"
    assert "재제출" in job["last_error"]


def test_media_files_are_cleaned_up_after_success(workspace):
    conn, root = workspace
    source = root / "weekly.mp4"
    source.write_bytes(b"video")
    job_id = create_job(conn, title="t", source=str(source), source_type="file")

    make_worker(conn, root).process(job_id)

    assert (root / "media" / f"{job_id}.m4a").exists(), "오디오는 사용자 산출물이므로 남긴다"
    assert source.exists(), "사용자의 원본 파일은 지우지 않는다"
    assert (root / "raw" / f"{job_id}.json").exists(), "raw JSON은 영구 보관한다"


def test_media_files_are_cleaned_up_after_success_for_url_source(workspace, monkeypatch):
    """URL로 받은 원본 영상도 job_id 기반 파일명으로 저장되어 정리 대상에 포함되어야 한다."""
    conn, root = workspace

    def fake_download_url(url, dest_dir, *, filename_stem=None):
        downloaded = dest_dir / f"{filename_stem}.mp4"
        downloaded.write_bytes(b"downloaded video")
        return downloaded

    monkeypatch.setattr("app.worker.download_url", fake_download_url)

    job_id = create_job(
        conn, title="t", source="https://example.com/video", source_type="url"
    )

    make_worker(conn, root).process(job_id)

    job = get_job(conn, job_id)
    assert job["stage"] == "done"
    leftover = list((root / "media").glob(f"{job_id}.*"))
    assert leftover == [root / "media" / f"{job_id}.m4a"], (
        f"오디오만 남고 나머지는 정리되어야 합니다: {leftover}"
    )


def test_stalled_job_completes_on_retry_without_resubmitting(workspace):
    conn, root = workspace
    source = root / "weekly.mp4"
    source.write_bytes(b"video")
    job_id = create_job(conn, title="t", source=str(source), source_type="file")

    class TimesOutOnceAsr(FakeAsr):
        def __init__(self):
            super().__init__()
            self.poll_calls = 0

        def poll(self, transcribe_id):
            self.poll_calls += 1
            if self.poll_calls == 1:
                return {"id": transcribe_id, "status": "transcribing"}
            return self.payload

    asr = TimesOutOnceAsr()
    worker = make_worker(conn, root, asr=asr)

    import app.worker as worker_module
    original_max_attempts = worker_module.MAX_POLL_ATTEMPTS
    worker_module.MAX_POLL_ATTEMPTS = 1  # 첫 폴링에서 바로 타임아웃되도록
    worker.process(job_id)
    worker_module.MAX_POLL_ATTEMPTS = original_max_attempts

    job = get_job(conn, job_id)
    assert job["stage"] == "stalled"
    submitted_count_before_retry = len(asr.submitted)

    worker.process(job_id)  # "다시 확인" 시뮬레이션

    job = get_job(conn, job_id)
    assert job["stage"] == "done"
    assert len(asr.submitted) == submitted_count_before_retry  # 재제출 없었음


def test_audio_only_mode_stops_at_audio_ready_without_calling_asr(workspace):
    """오디오만 뽑는 모드는 RTZR을 아예 호출하면 안 된다(크레딧을 쓰지 않는다)."""
    conn, root = workspace
    source = root / "weekly.mp4"
    source.write_bytes(b"video")
    job_id = create_job(
        conn, title="t", source=str(source), source_type="file", mode="audio_only"
    )

    class ForbiddenAsr(FakeAsr):
        def submit(self, *args, **kwargs):
            raise AssertionError("audio_only 모드에서는 submit()이 호출되면 안 된다")

        def poll(self, transcribe_id):
            raise AssertionError("audio_only 모드에서는 poll()이 호출되면 안 된다")

    make_worker(conn, root, asr=ForbiddenAsr()).process(job_id)

    job = get_job(conn, job_id)
    assert job["stage"] == "audio_ready"
    assert Path(job["audio_path"]).exists()
    assert job["md_path"] is None


def test_audio_only_job_completes_when_switched_to_full(workspace):
    """이어서 스크립트 추출: 오디오 재추출 없이 전사까지 끝나야 한다."""
    conn, root = workspace
    source = root / "weekly.mp4"
    source.write_bytes(b"video")
    job_id = create_job(
        conn, title="t", source=str(source), source_type="file", mode="audio_only"
    )
    worker = make_worker(conn, root)
    worker.process(job_id)
    assert get_job(conn, job_id)["stage"] == "audio_ready"

    original_audio_path = get_job(conn, job_id)["audio_path"]

    update_job(conn, job_id, mode="full")
    worker.process(job_id)

    job = get_job(conn, job_id)
    assert job["stage"] == "done"
    assert job["audio_path"] == original_audio_path  # 같은 오디오를 재사용
    assert Path(job["md_path"]).exists()


def test_cleanup_keeps_audio_but_removes_downloaded_video(workspace, monkeypatch):
    """오디오는 사용자가 가져갈 산출물이므로 남기고, 받은 원본 영상만 지운다."""
    conn, root = workspace

    def fake_download_url(url, dest_dir, *, filename_stem=None):
        downloaded = dest_dir / f"{filename_stem}.mp4"
        downloaded.write_bytes(b"downloaded video")
        return downloaded

    monkeypatch.setattr("app.worker.download_url", fake_download_url)

    job_id = create_job(
        conn, title="t", source="https://example.com/v", source_type="url"
    )

    make_worker(conn, root).process(job_id)

    assert get_job(conn, job_id)["stage"] == "done"
    assert (root / "media" / f"{job_id}.m4a").exists(), "오디오는 남아야 한다"
    assert not (root / "media" / f"{job_id}.mp4").exists(), "받은 원본 영상은 지워야 한다"


def test_delete_audio_removes_only_that_jobs_audio(workspace):
    conn, root = workspace
    source = root / "weekly.mp4"
    source.write_bytes(b"video")
    job_id = create_job(conn, title="t", source=str(source), source_type="file")
    worker = make_worker(conn, root)
    worker.process(job_id)
    assert (root / "media" / f"{job_id}.m4a").exists()

    worker.delete_audio(job_id)

    assert not (root / "media" / f"{job_id}.m4a").exists()


def test_missing_credentials_marks_job_failed_with_guidance(workspace):
    """자격 증명이 없으면 안내 문구와 함께 실패해야 한다."""
    conn, root = workspace
    source = root / "weekly.mp4"
    source.write_bytes(b"video")
    job_id = create_job(conn, title="t", source=str(source), source_type="file")

    def exploding_factory():
        from app.credentials import CredentialsMissingError
        raise CredentialsMissingError("RTZR 자격 증명이 없습니다. 설정 화면에서 입력하세요.")

    worker = Worker(
        conn=conn,
        asr_factory=exploding_factory,
        transcripts_dir=root / "transcripts",
        raw_dir=root / "raw",
        media_dir=root / "media",
        poll_interval=0,
    )
    worker.process(job_id)

    job = get_job(conn, job_id)
    assert job["stage"] == "failed"
    assert "설정" in job["last_error"]


def test_audio_ready_is_not_auto_resumed(workspace):
    """audio_ready는 '요청한 일을 끝낸' 상태다. 앱 재시작 때 멋대로 전사가 시작되면 안 된다."""
    conn, root = workspace
    job_id = create_job(
        conn, title="t", source="s", source_type="file", mode="audio_only"
    )
    update_job(conn, job_id, stage="audio_ready")
    worker = make_worker(conn, root)

    worker.recover()

    assert get_job(conn, job_id)["stage"] == "audio_ready"  # 되돌려지지 않음
    assert job_id not in worker.pending_job_ids()
    assert job_id not in worker.resumable_job_ids()
    assert job_id not in worker.fetched_job_ids()


def test_audio_only_job_does_not_need_credentials(workspace):
    """1단계는 RTZR 없이도 되어야 한다 — 외부 도구만 쓸 사람을 위해."""
    conn, root = workspace
    source = root / "weekly.mp4"
    source.write_bytes(b"video")
    job_id = create_job(
        conn, title="t", source=str(source), source_type="file", mode="audio_only"
    )

    def exploding_factory():
        raise AssertionError("audio_only 모드에서는 자격 증명을 요구하면 안 된다")

    worker = Worker(
        conn=conn,
        asr_factory=exploding_factory,
        transcripts_dir=root / "transcripts",
        raw_dir=root / "raw",
        media_dir=root / "media",
        poll_interval=0,
    )
    worker.process(job_id)

    assert get_job(conn, job_id)["stage"] == "audio_ready"
