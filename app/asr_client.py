"""리턴제로 RTZR STT OpenAPI 클라이언트.

문서: https://developers.rtzr.ai/docs/en/stt-file/
- 인증: POST /v1/authenticate (form) → access_token, 유효기간 6시간
- 제출: POST /v1/transcribe (multipart)
- 조회: GET  /v1/transcribe/{id} → status: transcribing | completed | failed
- 결과는 서버에 3일만 보관된다.
"""
import json
import time
from pathlib import Path

import httpx

BASE_URL = "https://openapi.vito.ai"
DEFAULT_MODEL = "sommers"
TOKEN_REFRESH_MARGIN_SEC = 300  # 만료 5분 전에 재발급


class AsrError(Exception):
    """RTZR 호출 실패의 최상위 예외."""


class AsrAuthError(AsrError):
    """인증 실패 또는 토큰 만료."""


class AsrPermanentError(AsrError):
    """재시도해도 소용없는 실패(400 등)."""


class AsrTemporaryError(AsrError):
    """재시도하면 성공할 수 있는 실패(429, 5xx, 네트워크)."""


class RtzrClient:
    def __init__(self, client_id: str, client_secret: str, http: httpx.Client | None = None):
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http or httpx.Client(base_url=BASE_URL, timeout=60.0)
        self._token: str | None = None
        self._expires_at: float = 0.0

    def token(self, *, force: bool = False) -> str:
        if not force and self._token and time.time() < self._expires_at:
            return self._token

        response = self._http.post(
            f"{BASE_URL}/v1/authenticate",
            data={"client_id": self._client_id, "client_secret": self._client_secret},
        )
        if response.status_code in (401, 403):
            raise AsrAuthError("RTZR 자격 증명이 거부되었습니다. .env의 값을 확인하세요.")
        if response.status_code >= 500:
            raise AsrTemporaryError(f"인증 서버 오류 {response.status_code}")
        if response.status_code != 200:
            raise AsrPermanentError(f"인증 실패 {response.status_code}: {response.text}")

        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise AsrPermanentError(f"RTZR 응답이 JSON이 아닙니다: {exc}") from exc

        access_token = payload.get("access_token")
        if not access_token:
            raise AsrPermanentError("RTZR 응답에 access_token이 없습니다.")
        self._token = access_token
        expire_at = payload.get("expire_at")
        self._expires_at = (
            float(expire_at) - TOKEN_REFRESH_MARGIN_SEC
            if expire_at
            else time.time() + 6 * 3600 - TOKEN_REFRESH_MARGIN_SEC
        )
        return self._token
