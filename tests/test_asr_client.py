import time

import httpx
import pytest

from app.asr_client import AsrAuthError, RtzrClient


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
