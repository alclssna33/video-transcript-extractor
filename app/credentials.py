"""RTZR 자격 증명 해석.

설정 화면(DB)에 저장된 값이 .env보다 우선한다.
자격 증명이 없어도 앱은 떠야 하므로, 실패는 부팅이 아니라 '사용 시점'에 발생한다.
"""
import sqlite3
from dataclasses import dataclass

from app.asr_client import RtzrClient
from app.config import Config
from app.db import get_setting

CLIENT_ID_KEY = "rtzr_client_id"
CLIENT_SECRET_KEY = "rtzr_client_secret"


class CredentialsMissingError(Exception):
    """RTZR 자격 증명이 설정되지 않았다."""


@dataclass(frozen=True)
class Credentials:
    client_id: str
    client_secret: str
    source: str  # "db" | "env"


def resolve_credentials(conn: sqlite3.Connection, config: Config) -> Credentials | None:
    """DB 설정 → .env 순으로 자격 증명을 찾는다. 둘 다 없으면 None.

    id와 secret은 쌍으로만 유효하다 — 한쪽만 있으면 그 출처는 무시한다.
    """
    db_id = get_setting(conn, CLIENT_ID_KEY)
    db_secret = get_setting(conn, CLIENT_SECRET_KEY)
    if db_id and db_secret:
        return Credentials(db_id, db_secret, "db")

    if config.client_id and config.client_secret:
        return Credentials(config.client_id, config.client_secret, "env")

    return None


def make_asr_client(conn: sqlite3.Connection, config: Config) -> RtzrClient:
    """사용 시점에 클라이언트를 만든다 — 설정이 앱 실행 중에 바뀔 수 있기 때문이다."""
    credentials = resolve_credentials(conn, config)
    if credentials is None:
        raise CredentialsMissingError(
            "RTZR 자격 증명이 없습니다. 설정 화면에서 client_id와 client_secret을 입력하세요."
        )
    return RtzrClient(credentials.client_id, credentials.client_secret)


def mask_secret(value: str) -> str:
    """설정 화면에는 저장 여부와 끝 4자리만 보여준다."""
    if len(value) <= 4:
        return "••••"
    return "••••" + value[-4:]
