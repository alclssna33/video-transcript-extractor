"""SQLite 접근 계층. SQL은 이 모듈 밖으로 새어나가지 않는다."""
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

# stage 전이: pending → extracted → submitted → fetched → done
# 예외 상태: failed(복구 불가), stalled(결과를 아직 못 받음, 재확인 가능)
STAGES = ("pending", "extracted", "submitted", "fetched", "done", "failed", "stalled")

UPDATABLE_COLUMNS = frozenset({
    "title", "stage", "audio_path", "rtzr_transcribe_id", "submitted_at",
    "duration_sec", "keywords", "spk_count", "speaker_map", "md_path",
    "attempts", "last_error",
})

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id                  TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    source              TEXT NOT NULL,
    source_type         TEXT NOT NULL,
    stage               TEXT NOT NULL,
    audio_path          TEXT,
    rtzr_transcribe_id  TEXT,
    submitted_at        TEXT,
    duration_sec        REAL,
    keywords            TEXT,
    spk_count           INTEGER,
    speaker_map         TEXT,
    md_path             TEXT,
    attempts            INTEGER NOT NULL DEFAULT 0,
    last_error          TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def create_job(
    conn: sqlite3.Connection,
    *,
    title: str,
    source: str,
    source_type: str,
    keywords: list[str] | None = None,
    spk_count: int | None = None,
) -> str:
    job_id = uuid.uuid4().hex[:12]
    timestamp = now_iso()
    conn.execute(
        """
        INSERT INTO jobs (id, title, source, source_type, stage, keywords,
                          spk_count, attempts, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'pending', ?, ?, 0, ?, ?)
        """,
        (
            job_id, title, source, source_type,
            json.dumps(keywords or [], ensure_ascii=False),
            spk_count, timestamp, timestamp,
        ),
    )
    conn.commit()
    return job_id


def get_job(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row | None:
    cursor = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    return cursor.fetchone()


def list_jobs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    cursor = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC")
    return cursor.fetchall()


def list_jobs_by_stage(conn: sqlite3.Connection, stages: tuple[str, ...]) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in stages)
    cursor = conn.execute(
        f"SELECT * FROM jobs WHERE stage IN ({placeholders}) ORDER BY created_at",
        stages,
    )
    return cursor.fetchall()


def update_job(conn: sqlite3.Connection, job_id: str, **fields) -> None:
    unknown = set(fields) - UPDATABLE_COLUMNS
    if unknown:
        raise ValueError(f"수정할 수 없는 컬럼입니다: {sorted(unknown)}")
    if not fields:
        return

    assignments = ", ".join(f"{column} = ?" for column in fields)
    conn.execute(
        f"UPDATE jobs SET {assignments}, updated_at = ? WHERE id = ?",
        (*fields.values(), now_iso(), job_id),
    )
    conn.commit()
