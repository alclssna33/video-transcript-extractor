"""설정 로딩. 자격 증명이 없으면 부팅 시점에 즉시 실패한다."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(Exception):
    """설정이 잘못되었거나 누락되었을 때."""


@dataclass(frozen=True)
class Config:
    client_id: str
    client_secret: str
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

    client_id = os.getenv("RTZR_CLIENT_ID", "").strip()
    client_secret = os.getenv("RTZR_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise ConfigError(
            "RTZR_CLIENT_ID / RTZR_CLIENT_SECRET가 필요합니다. "
            ".env.example을 .env로 복사한 뒤 https://developers.rtzr.ai/console/ 에서 "
            "발급받은 값을 채우세요."
        )

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
