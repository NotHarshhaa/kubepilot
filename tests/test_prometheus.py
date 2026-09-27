from app.tools.prometheus import prometheus


class FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_prom_query_formats_vector(monkeypatch):
    payload = {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {"metric": {"pod": "nginx-abc", "status": "500"}, "value": [1727000000, "12.5"]},
                {"metric": {}, "value": [1727000000, "0"]},
            ],
        },
    }
    monkeypatch.setattr(
        prometheus.httpx, "get", lambda url, params, timeout: FakeResponse(payload)
    )
    out = prometheus.prom_query('sum(rate(http_requests_total[5m])) by (pod)')
    assert '{pod="nginx-abc", status="500"} = 12.5' in out
    assert "{} = 0" in out


def test_prom_query_no_data(monkeypatch):
    payload = {"status": "success", "data": {"resultType": "vector", "result": []}}
    monkeypatch.setattr(prometheus.httpx, "get", lambda url, params, timeout: FakeResponse(payload))
    assert prometheus.prom_query("up") == "No data."


def test_prom_query_error_is_returned_not_raised(monkeypatch):
    def boom(url, params, timeout):
        raise ConnectionError("refused")

    monkeypatch.setattr(prometheus.httpx, "get", boom)
    out = prometheus.prom_query("up")
    assert out.startswith("Error querying Prometheus:")


def test_prom_query_range_builds_window(monkeypatch):
    captured = {}

    def fake_get(url, params, timeout):
        captured.update(params)
        return FakeResponse(
            {
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [
                        {"metric": {"pod": "a"}, "values": [[1, "1"], [2, "2"]]},
                    ],
                },
            }
        )

    monkeypatch.setattr(prometheus.httpx, "get", fake_get)
    out = prometheus.prom_query_range("up", duration="30m", step="15s")
    assert "2 points, latest=2" in out
    assert captured["step"] == "15s"
    assert 1790 <= (captured["end"] - captured["start"]) <= 1800


def test_parse_duration():
    assert prometheus._parse_duration("30m") == 1800
    assert prometheus._parse_duration("1h") == 3600
    assert prometheus._parse_duration("24h") == 86400
    assert prometheus._parse_duration("2d") == 172800
    import pytest

    with pytest.raises(ValueError):
        prometheus._parse_duration("fortnight")
