"""PromQL parsing without a live Prometheus."""

from __future__ import annotations

from chaos_ci_runner.probes import _query_prometheus


class _Resp:
    def __init__(self, payload: dict) -> None:
        self.status_code = 200
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def test_single_series_returns_value(monkeypatch) -> None:
    payload = {
        "data": {
            "resultType": "vector",
            "result": [{"value": [0, "3.5"]}],
        }
    }
    monkeypatch.setattr(
        "chaos_ci_runner.probes.requests.get",
        lambda *args, **kwargs: _Resp(payload),
    )
    assert _query_prometheus("http://prom", "up", timeout_s=1) == 3.5


def test_multiple_series_fail_closed(monkeypatch) -> None:
    payload = {
        "data": {
            "resultType": "vector",
            "result": [{"value": [0, "1"]}, {"value": [0, "2"]}],
        }
    }
    monkeypatch.setattr(
        "chaos_ci_runner.probes.requests.get",
        lambda *args, **kwargs: _Resp(payload),
    )
    assert _query_prometheus("http://prom", "up", timeout_s=1) is None
