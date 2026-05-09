"""Tests for SLO gate evaluation."""

from __future__ import annotations

import time

from chaos_ci_runner.config import GateSpec, HttpProbeSpec
from chaos_ci_runner.engines.base import ExperimentResult
from chaos_ci_runner.gate import evaluate
from chaos_ci_runner.probes import ProbeSample, ProbeWindow


def _exp(status: str = "succeeded") -> ExperimentResult:
    now = time.time()
    return ExperimentResult(
        name="x",
        engine="chaos-mesh",
        started_at=now,
        finished_at=now + 5,
        status=status,
    )


def _window(
    name: str,
    win: str,
    *,
    n_ok: int,
    n_fail: int,
    latency_ms: float = 50.0,
) -> ProbeWindow:
    samples = [ProbeSample(ok=True, latency_ms=latency_ms, status=200) for _ in range(n_ok)]
    samples += [ProbeSample(ok=False, latency_ms=latency_ms, status=500) for _ in range(n_fail)]
    return ProbeWindow(probe=name, window=win, kind="http", samples=samples)  # type: ignore[arg-type]


def _eval(
    *,
    experiments: list,
    http_specs: list[HttpProbeSpec] | None = None,
    windows: list[ProbeWindow] | None = None,
    gate: GateSpec | None = None,
):
    return evaluate(
        gate=gate or GateSpec(),
        experiments=experiments,
        http_probe_specs=http_specs or [],
        prometheus_probe_specs=[],
        windows=windows or [],
    )


def test_pass_when_everything_clean() -> None:
    spec = HttpProbeSpec(name="p", url="http://x", success_rate_pct=99, latency_p99_ms=200)
    out = _eval(
        experiments=[_exp(), _exp()],
        http_specs=[spec],
        windows=[_window("p", "baseline", n_ok=100, n_fail=0)],
    )
    assert out.passed is True
    assert out.pass_rate_pct == 100.0
    assert out.breaches == []


def test_fail_when_experiment_fails() -> None:
    out = _eval(
        experiments=[_exp(), _exp(status="failed")],
        gate=GateSpec(min_pass_rate_pct=100),
    )
    assert out.passed is False
    assert out.pass_rate_pct == 50.0
    assert any("pass rate" in r for r in out.reasons)


def test_fail_when_probe_breaches_success_rate() -> None:
    spec = HttpProbeSpec(name="p", url="http://x", success_rate_pct=99, latency_p99_ms=200)
    out = _eval(
        experiments=[_exp()],
        http_specs=[spec],
        windows=[_window("p", "during", n_ok=80, n_fail=20)],
    )
    assert out.passed is False
    assert any(b.metric == "success_rate_pct" for b in out.breaches)


def test_fail_when_probe_breaches_latency() -> None:
    spec = HttpProbeSpec(name="p", url="http://x", success_rate_pct=50, latency_p99_ms=10)
    out = _eval(
        experiments=[_exp()],
        http_specs=[spec],
        windows=[_window("p", "during", n_ok=100, n_fail=0, latency_ms=500.0)],
    )
    assert out.passed is False
    assert any(b.metric == "latency_p99_ms" for b in out.breaches)


def test_breach_ignored_when_disabled() -> None:
    spec = HttpProbeSpec(name="p", url="http://x", success_rate_pct=99, latency_p99_ms=200)
    out = _eval(
        experiments=[_exp()],
        http_specs=[spec],
        windows=[_window("p", "during", n_ok=80, n_fail=20)],
        gate=GateSpec(fail_on_probe_breach=False),
    )
    assert out.passed is True
    assert len(out.breaches) >= 1


def test_prometheus_probe_breach_detected() -> None:
    from chaos_ci_runner.config import PrometheusProbeSpec

    pspec = PrometheusProbeSpec(name="errors", query="rate(errors[1m])", max=0.01)
    win = ProbeWindow(
        probe="errors",
        window="during",
        kind="prometheus",
        samples=[ProbeSample(ok=False, latency_ms=0, status=200)] * 10,
        values=[0.05] * 10,
    )
    out = evaluate(
        gate=GateSpec(),
        experiments=[_exp()],
        http_probe_specs=[],
        prometheus_probe_specs=[pspec],
        windows=[win],
    )
    assert out.passed is False
    assert any(b.metric == "prom_success_rate_pct" for b in out.breaches)
