"""SQLite 접근 계층. SQL은 이 모듈 밖으로 새어나가지 않는다."""
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

# stage 전이: pending → extracted → audio_ready                 (mode=audio_only)
#             pending → extracted → submitted → fetched → done  (mode=full)
# 예외 상태: failed(복구 불가), stalled(결과를 아직 못 받음, 재확인 가능)
STAGES = (
    "pending", "extracted", "audio_ready", "submitted", "fetched",
    "done", "failed", "stalled",
)

# mode: 처리 범위. audio_only는 오디오 추출까지만 하고 멈춘다.
MODES = ("audio_only", "full")

UPDATABLE_COLUMNS = frozenset({
    "title", "stage", "audio_path", "rtzr_transcribe_id", "submitted_at",
    "duration_sec", "keywords", "spk_count", "speaker_map", "md_path",
    "attempts", "last_error", "mode",
})

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id                  TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    source              TEXT NOT NULL,
    source_type         TEXT NOT NULL,
    stage               TEXT NOT NULL,
    mode                TEXT NOT NULL DEFAULT 'full',
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

CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: Path) -> sqlite3.Connection:
    """
    Establish a database connection.

    check_same_thread=False: Task 7의 워커가 asyncio.to_thread()로
    블로킹 DB 호출을 다른 스레드에서 실행하기 때문에 필요하다.
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """구버전 DB에 없는 컬럼을 채운다. 기존 데이터는 그대로 둔다."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
    if "mode" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN mode TEXT NOT NULL DEFAULT 'full'")


def create_job(
    conn: sqlite3.Connection,
    *,
    title: str,
    source: str,
    source_type: str,
    keywords: list[str] | None = None,
    spk_count: int | None = None,
    mode: str = "full",
) -> str:
    if mode not in MODES:
        raise ValueError(f"알 수 없는 mode입니다: {mode!r}")
    job_id = uuid.uuid4().hex[:12]
    timestamp = now_iso()
    conn.execute(
        """
        INSERT INTO jobs (id, title, source, source_type, stage, mode, keywords,
                          spk_count, attempts, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, 0, ?, ?)
        """,
        (
            job_id, title, source, source_type, mode,
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
    if "mode" in fields and fields["mode"] not in MODES:
        raise ValueError(f"알 수 없는 mode입니다: {fields['mode']!r}")
    if "stage" in fields and fields["stage"] not in STAGES:
        raise ValueError(f"알 수 없는 stage입니다: {fields['stage']!r}")
    if not fields:
        return

    assignments = ", ".join(f"{column} = ?" for column in fields)
    conn.execute(
        f"UPDATE jobs SET {assignments}, updated_at = ? WHERE id = ?",
        (*fields.values(), now_iso(), job_id),
    )
    conn.commit()


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    cursor = conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    return row["value"] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    """빈 값은 '설정 없음'과 같으므로 저장하지 않고 지운다.

    이렇게 해야 설정을 비웠을 때 .env 폴백으로 되돌아갈 수 있다.
    """
    cleaned = value.strip()
    if cleaned:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, cleaned),
        )
    else:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
    conn.commit()
