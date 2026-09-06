"""설정 로딩.

자격 증명은 선택이다 — 없으면 설정 화면에서 입력할 수 있어야 하므로 부팅을 막지 않는다.
"""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(Exception):
    """설정이 잘못되었거나 누락되었을 때."""


@dataclass(frozen=True)
class Config:
    client_id: str | None
    client_secret: str | None
    data_dir: Path

    @property
    def transcripts_dir(self) -> Path:
        return self.data_dir / "transcripts"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def inbox_dir(self) -> Path:
        return self.data_dir / "inbox"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jobs.db"


def load_config(env_path: Path | None = None) -> Config:
    load_dotenv(env_path, override=False)

    # 자격 증명이 없어도 앱은 떠야 한다 — 설정 화면에서 입력할 수 있기 때문이다.
    # 실제 사용 시점의 해석은 app/credentials.py가 담당한다(DB 설정이 우선).
    client_id = os.getenv("RTZR_CLIENT_ID", "").strip() or None
    client_secret = os.getenv("RTZR_CLIENT_SECRET", "").strip() or None

    data_dir_str = os.getenv("DATA_DIR", "data").strip() or "data"
    config = Config(
        client_id=client_id,
        client_secret=client_secret,
        data_dir=Path(data_dir_str),
    )
    for directory in (
        config.transcripts_dir,
        config.raw_dir,
        config.media_dir,
        config.inbox_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return config
