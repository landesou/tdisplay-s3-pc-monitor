"""HTTP 链路：Token 校验（对/错/缺）+ /api/stats、/health、404、无快照降级。"""

import json
import urllib.error
import urllib.request

import pytest

from pcmond.http_link import HttpLink, check_bearer

TOKEN = "secret-token-123"
SNAPSHOT = {
    "v": 1,
    "seq": 7,
    "t": 1762761600,
    "host": "TEST",
    "up": 1,
    "cpu": {"avg": 1.0, "mhz": 100, "temp": None, "cores": [1]},
    "mem": {"pct": 1.0, "used": 1, "total": 2},
    "gpu": None,
    "net": {"down": 0.0, "up": 0.0},
    "disk": {"read": 0.0, "write": 0.0},
}


def http_get(url, token=None):
    """返回 (status, body, headers)；4xx/5xx 不抛异常。"""
    request = urllib.request.Request(url)
    if token is not None:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read(), response.headers
    except urllib.error.HTTPError as error:
        return error.code, error.read(), error.headers


@pytest.fixture()
def link():
    instance = HttpLink(lambda: SNAPSHOT, token=TOKEN, port=0)
    assert instance.start()
    try:
        yield instance
    finally:
        instance.stop()


def test_check_bearer_correct_token():
    assert check_bearer(f"Bearer {TOKEN}", TOKEN)


def test_check_bearer_wrong_token():
    assert not check_bearer("Bearer nope", TOKEN)


def test_check_bearer_missing_header():
    assert not check_bearer(None, TOKEN)
    assert not check_bearer("", TOKEN)


def test_check_bearer_wrong_scheme():
    assert not check_bearer(f"Basic {TOKEN}", TOKEN)


def test_check_bearer_refuses_when_token_empty():
    assert not check_bearer("Bearer anything", "")


def test_stats_returns_snapshot_with_valid_token(link):
    status, body, headers = http_get(f"http://127.0.0.1:{link.bound_port}/api/stats", TOKEN)
    assert status == 200
    assert json.loads(body) == SNAPSHOT
    # 与 USB 相同的紧凑分隔符
    assert b'":' in body and b'",' in body


def test_stats_without_token_is_401(link):
    status, body, headers = http_get(f"http://127.0.0.1:{link.bound_port}/api/stats")
    assert status == 401
    assert json.loads(body)["error"] == "unauthorized"
    assert headers.get("WWW-Authenticate") == 'Bearer realm="pcmond"'


def test_stats_with_wrong_token_is_401(link):
    status, _, _ = http_get(f"http://127.0.0.1:{link.bound_port}/api/stats", "wrong-token")
    assert status == 401


def test_health_needs_no_token(link):
    status, body, _ = http_get(f"http://127.0.0.1:{link.bound_port}/health")
    assert status == 200
    payload = json.loads(body)
    assert payload["status"] == "ok"
    assert payload["seq"] == 7


def test_unknown_path_is_404(link):
    status, body, _ = http_get(f"http://127.0.0.1:{link.bound_port}/nope", TOKEN)
    assert status == 404
    assert json.loads(body)["error"] == "not found"


def test_stats_before_first_snapshot_is_503():
    instance = HttpLink(lambda: None, token=TOKEN, port=0)
    assert instance.start()
    try:
        status, body, _ = http_get(f"http://127.0.0.1:{instance.bound_port}/api/stats", TOKEN)
        assert status == 503
        assert json.loads(body)["error"] == "no snapshot yet"
    finally:
        instance.stop()


def test_port_conflict_returns_false_and_does_not_raise():
    first = HttpLink(lambda: SNAPSHOT, token=TOKEN, port=0)
    assert first.start()
    try:
        second = HttpLink(lambda: SNAPSHOT, token=TOKEN, port=first.bound_port)
        assert second.start() is False  # 端口被占：降级，不崩
    finally:
        first.stop()


def test_running_property_tracks_lifecycle():
    instance = HttpLink(lambda: SNAPSHOT, token=TOKEN, port=0)
    assert instance.running is False
    assert instance.start()
    try:
        assert instance.running is True
    finally:
        instance.stop()
    assert instance.running is False
