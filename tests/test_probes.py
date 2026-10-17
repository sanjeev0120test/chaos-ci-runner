"""PromQL parsing and probe-window scoring without a live cluster."""

from __future__ import annotations

from chaos_ci_runner.probes import ProbeSample, ProbeWindow, _query_prometheus


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


def test_p99_ignores_failed_samples() -> None:
    window = ProbeWindow(probe="api", window="during", kind="http")
    for _ in range(100):
        window.record_sample(ProbeSample(ok=True, latency_ms=20, status=200))
    window.record_sample(ProbeSample(ok=False, latency_ms=5000, status=0, error="timeout"))
    assert window.success_rate_pct < 100
    assert window.latency_p99_ms == 20


def test_p99_is_zero_when_every_sample_failed() -> None:
    window = ProbeWindow(probe="api", window="during", kind="http")
    window.record_sample(ProbeSample(ok=False, latency_ms=5000, status=0, error="timeout"))
    assert window.latency_p99_ms == 0.0


def test_snapshot_does_not_change_when_a_late_sample_arrives() -> None:
    window = ProbeWindow(probe="api", window="during", kind="http")
    window.record_sample(ProbeSample(ok=True, latency_ms=20, status=200))
    frozen = window.snapshot()
    window.record_sample(ProbeSample(ok=False, latency_ms=5000, status=0, error="timeout"))
    assert frozen.total == 1
    assert frozen.latency_p99_ms == 20
    assert window.total == 2
