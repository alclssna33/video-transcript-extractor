"""job 파이프라인. 단계별로 진행 상황을 DB에 남겨 재시작해도 이어갈 수 있게 한다."""
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from app.asr_client import AsrTemporaryError
from app.audio_extract import download_url, extract_audio, probe_duration
from app.db import get_job, list_jobs_by_stage, update_job
from app.renderer import render_markdown, transcript_filename

RESULT_RETENTION_DAYS = 3  # RTZR은 결과를 3일만 보관한다
MAX_POLL_ATTEMPTS = 240    # 5초 간격 기준 약 20분


class Worker:
    def __init__(
        self,
        *,
        conn: sqlite3.Connection,
        asr,
        transcripts_dir: Path,
        raw_dir: Path,
        media_dir: Path,
        poll_interval: float = 5.0,
    ):
        self._conn = conn
        self._asr = asr
        self._transcripts_dir = transcripts_dir
        self._raw_dir = raw_dir
        self._media_dir = media_dir
        self._poll_interval = poll_interval

    def process(self, job_id: str) -> None:
        """job 하나를 끝까지 처리한다. 실패해도 예외를 밖으로 던지지 않는다."""
        try:
            job = get_job(self._conn, job_id)
            if job is None:
                return

            raw_path = self._raw_dir / f"{job_id}.json"
            if job["stage"] == "fetched" and raw_path.exists():
                # raw JSON은 이미 저장되어 있다 — RTZR을 다시 폴링하지 않는다.
                # (3일 지나 결과가 만료된 뒤에도 로컬 결과로 markdown을 만들 수 있어야 한다)
                payload = json.loads(raw_path.read_text(encoding="utf-8"))
            else:
                audio_path = self._ensure_audio(job)
                transcribe_id = self._ensure_submitted(job_id, job, audio_path)
                payload = self._await_result(transcribe_id)

                raw_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                update_job(self._conn, job_id, stage="fetched")

            self._write_markdown(job_id, payload, speaker_map={})
            update_job(self._conn, job_id, stage="done", last_error=None)
        except Exception as exc:  # 개별 실패가 워커를 멈추지 않게 격리
            update_job(self._conn, job_id, stage="failed", last_error=str(exc))

    def regenerate(self, job_id: str, *, speaker_map: dict[str, str]) -> Path:
        """화자 이름을 바꿔 raw JSON에서 md를 다시 만든다."""
        payload = json.loads(
            (self._raw_dir / f"{job_id}.json").read_text(encoding="utf-8")
        )
        update_job(
            self._conn, job_id,
            speaker_map=json.dumps(speaker_map, ensure_ascii=False),
        )
        return self._write_markdown(job_id, payload, speaker_map=speaker_map)

    def recover(self) -> None:
        """재시작 복구: 오디오 추출만 된 job은 되돌리고, 제출/완료된 job은 그대로 재개 가능하다."""
        for job in list_jobs_by_stage(self._conn, ("extracted",)):
            update_job(self._conn, job["id"], stage="pending")

    def pending_job_ids(self) -> list[str]:
        return [job["id"] for job in list_jobs_by_stage(self._conn, ("pending",))]

    def resumable_job_ids(self) -> list[str]:
        return [job["id"] for job in list_jobs_by_stage(self._conn, ("submitted",))]

    def fetched_job_ids(self) -> list[str]:
        return [job["id"] for job in list_jobs_by_stage(self._conn, ("fetched",))]

    def _ensure_audio(self, job: sqlite3.Row) -> Path:
        if job["audio_path"] and Path(job["audio_path"]).exists():
            return Path(job["audio_path"])

        source = Path(job["source"])
        if job["source_type"] == "url":
            source = download_url(job["source"], self._media_dir)

        duration = probe_duration(source)
        audio_path = self._media_dir / f"{job['id']}.m4a"
        extract_audio(source, audio_path)

        update_job(
            self._conn, job["id"],
            stage="extracted", audio_path=str(audio_path), duration_sec=duration,
        )
        return audio_path

    def _ensure_submitted(self, job_id: str, job: sqlite3.Row, audio_path: Path) -> str:
        if job["rtzr_transcribe_id"]:
            return job["rtzr_transcribe_id"]

        keywords = json.loads(job["keywords"] or "[]")
        transcribe_id = self._asr.submit(
            audio_path, keywords=keywords or None, spk_count=job["spk_count"]
        )
        # 제출 즉시 저장한다 — 여기가 재시작 복구의 핵심이다.
        update_job(
            self._conn, job_id,
            stage="submitted",
            rtzr_transcribe_id=transcribe_id,
            submitted_at=datetime.now(timezone.utc).isoformat(),
        )
        return transcribe_id

    def _await_result(self, transcribe_id: str) -> dict:
        interval = self._poll_interval
        for _ in range(MAX_POLL_ATTEMPTS):
            try:
                payload = self._asr.poll(transcribe_id)
            except AsrTemporaryError:
                time.sleep(interval)
                interval = min(interval * 2, 30.0)
                continue

            if payload.get("status") == "completed":
                return payload
            time.sleep(interval)

        raise TimeoutError(
            "전사 결과를 시간 내에 받지 못했습니다. 목록에서 '다시 확인'을 눌러주세요. "
            f"(결과는 제출 후 {RESULT_RETENTION_DAYS}일간만 보관됩니다)"
        )

    def _write_markdown(self, job_id: str, payload: dict, *, speaker_map: dict) -> Path:
        job = get_job(self._conn, job_id)
        meta = {
            "id": job_id,
            "title": job["title"],
            "date": job["created_at"],
            "source": job["source"],
            "duration_sec": job["duration_sec"] or 0,
            "keywords": json.loads(job["keywords"] or "[]"),
        }
        markdown = render_markdown(meta=meta, raw=payload, speaker_map=speaker_map)

        existing_md_path = job["md_path"]
        if existing_md_path:
            # 이미 이 job에 대해 파일이 만들어진 적 있으면(재생성) 같은 경로에 덮어쓴다.
            md_path = Path(existing_md_path)
        else:
            md_path = self._unique_transcript_path(job["created_at"], job["title"])
        md_path.write_text(markdown, encoding="utf-8")
        update_job(self._conn, job_id, md_path=str(md_path))
        return md_path

    def _unique_transcript_path(self, date_iso: str, title: str) -> Path:
        """같은 날짜+제목 파일이 이미 있으면 -2, -3 접미사를 붙인다."""
        base_name = transcript_filename(date_iso, title)
        candidate = self._transcripts_dir / base_name
        if not candidate.exists():
            return candidate

        stem = candidate.stem
        suffix = 2
        while True:
            candidate = self._transcripts_dir / f"{stem}-{suffix}{candidate.suffix}"
            if not candidate.exists():
                return candidate
            suffix += 1
