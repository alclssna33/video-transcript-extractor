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
            raise AsrAuthError(
                "RTZR 자격 증명이 거부되었습니다. 설정 화면에서 client_id와 client_secret을 확인하세요."
            )
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

    def submit(
        self,
        audio_path: Path,
        *,
        keywords: list[str] | None = None,
        spk_count: int | None = None,
    ) -> str:
        """전사를 요청하고 transcribe_id를 반환한다."""
        config: dict[str, object] = {
            "model_name": DEFAULT_MODEL,
            "language": "ko",
            "domain": "GENERAL",
            "use_diarization": True,
            "use_itn": True,
            "use_disfluency_filter": True,
            "use_paragraph_splitter": True,
        }
        if spk_count:
            config["diarization"] = {"spk_count": spk_count}
        if keywords:
            config["keywords"] = keywords

        with audio_path.open("rb") as audio_file:
            response = self._request(
                "POST",
                "/v1/transcribe",
                files={"file": (audio_path.name, audio_file, "application/octet-stream")},
                data={"config": json.dumps(config, ensure_ascii=False)},
            )
        return response.json()["id"]

    def poll(self, transcribe_id: str) -> dict:
        """전사 상태를 조회한다. 완료 전이면 status='transcribing'."""
        payload = self._request("GET", f"/v1/transcribe/{transcribe_id}").json()
        if payload.get("status") == "failed":
            message = payload.get("message") or json.dumps(payload, ensure_ascii=False)
            raise AsrPermanentError(f"전사 실패: {message}")
        return payload

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """토큰을 붙여 호출하고, 401이면 재발급 후 한 번만 재시도한다."""
        for attempt in (1, 2):
            headers = {"Authorization": f"Bearer {self.token(force=attempt == 2)}"}
            try:
                response = self._http.request(
                    method, f"{BASE_URL}{path}", headers=headers, **kwargs
                )
            except httpx.RequestError as exc:
                raise AsrTemporaryError(f"네트워크 오류: {exc}") from exc

            if response.status_code in (401, 403) and attempt == 1:
                continue  # 토큰 만료로 보고 재발급 후 재시도
            if response.status_code in (401, 403):
                raise AsrAuthError("인증에 실패했습니다. RTZR 자격 증명을 확인하세요.")
            if response.status_code == 429:
                raise AsrTemporaryError("요청이 너무 잦습니다(429). 폴링 간격을 늘리세요.")
            if response.status_code >= 500:
                raise AsrTemporaryError(f"RTZR 서버 오류 {response.status_code}")
            if response.status_code >= 400:
                raise AsrPermanentError(f"요청 거부 {response.status_code}: {response.text}")
            return response

        raise AsrAuthError("인증 재시도에 실패했습니다.")
