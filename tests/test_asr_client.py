import time

import httpx
import pytest

from app.asr_client import (
    AsrAuthError,
    AsrPermanentError,
    AsrTemporaryError,
    RtzrClient,
)


def make_client(handler) -> RtzrClient:
    transport = httpx.MockTransport(handler)
    return RtzrClient(
        client_id="cid",
        client_secret="secret",
        http=httpx.Client(transport=transport),
    )


def test_token_is_fetched_once_and_cached():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={
            "access_token": "tok-1",
            "expire_at": int(time.time()) + 6 * 3600,
        })

    client = make_client(handler)

    assert client.token() == "tok-1"
    assert client.token() == "tok-1"
    assert calls == ["/v1/authenticate"]


def test_expired_token_is_refetched():
    tokens = iter(["tok-1", "tok-2"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "access_token": next(tokens),
            "expire_at": int(time.time()) - 1,  # 이미 만료
        })

    client = make_client(handler)

    assert client.token() == "tok-1"
    assert client.token() == "tok-2"


def test_bad_credentials_raise_auth_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "invalid credentials"})

    client = make_client(handler)

    with pytest.raises(AsrAuthError):
        client.token()


def test_malformed_json_response_raises_permanent_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    client = make_client(handler)

    with pytest.raises(AsrPermanentError):
        client.token()


def test_missing_access_token_raises_permanent_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"expire_at": 9999999999})

    client = make_client(handler)

    with pytest.raises(AsrPermanentError):
        client.token()


def auth_response() -> httpx.Response:
    return httpx.Response(200, json={
        "access_token": "tok", "expire_at": int(time.time()) + 3600,
    })


def test_submit_sends_config_and_returns_id(tmp_path):
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"fake audio")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/authenticate":
            return auth_response()
        captured["content_type"] = request.headers["content-type"]
        captured["auth"] = request.headers["authorization"]
        captured["body"] = request.content
        return httpx.Response(200, json={"id": "transcribe-1"})

    client = make_client(handler)

    transcribe_id = client.submit(audio, keywords=["개비공"], spk_count=2)

    assert transcribe_id == "transcribe-1"
    assert captured["auth"] == "Bearer tok"
    assert "multipart/form-data" in captured["content_type"]
    body = captured["body"].decode("utf-8", errors="replace")
    assert '"model_name": "sommers"' in body
    assert '"use_diarization": true' in body
    assert '"language": "ko"' in body
    assert "개비공" in body
    assert '"spk_count": 2' in body


def test_submit_omits_spk_count_when_unknown(tmp_path):
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"fake audio")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/authenticate":
            return auth_response()
        captured["body"] = request.content
        return httpx.Response(200, json={"id": "transcribe-1"})

    client = make_client(handler)
    client.submit(audio)

    assert "spk_count" not in captured["body"].decode("utf-8", errors="replace")


def test_poll_returns_completed_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/authenticate":
            return auth_response()
        return httpx.Response(200, json={
            "id": "transcribe-1",
            "status": "completed",
            "results": {"utterances": [
                {"start_at": 0, "duration": 1000, "msg": "안녕하세요", "spk": 0, "lang": "ko"},
            ]},
        })

    client = make_client(handler)
    payload = client.poll("transcribe-1")

    assert payload["status"] == "completed"
    assert payload["results"]["utterances"][0]["msg"] == "안녕하세요"


def test_poll_raises_permanent_error_on_failed_status():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/authenticate":
            return auth_response()
        return httpx.Response(200, json={"id": "x", "status": "failed",
                                         "message": "audio decode error"})

    client = make_client(handler)

    with pytest.raises(AsrPermanentError) as exc:
        client.poll("x")
    assert "audio decode error" in str(exc.value)


def test_poll_retries_once_after_token_expiry():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/v1/authenticate":
            return auth_response()
        # 첫 조회는 401, 재발급 후 두 번째는 성공
        if calls.count("/v1/transcribe/x") == 1:
            return httpx.Response(401, json={"message": "expired"})
        return httpx.Response(200, json={"id": "x", "status": "transcribing"})

    client = make_client(handler)
    payload = client.poll("x")

    assert payload["status"] == "transcribing"
    assert calls.count("/v1/authenticate") == 2


def test_poll_raises_temporary_error_on_429():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/authenticate":
            return auth_response()
        return httpx.Response(429, json={"message": "too many requests"})

    client = make_client(handler)

    with pytest.raises(AsrTemporaryError):
        client.poll("x")
